import pytest


@pytest.fixture(autouse=True)
def no_real_indexing(monkeypatch):
    """Server startup indexes the GDPR in the background when a key exists; never do that in tests."""
    from gdpr_rag import ingest

    calls = []
    monkeypatch.setattr(ingest, "build", lambda db, models, log=print: calls.append(models) or 0)
    return calls
