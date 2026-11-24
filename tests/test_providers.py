import providers as P


def test_custom_models_never_use_default_model_pricing(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "different-model")
    assert P.model("deepseek") == "different-model"
    assert P.cost_usd("deepseek", {"input": 10000}) is None
    assert P.cost_usd("deepseek", {"input": 10000}, P.PROVIDERS["deepseek"]["model"]) > 0
