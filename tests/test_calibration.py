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


def _backdate(ledger, aid, days_ago):
    from datetime import datetime, timedelta, timezone
    made = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with ledger._get_conn() as conn:
        conn.execute("UPDATE assessments SET date_made=? WHERE id=?", (made.isoformat(), aid))
        conn.commit()


def test_wilson_ci_present_and_sane(ledger):
    for score in (1, 1, 1, -1):
        _log_resolved(ledger, "predictions", 0.7, score)

    cal = ledger.calibration("predictions")
    lo, hi = cal["observed_rate_ci95"]
    assert 0.0 <= lo <= cal["observed_rate"] <= hi <= 1.0
    # n=4 must produce a wide interval, not false precision
    assert hi - lo > 0.4


def test_band_reliability_flag(ledger):
    for _ in range(5):
        _log_resolved(ledger, "predictions", 0.9, 1)   # high band, n=5
    _log_resolved(ledger, "predictions", 0.6, 1)       # low band, n=1

    cal = ledger.calibration("predictions")
    bands = {b["band"]: b for b in cal["buckets"]}
    assert bands["high"]["reliable"] is True
    assert bands["low"]["reliable"] is False
    assert all("observed_rate_ci95" in b for b in cal["buckets"])


def test_overconfidence_requires_ci_exclusion(ledger):
    # 2 of 3 right at 0.9: gap >10pts but CI is huge at n=3 — no claim.
    _log_resolved(ledger, "predictions", 0.9, 1)
    _log_resolved(ledger, "predictions", 0.9, 1)
    _log_resolved(ledger, "predictions", 0.9, -1)
    cal = ledger.calibration("predictions")
    assert cal["overconfident"] is False

    # 4 of 16 right at 0.9: CI upper bound sits below the forecast — claim it.
    for score in [1, 1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1]:
        _log_resolved(ledger, "predictions", 0.9, score)
    cal = ledger.calibration("predictions")
    assert cal["overconfident"] is True


def test_overdue_unresolved_tracked(ledger):
    # Two pending past horizon, one pending still active, two resolved.
    a1 = ledger.log_assessment(text="t", domain="decisions", probability=0.7, horizon="2 weeks")
    _backdate(ledger, a1, 30)
    a2 = ledger.log_assessment(text="t", domain="decisions", probability=0.7, horizon="10 days")
    _backdate(ledger, a2, 20)
    a3 = ledger.log_assessment(text="t", domain="decisions", probability=0.7, horizon="3 months")
    _backdate(ledger, a3, 10)
    _log_resolved(ledger, "decisions", 0.8, 1)
    _log_resolved(ledger, "decisions", 0.8, -1)

    cal = ledger.calibration("decisions")
    assert cal["overdue_unresolved"] == 2
    assert cal["pending"] == 3
    # 2 scored out of (2 scored + 2 overdue)
    assert cal["resolution_rate"] == 0.5


def test_numbers_block_warns_on_selective_resolution():
    block = _calibration_numbers_block({
        "brier": 0.2, "n": 6, "min_n": 20, "sufficient": False,
        "baseline_brier": 0.25, "beats_baseline": True,
        "avg_confidence": 0.8, "observed_rate": 0.83,
        "observed_rate_ci95": [0.44, 0.97],
        "overdue_unresolved": 4, "resolution_rate": 0.6,
        "buckets": [{"band": "high", "n": 2, "avg_confidence": 0.9,
                     "observed_rate": 1.0, "observed_rate_ci95": [0.34, 1.0],
                     "reliable": False}],
    })
    assert "Selective resolution" in block
    assert "upper bound" in block
    assert "UNRELIABLE" in block
    assert "0.44" in block and "0.97" in block
