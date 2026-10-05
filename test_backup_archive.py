import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import backup_archive as backup


class BackupArchiveTests(unittest.TestCase):
    def setUp(self):
        self.raw = b"synthetic-file"
        self.payload = {"meta": {"organization_id": 2}, "organization": {"id": 2},
                        "users": [{"id": 3, "role": "planner"}],
                        "cases": [{"id": 8, "organization_id": 2}],
                        "notes": [{"id": 1, "case_id": 8, "organization_id": 2}],
                        "attachments": [{"id": 4, "case_id": 8, "filename": "foto.png",
                                         "storage_path": "org/2/cases/8/foto.png", "size_bytes": len(self.raw)}]}

    def archive(self):
        return backup.build_archive(self.payload, lambda key: self.raw)

    def changed_archive(self, archive, mutate):
        with zipfile.ZipFile(io.BytesIO(archive)) as original:
            entries = {name: original.read(name) for name in original.namelist()}
        mutate(entries)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as changed:
            for name, value in entries.items():
                changed.writestr(name, value)
        return out.getvalue()

    def test_round_trip_restores_exact_data_files_and_private_permissions(self):
        archive = self.archive()
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "restored"
            result = backup.restore_to_new_folder(archive, target)
            self.assertEqual(result["attachments"], 1)
            self.assertEqual((target / "attachments/4/foto.png").read_bytes(), self.raw)
            self.assertEqual(json.loads((target / "backup.json").read_text()), self.payload)
            self.assertEqual((target / "backup.json").stat().st_mode & 0o777, 0o600)

    def test_missing_or_truncated_storage_file_fails_the_whole_backup(self):
        with self.assertRaises(ValueError):
            backup.build_archive(self.payload, lambda key: b"truncated")
        with self.assertRaises(KeyError):
            backup.build_archive(self.payload, lambda key: {}[key])

    def test_cross_organization_and_orphaned_data_are_rejected(self):
        for table, field, value in (("cases", "organization_id", 9), ("notes", "case_id", 99), ("attachments", "case_id", 99)):
            with self.subTest(table=table):
                altered = copy.deepcopy(self.payload)
                altered[table][0][field] = value
                with self.assertRaises(ValueError):
                    backup.build_archive(altered, lambda key: self.raw)

    def test_password_hashes_cannot_enter_the_archive(self):
        self.payload["users"][0]["password_hash"] = "test-only-not-a-real-hash"
        with self.assertRaises(ValueError):
            self.archive()

    def test_corruption_and_missing_file_are_detected_before_writing(self):
        for action in (lambda data: data.__setitem__("attachments/4/foto.png", b"tampered-data"),
                       lambda data: data.pop("attachments/4/foto.png")):
            with self.subTest(action=action):
                archive = self.changed_archive(self.archive(), action)
                with tempfile.TemporaryDirectory() as root:
                    target = Path(root) / "restore"
                    with self.assertRaises(ValueError):
                        backup.restore_to_new_folder(archive, target)
                    self.assertFalse(target.exists())

    def test_path_traversal_is_rejected(self):
        archive = self.changed_archive(self.archive(), lambda data: data.__setitem__("../outside.txt", b"unsafe"))
        with self.assertRaises(ValueError):
            backup.verify_archive(archive)

    def test_existing_recovery_folder_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "restore"
            target.mkdir()
            original = target / "keep.txt"
            original.write_text("keep")
            with self.assertRaises(ValueError):
                backup.restore_to_new_folder(self.archive(), target)
            self.assertEqual(original.read_text(), "keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
