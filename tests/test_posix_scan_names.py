"""Filesystem-relative names must not be mistaken for Windows input paths."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tree_portability.core import Inventory, build_report, exit_code, inventory_from_paths, scan_directory


@unittest.skipIf(os.name == "nt", "Names require a POSIX filesystem")
class PosixScanNameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_leading_backslashes_are_literal_names_and_receive_proposals(self):
        names = [r"\report.txt", r"\\server\share", r"C:\report.txt"]
        for name in names:
            (self.root / name).write_bytes(b"synthetic")
        report = build_report(scan_directory(self.root))
        self.assertTrue(report["inventory_complete"], report["errors"])
        self.assertTrue(report["plan_complete"])
        self.assertEqual({row["source"] for row in report["entries"]}, set(names))
        self.assertEqual(exit_code(report), 1)
        for row in report["entries"]:
            self.assertIn("illegal_character", row["issues"])
            self.assertIsNotNone(row["proposed"])
            self.assertNotIn("\\", row["proposed"])
            self.assertNotIn(":", row["proposed"])
            self.assertEqual((self.root / row["source"]).read_bytes(), b"synthetic")

    def test_drive_like_directory_keeps_descendants_and_parent_mapping(self):
        (self.root / "C:" / "nested").mkdir(parents=True)
        (self.root / "C:" / "nested" / "report.txt").write_bytes(b"synthetic")
        report = build_report(scan_directory(self.root))
        self.assertTrue(report["plan_complete"], report["errors"])
        rows = {row["source"]: row for row in report["entries"]}
        self.assertEqual(set(rows), {"C:", "C:/nested", "C:/nested/report.txt"})
        self.assertTrue(all(not row["inferred"] for row in rows.values()))
        self.assertEqual(rows["C:/nested/report.txt"]["proposed"],
                         rows["C:"]["proposed"] + "/nested/report.txt")
        self.assertTrue(report["verification"]["target_aliases_unique"])
        self.assertTrue(report["verification"]["target_lengths_fit"])

    def test_cli_reports_complete_inventory_without_modifying_source(self):
        (self.root / "C:").mkdir()
        source = self.root / "C:" / r"\report.txt"
        source.write_bytes(b"synthetic")
        result = subprocess.run([sys.executable, "-m", "tree_portability", "--root",
                                 str(self.root), "--json"], capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertTrue(report["inventory_complete"])
        self.assertTrue(report["plan_complete"])
        self.assertEqual(len(report["entries"]), 2)
        self.assertEqual(source.read_bytes(), b"synthetic")
        self.assertNotIn(str(self.root), result.stdout)

    def test_drive_like_directory_does_not_bypass_entry_limit(self):
        (self.root / "C:").mkdir()
        (self.root / "C:" / "report.txt").touch()
        inventory = scan_directory(self.root, max_entries=1)
        self.assertFalse(inventory.complete)
        self.assertEqual(set(inventory.entries), {"C:"})
        self.assertIn("record_limit", [error["code"] for error in inventory.errors])

    def test_leading_backslash_symlink_is_inventoried_without_following(self):
        (self.root / "ordinary").mkdir()
        (self.root / "ordinary" / "file.txt").touch()
        (self.root / r"\link").symlink_to("ordinary", target_is_directory=True)
        report = build_report(scan_directory(self.root))
        rows = {row["source"]: row for row in report["entries"]}
        self.assertTrue(report["inventory_complete"], report["errors"])
        self.assertFalse(report["plan_complete"])
        self.assertEqual(rows[r"\link"]["kind"], "symlink")
        self.assertIsNone(rows[r"\link"]["proposed"])
        self.assertNotIn(r"\link/file.txt", rows)


class ManifestBoundaryTests(unittest.TestCase):
    def test_public_add_stays_strict_even_with_scan_source_label(self):
        inventory = Inventory(source_kind="scan")
        for path in (r"\report.txt", "C:/report.txt", "/absolute", "a/../b", "a//b", "a\0b"):
            inventory.add(path, "file")
        self.assertEqual(inventory.entries, {})
        self.assertEqual(len(inventory.errors), 6)

    def test_windows_absolute_paths_stay_rejected_in_both_manifest_modes(self):
        paths = [r"\report.txt", r"\\server\share", "C:/report.txt", r"C:\report.txt"]
        for strip in (True, False):
            with self.subTest(strip_line_endings=strip):
                inventory = inventory_from_paths(paths, strip_line_endings=strip)
                self.assertFalse(inventory.complete)
                self.assertEqual(inventory.entries, {})
                self.assertEqual(len(inventory.errors), len(paths))


if __name__ == "__main__":
    unittest.main()
