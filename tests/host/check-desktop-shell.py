#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Windows desktop shows a console program's window and starts a context-menu "Open" in the program's folder (round 624,
Discord forum item 4: a player's WoW private server; the owner asked for the log to fix it).

The player's log: `[console] the console of L"C:\\WoTLK Emulator\\worldserver.exe" starts without a window` (MySQL's error and the
exit code 1 were invisible) and the context-menu start of worldserver.exe ran with cwd=L"C:\\windows\\" (the double-clicked one
had its own folder). The 64-bit shell32.dll is a prebuilt file, so the context-menu part is done at process creation.
  - app/Madeira/ContentView.swift DesktopGDISection.applyShell: the library's Desktop entry, and only it, sets
    MADEIRA_CONSOLE_WINDOW=1 and MADEIRA_DESKTOP_CWD=1; another library start clears them; = 0 in its Game config wins;
  - build/ntdll-unix/process_ios.c desktop_cwd_apply: with MADEIRA_DESKTOP_CWD=1, a program explorer.exe starts with the current
    directory C:\\windows\\ whose file is a drive path outside the Windows folder starts in its own folder ([desktop-cwd]).

Checks: the Swift switch logic (sources); desktop_cwd_apply and its two helpers cut from process_ios.c and compiled on the host,
driven with the player's case and every case that must stay as it is; the call site (before the phase stamp and before the child's
directory is opened; a capped line); CI runs this check.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
app = (root / "app/Madeira/ContentView.swift").read_text()
proc = (root / "build/ntdll-unix/process_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


enum = app[app.index("enum DesktopGDISection {"):]
enum = enum[:enum.index("\n}\n")]
shell = enum[enum.index("private static func applyShell(_ entry: LibraryEntry) -> String? {"):]
check("applyShell: both switches for the library's Desktop entry only",
      'if entry.desktop == true {\n            setenv("MADEIRA_CONSOLE_WINDOW", "1", 1)\n            setenv("MADEIRA_DESKTOP_CWD", "1", 1)' in shell
      and "runsInDesktop" not in shell)
check("applyShell: cleared only when this run's Desktop set them",
      'if shellByDesktop {\n            unsetenv("MADEIRA_CONSOLE_WINDOW")\n            unsetenv("MADEIRA_DESKTOP_CWD")' in shell)
check("apply runs it first, and the Desktop's [bcd] line names both switches",
      enum.index("let shell = applyShell(entry)") < enum.index('setenv("MADEIRA_GDI_SHARED_SECTION", "1", 1)')
      and "+ (shell ?? \"\")" in enum and "MADEIRA_CONSOLE_WINDOW=1, MADEIRA_DESKTOP_CWD=1" in shell)
others = []
for path in sorted((root / "app/Madeira").rglob("*")):
    if path.suffix not in (".swift", ".m", ".mm", ".c", ".h") or path.name == "ConfigCatalog.generated.swift":
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for name in ('"MADEIRA_CONSOLE_WINDOW"', '"MADEIRA_DESKTOP_CWD"'):
        n = text.count(name)
        if n != (2 if path.name == "ContentView.swift" else 0):
            others.append((path.name, name, n))
check("nothing else in the app sets or clears the two switches", not others, others)

ntc = proc[proc.index("NTSTATUS WINAPI NtCreateUserProcess( HANDLE *process_handle_ptr"):]
ntc = ntc[:ntc.index("\n}\n")]
call = "if (desktop_cwd_apply( params, NtCurrentTeb()->Peb->ProcessParameters, getenv( \"MADEIRA_DESKTOP_CWD\" ) ))"
check("the call: in NtCreateUserProcess, after the spawn swap, before the phase stamp and before the child's directory is opened",
      ntc.count(call) == 1 and ntc.index("spawn_swap_apply( swap, &path, params );") < ntc.index(call)
      < ntc.index('snprintf( pbuf, sizeof(pbuf), "spawn:%s"') and ntc.index(call) < ntc.index("unixdir = get_unix_curdir( params );")
      and "if (desktop_cwd_n++ < 16)" in ntc[ntc.index(call):ntc.index(call) + 600])


def cut(start):
    a = proc.index(start)
    return proc[a:proc.index("\n}\n", a) + 3]


helpers = cut("static int ios_image_name_is( const WCHAR *image, int image_len, const char *name )") + \
    cut("static int ios_wchar_prefix_ci( const WCHAR *s, int len, const char *prefix )") + \
    cut("static int desktop_cwd_apply( RTL_USER_PROCESS_PARAMETERS *params, const RTL_USER_PROCESS_PARAMETERS *own, const char *on )")
harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;   /* u"" literals are uint_least16_t, the same type here */
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING DosPath; void *Handle; } CURDIR;
typedef struct { CURDIR CurrentDirectory; UNICODE_STRING ImagePathName, CommandLine; } RTL_USER_PROCESS_PARAMETERS;
@HELPERS@
static void us( UNICODE_STRING *s, const WCHAR *w ) { size_t n = 0; while (w[n]) n++; s->Buffer = (WCHAR *)w; s->Length = n * 2; s->MaximumLength = n * 2 + 2; }
static void print_w( const WCHAR *w, int n ) { for (int i = 0; i < n; i++) putchar( (char)w[i] ); }
static void one( const char *label, const WCHAR *parent, const WCHAR *cwd, const WCHAR *image, const char *on )
{
  RTL_USER_PROCESS_PARAMETERS own, p;
  memset( &own, 0, sizeof own ); memset( &p, 0, sizeof p );
  us( &own.ImagePathName, parent ); us( &p.CurrentDirectory.DosPath, cwd ); us( &p.ImagePathName, image );
  int r = desktop_cwd_apply( &p, &own, on );
  printf( "%s r=%d cwd=", label, r );
  print_w( p.CurrentDirectory.DosPath.Buffer, p.CurrentDirectory.DosPath.Length / 2 );
  printf( " len=%u max=%u nul=%d\n", p.CurrentDirectory.DosPath.Length, p.CurrentDirectory.DosPath.MaximumLength,
          p.CurrentDirectory.DosPath.Buffer[p.CurrentDirectory.DosPath.Length / 2] == 0 );
}
int main( void )
{
  const WCHAR *ex = u"C:\\windows\\explorer.exe";
  one( "player", ex, u"C:\\windows\\", u"C:\\WoTLK Emulator\\worldserver.exe", "1" );
  one( "case", u"C:\\WINDOWS\\EXPLORER.EXE", u"c:\\WINDOWS\\", u"D:\\Games\\x.exe", "1" );
  one( "root", ex, u"C:\\windows\\", u"D:\\game.exe", "1" );
  one( "off-unset", ex, u"C:\\windows\\", u"C:\\WoTLK Emulator\\worldserver.exe", NULL );
  one( "off-0", ex, u"C:\\windows\\", u"C:\\WoTLK Emulator\\worldserver.exe", "0" );
  one( "off-10", ex, u"C:\\windows\\", u"C:\\WoTLK Emulator\\worldserver.exe", "10" );
  one( "parent-cmd", u"C:\\windows\\system32\\cmd.exe", u"C:\\windows\\", u"C:\\WoTLK Emulator\\worldserver.exe", "1" );
  one( "cwd-own", ex, u"C:\\Games\\", u"C:\\WoTLK Emulator\\worldserver.exe", "1" );
  one( "cwd-system32", ex, u"C:\\windows\\system32\\", u"C:\\WoTLK Emulator\\worldserver.exe", "1" );
  one( "image-windows", ex, u"C:\\windows\\", u"C:\\windows\\system32\\notepad.exe", "1" );
  one( "image-windows2", ex, u"C:\\windows\\", u"C:\\Windows\\regedit.exe", "1" );
  one( "image-nt", ex, u"C:\\windows\\", u"\\??\\C:\\x\\y.exe", "1" );
  one( "image-bare", ex, u"C:\\windows\\", u"y.exe", "1" );
  return 0;
}
'''
cc = shutil.which("cc")
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "h.c").write_text(harness.replace("@HELPERS@", helpers))
    b = subprocess.run([cc, "-std=c11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("desktop_cwd_apply and its helpers compile on the host", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        out = {l.split(" ", 1)[0]: l.split(" ", 1)[1] for l in subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()}
        check("the player's case: worldserver.exe from explorer's C:\\windows\\ starts in C:\\WoTLK Emulator\\ (NUL-terminated)",
              out.get("player") == r"r=1 cwd=C:\WoTLK Emulator\ len=36 max=38 nul=1", out.get("player"))
        check("any case of C:\\windows\\ and explorer.exe; a program in a drive root gets the root",
              out.get("case") == r"r=1 cwd=D:\Games\ len=18 max=20 nul=1" and out.get("root") == r"r=1 cwd=D:\ len=6 max=8 nul=1", out)
        same = ["off-unset", "off-0", "off-10", "parent-cmd", "cwd-own", "cwd-system32", "image-windows", "image-windows2", "image-nt", "image-bare"]
        check("unchanged: the switch unset, 0 or anything but 1; another parent; another current directory; a program in the Windows "
              "folder; an NT path; a bare name",
              all(out.get(k, "").startswith("r=0 ") for k in same), {k: out.get(k) for k in same})

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-desktop-shell.py" in wf)
print("PASS: desktop shell" if ok else "FAIL: desktop shell")
raise SystemExit(0 if ok else 1)
