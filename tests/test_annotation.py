import json
from types import SimpleNamespace
from unittest.mock import Mock

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
