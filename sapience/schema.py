import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# Persona this memory system serves. Shown in tool descriptions and used to frame
# synthesized briefs, calibration, and bias maps. Override in your .env, e.g.
#   MEMORY_USER_CONTEXT="Jane Doe, founder of Acme"
USER_CONTEXT = os.environ.get("MEMORY_USER_CONTEXT", "the user")


MEMORY_TYPES = {
    "episodic",   # Specific events, conversations, decisions with context
    "semantic",   # Extracted patterns, principles, evolved thinking
    "user",       # Facts about the user: role, preferences, knowledge
    "feedback",   # How to work with the user: what to do/avoid and why
    "project",    # Ongoing initiatives, decisions, timelines
    "reference",  # Pointers to external systems/resources
}


@dataclass
class Memory:
    id: str
    content: str
    type: str
    created_at: str       # ISO 8601
    salience: float       # 0.0–1.0; higher = surfaces more readily
    source: str           # conversation, migration, consolidation, manual
    topic: str            # primary topic tag for grouping
    access_count: int = 0
    last_accessed: Optional[str] = None
    related_ids: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class SearchResult:
    memory: Memory
    score: float          # cosine similarity 0–1; higher = more similar


@dataclass
class ContextBrief:
    topic: str
    summary: str          # Claude-synthesized narrative
    key_facts: List[str]
    open_questions: List[str]
    evolved_thinking: List[str]   # how thinking has changed over time
    memory_count: int
