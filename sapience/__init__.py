"""Sapience — human-like memory and a judgment ledger for AI."""

# Load .env before any submodule import: memory_store, ledger, and schema read
# their env config (SAPIENCE_DATA_DIR, LEDGER_DOMAINS, keys, ...) at import time,
# so a console-script / uvx / pipx launch must pick up .env here, not in cli().
# Does not override variables already set (MCP-config env wins).
#
# usecwd=True is required for installed (non-editable) packages: bare
# find_dotenv() walks up from this file's location (site-packages/sapience/),
# never reaching a user's project directory. usecwd=True walks up from the
# actual process cwd instead, which is what a real console-script/uvx launch
# needs.
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))

__version__ = "0.1.1"
