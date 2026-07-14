"""
Resolution and calibration logic.

Reads resolved assessments from the ledger, extracts calibration patterns,
and writes high-salience feedback memories into ChromaDB via memory_store.
The weekly cron agent calls generate_calibration() per domain after running resolutions.
"""

import os
from typing import Optional

import anthropic

from . import memory_store
from . import ledger
from .schema import USER_CONTEXT


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)


def _calibration_numbers_block(cal: dict) -> str:
    """Render the quantitative calibration as a compact block for the LLM prompt
    and the saved memory, so every narrative claim is anchored to real numbers."""
    if not cal.get("brier"):
        return "No scored forecasts yet — no quantitative calibration available."
    lines = [
        f"n resolved: {cal['n']} (statistical threshold: {cal['min_n']} — "
        f"{'MET' if cal['sufficient'] else 'NOT met, treat as reflection'})",
        f"Brier score: {cal['brier']} (base-rate baseline {cal['baseline_brier']}; "
        f"{'beats' if cal['beats_baseline'] else 'does NOT beat'} baseline — lower is better)",
        f"Avg forecast: {cal['avg_confidence']}  vs  observed hit rate: {cal['observed_rate']}",
        f"Full-right rate: {cal['hit_rate']}",
    ]
    if cal.get("overconfident"):
        lines.append("Signal: OVERCONFIDENT (forecasts exceed outcomes by >10pts)")
    elif cal.get("underconfident"):
        lines.append("Signal: UNDERCONFIDENT (outcomes exceed forecasts by >10pts)")
    for b in cal.get("buckets", []):
        lines.append(f"  {b['band']} band: n={b['n']}, avg forecast {b['avg_confidence']} → observed {b['observed_rate']}")
    return "\n".join(lines)


def generate_calibration(domain: str) -> Optional[dict]:
    """
    Extract a calibration read from resolved assessments in a domain, grounded in
    the quantitative Brier/reliability stats. Writes a high-salience feedback
    memory. Returns None only if there is nothing scored yet. Below
    ledger.MIN_CALIBRATION_N the result is flagged reflection-only, not a
    statistically established bias.
    """
    resolved = ledger.list_resolved(domain=domain, limit=100)
    if len(resolved) < 3:
        return None

    cal = ledger.calibration(domain)
    numbers_block = _calibration_numbers_block(cal)

    assessment_text = "\n\n".join(
        f"[{a['date_made'][:10]} | forecast={a.get('probability')} ({a['confidence']}) | "
        f"score={'RIGHT' if a['score'] == 1 else ('PARTIAL' if a['score'] == 0 else 'WRONG')}]\n"
        f"Assessment: {a['text']}\n"
        f"Logic at time: {a['logic'] or 'not recorded'}\n"
        f"Conditions: {a['conditions'] or 'not recorded'}\n"
        f"Outcome: {a['outcome']}"
        for a in resolved
    )

    client = _get_client()
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=800,
        tools=[{
            "name": "calibration_pattern",
            "description": "Extract calibration pattern from resolved assessments",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The core calibration statement — what bias exists, under what conditions, with what track record",
                    },
                    "conditions": {
                        "type": "string",
                        "description": "The specific conditions under which this pattern holds",
                    },
                    "track_record": {
                        "type": "string",
                        "description": "Quantified track record e.g. '7/7 wrong', '4/5 right'",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Specific instruction for future sessions — what to apply differently",
                    },
                },
                "required": ["pattern", "conditions", "track_record", "instruction"],
            },
        }],
        tool_choice={"type": "tool", "name": "calibration_pattern"},
        messages=[{
            "role": "user",
            "content": (
                f'Analyze these resolved assessments in the domain "{domain}" for {USER_CONTEXT}.\n\n'
                f"QUANTITATIVE CALIBRATION (ground every claim in these numbers):\n{numbers_block}\n\n"
                f"Extract the primary calibration pattern — where is judgment off, under what "
                f"conditions, and what should change in future sessions. Anchor the track_record "
                f"in the Brier/reliability numbers above.\n\n"
                + (
                    "NOTE: the sample is below the statistical threshold. Frame this as a TENTATIVE "
                    "reflection and an early signal to watch — NOT an established bias. Do not overclaim.\n\n"
                    if not cal["sufficient"] else
                    "The sample meets the statistical threshold; you may state calibration patterns as established.\n\n"
                )
                + f"RESOLVED ASSESSMENTS:\n{assessment_text}"
            ),
        }],
    )

    extracted = response.content[0].input

    header = "CALIBRATION" if cal["sufficient"] else "CALIBRATION (reflection — below statistical threshold)"
    content = (
        f"{header} — {domain.upper()}\n\n"
        f"Quantitative read:\n{numbers_block}\n\n"
        f"Pattern: {extracted['pattern']}\n\n"
        f"Conditions where this applies: {extracted['conditions']}\n\n"
        f"Track record: {extracted['track_record']}\n\n"
        f"Apply as: {extracted['instruction']}"
    )

    # Idempotency: replace any prior calibration feedback for this domain so
    # re-running (e.g. weekly) updates the pattern instead of duplicating it.
    memory_store.delete_where({
        "$and": [
            {"type": {"$eq": "feedback"}},
            {"source": {"$eq": "resolver"}},
            {"topic": {"$eq": f"calibration-{domain}"}},
        ]
    })

    mid = memory_store.save(
        content=content,
        memory_type="feedback",
        salience=0.9,
        source="resolver",
        topic=f"calibration-{domain}",
        metadata={"domain": domain, "assessment_count": len(resolved)},
    )

    return {
        "memory_id": mid,
        "domain": domain,
        "assessments_used": len(resolved),
        "sufficient": cal["sufficient"],
        "calibration": cal,
        "pattern": extracted["pattern"],
        "track_record": extracted["track_record"],
        "instruction": extracted["instruction"],
    }


