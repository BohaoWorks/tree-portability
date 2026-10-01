"""Regenerate or check the committed, synthetic-only demo. Never creates a source tree."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tree_portability.cli import render_report, report_json
from tree_portability.core import build_report, inventory_from_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths = (ROOT / "examples/paths.txt").read_text(encoding="utf-8").splitlines()
    report = build_report(inventory_from_paths(paths), destination_prefix="D:/Team/Backup", path_budget=70)
    assert report["plan_complete"]
    outputs = {"demo.json": report_json(report), "demo.txt": render_report(report, preview=30)}
    for name, text in outputs.items():
        path = ROOT / "examples" / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                raise SystemExit(f"Stale demo: {name}; run python examples/make_demo.py")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
    print("Synthetic demo verified" if args.check else "Synthetic demo regenerated")


if __name__ == "__main__":
    main()
