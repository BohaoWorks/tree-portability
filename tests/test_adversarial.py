"""Synthetic edge cases: no user data or target filesystem required."""
import random
import tempfile
import unittest
from pathlib import Path

from tree_portability.cli import report_json
from tree_portability.core import (
    MAX_DEPTH, MAX_INPUT_PATH, PROFILES,
    _name_issues, alias, build_report, inventory_from_paths, lengths, scan_directory,
)


class AdversarialTests(unittest.TestCase):
    def test_randomized_tree_has_consistent_unique_legal_targets(self):
        rng = random.Random(7112026)
        names = ['Foo', 'foo', 'CON', 'A:B', 'A：B', 'café', 'cafe\u0301', '报告😀', 'tail.']
        for profile in PROFILES:
            paths = [f'{rng.choice(names)}/{rng.choice(names)}-{i}.txt' for i in range(150)]
            first = build_report(inventory_from_paths(paths), profile, 'D:/Backup', 90)
            self.assertTrue(first['plan_complete'])
            keys = set()
            rows = {row['source']: row for row in first['entries']}
            for row in rows.values():
                target = row['proposed']
                key = '/'.join(alias(part) for part in target.split('/'))
                self.assertNotIn(key, keys)
                keys.add(key)
                self.assertFalse(_name_issues(target.rpartition('/')[2], PROFILES[profile]))
                parent = row['source'].rpartition('/')[0]
                if parent:
                    self.assertEqual(target.rpartition('/')[0], rows[parent]['proposed'])
            rng.shuffle(paths)
            self.assertEqual(report_json(first), report_json(build_report(inventory_from_paths(paths), profile, 'D:/Backup', 90)))

    def test_tiny_budget_is_not_a_false_success_for_case_collision(self):
        report = build_report(inventory_from_paths(['A', 'a']), path_budget=1)
        self.assertFalse(report['plan_complete'])
        self.assertEqual(report['summary']['blocked_entries'], 1)

    def test_budget_boundary_counts_prefix_and_separator(self):
        inventory = inventory_from_paths(['a'])
        self.assertTrue(build_report(inventory, destination_prefix='D:/', path_budget=4)['plan_complete'])
        self.assertFalse(build_report(inventory, destination_prefix='D:/', path_budget=3)['plan_complete'])
        self.assertTrue(build_report(inventory, destination_prefix='D:/x', path_budget=6)['plan_complete'])
        self.assertFalse(build_report(inventory, destination_prefix='D:/x', path_budget=5)['plan_complete'])

    def test_combining_clip_remains_legal_and_within_both_budgets(self):
        name = 'x\u0327\u0301' * 200 + '.txt'
        report = build_report(inventory_from_paths([name]), path_budget=42)
        self.assertTrue(report['plan_complete'])
        target = report['entries'][0]['proposed']
        self.assertFalse(_name_issues(target, PROFILES['portable']))
        self.assertLessEqual(lengths(target)[0], 42)
        self.assertLessEqual(lengths(target)[1], 42)

    def test_depth_and_character_bounds_are_diagnostics(self):
        for path in ('/'.join(['a'] * (MAX_DEPTH + 1)), 'a' * (MAX_INPUT_PATH + 1)):
            result = build_report(inventory_from_paths([path]))
            self.assertFalse(result['inventory_complete'])
            self.assertEqual(result['entries'], [])
            self.assertEqual(result['errors'][0]['code'], 'input_path_limit')

    def test_regular_file_does_not_silently_become_a_directory(self):
        for paths in (['leaf', 'leaf/a'], ['leaf/a', 'leaf']):
            result = build_report(inventory_from_paths(paths))
            self.assertFalse(result['inventory_complete'])
            self.assertFalse(result['plan_complete'])

    def test_root_with_symlink_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'real' / 'nested').mkdir(parents=True)
            try:
                (base / 'link').symlink_to(base / 'real', target_is_directory=True)
            except OSError:
                self.skipTest('Symlink creation unavailable')
            result = build_report(scan_directory(base / 'link' / 'nested'))
            self.assertFalse(result['inventory_complete'])
            self.assertEqual(result['errors'][0]['code'], 'root_link')

    def test_fifo_is_skipped_without_reading_or_blocking(self):
        import os
        if not hasattr(os, 'mkfifo'):
            self.skipTest('POSIX FIFO only')
        with tempfile.TemporaryDirectory() as temp:
            os.mkfifo(Path(temp) / 'pipe')
            result = build_report(scan_directory(temp))
            self.assertFalse(result['plan_complete'])
            self.assertEqual(result['entries'][0]['issues'], ['special_skipped'])

    def test_manifest_limit_stops_an_infinite_duplicate_generator(self):
        def paths():
            while True:
                yield 'same.txt'
        result = build_report(inventory_from_paths(paths(), max_entries=10))
        self.assertFalse(result['inventory_complete'])
        self.assertEqual(result['summary']['records_read'], 10)
        self.assertEqual(result['summary']['entries'], 1)

    def test_scan_filename_with_newline_is_not_split(self):
        import os
        if os.name == 'nt':
            self.skipTest('Windows prohibits newline names')
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'one\ntwo.txt').write_bytes(b'synthetic')
            result = build_report(scan_directory(temp))
            self.assertTrue(result['plan_complete'])
            self.assertEqual(len(result['entries']), 1)
            self.assertIn('illegal_character', result['entries'][0]['issues'])
