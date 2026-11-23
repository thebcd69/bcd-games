#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The icuuc.dll / icuin.dll that tools/build-icu-optin.sh built, or the icuuc-optin.dll / icuin-optin.dll it shipped, are the DLLs BCD means.

  check-icu-dlls.py [--machine arm64ec|x86_64] [--icu-source ICU_SOURCE_DIR] FILE...
      FILE: icuuc.dll / icuin.dll (build/icu-win/out) or icuuc-optin.dll / icuin-optin.dll (app/Madeira/arm64ec-windows in a built tree);
            which of the two it is comes from the file name.
      --machine: arm64ec (default: what ships) or x86_64 (the test flavour that runs under a host Wine).
      --icu-source: the extracted tarball's source/ directory; every C function its public headers declare (U_CAPI ... U_EXPORT2 name) must
            then be exported too.

What it holds each file to (exit 1 naming every failed point; reads only):
  - a PE32+ DLL of the flavour asked for (ARM64EC: native code for the phone, x86-64 callers reach it through entry thunks; an ARM64EC image
    says machine 0x8664 in its file header and has CHPE metadata in its load configuration);
  - the export directory names the DLL icuuc.dll / icuin.dll (the names Windows' own ICU forwarders have and Qt6Core.dll imports);
  - it exports ICU's C API UNVERSIONED (u_strToUpper, ucol_open, ubrk_open, ucnv_open, ucal_open, udat_open, ... as Windows' system ICU
    does and Qt is built to import; ICU's default renaming would give ucol_open_78): the names Qt 6 calls (QT_COMMON, QT_I18N below), no
    name ending _78 among the C ones, and with --icu-source every public C function of the headers;
  - icuuc.dll carries ICU's data (the trimmed package, icudt78l/...: build/icu-win/trim-data.py) inside, icuin.dll carries none, and
    icuin.dll imports icuuc.dll (by that name: the app links icuuc-optin.dll as icuuc.dll);
  - the static imports are only DLLs Wine ships for 64-bit programs: kernel32, advapi32 and the UCRT's api-ms-win-crt-* sets (plus icuuc.dll
    for icuin.dll); libc++, libunwind and the MinGW runtime are linked in, and no libc++.dll / libwinpthread-1.dll / icudt*.dll is needed;
  - no absolute path of the machine that built it (/Users/<name>/, /home/<name>/, /private/, /Volumes/): the build maps them away with
    -ffile-prefix-map; only the GitHub runner's /Users/runner/work/llvm-mingw/... of the toolchain's own libc++abi may remain;
  - the PE time stamp is 0 (the build links with --no-insert-timestamp: the same inputs give the same bytes);
  - a plausible size (a stripped build: icuuc about 10 MB with its 6.8 MB of data, icuin about 4 MB).
