"""Build the Windows package for testers: program + browser extension + instructions, as one zip.

    .venv/Scripts/python packaging/build.py    # -> release/AdaptiveSubs-VERSION-Windows.zip

Needs: Windows, Git, Python 3.12 and requirements-build.txt in a clean virtualenv.
The program holds no API keys: each tester enters their own on its settings page.
"""
import hashlib
import importlib.metadata
import io
import json
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from source_release import MANIFEST, publish, snapshot, write_snapshot
from verify_source_release import verify

ROOT = Path(__file__).resolve().parent.parent     # repository root
RELEASE = ROOT / "release"

GUIDE = """Adaptive Subs（测试版）使用说明
================================

在 B 站和 YouTube 看英文视频时，只给你可能不认识的词配中文。由两部分组成：
  - 「程序」文件夹：在你电脑上生成注释（需要你自己的 API key）
  - 「浏览器插件」文件夹：在 B 站和 YouTube 的播放器上显示字幕和注释

一、第一次使用（约 5 分钟）
1. 把整个压缩包解压到一个文件夹（不要在压缩包里直接双击运行）。
2. 双击「程序」文件夹里的 AdaptiveSubs.exe。
   - 当前试用包没有数字签名。先核对来源和校验值；遇到系统拦截时保留提示，联系发布者核实。
3. 可以先点「先体验字幕」：使用内置原创示例，无需 key，不请求模型。
   要处理真实视频，再点「填写 API key」（以后在「准备情况 → 注释模型」旁点「更改」）：
   - 选择注释模型并填写它的 API key。例如 DeepSeek（国内可直接用）：
       DeepSeek：https://platform.deepseek.com/api_keys
       小米 MiMo：https://platform.xiaomimimo.com
       OpenAI：https://platform.openai.com/api-keys （国内需要海外网络和付款方式）
   - 语音识别（可选）：填小米 MiMo 的 key。只有在视频没有英文字幕时才用得到。
   - 点「保存并测试」，检查账号是否能看到所选模型；不代表实际生成能力或剩余额度。
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
用的是你自己的 API key，按服务商、模型和实际用量计费，以服务商账单为准。
生成注释、语音识别及点击需要上下文解释的单词时可能请求模型。
完整缓存可直接复用；重试会尽量复用成功的批次。

四、隐私
  - API key 只保存在这台电脑：%LOCALAPPDATA%\\AdaptiveSubs\\config.env，只发给对应的服务商。
  - 英语画像和标记过的词保存在同一个文件夹的 profile.json，不会上传到任何地方。
  - 程序监听本机地址（127.0.0.1），不提供公网服务。
  - 注释和查词会向所选服务商发送字幕上下文；语音识别向 MiMo 发送音频片段。
  - 画像页支持备份和恢复；控制台「数据与隐私」说明保存位置、外部请求及删除方法。

五、常见问题
  - 插件提示"程序没开"：确认 AdaptiveSubs.exe 的窗口开着，然后刷新视频页面。
  - YouTube 提示"连不上 YouTube"：程序要能连上 YouTube。用代理软件的话打开「系统代理」，再点「重试」。
  - 提示 key 无效：程序窗口「准备情况 → 注释模型」点「更改」，重新填写后「保存并测试」。
  - 8765 被其他程序占用：通常会自动换端口；若提示 Adaptive Subs 版本不一致，请先退出旧版。
  - 本地英语组件不可用：控制台会显示修复说明；修复后点「重新检查」，已有字幕和画像备份仍可读取。
  - 提示"这个视频说的主要是中文"：语音识别只支持英语视频。
  - 更多说明：https://github.com/LuoYu2233/adaptive-subs-app/blob/main/docs/使用指南.md
  - 新版本：https://github.com/LuoYu2233/adaptive-subs-app/releases

本程序包含开源组件和数据：Python、spaCy（MIT）、FFmpeg（LGPL，经 PyAV）、yt-dlp（Unlicense）、
ECDICT 词典数据（MIT，考试词表和释义）等。
"""


