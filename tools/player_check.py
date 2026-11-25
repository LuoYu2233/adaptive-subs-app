"""Exercise embedded-player failure/retry with a synthetic API and real iframe navigation.

No YouTube requests leave the browser. This validates local state handling, not live playback.
"""
FAKE_API = r"""window.fixturePlayers = window.fixturePlayers || [];
window.YT = { Player: class {
  constructor(id, options) {
    this.options = options; this.id = options.videoId; this.time = 0; this.state = 2;
    this.frame = document.createElement('iframe'); this.frame.id = id;
    this.frame.src = 'https://www.youtube.com/embed/' + this.id;
    document.getElementById(id).replaceWith(this.frame);
    window.fixturePlayers.push(this);
    setTimeout(() => options.events.onReady({target: this}), 0);
  }
  getVideoData() { return {video_id: this.id}; }
  cueVideoById(id) { this.id = id; }
  getCurrentTime() { return this.time; }
  seekTo(time) { this.time = time; }
  getPlayerState() { return this.state; }
  playVideo() { this.state = 1; this.options.events.onStateChange({target: this, data: 1}); }
  pauseVideo() { this.state = 2; }
  unloadModule() {}
  destroy() { this.frame.remove(); }
} };
window.onYouTubeIframeAPIReady();
"""


def check_player(context, base, output):
    from playwright.sync_api import expect
    page = context.new_page()
    errors, work, referrers = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: work.append(request.url) if "/api/open" in request.url or "/api/explain" in request.url else None)
    target = base + "/player.html?v=zzzzzzzzzzz"

    def iframe(route):
        referrers.append(route.request.all_headers().get("referer"))
        route.fulfill(content_type="text/html", body="<!doctype html><title>Original player fixture</title>")

    try:
        page.route("https://www.youtube.com/embed/**", iframe)
        # The outer check blocks external requests: the first API script must fail explicitly.
        page.goto(target)
        expect(page.locator("#playerNotice")).to_contain_text("未能加载")
        page.locator(".tabs [data-tab='transcript']").click()
        expect(page.locator(".tr-row")).to_have_count(2)
        page.locator(".tr-row").last.click()
        expect(page.locator("#line")).to_contain_text("river bank")
        page.route("https://www.youtube.com/iframe_api", lambda route: route.fulfill(content_type="text/javascript", body=FAKE_API))
        page.locator("#retryPlayer").click()
        expect(page.locator("#playerNotice")).to_be_hidden()
        page.wait_for_function("() => window.fixturePlayers.length === 1")
        assert page.evaluate("window.fixturePlayers[0].options.playerVars.origin") == base
        page.frame_locator("#yt").locator("title").wait_for(state="attached")
        assert referrers == [base + "/"], referrers  # No video query or full page path is exposed.

        for code, text, retry in ((101, "不允许嵌入", False), (153, "无法验证播放器来源", True), (5, "暂时无法播放", True)):
            page.reload()
            page.wait_for_function("() => window.fixturePlayers?.length === 1")
            expect(page.locator("#playerNotice")).to_be_hidden()
            page.evaluate("code => { const p = window.fixturePlayers.at(-1); p.options.events.onError({target: p, data: code}); }", code)
            expect(page.locator("#playerNotice")).to_contain_text(text)
            expect(page.locator("#retryPlayer")).to_be_visible() if retry else expect(page.locator("#retryPlayer")).to_be_hidden()
            expect(page.locator("#openYouTube")).to_have_attribute("href", "https://www.youtube.com/watch?v=zzzzzzzzzzz")
            expect(page.locator(".tr-row")).to_have_count(2)
        page.locator("#retryPlayer").click()
        expect(page.locator("#playerNotice")).to_be_hidden()
        page.evaluate("() => { const old = window.fixturePlayers[0]; old.options.events.onError({target: old, data: 100}); old.options.events.onReady({target: old}); }")
        expect(page.locator("#playerNotice")).to_be_hidden()
        assert page.evaluate("window.fixturePlayers.length") == 2
        page.evaluate("() => window.fixturePlayers.at(-1).options.events.onAutoplayBlocked()")
        expect(page.locator("#playerNotice")).to_contain_text("点击视频内的播放按钮")
        page.evaluate("() => window.fixturePlayers.at(-1).playVideo()")
        expect(page.locator("#playerNotice")).to_be_hidden()

        # A script that loads but never initializes has a bounded wait and no automatic retries.
        page.unroute("https://www.youtube.com/iframe_api")
        scripts = []
        def no_ready(route):
            scripts.append(route.request.url)
            route.fulfill(content_type="text/javascript", body="/* Deliberately never calls the API-ready callback. */")
        page.route("https://www.youtube.com/iframe_api", no_ready)
        page.clock.install()
        page.goto(target)
        expect(page.locator("#playerNotice")).to_contain_text("正在连接")
        page.clock.fast_forward(21000)
        expect(page.locator("#playerNotice")).to_contain_text("连接超时")
        assert len(scripts) == 1
        page.locator("#fsBtn").click()
        expect(page.locator("#playerFrame #playerNotice")).to_be_visible()
        page.evaluate("() => document.fullscreenElement ? document.exitFullscreen() : document.getElementById('fsBtn').click()")
        expect(page.locator(".main-col > #playerNotice")).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(output / "player-timeout-mobile.png"), full_page=True)
        assert not errors, errors
        assert not work, work
        print("Player checks passed: failed load, retry, embed errors, stale callbacks, timeout, origin-only Referer and intact subtitles.")
    finally:
        page.close()


