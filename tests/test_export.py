import copy
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
import srt

import annotate as A
import storage

FIXTURE = json.loads((Path(__file__).parent / "fixtures/export_meanings.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_export_matches_browser_meaning_grouping(case):
    pool = FIXTURE["notes"]
    before = copy.deepcopy(pool)
    cues = [{"notes": [pool[key] for key in row]} for row in case["cues"]]
    names = {id(note): key for key, note in pool.items()}
    selected = A.visible_notes(cues, case["level"], case["repeat"], case["perLine"])
    assert [[names[id(note)] for note in row] for row in selected] == case["expected"]
    assert pool == before


def sample():
    cues = []
    for i, zh in enumerate(("银行", "银行", "河岸")):
        text = "😀 The bank is here."
        offset = text.index("bank")
        cues.append({"s": i * 2, "e": i * 2 + 1.25, "text": text, "notes": [
            {"s": offset, "e": offset + 4, "key": "bank", "kind": "word", "lv": 5000, "zh": zh}]})
    return {"video": {"id": "abcdefghijk", "title": "Original export sample", "duration": 6}, "cues": cues}


def test_actual_export_preserves_unicode_positions_meanings_and_fractional_timing(tmp_path):
    out = tmp_path / "sample.srt"
    value = sample()
    before = copy.deepcopy(value)
    assert A.export_srt(value, out, 3000) == 3
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    subtitles = list(srt.parse(out.read_text(encoding="utf-8-sig")))
    assert [cue.content for cue in subtitles] == [
        "😀 The bank(银行) is here.", "😀 The bank(银行) is here.", "😀 The bank(河岸) is here."]
    assert subtitles[-1].start.total_seconds() == 4
    assert subtitles[-1].end.total_seconds() == 5.25
    assert value == before


@pytest.mark.parametrize("kind", ["partial", "offset", "time", "huge_time"])
def test_invalid_export_preserves_existing_file(tmp_path, kind):
    out = tmp_path / "sample.srt"
    out.write_bytes(b"previous complete export")
    value = sample()
    if kind == "partial": value["status"] = "partial"
    if kind == "offset": value["cues"][0]["notes"][0]["e"] = 999
    if kind == "time": value["cues"][0]["e"] = float("inf")
    if kind == "huge_time": value["cues"][-1]["e"] = 1e30
    with pytest.raises(ValueError): A.export_srt(value, out, 3000)
    assert out.read_bytes() == b"previous complete export"


def test_failed_export_replacement_preserves_existing_file(tmp_path, monkeypatch):
    out = tmp_path / "sample.srt"
    out.write_bytes(b"previous complete export")
    monkeypatch.setattr(storage.os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError): A.export_srt(sample(), out, 3000)
    assert out.read_bytes() == b"previous complete export"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("level", ["0", "-1", "100001", "bad"])
def test_bad_export_level_cannot_fall_through_to_generation(tmp_path, monkeypatch, level):
    monkeypatch.setattr(A, "PROJECT", tmp_path)
    forbidden = Mock(side_effect=AssertionError("Must not start generation"))
    monkeypatch.setattr(A, "find_candidates", forbidden)
    monkeypatch.setattr(sys, "argv", ["annotate.py", str(tmp_path / "input.srt"), "--export-srt", level])
    with pytest.raises(SystemExit) as caught: A.main()
    assert caught.value.code == 2
    forbidden.assert_not_called()


def test_cli_export_and_empty_input_do_not_need_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "PROJECT", tmp_path)
    forbidden = Mock(side_effect=AssertionError("Must not start generation"))
    monkeypatch.setattr(A, "find_candidates", forbidden)
    src = tmp_path / "abcdefghijk.en.srt"
    src.with_name("abcdefghijk.json").write_text(json.dumps(sample()))
    monkeypatch.setattr(sys, "argv", ["annotate.py", str(src), "--export-srt", "3000"])
    A.main()
    assert src.with_name("abcdefghijk.v3000.srt").is_file()
    src.write_text("")
    monkeypatch.setattr(sys, "argv", ["annotate.py", str(src)])
    with pytest.raises(SystemExit, match="字幕文件为空"): A.main()
    forbidden.assert_not_called()


def test_cli_keeps_completed_result_when_catalog_write_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(A, "PROJECT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(A, "find_candidates", lambda texts: [[] for _ in texts])
    monkeypatch.setattr(A, "run_batches", lambda *args, **kw: [])
    monkeypatch.setattr(A, "translate_lines", lambda *args, **kw: {0: "原创测试句。"})
    monkeypatch.setattr(A, "blend_word_levels", lambda cues: None)
    monkeypatch.setattr(A, "update_index", Mock(side_effect=OSError("disk full")))
    src = tmp_path / "abcdefghijk.en.srt"
    src.write_text("1\n00:00:00,000 --> 00:00:01,000\nOriginal test sentence.\n")
    monkeypatch.setattr(sys, "argv", ["annotate.py", str(src)])
    A.main()
    assert A.valid_result(json.loads((tmp_path / "abcdefghijk.json").read_text()))
    assert "无需重新生成" in capsys.readouterr().err
