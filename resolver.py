"""
Resolution and calibration logic.

Reads resolved assessments from the ledger, extracts calibration patterns,
and writes high-salience feedback memories into ChromaDB via memory_store.
The weekly cron agent calls generate_calibration() per domain after running resolutions.
"""

import os
from typing import Optional

import anthropic

import memory_store
from schema import USER_CONTEXT
import ledger


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)


def generate_calibration(domain: str) -> Optional[dict]:
    """
    Extract calibration pattern from all resolved assessments in a domain.
    Writes a high-salience feedback memory to ChromaDB. Returns summary or None
    if not enough data.
    """
    resolved = ledger.list_resolved(domain=domain, limit=100)

    if len(resolved) < 3:
        return None

    assessment_text = "\n\n".join(
        f"[{a['date_made'][:10]} | confidence={a['confidence']} | "
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
                f"Extract the primary calibration pattern — where is judgment systematically off, "
                f"under what conditions, and what should change in future sessions.\n\n"
                f"Be honest and specific. This will be injected as a prior into future sessions "
                f"to prevent repeating the same errors.\n\n"
                f"RESOLVED ASSESSMENTS:\n{assessment_text}"
            ),
        }],
    )

    extracted = response.content[0].input

    content = (
        f"CALIBRATION — {domain.upper()}\n\n"
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
                "Be specific — name conditions under which judgment is good vs. poor. "
                "Quantify where possible. This is for a quarterly self-calibration review.\n\n"
                f"ASSESSMENTS:\n{assessment_text}"
            ),
        }],
    )

    bias_map = response.content[0].input
    bias_map["stats"] = stats
    bias_map["total_resolved"] = len(resolved)

    return bias_map
