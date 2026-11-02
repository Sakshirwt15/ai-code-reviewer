"""Parse unified diffs, drop noise files, render line-numbered chunks for the LLM.

Key idea: inline GitHub comments must point at a line that is *visible in the diff* on the
RIGHT (new) side. So we track the new-file line number of every added/context line and
show those numbers to the LLM, then validate its answers against them.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from .security import redact_line

DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_LINE_CHARS = 2000

SKIP_FILENAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "pipfile.lock",
    "cargo.lock", "go.sum", "composer.lock", "gemfile.lock", "uv.lock",
}
SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip", ".gz", ".tar",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".lock", ".min.js", ".min.css",
    ".map", ".snap", ".pyc", ".ipynb", ".csv",
)
SKIP_DIR_PARTS = {
    "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv",
    "site-packages", "__snapshots__",
}
SKIP_NAME_PATTERNS = ["*_pb2.py", "*_pb2_grpc.py", "*.generated.*", "*.pb.go", "*.g.dart"]


@dataclass
class DiffLine:
    kind: str  # "add" | "del" | "ctx"
    old_no: int | None
    new_no: int | None
    content: str


@dataclass
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"  # modified | added | deleted | renamed
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "add")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "del")

    def commentable_lines(self) -> set[int]:
        """New-file line numbers GitHub allows inline comments on (added + context lines)."""
        return {
            ln.new_no for h in self.hunks for ln in h.lines
            if ln.kind in ("add", "ctx") and ln.new_no is not None
        }

    def added_lines(self) -> set[int]:
        return {ln.new_no for h in self.hunks for ln in h.lines if ln.kind == "add" and ln.new_no}


def parse_diff(text: str) -> list[FileDiff]:
    """Parse `git diff` style output (what GitHub returns for application/vnd.github.v3.diff)."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = DIFF_GIT_RE.match(line)
        if m:
            cur = FileDiff(path=m.group(2).strip('"'), old_path=m.group(1).strip('"'))
            files.append(cur)
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        if line.startswith("new file mode"):
            cur.status = "added"
        elif line.startswith("deleted file mode"):
            cur.status = "deleted"
        elif line.startswith("rename from "):
            cur.old_path, cur.status = line[len("rename from "):], "renamed"
        elif line.startswith("rename to "):
            cur.path = line[len("rename to "):]
        elif line.startswith(("Binary files", "GIT binary patch")):
            cur.binary = True
        elif line.startswith("+++ "):
            target = line[4:].strip().strip('"')
            if target.startswith("b/"):
                cur.path = target[2:]
        else:
            hm = HUNK_RE.match(line)
            if hm:
                i = _consume_hunk(lines, i, hm, cur)
                continue
        i += 1
    return files


def _consume_hunk(lines: list[str], i: int, hm: re.Match[str], cur: FileDiff) -> int:
    """Read one hunk using the line counts in its header (so '--- ' content can't confuse us)."""
    old_no, new_no = int(hm.group(1)), int(hm.group(3))
    remaining_old = int(hm.group(2)) if hm.group(2) is not None else 1
    remaining_new = int(hm.group(4)) if hm.group(4) is not None else 1
    hunk = Hunk(header=lines[i])
    i += 1
    while i < len(lines) and (remaining_old > 0 or remaining_new > 0 or lines[i].startswith("\\")):
        if lines[i].startswith("diff --git "):  # malformed counts: never swallow the next file
            break
        raw = lines[i]
        i += 1
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        tag, content = raw[:1], raw[1:]
        if tag == "+":
            hunk.lines.append(DiffLine("add", None, new_no, content))
            new_no += 1
            remaining_new -= 1
        elif tag == "-":
            hunk.lines.append(DiffLine("del", old_no, None, content))
            old_no += 1
            remaining_old -= 1
        else:  # context (some tools strip the leading space of blank lines)
            hunk.lines.append(DiffLine("ctx", old_no, new_no, content))
            old_no += 1
            new_no += 1
            remaining_old -= 1
            remaining_new -= 1
    cur.hunks.append(hunk)
    return i


