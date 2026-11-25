import copy
import json

import pytest

from result_validation import valid_result
import server


def sample():
    text = "😀 The bank approved it."
    s = text.index("bank")
    return {"video": {"id": "abcdefghijk", "title": "test", "duration": 5}, "cues": [
        {"s": 0, "e": 5, "text": text, "zh": "银行批准了。", "notes": [
            {"s": s, "e": s + 4, "key": "bank", "zh": "银行", "lv": 5000, "kind": "word"}]}]}


def test_valid_legacy_and_current_results_keep_unicode_codepoint_positions(tmp_path):
    for value in (sample(), sample() | {"status": "complete", "schema_version": 2}):
        before = copy.deepcopy(value)
        assert valid_result(value)
        assert value == before
        path = tmp_path / "result.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        assert server.saved_result(path) == value


@pytest.mark.parametrize("change", [
    {"s": float("nan")}, {"s": 10 ** 400}, {"e": float("inf")}, {"e": -1}, {"text": []}, {"zh": {}}, {"notes": "bad"},
    {"notes": [{"s": 0, "e": 999}]}, {"notes": [None]},
])
def test_corrupt_cue_cannot_short_circuit_generation(tmp_path, change):
    value = sample()
    value["cues"][0].update(change)
    assert not valid_result(value)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert server.saved_result(path) is None


def test_overlapping_notes_and_partial_results_are_not_complete():
    value = sample()
    value["cues"][0]["notes"] *= 2
    assert not valid_result(value)
    assert not valid_result(sample() | {"status": "partial"})


@pytest.mark.parametrize("path", [
    ("video", "id"), ("video", "title"), ("cues", 0, "text"), ("cues", 0, "zh"),
    ("cues", 0, "notes", 0, "key"), ("cues", 0, "notes", 0, "zh"), ("metadata",),
])
def test_invalid_unicode_anywhere_in_saved_response_is_rejected_without_rewriting(tmp_path, path):
    value = sample()
    target = value
    for key in path[:-1]: target = target[key]
    target[path[-1]] = "invalid \ud800 text"
    raw = json.dumps(value).encode()
    saved = tmp_path / "result.json"
    saved.write_bytes(raw)
    assert not valid_result(value)
    assert server.saved_result(saved) is None
    assert saved.read_bytes() == raw


def test_deeply_nested_json_is_rejected_without_crashing_or_modifying_file(tmp_path):
    path = tmp_path / "result.json"
    raw = b'[' * 2000 + b'0' + b']' * 2000
    path.write_bytes(raw)
    assert server.saved_result(path) is None
    assert path.read_bytes() == raw


@pytest.mark.parametrize("metadata", [{"extra": float("nan")}, {"\udfff": "invalid key"}])
def test_invalid_extra_metadata_cannot_break_response_serialization(metadata):
    assert not valid_result(sample() | {"metadata": metadata})
