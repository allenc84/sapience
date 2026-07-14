"""
Assessment ledger — SQLite-backed structured store for forward-looking assessments.

Separate from ChromaDB because we need queryable state: pending/resolved by domain,
filterable by date and score. Calibration outputs still flow through memory_store
into ChromaDB as high-salience feedback memories.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os

from .paths import data_dir

LEDGER_PATH = Path(os.environ.get("LEDGER_DB_PATH") or (data_dir() / "ledger.db"))

# Judgment domains this ledger tracks. Customize via LEDGER_DOMAINS in your .env
# (comma-separated), e.g. LEDGER_DOMAINS="investments,team-management,strategic-bets".
DOMAINS = {
    d.strip()
    for d in os.environ.get("LEDGER_DOMAINS", "predictions,decisions,commitments").split(",")
    if d.strip()
}
CONFIDENCE_LEVELS = {"high", "moderate", "low"}

# Categorical confidence <-> probability. A user may log either a numeric
# probability (0-1) or a categorical level; we always store both, so logging
# stays low-friction while calibration can be computed quantitatively.
CONFIDENCE_TO_PROB = {"high": 0.9, "moderate": 0.75, "low": 0.6}

# Minimum resolved assessments before a *statistical* calibration claim is made.
# Below this the system still reflects qualitatively, but labels it explicitly as
# "reflection, not statistics" — a bias is not a bias at n=3.
MIN_CALIBRATION_N = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prob_to_confidence(p: float) -> str:
    if p >= 0.85:
        return "high"
    if p >= 0.65:
        return "moderate"
    return "low"


def _clamp_prob(p: float) -> float:
    """Keep forecasts strictly inside (0,1) — no absolute certainty."""
    return max(0.01, min(0.99, float(p)))


def _outcome_value(score: int) -> float:
    """Map a resolution score to a [0,1] outcome for Brier scoring:
    1 = right -> 1.0, 0 = partial -> 0.5, -1 = wrong -> 0.0."""
    return {1: 1.0, 0: 0.5, -1: 0.0}[score]


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(LEDGER_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assessments (
                id              TEXT PRIMARY KEY,
                domain          TEXT NOT NULL,
                text            TEXT NOT NULL,
                confidence      TEXT DEFAULT 'moderate',
                probability     REAL DEFAULT NULL,
                horizon         TEXT DEFAULT '',
                logic           TEXT DEFAULT '',
                conditions      TEXT DEFAULT '',
                date_made       TEXT NOT NULL,
                status          TEXT DEFAULT 'pending',
                outcome         TEXT DEFAULT '',
                outcome_date    TEXT DEFAULT '',
                score           INTEGER DEFAULT NULL,
                source_session  TEXT DEFAULT ''
            )
        """)
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent migrations for pre-existing ledgers."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(assessments)").fetchall()}
    if "probability" not in cols:
        conn.execute("ALTER TABLE assessments ADD COLUMN probability REAL DEFAULT NULL")
    # Backfill probability from the categorical confidence for legacy rows.
    conn.execute(
        """UPDATE assessments SET probability = CASE confidence
               WHEN 'high' THEN 0.9 WHEN 'moderate' THEN 0.75 WHEN 'low' THEN 0.6
               ELSE 0.75 END
           WHERE probability IS NULL"""
    )


def log_assessment(
    text: str,
    domain: str,
    confidence: str = "moderate",
    probability: Optional[float] = None,
    horizon: str = "",
    logic: str = "",
    conditions: str = "",
    source_session: str = "",
) -> str:
    if domain not in DOMAINS:
        raise ValueError(f"Invalid domain '{domain}'. Must be one of: {sorted(DOMAINS)}")

    # Prefer an explicit numeric probability; otherwise derive it from the
    # categorical level. Always persist both so calibration is quantitative.
    if probability is not None:
        probability = _clamp_prob(probability)
        confidence = _prob_to_confidence(probability)
    else:
        if confidence not in CONFIDENCE_LEVELS:
            confidence = "moderate"
        probability = CONFIDENCE_TO_PROB[confidence]

    init_db()
    aid = str(uuid.uuid4())

    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO assessments
               (id, domain, text, confidence, probability, horizon, logic, conditions, date_made, status, source_session)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (aid, domain, text, confidence, probability, horizon, logic, conditions, _now(), source_session),
        )
        conn.commit()

    return aid