"""
import importlib.util
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# An ARM64EC image says machine 0x8664 (AMD64) in its file header, like the x86-64 DLLs it serves, and carries the ARM64EC (CHPE) metadata in its
# load configuration directory (IMAGE_LOAD_CONFIG_DIRECTORY64.CHPEMetadataPointer, offset 0xC8): what llvm-readobj shows as machine 0xA641.
# Wine's own ARM64EC DLLs in app/Madeira/arm64ec-windows (dcomp-optin.dll) are the same.
MACHINES = ("arm64ec", "x86_64")
ICU_MAJOR = "78"
DATA_PREFIX = ("icudt%sl/" % ICU_MAJOR).encode()

# What Qt 6 (qtbase: QCollator, QStringConverter and core5compat's QTextCodec, QTimeZone's ICU back end, QLocale's case mapping, the
# break iterators) calls, from Qt's sources as remembered: a list to read, not a claim about one Qt build's import table; the headers
# check below (--icu-source) is what covers every name, and Windows' own icuuc.dll / icuin.dll export the same split (common / i18n).
QT_COMMON = """
u_errorName u_strlen u_strToUpper u_strToLower u_strToTitle u_strFoldCase u_toupper u_tolower u_charType u_isspace u_getVersion
ucnv_open ucnv_close ucnv_reset ucnv_toUnicode ucnv_fromUnicode ucnv_getName ucnv_getMaxCharSize ucnv_getMinCharSize
ucnv_setSubstChars ucnv_getSubstChars ucnv_setFromUCallBack ucnv_setToUCallBack ucnv_countAvailable ucnv_getAvailableName
ucnv_countAliases ucnv_getAlias ucnv_getStandardName ucnv_countStandards ucnv_getStandard ucnv_getCanonicalName ucnv_getDefaultName
ucnv_toUCountPending ucnv_fromUCountPending ucnv_getStarters ucnv_getType ucnv_flushCache ucnv_resetToUnicode ucnv_resetFromUnicode
UCNV_FROM_U_CALLBACK_STOP UCNV_TO_U_CALLBACK_STOP UCNV_FROM_U_CALLBACK_SUBSTITUTE UCNV_TO_U_CALLBACK_SUBSTITUTE
ubrk_open ubrk_close ubrk_setText ubrk_first ubrk_last ubrk_next ubrk_previous ubrk_current ubrk_following ubrk_preceding ubrk_isBoundary
ubrk_getRuleStatus
uloc_getDefault uloc_setDefault uloc_forLanguageTag uloc_toLanguageTag uloc_getName uloc_canonicalize uloc_getLanguage uloc_getCountry
uloc_getScript uloc_countAvailable uloc_getAvailable uloc_addLikelySubtags uloc_getDisplayLanguage uloc_getDisplayCountry
uenum_next uenum_unext uenum_count uenum_reset uenum_close
unorm2_getNFCInstance unorm2_getNFDInstance unorm2_getNFKCInstance unorm2_getNFKDInstance unorm2_normalize unorm2_getInstance
uidna_openUTS46 uidna_nameToASCII uidna_close
""".split()
QT_I18N = """
ucol_open ucol_close ucol_setAttribute ucol_getAttribute ucol_strcoll ucol_getSortKey ucol_getLocaleByType ucol_getRules
ucal_open ucal_close ucal_clone ucal_get ucal_set ucal_setMillis ucal_getMillis ucal_add ucal_inDaylightTime ucal_getDSTSavings
ucal_getDefaultTimeZone ucal_getHostTimeZone ucal_getTimeZoneDisplayName ucal_getTimeZoneTransitionDate ucal_openTimeZones
ucal_openCountryTimeZones ucal_openTimeZoneIDEnumeration ucal_getCanonicalTimeZoneID ucal_getWindowsTimeZoneID
ucal_getTimeZoneIDForWindowsID ucal_getTimeZoneOffsetFromLocal ucal_getType ucal_getAttribute ucal_setAttribute ucal_setTimeZone
udat_open udat_close udat_format udat_parse udat_toPattern udat_getSymbols udat_countSymbols udat_setCalendar
unum_open unum_close unum_formatDouble unum_formatInt64 unum_parseDouble unum_setTextAttribute
uregex_open uregex_close uregex_setText uregex_find uregex_start
""".split()
# Public headers whose functions ICU compiles only when asked: icuplug.h (the uplug_* plug-in API, UCONFIG_ENABLE_PLUGINS, off by default and in
# Windows' own ICU, which has no plug-in loader either).
HEADERS_SKIPPED = {"icuplug.h"}
# The system DLLs Wine ships for 64-bit programs that these DLLs may import.
ALLOWED = {"kernel32.dll", "advapi32.dll"}
FORBIDDEN_PREFIX = ("libc++", "libunwind", "libwinpthread", "libstdc++", "libgcc", "icudt")


def load_pe():
    spec = importlib.util.spec_from_file_location("check_farm_imports", ROOT / "tools/check-farm-imports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def exports(pe):
    """(DLL name in the export directory, exported names) of a PE file."""
    rva, _size = pe.directories[0] if pe.directories else (0, 0)
    if not rva:
        return None, set()
    at = pe.offset(rva, 40)
    name = pe.name(struct.unpack_from("<I", pe.data, at + 12)[0])
    count = struct.unpack_from("<I", pe.data, at + 24)[0]
    names_rva = struct.unpack_from("<I", pe.data, at + 32)[0]
    out = set()
    base = pe.offset(names_rva, 4 * count)
    for i in range(count):
        name_rva = struct.unpack_from("<I", pe.data, base + 4 * i)[0]
        p = pe.offset(name_rva)
        end = pe.data.find(b"\0", p, p + 4096)
        out.add(pe.data[p:end].decode("latin-1"))
    return name, out


def header_functions(icu_source, role):
    """Every C function the public headers of one library declare: U_CAPI <type> U_EXPORT2 <name>(  (the type may span lines)."""
    folder = Path(icu_source) / ("common" if role == "uc" else "i18n") / "unicode"
    pattern = re.compile(r"U_CAPI\b[^;{}()]*?\bU_EXPORT2\s+(\w+)\s*\(")
    names = set()
    for header in sorted(folder.glob("*.h")):
        if header.name in HEADERS_SKIPPED:
            continue
        text = header.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)      # comments hold example calls
        text = re.sub(r"//[^\n]*", " ", text)
        names |= set(pattern.findall(text))
    return names


def is_arm64ec(pe):
    """True for an ARM64EC image: AMD64 in the file header and CHPE metadata in the load configuration."""
    if pe.machine != 0x8664 or len(pe.directories) <= 10:
        return False
    rva, size = pe.directories[10]
    if not rva or size < 0xD0:
        return False
    return struct.unpack_from("<Q", pe.data, pe.offset(rva + 0xC8, 8))[0] != 0


def role_of(path):
    base = Path(path).name.lower()
    for role, stem in (("uc", "icuuc"), ("in", "icuin")):
        if base in (stem + ".dll", stem + "-optin.dll"):
            return role, stem + ".dll"
    return None, None


def problems(path, machine="arm64ec", icu_source=None):
    role, dll_name = role_of(path)
    if role is None:
        return ["the file name is neither icuuc[-optin].dll nor icuin[-optin].dll"]
    data = Path(path).read_bytes()
    mod = load_pe()
    try:
        pe = mod.PE(data)
    except (mod.PEError, struct.error) as error:
        return ["not a readable PE file (%s)" % error]
    bad = []
    try:
        flavour = "arm64ec" if is_arm64ec(pe) else ("x86_64" if pe.machine == 0x8664 else "machine %#x" % pe.machine)
    except (mod.PEError, struct.error):
        flavour = "unreadable load configuration"
    if flavour != machine:
        bad.append("is %s, not %s (ARM64EC: machine 0x8664 in the header and CHPE metadata in the load configuration)" % (flavour, machine))
    if struct.unpack_from("<H", data, struct.unpack_from("<I", data, 0x3C)[0] + 24)[0] != 0x20B:
        bad.append("not a PE32+ (64-bit) image")
    try:
        name, have = exports(pe)
    except (mod.PEError, struct.error) as error:
        return bad + ["the export directory is unreadable (%s)" % error]
    if name != dll_name:
        bad.append("the export directory names the DLL %r, not %r (the app links it as %s)" % (name, dll_name, dll_name))
    want = QT_COMMON if role == "uc" else QT_I18N
    missing = sorted(set(want) - have)
    if missing:
        bad.append("does not export the ICU names Qt 6 uses: %s" % ", ".join(missing))
    versioned = sorted(n for n in have if not n.startswith("_Z") and n.endswith("_" + ICU_MAJOR))
    if versioned:
        bad.append("exports versioned ICU names (renaming is not off): %s" % ", ".join(versioned[:8]))
    if icu_source:
        declared = header_functions(icu_source, role)
        absent = sorted(declared - have)
        if not declared:
            bad.append("found no U_CAPI functions in the headers of %s: is --icu-source the tarball's source/ directory?" % icu_source)
        if absent:
            bad.append("%d public C functions of the %s headers are not exported (renamed or left out): %s"
                       % (len(absent), "common" if role == "uc" else "i18n", ", ".join(absent[:12])))
    if struct.unpack_from("<I", data, struct.unpack_from("<I", data, 0x3C)[0] + 8)[0] != 0:
        bad.append("the PE time stamp is not 0 (link with -Wl,--no-insert-timestamp: the build is reproducible)")
    imports = pe.imports() | pe.imports(delayed=True)
    allowed = set(ALLOWED) | ({"icuuc.dll"} if role == "in" else set())
    extra = sorted(i for i in imports if i not in allowed and not i.startswith("api-ms-win-crt-"))
    if extra:
        bad.append("imports beyond kernel32, advapi32%s and the UCRT sets: %s" % (", icuuc.dll" if role == "in" else "", extra))
    if any(i.startswith(FORBIDDEN_PREFIX) for i in imports):
        bad.append("imports a runtime or data DLL the app does not ship: %s" % sorted(i for i in imports if i.startswith(FORBIDDEN_PREFIX)))
    if role == "in" and "icuuc.dll" not in imports:
        bad.append("does not import icuuc.dll")
    holds_data = DATA_PREFIX + b"root.res\0" in data
    if role == "uc":
        if not holds_data:
            bad.append("carries no ICU data (%sroot.res is not inside)" % DATA_PREFIX.decode())
        else:
            for item in (b"en.res", b"coll/root.res", b"coll/ucadata.icu", b"cnvalias.icu", b"zoneinfo64.res", b"windowsZones.res",
                         b"brkitr/word.brk", b"nfkc_cf.nrm", b"ibm-5348_P100-1997.cnv", b"ibm-943_P15A-2003.cnv"):
                if DATA_PREFIX + item + b"\0" not in data:
                    bad.append("the data lacks %s" % (DATA_PREFIX + item).decode())
            for item in (b"de.res", b"zh.res", b"coll/zh.res", b"brkitr/cjdict.dict", b"unames.icu", b"translit/root.res"):
                if DATA_PREFIX + item + b"\0" in data:
                    bad.append("the data still has %s (the package is not trimmed)" % (DATA_PREFIX + item).decode())
    elif holds_data:
        bad.append("carries ICU data (it belongs in icuuc.dll only)")
    paths = sorted({m.group(0) for m in re.finditer(rb"/(?:Users|home)/([A-Za-z0-9_.-]+)/", data) if m.group(1) != b"runner"}
                   | {m for m in (b"/private/", b"/Volumes/", b"C:\\Users\\") if m in data})
    if paths:
        bad.append("the build machine's paths are inside (%s): the build maps them away with -ffile-prefix-map"
                   % ", ".join(x.decode("latin-1") for x in paths))
    low, high = (7 << 20, 16 << 20) if role == "uc" else (2 << 20, 8 << 20)
    if not low <= len(data) <= high:
        bad.append("%d bytes, outside %d-%d MB" % (len(data), low >> 20, high >> 20))
    return bad


def main(argv):
    machine, icu_source, files = "arm64ec", None, []
    it = iter(argv)
    for a in it:
        if a == "--machine":
            machine = next(it, "")
        elif a == "--icu-source":
            icu_source = next(it, "")
        elif a.startswith("--"):
            sys.exit(__doc__)
        else:
            files.append(a)
    if not files or machine not in MACHINES:
        sys.exit(__doc__)
    failed = False
    for f in files:
        found = problems(f, machine, icu_source)
        for p in found:
            print("::error::%s: %s" % (f, p))
        failed = failed or bool(found)
        if not found:
            role, dll = role_of(f)
            print("icu: %s is the %s %s (unversioned C API exports%s; ICU %s data %s; imports kernel32, advapi32, the UCRT sets%s)"
                  % (f, machine, dll, " incl. every public header function" if icu_source else "", ICU_MAJOR,
                     "inside" if role == "uc" else "not inside", " and icuuc.dll" if role == "in" else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
