"""The review pipeline: diff -> filter -> chunk -> (static + LLM) -> validate -> post -> save."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Settings
from .db import Database
from .diff_parser import FileDiff, chunk_diff, is_reviewable, parse_diff
from .filters import filter_findings
from .formatting import format_comment, format_summary
from .github_client import GitHubClient, GitHubError
from .llm import LLMError
from .reviewer import Reviewer
from .schemas import Finding
from .security import looks_like_injection
from .static_checks import run_static_checks

log = logging.getLogger(__name__)


@dataclass
class Analysis:
    summary: str = ""
    kept: list[Finding] = field(default_factory=list)
    unplaced: list[Finding] = field(default_factory=list)
    overflow: int = 0
    dropped: int = 0
    n_chunks: int = 0
    n_files: int = 0
    skipped_files: list[str] = field(default_factory=list)
    injection_suspected: bool = False
    errors: list[str] = field(default_factory=list)
    failed: bool = False  # every chunk failed


def analyze_diff(diff_text: str, reviewer: Reviewer, settings: Settings, *, title: str = "",
                 description: str = "", static_findings: list[Finding] | None = None,
                 exclude_fingerprints: set[str] | None = None) -> Analysis:
    """Pure function (no GitHub, no DB): used by the pipeline, the CLI and the eval harness."""
    all_files = parse_diff(diff_text)
    files = [f for f in all_files if is_reviewable(f)]
    skipped = [f.path for f in all_files if not is_reviewable(f)]
    if len(files) > settings.max_files:
        skipped += [f.path for f in files[settings.max_files:]]
        files = files[: settings.max_files]

    out = Analysis(n_files=len(files), skipped_files=skipped)
    out.injection_suspected = any(
        looks_like_injection(ln.content)
        for f in files for h in f.hunks for ln in h.lines if ln.kind == "add")

    static_findings = static_findings or []
    chunks = chunk_diff(files, settings.max_chunk_chars)
    out.n_chunks = len(chunks)
    summaries: list[str] = []
    findings: list[Finding] = []
    for chunk in chunks:
        hints = [s for s in static_findings if s.file in chunk.files and s.category != "style"]
        try:
            result = reviewer.review_chunk(chunk, title, description, hints)
        except (LLMError, ValueError) as exc:
            log.warning("chunk %s failed: %s", chunk.index, exc)
            out.errors.append(f"chunk {chunk.index}: {exc}")
            continue
        summaries.append(result.summary)
        findings += result.findings
    out.failed = bool(chunks) and not summaries

    findings += [s for s in static_findings if s.category != "style"]
    valid = {f.path: f.commentable_lines() for f in files}
    fr = filter_findings(
        findings, valid, min_confidence=settings.min_confidence,
        drop_style=settings.drop_style_comments, max_comments=settings.max_comments,
        exclude_fingerprints=exclude_fingerprints or set())
    out.kept, out.unplaced, out.overflow, out.dropped = fr.kept, fr.unplaced, fr.overflow, fr.dropped
    out.summary = reviewer.merge_summaries(summaries) if summaries else (
        "No reviewable code changes in this pull request." if not chunks else "")
    return out


@dataclass
class Outcome:
    status: str  # done | skipped | failed | dry_run
    message: str = ""
    run_id: int | None = None
    analysis: Analysis | None = None


def _collect_static(gh: GitHubClient, repo: str, sha: str, diff_text: str,
                    settings: Settings) -> list[Finding]:
    if not settings.enable_static_checks:
        return []
    py: list[FileDiff] = [f for f in parse_diff(diff_text)
                          if is_reviewable(f) and f.path.endswith(".py")][: settings.max_static_files]
    contents: dict[str, str] = {}
    for f in py:
        try:
            text = gh.get_file_content(repo, f.path, sha)
        except GitHubError as exc:
            log.warning("could not fetch %s: %s", f.path, exc)
            continue
        if text is not None:
            contents[f.path] = text
    return run_static_checks(contents)


def review_pull_request(gh: GitHubClient, db: Database | None, reviewer: Reviewer,
                        settings: Settings, repo: str, pr_number: int, *, dry_run: bool = False,
                        force: bool = False, installation_id: int | None = None) -> Outcome:
    pr = gh.get_pull(repo, pr_number)
    head_sha = pr["head"]["sha"]
    if pr.get("draft") and not force:
        return Outcome("skipped", "draft pull request")
    if pr.get("state") == "closed" and not force:
        return Outcome("skipped", "pull request is closed")

    run_id: int | None = None
    if db is not None and not dry_run:
        run_id = db.start_run(repo, pr_number, head_sha, model=settings.model_name,
                              prompt_version=reviewer.prompt_version,
                              installation_id=installation_id, force=force)
        if run_id is None:
            return Outcome("skipped", f"commit {head_sha[:7]} already reviewed (idempotent)")

    try:
        diff_text = gh.get_diff(repo, pr_number)
        static = _collect_static(gh, repo, head_sha, diff_text, settings)
        seen = db.previously_posted(repo, pr_number) if db is not None and not dry_run else set()
        analysis = analyze_diff(diff_text, reviewer, settings, title=pr.get("title") or "",
                                description=pr.get("body") or "", static_findings=static,
                                exclude_fingerprints=seen)
        if analysis.failed:
            raise RuntimeError("; ".join(analysis.errors) or "LLM review failed")

        if dry_run:
            return Outcome("dry_run", "dry run: nothing posted", None, analysis)

        comment_ids, inline_failed = _post_review(gh, repo, pr_number, head_sha, analysis,
                                                  settings, reviewer.prompt_version)
        if db is not None and run_id is not None:
            items = [(f, not inline_failed, comment_ids.get(i)) for i, f in enumerate(analysis.kept)]
            items += [(f, False, None) for f in analysis.unplaced]
            db.finish_run(run_id, summary=analysis.summary, files_reviewed=analysis.n_files,
                          chunks=analysis.n_chunks, items=items)
        return Outcome("done", f"{len(analysis.kept)} inline comment(s) posted", run_id, analysis)
    except Exception as exc:
        log.exception("review failed for %s#%s", repo, pr_number)
        if db is not None and run_id is not None:
            db.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        return Outcome("failed", str(exc), run_id)


def _post_review(gh: GitHubClient, repo: str, pr_number: int, sha: str, a: Analysis,
                 settings: Settings, prompt_version: str) -> tuple[dict[int, int], bool]:
    """Post one review (summary + inline comments). Returns ({kept index: comment id}, inline_failed)."""
    comments = [{"path": f.file, "line": f.line, "side": "RIGHT", "body": format_comment(f)}
                for f in a.kept]

    def body(inline_failed: bool) -> str:
        return format_summary(
            summary=a.summary, kept=a.kept, unplaced=a.unplaced, overflow=a.overflow,
            skipped_files=a.skipped_files, head_sha=sha, model=settings.model_name,
            prompt_version=prompt_version, injection_suspected=a.injection_suspected,
            inline_failed=inline_failed, partial_errors=len(a.errors))

    try:
        review = gh.create_review(repo, pr_number, sha, body(False), comments)
        failed = False
    except GitHubError as exc:
        if exc.status != 422 or not comments:
            raise
        # One bad line number makes GitHub reject the whole review: fall back to a summary
        # that lists the findings in text so nothing is lost.
        log.warning("inline comments rejected (%s); posting summary only", exc)
        review = gh.create_review(repo, pr_number, sha, body(True), [])
        failed = True

    ids: dict[int, int] = {}
    if comments and not failed:
        posted = gh.list_review_comments(repo, pr_number, review["id"])
        remaining = list(posted)
        for i, c in enumerate(comments):
            match = next((p for p in remaining if p.get("path") == c["path"]
                          and (p.get("line") or p.get("original_line")) == c["line"]
                          and p.get("body") == c["body"]), None)
            if match:
                ids[i] = match["id"]
                remaining.remove(match)
    return ids, failed


def sync_feedback(db: Database, gh_factory: Callable[[int | None], GitHubClient],
                  limit: int = 200) -> int:
    """Pull 👍/👎 reactions on our comments into the DB. (GitHub sends no webhook for these.)"""
    updated = 0
    clients: dict[int | None, GitHubClient] = {}
    for row in db.findings_for_feedback(limit):
        try:
            gh = clients.setdefault(row["installation_id"], gh_factory(row["installation_id"]))
            r = gh.get_comment_reactions(row["repo"], row["comment_id"])
        except GitHubError as exc:
            log.warning("feedback sync skipped comment %s: %s", row["comment_id"], exc)
            continue
        db.record_feedback(row["id"], r["up"], r["down"])
        updated += 1
    return updated
