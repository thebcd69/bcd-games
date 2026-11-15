#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_THREAD_QOS: an opt-in QoS class for a guest thread by its name (thread_ios.c, build.sh).

Every guest thread runs at QOS_CLASS_USER_INTERACTIVE, so iOS cannot tell a game's main thread from its worker pools (Dark
Souls III: five GXWorker threads took more performance-core time than the main thread). With
env.MADEIRA_THREAD_QOS = pattern=class;pattern=class a thread whose name (SetThreadDescription) matches a pattern runs at that
class: first match wins, * and ? are wildcards, classes are user-interactive, user-initiated (initiated), default, utility,
background. Unnamed and unmatched threads keep user-interactive, and eco still overrides all. Off unless the variable is set
(this one is off upstream too): sync.c is compiled with its pthread_set_qos_class_self_np renamed to madeira_guest_qos_self, which
passes the class through when no rule is set.

  1. the rule parser, the wildcard matcher, the per-thread table and madeira_guest_qos_self, cut out of thread_ios.c and run on
     the host with a stand-in for the QoS call: unset or empty -> off, every call passes through (class and relative priority
     untouched, nothing logged); rules are split at ';', trimmed and quoted values
     taken; a rule with an unknown class or a pattern of 64 characters or more is ignored; at most 32 rules; first match wins;
     * and ?; a table of 512 threads with reuse of a slot, removal and a changed class;
     a thread with a rule gets its class (and a refusal falls back to user-interactive); an eco class call passes through;
  2. the build: sync.c is compiled with the rename only, compile_one passes the extra flags to that file alone;
  3. the exit hook and the naming hook run for every thread but do nothing without a rule.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/thread_ios.c").read_text()
build = (R / "build/ntdll-unix/build.sh").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