def check_player_profile(context, base, output):
    """The local player keeps using the program's profile through read and write failures."""
    from playwright.sync_api import expect
    page = context.new_page()
    reads, posts, errors = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Keep the browser-only profile distinguishable from the shared one.
    page.add_init_script("""if (window === top && !sessionStorage.getItem("playerProfileFixture")) {
        localStorage.setItem("adaptive-subs:v2", JSON.stringify({introSeen:true, level:6000}));
        sessionStorage.setItem("playerProfileFixture","1");
    }""")
    mode = "pending"
    profile = {"rev": 5, "level": 4500, "known": [], "unknown": [], "marks": {"known": [], "unknown": []}}

    def serve_profile(route):
        if route.request.method == "GET":
            if mode == "pending":
                reads.append(route)
                return
            return route.fulfill(json=profile)
        posts.append(route.request.post_data_json)
        if mode == "invalid": return route.fulfill(json={})
        profile.update(rev=profile["rev"] + 1, level=posts[-1]["level"])
        return route.fulfill(json=profile)

    try:
        page.route(base + "/api/profile", serve_profile)
        page.route(base + "/api/health", lambda route: route.abort())  # A known local build never switches stores.
        page.goto(base + "/player.html?v=zzzzzzzzzzz")
        expect(page.locator("#levelRange")).to_be_disabled()
        page.locator(".tabs [data-tab='transcript']").click()
        expect(page.locator(".tr-row").first).to_be_visible()
        assert len(reads) == 1
        reads.pop().fulfill(status=503, json={"error": "暂时无法读取"})
        expect(page.locator("#profileIssue")).to_contain_text("暂时无法读取")
        expect(page.locator("#levelValue")).to_have_text("画像暂不可用")
        with page.expect_popup() as popup:
            page.locator("#openTest").click()
        expect(popup.value).to_have_url(base + "/profile")
        popup.value.close()
        expect(page.locator("#testDialog")).to_be_hidden()
        assert not posts
        page.locator(".tabs [data-tab='preview']").click()
        page.locator("#tab-preview [data-act='book']").first.click()
        expect(page.locator("#toast")).to_contain_text("不熟悉标记尚未确认")
        expect(page.locator("#bookCount")).to_have_text("1")
        mode = "ready"
        page.locator("#retryProfile").click()
        expect(page.locator("#levelValue")).to_contain_text("4,500")
        expect(page.locator("#profileIssue")).to_be_hidden()
        choose = """el => { el.value = 8; el.dispatchEvent(new Event('input')); }"""
        page.locator("#levelRange").evaluate(choose)
        expect(page.locator("#levelValue")).to_contain_text("8,000")
        assert not posts
        mode = "invalid"
        page.locator("#levelRange").dispatch_event("change")
        expect(page.locator("#toast")).to_contain_text("词汇量保存未确认")
        expect(page.locator("#levelValue")).to_contain_text("4,500")
        mode = "ready"
        page.locator("#levelRange").evaluate(choose)
        page.locator("#levelRange").dispatch_event("change")
        page.wait_for_function("document.querySelector('#levelValue').textContent.includes('8,000')")
        page.reload()
        expect(page.locator("#levelValue")).to_contain_text("8,000")
        assert posts == [{"level": 8000}, {"level": 8000}]
        assert page.evaluate("JSON.parse(localStorage.getItem('adaptive-subs:v2')).level") == 6000
        assert not errors, errors
        print("Player profile checks passed: saved subtitles while loading, profile retry, correct test destination, verified slider writes.")
    finally:
        page.close()


