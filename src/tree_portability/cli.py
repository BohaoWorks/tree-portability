"""CLI: read metadata or a manifest and print inert proposals."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

from . import __version__
from .core import (
    MAX_ENTRIES,
    MAX_INPUT_PATH,
    PROFILES,
    Inventory,
    build_report,
    exit_code,
    inventory_from_paths,
    scan_directory,
)


def report_json(report: dict) -> str:
    # ASCII escapes keep lone-surrogate POSIX names representable in valid JSON.
    return json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _display(text: str, limit: int = 100) -> str:
    # Source names are untrusted terminal input: escape controls, including ANSI.
    escaped = "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) in ("Cc", "Cf", "Cs") else char
        for char in text
    )
    return escaped if len(escaped) <= limit else escaped[:limit - 3] + "..."


def render_report(report: dict, *, preview: int = 12, color: bool = False) -> str:
    title = "TREE PORTABILITY  /  PLAN ONLY"
    if color:
        title = f"\033[1;36m{title}\033[0m"
    summary = report["summary"]
    limits = report["limits"]
    units = "UTF-8 bytes + UTF-16 units" if len(limits["path_budget_units"]) == 2 else "UTF-16 units"
    lines = [
        title,
        "=" * 66,
        f"Target   : {report['profile']}  |  path budget {limits['path_budget']} {units}",
        f"Prefix   : {_display(report['destination_prefix'] or '(relative target)')}",
        f"Inventory: {summary['entries']} entries  |  {summary['entries_with_issues']} with issues",
        f"Mapping  : {summary['mapping_entries']} changes  |  {summary['blocked_entries']} blocked",
        f"Status   : {'COMPLETE' if report['plan_complete'] else 'INCOMPLETE'}",
    ]
    for error in report["errors"][:preview]:
        lines.append(f"  ! {error['code']}: {_display(error['path'])} ({error['detail']})")
    rows = [row for row in report["entries"] if row["issues"] or (row["proposed"] is not None and row["source"] != row["proposed"])]
    if rows:
        lines.append("-" * 66)
    for row in rows[:preview]:
        lines.append(f"  {_display(row['source'])}")
        lines.append(f"    -> {_display(row['proposed']) if row['proposed'] is not None else '[no proposal]'}")
        if row["issues"]:
            lines.append("       " + ", ".join(row["issues"]))
        original = row["original_lengths"]
        proposed = row["proposed_lengths"]
        before = f"{original['path_utf8_bytes']} B / {original['path_utf16_units']} u16"
        after = f"{proposed['path_utf8_bytes']} B / {proposed['path_utf16_units']} u16" if proposed else "blocked"
        lines.append(f"       full path: {before} -> {after}")
    if len(rows) > preview:
        lines.append(f"  ... {len(rows) - preview} more; use --json for the full report.")
    lines += ["=" * 66, "No source entries renamed or copied. Review the mapping before use."]
    if not report["plan_complete"]:
        lines.append("Incomplete inventory or plan: do not treat these proposals as a complete mapping.")
    return "\n".join(lines) + "\n"


def _manifest(path: str, maximum: int) -> Inventory:
    """Stream a manifest, bounding each line before handing it to the parser."""
    inventory = None
    with open(path, encoding="utf-8", newline="") as handle:
        oversized = False

        def lines():
            nonlocal oversized
            while True:
                line = handle.readline(MAX_INPUT_PATH + 3)
                if not line:
                    break
                if len(line.rstrip("\r\n")) > MAX_INPUT_PATH + 1:
                    oversized = True
                    break
                yield line

        inventory = inventory_from_paths(lines(), maximum)
        if oversized:
            inventory.error("input_path_limit", "<oversized-record>", "Manifest record exceeds the input path limit")
    return inventory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline directory-tree portability checks and deterministic rename PLANS. Never renames or copies."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root", help="explicit directory to scan; observed links and reparse points are never traversed")
    source.add_argument("--path-list", help="UTF-8 manifest: relative slash-separated paths, directories end in /")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="portable")
    parser.add_argument("--destination-prefix", default="", help="fixed target prefix as text; never accessed")
    parser.add_argument("--path-budget", type=int, help="inclusive full-path limit including prefix and separators")
    parser.add_argument("--max-entries", type=int, default=MAX_ENTRIES, help="inventory/record cap (default: 100000)")
    parser.add_argument("--json", action="store_true", help="emit the complete JSON report to stdout")
    parser.add_argument("--output", help="write JSON to a NEW file; existing files are never overwritten")
    parser.add_argument("--preview", type=int, default=12, help="terminal preview rows (default: 12)")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.preview < 0 or args.preview > 1000:
            raise ValueError("preview must be between 0 and 1000")
        # Validate configuration before touching the explicitly supplied source.
        build_report(Inventory(max_entries=args.max_entries), args.profile, args.destination_prefix, args.path_budget)
        if args.output and args.root:
            root = Path(args.root).resolve()
            output = Path(args.output).resolve()
            if output == root or root in output.parents:
                raise ValueError("output must be outside the source root")
        try:
            inventory = scan_directory(args.root, args.max_entries) if args.root else _manifest(args.path_list, args.max_entries)
        except (OSError, UnicodeError) as exc:
            inventory = Inventory(max_entries=args.max_entries)
            code = type(exc).__name__
            inventory.error("manifest_read_failed", ".", code)
        report = build_report(inventory, args.profile, args.destination_prefix, args.path_budget)
        if args.output:
            # 'x' prevents accidental source/list/report overwrite; no parent dirs created.
            with open(args.output, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(report_json(report))
        use_color = args.color == "always" or (
            args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        )
        output = report_json(report) if args.json else render_report(report, preview=args.preview, color=use_color)
        sys.stdout.write(output)
        return exit_code(report)
    except (OSError, ValueError, UnicodeError) as exc:
        # File paths can contain control codes; avoid printing raw OS exceptions.
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        sys.stderr.write(f"tree-portability: {_display(detail, 240)}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
