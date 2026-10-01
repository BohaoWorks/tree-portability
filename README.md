# tree-portability

**Find names that won't travel. Plan the whole tree before you move it.**

An offline, standard-library Python CLI for checking a directory tree or path list
against conservative Windows, exFAT, and cross-platform naming profiles. Produces
a deterministic, collision-checked rename **plan**, including renamed parents and
the space consumed by your destination prefix.

**It never renames or copies source entries. No network, account, or runtime dependency.**

[中文说明](README.zh-CN.md) · [Example terminal report](examples/demo.txt) ·
[Complete JSON example](examples/demo.json) · [Design and limits](docs/design.md)

## Why a whole-tree plan?

Replacing `:` with `_` is easy. Knowing whether `A_B.txt` already exists, whether
`Foo` and `foo` merge as parents, or whether `D:/Team/Backup` leaves room for every
descendant is the harder part. This tool reserves existing sibling names, allocates
parents before children, and verifies the final namespace and lengths.

It flags `CON.txt`, illegal characters, trailing dots/spaces, UTF-8/UTF-16 length
limits, case/NFC aliases, and file-versus-directory aliases. `A:B.txt` and `A：B.txt`
also receive an encoding-risk warning; the fullwidth spelling is not silently
converted. The tool does not emulate rclone's encoding or integrate with a sync engine.

## Quick start

Python **3.10+**. From a downloaded or cloned checkout:

```sh
python -m pip install .
tree-portability --path-list examples/paths.txt --destination-prefix D:/Team/Backup --path-budget 70
```

No installation required:

```sh
PYTHONPATH=src python -m tree_portability --path-list examples/paths.txt
```

`PYTHONPATH=src` is POSIX-shell syntax; PowerShell users can install the package,
or set `$env:PYTHONPATH = 'src'` before `python -m tree_portability ...`.
The runtime is offline; installation may fetch the setuptools build dependency.
There is no registry release requirement: install this checkout rather than an
unverified same-name package from a registry.

Scan only an explicitly selected folder:

```sh
tree-portability --root ./photos --profile windows --destination-prefix D:/Photos --json
tree-portability --root ./photos --output photos-plan.json
```

The destination prefix is **text only**; no destination is accessed or inventoried.
Use an empty destination, or include all existing destination names in your own
inventory before using a plan. This tool cannot detect unseen destination conflicts.

## What the result means

Example changes from the included synthetic fixture:

```text
CON.txt       -> _CON~09c8cc7e.txt
A:B.txt       -> A_B~1d831b84.txt
Foo/readme.md -> Foo/readme.md
foo/README.md -> foo~2c26b46b/README.md
```

Exact reproducible output, including hashes, is in [examples/demo.txt](examples/demo.txt).
Only changed paths appear in `mapping`; `entries` covers every inventoried node,
including inferred directories. A renamed parent changes all descendant paths.
Mappings are review data, **not an executable sequence of filesystem operations**.

Exit codes:

- **0**: complete plan, no issues or changes
- **1**: complete plan, warnings and/or proposed changes
- **2**: incomplete inventory/plan, invalid options, or output failure

`--json` emits the complete report to stdout. `--output NEW.json` also writes JSON
to a **new** file; it refuses overwrite and output inside a scanned source root.
A complete plan verifies modeled target aliases and lengths, not real-world sync
success. Always check `inventory_complete`, `plan_complete`, and `verification`.
An incomplete report can contain useful partial proposals; don't apply it as a
complete mapping. An unchanged name may still carry an encoding-risk warning.

## Input and profiles

A path list is UTF-8, one **relative slash-separated** path per line. A final `/`
declares a directory. Parents are inferred; blank lines are ignored but count
toward the input cap. Backslashes inside a component are literal characters,
not separators. Absolute paths, `.`/`..`, empty components, NUL, and inconsistent
file/parent declarations are rejected. Use a directory scan for names containing
newlines; the line-based manifest cannot represent those names exactly.

| Profile | Component limits | Default whole-path budget |
| --- | --- | --- |
| `windows` | 255 UTF-16 code units | 259 UTF-16 code units |
| `exfat` | 255 UTF-16 code units | 259 UTF-16 code units |
| `portable` (default) | 255 UTF-8 bytes and 255 UTF-16 units | 240 in both units |

`--path-budget N` overrides the inclusive whole-path budget (1–32767), counting
prefix and `/` separators. These are **conservative interoperability policies**,
not exact NTFS/exFAT/Win32 emulation. exFAT uses a Windows-compatible policy here;
its 259 default is not an inherent exFAT filesystem limit. NFC plus Unicode
casefold intentionally over-reports some aliases. Extended/device paths and
8.3 aliases are unsupported. Long-path-enabled applications may accept more.

## Safety and scope

- Reads directory metadata, not file contents; never follows observed symlinks,
  junctions, or reparse points. A root with a linked ancestor is refused
- Links and special files have no proposal and make the plan incomplete
- Permission and traversal errors are visible; incomplete scans never appear clean
- Scans are snapshots, not atomic. Don't scan a tree being modified by an untrusted
  process; path replacement races are outside this MVP's protection
- Defaults: 100,000 input records/nodes, 128 levels, 16,384 characters per input
  path. `--max-entries` supports 1–1,000,000; memory grows with inventory size
- Generated names require at least nine units. Impossible allocations are explicit;
  this is not a proof that no other shorter naming scheme could work
- No copy engine, conflict resolution UI, content hashing, actual rename, or undo
- Reports contain relative filenames and your supplied destination prefix; review
  them before sharing. Absolute source roots and file contents are omitted

## Development

```sh
python -m pip install .
python -m unittest discover -s tests -v
python examples/make_demo.py --check
```

The CI workflow tests Python 3.10/3.12/3.14 on Linux and Windows, builds and installs
a wheel, and checks the committed demo. See [verification](docs/verification.md)
for the checks actually run in the development environment. CI results are separate
from local verification and are not assumed to have passed.

## Motivation

Public reports illustrate the problem this standalone planner addresses:
[Syncthing #10476](https://github.com/syncthing/syncthing/issues/10476) asks for
shortening plus a mapping log; [#9395](https://github.com/syncthing/syncthing/issues/9395)
reports invalid-character failures; [rclone #9976](https://github.com/rclone/rclone/issues/9976)
reports encoding aliases between distinct names. These are motivation, not
endorsements or claims that this project fixes those upstream issues.

MIT licensed. Contributions with minimal synthetic reproductions are welcome;
please avoid posting private directory listings.
