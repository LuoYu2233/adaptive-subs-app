"""Build a static folder for GitHub Pages: site/

    .venv/Scripts/python tools/build_site.py

Copies app/web/player.html -> site/index.html, app/web/assets/, and the player data
(data/index.json and each video's <id>.json from the videos you processed, app/data/vocab_test.json).
Never copies .env, .cache, raw subtitles or old per-level files.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent     # repository root
WEB = ROOT / "app" / "web"                        # the player page and its assets
VIDEOS = ROOT / "data"                            # videos you processed (from source: the repository root)
SITE = ROOT / "site"

sys.path.insert(0, str(ROOT / "app"))
import video_catalog
from storage import write_json
from subtitle_store import snapshot

SITE_README = """# Adaptive Subs Demo

只给你不认识的词配中文的英语字幕。

- **按词汇量实时调整**：拖动滑块或做 2 分钟词汇测试，注释量跟着变
- **结合语境翻译**：能识别习语、短语动词和熟词僻义
- **点词交互**：标记认识 / 不认识，加入生词本，生词本可一键回到原片段重听
- **精听工具**：重听本句、上一句 / 下一句、每句自动暂停
- **对比模式**：自适应 / 纯英文 / 中英对照

视频嵌入自 YouTube，国内需要能访问 YouTube。字幕注释由脚本预先生成，网页本身不调用任何 API；
个人设置和生词本只保存在你自己的浏览器里。
"""


def main():
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "data").mkdir(parents=True)

    shutil.copytree(WEB / "assets", SITE / "assets")
    # cache-busting: browsers / GitHub Pages' CDN must not serve an old app.js with a new page
    html = (WEB / "player.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        digest = hashlib.sha1((WEB / "assets" / name).read_bytes()).hexdigest()[:8]
        html = html.replace(f'assets/{name}"', f'assets/{name}?v={digest}"')
    (SITE / "index.html").write_text(html, encoding="utf-8")

    index = []
    for row in video_catalog.read(VIDEOS):
        cached = snapshot(VIDEOS / row["file"], row["id"])
        if cached is not None:
            write_json(SITE / "data" / row["file"], cached.data)
            index.append(row)
    (SITE / "data" / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy(ROOT / "app" / "data" / "vocab_test.json", SITE / "data" / "vocab_test.json")
    (SITE / "README.md").write_text(SITE_README, encoding="utf-8")

    total = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    print(f"site/ ready: {len(index)} video(s), {total / 1024:.0f} KB")
    for p in sorted(SITE.rglob("*")):
        if p.is_file():
            print("  ", p.relative_to(SITE).as_posix())


if __name__ == "__main__":
    main()
