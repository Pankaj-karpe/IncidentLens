from app.workers.tasks import _cap_confidence


def test_both_collectors_failed_caps_at_low():
    ctx = {"metrics": {"error": "x"}, "logs": {"error": "y"}}
    assert _cap_confidence("high", ctx) == "low"


def test_one_collector_failed_caps_at_medium():
    ctx = {"metrics": {"error": "x"}, "logs": {"error": None}}
    assert _cap_confidence("high", ctx) == "medium"
    assert _cap_confidence("low", ctx) == "low"          # never raised, only capped


def test_no_failures_keeps_the_models_confidence():
    ctx = {"metrics": {"error": None}, "logs": {"error": None}}
    assert _cap_confidence("high", ctx) == "high"


def test_empty_results_without_an_error_are_not_a_failure():
    # "logs: nothing found" is real evidence of a quiet service, so it is not capped
    ctx = {"metrics": {"available": False, "error": None}, "logs": {"available": False, "error": None}}
    assert _cap_confidence("medium", ctx) == "medium"