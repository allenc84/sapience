"""
MCP server — Claude Code calls these tools instead of reading flat markdown files.
"""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from . import memory_store
from . import consolidator
from . import ledger
from . import resolver
from .schema import MEMORY_TYPES, USER_CONTEXT

app = Server("sapience")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_memory",
            description=(
                "Search the memory system by semantic similarity. "
                "Use this at the start of any topic-specific conversation to surface relevant context, "
                "prior decisions, and evolved thinking — without being asked. "
                "Returns memories ranked by relevance × salience."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what context you need"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of memories to return (default 6)",
                        "default": 6
                    },
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"Filter by memory type. Options: {sorted(MEMORY_TYPES)}. Omit to search all."
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="save_memory",
            description=(
                "Save a new memory. Use this to record decisions made, insights surfaced, "
                "how the user's thinking has evolved, feedback given, or important context from this conversation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory content — be specific and self-contained"
                    },
                    "memory_type": {
                        "type": "string",
                        "description": f"Type of memory. One of: {sorted(MEMORY_TYPES)}",
                        "enum": sorted(MEMORY_TYPES)
                    },
                    "salience": {
                        "type": "number",
                        "description": "Importance weight 0.0–1.0. High-stakes decisions = 0.9+. Routine context = 0.3–0.5.",
                        "default": 0.5
                    },
                    "topic": {
                        "type": "string",
                        "description": "Primary topic tag (e.g. 'product-launch', 'hiring', 'q3-planning')"
                    },
                    "source": {
                        "type": "string",
                        "description": "Where this came from",
                        "default": "conversation"
                    }
                },
                "required": ["content", "memory_type"]
            }
        ),
        types.Tool(
            name="get_context_brief",
            description=(
                "Get a synthesized brief on a topic — what is known, how thinking has evolved, "
                "and what open questions remain. Use before deep-diving into any recurring topic "
                "like web onboarding tests, the stock portfolio, or an M&A situation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The topic to brief on (e.g. 'web onboarding', 'stock portfolio')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many memories to pull for synthesis (default 10)",
                        "default": 10
                    }
                },
                "required": ["topic"]
            }
        ),
        types.Tool(
            name="get_related",
            description="Given a memory ID, find semantically related memories. Implements spreading activation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5}
                },
                "required": ["memory_id"]
            }
        ),
        types.Tool(
            name="consolidate",
            description=(
                "Run the consolidation job: extract semantic patterns from recent episodic memories. "
                "Run this at the end of sessions covering important topics. "
                "This is what makes the memory system get smarter over time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days_back": {
                        "type": "integer",
                        "description": "How many days of episodic memory to consolidate (default 7)",
                        "default": 7
                    }
                }
            }
        ),
        types.Tool(
            name="list_memories",
            description="List recent memories, optionally filtered by type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "description": f"Filter by type. One of: {sorted(MEMORY_TYPES)}. Omit for all."
                    },
                    "limit": {"type": "integer", "default": 20}
                }
            }
        ),
        types.Tool(
            name="memory_stats",
            description="Return total memory count and breakdown by type.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="log_assessment",
            description=(
                "Log a forward-looking assessment to the judgment ledger. "
                "Use this whenever making a prediction, recommendation, or forward-looking call. "
                "Prefer a numeric 'probability' (0-1) — it enables real calibration (Brier score) "
                "over time. Assessments are tracked and later scored against what actually happened."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The assessment or prediction, stated clearly"
                    },
                    "domain": {
                        "type": "string",
                        "description": "Assessment domain (one of the configured LEDGER_DOMAINS)",
                        "enum": sorted(ledger.DOMAINS)
                    },
                    "probability": {
                        "type": "number",
                        "description": "Forecast probability the call proves right, 0-1 (e.g. 0.7). Preferred over 'confidence'; enables Brier scoring."
                    },
                    "confidence": {
                        "type": "string",
                        "description": "Categorical confidence, used only if 'probability' is omitted (high=0.9, moderate=0.75, low=0.6)",
                        "enum": ["high", "moderate", "low"],
                        "default": "moderate"
                    },
                    "horizon": {
                        "type": "string",
                        "description": "Expected resolution timeframe e.g. '2 weeks', '3 months'"
                    },
                    "logic": {
                        "type": "string",
                        "description": "The reasoning behind this assessment at the time"
                    },
                    "conditions": {
                        "type": "string",
                        "description": "Relevant conditions or context at time of assessment"
                    },
                    "source_session": {
                        "type": "string",
                        "description": "Brief label for where this came from e.g. 'planning session 2025-01-15'"
                    }
                },
                "required": ["text", "domain"]
            }
        ),
        types.Tool(
            name="list_pending_assessments",
            description=(
                "List unresolved assessments from the judgment ledger. "
                "Use during weekly review to surface what needs scoring, "
                "or at session start to remind of open calls in a domain."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Filter by domain. Omit for all domains.",
                        "enum": sorted(ledger.DOMAINS)
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 50)",
                        "default": 50
                    }
                }
            }
        ),
        types.Tool(
            name="resolve_assessment",
            description=(
                "Mark an assessment as resolved with its actual outcome and score. "
                "score: 1 = right, 0 = partially right, -1 = wrong. "
                "Call this as soon as the outcome of a prediction is known."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Assessment ID from log_assessment"
                    },
                    "outcome": {
                        "type": "string",
                        "description": "What actually happened"
                    },
                    "score": {
                        "type": "integer",
                        "description": "1 = right, 0 = partial, -1 = wrong",
                        "enum": [-1, 0, 1]
                    }
                },
                "required": ["id", "outcome", "score"]
            }
        ),
        types.Tool(
            name="generate_calibration",
            description=(
                "Extract calibration patterns from resolved assessments in a domain and write "
                "a high-salience feedback memory. Run this after a batch of resolutions in a domain. "
                "Requires at least 3 resolved assessments in the domain."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domain to generate calibration for",
                        "enum": sorted(ledger.DOMAINS)
                    }
                },
                "required": ["domain"]
            }
        ),
        types.Tool(
            name="get_bias_map",
            description=(
                "Generate a structured bias report across all resolved assessments. "
                "Shows where judgment is well-calibrated vs. systematically off. "
                "Use for quarterly self-calibration review."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Filter to one domain. Omit for full cross-domain map.",
                        "enum": sorted(ledger.DOMAINS)
                    }
                }
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "search_memory":
            results = memory_store.search(
                query=arguments["query"],
                top_k=arguments.get("top_k", 6),
                types=arguments.get("types"),
            )
            output = [
                {
                    "id": r.memory.id,
                    "score": round(r.score, 3),
                    "type": r.memory.type,
                    "topic": r.memory.topic,
                    "salience": r.memory.salience,
                    "created_at": r.memory.created_at[:10],
                    "content": r.memory.content,
                }
                for r in results
            ]
            return [types.TextContent(type="text", text=json.dumps(output, indent=2))]

        elif name == "save_memory":
            mid = memory_store.save(
                content=arguments["content"],
                memory_type=arguments["memory_type"],
                salience=arguments.get("salience", 0.5),
                topic=arguments.get("topic", ""),
                source=arguments.get("source", "conversation"),
            )
            return [types.TextContent(type="text", text=json.dumps({"id": mid, "status": "saved"}))]

        elif name == "get_context_brief":
            topic = arguments["topic"]
            top_k = arguments.get("top_k", 10)
            results = memory_store.search(query=topic, top_k=top_k)

            if not results:
                return [types.TextContent(type="text", text=json.dumps({
                    "topic": topic,
                    "summary": "No memories found on this topic.",
                    "memory_count": 0,
                }))]

            memory_text = "\n\n---\n\n".join(
                f"[{r.memory.type} | {r.memory.created_at[:10]} | salience={r.memory.salience}]\n{r.memory.content}"
                for r in results
            )

            # Use Claude to synthesize
            import anthropic
            import os
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            response = client.messages.create(
                model="claude-opus-4-8",
                max_tokens=1000,
                tools=[{
                    "name": "context_brief",
                    "description": "Synthesized context brief on a topic",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "key_facts": {"type": "array", "items": {"type": "string"}},
                            "evolved_thinking": {"type": "array", "items": {"type": "string"}},
                            "open_questions": {"type": "array", "items": {"type": "string"}},
                            "challenge_points": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["summary", "key_facts", "evolved_thinking", "open_questions", "challenge_points"],
                    },
                }],
                tool_choice={"type": "tool", "name": "context_brief"},
                messages=[{
                    "role": "user",
                    "content": f"""Synthesize these memories about "{topic}" into a context brief for use in an ongoing advisory conversation with {USER_CONTEXT}.

MEMORIES:
{memory_text}

Fields:
- summary: 2-3 sentence narrative of where things stand
- key_facts: most important specific facts/decisions
- evolved_thinking: how the user's thinking has changed on this topic
- open_questions: unresolved tensions or questions that keep appearing
- challenge_points: specific things to push back on or challenge the user about based on the history

Be specific. These will be used to develop the user's thinking, not just summarize what happened."""
                }]
            )
            brief = response.content[0].input
            brief["topic"] = topic
            brief["memory_count"] = len(results)
            return [types.TextContent(type="text", text=json.dumps(brief, indent=2))]

        elif name == "get_related":
            results = memory_store.get_related(
                memory_id=arguments["memory_id"],
                top_k=arguments.get("top_k", 5)
            )
            output = [
                {
                    "id": r.memory.id,
                    "score": round(r.score, 3),
                    "type": r.memory.type,
                    "topic": r.memory.topic,
                    "content": r.memory.content[:300],
                }
                for r in results
            ]
            return [types.TextContent(type="text", text=json.dumps(output, indent=2))]

        elif name == "consolidate":
            result = consolidator.run(days_back=arguments.get("days_back", 7))
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "list_memories":
            memories = memory_store.list_recent(
                memory_type=arguments.get("memory_type"),
                limit=arguments.get("limit", 20)
            )
            output = [
                {
                    "id": m.id,
                    "type": m.type,
                    "topic": m.topic,
                    "salience": m.salience,
                    "created_at": m.created_at[:10],
                    "content": m.content[:200] + ("..." if len(m.content) > 200 else ""),
                }
                for m in memories
            ]
            return [types.TextContent(type="text", text=json.dumps(output, indent=2))]

        elif name == "memory_stats":
            total = memory_store.count()
            by_type = {}
            for t in MEMORY_TYPES:
                mems = memory_store.list_recent(memory_type=t, limit=1000)
                by_type[t] = len(mems)
            return [types.TextContent(type="text", text=json.dumps({
                "total": total,
                "by_type": by_type,
            }))]

        elif name == "log_assessment":
            aid = ledger.log_assessment(
                text=arguments["text"],
                domain=arguments["domain"],
                confidence=arguments.get("confidence", "moderate"),
                probability=arguments.get("probability"),
                horizon=arguments.get("horizon", ""),
                logic=arguments.get("logic", ""),
                conditions=arguments.get("conditions", ""),
                source_session=arguments.get("source_session", ""),
            )
            logged = ledger.get_by_id(aid)
            return [types.TextContent(type="text", text=json.dumps({
                "id": aid, "status": "logged",
                "probability": logged["probability"], "confidence": logged["confidence"],
            }))]

        elif name == "list_pending_assessments":
            items = ledger.list_pending(
                domain=arguments.get("domain"),
                limit=arguments.get("limit", 50),
            )
            stats = ledger.get_stats()
            return [types.TextContent(type="text", text=json.dumps({
                "pending": items,
                "total_pending": sum(v["pending"] for v in stats["by_domain"].values()),
                "stats": stats,
            }, indent=2))]

        elif name == "resolve_assessment":
            aid = arguments["id"]
            item = ledger.get_by_id(aid)
            if not item:
                return [types.TextContent(type="text", text=json.dumps({"error": f"Assessment {aid} not found"}))]

            ok = ledger.resolve(
                assessment_id=aid,
                outcome=arguments["outcome"],
                score=arguments["score"],
            )
            if not ok:
                return [types.TextContent(type="text", text=json.dumps({
                    "error": "Assessment not found or already resolved"
                }))]

            remaining = len(ledger.list_pending(domain=item["domain"]))
            resolved_count = ledger.resolved_count_for_domain(item["domain"])
            return [types.TextContent(type="text", text=json.dumps({
                "status": "resolved",
                "domain": item["domain"],
                "score": arguments["score"],
                "remaining_pending_in_domain": remaining,
                "total_resolved_in_domain": resolved_count,
                "tip": (
                    f"Run generate_calibration(domain='{item['domain']}') — enough data for a quantitative read."
                    if resolved_count >= ledger.MIN_CALIBRATION_N else
                    f"generate_calibration works now but is reflection-only until {ledger.MIN_CALIBRATION_N} resolved "
                    f"({ledger.MIN_CALIBRATION_N - resolved_count} to go) — below that, patterns are signals to watch, not conclusions."
                ),
            }, indent=2))]

        elif name == "generate_calibration":
            domain = arguments["domain"]
            result = resolver.generate_calibration(domain)
            if result is None:
                count = ledger.resolved_count_for_domain(domain)
                return [types.TextContent(type="text", text=json.dumps({
                    "status": "insufficient_data",
                    "resolved_count": count,
                    "needed": 3,
                }))]
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_bias_map":
            result = resolver.generate_bias_map(domain=arguments.get("domain"))
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def cli():
    """Console-script entry point (`sapience`) and `python -m sapience.server`."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
