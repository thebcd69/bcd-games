#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-airconv-d9-psize-topology.py: DXMT Direct3D 9 vertex shaders that write the point size (night round 2026-11-11, G1 S6).

Alan Wake (778A58, build 634; A6232G, build 625: 67 pipelines a run) failed pipelines with AGX's "Vertex shader writes point size but
inputPrimitiveTopology is MTLPrimitiveTopologyClassTriangle": dxso_compile.cpp defined the AIR [[point_size]] output of every vertex
shader that declares dcl_psize / writes oPts, whatever the draw's topology. The converter's `vs_inject_point_size` (true only for a
point-list draw, d3d9_point_size.hpp) now gates the output. Checks, on a copy of dxmt/src (or $DXMT_SRC_DIR/src):
  - CI runs this check and the patch with the other airconv patches; the auto-tune rule is fixed in 652 and its fixture is there;
  - the patch applies, a second run changes nothing, each of the three anchors refuses it when it moves, and only those three places
    change;
  - with a host LLVM (LLVM_CONFIG, or Homebrew's llvm): a harness hand-assembles a vs_3_0 (dcl_psize o1; mov o1, c0.x), a vs_2_0 (mov
    oPts, c0) and a vertex shader without psize, builds the vertex function with compile_dxso for both variants (inject point size
    off = a triangle draw, on = a point-list draw) against the unpatched and the patched converter, and asserts: unpatched, the
    triangle variant of a psize shader HAS the air.point_size output (the bug); patched it has none, the oPts slot is still there for
    the shader's stores and the point-list variant is the same IR as before; a shader without psize is the same IR in every case.
    LLVM 22 differs from the LLVM 15 airconv is built against; the check applies host-only shims to ITS COPY (setOpaquePointers, Triple,
    enumerate by value, attributes that are gone) and skips the IR part when the harness does not build.
