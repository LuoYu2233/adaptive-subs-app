from unittest.mock import Mock

import pytest

import server as S


@pytest.mark.parametrize("message,code,retry", [
    ("Sign in to confirm you’re not a bot. Use --cookies-from-browser", "platform_verification", True),
    ("HTTP Error 429: Too Many Requests", "platform_rate_limit", True),
    ("Private video. Sign in if you've been granted access", "platform_login", False),
    ("Sign in to confirm your age", "platform_login", False),
    ("Video unavailable", "video_unavailable", False),
    ("HTTP Error 403: Forbidden", "platform_denied", True),
])
def test_platform_restrictions_fail_once_with_actionable_messages(message, code, retry):
    request = Mock(side_effect=RuntimeError(message))
    with pytest.raises(S.VideoAccessError) as error:
        S.with_retries("获取字幕", request)
    assert error.value.code == code and error.value.retry == retry
    assert "--cookies" not in str(error.value)
    request.assert_called_once()


def test_temporary_network_error_still_retries(monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda _: None)
    request = Mock(side_effect=[TimeoutError("timed out"), {"ok": True}])
    assert S.with_retries("获取字幕", request) == {"ok": True}
    assert request.call_count == 2


def test_unknown_error_is_not_misreported_as_no_subtitles():
    assert S.video_access_error(RuntimeError("Unexpected parser error")) is None
