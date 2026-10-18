# Design and report contract

## Pipeline

1. Read an explicit directory's metadata or a bounded UTF-8 path list. Infer
   missing parent directories. Reject inconsistent source types and unsafe paths.
2. Sort by depth and source spelling. Build a complete source tree, not a flat
   list of basenames. Compute conservative aliases using NFC and casefold after
   trimming terminal dots/spaces. Report file/directory and parent aliases.
3. Compute a bottom-up minimum reserve for descendants. Valid short names can use
   their original length; generated names reserve nine characters. Subtract the
   destination prefix, separators, and descendant reserve from each node's budget.
4. Allocate top-down. Preserve fitting legal names when possible. Otherwise use a
   safe, clipped stem plus `~` and eight SHA-256-derived characters. Short extensions
   are kept when space permits. Counters disambiguate hash/proposal collisions.
   Reserve all original sibling aliases before allocating any generated names.
5. Propagate renamed parent paths to descendants. Verify all non-null targets for
   unique aliases, legal components, and full-path budgets. Return incomplete if
   any inventory error, skipped node, or failed allocation remains.

For an identical, valid complete inventory and configuration, mappings and JSON
are deterministic regardless of input order. Truncated or conflicting inventories
may retain different partial records depending on enumeration order, and must not
be treated as deterministic complete plans. Changing the inventory can change a
proposal. This tool does not claim to minimize the number of renames or optimize
readability globally. A nine-character reserve can leave a very tight budget
unplannable even when a different naming scheme would fit.

## Encoding risk

The report's `encoding_alias_risk` flags names that would alias if the fullwidth
versions of Windows-forbidden punctuation were folded back to ASCII. It is a
warning about a possible transfer encoding, not a model of any particular sync
client. Distinct fullwidth names are preserved if legal and within budget.

## Unicode and limits

UTF-8 lengths count bytes; UTF-16 lengths count code units, so an emoji typically
uses four bytes and two units. Proposed names are NFC. Clipping occurs at Unicode
code-point boundaries and drops terminal combining marks; it is not a complete
Unicode grapheme-cluster algorithm (emoji ZWJ sequences may be shortened).
Casefold is conservative and not identical to every OS/filesystem collation.

The scanner never reads source file contents. It checks link/reparse metadata and
refuses linked root ancestors, but is not an atomic or race-hardened filesystem
walker. Freeze the source tree during inspection. A symlink or special file is
reported but not planned. Output exclusivity is for accidental overwrite
protection, not a defense against hostile concurrent replacement of output parents.

## Manifest records

The default UTF-8 manifest keeps its line-based behavior, including CRLF support
and an optional final line ending. With `--path-list FILE --null`, only NUL separates
records; CR and LF are literal filename characters, including at the end of a name.
Both formats use the same relative-path validation, directory `/` marker, inferred
parents, record/node caps, and planner. No filename quoting or escape decoding occurs.

NUL input is read in bounded chunks. A record over the input path limit is never
split into invented paths. A final nonempty record without NUL produces
`manifest_missing_terminator` and is omitted from the partial inventory; earlier
terminated records may remain, but `inventory_complete` and `plan_complete` are
false. This catches an incomplete final record, not truncation exactly at a record
boundary or a failed producer that wrote an empty file. Check the producer's exit
status too. Empty records are ignored but count toward the input cap; an empty
file is a valid empty inventory. These options do not add a JSON schema field or
change the report for equivalent complete inventories.

A manifest describes names and declared types, not on-disk metadata. Git index
lists can omit untracked files and empty directories and can include paths for
symlinks or submodules without type markers. Use a directory scan when those
filesystem distinctions matter; NUL delimiters alone cannot establish completeness.

## JSON schema version 1

- `mode`: always `plan-only`; `schema_version` and `tool_version` identify format
- `profile`, `destination_prefix`, `limits`: exact modeled policy and budgets
- `inventory_complete`: false after any input/read/cap/type error
- `plan_complete`: true only when the inventory and all allocations verify
- `verification`: unique target aliases, fitting lengths, validated node count
- `summary`: node/record/issue/change/blocked/error counts
- `errors`: at most 100 detail records; summary retains the full error count
- `entries`: every inventoried file/directory/link/special node, sorted by source;
  source kind, inferred flag, issue codes, proposal (or null), and before/after metrics
- `mapping`: changed nodes only, each with source, proposed, kind, and reason codes

Paths use `/`, even when the target prefix was supplied with backslashes. Lengths
include the prefix and separators but not a terminating NUL; defaults leave the
legacy Windows NUL allowance outside the 259 budget. The root itself is not a node.
An empty source can have a complete empty mapping, even if the prefix leaves no
space for additional files. Inventory counts include inferred directories and
skipped nodes; record counts include blank/duplicate manifest lines.

`--json` serializes using ASCII escapes so lone-surrogate POSIX names remain valid
JSON strings. Terminal rendering escapes controls, ANSI, bidi controls and surrogate
characters. Reports are still potentially sensitive because filenames are data.

## Boundary

This is a preflight planner. It does not inspect destination contents, guarantee
sync-client behavior, emulate all filesystem rules, resolve contents, rename,
copy, restore, or provide an apply command. A planned mapping is not a safe
operation ordering for a filesystem executor. Review the report and test a copy
with your actual destination and transfer tool before any migration.
