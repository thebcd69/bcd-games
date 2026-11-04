#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_USD_OSVERSION: the shared data page's Windows version block as upstream Wine's wineboot writes it (round 630, agent U S10).

SHINOBI: Art of Vengeance (5XMBFM), Football Manager 27 (88YTP3), Football Manager 2024 (DQY7RZ), RAIDOU Remastered, FINAL FANTASY
RESONANCE DEMO and Persona 3 Reload read KUSER_SHARED_DATA's version block (+0x260 NtBuildNumber ... +0x273) thousands of times
([subfloor] ml956) before they stop inside their own protection code. Upstream Wine fills that block in wineboot.exe
(programs/wineboot/wineboot.c, create_user_shared_data); BCD never starts wineboot.exe (env_ios.c's run_wineboot calls
virtual_init_user_shared_data instead), so the block reads 0.0.0 while the PEB says 10.0.19045. The key, off by default, writes
wineboot's values for that Windows 10.

Checks:
  - virtual_ios.c's virtual_init_user_shared_data has the block once, inside WINE_IOS, after the processor counts and before the
    architecture and CPU features; only the exact value 1 turns it on; it writes exactly wineboot's seven fields and prints one
    [usd-ver] line; wineboot.c (when the wine tree is here) writes the same seven fields, and env_ios.c's run_wineboot still calls
    virtual_init_user_shared_data in place of wineboot.exe;
  - the block, cut from the source and built on the host over a page laid out at the offsets of Wine's wdm.h: with the key unset,
    empty, 0, 11, " 1", "1 " or true no byte of the page changes and nothing is printed; with 1 the seven fields hold 19045, NtProductWinNt,
    TRUE, 10, 0, VER_SUITE_SINGLEUSERTS and "C:\\windows", every other byte is unchanged, and one line says so;
  - virtual_ios.c compiles for iOS (syntax only, pinned wine headers; skipped without a configured wine tree);
  - the catalog lists the key (default 0), the list policy allows it with the values 0 and 1, and CI runs this check.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
