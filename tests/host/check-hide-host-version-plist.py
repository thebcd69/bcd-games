#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-hide-host-version-plist.py: Windows programs do not see the iOS host's SystemVersion.plist; no Wine runs.

Ren'Py 6.99 (Doki Doki Literature Club 45E18B, 86QH9P; Coming Out on Top NQZKVF) stopped at start-up: Python's platform.mac_ver()
found /System/Library/CoreServices/SystemVersion.plist through Wine's "absolute /path is a Unix path" rule and called os.uname(),
which Python for Windows lacks (agent O1 S14, round 630).

1. Applies the patch to a copy of the wine checkout's dlls/ntdll/unix/file.c ($WINE_SRC, ./wine or ~/madeira-bcd/wine): once,
   then "already patched"; the helper sits before get_nt_and_unix_names and the call in its \\??\\unix\\ branch, after the
   separators are turned and before find_drive_nt_root, returning "not found" with the name already handed to the caller.
2. Compiles the helper: that exact path (in any case) is hidden; its folder, its neighbours, a longer name and other host paths
   are not.
A host without a wine checkout says so and passes (CI and the Mac run it).
"""
from pathlib import Path
import os, subprocess, sys, tempfile, shutil

root = Path(__file__).resolve().parents[2]
patch = root / "tools/patch-wine-hide-host-version-plist.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check and applies the patch after the other file.c patches",
      "python3 tests/host/check-hide-host-version-plist.py\n          python3 tools/patch-wine-hide-host-version-plist.py wine/dlls/ntdll/unix/file.c" in wf
      and wf.index("tools/patch-wine-file-info-fstat.py wine/dlls") < wf.index("tools/patch-wine-hide-host-version-plist.py wine/dlls")
      < wf.index("bash build/ntdll-unix/build.sh"))

candidates = [Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, root / "wine", Path.home() / "madeira-bcd/wine"]
real = next((c / "dlls/ntdll/unix/file.c" for c in candidates if c and (c / "dlls/ntdll/unix/file.c").exists()), None)
if real is None:
    print("skip: no wine checkout here (CI and the Mac run this check)")
    print("PASS: host version file hidden (skipped here)" if ok else "FAILED: host version file")
    sys.exit(0 if ok else 1)

with tempfile.TemporaryDirectory(prefix="madeira-hide-plist-") as tmp:
    copy = Path(tmp) / "file.c"
    shutil.copy(real, copy)
    r1 = subprocess.run([sys.executable, str(patch), str(copy)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(patch), str(copy)], capture_output=True, text=True)
    check("the patch applies once, then says it is already in",
          ("applied to" in r1.stdout or "already patched" in r1.stdout) and "already patched" in r2.stdout, r1.stdout + r1.stderr)
    src = copy.read_text()
    fn = src[src.index("NTSTATUS get_nt_and_unix_names( OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt_name,"):]
    fn = fn[:fn.index("\n}\n")]
    branch = fn[fn.index("unix_prefixW, ARRAY_SIZE(unix_prefixW) ))"):fn.index("    else\n    {")]
    check("the call is in the \\??\\unix\\ branch, after the separators are turned and before find_drive_nt_root, with the name handed over",
          branch.index("*unix_name_ret = unix_name = malloc(") < branch.index("unix_name[i] = '/';")
          < branch.index("if (ios_hidden_host_file( unix_name, lenA )) return STATUS_OBJECT_NAME_NOT_FOUND;")
          < branch.index("status = find_drive_nt_root( unix_name, lenA, &buffer, disposition );"))
    check("the helper is defined once, before get_nt_and_unix_names", src.count("static BOOL ios_hidden_host_file(") == 1
          and src.index("static BOOL ios_hidden_host_file(") < src.index("NTSTATUS get_nt_and_unix_names( OBJECT_ATTRIBUTES"))
    start = src.index("static BOOL ios_hidden_host_file(")
    helper = src[start:src.index("\n}\n", start) + 3]
    code = "#include <string.h>\n#include <strings.h>\n#include <assert.h>\n#include <stdio.h>\ntypedef int BOOL; typedef unsigned int ULONG;\n" + helper + r'''
static BOOL h( const char *s ) { return ios_hidden_host_file( s, (ULONG)strlen( s ) ); }
int main( void )
{
    assert( h( "/System/Library/CoreServices/SystemVersion.plist" ) );
    assert( h( "/system/library/coreservices/systemversion.plist" ) );
    assert( !h( "/System/Library/CoreServices/Info.plist" ) && !h( "/System/Library/CoreServices" ) && !h( "/System/Library/CoreServices/" ) );
    assert( !h( "/System/Library/CoreServices/SystemVersion.plist.bak" ) && !h( "/System/Library/CoreServices/SystemVersion.plis" ) );
    assert( !h( "/etc/hosts" ) && !h( "" ) && !h( "/private/var/mobile/SystemVersion.plist" ) );
    puts( "ok: only the host's SystemVersion.plist is hidden" );
    return 0;
}
'''
    src_c = Path(tmp) / "t.c"
    src_c.write_text(code)
    exe = Path(tmp) / "t"
    b = subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-o", str(exe), str(src_c)],
                       capture_output=True, text=True)
    check("the helper builds: " + b.stderr[-600:], b.returncode == 0)
    if b.returncode == 0:
        t = subprocess.run([str(exe)], capture_output=True, text=True)
        print(t.stdout, end="")
        check("only that file is hidden: " + (t.stderr[-300:] or "all cases"), t.returncode == 0)

print("PASS: Windows programs do not see the host's SystemVersion.plist; every other path as before" if ok else "FAILED: host version file")
sys.exit(0 if ok else 1)
