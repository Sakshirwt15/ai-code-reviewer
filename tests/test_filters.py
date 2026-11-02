from app.filters import filter_findings
from app.schemas import Finding


def F(line=5, sev="high", cat="bug", msg="something is wrong here", conf=0.9, file="a.py", **kw):
    return Finding(file=file, line=line, severity=sev, category=cat, message=msg,
                   confidence=conf, **kw)


VALID = {"a.py": {1, 2, 3, 4, 5, 6, 20}}


def test_drops_low_confidence_style_and_hallucinated_files():
    res = filter_findings(
        [F(conf=0.3), F(cat="style", line=2), F(file="ghost.py"), F(line=6, msg="real issue")],
        VALID, min_confidence=0.6)
    assert [f.line for f in res.kept] == [6]
    assert res.dropped == 3


def test_snaps_nearby_lines_and_reports_far_ones():
    res = filter_findings([F(line=8, msg="near issue"), F(line=100, msg="far issue")], VALID)
    assert res.kept[0].line == 6  # snapped to nearest commentable line
    assert [f.message for f in res.unplaced] == ["far issue"]


def test_dedupe_and_cap_sorted_by_severity():
    findings = [F(line=2, sev="low", msg="dup A", conf=0.7), F(line=2, sev="high", msg="dup B"),
                F(line=4, sev="medium", msg="other thing entirely"),
                F(line=20, sev="low", msg="third distinct problem", conf=0.8)]
    res = filter_findings(findings, VALID, max_comments=2)
    assert [f.severity for f in res.kept] == ["high", "medium"]
    assert res.overflow == 1


def test_exclude_previously_posted():
    from app.filters import fingerprint
    f = F(msg="already said this")
    res = filter_findings([f], VALID, exclude_fingerprints={fingerprint(f)})
    assert res.kept == [] and res.dropped == 1


def test_same_message_on_adjacent_lines_from_one_source_is_kept_but_cross_tool_dupes_merge():
    same = [F(line=4, msg="hard-coded credential in source"), F(line=5, msg="hard-coded credential in source")]
    assert len(filter_findings(same, VALID).kept) == 2
    cross = [F(line=4, msg="Hard-coded password found in source", source="llm"),
             F(line=5, msg="Possible hardcoded password string (B105)", source="bandit")]
    assert len(filter_findings(cross, VALID).kept) == 1
