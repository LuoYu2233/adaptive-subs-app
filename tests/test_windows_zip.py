import codecs
import contextlib
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("build_windows_package", Path(__file__).resolve().parents[1] / "packaging/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class WindowsZipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "待发布"
        self.files = {"程序/AdaptiveSubs.exe": b"program fixture", "浏览器插件/manifest.json": b'{"manifest_version":3}',
                      "使用说明.txt": "安装说明".encode("utf-8-sig")}
        for name, data in self.files.items():
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.zip = self.root / "AdaptiveSubs-Windows.zip"
        build.write_archive(self.folder, self.zip)
        self.previous = self.zip.read_bytes()

    def assert_previous_survives(self):
        self.assertEqual(self.zip.read_bytes(), self.previous)
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_replaces_previous_zip_and_extracts_unicode_paths(self):
        (self.folder / "使用说明.txt").write_bytes(codecs.BOM_UTF8 + "新版说明\r\n".encode())
        build.write_archive(self.folder, self.zip)
        with zipfile.ZipFile(self.zip) as archive:
            self.assertIsNone(archive.testzip())
            archive.extractall(self.root / "解压")
        out = self.root / "解压" / build.NAME
        self.assertEqual((out / "程序/AdaptiveSubs.exe").read_bytes(), self.files["程序/AdaptiveSubs.exe"])
        self.assertEqual((out / "使用说明.txt").read_text(encoding="utf-8-sig"), "新版说明\n")

    def test_incomplete_package_preserves_previous_zip(self):
        for name, data in self.files.items():
            with self.subTest(member=name):
                path = self.folder / name
                path.unlink()
                with self.assertRaises(ValueError):
                    build.write_archive(self.folder, self.zip)
                self.assert_previous_survives()
                path.write_bytes(data)

    def test_write_verification_and_replace_failures_preserve_previous_zip(self):
        failures = [(zipfile.ZipFile, "write", {"side_effect": OSError("disk full")}),
                    (zipfile.ZipFile, "testzip", {"return_value": "damaged member"}),
                    (Path, "replace", {"side_effect": PermissionError("destination in use")})]
        for owner, method, kwargs in failures:
            with self.subTest(method=method), patch.object(owner, method, **kwargs):
                with self.assertRaises((OSError, zipfile.BadZipFile)):
                    build.write_archive(self.folder, self.zip)
                self.assert_previous_survives()

    def test_build_entry_point_emits_windows_guide_and_preserves_last_good_zip(self):
        # Provide a frozen-program fixture at PyInstaller's output boundary.
        app = self.root / "app"
        for name in ("models/faster-whisper-base.en/model.bin", "data/ranks_en.txt"):
            path = app / name
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture")
        dist = self.root / "build/dist/AdaptiveSubs"
        dist.mkdir(parents=True)
        exe = dist / "AdaptiveSubs.exe"
        exe.write_bytes(b"program fixture")
        extension = self.root / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_bytes(self.files["浏览器插件/manifest.json"])

        release = self.root / "release"
        target = release / f"{build.NAME}.zip"
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.multiple(build, ROOT=self.root, APP=app, BUILD=self.root / "build", RELEASE=release))
            stack.enter_context(patch.dict(sys.modules, {"PyInstaller": object(), "av": object()}))
            stack.enter_context(patch.object(build.subprocess, "run"))
            build.main()
            with zipfile.ZipFile(target) as archive:
                guide = archive.read(f"{build.NAME}/使用说明.txt")
                self.assertEqual(guide, codecs.BOM_UTF8 + build.GUIDE.replace("\n", "\r\n").encode("utf-8"))
            before = target.read_bytes()
            exe.unlink()
            with self.assertRaises(ValueError):
                build.main()
            self.assertEqual(target.read_bytes(), before)

    @unittest.skipUnless(sys.platform == "win32", "native Windows file sharing")
    def test_open_destination_is_preserved(self):
        with self.zip.open("rb"):
            with self.assertRaises(PermissionError):
                build.write_archive(self.folder, self.zip)
        self.assert_previous_survives()


if __name__ == "__main__":
    unittest.main()
