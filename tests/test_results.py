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
        path.write_text(json.dumps(value))
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
    path.write_text(json.dumps(value))
    assert server.saved_result(path) is None


def test_overlapping_notes_and_partial_results_are_not_complete():
    value = sample()
    value["cues"][0]["notes"] *= 2
    assert not valid_result(value)
    assert not valid_result(sample() | {"status": "partial"})