SKIP (exit 0) of the source part when no dxmt sources are available.
"""
import difflib
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
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
step = wf.find("      - name: Patch airconv D3D9 point size in triangle pipelines\n")
check("CI runs this check and the patch, after the DEC3N patch, with the other native converter patches",
      "python3 tests/host/check-airconv-d9-psize-topology.py" in wf and step > 0
      and "run: python3 tools/patch-airconv-d9-psize-topology.py" in wf[step:step + 200]
      and step > wf.index("run: python3 tools/patch-airconv-dec3n.py"))
rules = (R / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "dxmt-d3d9-psize-triangle"'):] if '"id": "dxmt-d3d9-psize-triangle"' in rules else ""
rb = rb[:rb.index("\n    },\n")] if rb else ""
check("the auto-tune rule is fixed in 652, evaluates a session that ran, and names the AGX error; its fixture is there",
      '"fixedIn": 652' in rb and '"evaluateWhenRan": True' in rb
      and "Vertex shader writes point size but inputPrimitiveTopology is MTLPrimitiveTopologyClass" in rb
      and (R / "tests/fixtures/autotune/alan-wake-d3d9-psize-triangle-2026-11-08.expect").is_file(), rb[:300])
check("the old rule dxmt-d3d9-vs-ps-interface is untouched by this fixture (its signals are not in it)",
      "not written by vertex shader" not in (R / "tests/fixtures/autotune/alan-wake-d3d9-psize-triangle-2026-11-08.log").read_text(errors="replace")
      if (R / "tests/fixtures/autotune/alan-wake-d3d9-psize-triangle-2026-11-08.log").is_file() else True)

src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (src / "airconv/dxso_compile.cpp").is_file():
    alt = pathlib.Path.home() / "madeira-bcd/dxmt/src"
    src = alt if (alt / "airconv/dxso_compile.cpp").is_file() else src
FILE = "airconv/dxso_compile.cpp"
if not (src / FILE).is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: DXMT D3D9 point size topology" if ok else "FAILED: DXMT D3D9 point size topology")
    sys.exit(0 if ok else 1)
if "madeira-bcd: d9-psize-topology" in (src / FILE).read_text():
    print("note: the dxmt tree is already patched; only the static checks ran")
    print("PASS: DXMT D3D9 point size topology" if ok else "FAILED: DXMT D3D9 point size topology")
    sys.exit(0 if ok else 1)

HARNESS = r'''
#include "dxso_compile.hpp"
#include "airconv_public.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Verifier.h"
#include "llvm/Support/raw_ostream.h"
#include "llvm/TargetParser/Triple.h"
#include <cstdio>
#include <cstdlib>
#include <vector>
using namespace llvm;
int main(int argc, char **argv) {
  std::vector<uint32_t> words; FILE *f = fopen(argv[1], "r"); unsigned w;
  while (f && fscanf(f, "%x", &w) == 1) words.push_back(w);
  if (f) fclose(f);
  auto *sh = dxmt::dxso_shader_initialize(words.data(), words.size() * 4);
  if (!sh) { fprintf(stderr, "dxso_shader_initialize failed\n"); return 3; }
  LLVMContext ctx; Module mod("dxso.air", ctx);
  mod.setSourceFileName("airconv_generated.metal");
  mod.setTargetTriple(Triple("air64-apple-macosx14.0.0"));
  mod.setDataLayout("e-p:64:64:64-i1:8:8-i8:8:8-i16:16:16-i32:32:32-i64:64:64-f32:32:32-f64:64:64-v16:16:16-v24:32:32-v32:32:32-v48:64:64-v64:64:64-v96:128:128-v128:128:128-v192:256:256-v256:256:256-v512:512:512-v1024:1024:1024-n8:16:32");
  dxmt::compile_dxso(sh, nullptr, nullptr, nullptr, false, atoi(argv[2]) != 0, -1, false, "vs", ctx, mod);
  std::string err; raw_string_ostream es(err);
  bool bad = verifyModule(mod, &es);
  if (bad) {   // LLVM 22 rejects the LLVM 15 function attributes airconv puts on air.* intrinsics; anything else is real
    std::string m = es.str(); size_t pos = 0;
    while (pos < m.size()) { size_t e = m.find('\n', pos); if (e == std::string::npos) e = m.size(); std::string l = m.substr(pos, e - pos); pos = e + 1;
      if (l.empty() || l.rfind("Attribute '", 0) == 0 || l.rfind("ptr @air.", 0) == 0) continue; fprintf(stderr, "verify: %s\n", l.c_str()); return 4; }
  }
  mod.print(outs(), nullptr);
  return 0;
}
'''

# D3D9 shader bytecode (DXSO tokens), assembled by hand: version, dcl_position v0, mov, end.
BYTECODE = {
    "vs_3_0 psize": "FFFE0300 0200001F 80000000 900F0000 0200001F 80000000 E00F0000 0200001F 80000004 E00F0001 "
                    "02000001 E00F0000 90E40000 02000001 E00F0001 A0000000 0000FFFF",
    "vs_3_0 plain": "FFFE0300 0200001F 80000000 900F0000 0200001F 80000000 E00F0000 02000001 E00F0000 90E40000 0000FFFF",
    "vs_2_0 psize": "FFFE0200 0200001F 80000000 900F0000 02000001 C00F0000 90E40000 02000001 C00F0002 A0000000 0000FFFF",
}


def shim_for_llvm22(a):
    """Host-only differences between LLVM 22 and the LLVM 15 airconv targets, on the check's COPY."""
    def edit(rel, pairs):
        p = a / rel
        s = p.read_text()
        for old, new in pairs:
            s = s.replace(old, new)
        p.write_text(s)
    edit("dxso_compile.cpp", [("context.setOpaquePointers(false);", "/* host: opaque pointers only */"),
                              ('module.setTargetTriple("air64-apple-macosx15.0.0");', 'module.setTargetTriple(llvm::Triple("air64-apple-macosx15.0.0"));')])
    edit("ffp_compile.cpp", [('module.setTargetTriple("air64-apple-macosx15.0.0");', 'module.setTargetTriple(llvm::Triple("air64-apple-macosx15.0.0"));')])
    edit("air_signature.cpp", [("for (auto &element : enumerate(", "for (auto &&element : enumerate("), ("for (auto &item : enumerate(", "for (auto &&item : enumerate(")])
    edit("nt/air_builder.cpp", [('#include "llvm/IR/Type.h"', '#include "llvm/IR/Type.h"\n#include "llvm/IR/Module.h"\n#include "llvm/IR/Function.h"'),
                                ("Attribute::AttrKind::NoCapture", "Attribute::AttrKind::NoUnwind"), ("Attribute::AttrKind::ArgMemOnly", "Attribute::AttrKind::NoUnwind"),
                                ("->getNonOpaquePointerElementType()", "->getArrayElementType()")])


