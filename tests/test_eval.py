from eval.run_eval import run


def test_baseline_eval_is_healthy(settings):
    results, m = run(settings)
    assert len(results) >= 7
    assert m["recall"] == 1.0 and m["precision"] >= 0.9
    clean = next(r for r in results if r.name == "clean_change")
    assert clean.fp == 0  # no noise on a harmless change