def resolve(
    assessment_id: str,
    outcome: str,
    score: int,
    outcome_date: str = "",
) -> bool:
    if score not in (-1, 0, 1):
        raise ValueError("score must be -1 (wrong), 0 (partial), or 1 (right)")

    init_db()
    status = "resolved" if score != 0 else "partial"

    with _get_conn() as conn:
        cursor = conn.execute(
            """UPDATE assessments
               SET outcome=?, outcome_date=?, score=?, status=?
               WHERE id=? AND status='pending'""",
            (outcome, outcome_date or _now(), score, status, assessment_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_pending(domain: Optional[str] = None, limit: int = 50) -> list[dict]:
    init_db()
    with _get_conn() as conn:
        if domain:
            rows = conn.execute(
                "SELECT * FROM assessments WHERE status='pending' AND domain=? ORDER BY date_made DESC LIMIT ?",
                (domain, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM assessments WHERE status='pending' ORDER BY date_made DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def list_resolved(domain: Optional[str] = None, limit: int = 100) -> list[dict]:
    init_db()
    with _get_conn() as conn:
        if domain:
            rows = conn.execute(
                "SELECT * FROM assessments WHERE status != 'pending' AND domain=? ORDER BY outcome_date DESC LIMIT ?",
                (domain, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM assessments WHERE status != 'pending' ORDER BY outcome_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_by_id(assessment_id: str) -> Optional[dict]:
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM assessments WHERE id=?", (assessment_id,)
        ).fetchone()
    return dict(row) if row else None


def calibration(domain: Optional[str] = None) -> dict:
    """Quantitative calibration over resolved assessments that have both a
    probability and a score.

    Returns a Brier score (mean squared error of forecast vs. outcome; lower is
    better, 0 is perfect), a base-rate baseline for comparison, a reliability
    breakdown by confidence band, and over/under-confidence flags. `sufficient`
    is False until MIN_CALIBRATION_N resolutions exist — below that, treat the
    numbers as reflection, not a calibrated claim.
    """
    init_db()
    q = ("SELECT probability, score FROM assessments "
         "WHERE score IS NOT NULL AND probability IS NOT NULL")
    params: list = []
    if domain:
        q += " AND domain=?"
        params.append(domain)
    with _get_conn() as conn:
        rows = conn.execute(q, params).fetchall()

    pairs = [(float(r["probability"]), _outcome_value(r["score"])) for r in rows]
    n = len(pairs)
    result = {"n": n, "sufficient": n >= MIN_CALIBRATION_N, "min_n": MIN_CALIBRATION_N}
    if n == 0:
        result["brier"] = None
        return result

    brier = sum((p - o) ** 2 for p, o in pairs) / n
    mean_conf = sum(p for p, _ in pairs) / n
    mean_outcome = sum(o for _, o in pairs) / n
    baseline = mean_outcome * (1 - mean_outcome)  # always-predict-base-rate Brier

    buckets = []
    for lo, hi, label in [(0.0, 0.65, "low"), (0.65, 0.85, "moderate"), (0.85, 1.01, "high")]:
        b = [(p, o) for p, o in pairs if lo <= p < hi]
        if b:
            buckets.append({
                "band": label,
                "n": len(b),
                "avg_confidence": round(sum(p for p, _ in b) / len(b), 3),
                "observed_rate": round(sum(o for _, o in b) / len(b), 3),
            })

    result.update({
        "brier": round(brier, 4),
        "baseline_brier": round(baseline, 4),
        "beats_baseline": brier < baseline,
        "hit_rate": round(sum(1 for _, o in pairs if o == 1.0) / n, 3),
        "avg_confidence": round(mean_conf, 3),
        "observed_rate": round(mean_outcome, 3),
        "overconfident": (mean_conf - mean_outcome) > 0.1,
        "underconfident": (mean_outcome - mean_conf) > 0.1,
        "buckets": buckets,
    })
    return result


def get_stats() -> dict:
    init_db()
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]

        by_domain = {}
        for domain in sorted(DOMAINS):
            pending = conn.execute(
                "SELECT COUNT(*) FROM assessments WHERE domain=? AND status='pending'", (domain,)
            ).fetchone()[0]
            resolved = conn.execute(
                "SELECT COUNT(*) FROM assessments WHERE domain=? AND status!='pending'", (domain,)
            ).fetchone()[0]
            by_domain[domain] = {"pending": pending, "resolved": resolved}

        accuracy = {}
        for domain in sorted(DOMAINS):
            rows = conn.execute(
                "SELECT score FROM assessments WHERE domain=? AND score IS NOT NULL", (domain,)
            ).fetchall()
            if rows:
                scores = [r[0] for r in rows]
                accuracy[domain] = {
                    "total": len(scores),
                    "right": scores.count(1),
                    "partial": scores.count(0),
                    "wrong": scores.count(-1),
                    "accuracy_pct": round(100 * scores.count(1) / len(scores), 1),
                }

    calib = {"overall": calibration()}
    for domain in sorted(DOMAINS):
        c = calibration(domain)
        if c["n"] > 0:
            calib[domain] = c

    return {"total": total, "by_domain": by_domain, "accuracy": accuracy, "calibration": calib}


def resolved_count_for_domain(domain: str) -> int:
    init_db()
    with _get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM assessments WHERE domain=? AND score IS NOT NULL", (domain,)
        ).fetchone()[0]
