# /log — Judgment Ledger Command

Usage:
- `/log <description>` — log a new assessment from natural language
- `/log review` — show all pending assessments
- `/log review <domain>` — show pending for one domain (one of your configured `LEDGER_DOMAINS`)
- `/log resolve <id>` — score a specific assessment by ID
- `/log calibration <domain>` — generate calibration pattern from resolved assessments in a domain (requires 3+ resolved)
- `/log bias` — generate cross-domain bias map

---

## Routing

Read `$ARGUMENTS` and route to the correct behavior below.

---

## NEW ASSESSMENT: `/log <description>`

If `$ARGUMENTS` is non-empty and does not start with `review`, `resolve`, `calibration`, or `bias`:

Extract the following from the natural language description:

- **text**: The assessment or prediction, stated clearly and specifically. Include prices, names, quantities.
- **domain**: One of the configured `LEDGER_DOMAINS`. Infer from context which domain the assessment belongs to; if none fits cleanly, pick the closest and note the inference in the confirmation.
- **confidence**: `high` / `moderate` / `low`. Default `moderate` unless stated.
- **horizon**: e.g. "3 months", "2 weeks", "end of Q3". Infer from context. Leave blank if not determinable.
- **logic**: The reasoning behind the call *at this moment*. Pull from context or ask the user if unclear.
- **conditions**: Relevant conditions — price levels, team state, market context, etc.

Call `mcp__claude-memory__log_assessment` with the extracted fields. Then confirm back:

> Logged: [text] | [domain] | [confidence] | horizon: [horizon] | ID: [id]

If the input is ambiguous (domain unclear, no horizon, logic missing), make reasonable inferences and note them in the confirmation rather than asking.

---

## REVIEW: `/log review [domain]`

Call `mcp__claude-memory__list_pending_assessments` (with domain filter if provided).

Format the results as a table:

```
ID (first 8 chars)  | Domain           | Confidence | Horizon  | Date Made  | Assessment
--------------------|------------------|------------|----------|------------|-------------------------------
549e7e85            | predictions      | moderate   | 3 months | 2025-01-15 | ACME will stay above $100...
8320726d            | decisions        | high       | 2 weeks  | 2025-01-15 | Ship feature X by end of sprint...
```

After the table, prompt: "To resolve any of these: `/log resolve <id>`"

---

## RESOLVE: `/log resolve <id>`

1. Call `mcp__claude-memory__list_pending_assessments` to find the assessment matching the given ID (first 8 chars is enough to identify uniquely in most cases — match on prefix).

2. Display the assessment:
   > **Assessment**: [text]
   > **Domain**: [domain] | **Made**: [date] | **Confidence**: [confidence]
   > **Logic at time**: [logic]

3. Ask the user two questions:
   - What actually happened? (the outcome)
   - Score: 1 (right), 0 (partial), or -1 (wrong)?

4. Call `mcp__claude-memory__resolve_assessment` with the full ID, outcome, and score.

5. Confirm: "Resolved: [score label] — [outcome]"

6. If the domain now has 3+ resolved assessments, suggest: "You now have enough data to run `/log calibration [domain]`"

---

## CALIBRATION: `/log calibration <domain>`

Call `mcp__claude-memory__generate_calibration` for the specified domain.

Display the result:
> **Pattern**: [pattern]
> **Track record**: [track_record]
> **Apply as**: [instruction]
> Calibration memory saved (ID: [memory_id])

---

## BIAS MAP: `/log bias`

Call `mcp__claude-memory__get_bias_map` with no domain filter.

Display:
- Primary blind spot
- Primary strength
- Per-domain summary
- Overconfident areas
- Underconfident areas
- Stats table (total/pending/resolved per domain)
