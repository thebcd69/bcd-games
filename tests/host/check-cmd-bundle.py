#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""cmd.exe takes the aarch64-windows core (round 579); no Wine, no app build.

A library entry that is a .bat/.cmd starts "C:\\windows\\system32\\cmd.exe /c <file>" (Library.swift launchCommand).
WineProcessBridge.m picks the session core from the name: a backslash path means arm64ec-windows. cmd.exe is shipped only
as a native aarch64 program, so the session ran a native ARM64 cmd.exe against the ARM64EC kernel32 and stopped after
0.5 s with "could not load kernel32.dll, status c000007b" (VSWVB0, JFFRDP, H579EM, ZSRKW7: every log of such an entry).
The fix flips the core to aarch64-windows for the target cmd.exe, and for nothing else.

Part A reads the launch function: the name heuristic and the i386 override are as they were, and the one new statement is
an override to aarch64-windows that only the cmd.exe predicate can reach. Part B compiles the production predicate and the
production statements against the OLD decision over a grid of targets, MADEIRA_USE_ARM64EC values and i386 flags: the new
answer differs from the old one for cmd.exe, from arm64ec to aarch64, and for no other input. Part C holds the library's
spelling of the cmd.exe path to the predicate's, and the workflow, auto-tune rule and fixture to this fix.
"""
from pathlib import Path
import re, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "app/Madeira/WineProcessBridge.m").read_text().replace("\r\n", "\n")
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def function(source, start):
    a = source.index(start); b = source.index("{", a); depth = 1; c = b + 1
    while depth:
        depth += (source[c] == "{") - (source[c] == "}"); c += 1
    return source[a:c]


# ---- Part A: the launch function
thread = function(src, "static void *wine_process_thread(void *arg) {")
heuristic = re.search(r"BOOL use_arm64ec = \(force_ec[^;]*;", thread).group(0)
check("the name heuristic is upstream's, verbatim",
      heuristic == """BOOL use_arm64ec = (force_ec && *force_ec == '1') ||
                           (strstr(madeira_exe, "x64") != NULL) ||
                           (strchr(madeira_exe, '\\\\') != NULL);""")
i386 = "if (is_i386_target) use_arm64ec = NO;"
cmd_block = function(thread, "if (madeira_target_is_cmd(madeira_exe))")
assigns = re.findall(r"use_arm64ec\s*=[^=][^;]*;", thread)
check("the core is assigned three times only: the heuristic, the i386 override, the cmd.exe override (NO)",
      [a.replace("\n", "").replace("  ", "") for a in assigns[1:]] == ["use_arm64ec = NO;", "use_arm64ec = NO;"] and
      assigns[0].startswith("use_arm64ec = (force_ec"))
check("the cmd.exe override follows the i386 override and precedes the bundle choice",
      thread.index(i386) < thread.index("if (madeira_target_is_cmd(madeira_exe))") < thread.index("const char *bundle_subdir"))
check("the override sets aarch64 (NO) only; it says so in the log only when it changed the answer",
      "use_arm64ec = NO;" in cmd_block and "use_arm64ec = YES" not in cmd_block and
      re.search(r"if \(use_arm64ec\)\s*dprintf\(STDERR_FILENO, \"\[WineProc\] cmd\.exe exists only in aarch64-windows", cmd_block))
check("nothing but the predicate decides it: one call, in that one place", thread.count("madeira_target_is_cmd(") == 1 and
      src.count("madeira_target_is_cmd(") == 2)   # the definition and the call

# ---- Part B: the production predicate and statements, against the old decision
pred = function(src, "static BOOL madeira_target_is_cmd(")
code = r"""
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <stdlib.h>
typedef int BOOL;
#define YES 1
#define NO 0
""" + pred + r"""

/* the OLD decision: the heuristic and the i386 override, as the launch function had them */
static int core_old(const char *madeira_exe, const char *force_ec, int i386)
{
    const BOOL is_i386_target = i386;
    """ + heuristic + r"""
    if (is_i386_target) use_arm64ec = NO;
    return use_arm64ec;
}

/* the NEW decision: the same statements and the production cmd.exe block */
static int core_new(const char *madeira_exe, const char *force_ec, int i386)
{
    const BOOL is_i386_target = i386;
    """ + heuristic + r"""
    if (is_i386_target) use_arm64ec = NO;
    """ + cmd_block + r"""
    return use_arm64ec;
}