def safe_name(name):
    """Reject Windows aliases, special paths and accidentally bundled runtime data."""
    path = PurePosixPath(name)
    if not name or path.is_absolute() or str(path) != name:
        raise ValueError(f"Unsafe package path: {name}")
    for part in path.parts:
        low = part.casefold()
        if (part in {".", ".."} or part.endswith((".", " "))
                or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", part)):
            raise ValueError(f"Unsafe package path: {name}")
        if (low.endswith(".env") or low in {".env", "profile.json", ".instance.lock", ".instance.json",
                ".cache", "logs", "recovery", ".venv", "__pycache__"} or low.startswith("tasks.sqlite3")):
            raise ValueError(f"Runtime or private file in package: {name}")
    return name


def package(program, entries, version, revision, build_info, release=RELEASE):
    """Archive fresh native output plus committed extension; does not validate an EXE."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?", version):
        raise ValueError("Invalid release version")
    if program.is_symlink() or not (program / "AdaptiveSubs.exe").is_file():
        raise ValueError("Missing native executable")
    files = {}
    for path in program.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not accepted: {path.name}")
        if path.is_file():
            files["程序/" + path.relative_to(program).as_posix()] = path
        elif not path.is_dir():
            raise ValueError(f"Non-regular package file: {path.name}")
    for name, body in entries.items():
        if name.startswith("extension/"):
            files["浏览器插件/" + name.removeprefix("extension/")] = body
    if "浏览器插件/manifest.json" not in files:
        raise ValueError("Missing browser extension")
    files["使用说明.txt"] = GUIDE.replace("\n", "\r\n").encode("utf-8-sig")
    names = [safe_name(name) for name in files]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Case-insensitive package path collision")
    release.mkdir(parents=True, exist_ok=True)
    name = f"AdaptiveSubs-{version}-Windows"
    destination = release / f"{name}.zip"
    manifest = {"format": "adaptive-subs-windows-release", "version": version,
                "commit": revision, "build": build_info, "files": {}}
    with tempfile.TemporaryDirectory(dir=release, prefix=".windows-release-") as temp:
        staged = Path(temp) / "package.zip"
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, source in sorted(files.items()):
                info = zipfile.ZipInfo(f"{name}/{path}", (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                checksum, size = hashlib.sha256(), 0
                # Stream native binaries/model files; never hold the package in RAM.
                with (source.open("rb") if isinstance(source, Path) else io.BytesIO(source)) as stream:
                    with archive.open(info, "w", force_zip64=True) as target:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            target.write(chunk)
                            checksum.update(chunk)
                            size += len(chunk)
                manifest["files"][path] = {"sha256": checksum.hexdigest(), "bytes": size}
            info = zipfile.ZipInfo(f"{name}/{MANIFEST}", (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode())
        verify(staged, kind="windows")
        checksum = publish(staged, destination)
    return destination, checksum


def main():
    if sys.platform != "win32":
        sys.exit("Windows 程序必须在 Windows 环境构建；源码包使用 packaging/source_release.py")
    if sys.version_info[:2] != (3, 12):
        sys.exit("请使用 Python 3.12 的独立构建环境")
    entries, version, revision = snapshot(ROOT)
    if importlib.metadata.version("pyinstaller") != "6.22.3":
        sys.exit("请在独立 Python 3.12 环境安装 requirements-build.txt 中固定的构建依赖")
    build_info = {"python": platform.python_version(), "platform": sys.platform,
                  "machine": platform.machine(),
                  "packages": sorted({(d.metadata["Name"], d.version) for d in importlib.metadata.distributions()})}
    # All source and model preparation happens in isolated committed-source staging.
    # The working tree, old build output, ignored extension files and local ranks are never copied.
    with tempfile.TemporaryDirectory(prefix="asubs-build-") as temp:
        staging = Path(temp)
        source = staging / "source"
        write_snapshot(entries, source)
        model_command = [sys.executable, str(source / "tools" / "prepare_local_model.py"), "--install"]
        model = ROOT / "app" / "models" / "faster-whisper-base.en"
        if model.exists():
            model_command.extend(["--source", str(model)])
        subprocess.run(model_command, cwd=source, check=True)
        from wordfreq import top_n_list
        (source / "app" / "data" / "ranks_en.txt").write_text("\n".join(top_n_list("en", 100_000)), encoding="utf-8")
        dist = staging / "dist"
        subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--log-level", "WARN",
                        "--distpath", str(dist), "--workpath", str(staging / "work"),
                        str(source / "packaging" / "AdaptiveSubs.spec")], check=True, cwd=source)
        path, checksum = package(dist / "AdaptiveSubs", entries, version, revision, build_info)
    print(f"Windows test package: {path}\nSHA-256: {checksum}\nCommit: {revision}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError, importlib.metadata.PackageNotFoundError) as error:
        sys.exit(str(error))
