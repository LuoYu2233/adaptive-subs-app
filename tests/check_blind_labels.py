"""Load the unpacked extension in Chromium and score an exported session. No live platform or model."""
import base64
import io
import json
import re
from pathlib import Path
import subprocess
import tempfile
import wave

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"
BASE = "http://127.0.0.1:19876"
ID = "abcdefghijk"


def fixture(video_id):
    return {"video": {"id": video_id, "duration": 6, "title": "标注测试"}, "cues": [
        {"s": 0, "e": 1, "text": "😀 bank or bank?", "zh": "不能提示答案", "notes": [
            {"s": 10, "e": 14, "zh": "河岸", "lv": 12000, "key": "bank", "kind": "sense"}]},
        {"s": 2, "e": 3, "text": "Work from scratch.", "zh": "从头开始", "notes": []},
        {"s": 4, "e": 5, "text": "Home now.", "zh": "回家", "notes": []},
    ]}


def check():
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(Path(temp) / "browser"),
            channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
        errors, unexpected = [], []
        profile = {"known": [], "unknown": [], "level": 3000, "rev": 0, "marks": {"known": [], "unknown": []},
                   "setup": False, "stats": {"known": 0}, "lists": []}
        audio = io.BytesIO()
        with wave.open(audio, "wb") as sound:
            sound.setparams((1, 2, 8000, 0, "NONE", ""))
            sound.writeframes(bytes(96000))
        wav = "data:audio/wav;base64," + base64.b64encode(audio.getvalue()).decode()

        def route_request(route):
            url = route.request.url
            if url.startswith("chrome-extension://"): return route.continue_()
            if url == BASE + "/api/health":
                return route.fulfill(json={"ok": True, "ext": 2, "profile": "/profile", "setup": "/setup"})
            if url.startswith(BASE + "/api/profile") and route.request.method == "GET":
                return route.fulfill(json=profile)
            if url == BASE + "/api/ext/open":
                payload = route.request.post_data_json
                assert payload.get("subs_only") or payload.get("probe")
                video_id = payload["id"]
                if video_id == "fresh000001" and payload.get("subs_only"):
                    return route.fulfill(json={"cached": False, "running": True, "id": video_id})
                data = fixture(video_id)
                if video_id == "partial0001": data["status"] = "partial"
                return route.fulfill(json={"cached": True, "id": video_id, "data": data})
            if "/api/ext/events?" in url:
                assert "fresh000001" in url
                return route.fulfill(json={"events": [["meta", {"source": "manual", "cues": fixture("fresh000001")["cues"]}],
                                                     ["done", {"seconds": 1}]], "next": 2})
            if url.startswith(("https://www.youtube.com/watch?", "https://www.bilibili.com/video/")):
                return route.fulfill(content_type="text/html", body=f'<div id="movie_player"><div class="bpx-player-video-area"><div class="bpx-player-video-wrap"><video muted preload="auto" src="{wav}"></video></div></div></div>')
            if "/x/web-interface/view?" in url:
                return route.fulfill(json={"code": 0, "data": {"title": "测试", "owner": {"mid": 1, "name": "测试"},
                     "pages": [{"page": 1, "cid": 123, "duration": 6}]}})
            if "/x/web-interface/nav" in url:
                return route.fulfill(json={"code": 0, "data": {"isLogin": True, "wbi_img":
                    {"img_url": "https://test.invalid/" + "a"*32 + ".png", "sub_url": "https://test.invalid/" + "b"*32 + ".png"}}})
            if "/x/player/" in url:
                return route.fulfill(json={"code": 0, "data": {"subtitle": {"subtitles":
                    [{"lan": "en", "lan_doc": "英语", "subtitle_url": "https://test.hdslb.com/sub.json"}]}}})
            if url == "https://test.hdslb.com/sub.json": return route.fulfill(json={"body": [{"from": 0, "to": 1, "content": "Test."}]})
            unexpected.append(url)
            return route.abort()

        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            extension = worker.url.rsplit("/", 1)[0]
            context.route("**/*", route_request)
            context.on("page", lambda p: p.on("pageerror", lambda e: errors.append(str(e))))
            worker.evaluate("base => chrome.storage.session.set({base})", BASE)
            worker.evaluate("() => chrome.storage.local.set({settings:{mode:'bilingual'}, asubsKeysHint:1})")
            video = context.new_page()
            video.goto("https://www.youtube.com/watch?v=" + ID)
            expect(video.locator(".asubs-zh")).to_have_text("不能提示答案")

            def open_label():
                popup = context.new_page()
                video.bring_to_front()
                popup.goto(extension + "/popup.html")
                expect(popup.locator("#labelVideo")).to_be_enabled()
                with context.expect_page() as result: popup.locator("#labelVideo").click()
                page = result.value
                page.wait_for_url("**/label.html?tab=*")
                popup.close()
                return page

            page = open_label()
            page.locator("#new").click()
            expect(page.locator("#progress")).to_have_text("第 1 / 3 句")
            expect(video.locator(".asubs-zh")).to_be_hidden()
            expect(video.locator(".asubs-gz")).to_have_count(0)
            video.wait_for_function("document.querySelector('video').paused && document.querySelector('video').currentTime >= 1")
            page.get_by_role("button", name="bank", exact=True).nth(1).click()
            page.locator("#confirm").click()
            expect(page.locator("#progress")).to_have_text("第 2 / 3 句")
            page.close()
            expect(video.locator("html")).not_to_have_class(re.compile("asubs-labeling"))

            profile["level"] = 9000
            page = open_label()
            page.locator("#resume").click()
            expect(page.locator("#progress")).to_have_text("第 2 / 3 句")
            page.locator("#phrase").click()
            page.get_by_role("button", name="from", exact=True).click()
            page.get_by_role("button", name="scratch", exact=True).click()
            page.locator("#confirm").click()
            expect(page.locator("#progress")).to_have_text("第 3 / 3 句")
            page.locator("#confirm").click()
            expect(page.locator("#saved")).to_contain_text("已确认 3 句")
            video.evaluate("history.pushState({}, '', '/watch?v=another0000')")
            expect(page.locator("#confirm")).to_be_disabled()
            for button, name in (("answers", "labels.json"), ("viewer", "viewer.json")):
                with page.expect_download() as info: page.locator("#" + button).click()
                info.value.save_as(Path(temp) / name)
            labels = json.loads((Path(temp) / "labels.json").read_text(encoding="utf-8"))
            assert [c["unknown"] for c in labels["cues"]] == [[{"s": 10, "text": "bank"}], [{"s": 5, "text": "from scratch"}], []]
            viewer = json.loads((Path(temp) / "viewer.json").read_text(encoding="utf-8"))
            assert viewer == {"level": 3000, "known": [], "unknown": []}
            (Path(temp) / "video.json").write_text(json.dumps(fixture(ID)), encoding="utf-8")
            report = json.loads(subprocess.check_output(["node", str(ROOT / "tools/evaluate_prediction.cjs"), "score",
                "video.json", "labels.json", "viewer.json", "normal"], cwd=temp))
            assert (report["hits"], report["missedByReason"]["noCandidate"], report["extra"]) == (1, 1, 0)
            # The detached old window remains available for export while a new window resumes editing.
            video.goto("https://www.youtube.com/watch?v=" + ID)
            expect(video.locator(".asubs-zh")).to_have_text("不能提示答案")
            resumed = open_label()
            resumed.locator("#resume").click()
            expect(resumed.locator("#saved")).to_contain_text("已确认 3 句")
            resumed.close()
            page.close()

            video.goto("https://www.bilibili.com/video/BV1234567890/")
            expect(video.locator(".asubs-zh")).to_have_text("不能提示答案")
            page = open_label()
            page.locator("#new").click()
            expect(page.locator("#progress")).to_have_text("第 1 / 3 句")
            expect(video.locator(".asubs-zh")).to_be_hidden()
            video.evaluate("history.pushState({}, '', '?p=2')")
            expect(page.locator("#confirm")).to_be_disabled()
            page.close()
            video.goto("https://www.youtube.com/watch?v=fresh000001")
            expect(video.locator(".asubs-pill")).to_contain_text("注释已全部生成")
            page = open_label()
            page.locator("#new").click()
            expect(page.locator("#progress")).to_have_text("第 1 / 3 句")
            page.close()
            video.goto("https://www.youtube.com/watch?v=partial0001")
            expect(video.locator(".asubs-zh")).to_have_text("不能提示答案")
            blocked = context.new_page()
            video.bring_to_front()
            blocked.goto(extension + "/popup.html")
            expect(blocked.locator("#labelVideo")).to_be_visible()
            expect(blocked.locator("#labelVideo")).to_be_disabled()
            assert not errors, errors
            assert not unexpected, unexpected
            print("Passed: both video sites, blind playback, cutoff, resume, local snapshot, SPA changes and scored export.")
        finally:
            context.close()


if __name__ == "__main__":
    check()
