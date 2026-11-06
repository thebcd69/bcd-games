#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Package-time completeness of a Windows farm: the files that must be there, and every import of every PE resolving inside it (round 632, D17).

BCD's workflow has about 30 continue-on-error steps (the i386 farm, the extra Wine DLLs, 7-Zip, the stand-in DLLs, ...). A step that fails
does not fail the build, and after the build-628 rollback the lead noted that a later failure would silently leave stale files in the package:
the only guard was tools/check-optin-dlls-shipped.py (the opt-in DLLs lists and rules name). This reads every PE file of a farm folder,
resolves its static and delay imports against the farm's own file names, and compares the result and a short manifest with
tools/farm-baseline.json, which records what is known and accepted today:

  check-farm-imports.py [--baseline=FILE] [--warn-only] FARM_DIR      (FARM_DIR's name is the farm: arm64ec-windows, aarch64-windows, i386-windows)
  check-farm-imports.py --write-baseline FARM_DIR                     prints the farm's baseline entry (run it on a good build's farm)
  - every file in the baseline's "required" list is present, and the farm has at least "minFiles" files (a farm step that wrote nothing);
  - no PE file is unreadable (a half-written module);
  - every STATIC import of every PE resolves to a file of the farm (an api-set contract resolves when the farm ships apisetschema.dll), except
    the pairs the baseline lists in "staticGaps" (the opt-in modules whose imports resolve through MADEIRA_OPTIN_DLLS, for example);
  - a delay import without a file in the farm that the baseline does not list is a "::warning::" line with its importer (delay imports are
    optional by nature; the next missing module still shows up in the build log). Never a failure.
