"""Run ruff + bandit on changed Python files to give the LLM "ground truth" hints."""
from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .schemas import Finding

log = logging.getLogger(__name__)

_BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
_BANDIT_CONFIDENCE = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
_STYLE_ONLY_RUFF = {"F401", "F841", "F811"}  # unused import/variable/redefinition


def _tool_cmd(name: str) -> list[str] | None:
    exe = shutil.which(name)
    if exe:
        return [exe]
    if importlib.util.find_spec(name):
        return [sys.executable, "-m", name]
    return None


def parse_ruff_json(output: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        items = json.loads(output or "[]")
    except ValueError:
        return findings
    for it in items:
        code = it.get("code") or ""
        try:
            rel = Path(it["filename"]).resolve().relative_to(root.resolve()).as_posix()
            line = int(it["location"]["row"])
        except (KeyError, ValueError, TypeError):
            continue
        if code.startswith("E9") or code == "F821":
            severity, category, conf = "high", "bug", 0.95
        elif code in _STYLE_ONLY_RUFF:
            severity, category, conf = "low", "style", 0.9
        elif code.startswith("B"):
            severity, category, conf = "medium", "bug", 0.75
        else:
            severity, category, conf = "medium", "bug", 0.8
        findings.append(Finding(file=rel, line=line, severity=severity, category=category,
                                message=f"{it.get('message', 'ruff finding')} ({code})",
                                confidence=conf, source="ruff"))
    return findings


def parse_bandit_json(output: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        data = json.loads(output or "{}")
    except ValueError:
        return findings
    for it in data.get("results", []):
        try:
            rel = Path(it["filename"]).resolve().relative_to(root.resolve()).as_posix()
            line = int(it["line_number"])
        except (KeyError, ValueError, TypeError):
            continue
        findings.append(Finding(
            file=rel, line=line,
            severity=_BANDIT_SEVERITY.get(it.get("issue_severity", "LOW"), "low"),
            category="security",
            message=f"{it.get('issue_text', 'bandit finding')} ({it.get('test_id', '')})",
            confidence=_BANDIT_CONFIDENCE.get(it.get("issue_confidence", "LOW"), 0.5),
            source="bandit"))
    return findings


def run_static_checks(files: dict[str, str], timeout: int = 60) -> list[Finding]:
    """`files` maps repo-relative path -> file content at the PR head. Never raises."""
    py_files = {p: c for p, c in files.items() if p.endswith(".py")}
    if not py_files:
        return []
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="aireview_") as tmp:
        root = Path(tmp)
        for rel, content in py_files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        ruff = _tool_cmd("ruff")
        if ruff:
            cmd = [*ruff, "check", "--isolated", "--no-cache", "--output-format", "json",
                   "--select", "E9,F,B", str(root)]
            findings += _run(cmd, timeout, lambda out: parse_ruff_json(out, root), "ruff")
        bandit = _tool_cmd("bandit")
        if bandit:
            cmd = [*bandit, "-r", str(root), "-f", "json", "-q"]
            findings += _run(cmd, timeout, lambda out: parse_bandit_json(out, root), "bandit")
    return findings


def _run(cmd: list[str], timeout: int, parser, name: str) -> list[Finding]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("%s failed: %s", name, exc)
        return []
    return parser(proc.stdout)  # both tools exit non-zero when they find issues
