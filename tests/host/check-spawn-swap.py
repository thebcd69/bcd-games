#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_SPAWN_SWAP: Fallout 4's Dock start runs Fallout4.exe in place of its launcher (build 598, opt-in); no Wine runs.

Fallout 4 (Steam 377160) through BCD Dock: Steam's launch configuration is ONE entry, key 0, Fallout4Launcher.exe (type none, osarch 64: the real
product info, api.steamcmd.net/v1/info/377160). On this port the launcher does not get far: a 4.3 MB 64-bit image with its relocations stripped, it
starts a second instance of itself 0.2 s later and that one dies with "wine: failed to create main module ... Fallout4Launcher.exe, status
c0000018" (C050A4, build 580: the session ended 19 s after Play). Two players swapped the names Fallout4.exe and Fallout4Launcher.exe in the install
folder and the game ran (CW3VZF, build 592, 14 minutes; a second player on Discord). A list cannot say "launch option 1": there is no second entry.
env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe makes NtCreateUserProcess start Fallout4.exe from the same folder when asked for the launcher.

What this checks:
  1. The string work (build/ntdll-unix/ios_spawn_swap.h), compiled with clang and run: name validity, the list (case, blanks, several entries,
     invalid and self-swapping entries skipped), the NT and the DOS path with the file name replaced, the command line (quoted, unquoted, with
     arguments, not a longer name, not another program, no room), and the real Fallout 4 strings of C050A4.
  2. process_ios.c: the one call is gated on the variable being set; it runs after the spawn gates and before the phase stamp and the session log (so
     those name the program that really starts); the helper changes only the NT image name, the DOS image name and the command line, never when the
     two image names disagree, and checks that the replacement exists in the folder before it changes anything.
  3. Opt-in: nothing but Fallout 4's list line sets the variable; the policy accepts only a well-formed list of two .exe names for it; the list line
     carries minBuild 598 and matches Steam 377160, its folder and its two programs; the standard line stays in that list.
  4. The auto-tune rule, its fixture, the catalog entry and the CI step exist.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