with tempfile.TemporaryDirectory(prefix="madeira-psize-") as tmp:
    t = pathlib.Path(tmp)
    for v in ("old", "new"):
        shutil.copytree(src / "airconv", t / v / "airconv")
    script = str(R / "tools/patch-airconv-d9-psize-topology.py")
    before = (t / "new/airconv/dxso_compile.cpp").read_text()
    runs = [subprocess.run([sys.executable, script, str(t / "new/airconv/dxso_compile.cpp")], capture_output=True, text=True) for _ in range(2)]
    after = (t / "new/airconv/dxso_compile.cpp").read_text()
    check("the patch applies; a second run says it is already in and changes nothing",
          runs[0].returncode == 0 and runs[1].returncode == 0 and "already in" in runs[1].stdout
          and "madeira-bcd: d9-psize-topology" in after and after != before, runs[0].stdout + runs[0].stderr + runs[1].stdout)
    ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
    check("only the three places change: both DefineOutput(OutputPointSize) guards and the slot condition",
          len(ops) == 3 and after.count("if (oPts_used && vs_inject_point_size)") == 2
          and after.count("if (is_vertex && (oPts_arg_idx >= 0 || oPts_used)) {") == 1
          and before.count("if (oPts_used)\n        oPts_arg_idx = (int)sig.DefineOutput(air::OutputPointSize{});") == 2, ops)
    import importlib.util
    spec = importlib.util.spec_from_file_location("psize_patch", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # the script only acts under __main__
    for n, (old, _) in enumerate(mod.PAIRS, 1):
        moved = t / f"moved{n}"
        shutil.copytree(src / "airconv", moved / "airconv")
        mf = moved / "airconv/dxso_compile.cpp"
        text = mf.read_text()
        assert text.count(old) == 1
        mf.write_text(text.replace(old, old.replace("oPts", "oPtz", 1), 1))
        r = subprocess.run([sys.executable, script, str(mf)], capture_output=True, text=True)
        check(f"a moved anchor {n} refuses the patch and leaves the file alone",
              r.returncode != 0 and "not found exactly once" in r.stderr and "madeira-bcd: d9-psize-topology" not in mf.read_text(), r.stderr)

    llvm_config = os.environ.get("LLVM_CONFIG") or next(
        (p for p in ("/usr/local/opt/llvm/bin/llvm-config", "/opt/homebrew/opt/llvm/bin/llvm-config") if os.path.isfile(p)), None)
    headers = next((h for h in (src.parent / "include/native/directx", pathlib.Path.home() / "madeira-bcd/dxmt/include/native/directx")
                    if (h / "d3d9.h").is_file()), None)
    if not llvm_config or not headers:
        print("skip: no host LLVM (set LLVM_CONFIG) or no DirectX headers (dxmt/include/native/directx); the IR harness did not run")
    else:
        lbin = pathlib.Path(subprocess.run([llvm_config, "--bindir"], capture_output=True, text=True).stdout.strip())
        cxxflags = [f for f in subprocess.run([llvm_config, "--cxxflags"], capture_output=True, text=True).stdout.split()
                    if not f.startswith("-std=") and f != "-fno-exceptions"]
        libs = subprocess.run([llvm_config, "--ldflags", "--libs", "core", "support", "bitwriter"], capture_output=True, text=True).stdout.split()
        (t / "harness.cpp").write_text(HARNESS)
        exes = {}
        for v in ("old", "new"):
            a = t / v / "airconv"
            shim_for_llvm22(a)
            inc = [f"-I{a}", f"-I{src.parent / 'libs'}", f"-I{src / 'winemetal'}", f"-I{headers}", f"-I{headers.parent / 'windows'}",
                   f"-I{src.parent / 'include'}"]
            objs, procs = [], []
            for name in ("dxso_compile", "ffp_compile", "air_operations", "air_signature", "air_type", "metallib_writer", "nt/air_builder"):
                o = t / v / (name.replace("/", "_") + ".o")
                objs.append(o)
                procs.append(subprocess.Popen([str(lbin / "clang++"), "-std=c++20", "-O0", "-w"] + cxxflags + inc + ["-c", str(a / (name + ".cpp")), "-o", str(o)],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            ho = t / v / "harness.o"
            procs.append(subprocess.Popen([str(lbin / "clang++"), "-std=c++20", "-O0", "-w"] + cxxflags + inc + ["-c", str(t / "harness.cpp"), "-o", str(ho)],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            errs = [p.communicate()[1] for p in procs]
            exe = t / ("h-" + v)
            if all(p.returncode == 0 for p in procs):
                b = subprocess.run([str(lbin / "clang++")] + [str(o) for o in objs] + [str(ho)] + libs + ["-Wl,-undefined,dynamic_lookup", "-o", str(exe)],
                                   capture_output=True, text=True)
                exes[v] = exe if b.returncode == 0 else None
                if b.returncode != 0:
                    print(f"skip: the IR harness did not link against this LLVM ({v}): " + b.stderr.strip().splitlines()[-1][:200])
            else:
                exes[v] = None
                bad = next(e for e in errs if "error" in e)
                print(f"skip: the IR harness did not build against this LLVM ({v}): " + next(l for l in bad.splitlines() if "error" in l)[:200])
        if exes.get("old") and exes.get("new"):
            def ir(v, name, inject):
                hexf = t / (re.sub(r"\W", "_", name) + ".hex")
                hexf.write_text(BYTECODE[name])
                return subprocess.run([str(exes[v]), str(hexf), str(inject)], capture_output=True, text=True)
            got = {(v, n, i): ir(v, n, i) for v in ("old", "new") for n in BYTECODE for i in (0, 1)}
            check("the harness compiles every vertex shader in every build (the IR verifies apart from LLVM 22's attribute complaints)",
                  all(r.returncode == 0 and "define" in r.stdout for r in got.values()), [(k, r.stderr[:200]) for k, r in got.items() if r.returncode])
            for name in ("vs_3_0 psize", "vs_2_0 psize"):
                o0, n0, o1, n1 = got[("old", name, 0)], got[("new", name, 0)], got[("old", name, 1)], got[("new", name, 1)]
                check(f"{name}, triangle draw (inject off): unpatched has the point_size output (the AGX refusal); patched has none and keeps the oPts slot",
                      "air.point_size" in o0.stdout and "air.point_size" not in n0.stdout and "%oPts = alloca" in n0.stdout)
                check(f"{name}, point-list draw (inject on): the same IR before and after, point_size present",
                      o1.stdout == n1.stdout and "air.point_size" in n1.stdout)
            plain0, plain1 = got[("old", "vs_3_0 plain", 0)], got[("old", "vs_3_0 plain", 1)]
            check("a vertex shader without psize: the same IR before and after in both variants; point_size only in the injecting one",
                  plain0.stdout == got[("new", "vs_3_0 plain", 0)].stdout and plain1.stdout == got[("new", "vs_3_0 plain", 1)].stdout
                  and "air.point_size" not in plain0.stdout and "air.point_size" in plain1.stdout)

print("PASS: DXMT D3D9 point size topology" if ok else "FAILED: DXMT D3D9 point size topology")
sys.exit(0 if ok else 1)
