#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Scheduler hints for a running game, and the watch on the fast cores' clock (bcd.cfg `sched-hints`; app/Madeira/BCDSchedHints.swift,
BCDSchedHintsLive.swift, BCDSchedHints.m). No iPhone.

1. Off path, on the source: a session's start reads the key and returns at once without it, before anything is asked of iOS, a timer
   is made or a line is logged; the end returns at once when nothing was started; nothing else in the app calls the kernel helpers.
2. The kernel helpers, compiled and RUN on this Mac (the same kernel code as iOS): the process starts with no tiers, the first
   latency and throughput tiers are taken and can be read back, the process is put back to what it had, and the counters read. The
   form with a count of 1 (Porthole's) is refused by the kernel, which is why BCD passes the two-field structure.
3. BCDSchedHints.m compiles for iOS (arm64) without a warning.
4. The rule and the words (tests/host/bcd_sched_hints_checks.swift): the key's three values; held back after three seconds at
   1.3 GHz or below under half a core of load or more, free after two at 1.8 or above; the log's lines.
5. Settings' row, the catalog, the project, the workflow.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


plain = (APP / "BCDSchedHints.swift").read_text()
live = (APP / "BCDSchedHintsLive.swift").read_text()
helper = (APP / "BCDSchedHints.m").read_text()
content = (APP / "ContentView.swift").read_text()
menu = (APP / "BCDMenuMore.swift").read_text()
wf = (R / ".github/workflows/build-ipa.yml").read_text()

# ---- 1. off path
begin = live[live.index("    static func begin() {"):live.index("    private static func tick() {")]
first = [l.strip() for l in begin.splitlines()[1:3]]
check("a session's start reads the key (the game's line, then bcd.cfg) and returns at its second line without it",
      first == ["let wanted = BCDSchedHints.mode(MadeiraConfig.gameValue(BCDSchedHints.key) ?? MadeiraConfig.get(BCDSchedHints.key))",
                "guard wanted != .off, mode == .off else { return }"])
check("nothing is asked of iOS, no timer is made and no line is logged before that",
      all(begin.index("guard wanted != .off, mode == .off else { return }") < begin.index(x)
          for x in ("beginActivity", "madeira_sched_task_qos(", "madeira_sched_task_tiers(", "Timer(", "LogStore.shared.log(")))
check("the hints are given only for the value 1; `watch` gives none",
      begin.index("if wanted.gives {") < begin.index("beginActivity") < begin.index("madeira_sched_task_qos(") < begin.index("\n        }\n        LogStore")
      and "var gives: Bool { self == .hints }" in plain)
end = live[live.index("    static func end() {"):]
check("the end returns at once when nothing was started, and otherwise stops the timer, ends the activity and puts the tiers back",
      end.splitlines()[1].strip() == "guard mode != .off else { return }" and "timer?.invalidate()" in end
      and "ProcessInfo.processInfo.endActivity(activity)" in end and "madeira_sched_task_qos(before.latency, before.throughput, &l, &t)" in end
      and end.rstrip().endswith("mode = .off\n    }\n}"))
check("the plain file never reaches iOS or the kernel", re.search(r"madeira_\w+\(", plain) is None and "ProcessInfo" not in plain and "Timer" not in plain)
others = [p.name for p in APP.rglob("*.swift") if p.name != "BCDSchedHintsLive.swift" and re.search(r"madeira_(sched_|cpu_clock_sample)", p.read_text())]
check("only BCDSchedHintsLive.swift calls the kernel helpers", not others)
check("ContentView starts it once per session, and the session's end stops it",
      content.count("BCDSchedHintsLive.launch()") == 1 and "DispatchQueue.main.async { MainActor.assumeIsolated { begin() } }" in live
      and menu.count("BCDSchedHintsLive.end()") == 1 and menu.index("static func sessionEnded() {") < menu.index("BCDSchedHintsLive.end()"))
check("no string literal names an engine variable in a reading call, and the key is read through its constant (the catalog's scan)",
      re.search(r'getenv\(\s*"', live) is None and 'static let key = "sched-hints"' in plain and '"sched-hints"' not in live)

# ---- 2. the kernel helpers, run here
cc = shutil.which("cc") or shutil.which("clang")
if sys.platform != "darwin" or not cc:
    print("SKIP: the kernel helpers are run on a Mac only")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "main.c").write_text(r'''
#include <mach/mach.h>
#include <mach/task_policy.h>
#include <stdio.h>
#include <unistd.h>
int madeira_sched_task_tiers(unsigned *latency, unsigned *throughput);
void madeira_sched_task_qos(unsigned latency, unsigned throughput, int *kr_latency, int *kr_throughput);
unsigned madeira_sched_first_latency_tier(void);
unsigned madeira_sched_first_throughput_tier(void);
int madeira_cpu_clock_sample(double *fast_ms, double *fast_cycles, double *wall_ms);
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(void) {
  unsigned l = 9, t = 9, l0, t0;
  int a = -1, b = -1, one = (int)LATENCY_QOS_TIER_0;
  double f1 = -1, c1 = -1, w1 = -1, f2 = -1, c2 = -1, w2 = -1;
  volatile double burn = 0;
  expect(madeira_sched_task_tiers(&l0, &t0) == 0, "the process's tiers can be read");
  expect(madeira_sched_first_latency_tier() == 0xff0001 && madeira_sched_first_throughput_tier() == 0xfe0001, "the first tiers are the SDK's LATENCY_QOS_TIER_0 and THROUGHPUT_QOS_TIER_0");
  expect(task_policy_set(mach_task_self(), TASK_BASE_LATENCY_QOS_POLICY, (task_policy_t)&one, 1) == KERN_INVALID_ARGUMENT,
         "one number with a count of 1 (as Porthole passes it) is refused by the kernel: KERN_INVALID_ARGUMENT");
  expect(madeira_sched_task_tiers(&l, &t) == 0 && l == l0 && t == t0, "and it changed nothing");
  madeira_sched_task_qos(madeira_sched_first_latency_tier(), madeira_sched_first_throughput_tier(), &a, &b);
  expect(a == 0 && b == 0, "the two-field structure is taken for both tiers");
  expect(madeira_sched_task_tiers(&l, &t) == 0 && l == 0xff0001 && t == 0xfe0001, "read back: the process has the first latency and throughput tiers");
  madeira_sched_task_qos(l0, t0, &a, &b);
  expect(a == 0 && b == 0 && madeira_sched_task_tiers(&l, &t) == 0 && l == l0 && t == t0, "put back: the process has what it started with");
  expect(madeira_cpu_clock_sample(&f1, &c1, &w1) == 0 && f1 >= 0 && c1 >= 0 && w1 > 0, "the counters read");
  for (int i = 0; i < 20000000; i++) burn += i * 0.5;
  usleep(20000);
  expect(madeira_cpu_clock_sample(&f2, &c2, &w2) == 0 && w2 - w1 >= 15 && w2 - w1 < 5000 && f2 >= f1 && c2 >= c1, "a second reading is later, in milliseconds, and never behind the first");
  return fails ? 1 : 0;
}
''')
        shutil.copy(APP / "BCDSchedHints.m", t / "helper.c")
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-o", str(t / "h"), str(t / "main.c"), str(t / "helper.c")], capture_output=True, text=True)
        if check("the kernel helpers compile on this Mac as plain C, without a warning", b.returncode == 0 and "warning" not in b.stderr):
            r = subprocess.run([str(t / "h")], capture_output=True, text=True, timeout=60)
            print(r.stdout.rstrip())
            check("the kernel helpers, run here", r.returncode == 0 and "ok  " in r.stdout)
        else:
            print(b.stderr[-2000:])

