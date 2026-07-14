import pytest


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A ledger module pointed at a fresh temp database with known domains."""
    from sapience import ledger as L

    monkeypatch.setattr(L, "LEDGER_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(L, "DOMAINS", {"predictions", "decisions"})
    return L