def is_reviewable(fd: FileDiff) -> bool:
    """Drop lock files, images, generated/vendored code, deleted and binary files."""
    if fd.binary or fd.status == "deleted" or not fd.hunks:
        return False
    path = fd.path.lower()
    name = path.rsplit("/", 1)[-1]
    if name in SKIP_FILENAMES or path.endswith(SKIP_SUFFIXES):
        return False
    if any(part in SKIP_DIR_PARTS for part in path.split("/")[:-1]):
        return False
    return not any(fnmatch.fnmatch(name, pat) for pat in SKIP_NAME_PATTERNS)


# --------------------------------------------------------------------------------------
# Rendering + chunking (token limits)
# --------------------------------------------------------------------------------------
@dataclass
class Chunk:
    index: int
    text: str
    files: list[str]
    lines: dict[str, set[int]]  # path -> new-file line numbers shown in this chunk


@dataclass
class _Piece:
    path: str
    text: str
    lines: set[int]


def _render_line(ln: DiffLine) -> str:
    content = redact_line(ln.content)
    if len(content) > MAX_LINE_CHARS:
        content = content[:MAX_LINE_CHARS] + " …[line truncated]"
    if ln.kind == "add":
        return f"{ln.new_no:>5} + {content}"
    if ln.kind == "ctx":
        return f"{ln.new_no:>5}   {content}"
    return f"      - {content}"


def _split_hunk(h: Hunk, budget: int):
    cont = "@@ (continued) @@"
    group: list[DiffLine] = []
    size = len(h.header) + 1
    first = True
    for ln in h.lines:
        cost = len(_render_line(ln)) + 1
        if group and size + cost > budget:
            yield (h.header if first else cont), group
            first, group, size = False, [], len(cont) + 1
        group.append(ln)
        size += cost
    if group:
        yield (h.header if first else cont), group


def _file_pieces(fd: FileDiff, max_chars: int) -> list[_Piece]:
    header = f"### File: {fd.path} ({fd.status})\n"
    budget = max(max_chars - len(header), 500)
    pieces: list[_Piece] = []
    buf: list[str] = []
    buf_len = 0
    buf_lines: set[int] = set()

    def flush() -> None:
        nonlocal buf_len
        if buf:
            pieces.append(_Piece(fd.path, header + "\n".join(buf) + "\n", set(buf_lines)))
        buf.clear()
        buf_lines.clear()
        buf_len = 0

    for hunk in fd.hunks:
        for sub_header, sub_lines in _split_hunk(hunk, budget):
            rendered = [_render_line(ln) for ln in sub_lines]
            block_len = len(sub_header) + sum(len(r) + 1 for r in rendered) + 1
            if buf and buf_len + block_len > budget:
                flush()
            buf.append(sub_header)
            buf.extend(rendered)
            buf_len += block_len
            buf_lines |= {ln.new_no for ln in sub_lines if ln.new_no is not None}
    flush()
    return pieces


def chunk_diff(files: list[FileDiff], max_chars: int = 12000) -> list[Chunk]:
    """Pack file diffs into chunks of at most ~max_chars (a cheap proxy for tokens)."""
    pieces = [p for fd in files for p in _file_pieces(fd, max_chars)]
    chunks: list[Chunk] = []
    texts: list[str] = []
    paths: list[str] = []
    lines: dict[str, set[int]] = {}
    size = 0

    def flush() -> None:
        nonlocal size
        if texts:
            chunks.append(Chunk(len(chunks) + 1, "\n".join(texts), list(paths), dict(lines)))
        texts.clear()
        paths.clear()
        lines.clear()
        size = 0

    for p in pieces:
        if texts and size + len(p.text) > max_chars:
            flush()
        texts.append(p.text)
        if p.path not in paths:
            paths.append(p.path)
        lines.setdefault(p.path, set()).update(p.lines)
        size += len(p.text)
    flush()
    return chunks
