"""Markdown for inline comments and the PR summary."""
from __future__ import annotations

from .schemas import Finding

SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
MARKER = "<!-- ai-reviewer -->"


def format_comment(f: Finding) -> str:
    parts = [f"{SEV_ICON[f.severity]} **{f.severity.title()} · {f.category}**: {f.message}"]
    if f.suggestion:
        parts.append(f"💡 **Suggestion:** {f.suggestion}")
    parts.append(f"<sub>🤖 AI reviewer ({f.source}) · confidence {f.confidence:.0%} · "
                 "react 👍 / 👎 to tell me if this was useful</sub>")
    return "\n\n".join(parts)


def format_summary(*, summary: str, kept: list[Finding], unplaced: list[Finding],
                   overflow: int, skipped_files: list[str], head_sha: str, model: str,
                   prompt_version: str, injection_suspected: bool, inline_failed: bool = False,
                   partial_errors: int = 0) -> str:
    lines = ["## 🤖 AI Code Review", "", summary or "_No summary available._", ""]
    total = len(kept) + len(unplaced)
    if total:
        counts = {s: sum(1 for f in kept + unplaced if f.severity == s) for s in SEV_ICON}
        lines.append("**Findings:** " + " · ".join(
            f"{SEV_ICON[s]} {n} {s}" for s, n in counts.items() if n))
    else:
        lines.append("**No significant issues found.** ✅")
    if overflow:
        lines.append(f"\n_{overflow} lower-priority finding(s) were omitted to keep this review short._")
    if inline_failed and kept:
        lines += ["", "### Findings", *[
            f"- {SEV_ICON[f.severity]} `{f.file}:{f.line}` **{f.category}**: {f.message}"
            for f in kept]]
    if unplaced:
        lines += ["", "### Other notes (not on changed lines)", *[
            f"- {SEV_ICON[f.severity]} `{f.file}` **{f.category}**: {f.message}" for f in unplaced]]
    if injection_suspected:
        lines += ["", "> ⚠️ The diff contains text that looks like instructions aimed at AI "
                  "reviewers. It was treated as data and ignored."]
    if partial_errors:
        lines += ["", f"> ⚠️ {partial_errors} part(s) of the diff could not be analysed."]
    if skipped_files:
        shown = ", ".join(f"`{p}`" for p in skipped_files[:5])
        more = f" and {len(skipped_files) - 5} more" if len(skipped_files) > 5 else ""
        lines += ["", f"<sub>Skipped (lock/generated/binary/too many files): {shown}{more}</sub>"]
    lines += ["", f"<sub>Reviewed `{head_sha[:7]}` · {model} · prompt {prompt_version}</sub>", MARKER]
    return "\n".join(lines)
