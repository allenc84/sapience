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

LEDGER_PATH = Path(os.environ.get("LEDGER_DB_PATH", Path(__file__).parent / "ledger.db"))

# Judgment domains this ledger tracks. Customize via LEDGER_DOMAINS in your .env
# (comma-separated), e.g. LEDGER_DOMAINS="investments,team-management,strategic-bets".
DOMAINS = {
    d.strip()
    for d in os.environ.get("LEDGER_DOMAINS", "predictions,decisions,commitments").split(",")
    if d.strip()
}
CONFIDENCE_LEVELS = {"high", "moderate", "low"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        conn.commit()


def log_assessment(
    text: str,
    domain: str,
    confidence: str = "moderate",
    horizon: str = "",
    logic: str = "",
    conditions: str = "",
    source_session: str = "",
) -> str:
    if domain not in DOMAINS:
        raise ValueError(f"Invalid domain '{domain}'. Must be one of: {sorted(DOMAINS)}")
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "moderate"

    init_db()
    aid = str(uuid.uuid4())

    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO assessments
               (id, domain, text, confidence, horizon, logic, conditions, date_made, status, source_session)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (aid, domain, text, confidence, horizon, logic, conditions, _now(), source_session),
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

    return {"total": total, "by_domain": by_domain, "accuracy": accuracy}


def resolved_count_for_domain(domain: str) -> int:
    init_db()
    with _get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM assessments WHERE domain=? AND score IS NOT NULL", (domain,)
        ).fetchone()[0]
