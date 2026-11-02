import json
from pathlib import Path

from app.static_checks import parse_bandit_json, parse_ruff_json, run_static_checks


def test_parse_ruff(tmp_path: Path):
    out = json.dumps([{"code": "F821", "message": "Undefined name `x`",
                       "filename": str(tmp_path / "a.py"), "location": {"row": 3, "column": 1}},
                      {"code": "F401", "message": "unused import",
                       "filename": str(tmp_path / "a.py"), "location": {"row": 1, "column": 1}}])
    f = parse_ruff_json(out, tmp_path)
    assert f[0].file == "a.py" and f[0].severity == "high" and f[0].source == "ruff"
    assert f[1].category == "style"


def test_parse_bandit(tmp_path: Path):
    out = json.dumps({"results": [{"filename": str(tmp_path / "pkg" / "b.py"), "line_number": 7,
                                   "issue_severity": "HIGH", "issue_confidence": "MEDIUM",
                                   "issue_text": "Use of exec", "test_id": "B102"}]})
    f = parse_bandit_json(out, tmp_path)[0]
    assert f.file == "pkg/b.py" and f.line == 7 and f.severity == "high" and f.confidence == 0.7


def test_garbage_output_is_ignored(tmp_path: Path):
    assert parse_ruff_json("not json", tmp_path) == []
    assert parse_bandit_json("", tmp_path) == []


def test_run_static_checks_never_raises_and_ignores_non_python():
    assert run_static_checks({"README.md": "# hi"}) == []
    result = run_static_checks({"a.py": "import subprocess\nsubprocess.call('ls', shell=True)\n"})
    assert isinstance(result, list)  # may be empty if ruff/bandit are not installed
