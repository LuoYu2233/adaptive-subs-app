"""Drive the real player through a corrupt saved entry and out-of-order fetches."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from playwright.sync_api import expect, sync_playwright

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
IDS = ["first000001", "slow0000002", "last0000003", "bad00000004"]


def check():
    resume = threading.Event()
    class Files(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw): super().__init__(*a, directory=str(WEB), **kw)
        def log_message(self, *args): pass
        def payload(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            if self.path.startswith("/api/"): return self.payload(200, {"ok": False})
            if self.path == "/data/index.json":
                return self.payload(200,[{"id":v,"title":"片段 "+v,"file":v+".json"} for v in IDS])
            if self.path.startswith("/data/"):
                ident = self.path[6:-5]
                if ident == IDS[3]: return self.payload(409, {"error":"字幕缓存不完整"})
                if ident == IDS[1]:
                    resume.wait(10)
                return self.payload(200,{"video":{"id":ident,"title":ident,"duration":4},"cues":[{"s":0,"e":4,"text":ident,"notes":[]}]})
            return super().do_GET()
    server = ThreadingHTTPServer(("127.0.0.1",0), Files)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                errors=[]; page.on("pageerror",lambda e:errors.append(str(e)))
                page.route("**/*",lambda r: r.continue_() if r.request.url.startswith(f"http://127.0.0.1:{server.server_port}/") else r.abort())
                page.add_init_script("""localStorage.setItem('adaptive-subs:v2',JSON.stringify({introSeen:true}))""")
                page.goto(f"http://127.0.0.1:{server.server_port}/player.html")
                picker=page.locator("#videoSelect")
                expect(picker).to_have_value(IDS[0])
                page.wait_for_url("**/*?v=" + IDS[0])
                picker.select_option(IDS[3])
                expect(page.locator("#toast")).to_contain_text("无法载入")
                expect(picker).to_have_value(IDS[0])
                expect(picker.locator("option")).to_have_count(3)
                assert page.url.endswith("?v="+IDS[0])
                with page.expect_request(lambda request: request.url.endswith(IDS[1] + ".json")):
                    picker.select_option(IDS[1])
                picker.select_option(IDS[2])
                page.wait_for_url("**/*?v="+IDS[2])
                with page.expect_response(lambda r: r.url.endswith(IDS[1]+".json")):
                    resume.set()
                page.wait_for_timeout(120)
                expect(picker).to_have_value(IDS[2])
                assert page.url.endswith("?v="+IDS[2]) and not errors, errors
            finally:
                browser.close()
    finally:
        resume.set(); server.shutdown(); thread.join(5); server.server_close()
    print("Passed real player navigation after a corrupt entry and a slow superseded result.")


if __name__ == "__main__":
    check()
