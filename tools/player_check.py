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
