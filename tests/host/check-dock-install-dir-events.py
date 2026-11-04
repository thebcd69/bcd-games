#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-install-dir-events.py: the two silent install-folder failures (host result 41) report which check failed (round 630,
agent A1 S16).

N14Q6D (GP Bikes, build 626, a first start): no byte came from Steam's servers, the offline fallback ran and the start ended with a
silent 41. The app now words that case (offline fallback) and the folder mismatch; the host says which check failed.

Checks:
  - CI runs this check and applies the patch in the Dock build step after the other launch.c patches and before build.sh, without
    failing the step; the app keeps the two report fields; its words for 41 after the offline fallback and for a folder mismatch;
    auto-tune's dock-offline-fallback-no-install-dir names the case;
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/launch.c after every
    earlier launch.c patch of the workflow, the patch applies once and changes only the two failure branches; a second run changes
    nothing; a moved anchor refuses it and leaves the file alone;
  - the patched checks, cut from the source and built on the host: an unset or too long recorded folder reports its length and 41; an
    empty, too long, unterminated or non-UTF-8 answer reports the client's value and 41; a good answer reports nothing and goes on;
  - the patched host compiles with build/madeira-dock/build.sh's flags together with every Dock patch of the workflow, where llvm-mingw
    is at hand.
"""
from pathlib import Path
import difflib, importlib.util, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("installdir", root / "tools/patch-dock-install-dir-events.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the other launch.c patches, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-install-dir-events.py\n" in step
      and 'python3 tools/patch-dock-install-dir-events.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-ceg-zero-jobs.py") < step.index("patch-dock-install-dir-events.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps both report fields", '"launch-install-directory-expected-length"' in allowed and '"launch-install-directory-got"' in allowed)
words = dk[dk.index("            if result == 41 {\n"):]
words = words[:words.index("\n            }\n") + 14]
check("the app words 41 after the offline fallback and a folder mismatch, and leaves other 41s to the general words",
      'if fields["session-offline-fallback"] == "1" {' in words and "the offline start BCD tried instead couldn't find this game's folder (code 41)" in words
      and 'if fields["launch-install-directory-mismatch"] == "1" {' in words and "Use Check and repair files in the game's options" in words
      and words.count("return ") == 2)
rules = (root / "build/tools/autotune_rules.py").read_text()
rule = rules[rules.index('"id": "dock-offline-fallback-no-install-dir"'):]
rule = rule[:rule.index("\n    },\n")]
check("auto-tune's dock-offline-fallback-no-install-dir names it (noFix, Dock sessions, the fallback and 41 together)",
      '"noFix": True' in rule and '"session": "dock"' in rule
      and 'all": [s("[dock-report] session-offline-fallback=1"), s("[dock-report] probe-result=41")]' in rule)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if P.MARKER in orig:
        print("note: the Dock tree is already patched; the original is rebuilt from it")
        orig = orig.replace(P.NEW_EXPECTED, P.OLD_EXPECTED, 1).replace(P.NEW_GOT, P.OLD_GOT, 1)
    with tempfile.TemporaryDirectory(prefix="madeira-dock-install-dir-") as td:
        t = Path(td)
        shutil.copytree(dock / "src", t / "src")
        lc = t / "src/launch.c"
        lc.write_text(orig)
        for patch in ("tools/patch-dock-game-scm.py", "tools/patch-dock-keep-alive.py", "tools/patch-dock-steam-readback.py",
                      "tools/patch-dock-ceg-zero-jobs.py"):
            r = subprocess.run([sys.executable, str(root / patch), str(lc)], capture_output=True, text=True)
            check("the workflow's earlier patch %s applies to the copy" % patch, r.returncode == 0, r.stdout + r.stderr)
        before = lc.read_text()
        script = str(root / "tools/patch-dock-install-dir-events.py")
        r1 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
        once = lc.read_text()
        r2 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
        check("it applies once after them; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and lc.read_text() == once and P.MARKER in once,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
        check("nothing else changes: the two failure lines replaced, nothing more", len(ops) == 2 and all(o[0] == "replace" for o in ops)
              and all(o[2] - o[1] == 1 for o in ops), ops)
        bad = t / "bad.c"
        bad.write_text(before.replace(P.OLD_EXPECTED, "    if (!length || length >= 32767) return 41;\n"))
        r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "recorded folder" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)

        cut = once[once.index(P.LEN_DECL):]
        cut = cut[:cut.index("    length = GetFullPathNameW(actual, 32768, normalized, NULL);\n")]
        harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef unsigned int DWORD;
#define CP_UTF8 65001
#define MB_ERR_INVALID_CHARS 8
#define L_EXPECT L"MADEIRA_STEAM_HOST_EXPECTED_INSTALL"
struct obs { void (*event)(const char *, int32_t); };
static char events[256];
static void ev(const char *name, int32_t v) { char b[96]; snprintf(b, sizeof b, "%s=%d ", name, v); strncat(events, b, sizeof events - strlen(events) - 1); }
static DWORD env_length;              /* what GetEnvironmentVariableW returns */
static int32_t client_got; static const char *client_text; static int utf8_ok;
static DWORD GetEnvironmentVariableW(const wchar_t *n, wchar_t *v, DWORD size) { (void)n; if (size) v[0] = 0; return env_length; }
typedef int32_t (*install_dir_fn)(void *, uint32_t, char *, int32_t);
static int32_t fake_install_dir(void *m, uint32_t a, char *out, int32_t size)
{ (void)m; (void)a; memset(out, 'x', (size_t)size); if (client_text) memcpy(out, client_text, strlen(client_text) + 1); return client_got; }
static int MultiByteToWideChar(unsigned cp, unsigned flags, const char *s, int n, wchar_t *out, int size)
{ (void)cp; (void)flags; (void)s; (void)n; (void)out; (void)size; return utf8_ok; }
static int run(void)
{
    struct obs obs = { ev }, *o = &obs;
    static void *vtable[6] = { 0, 0, 0, 0, 0, (void *)fake_install_dir };
    void *manager_obj = vtable, *manager = &manager_obj;
    uint32_t appid = 1;
    static char actual_utf8[32768];
    static wchar_t actual[32768], expected[32768];
    events[0] = 0;
''' + cut + r'''
    (void)got; (void)expected;
    return 0;
}
int main(void)
{
    struct { DWORD len; int32_t got; const char *text; int utf8; } c[] = {
        {0, 10, "c:\\games", 1}, {32768, 10, "c:\\games", 1}, {9, 0, "", 1}, {9, -1, NULL, 1}, {9, 32768, "c:\\games", 1},
        {9, 10, NULL, 1}, {9, 10, "c:\\games", 0}, {9, 10, "c:\\games", 1},
    };
    for (unsigned i = 0; i < sizeof c / sizeof c[0]; i++) {
        env_length = c[i].len; client_got = c[i].got; client_text = c[i].text; utf8_ok = c[i].utf8;
        printf("%d|%s\n", run(), events);
    }
    return 0;
}
'''
        cc = shutil.which("cc") or shutil.which("clang")
        if cc:
            (t / "h.c").write_text(harness)
            b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-function",
                                str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
            check("the patched checks build on the host (C11, warnings are errors)", b.returncode == 0, b.stderr[-1500:])
            if b.returncode == 0:
                out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
                check("an unset or too long recorded folder: its length and 41",
                      out[:2] == ["41|launch-install-directory-expected-length=0 ", "41|launch-install-directory-expected-length=32768 "], out)
                check("an empty, failed, too long, unterminated or non-UTF-8 answer: the client's value and 41",
                      out[2:7] == ["41|launch-install-directory-got=0 ", "41|launch-install-directory-got=-1 ",
                                   "41|launch-install-directory-got=32768 ", "41|launch-install-directory-got=10 ",
                                   "41|launch-install-directory-got=10 "], out)
                check("a good answer reports nothing and goes on", out[7] == "0|", out)
        else:
            print("skip the host build: no C compiler")

        mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                  Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                      if (m / "x86_64-w64-mingw32-clang").exists()), None)
        if mingw:
            r = subprocess.run([sys.executable, str(root / "tools/patch-dock-licence-wait.py"), str(t / "src/session.c")], capture_output=True, text=True)
            check("the workflow's session.c patch applies to the copy too", r.returncode == 0, r.stdout + r.stderr)
            b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"],
                               capture_output=True, text=True)
            check("the patched host compiles with build/madeira-dock/build.sh's flags, with every Dock patch of the workflow",
                  b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
        else:
            print("skip the Dock compile: no llvm-mingw at hand")

print("PASS: Dock install-folder events" if ok else "FAILED: Dock install-folder events")
sys.exit(0 if ok else 1)
