"""Installed extension check against synthetic YouTube/Bilibili pages and the real local server.

Called by browser_check.py. External platform requests are fulfilled locally or blocked.
This covers extension wiring and interactions, not compatibility with the live websites.
"""
import json
import base64
import io
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_extension(playwright, base, home, output):
    from playwright.sync_api import expect

    extension = ROOT / "extension"
    context = playwright.chromium.launch_persistent_context(str(home / "chrome-extension-test"),
        channel="chromium", headless=True, viewport={"width": 1100, "height": 800},
        args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"])
    try:
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        worker.evaluate("base => chrome.storage.session.set({base})", base)
        worker.evaluate("() => chrome.storage.local.set({asubsKeysHint: 1})")
        data = json.loads((home / "data/zzzzzzzzzzz.json").read_text())
        bili = home / "data/extension/bili_BV1234567890_123_en.json"
        bili.parent.mkdir(exist_ok=True)
        bili.write_text(json.dumps(data | {"video": data["video"] | {"id": bili.stem}}))
        external = []
        fail_mark = False
        # A real media timeline crosses the page/content-script isolated-world boundary.
        sound = io.BytesIO()
        with wave.open(sound, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80000)
        audio_url = "data:audio/wav;base64," + base64.b64encode(sound.getvalue()).decode()

        def route_request(route):
            url = route.request.url
            if url.startswith(base + "/"):
                if fail_mark and url.endswith("/api/mark"):
                    return route.fulfill(status=507, content_type="application/json", body=json.dumps({"error": "测试磁盘不可写"}))
                return route.continue_()
            if url.startswith("chrome-extension://"):
                return route.continue_()
            external.append(url)
            if url.startswith("https://www.youtube.com/watch") or url.startswith("https://www.bilibili.com/video/"):
                youtube = "youtube" in url
                host = 'id="movie_player"' if youtube else 'id="bilibili-player" class="bpx-player-container"'
                html = f'''<!doctype html><html><head><meta charset="utf-8"><title>原创插件测试</title>
                  <style>body{{margin:30px;background:#202733;color:white;font:18px system-ui}} .fixture{{position:relative;width:960px;height:540px;background:#121820}} video{{width:960px;height:540px}}</style>
                  </head><body><p>Adaptive Subs 插件回归 · 原创句子 / 模拟页面</p>
                  <div {host}><div class="fixture"><video class="html5-main-video" src="{audio_url}" preload="auto"></video></div></div>
                  </body></html>'''
                return route.fulfill(content_type="text/html", body=html,
                    headers={"Content-Security-Policy": "require-trusted-types-for 'script'"})
            result = None
            if "/x/web-interface/view?" in url:
                result = {"title": "原创词义测试", "owner": {"mid": 1, "name": "测试"}, "pages": [{"page": 1, "cid": 123, "duration": 10}]}
            elif "/x/web-interface/nav" in url:
                result = {"isLogin": True, "wbi_img": {"img_url": "https://example.com/" + "a" * 32 + ".png", "sub_url": "https://example.com/" + "b" * 32 + ".png"}}
            elif "/x/player/" in url:
                result = {"subtitle": {"subtitles": [{"lan": "en", "lan_doc": "英语", "subtitle_url": "https://test.hdslb.com/sub.json"}]}}
            elif url == "https://test.hdslb.com/sub.json":
                return route.fulfill(content_type="application/json", body=json.dumps({"body": [
                    {"from": c["s"], "to": c["e"], "content": c["text"]} for c in data["cues"]]}))
            if result is not None:
                return route.fulfill(content_type="application/json", body=json.dumps({"code": 0, "data": result}),
                    headers={"Access-Control-Allow-Origin": "https://www.bilibili.com", "Access-Control-Allow-Credentials": "true"})
            return route.abort()

        context.route("**/*", route_request)
        for site, url in (("youtube", "https://www.youtube.com/watch?v=zzzzzzzzzzz"),
                          ("bilibili", "https://www.bilibili.com/video/BV1234567890")):
            result = worker.evaluate("() => handle({type:'setprofile',payload:{reset_marks:true}})")
            assert not result.get("error"), result
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("document.querySelector('video').readyState >= 1")
            gloss = page.locator(".asubs-gz")
            expect(gloss).to_have_text("银行", timeout=20000)
            page.locator(".asubs-t").filter(has_text="bank").click()
            expect(page.locator(".asubs-card-zh")).to_contain_text("银行")
            fail_mark = True
            page.locator('.asubs-card [data-act="known"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("标记保存未确认")
            expect(gloss).to_have_text("银行")
            fail_mark = False
            page.locator(".asubs-t").filter(has_text="bank").click()
            page.locator('.asubs-card [data-act="known"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("已加入熟词库")
            expect(gloss).to_have_count(0)
            page.locator("video").evaluate("v => { v.currentTime = 6; }")
            expect(gloss).to_have_text("河岸")
            page.locator(".asubs-t").filter(has_text="bank").click()
            expect(page.locator(".asubs-card-zh")).to_contain_text("河岸")
            page.locator('.asubs-card [data-act="unknown"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("已标为生词")
            saved = json.loads((home / "profile.json").read_text())
            assert len(saved["known"]) == len(saved["unknown"]) == 1
            assert {x["zh"] for x in saved["mark_details"].values()} == {"银行", "河岸"}
            page.screenshot(path=str(output / f"extension-{site}.png"), full_page=True)
            assert not errors, errors
            page.close()
        (output / "extension-requests.json").write_text(json.dumps(external, ensure_ascii=False, indent=2))
        print("Installed extension checks passed on synthetic YouTube/Bilibili pages: overlay, meaning cards, failed-save rollback and separate meaning feedback.")
    except Exception:
        for i, page in enumerate(context.pages):
            page.screenshot(path=str(output / f"extension-failure-{i}.png"), full_page=True)
        raise
    finally:
        context.close()
