"""Sapience — human-like memory and a judgment ledger for AI."""

# Load .env before any submodule import: memory_store, ledger, and schema read
# their env config (SAPIENCE_DATA_DIR, LEDGER_DOMAINS, keys, ...) at import time,
# so a console-script / uvx / pipx launch must pick up .env here, not in cli().
# Does not override variables already set (MCP-config env wins).
from dotenv import load_dotenv

load_dotenv()

__version__ = "0.1.0"
