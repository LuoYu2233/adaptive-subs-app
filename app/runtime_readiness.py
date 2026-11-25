"""Visible, retryable local dependency checks; no provider or model download requests."""
from __future__ import annotations

import threading
import time
import traceback


class ComponentError(RuntimeError):
    def __init__(self, component):
        super().__init__(f"{component}无法正常加载")
        self.component = component


class Readiness:
    def __init__(self, check):
        self.check = check
        self.lock = threading.Lock()
        self.state = {"state": "unchecked", "component": "", "message": "本地英语处理组件尚未检查"}
        self.thread = None

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self):
        with self.lock:
            if self.state["state"] in ("checking", "ready"):
                return dict(self.state)
            self.state = {"state": "checking", "component": "", "message": "正在准备本地英语处理组件…"}
            self.thread = threading.Thread(target=self._run, daemon=True)
            try:
                self.thread.start()
            except RuntimeError:
                self.state = {"state": "error", "component": "本地英语处理组件", "message": "无法开始本地组件检查，请重启程序后重试。"}
            return dict(self.state)

    def _run(self):
        started = time.monotonic()
        try:
            self.check()
            state = {"state": "ready", "component": "", "message": "本地英语处理组件已就绪"}
        except Exception as error:
            component = error.component if isinstance(error, ComponentError) else "本地英语处理组件"
            state = {"state": "error", "component": component,
                     "message": f"{component}无法正常加载；请修复安装后重新检查。已有字幕和体验示例仍可打开。"}
            print(f"本地组件检查失败：{component}；未启动生成任务")
            traceback.print_exception(error)
        state["seconds"] = round(time.monotonic() - started, 3)
        with self.lock:
            self.state = state
