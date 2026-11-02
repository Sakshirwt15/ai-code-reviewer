"""Evaluate review quality on seeded-bug PRs.

  python -m eval.run_eval                       # offline baseline (rule-based mock)
  python -m eval.run_eval --provider anthropic  # a real model (needs API key)
  python -m eval.run_eval --prompt v1           # compare prompt versions

A finding is a TRUE POSITIVE if it is on the same file within +/-2 lines of an expected bug
(and the category matches when given). Unmatched findings are FALSE POSITIVES (noise).
Expected bugs nobody found are FALSE NEGATIVES.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.llm import make_llm_client
from app.pipeline import analyze_diff
from app.reviewer import Reviewer

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
LINE_TOLERANCE = 2


@dataclass
class CaseResult:
    name: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    findings: list[str] = field(default_factory=list)
    error: str = ""


def evaluate_case(case_dir: Path, reviewer: Reviewer, settings: Settings) -> CaseResult:
    meta = json.loads((case_dir / "expected.json").read_text())
    expected = list(meta["expected"])
    res = CaseResult(case_dir.name)
    analysis = analyze_diff((case_dir / "pr.diff").read_text(), reviewer, settings,
                            title=meta.get("title", ""))
    if analysis.failed:
        res.error = "; ".join(analysis.errors)
    unmatched = list(expected)
    for f in analysis.kept:
        res.findings.append(f"{f.file}:{f.line} {f.severity}/{f.category}")
        hit = next((e for e in unmatched if e["file"] == f.file
                    and abs(e["line"] - f.line) <= LINE_TOLERANCE
                    and e.get("category", f.category) == f.category), None)
        if hit:
            unmatched.remove(hit)
            res.tp += 1
        else:
            res.fp += 1
    res.fn = len(unmatched)
    return res


def run(settings: Settings, dataset: Path = DATASET) -> tuple[list[CaseResult], dict[str, float]]:
    reviewer = Reviewer(make_llm_client(settings), settings.prompt_version, settings.max_retries)
    results = []
    for d in sorted(dataset.iterdir()):
        if (d / "expected.json").exists():
            results.append(evaluate_case(d, reviewer, settings))
            if settings.llm_provider != "mock":
                time.sleep(15)  # sirf real API ke liye  # free-tier rate limit se bachne ke liye
    tp, fp, fn = (sum(getattr(r, k) for r in results) for k in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return results, {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def write_report(results: list[CaseResult], metrics: dict, settings: Settings, path: Path) -> None:
    lines = [
        "# Evaluation results", "",
        f"- Date: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"- Provider / model: `{settings.llm_provider}` / `{settings.model_name}`",
        f"- Prompt version: `{settings.prompt_version}`",
        f"- Cases: {len(results)}", "",
        f"**Precision {metrics['precision']:.2f} · Recall {metrics['recall']:.2f} · "
        f"F1 {metrics['f1']:.2f}** (TP {metrics['tp']}, FP {metrics['fp']}, FN {metrics['fn']})", "",
        "| Case | TP | FP | FN | Findings |", "|---|---|---|---|---|",
    ]
    for r in results:
        note = r.error and f" ⚠ {r.error}" or ""
        lines.append(f"| {r.name} | {r.tp} | {r.fp} | {r.fn} | {', '.join(r.findings) or '-'}{note} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--prompt")
    ap.add_argument("--out", default=str(HERE / "results.md"))
    args = ap.parse_args()
    settings = Settings.from_env()
    if args.provider:
        settings = replace(settings, llm_provider=args.provider, llm_model="")
    if args.prompt:
        settings = replace(settings, prompt_version=args.prompt)
    results, metrics = run(settings)
    write_report(results, metrics, settings, Path(args.out))
    print(f"precision={metrics['precision']:.2f} recall={metrics['recall']:.2f} "
          f"f1={metrics['f1']:.2f}  -> {args.out}")


if __name__ == "__main__":
    main()