A gap the baseline lists but the farm no longer has is reported as stale (a warning). --warn-only turns every failure into a warning (exit 0).
"""
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PE_SUFFIXES = (".dll", ".exe", ".drv", ".sys", ".ocx", ".cpl", ".ax", ".acm", ".tlb")
MACHINES = {0x8664: "x64", 0xA641: "arm64ec", 0xA64E: "arm64x", 0xAA64: "arm64", 0x14C: "i386"}


class PEError(ValueError):
    pass


class PE:
    """Just enough of a PE file to list its import and delay-import module names. Every read is bounds-checked."""

    def __init__(self, data):
        self.data = data
        if data[:2] != b"MZ":
            raise PEError("no DOS header")
        pe = self.u32(0x3C)
        if data[pe:pe + 4] != b"PE\0\0":
            raise PEError("no PE signature")
        self.machine, sections = struct.unpack_from("<HH", data, self._at(pe + 4, 4))
        optional_size = struct.unpack_from("<H", data, self._at(pe + 20, 2))[0]
        optional = pe + 24
        magic = struct.unpack_from("<H", data, self._at(optional, 2))[0]
        if magic == 0x20B:
            self.image_base = struct.unpack_from("<Q", data, self._at(optional + 24, 8))[0]
            count_at, dirs_at = optional + 108, optional + 112
        elif magic == 0x10B:
            self.image_base = struct.unpack_from("<I", data, self._at(optional + 28, 4))[0]
            count_at, dirs_at = optional + 92, optional + 96
        else:
            raise PEError("unknown optional header")
        self.headers = struct.unpack_from("<I", data, self._at(optional + 60, 4))[0]
        count = struct.unpack_from("<I", data, self._at(count_at, 4))[0]
        self.directories = []
        for i in range(min(count, 16)):
            self.directories.append(struct.unpack_from("<II", data, self._at(dirs_at + i * 8, 8)))
        if not 0 < sections <= 96:
            raise PEError("section count")
        self.sections = []
        table = optional + optional_size
        for i in range(sections):
            vsize, rva, raw_size, raw = struct.unpack_from("<IIII", data, self._at(table + i * 40 + 8, 16))
            self.sections.append((rva, max(vsize, raw_size), raw, raw_size))

    def _at(self, offset, size):
        if offset < 0 or offset + size > len(self.data):
            raise PEError("truncated")
        return offset

    def u32(self, offset):
        return struct.unpack_from("<I", self.data, self._at(offset, 4))[0]

    def offset(self, rva, size=1):
        if rva < self.headers and rva + size <= self.headers:
            return self._at(rva, size)
        for start, length, raw, raw_size in self.sections:
            if start <= rva and rva - start + size <= min(length, raw_size):
                return self._at(raw + rva - start, size)
        raise PEError("RVA outside the file-backed sections")

    def name(self, rva):
        at = self.offset(rva)
        end = self.data.find(b"\0", at, at + 260)
        if end < 0:
            raise PEError("unterminated name")
        return self.data[at:end].decode("latin-1").lower()

    def imports(self, delayed=False):
        rva, size = self.directories[13 if delayed else 1] if len(self.directories) > (13 if delayed else 1) else (0, 0)
        if not rva:
            return set()
        width = 32 if delayed else 20
        found = set()
        for i in range(min(max(size // width, 1), 512)):
            at = self.offset(rva + i * width, width)
            fields = struct.unpack_from("<" + "I" * (width // 4), self.data, at)
            if not any(fields):
                return found
            if delayed:
                name_rva = fields[1] if fields[0] & 1 else fields[1] - (self.image_base & 0xFFFFFFFF)
            else:
                name_rva = fields[3]
            found.add(self.name(name_rva & 0xFFFFFFFF))
        raise PEError("unterminated import directory")


def scan(folder):
    """{name: (machine, static imports, delay imports)} for every PE file, and the names that could not be read."""
    result, unreadable = {}, []
    for path in sorted(Path(folder).iterdir()):
        if not path.is_file() or path.suffix.lower() not in PE_SUFFIXES:
            continue
        try:
            module = PE(path.read_bytes())
            result[path.name.lower()] = (module.machine, module.imports(), module.imports(delayed=True))
        except (PEError, struct.error, OSError, UnicodeError) as error:
            unreadable.append("%s (%s)" % (path.name, error))
    return result, unreadable


def gaps(folder, parsed):
    names = {p.name.lower() for p in Path(folder).iterdir() if p.is_file()}
    apiset = "apisetschema.dll" in names
    static, delay = {}, {}
    for name, (_machine, imports, delayed) in parsed.items():
        for kind, wanted, out in (("static", imports, static), ("delay", delayed, delay)):
            for dep in sorted(wanted):
                if dep in names or (apiset and dep.startswith(("api-ms-", "ext-ms-"))):
                    continue
                out.setdefault(name, []).append(dep)
    return static, delay, len(names)


def check(folder, baseline):
    farm = Path(folder).resolve().name
    entry = baseline.get("farms", {}).get(farm)
    if entry is None:
        raise ValueError("the baseline knows no farm called %s (arm64ec-windows, aarch64-windows, i386-windows)" % farm)
    parsed, unreadable = scan(folder)
    static, delay, count = gaps(folder, parsed)
    names = {p.name.lower() for p in Path(folder).iterdir() if p.is_file()}
    problems, warnings = [], []
    if count < entry["minFiles"]:
        problems.append("%s has %d files, at least %d are expected (a farm step wrote nothing)" % (farm, count, entry["minFiles"]))
    for need in entry["required"]:
        if need.lower() not in names:
            problems.append("%s: the required file %s is missing" % (farm, need))
    for item in unreadable:
        problems.append("%s: not a readable PE file: %s" % (farm, item))
    accepted_static = {k.lower(): set(v) for k, v in entry.get("staticGaps", {}).items()}
    accepted_delay = {k.lower(): set(v) for k, v in entry.get("delayGaps", {}).items()}
    for name, deps in sorted(static.items()):
        for dep in deps:
            if dep not in accepted_static.get(name, set()):
                problems.append("%s: %s imports %s, which the farm does not have" % (farm, name, dep))
    for name, deps in sorted(delay.items()):
        for dep in deps:
            if dep not in accepted_delay.get(name, set()):
                warnings.append("%s: %s delay-imports %s, which the farm does not have (a new gap: look at it, then add it to the baseline)" % (farm, name, dep))
    stale = [("static", n, d) for n, ds in accepted_static.items() for d in sorted(ds) if d not in static.get(n, [])]
    stale += [("delay", n, d) for n, ds in accepted_delay.items() for d in sorted(ds) if d not in delay.get(n, [])]
    for kind, name, dep in stale:
        warnings.append("%s: the baseline lists %s %s -> %s, which is resolved or gone now (the baseline can lose the line)" % (farm, kind, name, dep))
    return problems, warnings, count, len(parsed), sum(len(v) for v in static.values()), sum(len(v) for v in delay.values())


def write_baseline(folder):
    farm = Path(folder).resolve().name
    parsed, unreadable = scan(folder)
    static, delay, count = gaps(folder, parsed)
    names = sorted(p.name for p in Path(folder).iterdir() if p.is_file())
    return {farm: {"minFiles": int(count * 0.9), "required": [], "staticGaps": static, "delayGaps": delay, "unreadable": unreadable, "files": len(names)}}


def main(argv):
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    baseline_path = HERE / "farm-baseline.json"
    for flag in flags:
        if flag.startswith("--baseline="):
            baseline_path = Path(flag.split("=", 1)[1])
        elif flag not in ("--warn-only", "--write-baseline"):
            return "usage: check-farm-imports.py [--baseline=FILE] [--warn-only] [--write-baseline] FARM_DIR"
    if len(args) != 1 or not Path(args[0]).is_dir():
        return "usage: check-farm-imports.py [--baseline=FILE] [--warn-only] [--write-baseline] FARM_DIR"
    if "--write-baseline" in flags:
        print(json.dumps(write_baseline(args[0]), indent=1, sort_keys=True))
        return None
    try:
        problems, warnings, count, parsed, static, delay = check(args[0], json.loads(baseline_path.read_text()))
    except (OSError, ValueError, KeyError) as error:
        return "check-farm-imports: " + str(error)
    farm = Path(args[0]).resolve().name
    for line in warnings:
        print("::warning::" + line)
    if problems:
        for line in problems:
            print(("::warning::" if "--warn-only" in flags else "::error::") + line)
        if "--warn-only" not in flags:
            return "check-farm-imports: %s: %d problem(s)" % (farm, len(problems))
    print("check-farm-imports: %s: %d files, %d PE modules read, no unresolved static import beyond the %d the baseline accepts (%d delay-import gaps accepted)"
          % (farm, count, parsed, static, delay))
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
