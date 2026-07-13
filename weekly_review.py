"""
Weekly judgment ledger review.

Runs automatically at session end. Checks a 6-day gate — exits silently if not due.
When due: surfaces pending assessments by horizon status, runs calibration for any
domain with 3+ resolved items, saves a summary to memory.

No web search — outcomes require manual scoring via /log resolve.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import memory_store
import ledger
import resolver

STATE_FILE = Path(__file__).parent / ".weekly_review_state.json"
INTERVAL_DAYS = 6


def _get_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_run": None}


def _save_state(ts: str) -> None:
    STATE_FILE.write_text(json.dumps({"last_run": ts}))


def _is_due() -> bool:
    state = _get_state()
    if not state.get("last_run"):
        return True
    try:
        last = datetime.fromisoformat(state["last_run"])
        return (datetime.now(timezone.utc) - last) >= timedelta(days=INTERVAL_DAYS)
    except Exception:
        return True


def _horizon_status(item: dict) -> str:
    horizon = item.get("horizon", "")
    made = item.get("date_made", "")
    if not horizon or not made:
        return "active"
    try:
        made_dt = datetime.fromisoformat(made)
    except Exception:
        return "active"

    m = re.search(r"(\d+)\s*(day|week|month)", horizon.lower())
    if not m:
        return "active"

    qty, unit = int(m.group(1)), m.group(2)
    if unit == "day":
        delta = timedelta(days=qty)
    elif unit == "week":
        delta = timedelta(weeks=qty)
    else:
        delta = timedelta(days=qty * 30)

    due_dt = made_dt + delta
    now = datetime.now(timezone.utc)
    if now > due_dt:
        return f"OVERDUE (was due {due_dt.date()})"
    if now > due_dt - timedelta(days=7):
        return f"DUE SOON ({due_dt.date()})"
    return "active"


def run() -> dict:
    if not _is_due():
        return {"status": "skipped"}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"status": "error", "reason": "no API key"}

    now_str = datetime.now(timezone.utc).isoformat()
    pending = ledger.list_pending()

    overdue, due_soon, active = [], [], []
    for item in pending:
        hs = _horizon_status(item)
        item["_hs"] = hs
        if hs.startswith("OVERDUE"):
            overdue.append(item)
        elif hs.startswith("DUE SOON"):
            due_soon.append(item)
        else:
            active.append(item)

    # Run calibration for domains with sufficient resolved data
    calibrations = []
    for domain in sorted(ledger.DOMAINS):
        if ledger.resolved_count_for_domain(domain) >= 3:
            try:
                result = resolver.generate_calibration(domain)
                if result:
                    calibrations.append(result)
            except Exception as e:
                calibrations.append({"domain": domain, "error": str(e)})

    # Build summary
    lines = [
        f"WEEKLY LEDGER REVIEW — {datetime.now(timezone.utc).date()}",
        f"Pending: {len(pending)} total | {len(overdue)} overdue | {len(due_soon)} due soon | {len(active)} active",
    ]

    if overdue:
        lines.append("\nOVERDUE — score these with /log resolve:")
        for item in overdue:
            lines.append(f"  [{item['domain']}] {item['text'][:120]} ({item['_hs']})")

    if due_soon:
        lines.append("\nDUE SOON:")
        for item in due_soon:
            lines.append(f"  [{item['domain']}] {item['text'][:120]} ({item['_hs']})")

    if not pending:
        lines.append("\nNo pending assessments. Add calls with /log.")

    if calibrations:
        lines.append(f"\nCalibration updated — {len(calibrations)} domain(s):")
        for c in calibrations:
            if "pattern" in c:
                lines.append(f"  [{c['domain']}] {c['pattern'][:160]}")

    summary = "\n".join(lines)

    try:
        memory_store.save(
            content=summary,
            memory_type="project",
            salience=0.7,
            source="weekly_review",
            topic="judgment-ledger",
            metadata={
                "pending": len(pending),
                "overdue": len(overdue),
                "calibrations_run": len(calibrations),
            },
        )
    except Exception as e:
        print(f"[weekly_review] memory save skipped: {e}", file=sys.stderr)

    _save_state(now_str)

    return {
        "status": "complete",
        "pending": len(pending),
        "overdue": len(overdue),
        "due_soon": len(due_soon),
        "calibrations": len(calibrations),
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