def check_static_test(context, base, output):
    """Run the built static site under a path prefix with its real assets and a separate origin store."""
    import contextlib
    import io
    import tempfile
    from pathlib import Path
    from urllib.parse import urlsplit
    from unittest.mock import patch
    import build_site
    from playwright.sync_api import expect

    isolated = context.browser.new_context(viewport={"width": 390, "height": 844})
    try:
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder) / "site"
            with patch.multiple(build_site, SITE=site, VIDEOS=Path(folder) / "no-videos"), contextlib.redirect_stdout(io.StringIO()):
                build_site.main()
            errors, api_calls = [], []
            stage = "broken"
            def serve(route):
                path = urlsplit(route.request.url).path
                if "/api/" in path: api_calls.append(path)
                if not route.request.url.startswith(base + "/study/"): return route.abort()
                if path.endswith("/vocab_test.json") and stage == "broken":
                    return route.fulfill(json={"bands": [{}], "nonwords": []})
                target = site / (path.removeprefix("/study/") or "index.html")
                if target.is_file(): return route.fulfill(path=target)
                route.fulfill(status=404, body="missing")
            isolated.route("**/*", serve)
            page = isolated.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/study/")
            page.locator("#introSkip").click()
            page.locator("#openTest").click()
            expect(page.locator("#testBody")).to_contain_text("题库格式无效")
            expect(page.locator('[data-act="tstart"]')).to_have_count(0)
            stage = "valid"
            page.locator('[data-act="tretry"]').click()
            page.locator('[data-act="tstart"]').click()
            while page.locator('[data-act="tnext"]').count():
                page.locator('[data-act="trest"]').click()
                page.locator('[data-act="tnext"]').click()
            apply = page.locator('[data-act="tapply"]')
            expect(apply).to_be_visible()
            other = isolated.new_page()
            other.goto(base + "/study/")
            other.locator("#levelRange").fill("7")
            expect(other.locator("#levelValue")).to_contain_text("7,000")
            expect(page.locator("#levelValue")).to_contain_text("7,000")
            apply.click()
            expect(page.locator("#testSaveMessage")).to_contain_text("其他页面已更新")
            expect(apply).to_have_text("用本次结果替换最新设置")
            previous = page.evaluate("localStorage.getItem('adaptive-subs:v2')")
            page.evaluate("""() => {
                window.putPlayerData = Storage.prototype.setItem;
                Storage.prototype.setItem = function() { throw new Error('测试存储已满'); };
            }""")
            apply.click()
            expect(page.locator("#testSaveMessage")).to_contain_text("测试存储已满")
            assert page.evaluate("localStorage.getItem('adaptive-subs:v2')") == previous
            expect(page.locator("#levelValue")).to_contain_text("7,000")
            page.evaluate("() => { Storage.prototype.setItem = window.putPlayerData; }")
            apply.click()
            expect(page.locator("#testDialog")).to_be_hidden()
            expect(other.locator("#levelValue")).to_contain_text("1,500")
            page.reload()
            expect(page.locator("#levelValue")).to_contain_text("1,500")
            assert page.evaluate("JSON.parse(localStorage.getItem('adaptive-subs:v2')).test.est") == 500
            assert not errors and not api_calls, (errors, api_calls)
            print("Built static player passed: invalid bank retry, complete test, two-tab conflict, quota failure, saved result after reload.")
    finally:
        isolated.close()


