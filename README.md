# Adaptive Subs

只给你不认识的词配中文的英语字幕：英文字幕完整保留，只在你（按词汇量估计）可能不认识的单词、短语和熟词僻义上方标一个中文释义。

*English subtitles for Chinese learners of English: the subtitles stay in English, and only the words, phrases and
unusual senses you probably don't know get a short Chinese gloss, based on your vocabulary size.*

- **YouTube**：本地网页播放器，粘贴链接即看，注释边看边生成
- **B 站**：浏览器插件，在 B 站自己的播放器上显示；没有英文字幕的视频用语音识别生成
- 注释由大模型结合上下文生成（OpenAI / DeepSeek / 小米 MiMo 任选），key 只保存在你自己的电脑上

**在线体验**（几个处理好的视频，无需安装）：<https://luoyu2233.github.io/adaptive-subs/>

项目计划见 [PLAN.md](PLAN.md)，这一版的改动见 [DEMO_V2_PLAN.md](DEMO_V2_PLAN.md)，竞品调研见 [research/wordglance.md](research/wordglance.md)。

> 仓库里不包含处理好的视频字幕（那是别人视频的内容）。克隆后启动本地服务器，粘贴一个 YouTube 链接，就会在本机生成并保存到 `data/`。

## 功能
- **贴链接即看（本地即时版）**：粘贴任意 YouTube 链接，几秒内拿到英文字幕并开始播放，注释边看边生成，
  并优先处理你正在看的位置；处理完自动保存，下次秒开
- **按词汇量实时调整**：滑块 1500–15000 连续可调，或者做 2 分钟词汇测试（混有假词 + 例句核对词义）
- **注释多少**：宁多勿缺（默认，拿不准的也标）/ 适中 / 精简。难度按中国学生的考试词表校准，
  课本少教的生活词汇（枕头、脚趾、背包）按更难算；自动字幕里拼错或生造的词也会结合上下文解释
- **没人说话时字幕自动消失**，不会一直停留在上一句
- **结合语境的注释**：单词、短语 / 习语、熟词僻义，每条带难度值；同一个词最多标 2 次，每行最多 3 个
- **点词交互**：点任意单词 → 释义、"我认识了"、"总是提示"、加入生词本、查词典、重听本句
- **生词本**：保存原句和时间点，一键回到原片段重听；可导出 CSV（Anki 可导入）
- **本集预习**：按你的水平列出这集最值得先认识的 15 个词
- **字幕全文**：当前句高亮，点任意一句跳转
- **精听**：重听本句（R）、上一句 / 下一句（← →）、每句自动暂停、空格播放 / 暂停
- **对比模式**：自适应 / 纯英文 / 中英对照；注释可放在单词上方或括号里
- **字幕在视频里**，并有自己的全屏按钮（或按 F），视频和字幕一起全屏；全屏时顶部有精听工具条。
  YouTube 自带的全屏按钮已隐藏，因为它只会把视频放大，不带字幕。也可以选"视频下方（大字）"；手机竖屏时会自动放到视频下方

