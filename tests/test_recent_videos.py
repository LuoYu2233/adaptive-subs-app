import json
import os
from unittest.mock import Mock

import pytest

import server as S


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA", tmp_path)
    monkeypatch.setattr(S, "EXT_DATA", tmp_path / "extension")
    S.EXT_DATA.mkdir()
    S.recent_metadata.cache_clear()
    return tmp_path


def save(folder, ident, title="Original recent video", status="complete", extra=None):
    value = {"video": {"id": ident, "title": title, "duration": 1}, "status": status,
        "cues": [{"s": 0, "e": 1, "text": "Original sample.", "notes": []}], **(extra or {})}
    path = folder / f"{ident}.json"
    path.write_text(json.dumps(value))
    return path


def test_recent_list_rejects_partial_corrupt_unicode_mismatched_and_external_results(library):
    save(library, "abcdefghijk")
    save(library, "bbbbbbbbbbb", status="partial")
    save(library, "ccccccccccc", title="\ud800")
    save(library, "ddddddddddd", extra={"cues": "bad"})
    wrong = save(library, "eeeeeeeeeee")
    wrong.rename(library / "fffffffffff.json")
    (library / "broken.json").write_text('[[[[bad')
    outside = save(library.parent, "ggggggggggg")
    (library / outside.name).symlink_to(outside)
    before = {p.name: p.read_bytes() for p in library.glob("*.json")}
    assert [row["id"] for row in S.recent_videos()] == ["abcdefghijk"]
    assert {p.name: p.read_bytes() for p in library.glob("*.json")} == before


def test_recent_list_handles_long_metadata_and_platform_links(library):
    # The title was previously omitted when it did not fit the first 600 characters.
    save(library, "abcdefghijk", title="原创标题" * 200)
    save(S.EXT_DATA, "bili_BV1234567890_123_en")
    save(S.EXT_DATA, "yt_bbbbbbbbbbb_asr")
    rows = {row["id"]: row for row in S.recent_videos()}
    assert rows["abcdefghijk"]["title"] == "原创标题" * 200
    assert rows["abcdefghijk"]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert rows["bili_BV1234567890_123_en"]["url"] == "https://www.bilibili.com/video/BV1234567890"
    assert rows["yt_bbbbbbbbbbb_asr"]["url"] == "https://www.youtube.com/watch?v=bbbbbbbbbbb"
    assert rows["yt_bbbbbbbbbbb_asr"]["asr"] is True


def test_recent_list_only_parses_the_newest_needed_files_and_reuses_validated_metadata(library, monkeypatch):
    for i in range(30):
        path = save(library, f"video{i:06}")
        os.utime(path, (i + 1000, i + 1000))
    reader = Mock(wraps=S.saved_result)
    monkeypatch.setattr(S, "saved_result", reader)
    first = S.recent_videos(8)
    assert [row["id"] for row in first] == [f"video{i:06}" for i in range(29, 21, -1)]
    assert reader.call_count == 8
    assert S.recent_videos(8) == first and reader.call_count == 8
    first[0]["title"] = "Caller change"
    assert S.recent_videos(8)[0]["title"] != "Caller change"
    assert S.recent_videos(0) == []


def test_cache_invalidates_atomic_replacement_even_with_preserved_modification_time(library):
    path = save(library, "abcdefghijk", title="Previous")
    before = path.stat()
    assert S.recent_videos()[0]["title"] == "Previous"
    replacement = save(library, "temporary", title="Changed")
    value = json.loads(replacement.read_text())
    value["video"]["id"] = "abcdefghijk"
    replacement.write_text(json.dumps(value))
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)
    assert S.recent_videos()[0]["title"] == "Changed"
    path.unlink()
    assert S.recent_videos() == []


def test_cached_invalid_result_becomes_visible_after_completion(library):
    save(library, "abcdefghijk", status="partial")
    assert S.recent_videos() == []
    save(library, "abcdefghijk", title="Now complete")
    assert S.recent_videos()[0]["title"] == "Now complete"
