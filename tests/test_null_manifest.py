"""NUL manifests preserve exact paths without relaxing inventory safeguards."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tree_portability.cli import _manifest, main
from tree_portability.core import MAX_INPUT_PATH

REPO = Path(__file__).resolve().parents[1]


class NullManifestTests(unittest.TestCase):
    def invoke(self, *arguments):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPO / "src")
        return subprocess.run(
            [sys.executable, "-m", "tree_portability", *arguments],
            cwd=REPO, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )

    def read(self, content, maximum=100):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "paths.bin"
            manifest.write_bytes(content)
            return _manifest(str(manifest), maximum, null_terminated=True)

    def test_exact_newlines_carriage_returns_unicode_and_directories(self):
        names = ["line\nbreak.txt", "trailing\r\n", "dir\n/", "dir\n/报告😀.txt", "normal.txt"]
        inventory = self.read(("\0".join(names) + "\0").encode("utf-8"))
        self.assertTrue(inventory.complete, inventory.errors)
        self.assertEqual(set(inventory.entries), {name.removesuffix("/") for name in names})
        self.assertEqual(inventory.entries["dir\n"].kind, "directory")
        self.assertFalse(inventory.entries["dir\n"].inferred)
        self.assertEqual(inventory.records, len(names))

    def test_empty_file_and_empty_records(self):
        empty = self.read(b"")
        self.assertTrue(empty.complete)
        self.assertEqual(empty.records, 0)
        inventory = self.read(b"\0a.txt\0\0")
        self.assertTrue(inventory.complete)
        self.assertEqual(inventory.records, 3)
        self.assertEqual(set(inventory.entries), {"a.txt"})

    def test_empty_and_duplicate_records_count_against_cap(self):
        for content in (b"\0\0\0\0", b"a.txt\0" * 4):
            with self.subTest(content=content):
                inventory = self.read(content, maximum=3)
                self.assertFalse(inventory.complete)
                self.assertEqual(inventory.records, 3)
                self.assertEqual(inventory.errors[0]["code"], "record_limit")

    def test_inferred_directories_still_count_against_cap(self):
        inventory = self.read(b"a/b/c.txt\0", maximum=2)
        self.assertFalse(inventory.complete)
        self.assertEqual(inventory.entries, {})
        self.assertEqual(inventory.errors[0]["code"], "inventory_limit")

    def test_unterminated_record_is_not_accepted_as_complete_inventory(self):
        for content in (b"truncated.txt", b"good.txt\0truncated.txt"):
            with self.subTest(content=content):
                inventory = self.read(content)
                self.assertFalse(inventory.complete)
                self.assertNotIn("truncated.txt", inventory.entries)
                self.assertEqual(inventory.errors[0]["code"], "manifest_missing_terminator")
                if content.startswith(b"good.txt\0"):
                    self.assertIn("good.txt", inventory.entries)

    def test_oversized_record_is_not_split_into_fake_entries(self):
        for terminated in (b"", b"\0later.txt\0"):
            with self.subTest(terminated=terminated):
                inventory = self.read(b"good.txt\0" + b"x" * (MAX_INPUT_PATH * 4) + terminated)
                self.assertFalse(inventory.complete)
                self.assertEqual(set(inventory.entries), {"good.txt"})
                self.assertEqual(inventory.errors[0]["code"], "input_path_limit")

    def test_exact_path_limit_including_directory_marker(self):
        for suffix in ("", "/"):
            with self.subTest(suffix=suffix):
                path = "x" * MAX_INPUT_PATH
                accepted = self.read((path + suffix + "\0").encode())
                rejected = self.read((path + "x" + suffix + "\0").encode())
                self.assertTrue(accepted.complete, accepted.errors)
                self.assertEqual(set(accepted.entries), {path})
                self.assertFalse(rejected.complete)
                self.assertEqual(rejected.entries, {})

    def test_records_cross_read_boundaries_without_loss(self):
        names = ["x" * 4095, "报告😀" * 1500, "last\n"]
        inventory = self.read(("\0".join(names) + "\0").encode("utf-8"))
        self.assertTrue(inventory.complete, inventory.errors)
        self.assertEqual(set(inventory.entries), set(names))

    def test_literal_crlf_straddles_chunk_boundary(self):
        for boundary in (4096, 8192):
            with self.subTest(boundary=boundary):
                name = "x" * (boundary - 1) + "\r\n"
                inventory = self.read((name + "\0last.txt\0").encode())
                self.assertTrue(inventory.complete, inventory.errors)
                self.assertEqual(set(inventory.entries), {name, "last.txt"})

    def test_record_limit_stops_an_infinite_nul_stream(self):
        class InfiniteRecords(io.StringIO):
            reads = 0

            def read(self, size=-1):
                if not 0 < size <= MAX_INPUT_PATH + 3:
                    raise AssertionError(f"unbounded read: {size}")
                self.reads += 1
                if self.reads > 1:
                    raise AssertionError("reader continued beyond the record cap")
                return ("a\0" * size)[:size]

        stream = InfiniteRecords()
        with patch("tree_portability.cli.open", return_value=stream):
            inventory = _manifest("synthetic", 3, null_terminated=True)
        self.assertEqual(stream.reads, 1)
        self.assertEqual(inventory.records, 3)
        self.assertEqual(set(inventory.entries), {"a"})
        self.assertFalse(inventory.complete)
        self.assertEqual(inventory.errors[0]["code"], "record_limit")

    def test_reader_never_requests_unbounded_reads(self):
        class BoundedStream(io.StringIO):
            def read(self, size=-1):
                self.assert_bounded(size)
                return super().read(size)

            @staticmethod
            def assert_bounded(size):
                if not 0 < size <= MAX_INPUT_PATH + 3:
                    raise AssertionError(f"unbounded read: {size}")

        with patch("tree_portability.cli.open", return_value=BoundedStream("a\0b\0")):
            inventory = _manifest("synthetic", 100, null_terminated=True)
        self.assertTrue(inventory.complete)
        self.assertEqual(set(inventory.entries), {"a", "b"})

    def test_line_mode_keeps_existing_crlf_and_final_line_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lines = root / "lines.txt"
            nulls = root / "paths.bin"
            lines.write_bytes(b"a.txt\r\nCON.txt\nfolder/b.txt")
            nulls.write_bytes(b"a.txt\0CON.txt\0folder/b.txt\0")
            before = lines.read_bytes(), nulls.read_bytes()
            line_result = self.invoke("--path-list", str(lines), "--json")
            null_result = self.invoke("--path-list", str(nulls), "--null", "--json")
            self.assertEqual(line_result.returncode, 1, line_result.stderr)
            self.assertEqual(null_result.returncode, 1, null_result.stderr)
            self.assertEqual(line_result.stdout, null_result.stdout)
            self.assertEqual((lines.read_bytes(), nulls.read_bytes()), before)

    def test_null_requires_path_list_before_any_source_access(self):
        with patch("tree_portability.cli.scan_directory") as scan:
            with patch("tree_portability.cli.sys.stderr", new_callable=io.StringIO) as error:
                with self.assertRaises(SystemExit) as caught:
                    main(["--root", "synthetic", "--null"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--null requires --path-list", error.getvalue())
        scan.assert_not_called()

    def test_invalid_utf8_and_missing_terminator_return_exit_two(self):
        for content, code in ((b"\xff\0", "manifest_read_failed"), (b"good.txt\0partial", "manifest_missing_terminator")):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                manifest = Path(temporary) / "paths.bin"
                manifest.write_bytes(content)
                result = self.invoke("--path-list", str(manifest), "--null", "--json")
                self.assertEqual(result.returncode, 2, result.stderr)
                parsed = json.loads(result.stdout)
                self.assertFalse(parsed["inventory_complete"])
                self.assertFalse(parsed["plan_complete"])
                self.assertEqual(parsed["errors"][0]["code"], code)

    def test_null_records_keep_existing_unsafe_path_rejections(self):
        inventory = self.read(b"../escape\0/absolute\0./relative\0a//b\0")
        self.assertFalse(inventory.complete)
        self.assertEqual(inventory.entries, {})
        self.assertEqual(len(inventory.errors), 4)
        self.assertTrue(all(error["code"] == "invalid_path" for error in inventory.errors))

    def test_cli_preserves_and_safely_displays_literal_line_endings(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "paths.bin"
            manifest.write_bytes(b"line\nbreak.txt\0trailing\r\n\0")
            result = self.invoke("--path-list", str(manifest), "--null", "--json")
            self.assertEqual(result.returncode, 1, result.stderr)
            parsed = json.loads(result.stdout)
            self.assertTrue(parsed["plan_complete"])
            self.assertEqual({row["source"] for row in parsed["entries"]}, {"line\nbreak.txt", "trailing\r\n"})
            terminal = self.invoke("--path-list", str(manifest), "--null", "--color", "never")
            self.assertEqual(terminal.returncode, 1, terminal.stderr)
            self.assertIn("line\\u000abreak.txt", terminal.stdout)
            self.assertIn("trailing\\u000d\\u000a", terminal.stdout)

    @unittest.skipUnless(shutil.which("git"), "Git is needed for the real producer integration")
    def test_real_git_ls_files_null_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            repo.mkdir()
            names = ["normal.txt", "space name.txt", "报告😀.txt"]
            if os.name != "nt":
                names += ["line\nbreak.txt", "trailing\r\n"]
            for name in names:
                (repo / name).write_text("synthetic\n", encoding="utf-8")
            for args in (("init", "-q"), ("add", "--all")):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
            produced = subprocess.run(["git", "ls-files", "-z"], cwd=repo, check=True, capture_output=True, timeout=30)
            manifest = root / "paths.bin"
            manifest.write_bytes(produced.stdout)
            result = self.invoke("--path-list", str(manifest), "--null", "--json")
            self.assertEqual(result.returncode, 0 if os.name == "nt" else 1, result.stderr)
            parsed = json.loads(result.stdout)
            self.assertTrue(parsed["plan_complete"])
            self.assertEqual({row["source"] for row in parsed["entries"]}, set(names))


if __name__ == "__main__":
    unittest.main()
