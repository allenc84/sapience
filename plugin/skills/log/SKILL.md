---
description: Judgment ledger — log a prediction, review or resolve pending assessments, and generate calibration or bias maps from your track record
---

# /log — Judgment Ledger Command

Usage:
- `/sapience:log <description>` — log a new assessment from natural language
- `/sapience:log review` — show all pending assessments
- `/sapience:log review <domain>` — show pending for one domain (one of your configured `LEDGER_DOMAINS`)
- `/sapience:log resolve <id>` — score a specific assessment by ID
- `/sapience:log calibration <domain>` — generate calibration pattern from resolved assessments in a domain (requires 3+ resolved)
- `/sapience:log bias` — generate cross-domain bias map

---

## Routing

Read `$ARGUMENTS` and route to the correct behavior below.

---

## NEW ASSESSMENT: `/sapience:log <description>`

If `$ARGUMENTS` is non-empty and does not start with `review`, `resolve`, `calibration`, or `bias`:

Extract the following from the natural language description:

- **text**: The assessment or prediction, stated clearly and specifically. Include prices, names, quantities.
- **domain**: One of the configured `LEDGER_DOMAINS`. Infer from context which domain the assessment belongs to; if none fits cleanly, pick the closest and note the inference in the confirmation.
- **probability**: a number 0-1 for how likely the call is to prove right (e.g. `0.7`). Prefer this — it's what makes calibration (Brier score) real. Extract it if the user gives odds/percentages; otherwise fall back to **confidence** (`high`/`moderate`/`low`, default `moderate`), which maps to 0.9/0.75/0.6.
- **horizon**: e.g. "3 months", "2 weeks", "end of Q3". Infer from context. Leave blank if not determinable.
- **logic**: The reasoning behind the call *at this moment*. Pull from context or ask the user if unclear.
- **conditions**: Relevant conditions — price levels, team state, market context, etc.

Call `mcp__sapience__log_assessment` with the extracted fields. Then confirm back:

> Logged: [text] | [domain] | [confidence] | horizon: [horizon] | ID: [id]

If the input is ambiguous (domain unclear, no horizon, logic missing), make reasonable inferences and note them in the confirmation rather than asking.

---

## REVIEW: `/sapience:log review [domain]`

Call `mcp__sapience__list_pending_assessments` (with domain filter if provided).

Format the results as a table:

```
ID (first 8 chars)  | Domain           | Confidence | Horizon  | Date Made  | Assessment
--------------------|------------------|------------|----------|------------|-------------------------------
549e7e85            | predictions      | moderate   | 3 months | 2025-01-15 | ACME will stay above $100...
8320726d            | decisions        | high       | 2 weeks  | 2025-01-15 | Ship feature X by end of sprint...
```

After the table, prompt: "To resolve any of these: `/sapience:log resolve <id>`"

---

## RESOLVE: `/sapience:log resolve <id>`

1. Call `mcp__sapience__list_pending_assessments` to find the assessment matching the given ID (first 8 chars is enough to identify uniquely in most cases — match on prefix).

2. Display the assessment:
   > **Assessment**: [text]
   > **Domain**: [domain] | **Made**: [date] | **Confidence**: [confidence]
   > **Logic at time**: [logic]

3. Ask the user two questions:
   - What actually happened? (the outcome)
   - Score: 1 (right), 0 (partial), or -1 (wrong)?

4. Call `mcp__sapience__resolve_assessment` with the full ID, outcome, and score.

5. Confirm: "Resolved: [score label] — [outcome]"

6. If the domain now has 3+ resolved assessments, suggest: "You now have enough data to run `/sapience:log calibration [domain]`"

---

## CALIBRATION: `/sapience:log calibration <domain>`

Call `mcp__sapience__generate_calibration` for the specified domain.

Display the result:
> **Brier**: [calibration.brier] (baseline [calibration.baseline_brier] — [beats/does not beat])
> **Forecast vs. observed**: [calibration.avg_confidence] vs [calibration.observed_rate]
> **Pattern**: [pattern]
> **Track record**: [track_record]
> **Apply as**: [instruction]
> Calibration memory saved (ID: [memory_id])

If `sufficient` is false, prefix with: "⚠️ Reflection only — below the statistical threshold ([calibration.n]/[calibration.min_n] resolved). Treat as an early signal, not an established bias."

---

## BIAS MAP: `/sapience:log bias`

Call `mcp__sapience__get_bias_map` with no domain filter.

Display:
- Primary blind spot
- Primary strength
- Per-domain summary
- Overconfident areas
- Underconfident areas
- Stats table (total/pending/resolved per domain)