# ---- 3. compiles for iOS
sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True) if shutil.which("xcrun") else None
if not sdk or sdk.returncode or not sdk.stdout.strip():
    print("SKIP: no iOS SDK (xcrun) for the compile part")
else:
    c = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk.stdout.strip(), "-miphoneos-version-min=18.0",
                        "-Wall", "-Wextra", "-x", "objective-c", "-fsyntax-only", str(APP / "BCDSchedHints.m")], capture_output=True, text=True)
    if not check("BCDSchedHints.m compiles for iOS arm64 without a warning", c.returncode == 0 and "warning" not in c.stderr):
        print(c.stderr[-2000:])
header = (APP / "Madeira-Bridging-Header.h").read_text()
check("the bridging header declares the helpers as the file defines them",
      all(d in header and d[:-1] + " {" in helper for d in (
          "int madeira_sched_task_tiers(unsigned *latency, unsigned *throughput);",
          "void madeira_sched_task_qos(unsigned latency, unsigned throughput, int *kr_latency, int *kr_throughput);",
          "unsigned madeira_sched_first_latency_tier(void);", "unsigned madeira_sched_first_throughput_tier(void);",
          "int madeira_cpu_clock_sample(double *fast_ms, double *fast_cycles, double *wall_ms);")))

# ---- 4. the rule and the words
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copy(R / "tests/host/bcd_sched_hints_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(APP / "BCDSchedHints.swift")],
                           capture_output=True, text=True)
        if check("BCDSchedHints.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing",
                  r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

# ---- 5. the app around it
sections = (APP / "BCDSettingsSections.swift").read_text()
settings_live = (APP / "BCDSettingsLive.swift").read_text()
check("Settings > Memory & performance > Speed and power has the row (off, log only, on), after Eco mode",
      sections.index('title: "Eco mode"') < sections.index("title: BCDSchedHints.title") and "backend.setSchedHints($0)" in sections
      and "var schedHints: String { get }" in sections and "func setSchedHints(_ value: String)" in sections
      and "MadeiraConfig.set(BCDSchedHints.key, value.isEmpty ? nil : value)" in settings_live)
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the key, off by default, with its three values",
      re.search(r'key: "sched-hints", title: "[^"]*", kind: \.choice, defaultValue: ""', catalog) is not None
      and '("watch", "Log only")' in catalog and '("1", "On")' in catalog)
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
preview = (R / "tools/ui-preview.py").read_text()
check("the three files are in the project (the kernel helpers as a source of the app); the preview build leaves the engine-bound one out",
      all(project.count(f"{n} in Sources") >= 1 for n in ("BCDSchedHints.swift", "BCDSchedHintsLive.swift", "BCDSchedHints.m"))
      and '"BCDSchedHintsLive.swift"' in preview)
check("the workflow runs this check", "run: python3 tests/host/check-sched-hints.py" in wf)

print("FAILED: scheduler hints" if failures else "PASS: scheduler hints")
sys.exit(1 if failures else 0)
