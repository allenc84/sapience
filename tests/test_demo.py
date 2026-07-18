"""Keyless sanity checks on the demo dataset's designed calibration story.

The demo exists to show the bias map finding real patterns: overconfident on
product-bets, well-calibrated on hiring, underconfident on growth, with
enough binary resolutions to clear the statistical gate. These tests keep
content edits from silently breaking that story.
"""

from sapience.demo import ASSESSMENTS, DEMO_DOMAINS, MEMORIES
from sapience.ledger import MIN_CALIBRATION_N
from sapience.schema import MEMORY_TYPES


def _binary(domain):
    return [(prob, score) for _, d, prob, *_, score in ASSESSMENTS
            if d == domain and score in (1, -1)]


def _rates(domain):
    rows = _binary(domain)
    forecast = sum(p for p, _ in rows) / len(rows)
    observed = sum(1 for _, s in rows if s == 1) / len(rows)
    return forecast, observed


def test_memories_well_formed():
    for days_ago, mtype, topic, salience, content in MEMORIES:
        assert mtype in MEMORY_TYPES
        assert 0.0 <= salience <= 1.0
        assert days_ago > 0 and topic and len(content) > 40


def test_assessments_well_formed():
    domains = set(DEMO_DOMAINS.split(","))
    for days_ago, domain, prob, text, logic, horizon_days, outcome, score in ASSESSMENTS:
        assert domain in domains
        assert 0.0 < prob < 1.0
        assert score in (1, 0, -1, None)
        assert (outcome is None) == (score is None)
        assert days_ago > 0 and horizon_days > 0 and text and logic


def test_enough_data_for_statistical_calibration():
    total_binary = sum(len(_binary(d)) for d in DEMO_DOMAINS.split(","))
    assert total_binary >= MIN_CALIBRATION_N
    for d in DEMO_DOMAINS.split(","):
        assert len(_binary(d)) >= 3, f"{d} below per-domain calibration minimum"


def test_designed_calibration_story():
    forecast, observed = _rates("product-bets")
    assert forecast - observed >= 0.3, "product-bets must read as clearly overconfident"

    forecast, observed = _rates("hiring")
    assert abs(forecast - observed) <= 0.2, "hiring must read as well-calibrated"

    forecast, observed = _rates("growth")
    assert observed - forecast >= 0.15, "growth must read as underconfident"


def test_pending_includes_overdue():
    pending = [(days_ago, horizon)
               for days_ago, _, _, _, _, horizon, _, score in ASSESSMENTS
               if score is None]
    assert len(pending) >= 3
    overdue = [1 for days_ago, horizon in pending if days_ago > horizon]
    assert len(overdue) >= 2, "demo needs overdue calls for the /log review beat"
