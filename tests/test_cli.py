import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tree_portability.cli import _display, _manifest, main, render_report
from tree_portability.core import MAX_INPUT_PATH, build_report, inventory_from_paths

REPO = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def invoke(self, *arguments):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPO / "src")
        return subprocess.run(
            [sys.executable, "-m", "tree_portability", *arguments],
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def test_version_and_help(self):
        result = self.invoke("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("0.1.0", result.stdout)
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Never renames or copies", result.stdout)

    def test_explicit_source_is_required(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn("required", result.stderr)

    def test_manifest_json_end_to_end_and_repeatability(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "paths.txt"
            manifest.write_text("CON.txt\nFoo/a\nfoo/A\n报告😀.txt\nA:B\nA：B\n", encoding="utf-8")
            args = ("--path-list", str(manifest), "--destination-prefix", "D:/Backup", "--json")
            first = self.invoke(*args)
            second = self.invoke(*args)
            self.assertEqual(first.returncode, 1, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            parsed = json.loads(first.stdout)
            self.assertTrue(parsed["plan_complete"])
            self.assertEqual(parsed["destination_prefix"], "D:/Backup")
            self.assertNotIn(temporary.replace("\\", "\\\\"), first.stdout)

    def test_output_file_is_exclusive_and_source_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "paths.txt"
            manifest.write_text("CON.txt\n", encoding="utf-8")
            before = manifest.read_bytes()
            output = root / "report.json"
            result = self.invoke("--path-list", str(manifest), "--output", str(output))
            self.assertEqual(result.returncode, 1, result.stderr)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)["mode"], "plan-only")
            self.assertIn("PLAN ONLY", result.stdout)
            again = self.invoke("--path-list", str(manifest), "--output", str(output))
            self.assertEqual(again.returncode, 2)
            self.assertEqual(saved, output.read_bytes())
            self.assertEqual(before, manifest.read_bytes())
            overwrite_source = self.invoke("--path-list", str(manifest), "--output", str(manifest))
            self.assertEqual(overwrite_source.returncode, 2)
            self.assertEqual(before, manifest.read_bytes())

    def test_output_under_source_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            root.mkdir()
            (root / "a.txt").write_text("synthetic", encoding="utf-8")
            output = root / "report.json"
            result = self.invoke("--root", str(root), "--output", str(output))
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the source root", result.stderr)
            self.assertFalse(output.exists())

    def test_scan_end_to_end_never_modifies_synthetic_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            root.mkdir()
            (root / "报告😀.txt").write_text("synthetic\n", encoding="utf-8")
            (root / "folder").mkdir()
            (root / "folder" / "notes.txt").write_text("synthetic\n", encoding="utf-8")
            before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            result = self.invoke("--root", str(root), "--profile", "windows", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["plan_complete"])
            after = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_missing_manifest_and_invalid_utf8_return_json_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "absent"
            bad = Path(temporary) / "bad.txt"
            bad.write_bytes(b"\xff\xfe")
            for path in (missing, bad):
                result = self.invoke("--path-list", str(path), "--json")
                self.assertEqual(result.returncode, 2)
                parsed = json.loads(result.stdout)
                self.assertFalse(parsed["inventory_complete"])
                self.assertEqual(parsed["errors"][0]["code"], "manifest_read_failed")

    def test_manifest_read_permission_failure(self):
        with patch("tree_portability.cli.open", side_effect=PermissionError(13, "synthetic")):
            with patch("tree_portability.cli.sys.stdout") as output:
                output.isatty.return_value = False
                code = main(["--path-list", "synthetic.txt", "--json"])
                data = json.loads(output.write.call_args[0][0])
        self.assertEqual(code, 2)
        self.assertEqual(data["errors"][0]["detail"], "PermissionError")

    def test_oversized_manifest_record_cannot_be_split_into_fake_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "oversized.txt"
            manifest.write_text("x" * (MAX_INPUT_PATH * 4) + "\nlegitimate.txt\n", encoding="utf-8")
            inventory = _manifest(str(manifest), 100)
            self.assertFalse(inventory.complete)
            self.assertEqual(inventory.entries, {})
            self.assertEqual(inventory.errors[0]["code"], "input_path_limit")

    def test_bad_paths_and_budget_fail_with_exit_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "paths.txt"
            manifest.write_text("../escape\nCON.txt\n", encoding="utf-8")
            result = self.invoke("--path-list", str(manifest), "--json")
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)["plan_complete"])
            invalid = self.invoke("--path-list", str(manifest), "--path-budget", "0")
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("path budget", invalid.stderr)

    def test_terminal_controls_are_escaped(self):
        self.assertEqual(_display("\x1b[2Jbad\tname"), "\\u001b[2Jbad\\u0009name")
        self.assertIn("\\u202e", _display("name\u202e"))
        parsed = build_report(inventory_from_paths(["a\x1b[31m.txt"]))
        output = render_report(parsed)
        self.assertNotIn("\x1b", output)
        self.assertIn("\\u001b", output)

    def test_json_and_terminal_preview_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "paths.txt"
            manifest.write_text("CON.txt\nNUL.txt\n", encoding="utf-8")
            result = self.invoke("--path-list", str(manifest), "--preview", "1", "--color", "always")
            self.assertEqual(result.returncode, 1)
            self.assertIn("\x1b[1;36m", result.stdout)
            self.assertIn("1 more", result.stdout)
            invalid = self.invoke("--path-list", str(manifest), "--preview", "-1")
            self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
