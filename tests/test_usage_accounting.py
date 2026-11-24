from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import annotate as A
import asr
import providers as P
from usage_accounting import token_record, token_totals


def service(usage, text):
    reply = SimpleNamespace(usage=usage, choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason="stop")])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=reply))))


@pytest.mark.parametrize("usage,incomplete", [
    (None, 1), ({}, 1),
    (SimpleNamespace(prompt_tokens="10", completion_tokens="3"), 0),
    ({"prompt_tokens": 10, "completion_tokens": 3}, 0),
    ({"prompt_tokens": 10, "completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": None}}, 0),
    ({"prompt_tokens": -1, "completion_tokens": 3}, 1),
    ({"prompt_tokens": True, "completion_tokens": 3}, 1),
    ({"prompt_tokens": [10], "completion_tokens": 3}, 1),
    ({"prompt_tokens": float("inf"), "completion_tokens": 3}, 1),
    ({"prompt_tokens": 10, "completion_tokens": float("nan")}, 1),
    ({"prompt_tokens": 10, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 11}}, 1),
    ({"prompt_tokens": 10, "completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 4}}, 1),
])
def test_unusual_usage_never_discards_or_repeats_valid_generation(usage, incomplete):
    expected = {"items": [{"id": 0, "notes": []}]}
    client = service(usage, json.dumps(expected))
    counts = {}
    args = (client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}])
    assert A.call_json(*args, usage=counts) == expected
    assert A.call_json(*args, usage=counts) == expected
    assert client.chat.completions.create.call_count == 1
    total = A.usage_total(counts)
    assert total["calls"] == 1 and total["incomplete_calls"] == incomplete
    if incomplete:
        assert P.cost_usd("deepseek", total) is None
    logged = [json.loads(line) for line in (A.CACHE_DIR / "usage.jsonl").read_text().splitlines()]
    assert len(logged) == 1 and logged[0]["incomplete_calls"] == incomplete


def test_missing_metadata_does_not_claim_a_free_request():
    record = token_record(SimpleNamespace(usage=None))
    assert record["calls"] == 1 and record["incomplete_calls"] == 1
    assert P.cost_usd("deepseek", record) is None


@pytest.mark.parametrize("error", [ValueError("bad metadata"), TypeError("bad type"), AttributeError("missing field")])
def test_unexpected_accounting_errors_cannot_trigger_model_retries(monkeypatch, error):
    expected = {"items": [{"id": 0, "notes": []}]}
    client = service(None, json.dumps(expected))
    monkeypatch.setattr(A, "record_usage", Mock(side_effect=error))
    counts = {}
    assert A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}], usage=counts) == expected
    assert client.chat.completions.create.call_count == 1
    assert P.cost_usd("deepseek", A.usage_total(counts)) is None


def test_invalid_checkpoint_counters_do_not_poison_later_generations():
    counts = {"annotate": {"calls": 2, "input": "bad", "output": 1}}
    expected = {"items": [{"id": 0, "notes": []}]}
    client = service({"prompt_tokens": 10, "completion_tokens": 3}, json.dumps(expected))
    assert A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}], usage=counts) == expected
    assert A.usage_snapshot(counts)["annotate"]["calls"] == 3
    assert A.usage_total(counts)["incomplete_calls"] == 1
    assert P.cost_usd("deepseek", A.usage_total(counts)) is None


def test_concurrent_usage_counts_are_complete_and_logs_remain_parseable():
    counts = {}
    response = SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 3,
                                     "prompt_cache_hit_tokens": 4})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: A.record_usage(response, "test", "low", "annotate", counts), range(32)))
    assert A.usage_total(counts) == {"calls": 32, "input": 320, "output": 96, "cached_input": 128,
                                   "reasoning": 0, "incomplete_calls": 0}
    assert len([json.loads(line) for line in (A.CACHE_DIR / "usage.jsonl").read_text().splitlines()]) == 32


@pytest.mark.parametrize("value", [None, [], {"calls": True}, {"input": -1}, {"output": float("nan")},
                                  {"input": 1, "cached_input": 2}, {"output": 2, "reasoning": 3}])
def test_malformed_totals_cannot_produce_a_cost_estimate(value):
    normalized = token_totals(value)
    assert normalized["incomplete_calls"] >= 1
    assert P.cost_usd("deepseek", normalized) is None


@pytest.mark.parametrize("metadata,seconds,estimated", [
    (None, 1.0, 1), ({"seconds": "1.25"}, 1.25, 0), ({"seconds": 0}, 0.0, 0),
    ({"seconds": True}, 1.0, 1), ({"seconds": -1}, 1.0, 1), ({"seconds": "invalid"}, 1.0, 1),
    ({"seconds": float("nan")}, 1.0, 1), ({"seconds": float("inf")}, 1.0, 1),
    (SimpleNamespace(model_dump=lambda: {"seconds": "1.5"}), 1.5, 0),
    (SimpleNamespace(model_dump=Mock(side_effect=ValueError("bad SDK metadata"))), 1.0, 1),
])
def test_audio_usage_anomalies_preserve_cache_and_label_estimated_duration(monkeypatch, metadata, seconds, estimated):
    monkeypatch.setattr(asr.LIMIT, "wait", lambda *_: None)
    client = service(metadata, "Original speech sample.")
    counts = {}
    pcm = np.ones(asr.SR, dtype=np.int16)
    for _ in range(2):
        assert asr.transcribe(pcm, 0, 1, usage=counts, client_instance=client) == "Original speech sample."
    assert client.chat.completions.create.call_count == 1
    assert counts == {"asr_calls": 1, "asr_seconds": seconds, "asr_estimated_calls": estimated}