a = src.index("#define TQ_MAX_RULES   32")
b = src.index("\nvoid ios_thread_qos_exit( unsigned tid )\n{")
b = src.index("\n}\n", b) + 3
chunk = src[a:b]
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <pthread/qos.h>
static int lines;
#define dprintf(fd, ...) (lines++)
static int host_calls; static qos_class_t host_last; static int host_rel; static int host_refuse;
static int fake_set_qos(qos_class_t c, int r) { host_calls++; host_last = c; host_rel = r; return host_refuse ? 1 : 0; }
#define pthread_set_qos_class_self_np fake_set_qos
static int eco_applied;
void ios_eco_apply_self(void) { eco_applied++; }
''' + chunk + r'''
static const char *fake_exe = "";
static void tq_process_exe( char *out, size_t size ) { snprintf( out, size, "%s", fake_exe ); }   /* build 663: the PEB's file name, stood in */
static int fails;
static void expect(const char *what, long got, long want) { if (got != want) { printf("FAIL %s: got %ld want %ld\n", what, got, want); fails++; } }
int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "";
    char name[48];
    if (!strcmp(mode, "unset"))
    {
        expect("off", tq_enabled(), 0);
        tq_self_tid = 7;
        expect("passes the class through", madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0), 0);
        expect("  once, with the class asked", host_calls == 1 && host_last == QOS_CLASS_USER_INTERACTIVE && host_rel == 0, 1);
        madeira_guest_qos_self(QOS_CLASS_UTILITY, -3);
        expect("  eco class and relative priority untouched", host_calls == 2 && host_last == QOS_CLASS_UTILITY && host_rel == -3, 1);
        expect("  and nothing is logged", lines, 0);
        ios_thread_qos_exit(7);
        expect("exit hook does nothing", tq_lookup(7, name), 0);
    }
    else if (!strcmp(mode, "rules"))
    {
        expect("on", tq_enabled(), 1);
        expect("rule count: 5 good ones (an unknown class and a long pattern are dropped)", tq_nrules, 5);
        expect("class names", strcmp(tq_class_name(QOS_CLASS_UTILITY), "utility") == 0 && strcmp(tq_class_name(QOS_CLASS_BACKGROUND), "background") == 0
               && strcmp(tq_class_name(QOS_CLASS_DEFAULT), "default") == 0, 1);
        expect("first rule", strcmp(tq_rules[0].pat, "GXWorker*") == 0 && tq_rules[0].cls == QOS_CLASS_USER_INITIATED, 1);
        expect("quoted rule, blanks trimmed", strcmp(tq_rules[1].pat, "Core.Res.*") == 0 && tq_rules[1].cls == QOS_CLASS_UTILITY, 1);
        expect("'interactive' is user-interactive", tq_rules[3].cls == QOS_CLASS_USER_INTERACTIVE, 1);
        expect("glob * at the end", tq_glob("GXWorker*", "GXWorker3"), 1);
        expect("glob * in the middle", tq_glob("A*Z", "A-middle-Z"), 1);
        expect("glob ? is one character", tq_glob("W?rk", "Work") && !tq_glob("W?rk", "Wrk") && !tq_glob("W?rk", "Woork"), 1);
        expect("glob is case sensitive and anchored", !tq_glob("Work", "work") && !tq_glob("Work", "Worker") && !tq_glob("orker", "Worker"), 1);
        expect("empty pattern matches only the empty name", tq_glob("", "") && !tq_glob("", "x"), 1);
        expect("star alone matches anything", tq_glob("*", "") && tq_glob("*", "whatever"), 1);
        /* the table */
        expect("a new entry is a change", tq_store(10, QOS_CLASS_UTILITY, "EzWorkPool1"), 1);
        expect("  and is found, with its name", tq_lookup(10, name) == QOS_CLASS_UTILITY && !strcmp(name, "EzWorkPool1"), 1);
        expect("the same class again is no change", tq_store(10, QOS_CLASS_UTILITY, "EzWorkPool1"), 0);
        expect("a changed class is", tq_store(10, QOS_CLASS_BACKGROUND, "Renamed"), 1);
        expect("  and renames", tq_lookup(10, name) == QOS_CLASS_BACKGROUND && !strcmp(name, "Renamed"), 1);
        expect("removal", tq_store(10, QOS_CLASS_UNSPECIFIED, NULL) && tq_lookup(10, name) == 0, 1);
        expect("removing what is not there is no change", tq_store(11, QOS_CLASS_UNSPECIFIED, NULL), 0);
        { unsigned t; int n = 0; for (t = 100; t < 100 + 600; t++) n += tq_store(t, QOS_CLASS_UTILITY, "x"); expect("the table holds 512 threads", n, 512); }
        expect("a free slot is reused after a removal", tq_store(100, QOS_CLASS_UNSPECIFIED, NULL) == 1 && tq_store(9999, QOS_CLASS_UTILITY, "new") == 1, 1);
        /* the wrapper */
        tq_store(500, QOS_CLASS_UTILITY, "EzWorkPool9");
        tq_self_tid = 500; host_calls = 0;
        expect("a thread with a rule gets its class", madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0), 0);
        expect("  the call asked for it, with relative priority 0", host_calls == 1 && host_last == QOS_CLASS_UTILITY && host_rel == 0, 1);
        host_calls = 0; host_refuse = 1;
        tq_self_applied = QOS_CLASS_UNSPECIFIED;
        madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0);
        expect("a refusal falls back to user-interactive", host_calls == 2 && host_last == QOS_CLASS_USER_INTERACTIVE, 1);
        host_refuse = 0; host_calls = 0; tq_self_tid = 9000;
        madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0);
        expect("a thread with no entry keeps user-interactive", host_calls == 1 && host_last == QOS_CLASS_USER_INTERACTIVE, 1);
        tq_self_tid = 500; host_calls = 0;
        madeira_guest_qos_self(QOS_CLASS_BACKGROUND, 0);
        expect("an eco class call passes through even for a named thread", host_calls == 1 && host_last == QOS_CLASS_BACKGROUND, 1);
        ios_thread_qos_exit(500);
        expect("a thread that exits leaves the table", tq_lookup(500, name), 0);
    }
    else if (!strcmp(mode, "exe"))
    {
        /* build 663: "exe:<program>=class" rules: the program's file name, letter case aside, gives every thread of it the class at its
         * start; a thread-name rule still comes first for a thread whose name matches; name rules never match a program's name */
        expect("on, three rules", tq_enabled() && tq_nrules == 3, 1);
        expect("exe rules are marked and lowercased", tq_rules[0].exe == 1 && !strcmp(tq_rules[0].pat, "launcher.exe") && tq_have_exe == 1
               && tq_rules[2].exe == 0, 1);
        expect("the program's name matches whatever its case", tq_exe_class("LAUNCHER.EXE") == QOS_CLASS_UTILITY
               && tq_exe_class("SocialClubHelper.exe") == QOS_CLASS_BACKGROUND && tq_exe_class("RDR2.exe") == 0 && tq_exe_class("") == 0, 1);
        fake_exe = "SocialClubHelper.exe"; eco_applied = 0;
        ios_thread_qos_start(41);
        expect("a thread of a named program gets the class at its start", tq_lookup(41, name) == QOS_CLASS_BACKGROUND && !strcmp(name, "SocialClubHelper.exe")
               && eco_applied == 1, 1);
        fake_exe = "RDR2.exe"; eco_applied = 0;
        ios_thread_qos_start(42);
        expect("the game's own threads are left alone", tq_lookup(42, name) == 0 && eco_applied == 0, 1);
        tq_store(43, QOS_CLASS_USER_INITIATED, "Render*"); fake_exe = "Launcher.exe"; eco_applied = 0;
        ios_thread_qos_start(43);
        expect("a thread a name rule already placed keeps that class", tq_lookup(43, name) == QOS_CLASS_USER_INITIATED && eco_applied == 1, 1);
        tq_self_tid = 41; host_calls = 0;
        madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0);
        expect("  and the QoS call asks for it", host_calls == 1 && host_last == QOS_CLASS_BACKGROUND, 1);
    }
    else if (!strcmp(mode, "toomany"))
    {
        tq_enabled();
        expect("at most 32 rules", tq_nrules, 32);
    }
    if (!fails) printf("harness ok\n");
    return fails != 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        bres = subprocess.run([cc, "-std=gnu99", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-o", exe, c, "-lpthread"], capture_output=True, text=True)
        check("the thread QoS code builds on the host", bres.returncode == 0, bres.stderr[-2500:])
        if bres.returncode == 0:
            def run(mode, value=None):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_THREAD_QOS"}
                if value is not None:
                    env["MADEIRA_THREAD_QOS"] = value
                return subprocess.run([exe, mode], capture_output=True, text=True, env=env)
            for label, value in (("unset", None), ("empty", "")):
                r = run("unset", value)
                check("MADEIRA_THREAD_QOS %s: off, every call passes through" % label, r.returncode == 0, r.stdout + r.stderr)
            rules = ('GXWorker*=user-initiated; "Core.Res.*"=utility;EzWorkPool*=utility;Bnd*=interactive;FD4JobWorker*=utility;'
                     'BadClass*=nonsense;' + "L" * 70 + "=utility")
            r = run("rules", rules)
            check("rules, wildcards and the table", r.returncode == 0, r.stdout + r.stderr)
            r = run("exe", "exe:Launcher.exe=utility; exe:SocialClubHelper.exe=background;Render*=initiated")
            check("exe: rules (build 663): a program's threads by its file name, name rules first", r.returncode == 0, r.stdout + r.stderr)
            r = run("toomany", ";".join("P%d=utility" % i for i in range(40)))
            check("40 rules: the first 32 count", r.returncode == 0, r.stdout + r.stderr)

check("sync.c alone is compiled with the rename",
      "sync)\n" in build and 'compile_one "$src" "sync" -Dpthread_set_qos_class_self_np=madeira_guest_qos_self' in build
      and build.count("-Dpthread_set_qos_class_self_np=madeira_guest_qos_self") == 1)
check("compile_one passes the extra flags to its file only", "shift 2   # anything after src and name: extra flags for this file only" in build
      and '        "$@" \\\n        -c "$src" -o "$OBJ_DIR/$name.o"' in build)
check("build 663: the naming hook keeps a program's class for a name no rule matches (its own thread only), name rules skip exe: rules",
      "if (!tq_rules[i].exe && tq_glob( tq_rules[i].pat, name )) { cls = tq_rules[i].cls; break; }" in src
      and "if (tid != self) return;" in src and "if (exe[0] && (cls = tq_exe_class( exe ))) name = exe;" in src
      and "NtCurrentTeb()->Peb->ProcessParameters" in src[src.index("static void tq_process_exe( char *out, size_t size )\n{"):])
check("the exit hook and the naming hook are called for every thread",
      "ios_thread_qos_exit( (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread );" in src
      and "ios_thread_qos_named( handle, nm );   /* MADEIRA_THREAD_QOS */" in src)
text = (R / "recommended/recommendations.json").read_text() + (R / "build/tools/autotune_rules.py").read_text()
check("no recommended list and no auto-tune rule sets it", "MADEIRA_THREAD_QOS" not in text)

print("PASS: thread QoS" if ok else "FAIL: thread QoS")
sys.exit(0 if ok else 1)
