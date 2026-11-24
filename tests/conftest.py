"""Tests are isolated from real credentials, user profiles and network model calls."""
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    import annotate
    import providers
    import server
    from task_store import TaskStore
    from profile_store import Store
    monkeypatch.setattr(annotate, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(server, "TASK_STORE", TaskStore(tmp_path / "tasks.sqlite3"))
    monkeypatch.setattr(server, "PROFILE", Store(tmp_path / "profile.json"))
    monkeypatch.setattr(server, "JOBS", {})
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY", "ANNOTATION_PROVIDER",
                "OPENAI_MODEL", "DEEPSEEK_MODEL", "MIMO_MODEL", "ASR_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key in ("OPENAI_BASE_URL", "DEEPSEEK_BASE_URL", "MIMO_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    server.ASR.reset_client()
    providers.reset_clients()
    yield
    providers.reset_clients()
    server.ASR.reset_client()