def check_video_switching(context, base, output):
    """Delay both saved-file and open-job replies while the learner chooses another video."""
    from playwright.sync_api import expect
    page = context.new_page()
    older, newer, submitted = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"
    held, opens, events, errors = [], [], [], []
    entries = [{"id": vid, "file": vid + ".json", "title": "Video " + vid} for vid in [older, newer]]

    def saved(vid):
        return {"video": {"id": vid, "title": "Video " + vid, "duration": 5},
                "cues": [{"s": 0, "e": 5, "text": "Selected " + vid, "zh": "", "notes": []}]}

    def select(vid):
        page.locator("#videoSelect").select_option(vid)
        if vid == older:
            for _ in range(100):
                if held: break
                page.wait_for_timeout(20)
            assert held, "Saved subtitle request was not intercepted"

    def assert_newer():
        expect(page.locator("#videoSelect")).to_have_value(newer)
        expect(page.locator("#tab-transcript")).to_contain_text("Selected " + newer)
        expect(page).to_have_url(base + "/player.html?v=" + newer)

    try:
        page.add_init_script("if (window === top) localStorage.setItem('adaptive-subs:v2', JSON.stringify({introSeen:true, tab:'transcript'}))")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route(base + "/data/index.json", lambda route: route.fulfill(json=entries))
        page.route(base + "/data/" + newer + ".json", lambda route: route.fulfill(json=saved(newer)))
        page.route(base + "/data/" + older + ".json", lambda route: held.append(route))
        page.route(base + "/api/open", lambda route: opens.append(route))
        page.on("request", lambda request: events.append(request.url) if "/api/ext/events" in request.url else None)
        page.goto(base + "/player.html?v=" + older)
        expect(page.locator("#videoSelect option")).to_have_count(2)
        select(newer)
        assert_newer()
        assert len(held) == 1
        held.pop().fulfill(json=saved(older))
        page.wait_for_load_state("networkidle")
        assert_newer()

        select(older)
        select(newer)
        assert_newer()
        held.pop().fulfill(status=503, json={"error": "延迟的读取失败"})
        page.wait_for_load_state("networkidle")
        assert_newer()
        expect(page.locator("#toast")).not_to_contain_text("延迟的读取失败")

        for reply in ({"id": submitted, "cached": False}, {"id": submitted, "cached": True, "file": submitted + ".json"},
                      {"error": "延迟的打开失败"}):
            page.locator("#urlInput").fill(submitted)
            with page.expect_request(base + "/api/open"):
                page.locator("#urlForm").evaluate("e => e.requestSubmit()")
            # A new selection can finish while an earlier POST is still outstanding.
            select(older)
            held.pop().fulfill(json=saved(older))
            expect(page.locator("#videoSelect")).to_have_value(older)
            select(newer)
            assert_newer()
            opens.pop().fulfill(json=reply)
            page.wait_for_load_state("networkidle")
            assert_newer()
            expect(page.locator("#toast")).not_to_contain_text("延迟的打开失败")
        select(older)
        held.pop().fulfill(json=saved(newer))
        expect(page.locator("#line")).to_contain_text("与所选视频不匹配")
        expect(page.locator(".tr-row")).to_have_count(0)
        with page.expect_request(base + "/data/" + older + ".json"):
            page.locator(".retry-btn").click()
        page.wait_for_function("document.querySelector('#line').textContent.includes('重新读取字幕')")
        held.pop().fulfill(json=saved(older))
        expect(page.locator("#tab-transcript")).to_contain_text("Selected " + older)
        # Replay a saved entry from another video before the iframe is ready.
        delayed_api = FAKE_API.replace("setTimeout(() => options.events.onReady({target: this}), 0);",
                                       "window.finishPlayerLoad = () => options.events.onReady({target: this});")
        page.route("https://www.youtube.com/iframe_api", lambda route: route.fulfill(content_type="text/javascript", body=delayed_api))
        page.locator("#retryPlayer").click()
        page.wait_for_function("typeof window.finishPlayerLoad === 'function'")
        records = [{"key": vid, "term": vid, "zh": "", "sentence": "Selected " + vid, "vid": vid,
                    "kind": "word", "lv": 1000, "t": 2.375, "added": 1} for vid in (older, newer, submitted)]
        page.evaluate("book => AdaptivePlayerStore.create().update(p => ({...p,book}))", records)
        page.locator(".tabs [data-tab='book']").click()
        rows = page.locator("#bookList .wrow")
        expect(rows).to_have_count(3)
        fallback = rows.filter(has_text=submitted).locator("a.time-link")
        expect(fallback).to_have_attribute("href", "https://www.youtube.com/watch?v=" + submitted + "&t=2s")
        rows.filter(has_text=newer).locator("[data-act=seekt]").click()
        expect(page).to_have_url(base + "/player.html?v=" + newer)
        expect(page.locator("#line")).to_contain_text("Selected " + newer)
        page.evaluate("window.finishPlayerLoad()")
        page.wait_for_function("id => window.fixturePlayers[0].id === id && window.fixturePlayers[0].time === 2.375 && window.fixturePlayers[0].state === 1", arg=newer)
        assert len(page.evaluate("AdaptivePlayerStore.create().read().book")) == 3
        # Keep reporting the previous iframe ID until both rapid cue commands have been issued.
        page.evaluate("""() => {
            window.cueRequests = [];
            window.fixturePlayers[0].cueVideoById = id => { window.cueRequests.push(id); };
        }""")
        select(older)
        held.pop().fulfill(json=saved(older))
        expect(page).to_have_url(base + "/player.html?v=" + older)
        select(newer)
        expect(page).to_have_url(base + "/player.html?v=" + newer)
        assert page.evaluate("window.cueRequests") == [older, newer]
        assert not opens and not events and not errors, (opens, events, errors)
        print("Saved-word replay passed: cross-video selection, fractional time after delayed iframe readiness and original-video fallback.")
        print("Video switching passed: late success and failure, cached and live open replies all preserve the latest selection.")
    finally:
        page.close()
