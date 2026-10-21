"""Explicit roots must validate every component before parent traversal."""
import io
import json
import ntpath
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

from tree_portability.cli import main, report_json
from tree_portability.core import build_report, exit_code, scan_directory


class RootResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.local = self.base / "local"
        self.selected = self.local / "selected"
        self.selected.mkdir(parents=True)
        (self.selected / "local.txt").write_bytes(b"synthetic local")
        (self.local / "normal" / "child").mkdir(parents=True)
        (self.local / "regular_file").write_bytes(b"synthetic file")
        self.external = self.base / "external"
        (self.external / "nested").mkdir(parents=True)
        (self.external / "selected").mkdir()
        (self.external / "selected" / "external.txt").write_bytes(b"synthetic external")

    def linked_root(self):
        link = self.local / "link"
        try:
            link.symlink_to(self.external / "nested", target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable; deterministic reparse tests still run")
        return link / ".." / "selected"

    def root_forms(self, root):
        # relpath(root) would itself erase the component under test.
        relative = Path(os.path.relpath(self.local)) / root.relative_to(self.local)
        return (root, relative)

    def assert_refused_without_scanning(self, root, code, detail=None):
        with patch("tree_portability.core.os.scandir") as scan:
            report = build_report(scan_directory(root))
        self.assertFalse(report["inventory_complete"])
        self.assertFalse(report["plan_complete"])
        self.assertEqual(exit_code(report), 2)
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["errors"][0]["code"], code)
        if detail:
            self.assertEqual(report["errors"][0]["detail"], detail)
        scan.assert_not_called()
        self.assertNotIn(str(self.base), report_json(report))

    def test_symlink_before_parent_is_refused_for_absolute_and_relative_roots(self):
        for root in self.root_forms(self.linked_root()):
            with self.subTest(root=root):
                self.assert_refused_without_scanning(root, "root_link")

    def test_missing_before_parent_is_refused_for_absolute_and_relative_roots(self):
        for root in self.root_forms(self.local / "missing" / ".." / "selected"):
            with self.subTest(root=root):
                self.assert_refused_without_scanning(root, "scan_failed", "ENOENT")

    def test_file_before_parent_is_refused_for_absolute_and_relative_roots(self):
        for root in self.root_forms(self.local / "regular_file" / ".." / "selected"):
            with self.subTest(root=root):
                self.assert_refused_without_scanning(root, "scan_failed", "ENOTDIR")

    def test_reparse_before_parent_is_checked_before_platform_normalization(self):
        real_lstat = Path.lstat
        observed = []
        link = self.local / "junction"
        link.mkdir()

        def windows_like_lstat(path, *args, **kwargs):
            # Windows may normalize '..' before lstat. Every checked prefix must
            # therefore be parent-free, and the junction must be seen separately.
            observed.append(path)
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(Path(os.path.normpath(path)), *args, **kwargs)

        for root in self.root_forms(link / ".." / "selected"):
            with self.subTest(root=root), patch.object(Path, "lstat", windows_like_lstat):
                self.assert_refused_without_scanning(root, "root_link")
        self.assertIn(link, observed)
        self.assertTrue(all(".." not in path.parts for path in observed))

    def test_windows_anchored_and_drive_relative_roots_preserve_reparse_prefixes(self):
        observed = []

        class WindowsPath(PureWindowsPath):
            def lstat(self):
                observed.append(self)
                return SimpleNamespace(st_mode=stat.S_IFDIR,
                                       st_file_attributes=0x400 if self.name == "junction" else 0)

        def windows_abspath(path):
            path = str(path)
            if path == "C:":
                return "C:\\work"
            return ntpath.normpath(ntpath.join("C:\\work", path))

        for root in ("C:/local/junction/../selected", "C:local/junction/../selected",
                     "/local/junction/../selected", "local/junction/../selected"):
            with self.subTest(root=root):
                with patch("tree_portability.core.Path", WindowsPath):
                    with patch("tree_portability.core.os.path.abspath", windows_abspath):
                        self.assert_refused_without_scanning(root, "root_link")
        self.assertTrue(all(".." not in path.parts for path in observed))

    def test_windows_namespace_parent_traversal_is_refused(self):
        class WindowsPath(PureWindowsPath):
            def lstat(self):
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)

            def resolve(self, strict=False):
                return self

        roots = [r"\\?\C:\local\normal\..\selected",
                 r"\\.\C:\local\normal\..\selected",
                 r"\\?\UNC\server\share\normal\..\selected"]
        with patch("tree_portability.core.Path", WindowsPath):
            for root in roots:
                with self.subTest(root=root):
                    self.assert_refused_without_scanning(root, "scan_failed", "EINVAL")
        if os.name == "nt":
            for prefix in ("\\\\?\\", "\\\\.\\"):
                self.assert_refused_without_scanning(
                    prefix + str(self.local / "normal" / ".." / "selected"), "scan_failed", "EINVAL")

    def test_windows_drive_components_cannot_reset_the_root(self):
        class WindowsPath(PureWindowsPath):
            def lstat(self):
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)

            def resolve(self, strict=False):
                return self

        with patch("tree_portability.core.Path", WindowsPath):
            with patch("tree_portability.core.os.path.abspath", return_value="C:/work"):
                for root in ("C:/local/D:/selected", "C:/local/C:/selected",
                             "C:local/D:/selected", "local/D:/selected", "/local/D:/selected",
                             "C:/local/D:other/selected"):
                    with self.subTest(root=root):
                        self.assert_refused_without_scanning(root, "scan_failed", "EINVAL")
        if os.name == "nt":
            for drive in (self.local.drive, "Z:" if self.local.drive != "Z:" else "Y:"):
                self.assert_refused_without_scanning(
                    str(self.local) + "/" + drive + "/selected", "scan_failed", "EINVAL")

    def test_plain_and_existing_parent_traversal_select_the_same_tree(self):
        roots = [self.selected, self.local / "normal" / ".." / "selected",
                 self.local / "normal" / "child" / ".." / ".." / "selected"]
        expected = report_json(build_report(scan_directory(self.selected)))
        for absolute in roots:
            for root in self.root_forms(absolute):
                with self.subTest(root=root):
                    report = build_report(scan_directory(root))
                    self.assertTrue(report["plan_complete"])
                    self.assertEqual([row["source"] for row in report["entries"]], ["local.txt"])
                    self.assertEqual(report_json(report), expected)

    def test_file_root_preserves_not_a_directory_error(self):
        self.assert_refused_without_scanning(self.local / "regular_file", "not_a_directory")

    def invoke(self, root, output=None):
        args = [sys.executable, "-m", "tree_portability", "--root", str(root), "--json"]
        if output is not None:
            args.extend(["--output", str(output)])
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_cli_link_parent_never_returns_complete_for_the_wrong_tree(self):
        root = self.linked_root()
        for path in self.root_forms(root):
            with self.subTest(root=path):
                result = self.invoke(path)
                self.assertEqual(result.returncode, 2, result.stderr)
                report = json.loads(result.stdout)
                self.assertFalse(report["inventory_complete"])
                self.assertEqual(report["entries"], [])
                self.assertEqual(report["errors"][0]["code"], "root_link")

    def test_cli_missing_and_file_parent_produce_diagnostics_without_traceback(self):
        for component in ("missing", "regular_file"):
            with self.subTest(component=component):
                result = self.invoke(self.local / component / ".." / "selected")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertFalse(report["inventory_complete"])
                self.assertFalse(report["plan_complete"])
                self.assertEqual(report["entries"], [])

    def test_cli_output_inside_validated_parent_traversal_is_rejected(self):
        output = self.selected / "report.json"
        for root in self.root_forms(self.local / "normal" / ".." / "selected"):
            with self.subTest(root=root):
                result = self.invoke(root, output)
                self.assertEqual(result.returncode, 2)
                self.assertIn("outside the source root", result.stderr)
                self.assertFalse(output.exists())

    @unittest.skipIf(os.name == "nt", "POSIX double-slash root alias")
    def test_cli_output_inside_double_slash_root_is_rejected(self):
        root = Path("/" + str(self.selected))
        output = self.selected / "double-slash.json"
        result = self.invoke(root, output)
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside the source root", result.stderr)
        self.assertFalse(output.exists())

    def test_cli_invalid_link_parent_can_only_write_an_incomplete_report(self):
        root = self.linked_root()
        output = self.selected / "report.json"
        result = self.invoke(root, output)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["plan_complete"])
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["errors"][0]["code"], "root_link")
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
        self.assertEqual((self.selected / "local.txt").read_bytes(), b"synthetic local")

    def test_cli_output_guard_uses_scanned_root_not_a_second_root_resolution(self):
        root = self.local / "normal" / ".." / "selected"
        output = self.selected / "guarded.json"
        real_resolve = Path.resolve

        def no_second_root_resolution(path, *args, **kwargs):
            if path == root:
                self.fail("The output guard must use the root validated by the scanner")
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", no_second_root_resolution):
            with patch("tree_portability.cli.sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(main(["--root", str(root), "--output", str(output)]), 2)
        self.assertIn("outside the source root", stderr.getvalue())
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
