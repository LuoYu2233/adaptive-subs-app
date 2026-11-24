from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtime_readiness import Readiness, ComponentError
import server as S


def test_simultaneous_requests_start_one_local_check_and_ready_state_is_reused():
    entered, release = threading.Event(), threading.Event()
    def check():
        entered.set()
        assert release.wait(timeout=3)
    call = Mock(side_effect=check)
    state = Readiness(call)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: state.start(), range(20)))
    assert entered.wait(timeout=1)
    assert all(result["state"] == "checking" for result in results)
    release.set()
    state.thread.join(timeout=3)
    assert state.snapshot()["state"] == "ready"
    assert state.start()["state"] == "ready" and call.call_count == 1
    altered = state.snapshot()
    altered["state"] = "error"
    assert state.snapshot()["state"] == "ready"


def test_failed_check_has_safe_public_message_and_can_retry_after_repair():
    check = Mock(side_effect=[ImportError("private internal detail"), None])
    state = Readiness(check)
    state.start(); state.thread.join(timeout=2)
    first = state.snapshot()
    assert first["state"] == "error" and "private internal detail" not in str(first)
    state.start(); state.thread.join(timeout=2)
    assert state.snapshot()["state"] == "ready" and check.call_count == 2


@pytest.mark.parametrize("asr", [False, True])
def test_not_ready_cannot_create_clients_read_checkpoints_or_start_cloud_asr(tmp_path, monkeypatch, asr):
    state = Readiness(Mock(side_effect=ComponentError("英语语言模型")))
    state.start(); state.thread.join(timeout=2)
    monkeypatch.setattr(S, "RUNTIME", state)
    forbidden = Mock(side_effect=AssertionError("Must not begin provider or checkpoint work"))
    monkeypatch.setattr(S.P, "client", forbidden)
    monkeypatch.setattr(S.ASR, "client", forbidden)
    monkeypatch.setattr(S.TASK_STORE, "load", forbidden)
    with pytest.raises(S.JobBusy, match="本地英语处理组件尚未就绪"):
        S.get_job("abcdefghijk", start=True, save_dir=tmp_path,
                  asr_url="https://www.youtube.com/watch?v=abcdefghijk" if asr else None)
    forbidden.assert_not_called()
    assert not S.JOBS
    # Read-only probes remain available and do not retry the failed local check.
    assert S.get_job("abcdefghijk", start=False) is None
    assert state.check.call_count == 1


@pytest.mark.parametrize("issue,component", [("missing_model", "英语语言模型"), ("no_lemmas", "英语语言模型"),
                                           ("bad_ranks", "英语词频表")])
def test_component_check_identifies_missing_or_unusable_resources(monkeypatch, issue, component):
    class Model:
        lang = "en"
        def __call__(self, text):
            return [SimpleNamespace(is_alpha=True, lemma_="" if issue == "no_lemmas" else "bank")]
    nlp = Mock(side_effect=ImportError("missing")) if issue == "missing_model" else Mock(return_value=Model())
    ranks = Mock(return_value={} if issue == "bad_ranks" else {"bank": 3000})
    monkeypatch.setattr(S.A, "nlp", nlp)
    monkeypatch.setattr(S.A, "load_ranks", ranks)
    with pytest.raises(ComponentError) as result: S.check_local_components()
    assert result.value.component == component
