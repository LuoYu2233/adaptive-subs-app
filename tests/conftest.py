"""Tests are isolated from real credentials, user profiles and network model calls."""
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    import annotate
    import providers
    import server
    from task_store import TaskStore
    monkeypatch.setattr(annotate, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(server, "TASK_STORE", TaskStore(tmp_path / "tasks.sqlite3"))
    monkeypatch.setattr(server, "JOBS", {})
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY", "ANNOTATION_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    providers.reset_clients()
    yield
    providers.reset_clients()
