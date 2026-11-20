"""Build the Windows package for testers: program + browser extension + instructions, as one zip.

    .venv/Scripts/python build_app.py          # -> release/AdaptiveSubs-Windows.zip

Needs (once): .venv/Scripts/python -m pip install pyinstaller av
The program holds no API keys: each tester enters their own on its settings page.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
BUILD = ROOT / "build"
RELEASE = ROOT / "release"
NAME = "AdaptiveSubs-Windows"

GUIDE = """Adaptive Subs（测试版）使用说明
================================

在 B 站看英文视频时，只给你可能不认识的词配中文。由两部分组成：
  - 「程序」文件夹：在你电脑上生成注释（需要你自己的 API key）
  - 「浏览器插件」文件夹：在 B 站播放器上显示字幕和注释

一、第一次使用（约 5 分钟）
1. 把整个压缩包解压到一个文件夹（不要在压缩包里直接双击运行）。
2. 双击「程序」文件夹里的 AdaptiveSubs.exe。
   - Windows 可能提示"Windows 已保护你的电脑"：点「更多信息」→「仍要运行」。
   - 杀毒软件如果拦截，请选择允许（程序没有数字签名，所以容易被误报）。
3. 浏览器会自动打开设置页（没打开的话，访问 http://127.0.0.1:8765/setup ）：
   - 选择注释模型并填写它的 API key。推荐 DeepSeek（国内可直接用、速度最快）：
       DeepSeek：https://platform.deepseek.com/api_keys
       小米 MiMo：https://platform.xiaomimimo.com
       OpenAI：https://platform.openai.com/api-keys （国内需要海外网络和付款方式）
   - 语音识别（可选）：填小米 MiMo 的 key。只有在视频没有英文字幕时才用得到。
   - 点「保存并测试」，看到"连接正常"就好了。
4. 安装浏览器插件（Chrome 或 Edge）：
   - 地址栏打开 chrome://extensions （Edge 是 edge://extensions）
   - 打开右上角「开发者模式」
   - 点「加载已解压的扩展程序」，选择「浏览器插件」文件夹
   - 建议点工具栏的拼图图标，把 Adaptive Subs 固定出来

二、日常使用
1. 先双击 AdaptiveSubs.exe，看视频时保持那个黑色窗口开着（关掉窗口就停止）。
2. 在浏览器里登录 B 站，打开带英文的视频：
   - 有英文字幕的视频：自动显示字幕，注释边看边补上。
   - 没有英文字幕的：视频左上角点「语音识别英文字幕」。
3. 点工具栏的插件图标：调词汇量、注释多少、中英对照、字幕位置；「打开设置」可以换模型或 key。

三、费用
用的是你自己的 API key，按用量付费，大约：
  - 注释：每 10 分钟视频 ¥0.1–0.3（看选的模型）
  - 语音识别：每 10 分钟 ¥0.1（只有点了「语音识别」才会用）
生成过的视频会保存下来，再看不花钱。

四、隐私
  - API key 只保存在这台电脑：%LOCALAPPDATA%\\AdaptiveSubs\\config.env，只发给对应的服务商。
  - 程序只在本机（127.0.0.1）运行，别的电脑和网站访问不到。
  - 你的 B 站登录信息不会离开浏览器；发给程序的只有字幕文字和视频编号。

五、常见问题
  - 插件提示"程序没开"：确认 AdaptiveSubs.exe 的窗口开着，然后刷新 B 站页面。
  - 提示 key 无效：插件图标 →「打开设置」，重新填写后「保存并测试」。
  - 8765 端口被占用：程序会自动换一个端口，插件会自己找到，不用管。
  - 提示"这个视频说的主要是中文"：语音识别只支持英语视频。

本程序包含开源组件：Python、spaCy（MIT）、FFmpeg（LGPL，经 PyAV）、yt-dlp（Unlicense）等。
"""


def main():
    for mod in ("PyInstaller", "av"):
        try:
            __import__(mod)
        except ImportError:
            sys.exit(f"缺少 {mod}：先运行 .venv/Scripts/python -m pip install pyinstaller av")
    if not (ROOT / "data" / "ranks_en.txt").exists():
        from wordfreq import top_n_list
        (ROOT / "data" / "ranks_en.txt").write_text("\n".join(top_n_list("en", 100_000)), encoding="utf-8")

    dist = BUILD / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                    "--distpath", str(dist), "--workpath", str(BUILD / "work"), str(ROOT / "AdaptiveSubs.spec")],
                   check=True, cwd=ROOT)

    out = RELEASE / NAME
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copytree(dist / "AdaptiveSubs", out / "程序")
    shutil.copytree(ROOT / "extension", out / "浏览器插件", ignore=shutil.ignore_patterns("README.md"))
    (out / "使用说明.txt").write_text(GUIDE.replace("\n", "\r\n"), encoding="utf-8-sig")

    zpath = RELEASE / f"{NAME}.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(out.rglob("*")):
            z.write(f, Path(NAME) / f.relative_to(out))
    size = zpath.stat().st_size / 1e6
    print(f"\n  done: {zpath}  ({size:.0f} MB)")


if __name__ == "__main__":
    main()
