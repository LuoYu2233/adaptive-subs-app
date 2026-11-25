"""Complete the separately scaled phrase measurement using only a browser and a local HTML file."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def check():
    with TemporaryDirectory() as tmp, sync_playwright() as pw:
        out = Path(tmp) / "短语能力试测.html"
        subprocess.run(["node", str(ROOT / "tools/build_phrase_pilot.cjs"), str(out)], check=True)
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 385, "height": 750}, accept_downloads=True)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.on("request", lambda req: errors.append(req.url) if req.url.startswith(("https:", "http:")) else None)
            page.goto(out.as_uri())
            bank = page.locator("#data").text_content()
            page.locator("#begin").click()
            terms = []
            while page.locator("#quiz").is_visible():
                expect(page.locator("mark")).to_be_visible()
                terms.append(page.locator("mark").text_content())
                expect(page.locator("#choices button")).to_have_count(4)
                assert page.locator("#sentence").evaluate("e => e === document.activeElement")
                page.keyboard.press("0" if len(terms) % 2 else "3")
                assert len(terms) <= 8
            expect(page.locator("#complete")).to_be_focused()
            with page.expect_download() as download:
                page.locator("#record").click()
            record = json.loads(Path(download.value.path()).read_bytes())
            assert record["model"] == "3PL phrase CAT" and record["bankSha256"] == json.loads(bank)["bankSha256"]
            assert len(record["responses"]) == len(set(terms)) == len(terms)
            assert 6 <= len(terms) <= 8 and all(r["elapsedMs"] >= 0 for r in record["responses"])
            assert not errors, errors
        finally:
            browser.close()
    print("Passed offline phrase pilot, keyboard-only responses, local export and independent bank fingerprint.")


if __name__ == "__main__":
    check()
