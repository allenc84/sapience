"""Where Sapience stores its data (vector DB, ledger, review state).

Defaults to a per-user OS data directory so a pip-installed package never writes
into site-packages. Override the whole location with SAPIENCE_DATA_DIR, or point
individual stores with MEMORY_DB_PATH / LEDGER_DB_PATH.
"""

import os
from pathlib import Path

from platformdirs import user_data_dir


def data_dir() -> Path:
    d = Path(os.environ.get("SAPIENCE_DATA_DIR") or user_data_dir("sapience", "sapience"))
    d.mkdir(parents=True, exist_ok=True)
    return d
