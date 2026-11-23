"""Build the Windows package for testers: program + browser extension + instructions, as one zip.

    .venv/Scripts/python packaging/build.py    # -> release/AdaptiveSubs-Windows.zip

Needs (once): .venv/Scripts/python -m pip install pyinstaller
The program holds no API keys: each tester enters their own on its settings page.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent     # repository root
APP = ROOT / "app"
BUILD = ROOT / "build"
RELEASE = ROOT / "release"
NAME = "AdaptiveSubs-Windows"

GUIDE = """Adaptive Subs（测试版）使用说明
================================

在 B 站和 YouTube 看英文视频时，只给你可能不认识的词配中文。由两部分组成：
  - 「程序」文件夹：在你电脑上生成注释（需要你自己的 API key）
  - 「浏览器插件」文件夹：在 B 站和 YouTube 的播放器上显示字幕和注释

一、第一次使用（约 5 分钟）
1. 把整个压缩包解压到一个文件夹（不要在压缩包里直接双击运行）。
2. 双击「程序」文件夹里的 AdaptiveSubs.exe。
   - Windows 可能提示"Windows 已保护你的电脑"：点「更多信息」→「仍要运行」。
   - 杀毒软件如果拦截，请选择允许（程序没有数字签名，所以容易被误报）。
3. 程序窗口里点「填写 API key」（以后在「准备情况 → 注释模型」旁点「更改」）：
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
1. 先双击 AdaptiveSubs.exe，看视频时保持 Adaptive Subs 窗口开着（可以最小化，关掉窗口就停止）。
   窗口里能看到：模型和 key 是否就绪、插件有没有连上、正在处理的视频、英语画像和运行日志。
2. 打开一个英语视频：
   - B 站：先登录（B 站只把字幕给登录用户）。有英文字幕的视频马上显示纯英文，点视频左上角「生成注释」才开始注释（按你的 key 计费）。
   - YouTube：程序去取英文字幕（上传者的或自动生成的），几秒后显示纯英文，同样点「生成注释」开始注释。程序要能连上 YouTube（用代理的话打开「系统代理」）。
   - 没有英文字幕的：视频左上角点「语音识别英文字幕」。
3. 第一次用：在程序窗口「准备情况 → 英语画像」点「开始」（或插件图标 →「建立英语画像」），
   填身份和考试成绩、做 3 分钟词汇测试。字幕会按你的熟词库判断哪些词不用注释，以后随时可以重新测。
4. 看视频时点字幕里任意单词：显示释义，可以标「我认识」（不再注释）或「我不认识」（总会注释）。
   快捷键：按住 Z 临时看这一句的中文；按 B 切换字幕（自适应 / 纯英文 / 中英对照）；按 P 开关遮挡板。
5. 插件图标里还能调词汇量、注释多少、中英对照、字幕位置、遮挡板；「程序设置」可以换模型或 key。
6. 画面里本来印着字幕的视频：可以用遮挡板盖住原字幕，可以拖动、拖边缘调大小，按 UP 主 / 频道记住。
   默认不显示，看视频时按 P 打开 / 关闭。

三、费用
用的是你自己的 API key，按用量付费，大约：
  - 注释：每 10 分钟视频 ¥0.1–0.3（看选的模型）
  - 语音识别：每 10 分钟 ¥0.1（只有点了「语音识别」才会用）
生成过的视频会保存下来，再看不花钱。

四、隐私
  - API key 只保存在这台电脑：%LOCALAPPDATA%\\AdaptiveSubs\\config.env，只发给对应的服务商。
  - 英语画像和标记过的词保存在同一个文件夹的 profile.json，不会上传到任何地方。
  - 程序只在本机（127.0.0.1）运行，别的电脑和网站访问不到。
  - 你的 B 站、YouTube 登录信息不会离开浏览器；发给程序的只有字幕文字和视频编号。

五、常见问题
  - 插件提示"程序没开"：确认 AdaptiveSubs.exe 的窗口开着，然后刷新视频页面。
  - YouTube 提示"连不上 YouTube"：程序要能连上 YouTube。用代理软件的话打开「系统代理」，再点「重试」。
  - 提示 key 无效：程序窗口「准备情况 → 注释模型」点「更改」，重新填写后「保存并测试」。
  - 8765 端口被占用：程序会自动换一个端口，插件会自己找到，不用管。
  - 提示"这个视频说的主要是中文"：语音识别只支持英语视频。
  - 更多说明：https://github.com/LuoYu2233/adaptive-subs-app/blob/main/docs/使用指南.md
  - 新版本：https://github.com/LuoYu2233/adaptive-subs-app/releases

本程序包含开源组件和数据：Python、spaCy（MIT）、FFmpeg（LGPL，经 PyAV）、yt-dlp（Unlicense）、
ECDICT 词典数据（MIT，考试词表和释义）等。
"""


def main():
    for mod in ("PyInstaller", "av"):
        try:
            __import__(mod)
        except ImportError:
            sys.exit(f"缺少 {mod}：先运行 .venv/Scripts/python -m pip install pyinstaller av faster-whisper")
    model = APP / "models" / "faster-whisper-base.en"
    # local speech model, bundled (Hugging Face is often blocked in China). The release uses an int8 copy
    # (76 MB instead of 145 MB, same results), made once with:
    #   ct2-transformers-converter --model openai/whisper-base.en --output_dir models/faster-whisper-base.en
    #       --quantization int8 --copy_files tokenizer.json preprocessor_config.json
    # (needs: pip install ctranslate2 transformers torch). Without it, the fp16 copy is downloaded here.
    if not (model / "model.bin").exists():
        from huggingface_hub import snapshot_download
        snapshot_download("Systran/faster-whisper-base.en", local_dir=str(model), allow_patterns=["*.bin", "*.json", "*.txt"])
    if not (APP / "data" / "ranks_en.txt").exists():
        from wordfreq import top_n_list
        (APP / "data" / "ranks_en.txt").write_text("\n".join(top_n_list("en", 100_000)), encoding="utf-8")

    dist = BUILD / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                    "--distpath", str(dist), "--workpath", str(BUILD / "work"), str(ROOT / "packaging" / "AdaptiveSubs.spec")],
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
