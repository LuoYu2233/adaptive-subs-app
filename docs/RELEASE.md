# 学习功能草案

本草案基于基础设施分支。它试验按语境标记词义、校验词汇题库，并在词汇测试保存冲突时保留作答。判词仍使用难度阈值，IRT 方案尚待确定。请使用独立的 ASUBS_HOME 和浏览器配置试用。

草案会写入按语境生成的标记键与 mark_details。回到基础设施版前，请恢复试用前的画像和浏览器备份。

## 本地验证与制包

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python tools/quality_check.py             # 另需 Node.js 22
python tools/browser_check.py
python packaging/source_release.py        # 仅打包干净工作树的已提交源码
python packaging/verify_source_release.py release/AdaptiveSubs-0.4.0-alpha.23-source.zip
python tools/release_smoke.py release/AdaptiveSubs-0.4.0-alpha.23-source.zip
python tools/instance_check.py           # 临时数据目录，验证重复启动、崩溃恢复和退出
python tools/runtime_check.py            # 模拟组件缺失，再加载真实本地组件验证修复
```

源码包附有逐文件 SHA-256 清单、Git 提交号和外部 `.sha256` 校验文件；相同提交可得到相同源码 ZIP。校验检测损坏或意外修改，不能替代发布者签名。
制包工具只读取 Git 提交中的允许路径，拒绝被误跟踪的 key 配置、画像、任务数据库与缓存；不从工作目录收集用户数据。

## 可选本地语音兜底

源码包不含语音模型。主动安装会从 [Systran 的公开模型仓库](https://huggingface.co/Systran/faster-whisper-base.en) 下载约 148 MB 固定版本文件，并核对逐文件 SHA-256；不需要付费 API。安装工具不会覆盖已有但不同或损坏的模型目录，可指定新目录并通过 `LOCAL_ASR_MODEL_DIR` 使用。

```sh
python tools/prepare_local_model.py --install
python tools/prepare_local_model.py                 # 再次校验，无下载
# 已有离线文件：加 --install --source /path/to/model
python tools/local_asr_check.py                    # 另需本机 eSpeak NG
```

识别时只读取完整本地模型，不补下载。真实组件检查使用三段原创合成英语和静音，保存参考文本、转写、逐词差异、耗时、音频摘要和环境信息到 `test-results/local-asr/check/`。该检查不代表真实视频、口音或噪声条件下的准确度验收，不纳入离线单元测试。

## 升级与回退

1. 在画像页下载备份；等待生成完成或取消任务后退出程序。
2. 保留旧程序目录。另存一份完整用户数据目录，包括 `profile.json`、`tasks.sqlite3`、`data/`、`.cache/` 和配置文件。完整副本含明文 key 和视频记录，请自行妥善保管。
3. 把新版解压到新目录，使用原用户目录，或通过 `ASUBS_HOME` 指定独立试用目录。不要同时让两个版本写同一个用户目录。
4. 在扩展管理页重新加载新版插件，再刷新视频页面。
5. 若要回退，退出新版，恢复升级前的数据目录副本，再启动旧版及匹配的旧插件。

源码运行默认把用户数据放在源码根目录；Windows 打包版默认使用 `%LOCALAPPDATA%\AdaptiveSubs`。
alpha.11 增加同目录单实例保护：重复启动会使用已运行的同版本服务，换端口也不会开启第二个写入进程。崩溃退出由操作系统释放锁，无需删除文件；请把用户数据放在本机磁盘。不同版本或不支持此保护的旧程序需先退出。
画像 JSON 备份只包含学习档案；视频、任务、缓存、API key、浏览器生词本和扩展设置需另行保留。
生词本可在播放器单独导出 JSON，再从目标浏览器恢复；不同浏览器或不同本地端口不会自动同步收藏。浏览器重建前保留的原始副本用于人工检查，不能当作正常生词本备份导入。

## 已验证与未验证

已验证：Linux / Python 3.12 依赖安装、离线回归、真实 Chromium 页面交互，以及实际插件在本地模拟的视频页面上的字幕、词义卡片、标记和失败回退。云端请求使用模拟模型；本地 Whisper 另做了真实 CPU 合成语音检查，没有付费生成。

待验证：真实模型输出准确度、真实 B 站/YouTube 字幕接口与登录限制、长视频实际费用和延迟、Windows 安装与打包、自动升级及签名。
真实 YouTube 单次公共字幕获取已成功，但随后匿名访问受到平台身份验证限制，真实页面端到端检查未通过。可用 `python tools/live_youtube_check.py --video VIDEO_ID` 显式复查；该工具会联系真实平台，不包含在定时回归中。平台限制不会通过自动高频重试或读取浏览器登录信息来处理。
程序代码、依赖、数据和图标各自的授权范围需要在正式商业发行前逐项核实；仓库注明图标和入场插画不属于 MIT 代码许可。