## 准备（只需要一次）

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m spacy download en_core_web_sm
```

把 `.env.example` 复制成 `.env`，填入 API key；也可以启动后在设置页 <http://127.0.0.1:8765/setup> 里填。

**注释模型三选一**：
- OpenAI gpt-6-luna
- DeepSeek deepseek-flash
- 小米 MiMo mimo-v2.6-flash

语音识别固定用小米 MiMo。在 96 行字幕上的实测对比（2026-11）：

| 模型 | 注释质量 | 一批（12 行）用时 | 96 行费用 |
|---|---|---|---|
| gpt-6-luna | 好，译文最通顺 | 约 19 秒 | $0.012 |
| deepseek-flash（不思考） | 和 GPT 接近，标得稍多 | **约 3 秒** | $0.019（非高峰时段减半） |
| mimo-v2.6-flash（不思考） | 漏掉的稍多 | 约 14 秒 | $0.006 |

DeepSeek 只对离你观看位置较远的批次打开思考模式；MiMo 的思考模式要约 80 秒一批，所以不开。

## 1. 本地使用：双击 `start.bat`

它会启动本地服务器（`server.py`），并自动在浏览器打开播放页（通常是 <http://127.0.0.1:8765/player.html>）。看的时候不要关掉那个黑色窗口。
如果 8765 端口被别的程序占用，会自动换到 8766、8767……，浏览器会直接打开实际的地址；如果服务器已经在运行，就直接打开它，不会重复启动。

- **看新视频**：在顶部输入框粘贴 YouTube 链接，回车：
  1. 2–5 秒：拿到英文字幕，视频可以开始播放，先显示纯英文字幕
  2. 约 15–20 秒：开头部分的注释出现；还没生成注释的句子在右侧字幕全文里显示为浅色
  3. 拖动进度条后，服务器会优先处理你跳到的位置
  4. 全部完成后保存到 `data/视频ID.json`，下次打开（以及部署到 GitHub Pages 后）都会直接加载
- **实测**：8 分钟的视频约 70 秒全部生成；20 分钟的 TED 演讲约 4 分钟
- **费用**：默认模型 `gpt-6-luna`，10 分钟视频约 7.7 万 token、约 **$0.02**（gpt-5-mini 约 $0.18）。
  服务器窗口里会打印每个视频的用量，所有调用的明细在 `.cache/usage.jsonl`。想换模型，在 `.env` 里加一行 `OPENAI_MODEL=...`
- 优先使用人工字幕；没有人工字幕时用 YouTube 自动字幕（没有标点，可能有错字，会弹出提示）
- YouTube 的字幕服务器偶尔会卡住不响应：10 秒没反应就自动重试（最多 3 次），还会换另一条英文字幕轨道；
  都失败时视频里会显示原因和「重试」按钮，服务器窗口只打印一行说明
- **已经处理过的视频**：在顶部下拉框里切换

也可以手动启动：`.venv/Scripts/python server.py`（`--port` 可以换端口）。

### 在 B 站上用（浏览器插件，原型）

`extension/` 文件夹是一个 Chrome / Edge 插件，直接在 B 站自己的播放器上显示注释字幕，注释同样由这个本地服务器生成。
安装和使用见 [extension/README.md](extension/README.md)。有英文字幕的视频（UP 主上传的或 B 站 AI 生成的，需要登录 B 站）直接用；
没有英文字幕的，点视频左上角的「语音识别英文字幕」，用小米 MiMo ASR 识别（`asr.py`，10 分钟约 ¥0.1，需要 MiMo 的 key；音频用 PyAV 解码，不需要另装 ffmpeg）。

### 发给别人试用：打包成 Windows 程序

```bash
.venv/Scripts/python -m pip install pyinstaller av    # 只需一次
.venv/Scripts/python build_app.py
```

生成 `release/AdaptiveSubs-Windows.zip`（约 94 MB），里面有：
- `程序/AdaptiveSubs.exe`
- `浏览器插件/`
- `使用说明.txt`

对方用法：
1. 解压后双击 exe，第一次运行会自动打开设置页
2. 在设置页选注释模型、填**他们自己的** API key（存在他们电脑的 `%LOCALAPPDATA%\AdaptiveSubs\config.env`）
3. 按说明装好插件，就能在 B 站用

程序里**不包含你的任何 key**：`build_app.py` 不打包 `.env`。程序只监听本机，也只提供播放页需要的文件。

> 不要直接双击 `player.html` 或 `site/index.html`。地址以 `file://` 开头时，浏览器会禁止读取字幕和题库数据，YouTube 也不能播放。

## 2. 批量 / 离线处理（可选）

不想开着网页等，也可以用命令行处理整集：

```bash
.venv/Scripts/yt-dlp --skip-download --write-subs --sub-langs en --sub-format srt -o "data/%(id)s.%(ext)s" "https://www.youtube.com/watch?v=视频ID"
.venv/Scripts/python annotate.py data/视频ID.en.srt --title "视频标题"
```

- `--dry-run`：只跑词频筛选，不调用 API
- `--export-srt 5000`：按某个词汇量导出带括号注释的 `.srt`，任何播放器都能用

API 结果缓存在 `.cache/`，重复运行不会重复花钱。
词汇测试的题库（`data/vocab_test.json`）已经生成好了。要重新生成就运行 `python make_vocab_test.py`。

## 3. 分享给别人（GitHub Pages）

```bash
.venv/Scripts/python build_site.py
```

生成 `site/` 文件夹，里面包含网页和所有**已处理好的视频**，不含 `.env`、缓存和原始字幕。
把 `site/` 里的**所有文件和文件夹**上传到 GitHub 仓库，覆盖旧文件；Settings → Pages 选择 `main` 分支、`/ (root)` 目录。

GitHub Pages 上没有后端，所以**不显示粘贴链接的输入框**，只能看已经处理好的视频。
想让别人也能贴链接，需要把 `server.py` 部署到一台服务器上，要考虑 API 费用和 YouTube 对服务器的限制。

## 许可证

[MIT](LICENSE)。词频数据来自 [wordfreq](https://github.com/rspeer/wordfreq)（数据 CC BY-SA 4.0），在本机生成，不随仓库分发。
