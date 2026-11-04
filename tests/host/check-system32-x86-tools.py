#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_SYSTEM32_X86_TOOLS (on unless 0, 2026-11-01): Windows' command-line tools in the 64-bit system32.

Messiah (Steam 577980; logs ANV0TK, 2D4QK1, 53W6G9 build 614, MZYTWC 622, 83H0WC 625, 9ACRYW 626) starts through "Launch Messiah.bat",
which Valve's 64-bit client runs with the 64-bit cmd.exe. The script's first command pipes `reg Query ...` into `find`; the app had
reg.exe, find.exe, xcopy.exe and sdbinst.exe only in the i386 farm, so nothing named reg resolved in the 64-bit system32 and Wine's
cmd ends a batch whose pipe cannot start its left program with ExitProcess(255): no program started. A session now links the i386
tools into system32 where the name is absent, as it links msiexec.exe for MADEIRA_MSIEXEC_SYSTEM32, and Wine starts them as WoW64.

Checks:
  - the block in WineProcessBridge.m: the variable, the tool list (none of msiexec, regsvr32, rundll32 or the service tools), only
    absent names or BCD's own links are (re)made, and with the variable at 0 BCD's links go;
  - the block, cut from the file and compiled with Foundation on the host, run against a real folder: an absent name is linked; a
    real file, a link to another place (a farm's) and a dangling link that is not BCD's stay; BCD's link left by an earlier install
    (dangling) is made again; at 0 BCD's links are removed and nothing else is touched; without the i386 farm nothing is linked;
  - every listed tool ships in the i386 farm (docs/BINARIES-WOW64.md) and in neither 64-bit farm (app/Madeira/aarch64-windows,
    arm64ec-windows), so the link never shadows a 64-bit program;
  - Wine's cmd still ends a batch with ExitProcess(255) when a pipe's left program cannot start (the reason for the links);
  - auto-tune's rule bat-launch-64bit-cmd-missing-tool (fixedIn 627, evaluateWhenRan: the session "ran"), its fixture, and the
    engine taking a fixedIn rule for a session that ran; the catalog entry; CI.
"""
from pathlib import Path
import json, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
start = bridge.find("/* madeira-bcd (2026-11-01): MADEIRA_SYSTEM32_X86_TOOLS")
check("the block is in WineProcessBridge.m", start >= 0)
block_open = bridge.index("\n            {\n", start) + 1
depth, i = 0, block_open
while True:
    c = bridge[i]
    if c == "{":
        depth += 1
    elif c == "}":
        depth -= 1
        if depth == 0:
            break
    i += 1
block = bridge[block_open:i + 1]
tools = re.findall(r'"([a-z0-9]+\.exe)"', block.split("};")[0])
check("26 tools, sorted, none of msiexec, regsvr32, rundll32, sc, net, schtasks, shutdown",
      len(tools) == 26 and tools == sorted(tools)
      and not set(tools) & {"msiexec.exe", "regsvr32.exe", "rundll32.exe", "sc.exe", "net.exe", "schtasks.exe", "shutdown.exe"}, tools)
check("reg, find, xcopy and sdbinst (Messiah's script) are in the list", {"reg.exe", "find.exe", "xcopy.exe", "sdbinst.exe"} <= set(tools))
check("the switch is MADEIRA_SYSTEM32_X86_TOOLS, on unless exactly 0",
      'getenv("MADEIRA_SYSTEM32_X86_TOOLS")' in block and 'const BOOL toolsOn = !(toolsWant && strcmp(toolsWant, "0") == 0);' in block)
check("only an absent name or BCD's own link is (re)made; at 0 only BCD's links go",
      "if (!there || ours) {" in block and "} else if (ours) {" in block and "has_i386_set" in block)
check("the block comes after the msiexec link and before the AVX module",
      bridge.index("[msiexec64] removed BCD's system32") < start < bridge.index("madeira-bcd: the AVX opt-in lives in a separately built FEX module"))

# ---- the block on the host, against a real folder
HARNESS = r'''#import <Foundation/Foundation.h>
#include <sys/stat.h>
#include <unistd.h>
static void run(NSString *bundlePath, NSString *sys32Dir, BOOL has_i386_set) {
    NSFileManager *fm = [NSFileManager defaultManager];
''' + block + r'''
}
int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *bundle = [NSString stringWithUTF8String:argv[1]], *sys32 = [NSString stringWithUTF8String:argv[2]];
        run(bundle, sys32, strcmp(argv[3], "1") == 0);
    }
    return 0;
}
'''
clang = shutil.which("clang") or (["xcrun", "clang"] if shutil.which("xcrun") else None)
if clang:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        src = t / "h.m"
        src.write_text(HARNESS)
        exe = t / "h"
        cmd = ([clang] if isinstance(clang, str) else clang) + ["-fobjc-arc", "-framework", "Foundation", str(src), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        check("the block compiles on the host", r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            bundle = t / "Madeira.app"
            (bundle / "i386-windows").mkdir(parents=True)
            for name in tools:
                (bundle / "i386-windows" / name).write_text("MZ")
            other = t / "farm64"
            other.mkdir()
            (other / "where.exe").write_text("MZ")
            sys32 = t / "system32"
            sys32.mkdir()
            (sys32 / "find.exe").write_text("a real file")                          # a real file: stays
            (sys32 / "where.exe").symlink_to(other / "where.exe")                     # another link that resolves: stays
            (sys32 / "sort.exe").symlink_to(t / "gone" / "sort.exe")                  # a dangling link that is not BCD's: stays
            (sys32 / "reg.exe").symlink_to(t / "Old.app" / "i386-windows" / "reg.exe")  # BCD's link from an earlier install: made again

            def run(on, have_i386="1"):
                env = dict(**__import__("os").environ)
                env.pop("MADEIRA_SYSTEM32_X86_TOOLS", None)
                if on is not None:
                    env["MADEIRA_SYSTEM32_X86_TOOLS"] = on
                return subprocess.run([str(exe), str(bundle), str(sys32), have_i386], capture_output=True, text=True, env=env)

            def link(name):
                p = sys32 / name
                return __import__("os").readlink(p) if p.is_symlink() else None

            out = run(None)
            want_linked = [n for n in tools if n not in ("find.exe", "where.exe", "sort.exe")]
            check("unset (on): every absent name is linked to the i386 tool, BCD's old link is made again",
                  all(link(n) == str(bundle / "i386-windows" / n) for n in want_linked), {n: link(n) for n in want_linked})
            check("a real file, another place's link and a dangling foreign link stay",
                  (sys32 / "find.exe").read_text() == "a real file" and link("where.exe") == str(other / "where.exe")
                  and link("sort.exe") == str(t / "gone" / "sort.exe"))
            check("the log line counts them", "[x86-tools] system32: %d of 26 i386 command-line tools linked" % len(want_linked) in out.stderr
                  and "3 names already there left alone" in out.stderr and "(env.MADEIRA_SYSTEM32_X86_TOOLS=on)" in out.stderr, out.stderr)
            run(None)
            check("a second session changes nothing", all(link(n) == str(bundle / "i386-windows" / n) for n in want_linked))
            out = run("0")
            check("at 0 BCD's links go and nothing else is touched",
                  all(not (sys32 / n).exists() and not (sys32 / n).is_symlink() for n in want_linked)
                  and (sys32 / "find.exe").read_text() == "a real file" and link("where.exe") == str(other / "where.exe")
                  and link("sort.exe") == str(t / "gone" / "sort.exe")
                  and "%d of BCD's links removed" % len(want_linked) in out.stderr, out.stderr)
            out = run("1", have_i386="0")
            check("without the i386 farm nothing is linked", not any((sys32 / n).is_symlink() for n in want_linked), out.stderr)
else:
    print("note: no clang; the block was not run")

# ---- the farms
inv = (root / "docs/BINARIES-WOW64.md").read_text()
missing32 = [n for n in tools if "`i386-windows/%s`" % n not in inv]
check("every tool ships in the i386 farm (docs/BINARIES-WOW64.md)", not missing32, missing32)
in64 = [n for n in tools for d in ("aarch64-windows", "arm64ec-windows") if (root / "app/Madeira" / d / n).exists()]
check("no tool is in a 64-bit farm (the link never shadows a 64-bit program)", not in64, in64)
cmdsrc = root.parent / "madeira-bcd/wine/programs/cmd/wcmdmain.c"
if cmdsrc.exists():
    body = cmdsrc.read_text()
    check("Wine's cmd still ends a batch with ExitProcess(255) when a pipe's left program cannot start",
          "if (return_code == RETURN_CODE_CANT_LAUNCH && context) ExitProcess(255);" in body)

# ---- auto-tune
import importlib.util
spec = importlib.util.spec_from_file_location("autotune_rules", root / "build/tools/autotune_rules.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r["id"] == "bat-launch-64bit-cmd-missing-tool"), None)
check("auto-tune knows the stop: fixedIn 627, no line, also for a session that ran, off once the links are made",
      rule is not None and rule.get("fixedIn") == 627 and rule["lines"] == [] and rule.get("evaluateWhenRan") is True
      and '"[x86-tools] system32:"' in json.dumps(rule.get("none", [])), rule)
exp = json.loads((root / "tests/fixtures/autotune/messiah-bat-x86-tools-2026-11-01.expect").read_text())
check("the fixture (9ACRYW) is that stop", exp["matched"] == ["bat-launch-64bit-cmd-missing-tool"] and exp["status"] == "known", exp)
core = (root / "app/Madeira/AutoTuneCore.swift").read_text()
check("the engine lets a fixedIn rule marked evaluateWhenRan speak for a session that ran",
      "rule.evaluateWhenRan == true && (rule.noFix == true || rule.fixedIn != nil) && report.played" in core
      and "$0.evaluateWhenRan == true && ($0.noFix == true || $0.fixedIn != nil)" in core)
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents the switch", 'key: "env.MADEIRA_SYSTEM32_X86_TOOLS"' in cat)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-system32-x86-tools.py" in wf)
print("PASS: x86 tools in the 64-bit system32" if ok else "FAILED: x86 tools in the 64-bit system32")
sys.exit(0 if ok else 1)
