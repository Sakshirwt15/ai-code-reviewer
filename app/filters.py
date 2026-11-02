"""Noise filter + de-duplication. Fewer, better comments beat many mediocre ones."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .schemas import Finding

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def fingerprint(f: Finding) -> str:
    """Stable id of a finding that ignores the line number (lines shift between commits)."""
    msg = re.sub(r"\d+", "", re.sub(r"\s+", " ", f.message.lower())).strip()[:80]
    return hashlib.sha1(f"{f.file}|{f.category}|{msg}".encode()).hexdigest()[:16]


@dataclass
class FilterResult:
    kept: list[Finding] = field(default_factory=list)  # become inline comments
    unplaced: list[Finding] = field(default_factory=list)  # real issues, no valid diff line
    dropped: int = 0
    overflow: int = 0  # good findings cut by max_comments


def _resolve_path(path: str, valid: dict[str, set[int]]) -> str | None:
    p = path.strip().replace("\\", "/")
    for candidate in (p, p.removeprefix("./"), p.removeprefix("a/"), p.removeprefix("b/")):
        if candidate in valid:
            return candidate
    return None  # hallucinated file


def _quality_key(f: Finding) -> tuple[int, float]:
    return (SEVERITY_RANK[f.severity], -f.confidence)


def _is_duplicate(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if a.line == b.line and a.category == b.category:
        return True
    # Different lines are only merged when two *different tools* (e.g. LLM + bandit) describe the
    # same issue with slightly shifted lines. Two identical messages from one source on adjacent
    # lines are usually two real problems (e.g. two hard-coded secrets).
    close = abs(a.line - b.line) <= 3 and a.source != b.source
    return close and SequenceMatcher(None, a.message.lower(), b.message.lower()).ratio() > 0.5


def dedupe(findings: list[Finding]) -> list[Finding]:
    result: list[Finding] = []
    for f in sorted(findings, key=_quality_key):
        if not any(_is_duplicate(f, kept) for kept in result):
            result.append(f)
    return result


def filter_findings(
    findings: list[Finding],
    valid_lines: dict[str, set[int]],
    *,
    min_confidence: float = 0.6,
    drop_style: bool = True,
    max_comments: int = 10,
    exclude_fingerprints: frozenset[str] | set[str] = frozenset(),
    snap_distance: int = 3,
) -> FilterResult:
    out = FilterResult()
    candidates: list[Finding] = []
    unplaced: list[Finding] = []

    for f in findings:
        path = _resolve_path(f.file, valid_lines)
        if path is None:
            out.dropped += 1
            continue
        f = f.model_copy(update={"file": path})
        if f.confidence < min_confidence or (drop_style and f.category == "style"):
            out.dropped += 1
            continue
        if fingerprint(f) in exclude_fingerprints:  # already posted on an earlier commit
            out.dropped += 1
            continue
        lines = valid_lines[path]
        if f.line in lines:
            candidates.append(f)
            continue
        nearest = min(lines, key=lambda n: abs(n - f.line), default=None)
        if nearest is not None and abs(nearest - f.line) <= snap_distance:
            candidates.append(f.model_copy(update={"line": nearest}))
        elif f.severity in ("high", "medium"):
            unplaced.append(f)
        else:
            out.dropped += 1

    deduped = dedupe(candidates)
    deduped.sort(key=lambda f: (*_quality_key(f), f.file, f.line))
    out.kept = deduped[:max_comments]
    out.overflow = max(0, len(deduped) - max_comments)
    out.unplaced = dedupe(unplaced)[:5]
    return out
