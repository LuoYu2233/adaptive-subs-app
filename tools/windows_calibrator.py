"""Select a CAT record, word table and viewing sessions to freeze an offline viewing model."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from fit_viewing_feedback import frozen_report

JSON_FILES = [("JSON 文件", "*.json"), ("所有文件", "*.*")]
FIELDS = {"词频和考纲": "b_initial", "仅词频": "b_frequency"}


class Calibrator(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=18)
        root.title("Adaptive Subs · 观看反馈校准")
        root.minsize(530, 310)
        self.pack(fill="both", expand=True)
        self.columnconfigure(1, weight=1)
        self.files = {}
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.buttons = {}
        self.names = {}
        ttk.Label(self, text="为同一位学习者生成观看校准报告。文件只在本机处理。").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        for row, (key, caption) in enumerate((("record", "选择完整测量记录"), ("table", "选择配套词难度表"),
                                               ("train", "选择观看记录（可多选）")), 1):
            button = ttk.Button(self, text=caption, command=lambda k=key: self.choose(k))
            button.grid(row=row, column=0, sticky="ew", padx=(0, 12), pady=3)
            self.buttons[key] = button
            name = tk.StringVar(value="尚未选择")
            ttk.Label(self, textvariable=name, wraplength=320).grid(row=row, column=1, sticky="w")
            self.names[key] = name
        ttk.Label(self, text="观看时使用的难度量尺").grid(row=4, column=0, sticky="w", pady=7)
        self.field = ttk.Combobox(self, values=list(FIELDS), state="readonly")
        self.field.current(0)
        self.field.grid(row=4, column=1, sticky="w")
        self.save = ttk.Button(self, text="生成校准报告…", command=self.generate, state="disabled")
        self.save.grid(row=5, column=0, sticky="ew", pady=8)
        self.status = tk.StringVar(value="请先选择试测导出的 word-cat-record.json。")
        ttk.Label(self, textvariable=self.status, wraplength=490).grid(row=6, column=0, columnspan=2, sticky="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after_idle(self.buttons["record"].focus_set)

    def choose(self, key):
        if key == "train":
            paths = filedialog.askopenfilenames(parent=self, title="选择先前同一学习者的观看记录", filetypes=JSON_FILES)
            if not paths: return
            self.files[key] = [Path(p) for p in paths]
            self.names[key].set(f"已选择 {len(paths)} 段 · " + Path(paths[0]).name)
        else:
            path = filedialog.askopenfilename(parent=self, title=self.buttons[key].cget("text"), filetypes=JSON_FILES)
            if not path: return
            self.files[key] = Path(path)
            self.names[key].set(Path(path).name)
        if len(self.files) == 3:
            self.save.configure(state="normal")
            self.status.set("请在新视频开始盲标前生成报告。")

    def generate(self):
        dest = filedialog.asksaveasfilename(parent=self, title="保存观看校准报告", initialfile="观看校准.json",
                                            defaultextension=".json", filetypes=JSON_FILES)
        if not dest: return
        for button in (*self.buttons.values(), self.save, self.field):
            button.configure(state="disabled")
        self.status.set("正在核对文件并计算…")
        self.future = self.executor.submit(frozen_report, self.files["record"], self.files["table"],
                                          FIELDS[self.field.get()], self.files["train"], output=Path(dest))
        self.after(70, self.finish, dest)

    def finish(self, dest):
        if not self.future.done():
            self.after(70, self.finish, dest)
            return
        try:
            self.future.result()
            self.status.set(f"已保存到 {dest}。在扩展的「IRT 判词试用」中选择这份观看校准报告。")
        except Exception as err:
            self.status.set(f"无法生成报告。{err}")
        self.future = None
        for button in (*self.buttons.values(), self.save):
            button.configure(state="normal")
        self.field.configure(state="readonly")

    def close(self):
        if self.future is not None:
            self.status.set("正在保存，请等本次计算完成后关闭。")
            return
        self.executor.shutdown(wait=False)
        self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Calibrator(root)
    root.mainloop()
