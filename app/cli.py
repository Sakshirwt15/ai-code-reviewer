"""Command line entry point (also what the GitHub Action runs).

  python -m app.cli review-diff path/to/pr.diff        # local, no GitHub needed
  python -m app.cli review owner/repo 12 --dry-run     # real PR, print only
  python -m app.cli review owner/repo 12               # real PR, post the review
  python -m app.cli sync-feedback                      # pull 👍/👎 reactions into the DB
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .db import Database
from .github_client import make_github_client
from .llm import make_llm_client
from .pipeline import Analysis, analyze_diff, review_pull_request, sync_feedback
from .reviewer import Reviewer


def _print_analysis(a: Analysis) -> None:
    print(f"\nSummary: {a.summary}")
    print(f"Files reviewed: {a.n_files}  chunks: {a.n_chunks}  dropped: {a.dropped}"
          f"  skipped: {len(a.skipped_files)}")
    if a.injection_suspected:
        print("WARNING: diff contains text that looks like a prompt-injection attempt")
    for f in a.kept:
        print(f"\n[{f.severity.upper()}/{f.category}] {f.file}:{f.line} (conf {f.confidence:.0%}, {f.source})")
        print(f"  {f.message}")
        if f.suggestion:
            print(f"  fix: {f.suggestion}")
    for f in a.unplaced:
        print(f"\n[{f.severity.upper()}/{f.category}] {f.file} (not on a changed line): {f.message}")
    if not a.kept and not a.unplaced:
        print("\nNo significant issues found.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-review")
    parser.add_argument("--provider", help="override LLM_PROVIDER (anthropic|gemini|groq|mock)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_diff = sub.add_parser("review-diff", help="review a local unified diff file")
    p_diff.add_argument("path")

    p_pr = sub.add_parser("review", help="review a GitHub pull request")
    p_pr.add_argument("repo", help="owner/name")
    p_pr.add_argument("pr", type=int)
    p_pr.add_argument("--dry-run", action="store_true", help="print findings, post nothing")
    p_pr.add_argument("--force", action="store_true", help="re-review even if already done")

    sub.add_parser("sync-feedback", help="update 👍/👎 counts from GitHub")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if args.provider:
        settings = replace(settings, llm_provider=args.provider, llm_model="")

    if args.cmd == "review-diff":
        reviewer = Reviewer(make_llm_client(settings), settings.prompt_version, settings.max_retries)
        text = Path(args.path).read_text(encoding="utf-8")
        _print_analysis(analyze_diff(text, reviewer, settings, title=Path(args.path).stem))
        return 0

    if args.cmd == "review":
        gh = make_github_client(settings)
        reviewer = Reviewer(make_llm_client(settings), settings.prompt_version, settings.max_retries)
        db = None if args.dry_run else Database(settings.database_url)
        outcome = review_pull_request(gh, db, reviewer, settings, args.repo, args.pr,
                                      dry_run=args.dry_run, force=args.force)
        print(f"{outcome.status}: {outcome.message}")
        if outcome.analysis:
            _print_analysis(outcome.analysis)
        return 1 if outcome.status == "failed" else 0

    if args.cmd == "sync-feedback":
        n = sync_feedback(Database(settings.database_url), lambda _i: make_github_client(settings))
        print(f"updated feedback for {n} comment(s)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
