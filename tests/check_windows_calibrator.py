"""Use the same Tk buttons as a viewer and check the resulting UTF-8 frozen model."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import windows_calibrator as ui


def packaged(exe, record, table, study, output):
    from pywinauto import Desktop, keyboard
    desktop = Desktop(backend="win32")
    process = subprocess.Popen([str(exe)])
    main = desktop.window(title="Adaptive Subs · 观看反馈校准")
    try:
        main.wait("visible", timeout=90)
        main.set_focus()
        def choose(keys, title, path):
            keyboard.send_keys(keys, pause=.12)
            dialog = desktop.window(title=title, class_name="#32770")
            dialog.wait("visible", timeout=10)
            inputs = dialog.descendants(class_name="Edit")
            # Standard shell file dialogs put the filename in edit control 1148 or 1001.
            field = next((e for e in inputs if e.control_id() in (1148,1001)), inputs[0])
            field.set_edit_text(str(path))
            dialog.child_window(control_id=1).click()
            dialog.wait_not("visible", timeout=10)
            main.set_focus()
        choose("{SPACE}", "选择完整测量记录", record)
        choose("{TAB}{SPACE}", "选择配套词难度表", table)
        choose("{TAB}{SPACE}", "选择先前同一学习者的观看记录", study)
        choose("{TAB}{TAB}{SPACE}", "保存观看校准报告", output)
        deadline = time.monotonic() + 45
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        result = json.loads(output.read_text(encoding="utf-8"))
        assert result["training"]["included"] == 1 and result["trainingMediaHashes"] == ["b"*64]
        assert "科技" in result["estimate"]["domainBias"] and result["frozenAt"] >= 20
        main.close()
        assert process.wait(timeout=25) == 0
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    print("Packaged EXE produced the UTF-8 report through all native file dialogs.")


def check(exe=None):
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory) / "我的 观看资料"
        base.mkdir()
        def save(name, data):
            path = base / name
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return path
        table = save("难度.json", {"version":1, "scale":"natural-log lemma rank",
            "words":{"plume":{"b_initial":7,"b_frequency":7}}})
        digest = sha256(table.read_bytes()).hexdigest()
        record = save("测量记录.json", {"version":1,"model":"3PL word CAT","fits":{"b_initial":{"theta":7,"se":.3}},
            "timing":{"startedAt":5,"completedAt":9},
            "parameters":{"discrimination":2,"thetaByField":{"b_initial":7},"tableSha256":digest}})
        study = save("观看.json", {"version":1,"kind":"word-viewing-study","id":"s1","clip":"c1","domain":"科技",
            "field":"b_initial","tableSha256":digest,"subtitleSha256":"a"*64,"mediaSha256":"b"*64,"started":10,"ended":20,
            "exposures":[{"cue":0,"start":0,"end":5,"word":"plume","at":15,"shown":False,"event":"lookup","pUnknown":.5}]})
        output = base / "冻结参数.json"
        if exe is not None:
            return packaged(exe, record, table, study, output)
        root = tk.Tk()
        form = ui.Calibrator(root)
        root.update()
        try:
            with patch.object(ui.filedialog, "askopenfilename", side_effect=[str(record),str(table)]):
                form.buttons["record"].invoke()
                form.buttons["table"].invoke()
            assert form.save.instate(["disabled"])
            with patch.object(ui.filedialog, "askopenfilenames", return_value=(str(study),)):
                form.buttons["train"].invoke()
            def generate(dest):
                with patch.object(ui.filedialog, "asksaveasfilename", return_value=str(dest)):
                    form.save.invoke()
                deadline = time.monotonic()+30
                while form.future is not None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                assert form.future is None and form.save.instate(["!disabled"])
            generate(output)
            result = json.loads(output.read_text(encoding="utf-8"))
            assert result["training"]["included"] == 1 and result["trainingMediaHashes"] == ["b"*64]
            assert result["estimate"]["domainBias"].keys() == {"科技"} and result["frozenAt"] >= 20
            assert str(output) in form.status.get()
            original = record.read_bytes()
            generate(record)
            assert record.read_bytes() == original and "不能覆盖" in form.status.get()
            original = output.read_bytes()
            form.field.current(1)
            generate(output)
            assert output.read_bytes() == original and "无法生成" in form.status.get()
        finally:
            form.close()
    print("Passed native form selection, real fitting, UTF-8 export and failure recovery.")


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else None)
