#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The fresh 64-bit DXMT set (owner's decision of 2026-11-15): env.MADEIRA_DXMT_FRESH = 1 and nothing else changes the DLLs a game runs.

app/Madeira/arm64ec-windows/d3d11.dll, dxgi.dll, d3d10core.dll and winemetal.dll are binaries upstream committed; each was rebuilt
from its source state (d3d11 and dxgi: dxmt 2dc8d16, with b3d3f29's memset for d3d11; d3d10core and winemetal: dxmt b15e8ba) and
matches it in every section byte (K6D, 2026-11-15). The fresh set is BCD's build of them from its pin: d3d11-src.dll
(tools/build-d3d11-dll.sh), dxgi-src.dll (tools/build-dxgi-dll.sh), d3d10core-src.dll and winemetal-src.dll
(tools/build-dxmt-src-dlls.sh), plus DXMT's internal shaders compiled from the pin, which d3d11-src.dll and d3d9-dxmt.dll load only
when the process has MADEIRA_DXMT_FRESH=1 (tools/patch-d3d11-src-metallib.py).

Checks, no Wine runs:
  * the committed DXMT DLLs are the files they were (sha256): with every key off every game runs exactly them;
  * WineProcessBridge.m's block, compiled on the host with Foundation and run against a fake bundle and prefix: without the key no
    link changes; with "1" all four names are linked to the -src files (sysx64 always, system32 in an ARM64EC session); a build that
    lacks one of the four links none and removes the variable, so no DLL acts on it; other values do nothing; the MADEIRA_D3D11_SRC
    and MADEIRA_DXGI_SRC blocks after it change nothing under the key and keep their old behaviour without it;
  * the session end removes the variable; the catalog lists the key, off by default;
  * the [dxmt-shaders] switch, applied to a copy of the pin's dxmt_command.cpp: without the variable the embedded upstream library
    loads as before; idempotent; the d3d11-src and d3d9-dxmt builds apply it to copies only;
  * tools/build-dxmt-src-dlls.sh ships only the -src names, checks exports and imported DLLs against the committed binaries, never
    marks winemetal as a Wine builtin, and its failures warn; the workflow runs it after the arm64ec Wine build, hashing the committed
    DLLs before and after, and refuses nothing for an incomplete set;
  * .gitignore keeps the outputs out of git.
"""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else f": {detail}"))
    ok &= bool(cond)


EC = R / "app/Madeira/arm64ec-windows"
# The committed binaries' hashes, with the source state each was rebuilt from (tests/host/dxmt-committed-sha256.txt). A deliberate
# replacement of one of them updates that file in the same commit.
HASHES = R / "tests/host/dxmt-committed-sha256.txt"
want = dict(l.split()[::-1] for l in HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")) if HASHES.is_file() else {}
check("the committed DXMT hashes are recorded", set(want) == {"d3d11.dll", "dxgi.dll", "d3d10core.dll", "winemetal.dll"}, str(sorted(want)))
for name, h in sorted(want.items()):
    f = EC / name
    got = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else "missing"
    check(f"committed arm64ec {name} is the file it was", got == h, got)

# --- WineProcessBridge.m ---
m = (R / "app/Madeira/WineProcessBridge.m").read_text()
a = m.find("/* madeira-bcd (2026-11-15, owner's decision: the frozen 64-bit DLLs audited and rebuilt")
b = m.find("/* madeira-bcd: env.MADEIRA_DXGI_SRC = 1 (the game's own file) runs the")
c = m.find("/* madeira-bcd: env.MADEIRA_D3D11_SRC = 1 (the game's own file) runs the")
d = m.find("/* madeira-bcd (2026-10-23): env.MADEIRA_D3D9_X64 = dxmt")
check("the key's block sits after the farms are relinked and before the DXGI_SRC, D3D11_SRC and D3D9_X64 blocks",
      m.find('{ "sysx64",  "arm64ec-windows" }') < a < b < c < d and min(a, b, c, d) > 0)
fresh_blk = m[a:b]
check("only the value 1 counts", 'strcmp(dxmtFresh, "1") == 0' in fresh_blk)
check("the session end removes the variable", 'unsetenv("MADEIRA_DXMT_FRESH");' in m[m.find('unsetenv("MADEIRA_D3D11_SRC");'):][:400])


def blk(start):
    """The `{ ... }` statement right after the comment that starts at `start`."""
    i = m.index("{\n", m.index("*/", start))
    depth, j = 0, i
    while True:
        if m[j] == "{":
            depth += 1
        elif m[j] == "}":
            depth -= 1
            if depth == 0:
                return m[i:j + 1]
        j += 1


HARNESS = r'''#import <Foundation/Foundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
/* The D3D11_SRC block asks the GPU (2026-11-20): BCD_TEST_GPU_BC=1 stands for a GPU that samples BC; the level reader is the file's own. */
static bool bcd_gpu_samples_bc(void) { const char *v = getenv("BCD_TEST_GPU_BC"); return v && v[0] == '1'; }
LEVEL2_FN
int main(int argc, char **argv) {
  @autoreleasepool {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *bundlePath = @(argv[1]);
    NSString *prefix = @(argv[2]);
    NSString *sys32Dir = [prefix stringByAppendingPathComponent:@"drive_c/windows/system32"];
    const BOOL use_arm64ec = argv[3][0] == '1';
    (void)argc;
    FRESH_BLOCK
    DXGI_BLOCK
    D3D11_BLOCK
    printf("FRESH_ENV=%s\n", getenv("MADEIRA_DXMT_FRESH") ? getenv("MADEIRA_DXMT_FRESH") : "(unset)");
  }
  return 0;
}
'''
clang = shutil.which("xcrun")
if not clang or sys.platform != "darwin":
    print("SKIP the block's run: no xcrun/Foundation on this host")
else:
    la = m.find("static bool bcd_mip_clamp_auto_level2(const char *v)")
    level2 = m[la:m.index("\n}\n", la) + 2] if la > 0 else ""
    check("WineProcessBridge.m has bcd_mip_clamp_auto_level2 for the D3D11_SRC block", bool(level2))
    src = (HARNESS.replace("LEVEL2_FN", level2).replace("FRESH_BLOCK", blk(a)).replace("DXGI_BLOCK", blk(b))
           .replace("D3D11_BLOCK", blk(c)))
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        (t / "h.m").write_text(src)
        p = subprocess.run(["xcrun", "clang", "-fobjc-arc", "-framework", "Foundation", "-o", str(t / "h"), str(t / "h.m")],
                           capture_output=True, text=True)
        check("the block compiles on the host with Foundation", p.returncode == 0, p.stderr[-800:])
        names = ["d3d11.dll", "dxgi.dll", "d3d10core.dll", "winemetal.dll"]
        srcs = ["d3d11-src.dll", "dxgi-src.dll", "d3d10core-src.dll", "winemetal-src.dll"]

        def scenario(tag, env, ec, have=srcs):
            bundle, prefix = t / tag / "bundle", t / tag / "prefix"
            (bundle / "arm64ec-windows").mkdir(parents=True)
            for n in names + list(have):
                (bundle / "arm64ec-windows" / n).write_text(n)
            for farm in ("system32", "sysx64"):   # the farms as the session relinked them from the bundle a moment before
                (prefix / "drive_c/windows" / farm).mkdir(parents=True)
                for n in names:
                    os.symlink(bundle / "arm64ec-windows" / n, prefix / "drive_c/windows" / farm / n)
            e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
            e.update(env)
            r = subprocess.run([str(t / "h"), str(bundle), str(prefix), "1" if ec else "0"], capture_output=True, text=True, env=e)
            links = {}
            for farm in ("system32", "sysx64"):
                for n in names:
                    links[(farm, n)] = os.path.basename(os.readlink(prefix / "drive_c/windows" / farm / n))
            return r, links

        if p.returncode == 0:
            committed = {(f, n): n for f in ("system32", "sysx64") for n in names}
            r, l = scenario("off", {}, True)
            check("no key: every farm link stays on the committed DLL", l == committed and "MADEIRA_DXMT_FRESH" not in r.stderr, str(l))
            r, l = scenario("ec", {"MADEIRA_DXMT_FRESH": "1"}, True)
            check("key in an ARM64EC session: all four names -> the -src files in system32 and sysx64",
                  l == {(f, n): s for f in ("system32", "sysx64") for n, s in zip(names, srcs)} and "FRESH_ENV=1" in r.stdout, str(l))
            r, l = scenario("dock", {"MADEIRA_DXMT_FRESH": "1"}, False)
            check("key in an aarch64 (Dock) session: sysx64 only",
                  l == {**committed, **{("sysx64", n): s for n, s in zip(names, srcs)}}, str(l))
            r, l = scenario("missing", {"MADEIRA_DXMT_FRESH": "1"}, True, have=srcs[:3])
            check("a build without winemetal-src.dll links none and removes the variable",
                  l == committed and "FRESH_ENV=(unset)" in r.stdout and "has no winemetal-src.dll" in r.stderr, r.stderr[-300:])
            for v in ("2", "true", "0", ""):
                r, l = scenario("value" + (v or "empty"), {"MADEIRA_DXMT_FRESH": v}, True)
                check(f"value {v!r} does nothing", l == committed, str(l))
            r, l = scenario("all", {"MADEIRA_DXMT_FRESH": "1", "MADEIRA_D3D11_SRC": "1", "MADEIRA_DXGI_SRC": "1"}, True)
            check("with MADEIRA_D3D11_SRC and MADEIRA_DXGI_SRC as well: the same four links",
                  l == {(f, n): s for f in ("system32", "sysx64") for n, s in zip(names, srcs)}, str(l))
            r, l = scenario("src", {"MADEIRA_D3D11_SRC": "1"}, True)
            check("MADEIRA_D3D11_SRC alone: only d3d11.dll, as before",
                  l == {**committed, ("system32", "d3d11.dll"): "d3d11-src.dll", ("sysx64", "d3d11.dll"): "d3d11-src.dll"}, str(l))
            # 2026-11-20 (the owner's rule that nothing that works may break): MADEIRA_MIP_CLAMP_AUTO = 2 links d3d11-src.dll only on a GPU
            # that cannot sample BC, and only when the game sets no MADEIRA_D3D11_SRC of its own (tests/host/check-dxmt-bc-half.py).
            src_d3d11 = {**committed, ("system32", "d3d11.dll"): "d3d11-src.dll", ("sysx64", "d3d11.dll"): "d3d11-src.dll"}
            r, l = scenario("bc-yes", {"MADEIRA_MIP_CLAMP_AUTO": "2", "BCD_TEST_GPU_BC": "1"}, True)
            check("value 2 on a GPU that samples BC: the committed d3d11.dll stays",
                  l == committed and "samples BC textures: the committed d3d11.dll is kept" in r.stderr, str(l) + r.stderr[-300:])
            r, l = scenario("bc-no", {"MADEIRA_MIP_CLAMP_AUTO": "2", "BCD_TEST_GPU_BC": "0"}, True)
            check("value 2 on a GPU without BC: d3d11.dll -> d3d11-src.dll in both farms",
                  l == src_d3d11 and "MADEIRA_MIP_CLAMP_AUTO=2 on a GPU without BC sampling: d3d11.dll -> d3d11-src.dll" in r.stderr,
                  str(l) + r.stderr[-300:])
            r, l = scenario("bc-no-dock", {"MADEIRA_MIP_CLAMP_AUTO": " 2 ", "BCD_TEST_GPU_BC": "0"}, False)
            check("value ' 2 ' on a GPU without BC in an aarch64 (Dock) session: sysx64 only",
                  l == {**committed, ("sysx64", "d3d11.dll"): "d3d11-src.dll"}, str(l))
            r, l = scenario("bc-no-own0", {"MADEIRA_MIP_CLAMP_AUTO": "2", "MADEIRA_D3D11_SRC": "0", "BCD_TEST_GPU_BC": "0"}, True)
            check("the game's own MADEIRA_D3D11_SRC = 0 wins over value 2", l == committed and "that value decides" in r.stderr, str(l))
            for v in ("1", "22", "2x", ""):
                r, l = scenario("bc-no-v" + (v or "empty"), {"MADEIRA_MIP_CLAMP_AUTO": v, "BCD_TEST_GPU_BC": "0"}, True)
                check(f"MADEIRA_MIP_CLAMP_AUTO {v!r} on a GPU without BC links nothing", l == committed, str(l))
            r, l = scenario("bc-no-missing", {"MADEIRA_MIP_CLAMP_AUTO": "2", "BCD_TEST_GPU_BC": "0"}, True, have=srcs[1:])
            check("value 2 on a GPU without BC in a build without d3d11-src.dll: the committed one stays and the log says so",
                  l == committed and "MADEIRA_MIP_CLAMP_AUTO=2 but this build has no d3d11-src.dll" in r.stderr, r.stderr[-300:])

cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog lists env.MADEIRA_DXMT_FRESH, off by default",
      re.search(r'key: "env\.MADEIRA_DXMT_FRESH".*kind: \.bool, defaultValue: "0"', cat) is not None)

# --- the [dxmt-shaders] switch ---
dxmt = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
cmd_cpp = dxmt / "dxmt/dxmt_command.cpp"
if not cmd_cpp.is_file():
    print(f"SKIP the switch's replay: no dxmt sources at {dxmt}")
else:
    with tempfile.TemporaryDirectory() as t:
        copy = pathlib.Path(t) / "dxmt_command.cpp"
        shutil.copy(cmd_cpp, copy)
        before = cmd_cpp.read_text()
        run = lambda: subprocess.run([sys.executable, str(R / "tools/patch-d3d11-src-metallib.py"), str(copy)], capture_output=True, text=True)
        r1 = run(); s1 = copy.read_text(); r2 = run()
        check("the switch applies to a copy of the pin's dxmt_command.cpp", r1.returncode == 0 and "[dxmt-shaders]" in s1)
        check("second run: already patched, unchanged", r2.returncode == 0 and "already" in r2.stdout and copy.read_text() == s1)
        check("without MADEIRA_DXMT_FRESH=1 it loads the embedded upstream library exactly as before",
              "else\n    library_ = device.newLibrary(dxmt_command, dxmt_command_len, error);" in s1
              and 'env::getEnvVar("MADEIRA_DXMT_FRESH") == "1"' in s1 and s1.count("newLibrary(") == 2)
        check("the submodule's dxmt_command.cpp is untouched", cmd_cpp.read_text() == before)
d9 = (R / "tools/build-d3d9-x64-dll.sh").read_text()
check("d3d9-dxmt.dll applies the switch to a copy only and compiles that copy",
      'cp "$X/dxmt_command.cpp" "$OUT/tree/src/dxmt/dxmt_command.cpp"' in d9
      and 'tools/patch-d3d11-src-metallib.py" "$OUT/tree/src/dxmt/dxmt_command.cpp"' in d9
      and '[ "$s" = dxmt_command ] && src="$DXMT_COMMAND_CPP"' in d9
      and 'metal -std="$DXMT_METAL_STD" --target=air64-apple-macos14.0' in d9)

# --- tools/build-dxmt-src-dlls.sh ---
sh = (R / "tools/build-dxmt-src-dlls.sh").read_text()
check("it ships only d3d10core-src.dll and winemetal-src.dll, never a committed name",
      '"$SHIP/$f-src.dll"' in sh and not re.search(r'(cp|mv)[^\n]*"\$SHIP/(d3d10core|winemetal|d3d11|dxgi)\.dll"', sh))
check("it compares exports and imported DLLs with the committed binaries",
      'diff "$OUT/$f.exports.committed" "$OUT/$f.exports.built"' in sh and 'diff "$OUT/$f.imports.committed" "$OUT/$f.imports.built"' in sh)
check("winemetal imports the committed one's ntdll functions only, and carries no Wine builtin mark",
      'diff "$OUT/winemetal.ntdll.committed" "$OUT/winemetal.ntdll.built"' in sh and 'grep -aqF "Wine builtin DLL" "$OUT/winemetal.dll" && fail' in sh)
check("its failures warn and leave the bundle alone",
      "::warning::d3d10core-src.dll / winemetal-src.dll NOT built" in sh and sh.index("fail()") < sh.index('mv -f "$SHIP/$f-src.dll.tmp"'))
check("it builds src/winemetal/meson.build's and src/d3d10/meson.build's sources and checks the lists",
      'WM_SRC=(main airconv_thunks winemetal_thunks wmt_api_census)' in sh and "src/winemetal/meson.build lists other sources" in sh
      and "src/d3d10/meson.build lists other d3d10core sources" in sh)

# --- the workflow ---
wf = (R / ".github/workflows/build-ipa.yml").read_text()
steps = re.findall(r"\n      - name: (.+)", wf)
idx = {n: i for i, n in enumerate(steps)}
bs = next((i for i, n in enumerate(steps) if n.startswith("Build d3d10core-src.dll and winemetal-src.dll")), None)
check("the workflow builds the two DLLs after the arm64ec Wine build and before the PE DLLs are verified",
      bs is not None and idx["Build extra Wine DLLs for arm64ec"] < bs < idx["Verify committed PE DLLs"])
if bs is not None:
    block = wf[wf.index("- name: " + steps[bs]):]
    block = block[:block.index("\n      - name:", 10)]
    check("that step hashes the committed DLLs before and after and fails the run if one changed",
          "continue-on-error" not in block and 'shipped > "$T/before"' in block and 'shipped > "$T/after"' in block
          and "exit 1" in block and "bash tools/build-dxmt-src-dlls.sh ||" in block)
check("the host checks run in the workflow",
      "python3 tests/host/check-dxmt-fresh.py" in wf and "python3 tests/host/check-d3d11-src-fixes.py" in wf)
check("an incomplete fresh set is said, not refused",
      "the fresh 64-bit DXMT set is incomplete" in wf and "::warning::the fresh 64-bit DXMT set is incomplete" in wf)
gi = (R / ".gitignore").read_text()
check(".gitignore keeps the outputs out of git",
      all(x in gi for x in ("build/dxmt-src-dlls/", "app/Madeira/arm64ec-windows/d3d10core-src.dll",
                            "app/Madeira/arm64ec-windows/winemetal-src.dll")))
print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
