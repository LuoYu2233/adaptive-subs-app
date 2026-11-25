"""Run opt-in viewing, successful corrections, export and deletion in an installed extension."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
VIDEO = "https://www.youtube.com/watch?v=abcdefghijk"
BASE = "http://127.0.0.1:19876"
WORDS = ["orbit", "plume", "fog", "hush", "glass", "fern", "bank", "skip"]
PROB = [.9, .8, .7, .65, .3, .1, .8, .8]


def check():
    sentence = " ".join(WORDS)
    notes = [{"s": sentence.index(w), "e": sentence.index(w) + len(w), "key": w,
              "kind": "sense" if w == "skip" else "word", "lv": 9000 - i, "zh": "中文释义"}
             for i, w in enumerate(WORDS)]
    video = {"video": {"id": "abcdefghijk", "title": "私人测试标题", "duration": 60},
             "cues": [{"s": 0, "e": 60, "text": sentence, "notes": notes}]}
    profile = {"level": 5000, "rev": 1, "known": ["bank"], "unknown": [], "marks": {"known": ["bank"], "unknown": []}}
    model = {"version": 1, "fingerprint": "1" * 64, "field": "b_initial", "probabilities": dict(zip(WORDS, PROB))}
    with TemporaryDirectory() as temp, sync_playwright() as pw:
        ext = ROOT / "extension"
        ctx = pw.chromium.launch_persistent_context(str(Path(temp) / "browser"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--autoplay-policy=no-user-gesture-required"])
        try:
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            origin = worker.url.rsplit("/", 1)[0]
            worker.evaluate("base => chrome.storage.session.set({base})", BASE)
            worker.evaluate("model => chrome.storage.local.set({settings:{pref:'normal'},asubsKeysHint:1,irtEnabled:true,irtPreview:model})", model)
            unexpected = []

            def route(r):
                url = r.request.url
                if url.startswith("chrome-extension://"): return r.continue_()
                if url == BASE + "/api/health": return r.fulfill(json={"ok": True, "ext": 2, "profile": "/profile"})
                if url.startswith(BASE + "/api/profile"): return r.fulfill(json=profile)
                if url == BASE + "/api/ext/open":
                    return r.fulfill(json={"id": "abcdefghijk", "cached": True, "data": video})
                if url == BASE + "/api/mark":
                    mark = r.request.post_data_json
                    if mark["key"] == "hush": return r.fulfill(status=500, json={"error": "cannot save"})
                    state, key = mark["state"], mark["key"]
                    profile[state].append(key)
                    profile["marks"][state].append(key)
                    profile["rev"] += 1
                    return r.fulfill(json={"ok": True, "rev": profile["rev"]})
                if url == VIDEO:
                    return r.fulfill(content_type="text/html", body='<div id="movie_player" style="width:800px;height:400px"><video style="width:100%;height:100%" muted></video></div>')
                unexpected.append(url)
                return r.abort()

            ctx.route("**/*", route)
            page = ctx.new_page()
            page.on("pageerror", lambda e: unexpected.append(str(e)))
            page.goto(VIDEO)
            expect(page.locator(".asubs-w")).to_have_text(["orbit","plume","fog"])
            assert not worker.evaluate("async () => (await chrome.storage.local.get('wordStudyDraft')).wordStudyDraft")
            tab_id = worker.evaluate("async () => (await chrome.tabs.query({active:true,currentWindow:true}))[0].id")
            control = ctx.new_page()
            control.goto(origin + "/study.html?tab=" + str(tab_id))
            expect(control.locator("#begin")).to_be_visible()
            control.locator("#clip").fill("clip01")
            control.locator("#domain").fill("科技")
            control.locator("#begin").click()
            expect(control.locator("#status")).to_contain_text("正在记录")
            expect(control.locator("#status")).to_contain_text("0 次")
            page.bring_to_front()
            page.evaluate("""async () => {
              const video=document.querySelector('video'), canvas=document.createElement('canvas');
              const c=canvas.getContext('2d'); c.fillRect(0,0,20,20);
              video.srcObject=canvas.captureStream(20);
              setInterval(()=>{c.fillStyle='rgb('+Math.floor(performance.now())%255+',3,4)';c.fillRect(0,0,20,20)},50);
              document.querySelector('#movie_player').classList.add('ad-showing');
              await video.play();
            }""")
            page.wait_for_timeout(650)
            expect(control.locator("#status")).to_contain_text("0 次")
            page.evaluate("document.querySelector('#movie_player').classList.remove('ad-showing')")
            expect(control.locator("#status")).to_contain_text("6 次")
            def click_word(i, action):
                page.locator(f".asubs-t[data-n='{i}']").click()
                page.locator(f".asubs-card [data-act='{action}']").click()
            click_word(5, "close")
            click_word(3, "unknown")  # unsuccessful save leaves only the actual lookup
            click_word(0, "known")
            click_word(4, "unknown")
            control.bring_to_front()
            control.locator("#stop").click()
            expect(control.locator("#status")).to_contain_text("已停止")
            with control.expect_download() as dl:
                control.locator("#export").click()
            result = json.loads(Path(dl.value.path()).read_bytes())
            assert result["kind"] == "word-viewing-study" and result["tableSha256"] == "1" * 64
            assert result["clip"] == "clip01" and result["domain"] == "科技"
            rows = {r["word"]: (r["shown"], r["event"]) for r in result["exposures"]}
            assert rows == {"orbit": (True, "known"), "plume": (True, "none"), "fog": (True, "none"),
                            "hush": (False, "lookup"), "glass": (False, "unknown"), "fern": (False, "lookup")}, rows
            for hidden in ("abcdefghijk", "私人测试标题", sentence, "youtube.com"):
                assert hidden not in json.dumps(result, ensure_ascii=False)
            control.once("dialog", lambda dialog: dialog.accept())
            control.locator("#clear").click()
            expect(control.locator("#begin")).to_be_visible()
            assert not worker.evaluate("async () => Object.keys(await chrome.storage.local.get(null)).some(k => k.startsWith('wordStudy'))")
            control.locator("#begin").click()
            expect(control.locator("#status")).to_contain_text("正在记录")
            page.reload()
            expect(control.locator("#status")).to_contain_text("已停止")
            assert not unexpected, unexpected
        finally:
            ctx.close()
    print("Installed extension passed consent, playback dwell, ad exclusion, capped display, feedback, export, deletion and reload.")


if __name__ == "__main__":
    check()