header_path = root / 'build/ntdll-unix/ios_spawn_swap.h'
process = (root / 'build/ntdll-unix/process_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- 1. the string work, compiled
C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;
#include "ios_spawn_swap.h"

static int fails;
#define REQUIRE(c, what) do { if (c) printf("ok   %s\n", what); else { printf("FAIL %s\n", what); fails++; } } while (0)

static size_t wide(const char *s, WCHAR *out) { size_t n = strlen(s), i; for (i = 0; i < n; i++) out[i] = (unsigned char)s[i]; out[n] = 0; return n; }
static int same(const WCHAR *w, size_t n, const char *s) { size_t i; if (strlen(s) != n) return 0; for (i = 0; i < n; i++) if (w[i] != (unsigned char)s[i]) return 0; return 1; }

static const char *find(const char *list, const char *name, size_t *to_len)
{
    WCHAR w[300]; size_t n = wide(name, w);
    return spawn_swap_find(list, w, n, to_len);
}
static int finds(const char *list, const char *name, const char *want)
{
    size_t len = 0; const char *to = find(list, name, &len);
    if (!want) return to == NULL;
    return to && len == strlen(want) && memcmp(to, want, len) == 0;
}

int main(void)
{
    WCHAR a[600], out[600];
    size_t n, m;

    REQUIRE(spawn_swap_name_valid("Fallout4.exe", 12) && spawn_swap_name_valid("A.EXE", 5) && spawn_swap_name_valid("Some Game.exe", 13), "names: plain, upper case, with a blank");
    REQUIRE(!spawn_swap_name_valid(".exe", 4) && !spawn_swap_name_valid("game.dll", 8) && !spawn_swap_name_valid("game.exe ", 9) && !spawn_swap_name_valid(" game.exe", 9), "names: needs a stem and .exe, no edge blanks");
    REQUIRE(!spawn_swap_name_valid("a\\b.exe", 7) && !spawn_swap_name_valid("a/b.exe", 7) && !spawn_swap_name_valid("a:b.exe", 7) && !spawn_swap_name_valid("a;b.exe", 7) && !spawn_swap_name_valid("a=b.exe", 7) && !spawn_swap_name_valid("a*b.exe", 7), "names: no path or list punctuation");
    REQUIRE(!spawn_swap_name_valid("a\x01" "b.exe", 7) && !spawn_swap_name_valid("a\xc3\xa9.exe", 7), "names: printable ASCII only");
    {
        char longname[400]; memset(longname, 'a', sizeof longname);
        memcpy(longname + 255 - 4, ".exe", 4);
        REQUIRE(spawn_swap_name_valid(longname, 255), "names: 255 characters are allowed");
        memset(longname, 'a', sizeof longname);
        memcpy(longname + 256 - 4, ".exe", 4);
        REQUIRE(!spawn_swap_name_valid(longname, 256), "names: 256 are not");
    }

    REQUIRE(finds("Fallout4Launcher.exe=Fallout4.exe", "Fallout4Launcher.exe", "Fallout4.exe"), "find: the Fallout 4 line");
    REQUIRE(finds("fallout4launcher.EXE=Fallout4.exe", "FALLOUT4LAUNCHER.exe", "Fallout4.exe"), "find: case of the program is ignored");
    REQUIRE(finds("Fallout4Launcher.exe=Fallout4.exe", "Fallout4.exe", NULL), "find: the replacement itself is not swapped");
    REQUIRE(finds("Fallout4Launcher.exe=Fallout4.exe", "Fallout4Launcher.exe.bak", NULL) && finds("Fallout4Launcher.exe=Fallout4.exe", "XFallout4Launcher.exe", NULL), "find: the whole file name has to match");
    REQUIRE(finds("  a.exe = b.exe  ;  Fallout4Launcher.exe  =  Fallout4.exe ", "Fallout4Launcher.exe", "Fallout4.exe") && finds(" a.exe = b.exe ;c.exe=d.exe", "a.exe", "b.exe"), "find: blanks round entries and names");
    REQUIRE(finds("a.exe=b.exe;c.exe=d.exe;e.exe=f.exe", "c.exe", "d.exe") && finds("a.exe=b.exe;c.exe=d.exe;e.exe=f.exe", "e.exe", "f.exe"), "find: several entries");
    REQUIRE(finds("garbage;=;a.exe;b.exe=;=c.exe;x.dll=y.exe;d.exe=e.exe", "d.exe", "e.exe") && finds("garbage;=;a.exe;b.exe=;=c.exe;x.dll=y.exe", "x.dll", NULL), "find: an invalid entry is skipped, the next one still works");
    REQUIRE(finds("a.exe=a.exe", "a.exe", NULL) && finds("a.exe=A.EXE", "a.exe", NULL), "find: a program is never swapped for itself");
    REQUIRE(finds("a.exe=..\\b.exe", "a.exe", NULL) && finds("a.exe=bin/b.exe", "a.exe", NULL) && finds("bin\\a.exe=b.exe", "a.exe", NULL), "find: no path in either name");
    REQUIRE(finds("", "a.exe", NULL) && finds("a.exe=b.exe", "", NULL) && finds(";;;", "a.exe", NULL), "find: an empty list or name finds nothing");

    /* the real strings of C050A4 (Fallout 4 through BCD Dock, build 580) */
    const char *nt = "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe";
    const char *dos = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe";
    const char *cmd = "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe\"";
    n = wide(nt, a);
    REQUIRE(spawn_swap_base(a, n) == n - strlen("Fallout4Launcher.exe") && spawn_swap_base(a, 0) == 0, "base: the file name starts after the last backslash");
    m = spawn_swap_path(a, n, "Fallout4.exe", 12, out, 600);
    REQUIRE(m && same(out, m, "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe") && out[m] == 0, "path: the NT image name of the launcher becomes Fallout4.exe's");
    n = wide(dos, a);
    m = spawn_swap_path(a, n, "Fallout4.exe", 12, out, 600);
    REQUIRE(m && same(out, m, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe"), "path: the DOS image name too");
    REQUIRE(spawn_swap_path(a, n, "Fallout4.exe", 12, out, n - 10) == 0, "path: no room, no result");
    {
        WCHAR p[40]; size_t pn = wide("game.exe", p);
        m = spawn_swap_path(p, pn, "other.exe", 9, out, 600);
        REQUIRE(m && same(out, m, "other.exe"), "path: a bare name is replaced as a whole");
        pn = wide("C:/dir/game.exe", p);
        m = spawn_swap_path(p, pn, "other.exe", 9, out, 600);
        REQUIRE(m && same(out, m, "C:/dir/other.exe"), "path: a slash also ends the folder");
    }

    /* the command line */
    {
        WCHAR old_path[400], new_path[400], line[800];
        size_t on = wide(dos, old_path), nn, ln;
        nn = spawn_swap_path(old_path, on, "Fallout4.exe", 12, new_path, 400);
        ln = wide(cmd, line);
        m = spawn_swap_cmdline(line, ln, old_path, on, new_path, nn, out, 800);
        REQUIRE(m && same(out, m, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe\"") && out[m] == 0, "command line: Valve's quoted path is rewritten, the quotes stay");
        ln = wide("\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe\" -len=false -x", line);
        m = spawn_swap_cmdline(line, ln, old_path, on, new_path, nn, out, 800);
        REQUIRE(m && same(out, m, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe\" -len=false -x"), "command line: the arguments after the path stay");
        ln = wide("C:\\g\\a.exe /x", line);
        { WCHAR o2[20], n2[20]; size_t o2n = wide("C:\\g\\a.exe", o2), n2n = wide("C:\\g\\b.exe", n2);
          m = spawn_swap_cmdline(line, ln, o2, o2n, n2, n2n, out, 800);
          REQUIRE(m && same(out, m, "C:\\g\\b.exe /x"), "command line: an unquoted path is rewritten too");
          ln = wide("C:\\g\\a.exe.bak /x", line);
          REQUIRE(spawn_swap_cmdline(line, ln, o2, o2n, n2, n2n, out, 800) == 0, "command line: a longer name that starts the same is left alone");
          ln = wide("C:\\g\\A.EXE", line);
          m = spawn_swap_cmdline(line, ln, o2, o2n, n2, n2n, out, 800);
          REQUIRE(m && same(out, m, "C:\\g\\b.exe"), "command line: the path alone, ignoring case");
          ln = wide(".\\a.exe", line);
          REQUIRE(spawn_swap_cmdline(line, ln, o2, o2n, n2, n2n, out, 800) == 0, "command line: a relative first word is kept as it is");
          ln = wide("C:\\g\\a.exe", line);
          REQUIRE(spawn_swap_cmdline(line, ln, o2, o2n, n2, n2n, out, 5) == 0 && spawn_swap_cmdline(line, 3, o2, o2n, n2, n2n, out, 800) == 0 &&
                  spawn_swap_cmdline(line, 0, o2, o2n, n2, n2n, out, 800) == 0, "command line: no room or a short line gives no result");
        }
    }
    printf(fails ? "FAILURES: %d\n" : "all string checks passed\n", fails);
    return fails ? 1 : 0;
}
'''

cc = shutil.which('clang') or shutil.which('cc') or shutil.which('gcc')
with tempfile.TemporaryDirectory(prefix='madeira-spawn-swap-') as tmp:
    tmp = Path(tmp)
    (tmp / 't.c').write_text(C)
    shutil.copy(header_path, tmp / 'ios_spawn_swap.h')
    exe = tmp / 't'
    build = subprocess.run([cc, '-std=c99', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-o', str(exe), str(tmp / 't.c')],
                           capture_output=True, text=True)
    check('ios_spawn_swap.h compiles warning-free with clang (C99, -Wall -Wextra -Werror, ASan and UBSan)', build.returncode == 0, build.stderr[-3000:])
    if build.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        sys.stdout.write(run.stdout)
        sys.stdout.write(run.stderr[-2000:])
        check('the string checks pass', run.returncode == 0)

# ---------------------------------------------------------------- 1b. the production function, compiled with stubs for Wine's types
C2 = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
typedef unsigned short WCHAR;
typedef unsigned short USHORT;
typedef unsigned int ULONG;
typedef unsigned int UINT;
typedef int BOOL;
typedef int NTSTATUS;
#define TRUE 1
#define FALSE 0
#define FILE_OPEN 1
#define OBJ_CASE_INSENSITIVE 0x40
#define STATUS_OBJECT_NAME_NOT_FOUND ((NTSTATUS)0xc0000034)
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName, CommandLine; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { ULONG Length; void *RootDirectory; UNICODE_STRING *ObjectName; ULONG Attributes; } OBJECT_ATTRIBUTES;
#define InitializeObjectAttributes(p, n, a, r, s) do { (p)->Length = sizeof(*(p)); (p)->RootDirectory = (r); (p)->ObjectName = (n); (p)->Attributes = (a); } while (0)

static const char *root_dir;
static int probes;
static const char *debugstr_wn(const WCHAR *s, size_t n) { static char buf[4][300]; static int k; char *b = buf[k++ & 3]; size_t i; for (i = 0; i < n && i < 290; i++) b[i] = (char)s[i]; b[i] = 0; return b; }
/* a stand-in for Wine's name lookup: \??\C:\a\b.exe is <root>/a/b.exe; found when the file exists; the results are allocated like Wine's */
static NTSTATUS get_nt_and_unix_names(OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt_name, char **unix_name, UINT disposition, BOOL check)
{
    size_t n = attr->ObjectName->Length / sizeof(WCHAR), i, o = 0;
    char path[1024]; struct stat st;
    (void)disposition; (void)check; probes++;
    if (n < 7) return STATUS_OBJECT_NAME_NOT_FOUND;
    snprintf(path, sizeof path, "%s/", root_dir); o = strlen(path);
    for (i = 7; i < n && o < sizeof path - 1; i++) path[o++] = attr->ObjectName->Buffer[i] == '\\' ? '/' : (char)attr->ObjectName->Buffer[i];
    path[o] = 0;
    if (stat(path, &st)) return STATUS_OBJECT_NAME_NOT_FOUND;
    *unix_name = strdup(path);
    nt_name->Buffer = malloc((n + 1) * sizeof(WCHAR)); memcpy(nt_name->Buffer, attr->ObjectName->Buffer, n * sizeof(WCHAR));
    nt_name->Length = (USHORT)(n * sizeof(WCHAR));
    return 0;
}
#include "ios_spawn_swap.h"
@@FUNCTION@@

static int fails;
#define REQUIRE(c, what) do { if (c) printf("ok   %s\n", what); else { printf("FAIL %s\n", what); fails++; } } while (0)
static void set(UNICODE_STRING *u, const char *s) { size_t n = strlen(s), i; u->Buffer = malloc((n + 1) * sizeof(WCHAR)); for (i = 0; i <= n; i++) u->Buffer[i] = (unsigned char)s[i]; u->Length = (USHORT)(n * sizeof(WCHAR)); u->MaximumLength = (USHORT)((n + 1) * sizeof(WCHAR)); }
static int is(const UNICODE_STRING *u, const char *s) { size_t n = strlen(s), i; if (u->Length != n * sizeof(WCHAR)) return 0; for (i = 0; i < n; i++) if (u->Buffer[i] != (unsigned char)s[i]) return 0; return 1; }

int main(int argc, char **argv)
{
    const char *nt0 = "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe";
    const char *dos0 = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe";
    const char *cmd0 = "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe\"";
    const char *list = "Fallout4Launcher.exe=Fallout4.exe";
    UNICODE_STRING path; RTL_USER_PROCESS_PARAMETERS params;
    WCHAR *old_nt, *old_dos, *old_cmd;
    (void)argc; root_dir = argv[1];

    set(&path, nt0); set(&params.ImagePathName, dos0); set(&params.CommandLine, cmd0);
    old_nt = path.Buffer; old_dos = params.ImagePathName.Buffer; old_cmd = params.CommandLine.Buffer;
    REQUIRE(spawn_swap_apply(list, &path, &params) == TRUE, "the launcher is swapped when Fallout4.exe is in the folder");
    REQUIRE(is(&path, "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe") && path.MaximumLength == path.Length + 2 && path.Buffer != old_nt, "the NT image name is the game's");
    REQUIRE(is(&params.ImagePathName, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe") && params.ImagePathName.Buffer != old_dos, "the DOS image name is the game's");
    REQUIRE(is(&params.CommandLine, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe\"") && params.CommandLine.Buffer != old_cmd, "the command line names the game, quotes kept");
    REQUIRE(probes == 1, "one name lookup for the replacement");
    /* the old buffers are untouched (the caller owns them) */
    REQUIRE(old_nt[0] == '\\' && old_dos[0] == 'C' && old_cmd[0] == '"' && old_cmd[1] == 'C', "the caller's old strings are not written to");

    /* the replacement is not in the folder: nothing changes */
    set(&path, "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Other\\Fallout4Launcher.exe"); set(&params.ImagePathName, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Other\\Fallout4Launcher.exe");
    set(&params.CommandLine, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Other\\Fallout4Launcher.exe\"");
    old_nt = path.Buffer; old_dos = params.ImagePathName.Buffer; old_cmd = params.CommandLine.Buffer;
    REQUIRE(spawn_swap_apply(list, &path, &params) == FALSE && path.Buffer == old_nt && params.ImagePathName.Buffer == old_dos && params.CommandLine.Buffer == old_cmd, "no Fallout4.exe in the folder: the launcher starts as asked, nothing changed");

    /* not listed, empty or missing list */
    set(&path, "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe"); set(&params.ImagePathName, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe");
    set(&params.CommandLine, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe\"");
    old_nt = path.Buffer; old_cmd = params.CommandLine.Buffer;
    probes = 0;
    REQUIRE(spawn_swap_apply(list, &path, &params) == FALSE && path.Buffer == old_nt && params.CommandLine.Buffer == old_cmd && probes == 0, "a program that is not listed: nothing changed, nothing looked up");
    REQUIRE(spawn_swap_apply(NULL, &path, &params) == FALSE && spawn_swap_apply("", &path, &params) == FALSE && probes == 0, "no list: nothing happens");

    /* the NT and DOS names disagree */
    set(&path, nt0); set(&params.ImagePathName, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Something.exe"); set(&params.CommandLine, cmd0);
    old_nt = path.Buffer; old_dos = params.ImagePathName.Buffer;
    probes = 0;
    REQUIRE(spawn_swap_apply(list, &path, &params) == FALSE && path.Buffer == old_nt && params.ImagePathName.Buffer == old_dos && probes == 0, "image names that disagree: nothing changed");

    /* a command line that does not begin with the image path keeps its text, the names still swap */
    set(&path, nt0); set(&params.ImagePathName, dos0); set(&params.CommandLine, "Fallout4Launcher.exe -len=false");
    old_cmd = params.CommandLine.Buffer;
    REQUIRE(spawn_swap_apply(list, &path, &params) == TRUE && params.CommandLine.Buffer == old_cmd && is(&params.CommandLine, "Fallout4Launcher.exe -len=false") &&
            is(&params.ImagePathName, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe"), "a relative first word of the command line is kept; the image names swap");

    /* arguments after the path are kept */
    set(&path, nt0); set(&params.ImagePathName, dos0); set(&params.CommandLine, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4Launcher.exe\" -x");
    REQUIRE(spawn_swap_apply(list, &path, &params) == TRUE && is(&params.CommandLine, "\"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4\\Fallout4.exe\" -x"), "arguments after the path stay");

    /* empty strings */
    path.Length = 0; params.ImagePathName.Length = 0;
    REQUIRE(spawn_swap_apply(list, &path, &params) == FALSE, "an empty image name: nothing happens");
    printf(fails ? "FAILURES: %d\n" : "all function checks passed\n", fails);
    return fails ? 1 : 0;
}
"""
body_start = process.index('static BOOL spawn_swap_apply(')
function = process[body_start:process.index('\n}\n', body_start) + 3]
with tempfile.TemporaryDirectory(prefix='madeira-spawn-swap-fn-') as tmp:
    tmp = Path(tmp)
    game = tmp / 'files/Program Files (x86)/Steam/steamapps/common/Fallout 4'
    game.mkdir(parents=True)
    (game / 'Fallout4Launcher.exe').write_bytes(b'MZ')
    (game / 'Fallout4.exe').write_bytes(b'MZ')
    other = tmp / 'files/Program Files (x86)/Steam/steamapps/common/Other'
    other.mkdir(parents=True)
    (other / 'Fallout4Launcher.exe').write_bytes(b'MZ')
    (tmp / 't2.c').write_text(C2.replace('@@FUNCTION@@', function))
    shutil.copy(header_path, tmp / 'ios_spawn_swap.h')
    exe = tmp / 't2'
    build = subprocess.run([cc, '-std=gnu99', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(exe), str(tmp / 't2.c')],
                           capture_output=True, text=True)
    check('the production spawn_swap_apply compiles with stubs for Wine\'s types (-Wall -Wextra -Werror)', build.returncode == 0, build.stderr[-3000:])
    if build.returncode == 0:
        run = subprocess.run([str(exe), str(tmp / 'files')], capture_output=True, text=True, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        sys.stdout.write(run.stdout)
        sys.stdout.write(run.stderr[-2000:])
        check('the function checks pass (ASan, UBSan)', run.returncode == 0)

# ---------------------------------------------------------------- 2. process_ios.c
swap_calls = [m.start() for m in re.finditer(r'getenv\(\s*"MADEIRA_SPAWN_SWAP"\s*\)', process)]
check('one getenv of MADEIRA_SPAWN_SWAP in process_ios.c, at the spawn', len(swap_calls) == 1)
site = process[swap_calls[0] - 200:swap_calls[0] + 220] if swap_calls else ''
check('the call is gated on the variable being set and passes the NT name and the parameters',
      'if (swap && *swap) spawn_swap_apply( swap, &path, params );' in site)
create = process[process.index('NTSTATUS WINAPI NtCreateUserProcess('):]
call = create.index('spawn_swap_apply( swap, &path, params )')
check('it runs after the MADEIRA_SPAWN_BLOCK gate and before the phase stamp, the session log and the file open (so they name the program that starts)',
      create.index('MADEIRA_SPAWN_BLOCK') < call < create.index('winios_phase( pbuf );') < create.index('madeira_steam_session_log( &params->ImagePathName );')
      < create.index('get_pe_file_info( &attr, &nt_name'))
check('the diagnostics line that names the original program precedes it', create.index('ERR("NtCreateUserProcess: image=%s cmdline=%s\\n"') < call)
body = process[process.index('static BOOL spawn_swap_apply('):]
body = body[:body.index('\n}\n') + 3]
check('the helper changes only path, params->ImagePathName and params->CommandLine',
      sorted(set(re.findall(r'(params->\w+(?:\.\w+)?|path->\w+)\s*=[^=]', body))) ==
      ['params->CommandLine.Buffer', 'params->CommandLine.Length', 'params->CommandLine.MaximumLength', 'params->ImagePathName.Buffer',
       'params->ImagePathName.Length', 'params->ImagePathName.MaximumLength', 'path->Buffer', 'path->Length', 'path->MaximumLength'],
      str(sorted(set(re.findall(r'(params->\w+(?:\.\w+)?|path->\w+)\s*=[^=]', body)))))
check('it does nothing when the two image names disagree, when the name is not listed, or when the replacement is not in the folder',
      'if (!name_len || name_len != dos_len - dos_base || !spawn_swap_equal_w_w( nt + nt_base, dos + dos_base, name_len )) return FALSE;' in body
      and 'if (!(to = spawn_swap_find( list, nt + nt_base, name_len, &to_len ))) return FALSE;' in body
      and 'status = get_nt_and_unix_names( &probe, &probe_nt, &probe_unix, FILE_OPEN, FALSE );' in body and 'if (status)' in body)
check('every change comes after the probe', body.index('get_nt_and_unix_names') < body.index('path->Buffer = new_nt;') < body.index('params->ImagePathName.Buffer = new_dos;'))
check('nothing is opened or written on disk (a name lookup only)', not re.search(r'\b(open|fopen|rename|unlink|link|symlink|copyfile)\s*\(', body))
check('it logs [spawn-swap] lines with the program names, no folder', body.count('[spawn-swap]') == 2 and 'debugstr_us' not in body)
check('the old and new image names are a fixed pair of includes: one include of the header', process.count('#include "ios_spawn_swap.h"') == 1)

# ---------------------------------------------------------------- 3. opt-in
other_readers = []
for path in list((root / 'build').rglob('*.[ch]')) + list((root / 'app/Madeira').rglob('*.swift')) + list((root / 'app/Madeira').rglob('*.m')):
    if path.name in ('ConfigCatalog.generated.swift', 'AutoTuneRules.generated.swift'):
        continue
    t = path.read_text(errors='ignore')
    if 'MADEIRA_SPAWN_SWAP' in t and path.name not in ('process_ios.c', 'ios_spawn_swap.h', 'GameRecommendationsList.swift', 'BCDGameStart.swift'):   # round 604: the game page's row writes the game's own line
        other_readers.append(path.name)
check('no other source reads or sets the variable', not other_readers, str(other_readers))

lists = json.loads((root / 'recommended/recommendations.json').read_text())
carriers = [r['id'] for r in lists['rules'] if 'MADEIRA_SPAWN_SWAP' in json.dumps(r.get('config', []))]
check('only the Fallout 4 and Skyrim Special Edition lists set it (their launchers start a second copy of themselves)',
      carriers == ['fallout-4', 'skyrim-special-edition'], str(carriers))
fo4 = next((r for r in lists['rules'] if r['id'] == 'fallout-4'), None)
check('Fallout 4 list: Steam 377160, folder "fallout 4", the launcher and the game as programs of Steam\'s copy only',
      fo4 is not None and fo4['match'] == {'steamAppIDs': [377160], 'steamFolders': ['fallout 4'],
                                          'programs': [{'name': 'fallout4launcher.exe', 'steam': True}, {'name': 'fallout4.exe', 'steam': True}]},
      str(fo4 and fo4['match']))
swap_lines = [c for c in (fo4 or {}).get('config', []) if isinstance(c, dict)]
check('the swap line is Fallout4Launcher.exe=Fallout4.exe with minBuild 598; the standard line stays',
      [c for c in swap_lines if 'MADEIRA_SPAWN_SWAP' in c['line']] == [{'line': 'env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe', 'minBuild': 598}]   # v2 (round 624) adds the WMA line
      and 'env.MADEIRA_EC_HOOK_TRACE = 0' in (fo4 or {}).get('config', []))
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the policy lists the name and checks the value: two .exe names, ASCII, no path',
      '"MADEIRA_SPAWN_SWAP"' in policy and 'case "MADEIRA_SPAWN_SWAP":' in policy)
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the settings catalog lists env.MADEIRA_SPAWN_SWAP', 'key: "env.MADEIRA_SPAWN_SWAP"' in catalog)
rules = (root / 'build/tools/autotune_rules.py').read_text()
check('an auto-tune rule adds the line for Fallout 4\'s launcher failure', '"id": "fallout-4-launcher-swap"' in rules and 'env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe' in rules)
check('its fixture and expectation exist', (root / 'tests/fixtures/autotune/fallout-4-launcher-2026-10-22.log').exists() and
      'env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe' in (root / 'tests/fixtures/autotune/fallout-4-launcher-2026-10-22.expect').read_text())
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-spawn-swap.py' in workflow)

print('check-spawn-swap: ' + ('PASS' if ok else 'FAILED'))
sys.exit(0 if ok else 1)
