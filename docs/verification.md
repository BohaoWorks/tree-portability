# Verification record

Local verification: **2026-10-01, cloud Linux, CPython 3.12**.
Synthetic inputs only; no user-computer operations, real directory inventories,
network services, registry publication, or source-file renames/copies were used.

## NUL manifest update, 2026-10-18

Verified locally on cloud Linux with CPython 3.12.14:

- **74 unittest cases passed**, including 17 new NUL-manifest regression cases
- Literal and trailing CR/LF, Unicode, explicit directories, empty records,
  truncated final records, invalid UTF-8, record/node/path caps, chunk boundaries,
  an infinite synthetic input stream, and rejection of `--root --null`
- A real temporary Git index produced `git ls-files -z` input; exact names were
  retained, including newline-containing names on this Linux run
- Equivalent line/NUL inventories produced byte-identical JSON; the committed
  demo remained byte-for-byte unchanged
- Wheel built without runtime dependencies, installed in a clean virtual
  environment, and the 74-case suite and demo passed again
- The installed console script was run outside the checkout against synthetic
  CR/LF names and an unterminated record; expected exit codes 1 and 2 were verified
- Independent code review found no blocking issues; 2,000 additional deterministic
  randomized differential cases passed before the final regression-suite run

These are local results. The existing Linux/Windows and Python 3.10/3.12/3.14
workflow must be checked for the exact published commit; earlier CI is not evidence
for this update. No actual destination transfer or source rename was performed.

## Executed

- **57 unittest cases passed**, with no skips on Linux. Core planners, real scanner
  fixtures, mocked permission failures, and subprocess CLI tests are covered
- Full-tree case/NFC aliases, parent propagation, file/directory aliases, trailing
  aliases, reserved devices, UTF-8/UTF-16 limits, and Unicode prefix accounting
- Preexisting proposed names, forced hash collisions and counter collisions,
  repeatability under input shuffles, and 450 randomized entries across profiles
- Impossible budgets, descendant reservations, malicious/oversized paths,
  inconsistent source types, bounded errors and infinite duplicate iterators
- Actual directory symlinks and a linked root ancestor refused; POSIX FIFO skipped
  without reading or blocking; newline-containing filename treated as one node
- End-to-end JSON, exit codes, terminal control escaping, exclusive report output,
  rejection of output inside a scanned source, and unchanged synthetic file contents
- Committed JSON and terminal demo regenerated and verified byte-for-byte
- Source compilation, wheel build, source distribution build, clean-venv install,
  installed `tree-portability --version`, and installed manifest/scan smoke tests
  from `/tmp`, outside the checkout
- A separate **100,000-file** flat inventory produced a complete unchanged plan in
  **3.331 seconds**, peak RSS **239,504 KiB**, on this cloud run. This is a single
  synthetic observation, not a performance guarantee; deeper or conflicting trees
  can take more time and memory

The inherited test that required a root parent to change under a 70-unit budget
was corrected to a 50-unit budget. At 70, preserving that root was valid and a
lower parent was shortened correctly. This was a fixture expectation correction,
not evidence of a broken allocator.

## Hosted verification

On 2026-10-02, the [2026-10-01 Actions run](https://github.com/BohaoWorks/tree-portability/actions/runs/36834990251)
was verified successful for commit `2846ff779253a5ad7875d9e3666eb4df6b75d82d`.
All six Linux/Windows and Python 3.10/3.12/3.14 jobs passed. This result applies
only to that commit; later commits need their own checks.

No real Windows/exFAT destination transfer was verified. Windows may skip real symlink creation if account permissions deny
it; reparse-point metadata recognition is also tested with synthetic metadata.
The FIFO and newline-filename tests intentionally skip on Windows.

## Limitations retained deliberately

This is a conservative model, not filesystem conformance certification. Scanner
path replacement races, Unicode grapheme preservation, destination contents,
8.3 aliases, sync-engine encodings and actual rename execution are outside scope.
Review the [design](design.md) and test a disposable copy with your actual transfer
workflow before a migration. An incomplete plan is never a complete apply map.
