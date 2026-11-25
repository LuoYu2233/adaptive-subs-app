"""Local model input, offline loading and lifecycle boundaries, without real weights."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import local_asr as L


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ASR_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(L, "_model", None)
    monkeypatch.setattr(L, "_model_path", None)
    monkeypatch.setattr(L, "_schedule_unload", lambda: None)
    for name in L.REQUIRED_FILES:
        (tmp_path / name).write_bytes(b"fixture")
    return tmp_path


def test_missing_tokenizer_cannot_start_implicit_download(model_dir, monkeypatch):
    constructor = Mock()
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=constructor))
    (model_dir / "tokenizer.json").unlink()
    assert not L.available()
    with pytest.raises(RuntimeError, match="tokenizer.json"):
        L.transcribe(np.ones(L.SR, dtype=np.int16), 0, 1)
    constructor.assert_not_called()


def test_complete_local_model_loads_once_and_uses_offline_cpu(model_dir, monkeypatch):
    model = SimpleNamespace(transcribe=Mock(return_value=([SimpleNamespace(text="  Original sentence.  ")], None)))
    constructor = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=constructor))
    assert L.available()
    pcm = np.full(L.SR, 16384, dtype=np.int16)
    for _ in range(2):
        assert L.transcribe(pcm, 0, 1) == "Original sentence."
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["local_files_only"] is True
    assert constructor.call_args.kwargs["device"] == "cpu"
    np.testing.assert_allclose(model.transcribe.call_args.args[0], 0.5)


def test_silence_and_tiny_fragments_do_not_load_model(monkeypatch):
    constructor = Mock(side_effect=AssertionError("Must not load"))
    monkeypatch.setattr(L, "_get_model", constructor)
    assert L.transcribe(np.zeros(L.SR, dtype=np.int16), 0, 1) == ""
    assert L.transcribe(np.ones(100, dtype=np.int16), 0, 0.01) == ""
    constructor.assert_not_called()


@pytest.mark.parametrize("pcm,s,e", [
    (np.ones(100), 0, 1), (np.ones((2, 100), dtype=np.int16), 0, 1),
    (np.ones(100, dtype=np.int16), -1, 1), (np.ones(100, dtype=np.int16), 2, 1),
    (np.ones(100, dtype=np.int16), 0, float("nan")),
])
def test_invalid_audio_fails_before_model_load(pcm, s, e, monkeypatch):
    monkeypatch.setattr(L, "_get_model", Mock(side_effect=AssertionError("Must not load")))
    with pytest.raises(ValueError):
        L.transcribe(pcm, s, e)


def test_idle_model_is_unloaded_only_after_timeout(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(L, "_model", sentinel)
    monkeypatch.setattr(L, "_last_used", 1000)
    monkeypatch.setattr(L.time, "time", lambda: 1001)
    L._unload_if_idle()
    assert L._model is sentinel
    monkeypatch.setattr(L.time, "time", lambda: 1001 + L.IDLE_UNLOAD)
    L._unload_if_idle()
    assert L._model is None


def test_failed_recognition_still_schedules_unload(model_dir, monkeypatch):
    model = SimpleNamespace(transcribe=Mock(side_effect=RuntimeError("failed")))
    monkeypatch.setattr(L, "_get_model", lambda: model)
    schedule = Mock()
    monkeypatch.setattr(L, "_schedule_unload", schedule)
    with pytest.raises(RuntimeError, match="failed"):
        L.transcribe(np.ones(L.SR, dtype=np.int16), 0, 1)
    schedule.assert_called_once()
