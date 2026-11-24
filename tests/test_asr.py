from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import annotate as A
import asr
import server as S


def client(text="I went to the bank.", reason="stop"):
    reply = SimpleNamespace(usage=None, choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason=reason)])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=reply))))


@pytest.fixture(autouse=True)
def no_rate_delay(monkeypatch):
    monkeypatch.setattr(asr.LIMIT, "wait", lambda *_: None)


@pytest.fixture
def pcm():
    return np.ones(asr.SR, dtype=np.int16)


def test_audio_cache_reuses_only_identical_audio_and_model(pcm):
    service, usage = client(), {}
    args = {"client_instance": service, "model": "test", "usage": usage}
    assert asr.transcribe(pcm, 0, 1, **args) == "I went to the bank."
    assert asr.transcribe(pcm, 0, 1, **args) == "I went to the bank."
    assert service.chat.completions.create.call_count == 1
    assert usage == {"asr_calls": 1, "asr_seconds": 1.0}
    asr.transcribe(pcm * 2, 0, 1, **args)
    asr.transcribe(pcm, 0, 1, **{**args, "model": "changed"})
    assert service.chat.completions.create.call_count == 3


@pytest.mark.parametrize("text,reason,exc", [(None, "stop", asr.AudioError), ("", "stop", asr.AudioError),
                                            ("", "content_filter", asr.ContentRefused)])
def test_failed_recognition_is_not_cached_as_silence(pcm, text, reason, exc):
    service = client(text, reason)
    with pytest.raises(exc):
        asr.transcribe(pcm, 0, 1, client_instance=service)
    assert not list((A.CACHE_DIR / "asr").glob("*.json"))


def test_explicit_music_label_is_valid_cached_silence(pcm):
    service = client("[Music]")
    assert asr.transcribe(pcm, 0, 1, client_instance=service) == ""
    assert asr.transcribe(pcm, 0, 1, client_instance=service) == ""
    assert service.chat.completions.create.call_count == 1


def test_audio_cache_disk_failure_does_not_repeat_request(pcm, monkeypatch):
    service = client()
    monkeypatch.setattr(asr, "write_json", Mock(side_effect=OSError("disk full")))
    assert asr.transcribe(pcm, 0, 1, client_instance=service)
    assert service.chat.completions.create.call_count == 1


def test_cancelled_recognition_does_not_call_model(pcm):
    service = client()
    with pytest.raises(A.GenerationCancelled):
        asr.transcribe(pcm, 0, 1, client_instance=service, cancelled=lambda: True)
    service.chat.completions.create.assert_not_called()


def test_local_fallback_failure_completes_dependency_as_failed(pcm, monkeypatch):
    j = S.Job("yt_abcdefghijk_asr", asr_url="https://www.youtube.com/watch?v=abcdefghijk")
    j.seg = SimpleNamespace(view=lambda: pcm)
    j.pieces, j.texts, j.has_text = [(0, 1)], [""], [False]
    j.cues = [{"s": 0, "e": 1, "text": ""}]
    monkeypatch.setattr(asr, "transcribe", Mock(side_effect=asr.ContentRefused("")))
    monkeypatch.setattr(S.LOCAL_ASR, "available", lambda: False)
    S.run_asr(j, 0)
    assert j.has_text == [True]  # Annotation workers must not wait forever.
    assert j.asr_stats["errors"] == 1 and j.asr_stats["ok"] == 0


def test_rate_limit_wait_is_cancellable():
    limiter = asr.RateLimiter(60)
    limiter.wait()
    with pytest.raises(A.GenerationCancelled):
        limiter.wait(cancelled=lambda: True)


def test_normal_spoken_risk_discussion_is_not_a_model_refusal():
    assert not asr.is_refusal("The investment was considered high risk by analysts.")
    assert not asr.is_refusal("My request was rejected yesterday.")
    assert asr.is_refusal("The request was rejected because it was considered high risk.")