int main(void)
{
    const char *exes[] = {
        "cmd.exe", "CMD.EXE", "Cmd.Exe", "C:\\windows\\system32\\cmd.exe", "C:\\Windows\\System32\\CMD.EXE",
        "c:\\windows\\system32\\cmd.exe",
        "explorer.exe", "cube.exe", "cube-x64.exe", "hello-x64.exe", "services.exe", "notepad.exe",
        "C:\\windows\\syswow64\\cmd.exe", "C:\\Games\\cmd.exe", "C:\\Program Files\\Tool\\cmd.exe",
        "C:\\windows\\system32\\cmd.exe ", " cmd.exe", "cmd", "xcmd.exe", "cmd.exe.bat", "cmd.exe\\x", "D:\\windows\\system32\\cmd.exe",
        "C:\\windows\\system32\\notepad.exe", "C:\\windows\\system32\\explorer.exe", "C:\\Games\\NeoEE-BCD\\Play NeoEE.bat",
        "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Game\\game.exe", "C:\\Games\\x64\\cmd.exe", "C:\\Games\\game-x64.exe",
        "C:\\windows\\system32\\cmd-x64.exe", "", "a", "x64", "\\", "C:",
    };
    const char *forces[] = { NULL, "", "0", "1", "2", "true" };
    int flipped = 0, same = 0;
    for (size_t e = 0; e < sizeof(exes) / sizeof(exes[0]); e++)
        for (size_t f = 0; f < sizeof(forces) / sizeof(forces[0]); f++)
            for (int i386 = 0; i386 < 2; i386++)
            {
                int o = core_old(exes[e], forces[f], i386), n = core_new(exes[e], forces[f], i386);
                int cmd = madeira_target_is_cmd(exes[e]);
                if (o != n)
                {
                    if (!(cmd && o == YES && n == NO))
                    { printf("FAIL: exe=\"%s\" force=%s i386=%d old=%d new=%d\n", exes[e], forces[f] ? forces[f] : "(null)", i386, o, n); return 1; }
                    flipped++;
                }
                else same++;
                if (cmd && n != NO) { printf("FAIL: cmd.exe stayed arm64ec: \"%s\"\n", exes[e]); return 1; }
            }
    /* the four logged launches: old = arm64ec (the failure), new = aarch64 */
    if (core_old("C:\\windows\\system32\\cmd.exe", NULL, 0) != YES) { puts("FAIL: the old decision for the logged launch"); return 1; }
    if (core_new("C:\\windows\\system32\\cmd.exe", NULL, 0) != NO) { puts("FAIL: the new decision for the logged launch"); return 1; }
    /* programs the library starts by themselves keep their cores */
    if (core_new("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Game\\game.exe", NULL, 0) != YES) { puts("FAIL: a game path"); return 1; }
    if (core_new("explorer.exe", NULL, 0) != NO) { puts("FAIL: explorer.exe"); return 1; }
    if (core_new("cube-x64.exe", NULL, 0) != YES) { puts("FAIL: a test x64 program"); return 1; }
    if (core_new("C:\\Games\\cmd.exe", NULL, 0) != YES) { puts("FAIL: a user's own cmd.exe elsewhere"); return 1; }
    if (flipped == 0 || same == 0) { puts("FAIL: the grid did not cover both outcomes"); return 1; }
    printf("grid ok: %d inputs differ (cmd.exe only, arm64ec -> aarch64), %d are identical\n", flipped, same);
    return 0;
}
"""
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / "check.c"; exe = Path(tmp) / "check"; c.write_text(code)
    subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-Werror=implicit-function-declaration", str(c), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    check("production predicate and statements: only cmd.exe changes, only arm64ec -> aarch64 (%s)" % out.stdout.strip().replace("\n", " | "),
          out.returncode == 0 and "grid ok" in out.stdout)

# ---- Part C: the library's spelling, the workflow, the auto-tune rule
lib = (root / "app/Madeira/Library.swift").read_text()
# Round 630 (agent G1 S6): the target goes through cmdTarget (CALL before a path with ( ) & ... , quoted as before otherwise)
spelling = 'return ("C:\\\\windows\\\\system32\\\\cmd.exe", withExtra("/c \\(cmdTarget(path))"))'
check("the library starts a .bat/.cmd as C:\\windows\\system32\\cmd.exe (the spelling the predicate matches)",
      spelling in lib and '"C:\\\\windows\\\\system32\\\\cmd.exe"' in pred)
check("a batch file inside the Wine desktop goes through explorer.exe, which already has the aarch64 core",
      'return ("explorer.exe", "/desktop=shell,' in lib and "cmd /c" in lib)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "python3 tests/host/check-cmd-bundle.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
rid = rules.find('"id": "cmd-exe-arm64ec-core"')
block = rules[rid:rid + 3600]
block = block[:block.find("\n    },\n")]
check("auto-tune knows the failure: no line, fixed in 579, both of the engine's own signal lines",
      rid >= 0 and '"lines": []' in block and '"fixedIn": 579' in block and
      "Target exe: C:\\\\windows\\\\system32\\\\cmd.exe (bundle=arm64ec-windows)" in block and
      "wine: could not load kernel32.dll, status c000007b" in block)
fx = root / "tests/fixtures/autotune/bat-cmd-arm64ec-core-2026-10-21"
expect = fx.with_suffix(".expect").read_text()
log = fx.with_suffix(".log").read_text()
check("its fixture (VSWVB0) is matched by the rule and stays 'known'",
      '"cmd-exe-arm64ec-core"' in expect and '"known"' in expect and "(bundle=arm64ec-windows)" in log)
import sys
sys.exit(0 if ok else 1)