virt = (root / "build/ntdll-unix/virtual_ios.c").read_text()
env_src = (root / "build/ntdll-unix/env_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


fn = virt[virt.index("void virtual_init_user_shared_data(void)\n{\n"):]
fn = fn[:fn.index("\n}\n") + 3]
check("virtual_init_user_shared_data reads the key once, nothing else of the file does",
      fn.count('getenv( "MADEIRA_USD_OSVERSION" )') == 1 and virt.count("MADEIRA_USD_OSVERSION") == fn.count("MADEIRA_USD_OSVERSION"))
start = fn.index("#ifdef WINE_IOS\n    /* madeira-bcd (round 630, agent U S10;")
block = fn[start:fn.index("#endif\n", start) + len("#endif\n")]
check("the block sits after the processor counts and before the architecture and CPU features, inside WINE_IOS",
      fn.index("data->ActiveGroupCount      = 1;") < start < fn.index("switch (native_machine)") < fn.index("init_shared_data_cpuinfo( data );"))
check("only the exact value 1 turns it on", "if (usd_ver && usd_ver[0] == '1' && !usd_ver[1])" in block)
fields = ["data->NtBuildNumber      = 19045;", "data->NtProductType      = NtProductWinNt;", "data->ProductTypeIsValid = TRUE;",
          "data->NtMajorVersion     = 10;", "data->NtMinorVersion     = 0;", "data->SuiteMask          = VER_SUITE_SINGLEUSERTS;",
          "memcpy( data->NtSystemRoot, rootW, sizeof(rootW) );"]
writes = [l.strip() for l in block.split("\n") if re.match(r"\s*(data->\w+\s*=|memcpy\()", l)]
check("it writes exactly wineboot's seven fields, each once, and prints one [usd-ver] line",
      writes == fields and block.count("dprintf( 2,") == 1 and "[usd-ver] r630 MADEIRA_USD_OSVERSION=1" in block, writes)
run_wb = env_src[env_src.index("static void run_wineboot( WCHAR *env, SIZE_T size )"):]
run_wb = run_wb[:run_wb.index("#endif")]
check("env_ios.c's run_wineboot still calls virtual_init_user_shared_data in place of wineboot.exe (iOS)",
      "virtual_init_user_shared_data();" in run_wb and "return;" in run_wb)

wine = Path(os.environ.get("WINE_SRC", root / "wine"))
if not (wine / "programs/wineboot/wineboot.c").is_file():
    wine = Path.home() / "madeira-bcd/wine"
wb = wine / "programs/wineboot/wineboot.c"
wdm = wine / "include/ddk/wdm.h"
offsets = {"NtSystemRoot": 0x30, "NtBuildNumber": 0x260, "NtProductType": 0x264, "ProductTypeIsValid": 0x268,
           "NtMajorVersion": 0x26c, "NtMinorVersion": 0x270, "SuiteMask": 0x2d0}
if wb.is_file() and wdm.is_file():
    cud = wb.read_text()
    cud = cud[cud.index("static void create_user_shared_data(void)"):]
    cud = cud[:cud.index("\n}\n")]
    named = sorted(set(re.findall(r"data->(\w+)\s*=", cud)) | ({"NtSystemRoot"} if "wcscpy( data->NtSystemRoot" in cud else set()))
    check("upstream wineboot writes the same seven fields", named == sorted(offsets), named)
    w = wdm.read_text()
    found = {}
    for name in offsets:
        m = re.search(r"\b%s(\[\d+\])?;\s*/\* (0x[0-9a-f]+) \*/" % name, w)
        found[name] = int(m.group(2), 16) if m else None
    check("the harness's offsets are wdm.h's", found == offsets, found)
else:
    print("skip the wineboot/wdm.h comparison: no wine tree (set WINE_SRC)")

body = block[block.index("    {\n        const char *usd_ver"):block.rindex("    }\n") + 6]
harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
typedef uint16_t WCHAR;
typedef uint32_t ULONG;
typedef unsigned char BOOLEAN;
#define TRUE 1
#define VER_SUITE_SINGLEUSERTS 0x00000100
typedef enum { NtProductWinNt = 1, NtProductLanManNt, NtProductServer } NT_PRODUCT_TYPE;
typedef struct {
    unsigned char pad0[0x30];
    WCHAR NtSystemRoot[260];
    unsigned char pad1[0x260 - 0x30 - 520];
    ULONG NtBuildNumber;
    NT_PRODUCT_TYPE NtProductType;
    BOOLEAN ProductTypeIsValid;
    unsigned char pad2[3];
    ULONG NtMajorVersion;
    ULONG NtMinorVersion;
    unsigned char pad3[0x2d0 - 0x274];
    ULONG SuiteMask;
    unsigned char pad4[0x1000 - 0x2d4];
} KUSER_SHARED_DATA;
_Static_assert(offsetof(KUSER_SHARED_DATA, NtSystemRoot) == 0x30, "root");
_Static_assert(offsetof(KUSER_SHARED_DATA, NtBuildNumber) == 0x260, "build");
_Static_assert(offsetof(KUSER_SHARED_DATA, NtProductType) == 0x264, "product");
_Static_assert(offsetof(KUSER_SHARED_DATA, ProductTypeIsValid) == 0x268, "valid");
_Static_assert(offsetof(KUSER_SHARED_DATA, NtMajorVersion) == 0x26c, "major");
_Static_assert(offsetof(KUSER_SHARED_DATA, NtMinorVersion) == 0x270, "minor");
_Static_assert(offsetof(KUSER_SHARED_DATA, SuiteMask) == 0x2d0, "suite");
static void init(KUSER_SHARED_DATA *data)
{
''' + body + r'''
}
int main(int argc, char **argv)
{
    static KUSER_SHARED_DATA page, before;
    for (size_t i = 0; i < sizeof(page); i++) ((unsigned char *)&page)[i] = (unsigned char)(i * 7 + 3);
    memcpy(&before, &page, sizeof(page));
    if (argc > 1) setenv("MADEIRA_USD_OSVERSION", argv[1], 1); else unsetenv("MADEIRA_USD_OSVERSION");
    init(&page);
    size_t changed = 0, outside = 0;
    for (size_t i = 0; i < sizeof(page); i++) {
        if (((unsigned char *)&page)[i] == ((unsigned char *)&before)[i]) continue;
        changed++;
        int in = (i >= 0x30 && i < 0x30 + 22) || (i >= 0x260 && i < 0x269) || (i >= 0x26c && i < 0x274) || (i >= 0x2d0 && i < 0x2d4);
        if (!in) outside++;
    }
    char root[16] = {0};
    for (int i = 0; i < 15 && page.NtSystemRoot[i]; i++)
        root[i] = page.NtSystemRoot[i] >= 0x20 && page.NtSystemRoot[i] < 0x7f ? (char)page.NtSystemRoot[i] : '?';
    printf("changed=%zu outside=%zu build=%u product=%u valid=%u major=%u minor=%u suite=0x%x root=%s term=%u\n", changed, outside,
           page.NtBuildNumber, (unsigned)page.NtProductType, page.ProductTypeIsValid, page.NtMajorVersion, page.NtMinorVersion,
           page.SuiteMask, root, (unsigned)page.NtSystemRoot[10]);
    return 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("skip the compiled part: no C compiler")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-usd-ver-") as d:
        d = Path(d)
        (d / "t.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(d / "t.c"), "-o", str(d / "t")], capture_output=True, text=True)
        check("the block builds on the host over a page at wdm.h's offsets (warnings are errors)", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            for v in (None, "", "0", "11", " 1", "1 ", "true"):
                r = subprocess.run([str(d / "t")] + ([] if v is None else [v]), capture_output=True, text=True)
                check("key %r: no byte of the page changes, nothing is printed" % (v,),
                      r.stdout.startswith("changed=0 outside=0 ") and r.stderr == "", r.stdout + r.stderr)
            r = subprocess.run([str(d / "t"), "1"], capture_output=True, text=True)
            out = r.stdout.strip()
            check("key 1: the seven fields hold wineboot's Windows 10 values, nothing else changes",
                  re.match(r"changed=\d+ outside=0 build=19045 product=1 valid=1 major=10 minor=0 suite=0x100 root=C:\\windows term=0$", out)
                  is not None, out)
            check("key 1: one [usd-ver] line says so", r.stderr.count("[usd-ver] r630 MADEIRA_USD_OSVERSION=1") == 1
                  and "build=19045 major=10 minor=0 product=1 suite=0x100" in r.stderr, r.stderr)

config = wine / "build-macos/include/config.h"
if shutil.which("xcrun") and config.exists():
    try:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ""
    if sdk:
        build = root / "build/ntdll-unix"
        flags = ["-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                 "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
                 "-include", str(config), "-include", str(build / "shims/wine_ios_exit.h"), f"-I{build / 'shims'}",
                 f"-I{wine / 'build-macos/dlls/ntdll'}", f"-I{wine / 'dlls/ntdll'}", f"-I{wine / 'dlls/ntdll/unix'}",
                 f"-I{wine / 'build-macos/include'}", f"-I{wine / 'include'}", "-D__WINESRC__", "-DLTC_NO_PROTOTYPES",
                 "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB",
                 "-DWINE_IOS=1", "-Dget_thread_context=ntdll_get_thread_context", "-Dset_thread_context=ntdll_set_thread_context"]
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *flags, str(build / "virtual_ios.c")], capture_output=True, text=True)
        check("virtual_ios.c compiles for iOS (syntax only, pinned wine headers)", r.returncode == 0, r.stderr[-2000:])
        check("...and the block adds no warning", "usd_ver" not in r.stderr and "rootW" not in r.stderr, r.stderr[-2000:])
    else:
        print("skip the iOS syntax check: no iphoneos SDK")
else:
    print("skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)")

cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_USD_OSVERSION, default off, from virtual_ios.c",
      re.search(r'"env\.MADEIRA_USD_OSVERSION": \{[^}]*"default": "0"[^}]*"sources": \["build/ntdll-unix/virtual_ios\.c"\]', cat, re.S) is not None)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy allows the key with the values 0 and 1",
      '"MADEIRA_USD_OSVERSION",' in pol and 'case "MADEIRA_USD_OSVERSION":' in pol
      and 'MADEIRA_USD_OSVERSION is 0 or 1' in pol)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-usd-osversion.py" in wf)
print("PASS: the shared page's Windows version block (MADEIRA_USD_OSVERSION)" if ok else "FAILED: MADEIRA_USD_OSVERSION")
sys.exit(0 if ok else 1)
