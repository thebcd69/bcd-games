#!/usr/bin/env python3
"""SwapDeviceContextState (tools/patch-dxmt-context-state-swap.py) and the
opt-in source build of DXMT's 64-bit d3d11.dll around it; no Wine runs.

Applies the patch to a copy of dxmt/src/d3d11 (or $DXMT_SRC/src/d3d11) through
its optional root argument and checks: IMPLEMENT_ME is gone from the method,
the one-time "[d3d11] madeira-bcd: SwapDeviceContextState in use" line is in,
a second run changes nothing, the submodule itself stays untouched. Then the
wiring: tools/build-d3d11-dll.sh patches a copy, compares its exports and
imported DLLs with the committed d3d11.dll, checks the marker and ships
d3d11-src.dll (never d3d11.dll); the workflow builds it before any DXMT patch
step without failing the run; WineProcessBridge.m links it in only for
env.MADEIRA_D3D11_SRC = 1; the catalog lists the switch, off by default.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
dxmt = Path(os.environ.get("DXMT_SRC", root / "dxmt"))
d3d11 = dxmt / "src/d3d11"
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


if not (d3d11 / "d3d11_context_impl.cpp").exists():
    print("note: %s not checked out (set DXMT_SRC); patch checks skipped" % d3d11)
else:
    before = (d3d11 / "d3d11_context_impl.cpp").read_text()
    with tempfile.TemporaryDirectory() as t:
        copy = Path(t) / "d3d11"
        shutil.copytree(d3d11, copy)
        run = lambda: subprocess.run([sys.executable, str(root / "tools/patch-dxmt-context-state-swap.py"), str(copy)],
                                     capture_output=True, text=True, cwd=t)
        r = run()
        check("patch applies to the directory given as argument", r.returncode == 0 and "patched" in r.stdout)
        src = (copy / "d3d11_context_impl.cpp").read_text()
        body = src[src.index("SwapDeviceContextState(ID3DDeviceContextState *pState"):]
        body = body[:body.index("\n  }\n")]
        check("SwapDeviceContextState no longer IMPLEMENT_ME", "IMPLEMENT_ME" not in body and "state_ = std::move(next->state);" in body)
        check("first swap logs the marker line once",
              'Logger::info("[d3d11] madeira-bcd: SwapDeviceContextState in use (context state swap)")' in body
              and "std::exchange(s_swapNoted, true)" in body)
        check("state object holds a D3D11ContextState", "D3D11ContextState state;" in (copy / "d3d11_context_state.hpp").read_text())
        once = src
        r = run()
        check("second run: already patched, file unchanged", r.returncode == 0 and "already patched" in r.stdout
              and (copy / "d3d11_context_impl.cpp").read_text() == once)
    check("the submodule is untouched", (d3d11 / "d3d11_context_impl.cpp").read_text() == before)

# --- the build script ---
sh = (root / "tools/build-d3d11-dll.sh").read_text()
check("build script patches a copy, never the submodule",
      'cp -R "$D/src/d3d11" "$D/src/d3d10" "$OUT/tree/src/"' in sh
      and 'tools/patch-dxmt-context-state-swap.py" "$OUT/tree/src/d3d11"' in sh)
check("build script compares exports and imported DLLs with the committed d3d11.dll",
      'exports "$SHIP/d3d11.dll" > "$OUT/exports.upstream"' in sh and 'diff "$OUT/exports.upstream" "$OUT/exports.built"' in sh
      and 'diff "$OUT/imports.upstream" "$OUT/imports.built"' in sh)
check("build script greps the patch marker in the result",
      'MARK="madeira-bcd: SwapDeviceContextState in use"' in sh and 'grep -aqF "$MARK" "$OUT/d3d11.dll"' in sh)
check("build script checks the unpatched build against upstream's binary",
      'link_dll "$OUT/plain/d3d11.dll"' in sh and 'comm -12 "$OUT/plain/nm.upstream" "$OUT/plain/nm.built"' in sh)
check("build script ships d3d11-src.dll and never overwrites d3d11.dll",
      'DEST="$SHIP/d3d11-src.dll"' in sh and not re.search(r'(cp|mv)[^\n]*"\$SHIP/d3d11\.dll"', sh))
check("build script failures are warnings that leave the bundle alone",
      "::warning::d3d11-src.dll NOT built" in sh and sh.index("fail()") < sh.index('mv -f "$DEST.tmp" "$DEST"'))

# --- the workflow ---
wf = (root / ".github/workflows/build-ipa.yml").read_text()
steps = re.findall(r"\n      - name: (.+)", wf)
idx = {n: i for i, n in enumerate(steps)}
b = next((i for i, n in enumerate(steps) if n.startswith("Build d3d11-src.dll")), None)
# "Package unsigned IPA" until the paired apps (ca82558), "Package Madeira and Madeira2 unsigned IPAs" since
package = next((i for i, n in enumerate(steps) if n.startswith("Package") and "unsigned IPA" in n), None)
check("workflow has the d3d11-src.dll step", b is not None)
check("workflow has the step that packages the unsigned IPA", package is not None)
if b is not None and package is not None:
    first_dxmt_patch = min(i for i, n in enumerate(steps) if n.startswith("Patch DXMT") or n.startswith("Patch winemetal")
                           or n.startswith("Patch airconv"))
    check("it runs after llvm-mingw and before every DXMT patch step",
          idx["Install ninja/meson and fetch llvm-mingw"] < b < first_dxmt_patch)
    check("it runs before the IPA is packaged", b < idx["Verify committed PE DLLs"] < package)
    block = wf[wf.index("- name: " + steps[b]):]
    block = block[:block.index("\n      - name:", 10)]
    check("its failure does not fail the run", "continue-on-error: true" in block and "bash tools/build-d3d11-dll.sh" in block)

# --- the app ---
m = (root / "app/Madeira/WineProcessBridge.m").read_text()
blk = m[m.index('getenv("MADEIRA_D3D11_SRC")') - 200:]
blk = blk[:blk.index("upstream's d3d11.dll stays")]
check("WineProcessBridge links d3d11-src.dll for an explicit source selection or Auto HDR",
      "d3d11Src && d3d11Src[0] == '1'" in blk and '@"d3d11-src.dll"' in blk
      and '|| autoHDR' in blk and 'BOOL autoHDR = hdr && !strcmp(hdr, "1");' in blk
      and 'stringByAppendingPathComponent:@"d3d11.dll"' in blk and "drive_c/windows/sysx64" in blk
      and "if (use_arm64ec) [dirs addObject:sys32Dir];" in blk)
check("the switch block comes after the farms are relinked from the bundle",
      m.index('{ "sysx64",  "arm64ec-windows" }') < m.index('getenv("MADEIRA_D3D11_SRC")'))
gi = (root / ".gitignore").read_text()
check(".gitignore keeps the built DLL out of git", "app/Madeira/arm64ec-windows/d3d11-src.dll" in gi and "build/dxmt-d3d11/" in gi)
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("catalog lists env.MADEIRA_D3D11_SRC, off by default",
      re.search(r'key: "env.MADEIRA_D3D11_SRC".*kind: \.bool, defaultValue: "0"', cat) is not None)

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
