#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Direct3D 10/11 fixes d3d11-src.dll lacked (owner's decision of 2026-11-15) are in tools/build-d3d11-dll.sh's BCD_PATCHES; no
Wine runs, nothing is compiled.

The audit of the CI-only DXMT patch steps found three that never reached a 64-bit DLL: the short-constant-buffer and 16-bit index
fixes (dxmt 1b4f3dc keeps them inside `#if defined(__i386__)`), the tiny fullscreen window (util, which d3d11-src.dll compiled from
the pristine submodule; the i386 build uses wsi_window_win32.cpp) and the opt-in fault report (its reader is #ifndef __i386__). This
replays the copy steps of tools/build-d3d11-dll.sh (context-state swap, small rings, every BCD_PATCHES script in its order, the
Lossless Scaling bridge, the [dxmt-shaders] switch) twice on a copy of the pinned dxmt/src, and again on a copy the workflow's whole
DXMT/airconv/winemetal patch chain already patched (what a re-run meets): every script exits 0, the second round changes nothing,
every marker BCD_PATCHES names is in the patched copy, the 25 i386 gates are open, and the submodule is untouched. Then the wiring of
the build script: the copy holds util and winemetal, d3d11-src.dll links the copy's util and the recipe DLL the submodule's, the new
markers are checked in the result and not in the recipe DLL, DXMT's internal shaders are compiled with the flags of DXMT's meson build
and build/dxmt-ios/build.sh, and the copy-only scripts stay outside the workflow's chain and the i386 farm's cache key.

SKIP (exit 0) of the replay when no dxmt sources are available (set DXMT_SRC_DIR to a directory holding an unpatched `src`)."""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SRC = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else f": {detail}"))
    ok &= bool(cond)


sh = (R / "tools/build-d3d11-dll.sh").read_text()
m = re.search(r"\nBCD_PATCHES=\((.*?)\)\n", sh, re.S)
check("tools/build-d3d11-dll.sh has its BCD_PATCHES list", m is not None)
pairs = re.findall(r'"([^"]+\.py)" "((?:[^"\\]|\\.)*)"', m.group(1)) if m else []
names = [p for p, _ in pairs]
for new, marker in (("patch-dxmt-wsi-tiny-fullscreen.py", "[wsi-fullscreen] a fullscreen swapchain"),
                    ("patch-dxmt-d3d11-fault-info.py", "[fault-enc] madeira-bcd frame "),
                    ("patch-d3d11-src-all-arch.py", "[cb-short] madeira-bcd #")):
    check(f"BCD_PATCHES has {new} with its marker", (new, marker) in pairs)
check("patch-d3d11-src-all-arch.py comes last (every other script finds its anchors first)", names and names[-1] == "patch-d3d11-src-all-arch.py")
check("the 11 entries of 2026-11-14 are all still there",
      all(n in names for n in ("patch-dxmt-copy-structure-count.py", "patch-dxmt-buffer-rtv.py", "patch-dxmt-srv-getdesc-mips.py",
                               "patch-dxmt-d11-query-stuck.py", "patch-dxmt-tess-pso-failed.py",
                               "patch-dxmt-update-subresource-staging.py", "patch-dxmt-d11-swapchain-rotation.py",
                               "patch-dxmt-texture-limit.py", "patch-dxmt-layer-format.py", "patch-dxmt-view-private-ref.py",
                               "patch-dxmt-lsfg.py")))


def snapshot(d):
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob("*") if p.is_file()}


def copy_steps(src, work):
    """tools/build-d3d11-dll.sh's copy: the steps that change it, in the script's order. Returns [(round, step, rc, output)]."""
    tree = work / "tree/src"
    tree.mkdir(parents=True)
    for d in ("d3d11", "d3d10", "dxmt", "util", "winemetal"):
        shutil.copytree(src / d, tree / d)
    (work / "patchroot").mkdir()
    os.symlink(work / "tree", work / "patchroot/dxmt")
    out = []

    def run(rnd, step, args, cwd):
        p = subprocess.run([sys.executable, str(R / "tools" / step)] + args, cwd=cwd, capture_output=True, text=True)
        out.append((rnd, step, p.returncode, (p.stdout + p.stderr).strip()))

    for rnd in (1, 2):
        run(rnd, "patch-dxmt-context-state-swap.py", [str(tree / "d3d11")], work)
        run(rnd, "patch-d3d11-src-small-rings.py", [str(tree / "dxmt")], work)
        for script in names:
            run(rnd, script, [], work / "patchroot")
        run(rnd, "patch-d3d11-src-lsfg-bridge.py", [str(tree / "d3d11")], work)
        run(rnd, "patch-d3d11-src-metallib.py", [str(tree / "dxmt/dxmt_command.cpp")], work)
    return tree, out


def chain_patch(src, work):
    """The workflow's DXMT/airconv/winemetal chain on a copy, as check-dxmt-patch-chain.py runs it (with the workflow's flags)."""
    t = work / "chain"
    shutil.copytree(src, t / "dxmt/src")
    (t / "build").mkdir()
    shutil.copy(R / "build/madeira_cfg.h", t / "build/madeira_cfg.h")
    shutil.copytree(R / "research/remote-metal", t / "research/remote-metal")
    wf = (R / ".github/workflows/build-ipa.yml").read_text()
    bad = []
    for s, flags in re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)((?: --[\w-]+)*)", wf):
        args = flags.split()
        if s.endswith("patch-dxmt-metal-atomics.py"):   # it defaults to <repository>/dxmt; point it at the copy
            args += ["--source", str(t / "dxmt/src/airconv/shaders/air_tessellation.metal")]
        p = subprocess.run([sys.executable, str(R / s)] + args, cwd=t, capture_output=True, text=True)
        if p.returncode:
            bad.append(s)
    return t / "dxmt/src", bad


GATE = "#if defined(__i386__)  /* madeira-bcd: 32-bit (i386) builds only, as the fork shipped it */"
if not (SRC / "d3d11/d3d11_context_impl.cpp").is_file() or not (SRC / "util/wsi_window_headless.cpp").is_file():
    print(f"SKIP replay: no dxmt sources at {SRC} (check out the submodule or set DXMT_SRC_DIR)")
else:
    before = snapshot(SRC)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        chained, bad = chain_patch(SRC, tmp)
        check("the workflow's chain applies to the copy (the already patched tree)", not bad, str(bad))
        for kind, src in (("pinned", SRC), ("chain-patched", chained)):
            work = tmp / kind
            tree, out = copy_steps(src, work)
            failed = [(r, s, o[-200:]) for r, s, rc, o in out if rc]
            check(f"{kind} tree: every copy step exits 0, both rounds", not failed, str(failed))
            again = [(s, o[-160:]) for r, s, rc, o in out
                     if r == 2 and re.search(r"(?<!already )patched|gates opened|loads the library", o.replace("already patched", ""))
                     and "already" not in o]
            check(f"{kind} tree: the second round changes nothing", not again, str(again))
            text = "\n".join(p.read_text(errors="ignore") for p in tree.rglob("*") if p.is_file() and p.suffix in (".cpp", ".hpp", ".h", ".c"))
            missing = [mk for _, mk in pairs if mk.replace('\\"', '"') not in text]
            check(f"{kind} tree: every BCD_PATCHES marker is in the patched copy", not missing, str(missing))
            for mk in ("[idx-align] madeira-bcd", "[vb-align] madeira-bcd slot", "[dxmt-shaders] madeira-bcd", "[wsi-fullscreen]",
                       "[fault-enc] madeira-bcd frame "):
                check(f"{kind} tree: {mk} is in the patched copy", mk in text)
            check(f"{kind} tree: no i386 gate of dxmt 1b4f3dc is left, 25 are open",
                  GATE not in text and text.count("tools/patch-d3d11-src-all-arch.py") == 25)
            cmd = (tree / "dxmt/dxmt_command.cpp").read_text()
            check(f"{kind} tree: without MADEIRA_DXMT_FRESH=1 the embedded upstream library loads as before",
                  'env::getEnvVar("MADEIRA_DXMT_FRESH") == "1"' in cmd
                  and "else\n    library_ = device.newLibrary(dxmt_command, dxmt_command_len, error);" in cmd
                  and "library_ = device.newLibrary(dxmt_command_fresh, dxmt_command_fresh_len, error);" in cmd)
    check("the submodule is untouched", snapshot(SRC) == before)

# --- the build script ---
check("the copy holds util and winemetal", 'cp -R "$D/src/util" "$D/src/winemetal" "$OUT/tree/src/"' in sh)
check("the recipe DLL compiles and links the submodule's util, d3d11-src.dll the copy's",
      'job "$D" "$U/$s" "$o" util; PLAIN_UTIL_OBJ+=("$o")' in sh
      and 'job "$OUT/tree" "$OUT/tree/src/util/$s" "$o" util; UTIL_OBJ+=("$o")' in sh
      and '"$AR" csrDT "$OUT/plain/libutil.a" "${PLAIN_UTIL_OBJ[@]}"' in sh and '"$AR" csrDT "$OUT/libutil.a" "${UTIL_OBJ[@]}"' in sh
      and 'local util="${dxmt%/libdxmt.a}/libutil.a"' in sh and '-lwinemetal "$util" -lntdll' in sh
      and 'link_dll "$OUT/plain/d3d11.dll" "$OUT/plain/libdxmt.a" ' in sh and 'link_dll "$OUT/d3d11.dll" "$OUT/libdxmt.a" ' in sh)
check("every object includes the util headers of its own root (the copy's for d3d11-src.dll; $U, as before, for the recipe DLL)",
      'util)   inc=(-I"$root/src/util" "${INC_BASE[@]}") ;;' in sh and 'dxmt)   inc=(-I"$root/src/dxmt" -I"$root/src/util" "${INC_DXMT[@]}") ;;' in sh
      and '-I"$D/src/winemetal" -I"$root/src/util" "${INC_BASE[@]}" -I"$OUT/gen") ;;' in sh)
check("the result is checked for [idx-align] and [vb-align], and the recipe DLL must not have them",
      '"[idx-align] madeira-bcd" "[vb-align] madeira-bcd slot"' in sh
      and 'grep -aqF "$m" "$OUT/plain/d3d11.dll" && fail' in sh)
nat = (R / "build/dxmt-ios/build.sh").read_text()
check("DXMT's internal shaders are compiled with the native build's flags (-std=metal3.1, AIR target macOS 14)",
      'DXMT_METAL_STD="${DXMT_METAL_STD:-metal3.1}"' in sh and 'metal -std="$DXMT_METAL_STD" --target=air64-apple-macos14.0' in sh
      and 'DXMT_METAL_STD="${DXMT_METAL_STD:-metal3.1}"' in nat and 'metal -std="$DXMT_METAL_STD" --target=air64-apple-macos14.0' in nat)
check("the [dxmt-shaders] switch is applied to the copy's dxmt_command.cpp only, and checked in the result",
      'tools/patch-d3d11-src-metallib.py" "$OUT/tree/src/dxmt/dxmt_command.cpp"' in sh
      and 'grep -aqF "[dxmt-shaders] madeira-bcd" "$OUT/d3d11.dll" || fail' in sh)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("the copy-only scripts are never run on the submodule by the workflow",
      not re.search(r"python3 tools/patch-d3d11-src-(all-arch|metallib)\.py", wf))
check("their names stay outside the i386 farm's cache key (tools/patch-dxmt-*.py)",
      not (R / "tools/patch-dxmt-all-arch.py").exists() and (R / "tools/patch-d3d11-src-all-arch.py").is_file()
      and "'tools/patch-dxmt-*.py'" in wf)
print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
