"""
Seed a self-contained demo dataset: a fictional founder's memories and
judgment ledger, with a designed calibration story the bias map can find —
overconfident on product bets, well-calibrated on hiring, underconfident
on growth.

Everything is fictional (Jordan Reyes, founder of "Meridian", a B2B
analytics SaaS). Use this to record demos or try Sapience without pointing
it at real data:

    OPENAI_API_KEY=... sapience-demo --dir ./sapience-demo-data

Requires OPENAI_API_KEY (memories are embedded for real, ~40 short texts).
The target directory must be empty or new unless --force is passed.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DEMO_DOMAINS = "product-bets,hiring,growth"
DEMO_USER_CONTEXT = "Jordan Reyes, founder and CEO of Meridian (B2B analytics SaaS, 19 people, post-seed)"

# (days_ago, type, topic, salience, content)
MEMORIES = [
    (118, "user", "profile", 0.9,
     "Jordan Reyes is founder/CEO of Meridian, a B2B analytics SaaS — 19 people, $1.8M ARR, "
     "post-seed, preparing a Series A for late this year. Technical background (ex-staff eng), "
     "first-time CEO. Wants advisor-grade pushback, not cheerleading."),
    (115, "feedback", "working-style", 0.8,
     "Jordan asks for the counterargument first. When given three options, they pick fastest-to-learn, "
     "not lowest-risk. Do not pad estimates — they notice and discount everything after."),
    (110, "project", "enterprise-launch", 0.8,
     "Enterprise tier launch planned for Q3: SSO, audit logs, usage-based pricing. Bet is that "
     "mid-market accounts (Sable Health, Corvid Logistics) will 3x contract value. Sam (VP Eng) "
     "estimates 10 weeks; Jordan suspects 14."),
    (104, "episodic", "team-sam", 0.7,
     "1:1 with Sam (VP Eng): pushed back hard on shipping SSO before audit logs — argued security "
     "reviews die on audit logs, not login. Agreed to reorder. Sam flagged burnout risk on the data "
     "platform pair; hiring req approved for a senior platform eng."),
    (98, "episodic", "series-a", 0.85,
     "First Series A soft-circle conversation (Harbor Ridge Capital). Partner pushed on net revenue "
     "retention: 104% is 'a seed number, not an A number.' Takeaway: NRR above 110% by raise time "
     "or the round prices flat."),
    (95, "project", "churn", 0.8,
     "Churn workstream opened: 3 of 41 accounts churned in one quarter, all self-serve, all under "
     "$6k ACV. Hypothesis: onboarding, not product. Priya owns a 30-day activation experiment."),
    (91, "episodic", "team-priya", 0.7,
     "Priya (growth lead) presented activation funnel: 62% of new workspaces never connect a second "
     "data source; those that do retain 4x. Redesigning onboarding around 'second source in first "
     "session.' Ship in two weeks."),
    (86, "semantic", "product", 0.75,
     "Pattern across three quarters: features Jordan is most excited about ship late and land soft; "
     "features pulled from support tickets ship on time and drive expansion. Enthusiasm is a "
     "contrarian indicator for Meridian's roadmap."),
    (80, "episodic", "pricing", 0.7,
     "Pricing council: moved from per-seat to per-seat + usage blend for enterprise tier. Corvid "
     "Logistics pilot accepted the blend without negotiation — signal the floor is too low. "
     "Revisit after three more enterprise quotes."),
    (74, "episodic", "team-sam", 0.75,
     "Sam's platform hire: two finalists. Jordan preferred the ex-FAANG candidate; Sam preferred "
     "the startup generalist. Deferred to Sam — his team, his call. Candidate (Ana) accepted."),
    (67, "episodic", "series-a", 0.8,
     "Board check-in: Marcus (seed lead) says raise in Q4 or skip to profitability narrative; "
     "half-measures price worst. Agreed: decision gate at end of Q3 based on NRR and enterprise "
     "tier bookings."),
    (60, "episodic", "churn", 0.7,
     "Activation experiment first read: second-source-in-first-session up from 38% to 55% for new "
     "cohorts. Too early for retention read, but support tickets about empty dashboards dropped by "
     "half. Priya wants to extend the pattern to template galleries."),
    (53, "episodic", "enterprise-launch", 0.75,
     "Enterprise launch slipped two weeks — audit log schema rework after Sable Health's security "
     "review asked for immutable exports. Sam called the slip a week early, which is progress; "
     "the old pattern was discovering slips at the deadline."),
    (46, "episodic", "team-priya", 0.7,
     "Priya asked for a 'Head of Growth' title ahead of the raise. Deferred until post-A: titles "
     "granted mid-raise read as window dressing, and two stronger external candidates would want "
     "the role. Priya took it professionally; watch for disengagement."),
    (39, "episodic", "eu-expansion", 0.6,
     "Inbound from two EU prospects (Bremen logistics, Lyon fintech) both blocked on data residency. "
     "Parked EU deployment until post-A: infra cost ~1 platform-eng-quarter, and the two deals "
     "total $38k ACV. Revisit if EU inbound exceeds 5/quarter."),
    (32, "episodic", "enterprise-launch", 0.85,
     "Enterprise tier launched. Sable Health signed at $54k (3.2x their prior contract). Corvid "
     "still in security review. SSO+audit-logs ordering was right — Sable's review cleared in "
     "9 days, fastest ever."),
    (25, "episodic", "series-a", 0.8,
     "NRR crossed 109% on the strength of enterprise expansion. Harbor Ridge partner re-engaged "
     "unprompted. Q3 gate now leans 'raise' — needs Corvid signed and one more quarter of the "
     "activation cohort holding."),
    (18, "episodic", "team-sam", 0.75,
     "Ana (platform hire) shipped the usage-metering pipeline in her first month — Sam's "
     "startup-generalist call over Jordan's ex-FAANG preference was right. Logged in the ledger; "
     "pattern to watch: Jordan over-weights brand-name pedigree in hiring calls."),
    (11, "semantic", "judgment", 0.8,
     "Emerging pattern from the ledger: Jordan's product-timeline calls at high confidence keep "
     "missing (enterprise launch, template gallery, mobile beta), while 'coin-flip' growth calls "
     "keep landing. Confidence and domain knowledge are inversely correlated right now — the "
     "product estimates inherit builder's optimism."),
    (5, "episodic", "board", 0.7,
     "Prepped Q3 board deck: leading with NRR 109%, enterprise ACV 3.1x, activation 55%. Marcus "
     "pre-read: 'the calibration section is the most investable slide' — the ledger's hit-rate "
     "table went into the appendix."),
    (2, "reference", "systems", 0.4,
     "Meridian dashboards: growth metrics live in Metabase (activation board 'A2'), revenue in "
     "ChartMogul, incidents in Linear project MER-OPS."),
]

# (days_ago, domain, probability, text, logic, horizon_days, outcome, score)
# score: 1 right, -1 wrong, 0 partial, None pending.
ASSESSMENTS = [
    # --- product-bets: overconfident at the top of the scale ---
    (112, "product-bets", 0.9, "Enterprise tier ships within Sam's 10-week estimate",
     "Sam's estimates have improved; scope is frozen", 70, "Slipped 2 weeks on audit-log rework", -1),
    (98, "product-bets", 0.9, "Template gallery lifts new-workspace activation by 10pts within a month of launch",
     "Strongest-requested feature in support tickets", 60, "Shipped late, lifted activation 3pts", -1),
    (90, "product-bets", 0.85, "Sable Health security review clears without a custom-work ask",
     "SSO+audit logs cover their checklist", 45, "Cleared, but required immutable export rework first", -1),
    (83, "product-bets", 0.9, "Mobile dashboard beta ready for the Q3 board meeting",
     "Two engineers freed up post-launch", 55, "Not started — both engineers absorbed into metering", -1),
    (76, "product-bets", 0.85, "Usage-based pricing blend increases average enterprise quote by 40%+",
     "Corvid accepted the blend without pushback", 50, "Quotes up 55% on three deals", 1),
    (70, "product-bets", 0.9, "SSO-before-audit-logs reordering was unnecessary; reviews gate on SSO",
     "Every buyer asks about SSO first", 40, "Wrong — both reviews gated on audit logs, Sam was right", -1),
    (62, "product-bets", 0.7, "Corvid Logistics signs enterprise tier this quarter",
     "Pilot went well; champion is engaged", 60, "Still in security review at quarter end", -1),
    (55, "product-bets", 0.7, "Immutable audit exports become a named requirement in 2+ more enterprise deals",
     "Sable won't be unique among regulated buyers", 50, "Named in both subsequent health-sector deals", 1),
    (48, "product-bets", 0.7, "Metering pipeline handles quarter-end billing without manual correction",
     "Ana's design reviews were clean", 45, "One manual correction in the first cycle, clean after", 0),
    # --- hiring: well-calibrated ---
    (100, "hiring", 0.75, "Platform eng req closes within 6 weeks",
     "Two warm candidates already in pipeline", 42, "Ana accepted in week 5", 1),
    (88, "hiring", 0.7, "Ana (startup generalist) outperforms the ex-FAANG profile within 90 days",
     "Sam's read on scrappiness; Jordan disagreed", 90, "Shipped metering pipeline in month one", 1),
    (81, "hiring", 0.7, "First support hire reduces founder-answered tickets below 20%",
     "Ticket volume is categorizable; playbooks exist", 40, "Founder tickets at 15% by week 5", 1),
    (72, "hiring", 0.75, "Contract designer converts to full-time by end of quarter",
     "Engaged, likes the team", 55, "Took a bigger offer elsewhere", -1),
    (64, "hiring", 0.7, "Sam's burnout flag on the data-platform pair resolves without attrition",
     "Ana's arrival redistributes load", 60, "Both stayed; one moved to metering team", 1),
    (58, "hiring", 0.75, "Priya stays through the raise despite the title deferral",
     "Took it professionally; equity refresh landed", 90, "Engaged and shipping; no signals of a search", 1),
    (50, "hiring", 0.9, "Ana passes probation with a strong review",
     "Month-one delivery already exceptional", 60, "Strongest 90-day review on record", 1),
    (44, "hiring", 0.9, "No regretted attrition this quarter",
     "eNPS stable, comp adjusted in April", 60, "Zero attrition", 1),
    (36, "hiring", 0.7, "Second platform-eng req (backfill scope) closes within 8 weeks",
     "Pipeline warmed by Ana's referrals", 56, "Offer out in week 7, accepted week 8", 1),
    # --- growth: underconfident ---
    (94, "growth", 0.6, "Second-source onboarding redesign lifts week-1 activation above 50%",
     "Big change; self-serve users resist flow changes", 30, "Hit 55% in first cohort read", 1),
    (85, "growth", 0.6, "Activation-cohort retention beats control at day 30",
     "Correlation might not be causal", 35, "4.1x retention vs control", 1),
    (78, "growth", 0.55, "Empty-dashboard support tickets drop by a third after onboarding change",
     "Tickets have many causes; one fix rarely moves a third", 30, "Dropped by half", 1),
    (68, "growth", 0.65, "NRR crosses 108% before the Q3 board meeting",
     "Needs both Sable expansion and base retention holding", 55, "Crossed 109%", 1),
    (59, "growth", 0.6, "Self-serve churn halves this quarter vs last",
     "Activation fix is upstream; may take two quarters to show", 80, "Churn fell from 3 accounts to 1", 1),
    (49, "growth", 0.6, "Template gallery drives measurable second-source adoption despite the soft launch",
     "Soft launches underperform at Meridian", 40, "No measurable lift over the redesigned onboarding", -1),
    (42, "growth", 0.55, "Harbor Ridge re-engages before we run a formal process",
     "Partners rarely chase; they wait for the deck", 60, "Partner emailed unprompted after NRR update", 1),
    # --- pending: 2 overdue, 3 active ---
    (40, "product-bets", 0.8, "Corvid signs at $60k+ ACV once security review clears",
     "Blend pricing held on three prior quotes", 30, None, None),
    (38, "growth", 0.65, "EU inbound stays under the 5/quarter revisit threshold",
     "Two data-residency blocks in one month might be noise", 30, None, None),
    (12, "hiring", 0.75, "Support hire #2 ramps to full ticket load in 4 weeks",
     "Playbooks from hire #1 transfer directly", 28, None, None),
    (8, "product-bets", 0.85, "Q4 SOC 2 Type I completes without pulling Sam off roadmap",
     "Auditor scoped it as evidence-collection only", 75, None, None),
    (4, "growth", 0.6, "Activation cohort holds above 50% through the Q3 board meeting",
     "Novelty effects usually decay by week 6", 45, None, None),
]


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Seed the fictional Meridian demo dataset.")
    parser.add_argument("--dir", default="./sapience-demo-data",
                        help="Target data directory (created if missing; default ./sapience-demo-data)")
    parser.add_argument("--force", action="store_true",
                        help="Seed even if the directory is not empty")
    args = parser.parse_args()

    target = os.path.abspath(os.path.expanduser(args.dir))
    if os.path.isdir(target) and os.listdir(target) and not args.force:
        _fail(f"{target} is not empty — refusing to seed over existing data (use --force to override)")
    if not os.environ.get("OPENAI_API_KEY"):
        _fail("OPENAI_API_KEY is required (demo memories are embedded for real)")

    # Env must be set before sapience modules are imported: paths are resolved
    # at import time.
    os.makedirs(target, exist_ok=True)
    os.environ["SAPIENCE_DATA_DIR"] = target
    os.environ.pop("MEMORY_DB_PATH", None)
    os.environ.pop("LEDGER_DB_PATH", None)
    os.environ["LEDGER_DOMAINS"] = DEMO_DOMAINS

    from . import ledger, memory_store

    if memory_store.count() and not args.force:
        _fail(f"memory DB at {target} already has records — refusing to seed (use --force)")

    now = datetime.now(timezone.utc)
    print(f"Seeding demo data into {target}")

    collection = memory_store._get_collection()
    for days_ago, mtype, topic, salience, content in MEMORIES:
        mid = memory_store.save(content=content, memory_type=mtype, salience=salience,
                                topic=topic, source="demo-seed")
        got = collection.get(ids=[mid], include=["metadatas"])
        meta = got["metadatas"][0].copy()
        meta["created_at"] = (now - timedelta(days=days_ago)).isoformat()
        collection.update(ids=[mid], metadatas=[meta])
    print(f"  {len(MEMORIES)} memories embedded and backdated")

    resolved = pending = 0
    for days_ago, domain, prob, text, logic, horizon_days, outcome, score in ASSESSMENTS:
        made = now - timedelta(days=days_ago)
        aid = ledger.log_assessment(text=text, domain=domain, probability=prob, logic=logic,
                                    horizon=f"{horizon_days} days", source_session="demo-seed")
        if score is not None:
            ledger.resolve(assessment_id=aid, outcome=outcome, score=score)
            resolved += 1
        else:
            pending += 1
        with ledger._get_conn() as conn:
            conn.execute("UPDATE assessments SET date_made=? WHERE id=?", (made.isoformat(), aid))
            if score is not None:
                resolved_on = made + timedelta(days=max(1, horizon_days - 3))
                conn.execute("UPDATE assessments SET outcome_date=? WHERE id=?",
                             (resolved_on.isoformat(), aid))
            conn.commit()
    print(f"  {resolved} resolved + {pending} pending assessments backdated")

    config = {
        "mcpServers": {
            "sapience-demo": {
                "command": "sapience",
                "env": {
                    "SAPIENCE_DATA_DIR": target,
                    "LEDGER_DOMAINS": DEMO_DOMAINS,
                    "MEMORY_USER_CONTEXT": DEMO_USER_CONTEXT,
                },
            }
        }
    }
    print("\nDone. Point a server at it with this MCP config:\n")
    print(json.dumps(config, indent=2))
    print(
        "\nDemo flow that shows the designed story:\n"
        "  1. search_memory('how is the enterprise launch going')\n"
        "  2. get_context_brief('series A')\n"
        "  3. get_bias_map()  — overconfident on product-bets, calibrated on hiring,\n"
        "     underconfident on growth\n"
        "  4. /sapience:log review  — 2 overdue calls waiting to be scored\n"
    )


if __name__ == "__main__":
    main()