def generate_bias_map(domain: Optional[str] = None) -> dict:
    """
    Generate a structured bias report across all resolved assessments.
    Used by the get_bias_map tool and the quarterly review ritual.
    """
    stats = ledger.get_stats()
    resolved = ledger.list_resolved(domain=domain, limit=200)

    if not resolved:
        return {"status": "no_resolved_assessments", "stats": stats}

    overall_cal = stats.get("calibration", {}).get("overall", {})
    numbers_block = _calibration_numbers_block(overall_cal)
    sufficient = overall_cal.get("sufficient", False)

    assessment_text = "\n\n".join(
        f"[{a['domain']} | {a['date_made'][:10]} | confidence={a['confidence']} | "
        f"score={'RIGHT' if a['score'] == 1 else ('PARTIAL' if a['score'] == 0 else 'WRONG')}]\n"
        f"{a['text']}\nOutcome: {a['outcome']}"
        for a in resolved
    )

    client = _get_client()
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1500,
        tools=[{
            "name": "bias_map",
            "description": "Structured bias map from resolved assessments",
            "input_schema": {
                "type": "object",
                "properties": {
                    "well_calibrated": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Areas where judgment has been accurate, with evidence",
                    },
                    "systematically_overconfident": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Areas of consistent overconfidence, with conditions and track record",
                    },
                    "systematically_underconfident": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Areas where confidence was too low relative to outcomes",
                    },
                    "domain_summary": {
                        "type": "object",
                        "description": "Key calibration insight per domain as domain→insight string pairs",
                    },
                    "primary_blind_spot": {
                        "type": "string",
                        "description": "The single most important systematic error",
                    },
                    "primary_strength": {
                        "type": "string",
                        "description": "The single most reliable area of good judgment",
                    },
                },
                "required": [
                    "well_calibrated",
                    "systematically_overconfident",
                    "domain_summary",
                    "primary_blind_spot",
                    "primary_strength",
                ],
            },
        }],
        tool_choice={"type": "tool", "name": "bias_map"},
        messages=[{
            "role": "user",
            "content": (
                f"Generate a bias map for {USER_CONTEXT} based on these resolved assessments.\n\n"
                f"QUANTITATIVE CALIBRATION (ground claims in these numbers):\n{numbers_block}\n\n"
                "Be specific — name conditions under which judgment is good vs. poor. "
                "Quantify where possible. This is for a quarterly self-calibration review.\n\n"
                + (
                    "NOTE: sample is below the statistical threshold — frame findings as tentative "
                    "signals to watch, not established biases.\n\n"
                    if not sufficient else ""
                )
                + f"ASSESSMENTS:\n{assessment_text}"
            ),
        }],
    )

    bias_map = response.content[0].input
    bias_map["stats"] = stats
    bias_map["total_resolved"] = len(resolved)
    bias_map["sufficient"] = sufficient

    return bias_map
