import providers as P
import sys
from types import SimpleNamespace

import asr


def test_custom_models_never_use_default_model_pricing(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "different-model")
    assert P.model("deepseek") == "different-model"
    assert P.cost_usd("deepseek", {"input": 10000}) is None
    assert P.cost_usd("deepseek", {"input": 10000}, P.PROVIDERS["deepseek"]["model"]) > 0


def test_clients_follow_endpoint_and_key_configuration(monkeypatch):
    created = []
    def create(**args):
        client = SimpleNamespace(**args)
        created.append(client)
        return client
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=create))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-a")
    first = P.client("deepseek")
    assert P.client("deepseek") is first
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://different.example.invalid/v1")
    assert P.client("deepseek") is not first
    assert P.cost_usd("deepseek", {"input": 10000}) is None
    monkeypatch.setenv("MIMO_API_KEY", "test-key-a")
    original = asr.client()
    monkeypatch.setenv("MIMO_BASE_URL", "https://speech.example.invalid/v1")
    changed = asr.client()
    assert changed is not original
    monkeypatch.setenv("MIMO_API_KEY", "test-key-b")
    assert asr.client() is not changed


def test_endpoint_fingerprints_normalize_trailing_slashes():
    assert P.endpoint_id("https://example.invalid/v1/") == P.endpoint_id("https://example.invalid/v1")
