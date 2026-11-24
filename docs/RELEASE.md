# 0.4.0-alpha.3 本地试用版

本版用于验证新的学习体验与恢复能力。交付物为源码包，需要 Python 3.12 安装依赖；不包含 Windows 可执行程序，也没有自动部署到公开 Releases。

与 alpha.1 相比，alpha.2 增加日志容量控制与脱敏、Unicode 注释定位修复、损坏结果重试、更严格的模型响应校验、长字幕渲染优化和插件弹窗保存反馈。旧版源码包保留供回退。

alpha.3 进一步修复画像文件损坏后可能被普通标记覆盖的问题，提供先保留原始文件再重建、导入备份的恢复流程。恢复副本位于用户目录 `recovery/`，完整备份或清理时请一并处理。

## 这一版的变化

- 不同语境的同词异义分别学习；播放器和插件共享筛选、重复提示与标记保存规则。
- 任务断点保存、重启恢复、取消、部分失败重试，以及相同字幕/音频片段的结果缓存。
- 无密钥互动体验、模型名称设置与可见性检查、完整长句处理、明确的失败提示。
- 英语画像备份、预览恢复、版本冲突保护、清除与数据说明。
- 浏览器及实际插件回归检查，支持每小时本地工程检查。

## 运行源码试用包

解压后在源码目录执行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app/server.py --port 8765
```

Linux/macOS 可在浏览器打开 `http://127.0.0.1:8765/app`。Linux / Python 3.12 已验证；macOS 尚未验证。
Windows 把上面的 Python 路径替换为 `.venv\Scripts\python`，也可使用 `start.bat`；本版 Windows 安装与打包产物尚未验证。

先点「先体验字幕」，不需要 key。真实视频生成需要在设置页填写自己的模型 key。模型列表检查只能确认账号可见性，不能保证实际生成能力或剩余额度。
浏览器扩展目录为 `extension/`，通过 Chrome/Edge 的扩展管理页加载已解压扩展；本版在 Playwright Chromium 中实际加载验证，Chrome/Edge 正式版本仍需独立验收。

## 本地验证与制包

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python tools/quality_check.py             # 另需 Node.js 22
python tools/browser_check.py
python packaging/source_release.py        # 仅打包干净工作树的已提交源码
python packaging/verify_source_release.py release/AdaptiveSubs-0.4.0-alpha.3-source.zip
```

源码包附有逐文件 SHA-256 清单、Git 提交号和外部 `.sha256` 校验文件；相同提交可得到相同源码 ZIP。校验检测损坏或意外修改，不能替代发布者签名。
制包工具只读取 Git 提交中的允许路径，拒绝被误跟踪的 key 配置、画像、任务数据库与缓存；不从工作目录收集用户数据。

## 升级与回退

1. 在画像页下载备份；等待生成完成或取消任务后退出程序。
2. 保留旧程序目录。另存一份完整用户数据目录，包括 `profile.json`、`tasks.sqlite3`、`data/`、`.cache/` 和配置文件。完整副本含明文 key 和视频记录，请自行妥善保管。
3. 把新版解压到新目录，使用原用户目录，或通过 `ASUBS_HOME` 指定独立试用目录。不要同时让两个版本写同一个用户目录。
4. 在扩展管理页重新加载新版插件，再刷新视频页面。
5. 若要回退，退出新版，恢复升级前的数据目录副本，再启动旧版及匹配的旧插件。旧版不理解新的逐义项标记，不应直接共用新版数据。

源码运行默认把用户数据放在源码根目录；Windows 打包版默认使用 `%LOCALAPPDATA%\AdaptiveSubs`。
画像 JSON 备份只包含学习档案；视频、任务、缓存、API key、浏览器生词本和扩展设置需另行保留。

## 已验证与未验证

已验证：Linux / Python 3.12 依赖安装、离线回归、真实 Chromium 页面交互，以及实际插件在本地模拟的视频页面上的字幕、词义卡片、标记和失败回退。测试使用临时数据和模拟模型，没有付费生成。

待验证：真实模型输出准确度、真实 B 站/YouTube 字幕接口与登录限制、长视频实际费用和延迟、Windows 安装与打包、自动升级及签名。
程序代码、依赖、数据和图标各自的授权范围需要在正式商业发行前逐项核实；仓库注明图标和入场插画不属于 MIT 代码许可。

具体测试记录与已知边界见 [工程记录](ENGINEERING_PROGRESS.md)。
