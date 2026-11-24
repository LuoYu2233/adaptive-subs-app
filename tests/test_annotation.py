import json
from types import SimpleNamespace
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import annotate as A


def response(value):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])


def fake_client(*values):
    create = Mock(side_effect=[v if isinstance(v, Exception) else response(v) for v in values])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_polysemy_survives_finalization():
    cues = [
        {"notes": [{"key": "bank", "kind": "word", "zh": "银行", "lv": 3000}]},
        {"notes": [{"key": "bank", "kind": "word", "zh": "河岸", "lv": 5000}]},
    ]
    A.unify_glosses(cues)
    assert [c["notes"][0]["zh"] for c in cues] == ["银行", "河岸"]
    assert [c["notes"][0]["lv"] for c in cues] == [3000, 5000]


def test_request_failure_is_not_an_empty_annotation():
    client = fake_client(TimeoutError(), TimeoutError())
    with pytest.raises(A.ModelResponseError):
        A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}])
    assert not list(A.CACHE_DIR.glob("*.json"))


def test_legitimate_empty_notes_are_cached():
    expected = {"items": [{"id": 0, "notes": []}]}
    client = fake_client(expected)
    args = (client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}])
    assert A.call_json(*args) == expected
    assert A.call_json(*args) == expected
    assert client.chat.completions.create.call_count == 1


@pytest.mark.parametrize("bad", [[], {"items": []}, {"items": [{}]}, {"items": [{"id": 0, "notes": "bad"}]}])
def test_missing_or_malformed_annotations_are_not_cached(bad):
    with pytest.raises(A.ModelResponseError):
        A.call_json(fake_client(bad, bad), "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}])
    assert not list(A.CACHE_DIR.glob("*.json"))


def test_corrupt_cache_is_replaced():
    expected = {"items": [{"id": 0, "notes": []}]}
    client = fake_client(expected, expected)
    args = (client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}])
    A.call_json(*args)
    cache = next(A.CACHE_DIR.glob("*.json"))
    cache.write_text('{"items":')
    assert A.call_json(*args) == expected
    assert json.loads(cache.read_text()) == expected


def test_invalid_credentials_are_not_retried():
    exc = RuntimeError("invalid credentials")
    exc.status_code = 401
    client = fake_client(exc)
    with pytest.raises(A.ModelResponseError):
        A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}])
    assert client.chat.completions.create.call_count == 1


def test_cache_disk_failure_never_repeats_paid_request(monkeypatch):
    expected = {"items": [{"id": 0, "notes": []}]}
    client = fake_client(expected)
    monkeypatch.setattr(A, "write_json", Mock(side_effect=OSError("disk full")))
    assert A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}]) == expected
    assert client.chat.completions.create.call_count == 1


def test_usage_log_failure_never_repeats_paid_request(monkeypatch):
    expected = {"items": [{"id": 0, "notes": []}]}
    client = fake_client(expected)
    monkeypatch.setattr(A, "record_usage", Mock(side_effect=OSError("disk full")))
    assert A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}]) == expected
    assert client.chat.completions.create.call_count == 1


def test_simultaneous_identical_requests_share_one_generation():
    client = fake_client({"items": [{"id": 0, "notes": []}]})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}]), range(12)))
    assert all(result == results[0] for result in results)
    assert client.chat.completions.create.call_count == 1


def test_global_request_limit_applies_across_distinct_generations(monkeypatch):
    monkeypatch.setattr(A, "MODEL_SLOTS", threading.BoundedSemaphore(2))
    lock, entered, release = threading.Lock(), threading.Event(), threading.Event()
    active, maximum = 0, 0

    def create(**kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
            if active == 2:
                entered.set()
        try:
            assert release.wait(timeout=5)
            return response({"items": [{"id": json.loads(kwargs["messages"][1]["content"])[0]["id"], "notes": []}]})
        finally:
            with lock:
                active -= 1

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(A.call_json, client, "test", "low", A.ANNOTATE_PROMPT, [{"id": i}]) for i in range(8)]
        try:
            assert entered.wait(timeout=5)
        finally:
            release.set()
        assert all(f.result(timeout=5) for f in futures)
    assert maximum == 2


def test_cancelled_work_never_calls_model():
    client = fake_client()
    with pytest.raises(A.GenerationCancelled):
        A.call_json(client, "test", "low", A.ANNOTATE_PROMPT, [{"id": 0}], cancelled=lambda: True)
    client.chat.completions.create.assert_not_called()


def test_large_transcript_windows_keep_neighbors_without_losing_cues(monkeypatch):
    windows = []
    def analyze(texts):
        windows.append(texts)
        return [[{"source": text}] for text in texts]
    monkeypatch.setattr(A, "_find_candidates", analyze)
    texts = [f"line {i}" for i in range(205)]
    result = A.find_candidates(texts)
    assert [row[0]["source"] for row in result] == texts
    assert any("line 99" in w and "line 100" in w and "line 101" in w for w in windows)
    assert max(map(len, windows)) <= 102


@pytest.mark.parametrize("notes", [
    [{"span": "absent", "zh": "没有", "kind": "word", "level": 5000}],
    [{"span": "bank", "zh": "银行", "kind": "word", "level": "Infinity"}],
    [{"span": "bank", "zh": "（解释）", "kind": "word", "level": 5000}],
])
def test_unplaceable_or_invalid_gloss_is_not_cached(notes):
    bad = {"items": [{"id": 0, "notes": notes}]}
    with pytest.raises(A.ModelResponseError):
        A.call_json(fake_client(bad, bad), "test", "low", A.ANNOTATE_PROMPT, [{"id": 0, "line": "The bank."}])
    assert not list(A.CACHE_DIR.glob("*.json"))


@pytest.mark.parametrize("prompt,items", [
    (A.GLOSS_PROMPT, []),
    (A.GLOSS_PROMPT, [{"id": 0, "skip": "false"}]),
    (A.GLOSS_PROMPT, [{"id": 0, "zh": ["银行"], "level": 5000}]),
    (A.TRANSLATE_PROMPT, []),
    (A.TRANSLATE_PROMPT, [{"id": 0, "src": "The bank", "zh": {"wrong": "type"}}]),
    (A.TRANSLATE_PROMPT, [{"id": 0, "src": "Another sentence", "zh": "银行。"}]),
])
def test_invalid_fallback_or_translation_cannot_poison_cache(prompt, items):
    bad = {"items": items}
    with pytest.raises(A.ModelResponseError):
        A.call_json(fake_client(bad, bad), "test", "low", prompt, [{"id": 0, "line": "The bank."}])
    assert not list(A.CACHE_DIR.glob("*.json"))


def test_explicit_skip_and_aligned_translation_are_valid():
    payload = [{"id": 0, "line": "The bank."}]
    assert A.validate_response({"items": [{"id": 0, "skip": True}]}, A.GLOSS_PROMPT, payload)
    assert A.validate_response({"items": [{"id": 0, "src": "The bank", "zh": "银行。"}]}, A.TRANSLATE_PROMPT, payload)
