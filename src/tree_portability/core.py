"""Conservative, offline name rules and a bounded whole-tree planner.

Nothing in this module opens source file contents, renames, or copies entries.
The profiles deliberately over-report aliases; they are not filesystem emulators.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

MAX_ENTRIES = 100_000
MAX_DEPTH = 128
MAX_INPUT_PATH = 16_384
MIN_GENERATED_NAME = 9  # "~" plus eight hash/counter characters
INVALID = frozenset('<>:"/\\|?*')
RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
RESERVED.update(f"{stem}{digit}" for stem in ("COM", "LPT") for digit in "123456789¹²³")
FULLWIDTH = str.maketrans("＜＞：＂／＼｜？＊", '<>:"/\\|?*')


@dataclass(frozen=True)
class Profile:
    name: str
    path_budget: int
    utf8_limit: int | None
    utf16_limit: int = 255
    both_path_units: bool = False


PROFILES = {
    "windows": Profile("windows", 259, None),
    "exfat": Profile("exfat", 259, None),
    "portable": Profile("portable", 240, 255, both_path_units=True),
}


@dataclass(slots=True)
class Entry:
    path: str
    kind: str
    inferred: bool = False

    @property
    def parent(self) -> str:
        return self.path.rpartition("/")[0]

    @property
    def name(self) -> str:
        return self.path.rpartition("/")[2]


@dataclass
class Inventory:
    max_entries: int = MAX_ENTRIES
    entries: dict[str, Entry] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    complete: bool = True
    records: int = 0
    error_count: int = 0
    source_kind: str = "path-list"

    def __post_init__(self) -> None:
        if not 1 <= self.max_entries <= 1_000_000:
            raise ValueError("max_entries must be between 1 and 1000000")

    def error(self, code: str, path: str, detail: str) -> None:
        self.complete = False
        self.error_count += 1
        # Input and traversal caps also bound errors, even for hostile manifests.
        if len(self.errors) < 100:
            self.errors.append({"code": code, "path": path[:512], "detail": detail})

    def add(self, path: str, kind: str) -> bool:
        parts = path.split("/")
        if (
            not path
            or path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:[/\\]", path)
            or any(part in ("", ".", "..") for part in parts)
            or "\0" in path
        ):
            self.error("invalid_path", path, "Expected a relative slash-separated path without . or ..")
            return True
        if len(parts) > MAX_DEPTH or len(path) > MAX_INPUT_PATH:
            self.error("input_path_limit", path, "Maximum depth is 128; maximum input path is 16384 characters")
            return True
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        for index, prefix in enumerate(prefixes):
            existing = self.entries.get(prefix)
            expected = kind if index == len(parts) - 1 else "directory"
            if existing and existing.kind != expected:
                self.error("source_type_conflict", prefix, "A source path cannot be both a directory and a non-directory")
                return True
        missing = sum(prefix not in self.entries for prefix in prefixes)
        if len(self.entries) + missing > self.max_entries:
            self.error("inventory_limit", ".", f"Inventory exceeds {self.max_entries} entries including inferred directories")
            return False
        for index, prefix in enumerate(prefixes):
            final = index == len(parts) - 1
            if prefix not in self.entries:
                self.entries[prefix] = Entry(prefix, kind if final else "directory", inferred=not final)
            elif final:
                self.entries[prefix].inferred = False
        return True


def inventory_from_paths(lines: Iterable[str], max_entries: int = MAX_ENTRIES) -> Inventory:
    """Read relative POSIX paths; one trailing '/' declares a directory."""
    inventory = Inventory(max_entries=max_entries)
    for record, line in enumerate(lines, 1):
        if record > max_entries:
            inventory.error("record_limit", ".", f"Input exceeds {max_entries} records (blank lines and duplicates count)")
            break
        inventory.records = record
        path = line.rstrip("\r\n")
        if not path:
            continue
        kind = "directory" if path.endswith("/") else "file"
        if kind == "directory":
            path = path[:-1]
        if not inventory.add(path, kind):
            break
    return inventory


def _is_link(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def scan_directory(root: str | os.PathLike[str], max_entries: int = MAX_ENTRIES) -> Inventory:
    """Only walk an explicitly supplied directory, without following observed links.

    All reparse points (including Windows junctions) are excluded. This is a
    metadata snapshot, not an atomic walk of a concurrently modified filesystem.
    """
    inventory = Inventory(max_entries=max_entries, source_kind="scan")
    root_path = Path(os.path.abspath(root))
    try:
        for ancestor in (*reversed(root_path.parents), root_path):
            if _is_link(ancestor.lstat()):
                inventory.error("root_link", ".", "The explicit root or one of its ancestors is a link/reparse point")
                return inventory
        if not stat.S_ISDIR(root_path.lstat().st_mode):
            inventory.error("not_a_directory", ".", "The explicit root must be a directory")
            return inventory
    except OSError as exc:
        inventory.error("scan_failed", ".", errno.errorcode.get(exc.errno, "OS_ERROR"))
        return inventory

    pending = [(root_path, "")]
    while pending:
        directory, relative = pending.pop()
        children = []
        try:
            metadata = directory.lstat()
            if _is_link(metadata) or not stat.S_ISDIR(metadata.st_mode):
                inventory.error("directory_changed", relative or ".", "Directory changed type during scan; traversal skipped")
                continue
            with os.scandir(directory) as iterator:
                for item in iterator:
                    path = f"{relative}/{item.name}" if relative else item.name
                    inventory.records += 1
                    if inventory.records > max_entries:
                        inventory.error("record_limit", ".", f"Scan exceeds {max_entries} directory entries")
                        return inventory
                    try:
                        metadata = item.stat(follow_symlinks=False)
                        if _is_link(metadata):
                            kind = "symlink"
                        elif stat.S_ISDIR(metadata.st_mode):
                            kind = "directory"
                        elif stat.S_ISREG(metadata.st_mode):
                            kind = "file"
                        else:
                            kind = "special"
                    except OSError as exc:
                        inventory.error("entry_stat_failed", path, errno.errorcode.get(exc.errno, "OS_ERROR"))
                        continue
                    if not inventory.add(path, kind):
                        return inventory
                    if kind == "directory" and path in inventory.entries:
                        children.append((Path(item.path), path))
        except OSError as exc:
            inventory.error("scan_failed", relative or ".", errno.errorcode.get(exc.errno, "OS_ERROR"))
            continue
        pending.extend(sorted(children, key=lambda pair: pair[1], reverse=True))
    return inventory


def lengths(text: str) -> tuple[int, int]:
    """UTF-8 bytes and UTF-16 code units; lone surrogates get a separate issue."""
    return len(text.encode("utf-8", "surrogatepass")), len(text.encode("utf-16-le", "surrogatepass")) // 2


def alias(name: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name.rstrip(" .")).casefold())


def _reserved(name: str) -> bool:
    return name.rstrip(" .").split(".", 1)[0].rstrip(" ").upper() in RESERVED


def _name_issues(name: str, profile: Profile) -> set[str]:
    issues = set()
    if any(char in INVALID or ord(char) < 32 for char in name):
        issues.add("illegal_character")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in name):
        issues.add("invalid_unicode")
    if name != name.rstrip(" ."):
        issues.add("trailing_dot_or_space")
    if _reserved(name):
        issues.add("reserved_name")
    if name != unicodedata.normalize("NFC", name):
        issues.add("non_nfc")
    utf8, utf16 = lengths(name)
    if profile.utf8_limit is not None and utf8 > profile.utf8_limit:
        issues.add("component_utf8_limit")
    if utf16 > profile.utf16_limit:
        issues.add("component_utf16_limit")
    return issues


def _safe_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = "".join(
        "_" if char in INVALID or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF else char
        for char in name
    ).rstrip(" .") or "_"
    if _reserved(name):
        name = "_" + name
    return name


def _join(parent: str, name: str) -> str:
    return parent + ("" if not parent or parent.endswith("/") else "/") + name


def validate_prefix(prefix: str, profile: Profile) -> str:
    """Validate a fixed target prefix as text. It is never accessed on disk."""
    prefix = prefix.replace("\\", "/")
    if not prefix:
        return ""
    if "\0" in prefix or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in prefix):
        raise ValueError("destination prefix contains a control character or invalid Unicode")
    # Extended Win32/device namespaces are intentionally outside these profiles.
    if prefix.startswith(("//?/", "//./")):
        raise ValueError("extended/device destination namespaces are unsupported")
    trimmed = prefix.rstrip("/")
    if not trimmed:
        if prefix != "/":
            raise ValueError("destination prefix must contain a directory, drive, or UNC share")
        return "/"
    parts = trimmed.split("/")
    start = 0
    if re.fullmatch(r"[A-Za-z]:", parts[0]):
        if len(parts) == 1:
            if prefix != parts[0] + "/":
                raise ValueError("drive prefix must be absolute, for example D:/")
            return prefix
        start = 1
    elif re.match(r"^[A-Za-z]:", parts[0]):
        raise ValueError("drive-relative destination prefixes are unsupported")
    elif trimmed.startswith("//"):
        if len(parts) < 4 or not parts[2] or not parts[3]:
            raise ValueError("UNC prefix must contain a server and share")
        start = 2
    elif trimmed.startswith("/"):
        start = 1
    for part in parts[start:]:
        if part in ("", ".", "..") or _name_issues(part, profile):
            raise ValueError("destination prefix contains a nonportable or oversized component")
    return trimmed


def _clip(text: str, utf8_budget: int | None, utf16_budget: int) -> str:
    output = []
    utf8 = utf16 = 0
    for char in text:
        bytes8, units16 = lengths(char)
        if utf16 + units16 > utf16_budget or (utf8_budget is not None and utf8 + bytes8 > utf8_budget):
            break
        output.append(char)
        utf8 += bytes8
        utf16 += units16
    # Avoid splitting a combining sequence at the truncation boundary.
    while output and unicodedata.combining(output[-1]):
        output.pop()
    return "".join(output).rstrip(" .")


def _base36(number: int) -> str:
    result = ""
    while number:
        number, digit = divmod(number, 36)
        result = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + result
    return result or "0"


def _candidate(entry: Entry, utf8_budget: int | None, utf16_budget: int, attempt: int) -> str:
    digest = hashlib.sha256(entry.path.encode("utf-8", "surrogatepass")).hexdigest()
    counter = "-" + _base36(attempt) if attempt else ""
    suffix = "~" + digest[:8 - len(counter)] + counter
    name = _safe_name(entry.name)
    stem, dot, extension = name.rpartition(".")
    extension = dot + extension if dot and stem and entry.kind == "file" else ""
    # Keep short extensions only when enough room remains for a readable stem.
    bytes8, units16 = lengths(extension)
    if (
        units16 > 16
        or units16 + MIN_GENERATED_NAME + 1 > utf16_budget
        or (utf8_budget is not None and bytes8 + MIN_GENERATED_NAME + 1 > utf8_budget)
    ):
        extension = ""
    else:
        name = stem if extension else name
    ext8, ext16 = lengths(extension)
    stem = _clip(
        name,
        None if utf8_budget is None else utf8_budget - len(suffix) - ext8,
        utf16_budget - len(suffix) - ext16,
    )
    return stem + suffix + extension


def _metrics(name: str, path: str) -> dict[str, int]:
    name8, name16 = lengths(name)
    path8, path16 = lengths(path)
    return {
        "name_utf8_bytes": name8,
        "name_utf16_units": name16,
        "path_utf8_bytes": path8,
        "path_utf16_units": path16,
    }


def build_report(
    inventory: Inventory,
    profile_name: str = "portable",
    destination_prefix: str = "",
    path_budget: int | None = None,
) -> dict:
    """Allocate names across the entire tree; failed allocations stay null.

    Bottom-up descendant reserves let the top-down pass shorten ancestors before
    allocating their children. A nine-character generated name is our lower
    bound, not a proof that no shorter naming scheme could fit.
    """
    if profile_name not in PROFILES:
        raise ValueError(f"unknown profile: {profile_name}")
    profile = PROFILES[profile_name]
    prefix = validate_prefix(destination_prefix, profile)
    budget = profile.path_budget if path_budget is None else path_budget
    if not isinstance(budget, int) or not 1 <= budget <= 32_767:
        raise ValueError("path budget must be between 1 and 32767")

    ordered = sorted(inventory.entries.values(), key=lambda entry: (entry.path.count("/"), entry.path))
    children: dict[str, list[Entry]] = defaultdict(list)
    issues: dict[str, set[str]] = {}
    source_aliases: dict[str, str] = {}
    encoder_aliases: dict[str, str] = {}
    target_groups: dict[str, list[str]] = defaultdict(list)
    encoder_groups: dict[str, list[str]] = defaultdict(list)
    for entry in ordered:
        children[entry.parent].append(entry)
        entry_issues = _name_issues(entry.name, profile)
        source_aliases[entry.path] = _join(source_aliases.get(entry.parent, ""), alias(entry.name))
        encoder_aliases[entry.path] = _join(encoder_aliases.get(entry.parent, ""), alias(entry.name.translate(FULLWIDTH)))
        target_groups[source_aliases[entry.path]].append(entry.path)
        encoder_groups[encoder_aliases[entry.path]].append(entry.path)
        original8, original16 = lengths(_join(prefix, entry.path))
        if original16 > budget:
            entry_issues.add("path_utf16_limit")
        if profile.both_path_units and original8 > budget:
            entry_issues.add("path_utf8_limit")
        if entry.kind not in ("file", "directory"):
            entry_issues.add(f"{entry.kind}_skipped")
        issues[entry.path] = entry_issues

    for group in target_groups.values():
        if len(group) < 2:
            continue
        stripped = {"/".join(part.rstrip(" .") for part in path.split("/")) for path in group}
        normalized = {unicodedata.normalize("NFC", path) for path in stripped}
        codes = {"target_alias_collision"}
        if len(stripped) < len(group):
            codes.add("trailing_alias_collision")
        if len(normalized) < len(stripped):
            codes.add("nfc_collision")
        if len(normalized) > 1:
            codes.add("case_collision")
        if len({inventory.entries[path].kind for path in group}) > 1:
            codes.add("file_directory_alias")
        for path in group:
            issues[path].update(codes)
    for group in encoder_groups.values():
        if len({source_aliases[path] for path in group}) > 1:
            for path in group:
                issues[path].add("encoding_alias_risk")

    minimum: dict[str, tuple[int, int]] = {}
    for siblings in children.values():
        preserved = set()
        for entry in siblings:
            key = alias(entry.name)
            if not _name_issues(entry.name, profile) and key not in preserved:
                original8, original16 = lengths(entry.name)
                minimum[entry.path] = (min(original8, MIN_GENERATED_NAME), min(original16, MIN_GENERATED_NAME))
                preserved.add(key)
            else:
                minimum[entry.path] = (MIN_GENERATED_NAME, MIN_GENERATED_NAME)
    tail_min: dict[str, tuple[int, int]] = {}
    for entry in reversed(ordered):
        descendants = children.get(entry.path, [])
        tail_min[entry.path] = (
            max((1 + minimum[child.path][0] + tail_min[child.path][0] for child in descendants), default=0),
            max((1 + minimum[child.path][1] + tail_min[child.path][1] for child in descendants), default=0),
        )

    proposed: dict[str, str | None] = {"": ""}
    allocation_reasons: dict[str, set[str]] = defaultdict(set)
    for parent in [""] + [entry.path for entry in ordered if entry.kind == "directory"]:
        siblings = children.get(parent, [])
        parent_relative = proposed.get(parent)
        reserved = {alias(entry.name) for entry in siblings}
        used = set()
        for entry in siblings:
            if entry.kind not in ("file", "directory"):
                proposed[entry.path] = None
                continue
            if parent_relative is None:
                proposed[entry.path] = None
                issues[entry.path].add("ancestor_unplannable")
                continue
            parent_full = _join(prefix, parent_relative) if parent_relative else prefix
            parent8, parent16 = lengths(parent_full)
            separator = int(bool(parent_full) and not parent_full.endswith("/"))
            allowed16 = min(profile.utf16_limit, budget - parent16 - separator - tail_min[entry.path][1])
            allowed8 = profile.utf8_limit
            if profile.both_path_units:
                allowed8 = min(profile.utf8_limit, budget - parent8 - separator - tail_min[entry.path][0])
            name8, name16 = lengths(entry.name)
            safe_original = not _name_issues(entry.name, profile)
            fits = name16 <= allowed16 and (allowed8 is None or name8 <= allowed8)
            original_alias = alias(entry.name)
            if safe_original and fits and original_alias not in used:
                name = entry.name
            else:
                if original_alias in used:
                    allocation_reasons[entry.path].add("alias_renamed")
                if not fits:
                    allocation_reasons[entry.path].add("budget_shortened")
                if allowed16 < MIN_GENERATED_NAME or (allowed8 is not None and allowed8 < MIN_GENERATED_NAME):
                    proposed[entry.path] = None
                    issues[entry.path].add("budget_unplannable")
                    continue
                name = None
                # Each counter gives a different suffix. Blocked namespaces are
                # finite and bounded by the sibling inventory, not a retry loop.
                for attempt in range(len(reserved) + len(used) + 1):
                    candidate = _candidate(entry, allowed8, allowed16, attempt)
                    key = alias(candidate)
                    if key not in reserved and key not in used and not _name_issues(candidate, profile):
                        name = candidate
                        break
                if name is None:
                    proposed[entry.path] = None
                    issues[entry.path].add("namespace_unplannable")
                    continue
            proposed[entry.path] = _join(parent_relative, name)
            used.add(alias(name))
            if parent_relative != parent:
                allocation_reasons[entry.path].add("parent_renamed")

    entries = []
    mapping = []
    final_aliases = set()
    aliases_unique = True
    lengths_fit = True
    validated = 0
    for entry in sorted(ordered, key=lambda item: item.path):
        target = proposed.get(entry.path)
        row = {
            "source": entry.path,
            "kind": entry.kind,
            "inferred": entry.inferred,
            "issues": sorted(issues[entry.path]),
            "proposed": target,
            "original_lengths": _metrics(entry.name, _join(prefix, entry.path)),
            "proposed_lengths": None,
        }
        if target is not None:
            target_name = target.rpartition("/")[2]
            target_full = _join(prefix, target)
            row["proposed_lengths"] = _metrics(target_name, target_full)
            key = "/".join(alias(part) for part in target.split("/"))
            aliases_unique = aliases_unique and key not in final_aliases
            final_aliases.add(key)
            target8, target16 = lengths(target_full)
            lengths_fit = lengths_fit and target16 <= budget and (
                not profile.both_path_units or target8 <= budget
            ) and not _name_issues(target_name, profile)
            validated += 1
            if target != entry.path:
                mapping.append({
                    "source": entry.path,
                    "proposed": target,
                    "kind": entry.kind,
                    "reasons": sorted(issues[entry.path] | allocation_reasons[entry.path]),
                })
        entries.append(row)

    blocked = len(entries) - validated
    complete = inventory.complete and not blocked and aliases_unique and lengths_fit
    return {
        "schema_version": 1,
        "tool_version": "0.1.0",
        "mode": "plan-only",
        "profile": profile.name,
        "destination_prefix": prefix,
        "limits": {
            "component_utf8_bytes": profile.utf8_limit,
            "component_utf16_units": profile.utf16_limit,
            "path_budget": budget,
            "path_budget_units": ["utf8_bytes", "utf16_units"] if profile.both_path_units else ["utf16_units"],
            "max_entries": inventory.max_entries,
            "generated_name_minimum": MIN_GENERATED_NAME,
        },
        "source_kind": inventory.source_kind,
        "inventory_complete": inventory.complete,
        "plan_complete": complete,
        "verification": {
            "target_aliases_unique": aliases_unique,
            "target_lengths_fit": bool(lengths_fit),
            "validated_entries": validated,
        },
        "summary": {
            "entries": len(entries),
            "records_read": inventory.records,
            "entries_with_issues": sum(bool(row["issues"]) for row in entries),
            "mapping_entries": len(mapping),
            "blocked_entries": blocked,
            "inventory_errors": inventory.error_count,
            "omitted_error_details": inventory.error_count - len(inventory.errors),
        },
        "errors": sorted(inventory.errors, key=lambda error: (error["path"], error["code"], error["detail"])),
        "mapping": mapping,
        "entries": entries,
    }


def exit_code(report: dict) -> int:
    if not report["plan_complete"]:
        return 2
    return 1 if report["summary"]["entries_with_issues"] or report["mapping"] else 0
