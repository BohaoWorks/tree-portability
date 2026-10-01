import hashlib
import itertools
import json
import os
import random
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tree_portability.cli import report_json
from tree_portability.core import (
    MAX_ENTRIES,
    PROFILES,
    Inventory,
    _is_link,
    _name_issues,
    alias,
    build_report,
    exit_code,
    inventory_from_paths,
    lengths,
    scan_directory,
)


def report(paths, **kwargs):
    return build_report(inventory_from_paths(paths), **kwargs)


class PlannerTests(unittest.TestCase):
    def assert_valid_plan(self, result):
        self.assertTrue(result["plan_complete"], result["errors"])
        self.assertTrue(result["verification"]["target_aliases_unique"])
        self.assertTrue(result["verification"]["target_lengths_fit"])
        rows = {row["source"]: row for row in result["entries"]}
        keys = set()
        profile = PROFILES[result["profile"]]
        for row in rows.values():
            target = row["proposed"]
            self.assertIsNotNone(target)
            key = "/".join(alias(part) for part in target.split("/"))
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertFalse(_name_issues(target.rpartition("/")[2], profile))
            metrics = row["proposed_lengths"]
            self.assertLessEqual(metrics["path_utf16_units"], result["limits"]["path_budget"])
            if profile.both_path_units:
                self.assertLessEqual(metrics["path_utf8_bytes"], result["limits"]["path_budget"])
            parent = row["source"].rpartition("/")[0]
            if parent:
                self.assertEqual(target.rpartition("/")[0], rows[parent]["proposed"])

    def test_clean_tree_keeps_names(self):
        result = report(["photos/", "photos/报告😀.jpg", "notes.txt"])
        self.assert_valid_plan(result)
        self.assertEqual(result["mapping"], [])
        self.assertEqual(exit_code(result), 0)

    def test_reserved_devices_and_superscript_digits(self):
        result = report(["CON.txt", "nul.TAR.gz", "COM¹.txt", "LPT9", "CONIN$.log"])
        self.assert_valid_plan(result)
        self.assertTrue(all("reserved_name" in row["issues"] for row in result["entries"]))
        self.assertEqual(exit_code(result), 1)

    def test_ascii_and_fullwidth_colon_stay_distinct(self):
        result = report(["A:B.txt", "A：B.txt", "A_B.txt"])
        self.assert_valid_plan(result)
        rows = {row["source"]: row for row in result["entries"]}
        self.assertIn("encoding_alias_risk", rows["A:B.txt"]["issues"])
        self.assertIn("encoding_alias_risk", rows["A：B.txt"]["issues"])
        self.assertEqual(rows["A：B.txt"]["proposed"], "A：B.txt")
        self.assertEqual(rows["A_B.txt"]["proposed"], "A_B.txt")

    def test_parent_case_collisions_relocate_all_descendants(self):
        result = report(["Foo/readme.md", "foo/README.md", "foo/other.txt"])
        self.assert_valid_plan(result)
        rows = {row["source"]: row for row in result["entries"]}
        self.assertIn("case_collision", rows["foo"]["issues"])
        self.assertIn("target_alias_collision", rows["foo/README.md"]["issues"])
        self.assertNotEqual(rows["foo"]["proposed"], "foo")
        self.assertTrue(rows["foo/other.txt"]["proposed"].startswith(rows["foo"]["proposed"] + "/"))

    def test_file_directory_aliases_share_one_namespace(self):
        result = report(["Data", "data/child.txt", "data./"])
        self.assert_valid_plan(result)
        rows = {row["source"]: row for row in result["entries"]}
        self.assertIn("file_directory_alias", rows["Data"]["issues"])
        self.assertIn("file_directory_alias", rows["data"]["issues"])

    def test_trailing_aliases(self):
        result = report(["report.txt", "report.txt.", "report.txt ", "folder/", "folder./"])
        self.assert_valid_plan(result)
        rows = {row["source"]: row for row in result["entries"]}
        self.assertIn("trailing_alias_collision", rows["report.txt."]["issues"])
        self.assertEqual(rows["report.txt"]["proposed"], "report.txt")

    def test_nfc_parent_and_combining_collisions(self):
        result = report(["café/é.txt", "cafe\u0301/e\u0301.txt", "é.txt", "e\u0301.txt"])
        self.assert_valid_plan(result)
        rows = {row["source"]: row for row in result["entries"]}
        self.assertIn("nfc_collision", rows["cafe\u0301"]["issues"])
        self.assertIn("nfc_collision", rows["é.txt"]["issues"])
        self.assertIn("non_nfc", rows["e\u0301.txt"]["issues"])

    def test_existing_suggested_name_is_reserved(self):
        first = report(["CON.txt"])
        suggestion = first["mapping"][0]["proposed"]
        result = report(["CON.txt", suggestion])
        self.assert_valid_plan(result)
        rows = {row["source"]: row for row in result["entries"]}
        self.assertEqual(rows[suggestion]["proposed"], suggestion)
        self.assertNotEqual(rows["CON.txt"]["proposed"], suggestion)

    def test_hash_suffix_collisions_get_deterministic_counters(self):
        constant_hash = SimpleNamespace(hexdigest=lambda: "a" * 64)
        with patch("tree_portability.core.hashlib.sha256", return_value=constant_hash):
            first = report(["A:B.txt", "A?B.txt", "A_B~aaaaaaaa.txt", "A_B~aaaaaa-1.txt"])
            second = report(["A_B~aaaaaa-1.txt", "A?B.txt", "A_B~aaaaaaaa.txt", "A:B.txt"])
        self.assert_valid_plan(first)
        self.assertEqual(report_json(first), report_json(second))
        self.assertTrue(any("-2" in row["proposed"] for row in first["mapping"]))

    def test_prefix_budget_shortens_parents_before_children(self):
        paths = [
            "Research_Project_2026_Final/Department_of_Computer_Science/报告😀_Final_Submission.pdf",
            "Research_Project_2026_Final/Department_of_Computer_Science/notes.txt",
        ]
        result = report(paths, destination_prefix="D:/Team/Backup", path_budget=50)
        self.assert_valid_plan(result)
        parent = next(row for row in result["entries"] if row["source"] == "Research_Project_2026_Final")
        self.assertNotEqual(parent["source"], parent["proposed"])
        self.assertTrue(any("parent_renamed" in row["reasons"] for row in result["mapping"]))

    def test_deep_but_short_clean_tree_is_not_overreserved(self):
        path = "/".join(["a"] * 70) + "/x"
        result = report([path])
        self.assert_valid_plan(result)
        self.assertEqual(result["mapping"], [])

    def test_utf8_and_utf16_are_different_limits(self):
        name = "😀" * 70 + ".txt"
        portable = report([name])
        windows = report([name], profile_name="windows")
        self.assert_valid_plan(portable)
        self.assert_valid_plan(windows)
        self.assertIn("component_utf8_limit", portable["entries"][0]["issues"])
        self.assertEqual(windows["mapping"], [])
        self.assertEqual(lengths("😀"), (4, 2))
        self.assertEqual(lengths("中文"), (6, 2))

    def test_each_profile_bounds_long_components(self):
        for profile_name in PROFILES:
            with self.subTest(profile=profile_name):
                result = report(["x" * 400 + ".txt"], profile_name=profile_name)
                self.assert_valid_plan(result)
                self.assertTrue(result["mapping"])

    def test_unicode_prefix_is_included_in_both_budgets(self):
        result = report(["报告😀" * 20 + ".txt"], destination_prefix="/备份😀", path_budget=60)
        self.assert_valid_plan(result)
        metrics = result["entries"][0]["proposed_lengths"]
        self.assertGreater(metrics["path_utf8_bytes"], metrics["path_utf16_units"])
        self.assertLessEqual(metrics["path_utf8_bytes"], 60)

    def test_infeasible_budget_is_explicit(self):
        result = report(["CON.txt"], destination_prefix="D:/Backup", path_budget=12)
        self.assertFalse(result["plan_complete"])
        self.assertIsNone(result["entries"][0]["proposed"])
        self.assertIn("budget_unplannable", result["entries"][0]["issues"])
        self.assertEqual(exit_code(result), 2)

    def test_blocked_parent_blocks_children(self):
        result = report(["very_long_parent_name/CON.txt"], path_budget=8)
        self.assertFalse(result["plan_complete"])
        rows = {row["source"]: row for row in result["entries"]}
        self.assertIn("ancestor_unplannable", rows["very_long_parent_name/CON.txt"]["issues"])

    def test_bad_paths_never_become_mapping_entries(self):
        paths = ["/absolute", "../escape", "a/../b", "./a", "a//b", "C:/absolute", "\\\\server\\share", "a\0b", "a//"]
        result = report(paths)
        self.assertFalse(result["inventory_complete"])
        self.assertFalse(result["plan_complete"])
        self.assertEqual(result["entries"], [])
        self.assertEqual(len(result["errors"]), len(paths))

    def test_literal_backslash_is_a_diagnosed_posix_name(self):
        result = report(["dir/a\\b"])
        self.assert_valid_plan(result)
        self.assertIn("illegal_character", result["entries"][1]["issues"])

    def test_source_file_as_parent_is_invalid_in_either_input_order(self):
        for paths in (["item", "item/child"], ["item/child", "item"]):
            result = report(paths)
            self.assertFalse(result["plan_complete"])
            self.assertIn("source_type_conflict", [error["code"] for error in result["errors"]])

    def test_explicit_directory_overrides_inferred_flag(self):
        result = report(["a/file.txt", "a/"])
        self.assert_valid_plan(result)
        self.assertFalse(result["entries"][0]["inferred"])

    def test_surrogates_are_reported_and_json_is_valid(self):
        result = report(["bad\udcff.txt"])
        self.assert_valid_plan(result)
        self.assertIn("invalid_unicode", result["entries"][0]["issues"])
        self.assertEqual(json.loads(report_json(result)), result)

    def test_reproducible_across_input_orders_and_repeated_runs(self):
        paths = ["Foo/a.txt", "foo/A.txt", "CON.txt", "A:B.txt", "A：B.txt", "e\u0301.txt", "é.txt"]
        expected = report_json(report(paths, destination_prefix="D:/Backup"))
        generator = random.Random(2026)
        for _ in range(10):
            generator.shuffle(paths)
            self.assertEqual(expected, report_json(report(paths, destination_prefix="D:/Backup")))

    def test_stable_mapping_can_be_rechecked_as_a_clean_tree(self):
        result = report(["Foo/a", "foo/A", "CON.txt", "e\u0301.txt"])
        targets = [row["proposed"] + ("/" if row["kind"] == "directory" else "") for row in result["entries"]]
        second = report(targets)
        self.assert_valid_plan(second)
        self.assertEqual(second["mapping"], [])
        self.assertEqual(exit_code(second), 0)

    def test_invalid_prefixes_and_budgets(self):
        for prefix in ("D:relative", "D:", "D:/CON", "/a/../b", "//server", "\\\\?\\C:\\x", "/a//b", "/bad\0name"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                report(["a"], destination_prefix=prefix)
        for budget in (0, -1, 32768):
            with self.assertRaises(ValueError):
                report(["a"], path_budget=budget)
        for prefix in ("D:/", "/", "//server/share", "D:\\Backup"):
            self.assert_valid_plan(report(["a"], destination_prefix=prefix))

    def test_many_existing_candidates_cannot_steal_source_names(self):
        constant_hash = SimpleNamespace(hexdigest=lambda: "b" * 64)
        paths = [f"x{char}y.txt" for char in ':?*"<>|']
        paths += ["x_y~bbbbbbbb.txt", "x_y~bbbbbb-1.txt", "x_y~bbbbbb-2.txt"]
        with patch("tree_portability.core.hashlib.sha256", return_value=constant_hash):
            result = report(paths)
        self.assert_valid_plan(result)
        for row in result["entries"]:
            if "~" in row["source"]:
                self.assertEqual(row["source"], row["proposed"])

    def test_100k_inventory_and_report_are_bounded(self):
        consumed = 0

        def paths():
            nonlocal consumed
            for index in range(MAX_ENTRIES + 500):
                consumed += 1
                yield f"file-{index:06}.txt"

        inventory = inventory_from_paths(paths())
        self.assertEqual(len(inventory.entries), MAX_ENTRIES)
        self.assertEqual(consumed, MAX_ENTRIES + 1)
        result = build_report(inventory)
        self.assertFalse(result["plan_complete"])
        self.assertEqual(result["summary"]["entries"], MAX_ENTRIES)
        self.assertTrue(result["verification"]["target_aliases_unique"])
        self.assertEqual(result["summary"]["mapping_entries"], 0)

    def test_inferred_directories_and_duplicate_records_count_against_caps(self):
        inventory = inventory_from_paths(["a/b/c.txt"], max_entries=2)
        self.assertEqual(len(inventory.entries), 0)
        self.assertFalse(inventory.complete)
        inventory = inventory_from_paths(itertools.repeat("a.txt"), max_entries=5)
        self.assertEqual(inventory.records, 5)
        self.assertFalse(inventory.complete)
        self.assertEqual(len(inventory.entries), 1)

    def test_error_details_are_capped(self):
        result = report(["../bad"] * 1000)
        self.assertEqual(result["summary"]["inventory_errors"], 1000)
        self.assertEqual(len(result["errors"]), 100)
        self.assertEqual(result["summary"]["omitted_error_details"], 900)


class ScannerTests(unittest.TestCase):
    def test_scan_is_explicit_read_only_and_has_no_root_path_in_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            root.mkdir()
            (root / "Folder").mkdir()
            (root / "Folder" / "报告😀.txt").write_text("synthetic", encoding="utf-8")
            before = (root / "Folder" / "报告😀.txt").read_bytes()
            result = build_report(scan_directory(root))
            self.assertTrue(result["plan_complete"])
            self.assertNotIn(str(root), report_json(result))
            self.assertEqual((root / "Folder" / "报告😀.txt").read_bytes(), before)
            self.assertEqual([row["source"] for row in result["entries"]], ["Folder", "Folder/报告😀.txt"])

    def test_missing_root_and_file_root_are_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "file"
            file.write_text("synthetic", encoding="utf-8")
            for path in (root / "absent", file):
                self.assertFalse(build_report(scan_directory(path))["plan_complete"])

    def test_permission_failures_are_visible_even_if_other_entries_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "private").mkdir()
            (root / "readable.txt").write_text("synthetic", encoding="utf-8")
            scandir = os.scandir

            def denied(path):
                if Path(path).name == "private":
                    raise PermissionError(13, "synthetic denial")
                return scandir(path)

            with patch("tree_portability.core.os.scandir", side_effect=denied):
                result = build_report(scan_directory(root))
            self.assertFalse(result["inventory_complete"])
            self.assertEqual(result["errors"][0]["code"], "scan_failed")
            self.assertEqual(result["errors"][0]["path"], "private")
            self.assertEqual(result["errors"][0]["detail"], "EACCES")

    def test_root_permission_failure(self):
        with patch("tree_portability.core.Path.lstat", side_effect=PermissionError(13, "synthetic denial")):
            result = build_report(scan_directory("synthetic"))
        self.assertFalse(result["plan_complete"])
        self.assertEqual(result["errors"][0]["detail"], "EACCES")

    def test_symlink_and_junction_metadata_are_detected(self):
        self.assertTrue(_is_link(SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)))
        self.assertTrue(_is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)))
        self.assertFalse(_is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)))

    def test_observed_symlink_is_never_traversed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            target = Path(temporary) / "synthetic-outside"
            root.mkdir()
            target.mkdir()
            (target / "never-read.txt").write_text("synthetic", encoding="utf-8")
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("This Windows account cannot create symlinks; mocked reparse test still runs")
            result = build_report(scan_directory(root))
            self.assertFalse(result["plan_complete"])
            self.assertEqual([row["source"] for row in result["entries"]], ["link"])
            self.assertIn("symlink_skipped", result["entries"][0]["issues"])
            self.assertFalse(build_report(scan_directory(link))["inventory_complete"])

    def test_scan_entry_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(5):
                (root / f"{index}.txt").write_text("synthetic", encoding="utf-8")
            result = build_report(scan_directory(root, max_entries=3))
            self.assertFalse(result["inventory_complete"])
            self.assertEqual(result["summary"]["entries"], 3)


if __name__ == "__main__":
    unittest.main()
