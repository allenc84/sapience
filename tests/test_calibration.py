"""Calibration math regression tests.

These cover the three correctness bugs found in the 2026-07 external review:
partial outcomes corrupting the Brier baseline, a perfect 0.0 Brier rendering
as "no data", and domain-filtered reports grounded in overall numbers.
"""

from sapience.resolver import _calibration_numbers_block


def _log_resolved(ledger, domain, probability, score):
    aid = ledger.log_assessment(text="t", domain=domain, probability=probability)
    assert ledger.resolve(aid, outcome="o", score=score)
    return aid


def test_partials_excluded_from_brier(ledger):
    _log_resolved(ledger, "predictions", 0.8, 1)
    _log_resolved(ledger, "predictions", 0.8, 1)
    _log_resolved(ledger, "predictions", 0.8, -1)
    _log_resolved(ledger, "predictions", 0.9, 0)  # partial — must not be scored

    cal = ledger.calibration("predictions")
    assert cal["n"] == 3
    assert cal["n_partial_excluded"] == 1
    # Brier over the three binary outcomes only: ((.2)^2 + (.2)^2 + (.8)^2) / 3
    assert cal["brier"] == round((0.04 + 0.04 + 0.64) / 3, 4)
    # Base rate 2/3 -> baseline 2/9, from binary outcomes only
    assert cal["baseline_brier"] == round(2 / 9, 4)


def test_constant_base_rate_forecast_does_not_beat_baseline(ledger):
    # Forecasting exactly the base rate must score equal to the baseline,
    # never beat it. 0.5 and the 1/0 outcomes are exact binary floats, so
    # equality here is deterministic.
    _log_resolved(ledger, "predictions", 0.5, 1)
    _log_resolved(ledger, "predictions", 0.5, -1)

    cal = ledger.calibration("predictions")
    assert cal["brier"] == cal["baseline_brier"] == 0.25
    assert cal["beats_baseline"] is False


def test_perfect_brier_is_not_treated_as_missing():
    block = _calibration_numbers_block({
        "brier": 0.0,
        "n": 3,
        "min_n": 20,
        "sufficient": False,
        "baseline_brier": 0.25,
        "beats_baseline": True,
        "avg_confidence": 0.9,
        "observed_rate": 1.0,
    })
    assert "No scored forecasts" not in block
    assert "Brier score: 0.0" in block


def test_no_data_block_when_brier_is_none():
    assert "No scored forecasts" in _calibration_numbers_block({"brier": None, "n": 0})
    assert "No scored forecasts" in _calibration_numbers_block({})


def test_domain_filter_isolates_numbers(ledger):
    _log_resolved(ledger, "predictions", 0.9, 1)
    _log_resolved(ledger, "predictions", 0.9, 1)
    _log_resolved(ledger, "decisions", 0.6, -1)

    cal = ledger.calibration("predictions")
    assert cal["n"] == 2
    assert cal["observed_rate"] == 1.0
    cal_all = ledger.calibration()
    assert cal_all["n"] == 3


def test_pending_forecasts_surface_in_calibration(ledger):
    _log_resolved(ledger, "predictions", 0.8, 1)
    ledger.log_assessment(text="unresolved", domain="predictions", probability=0.7)

    cal = ledger.calibration("predictions")
    assert cal["pending"] == 1
    block = _calibration_numbers_block(cal)
    assert "Unresolved (pending) forecasts: 1" in block
