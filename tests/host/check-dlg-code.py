#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The code that showed a message box ([dlg-code]; env.MADEIRA_DIALOG_CODE_DUMP = 1, default off, log only; build 641).

The Last of Us Part I (builds 638 to 640) destroys its Direct3D 12 device and shows "a GPU supporting D3D_FEATURE_LEVEL_12_0 or higher is
required" after reading BCD's answers; the log does not say which answer it rejects. With the key on, the first two message boxes of a start
print the game's saved registers, its stack, the return addresses into the program and the code before them (virtual_ios.c,
madeira_dialog_code_dump, called from win32u's window naming in driver_ios.c).

Checks:
  - build/ntdll-unix/ios_dlg_code.h compiled on the host: every x86-64 call form ends a return address, data words and other
    instructions do not;
  - virtual_ios.c: the key is read once and exactly "1" turns it on; nothing runs with it off; at most two boxes; the reads are safe ones;
  - driver_ios.c: the hook sits after the [win-name] line, only for a Static child of a #32770 dialog, through a weak symbol;
  - the key is in the settings catalog and the list policy (0 or 1); tools/jumbo-code-disasm.py reads the rows;
  - both files still compile for iOS (syntax only, pinned Wine headers) when the Wine tree is there.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


# ---------------------------------------------------------------- the call-site test, on the host
header = root / "build/ntdll-unix/ios_dlg_code.h"
check("ios_dlg_code.h exists", header.exists())
calls = {
    "e8 rel32": "9090" + "e8" + "d2792620",
    "ff 15 rip": "90" + "ff15" + "f9bcc501",
    "ff 90 reg+disp32": "90" + "ff90" + "88000000",
    "ff 94 sib disp32": "ff94" + "24" + "88000000",
    "ff 50 reg+disp8": "90909090" + "ff50" + "08",
    "ff 54 sib disp8": "909090" + "ff54" + "2408",
    "ff d0 call rax": "9090909090" + "ffd0",
    "41 ff d3 call r11": "90909090" + "41ffd3",
    "ff 10 call [rax]": "9090909090" + "ff10",
    "ff 14 sib call [base+index]": "90909090" + "ff14" + "c8",
}
not_calls = {
    "plain data": "00000000000000",
    "a pointer": "4010260100000000"[:14],
    "mov rax, rbx; ret": "90909090" + "4889d8",
    "jmp rel32": "9090" + "e9d2792620",
    "ff e0 jmp rax": "9090909090" + "ffe0",
    "ff 25 jmp [rip]": "90" + "ff25" + "f9bcc501",
    "int3s": "cccccccccccccc",
}
cc = shutil.which("cc") or shutil.which("clang")
if cc and header.exists():
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        exe = Path(tmp) / "t"
        rows = []
        for name, hx in list(calls.items()) + list(not_calls.items()):
            b = bytes.fromhex(hx)
            assert len(b) == 7, (name, hx)
            rows.append('    { "%s", { %s }, %d },' % (name, ", ".join("0x%02x" % x for x in b), 1 if name in calls else 0))
        c.write_text('#include <stdio.h>\n#include "ios_dlg_code.h"\n'
                     "struct t { const char *name; unsigned char b[7]; int want; };\n"
                     "static const struct t cases[] = {\n" + "\n".join(rows) + "\n};\n"
                     "int main(void) { int bad = 0; for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {\n"
                     "  int got = ios_dlg_call_site( cases[i].b ); if (got != cases[i].want) { printf(\"%s: got %d\\n\", cases[i].name, got); bad = 1; } }\n"
                     "  return bad; }\n")
        b = subprocess.run([cc, "-Wall", "-Werror", "-I", str(header.parent), "-o", str(exe), str(c)], capture_output=True, text=True)
        check("ios_dlg_code.h compiles on the host with -Wall -Werror", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check("every call form ends a return address (%d), and data, jumps and other instructions do not (%d)"
                  % (len(calls), len(not_calls)), r.returncode == 0, r.stdout)
else:
    print("skip the host run: no C compiler")

# ---------------------------------------------------------------- virtual_ios.c
text = (root / "build/ntdll-unix/virtual_ios.c").read_text(errors="replace")
en = text[text.find("static int ios_dlg_code_dump_enabled(void)"):]
en = en[:en.find("\n}\n") + 3]
check("MADEIRA_DIALOG_CODE_DUMP is read once and is on only when it is exactly 1",
      'getenv( "MADEIRA_DIALOG_CODE_DUMP" )' in en and "cached = (s && *s == '1' && !s[1]) ? 1 : 0;" in en, en)
dump = text[text.find("void madeira_dialog_code_dump( const char *text )"):]
dump = dump[:dump.find("\n}\n") + 3]
check("the dump returns at once with the key off, and stops after two boxes",
      "if (!ios_dlg_code_dump_enabled() || !teb || calls >= 2) return;" in dump
      and dump.find("ios_dlg_code_dump_enabled()") < dump.find("dprintf("), dump[:600])
check("it reads the emulator's saved state as [jumbo-code] does (TEB+0x1788, state +0x30, RIP 0x18, RSP 0x40)",
      "cpuarea = *(void **)((char *)teb + 0x1788);" in dump and "st = *(void **)((char *)cpuarea + 0x30);" in dump
      and "rip = ((uint64_t *)st)[0x18 / 8];" in dump and "rsp = ((uint64_t *)st)[0x40 / 8];" in dump)
check("every guest read goes through ios_safe_read / ios_safe_read64 (no plain dereference of a guest address)",
      dump.count("ios_safe_read64(") == 2 and "ios_safe_read( (uintptr_t)(w - 7), b, 7 )" in dump
      and "ios_safe_read( (uintptr_t)(base + 0x3c), &lfanew, 4 )" in dump and "*(uint64_t *)a" not in dump)
check("the scan keeps to the program's image, the first 64 KB of the stack, 24 frames and 8 code windows",
      "if (w < base + 0x1000 || w >= end) continue;" in dump and "a < rsp + 0x10000 && nseen < 24" in dump
      and "nret < 8" in dump and "ios_dlg_call_site( b )" in dump)
check("ios_dlg_code.h is included by virtual_ios.c", '#include "ios_dlg_code.h"' in text)

# ---------------------------------------------------------------- driver_ios.c
drv = (root / "build/win32u-unix/driver_ios.c").read_text(errors="replace")
check("driver_ios.c declares the dump weak", "extern void madeira_dialog_code_dump( const char *text ) __attribute__((weak));" in drv)
name = drv.find('dprintf( 2, "[win-name] #%u hwnd=%p class=\'%s\' text=\\"%s\\" rev=ml853\\n", n, hwnd, cls, txt );')
hook = drv.find("if (madeira_dialog_code_dump && txt[0] && !strcmp( cls, \"Static\" ))")
call = drv.find("madeira_dialog_code_dump( txt );")
check("the hook follows the [win-name] line and calls only for a Static child of a #32770 dialog",
      0 < name < hook < call and call - hook < 900
      and "static const WCHAR dialogW[] = {'#','3','2','7','7','0'};" in drv[hook:call]
      and "NtUserGetAncestor( hwnd, GA_PARENT )" in drv[hook:call], (name, hook, call))

# ---------------------------------------------------------------- the key is known to the settings and to downloaded lists; the tool
cat = (root / "build/tools/gen-config-catalog.py").read_text()
gen = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("settings catalog entry env.MADEIRA_DIALOG_CODE_DUMP (generator and generated file)",
      '"env.MADEIRA_DIALOG_CODE_DUMP"' in cat and 'ConfigOption(key: "env.MADEIRA_DIALOG_CODE_DUMP"' in gen)
check("a downloaded list may set MADEIRA_DIALOG_CODE_DUMP (allowedEnvNames), 0 or 1",
      '"MADEIRA_DIALOG_CODE_DUMP"' in pol.split("static let allowedEnvNames")[1].split("]\n")[0]
      and "MADEIRA_DIALOG_CODE_DUMP is 0 or 1" in pol)
tool = subprocess.run([sys.executable, str(root / "tools/jumbo-code-disasm.py"), "--selftest"], capture_output=True, text=True)
check("tools/jumbo-code-disasm.py reads [dlg-code] rows (its selftest)", tool.returncode == 0, tool.stdout + tool.stderr)

# ---------------------------------------------------------------- both files still compile for iOS (when the wine headers exist)
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
config = wine / "build-macos/include/config.h"
if shutil.which("xcrun") and config.exists():
    try:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ""
    if sdk:
        nt = root / "build/ntdll-unix"
        common = ["-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                  "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion", "-D__WINESRC__",
                  "-D_ACRTIMP=", "-DWINBASEAPI=", '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-Wall"]
        v = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *common, "-include", str(config),
                            "-include", str(nt / "shims/wine_ios_exit.h"), f"-I{nt / 'shims'}",
                            f"-I{wine / 'build-macos/dlls/ntdll'}", f"-I{wine / 'dlls/ntdll'}", f"-I{wine / 'dlls/ntdll/unix'}",
                            f"-I{wine / 'build-macos/include'}", f"-I{wine / 'include'}", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE",
                            "-D_NTSYSTEM_", '-DBINDIR="/usr/local/bin"', '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"',
                            str(nt / "virtual_ios.c")], capture_output=True, text=True)
        check("virtual_ios.c compiles for iOS (syntax only, pinned wine headers), no warning in the new code",
              v.returncode == 0 and "dlg" not in v.stderr, v.stderr[-2000:])
        w32 = root / "build/win32u-unix"
        d = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *common, "-fvisibility=hidden", "-fno-stack-protector",
                            "-include", str(w32 / "config_ios.h"), "-include", str(nt / "shims/wine_ios_exit.h"), f"-I{w32}",
                            f"-I{wine / 'build-macos/include'}", f"-I{nt / 'shims'}", f"-I{wine / 'build-macos/dlls/win32u'}",
                            f"-I{wine / 'dlls/win32u'}", f"-I{wine / 'include'}", "-D_WIN32U_",
                            "-D__wine_unix_lib_init=win32u_unix_lib_init", "-USONAME_LIBFREETYPE", "-USONAME_LIBFONTCONFIG",
                            "-USONAME_LIBEGL", "-USONAME_LIBVULKAN", "-USONAME_LIBGNUTLS", "-UHAVE_FT2BUILD_H",
                            str(w32 / "driver_ios.c")], capture_output=True, text=True)
        check("driver_ios.c compiles for iOS (syntax only, pinned wine headers), no warning in the new code",
              d.returncode == 0 and "dialog" not in d.stderr, d.stderr[-2000:])
    else:
        print("skip the iOS syntax check: no iphoneos SDK")
else:
    print("skip the iOS syntax check: no wine headers (set WINE_SRC)")

print("PASS: dialog code dump" if ok else "FAILED: dialog code dump")
sys.exit(0 if ok else 1)
