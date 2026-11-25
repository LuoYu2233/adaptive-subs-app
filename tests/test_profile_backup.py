import copy
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import profile_store as profiles


def example():
    return {"format": "adaptive-subs-profile", "version": 1, "profile": {
        "field": "计算机", "identity": "undergrad", "exams": {"cet4": 555, "ielts": 6.5}, "level": 4500,
        "known": ["sense:bank", "phrase:take off"], "unknown": ["forecast"],
        "test": {"date": "2026-11-24", "est": 4600, "bands": [1, .5, 0], "known": ["dock"], "unknown": ["anchor"]}}}


def competing_writer(path, prefix, token, gate, results):
    store = profiles.Store(path)
    original_save = store._save
    def delayed_save(profile):
        time.sleep(.025)
        original_save(profile)
    store._save = delayed_save
    gate.wait(timeout=30)
    try:
        store.restore_backup(example(), token)
        outcome = "saved"
    except profiles.RestoreConflict:
        outcome = "conflict"
    for word in ("one", "two", "three", "four"):
        assert store.mark(prefix + " " + word, "known") is None
    results.put(outcome)


class ProfileBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "用户数据"
        self.store = profiles.Store(self.folder / "profile.json")

    def restore(self, backup):
        preview = self.store.preview_backup(backup)
        return self.store.restore_backup(backup, preview["token"])

    def test_roundtrip_preserves_user_inputs_including_date_and_exact_level(self):
        backup = example()
        first = self.restore(backup)
        self.assertIsNone(first["previous"])
        self.assertEqual(self.store.export_backup(), backup)
        p = self.store.load() | {"api_key": "should-not-export", "future_metadata": {"internal": 1}}
        original = json.dumps(p, ensure_ascii=False, indent=4).encode("utf-8")
        self.store.path.write_bytes(original)
        self.assertEqual(self.store.export_backup(), backup)
        cleared = self.restore(backup | {"profile": {}})
        self.assertEqual(self.store.export_backup()["profile"], {})
        self.assertEqual((self.folder / cleared["previous"]).read_bytes(), original)
        self.assertEqual(self.store.load()["rev"], 2)
        self.assertEqual(backup, example())

    def test_stale_preview_cannot_erase_new_feedback_or_switch_backups(self):
        backup = example()
        self.restore(backup)
        token = self.store.preview_backup(backup)["token"]
        with self.assertRaises(profiles.RestoreConflict):
            self.store.restore_backup(backup | {"profile": {}}, token)
        self.store.mark("new", "unknown")
        before = self.store.path.read_bytes()
        with self.assertRaises(profiles.RestoreConflict):
            self.store.restore_backup(backup, token)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse((self.folder / "profile-backups").exists())

    def test_two_confirmations_for_same_preview_only_commit_once(self):
        backup = example()
        token = self.store.preview_backup(backup)["token"]
        meeting = threading.Barrier(2)
        results = []
        def confirm():
            meeting.wait(timeout=5)
            try:
                self.store.restore_backup(backup, token)
                results.append("saved")
            except profiles.RestoreConflict: results.append("conflict")
        threads = [threading.Thread(target=confirm) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        self.assertCountEqual(results, ["conflict", "saved"])
        self.assertEqual(self.store.load()["rev"], 1)

    def test_independent_processes_serialize_restore_and_preserve_all_marks(self):
        token = self.store.preview_backup(example())["token"]
        ctx = multiprocessing.get_context("spawn")
        gate, results = ctx.Barrier(2), ctx.Queue()
        workers = [ctx.Process(target=competing_writer, args=(self.store.path, prefix, token, gate, results))
                   for prefix in ("left", "right")]
        try:
            for worker in workers: worker.start()
            for worker in workers:
                worker.join(timeout=45)
                self.assertEqual(worker.exitcode, 0)
            self.assertCountEqual([results.get(timeout=3), results.get(timeout=3)], ["saved", "conflict"])
        finally:
            for worker in workers:
                if worker.is_alive(): worker.terminate()
                if worker.pid: worker.join()
            results.close()
        profile = self.store.load()
        self.assertEqual(profile["rev"], 9)
        for prefix in ("left", "right"):
            self.assertTrue(all(prefix + " " + word in profile["known"] for word in ("one", "two", "three", "four")))

    def test_bad_backups_leave_original_untouched(self):
        backup = example()
        self.restore(backup)
        before = self.store.path.read_bytes()
        for path, bad in [
            (["version"], True), (["format"], "other"), (["profile", "token"], "should-not-store"),
            (["profile", "field"], "\ud800"), (["profile", "known"], ["../x"]),
            (["profile", "level"], True), (["profile", "exams", "ielts"], float("inf")),
            (["profile", "test", "bands"], [float("nan")]), (["profile", "test", "date"], "not-a-date")]:
            value = copy.deepcopy(backup)
            obj = value
            for k in path[:-1]: obj = obj[k]
            obj[path[-1]] = bad
            with self.subTest(path=path), self.assertRaises(ValueError): self.restore(value)
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_invalid_local_file_blocks_writes_and_is_recoverable_byte_for_byte(self):
        self.folder.mkdir()
        for original in (b'\xffbroken', b'[]', b'{"known":{}}', b'{"rev":"1"}'):
            self.store.path.write_bytes(original)
            with self.assertRaises(profiles.ProfileUnreadable): self.store.mark("new", "unknown")
            with self.assertRaises(profiles.ProfileUnreadable): self.store.export_backup()
            self.assertEqual(self.store.path.read_bytes(), original)
            result = self.restore(example())
            self.assertEqual((self.folder / result["previous"]).read_bytes(), original)
            self.assertEqual(self.store.export_backup(), example())

    def test_failure_to_copy_or_replace_cannot_destroy_original(self):
        backup = example()
        self.restore(backup)
        before = self.store.path.read_bytes()
        target = self.folder / "profile-backups"
        target.write_bytes(b"busy")
        with self.assertRaises(OSError): self.restore(backup | {"profile": {}})
        self.assertEqual(before, self.store.path.read_bytes())
        target.unlink()
        with patch.object(Path, "replace", side_effect=OSError("destination locked")):
            with self.assertRaises(OSError): self.restore(backup | {"profile": {}})
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertFalse(list(self.folder.glob(".profile-*.tmp")))

    def test_reset_and_form_validation_are_one_write(self):
        self.restore(example())
        before = self.store.path.read_bytes()
        self.assertIsNotNone(self.store.update({"reset_marks": True, "identity": "invalid"}))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertIsNone(self.store.update({"reset_marks": True}))
        self.assertEqual(self.store.load()["known"], [])
        self.assertEqual(self.store.load()["rev"], 2)


if __name__ == "__main__":
    unittest.main()
