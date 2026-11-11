#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-airconv-dec3n.py: Direct3D 9 vertex formats DEC3N and UDEC3 (night round 2026-11-11, G1 S14 / G2 S5 / G3 S2).

Battlefield 2 (12 logs) and Cold Fear (0KQ7V8) aborted with
  Assertion failed: (0 && "unused"), function unpack_fvec4_from_addr, file air_operations.cpp
(DEC3N = Int1010102Normalized); UDEC3 (dxmt's 0x1000) aborted the same way at assert(value). Checks, on a copy of
dxmt/src (or $DXMT_SRC_DIR/src):
  - the patch applies, a second run changes nothing, a moved anchor refuses it and leaves the file alone;
  - the DEC3N branch sits before `std::string op;` and the 0x1000 case before `case MTLAttributeFormat::Invalid:`;
    the old assert stays as the unreachable default; nothing else in the file changed (the patch only adds lines);
  - CI runs this check and the patch next to the other airconv patches; the auto-tune rule is fixed in 652 with its fixtures;
  - with a host LLVM (LLVM_CONFIG, or Homebrew's llvm): a harness builds pull_vec4_from_addr for formats 40, 0x1000 and 41
    against the unpatched and the patched file: unpatched 40 and 0x1000 abort; patched they verify and give the D3D9
    expansion (shl/ashr/sitofp/fdiv 511, clamp at -1, w = 1; and/lshr 0x3ff lanes, w = 1); 41 is the same IR before and after.
SKIP (exit 0) of the source part when no dxmt sources are available.
"""
import difflib
import os
import pathlib
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
step = wf.find("      - name: Patch airconv DEC3N and UDEC3 vertex formats\n")
check("CI runs this check and the patch, after the geometry shader input patch",
      "python3 tests/host/check-airconv-dec3n.py" in wf and step > 0
      and "run: python3 tools/patch-airconv-dec3n.py" in wf[step:step + 200]
      and step > wf.index("run: python3 tools/patch-airconv-gs-input-siv.py"))
rules = (R / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "airconv-vertex-format-assert"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule names both asserts, fixed in 652, and its fixtures are there",
      '"fixedIn": 652' in rb and 'function unpack_fvec4_from_addr, file air_operations.cpp' in rb
      and 'function pull_vec4_from_addr_checked, file air_operations.cpp' in rb
      and all((R / "tests/fixtures/autotune" / (n + ".expect")).is_file()
              for n in ("cold-fear-dec3n-assert-2026-11-08", "battlefield2-dec3n-assert-2026-11-04")))

src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (src / "airconv/air_operations.cpp").is_file():
    alt = pathlib.Path.home() / "madeira-bcd/dxmt/src"
    src = alt if (alt / "airconv/air_operations.cpp").is_file() else src
OPS = "airconv/air_operations.cpp"
if not (src / OPS).is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: airconv DEC3N and UDEC3" if ok else "FAILED: airconv DEC3N and UDEC3")
    sys.exit(0 if ok else 1)
if "madeira-bcd: DEC3N and UDEC3 vertex formats" in (src / OPS).read_text():
    print("note: the dxmt tree is already patched; only the static checks ran")
    print("PASS: airconv DEC3N and UDEC3" if ok else "FAILED: airconv DEC3N and UDEC3")
    sys.exit(0 if ok else 1)

HARNESS = r'''
#include "air_operations.hpp"
#include "air_type.hpp"
#include "nt/air_builder.hpp"
#include "llvm/IR/Verifier.h"
#include "llvm/Support/raw_ostream.h"
#include <cstdlib>
using namespace llvm;
int main(int argc, char **argv) {
  unsigned fmt = (unsigned)strtoul(argv[1], 0, 0);
  LLVMContext ctx; Module mod("m", ctx);
  dxmt::air::AirType types(ctx);
  auto *ft = FunctionType::get(types._float4, {PointerType::get(ctx, 1), types._int}, false);
  auto *f = Function::Create(ft, Function::ExternalLinkage, "pull", mod);
  IRBuilder<> b(BasicBlock::Create(ctx, "e", f));
  llvm::air::AIRBuilder air(b, llvm::errs());
  dxmt::air::AIRBuilderContext abctx{.llvm = ctx, .module = mod, .builder = b, .types = types, .air = air};
  auto r = dxmt::air::pull_vec4_from_addr((dxmt::air::MTLAttributeFormat)fmt, f->getArg(0), f->getArg(1)).build(abctx);
  if (auto e = r.takeError()) { errs() << "error\n"; return 2; }
  Value *v = r.get();
  if (v->getType() != types._float4) v = b.CreateSIToFP(v, types._float4);
  b.CreateRet(v);
  if (verifyFunction(*f, &errs())) { errs() << "verify failed\n"; return 3; }
  f->print(outs());
  return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="madeira-dec3n-") as tmp:
    t = pathlib.Path(tmp)
    for v in ("old", "new"):
        shutil.copytree(src / "airconv", t / v / "dxmt/src/airconv")
    before = (t / "new/dxmt/src" / OPS).read_text()
    script = str(R / "tools/patch-airconv-dec3n.py")
    runs = [subprocess.run([sys.executable, script], cwd=t / "new", capture_output=True, text=True) for _ in range(2)]
    after = (t / "new/dxmt/src" / OPS).read_text()
    check("the patch applies; a second run says so and changes nothing",
          runs[0].returncode == 0 and runs[1].returncode == 0 and "already patched" in runs[1].stdout
          and "madeira-bcd: DEC3N and UDEC3 vertex formats" in after and (t / "new/dxmt/src" / OPS).read_text() == after,
          runs[0].stdout + runs[0].stderr)
    un = after[after.index("AIRBuilderResult unpack_fvec4_from_addr("):]
    check("the DEC3N branch comes before `std::string op;`; the old assert stays as the unreachable default",
          un.index("if (format == MTLAttributeFormat::Int1010102Normalized) {") < un.index("  std::string op;")
          and 'assert(0 && "unused");' in un and "511.0f" in un and "CreateAShr" in un and "get_float(-1.0f)" in un)
    pc = after[after.index("AIRBuilderResult pull_vec4_from_addr_checked("):]
    check("the UDEC3 case (0x1000) comes before the Invalid case",
          pc.index("case (MTLAttributeFormat)0x1000: {") < pc.index("case MTLAttributeFormat::Invalid:") < pc.index("assert(value);"))
    removed = [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l.startswith("- ")]
    check("the patch only adds lines", not removed, removed[:5])
    moved = t / "moved"
    shutil.copytree(src / "airconv", moved / "dxmt/src/airconv")
    mf = moved / "dxmt/src" / OPS
    mf.write_text(mf.read_text().replace("  std::string op;\n", "  std::string op_name;\n", 1))
    r3 = subprocess.run([sys.executable, script], cwd=moved, capture_output=True, text=True)
    check("a moved anchor refuses the patch and leaves the file alone",
          r3.returncode != 0 and "moved" in r3.stderr and "madeira-bcd: DEC3N" not in mf.read_text(), r3.stderr)

    llvm_config = os.environ.get("LLVM_CONFIG") or next(
        (p for p in ("/usr/local/opt/llvm/bin/llvm-config", "/opt/homebrew/opt/llvm/bin/llvm-config") if os.path.isfile(p)), None)
    if not llvm_config:
        print("skip: no host LLVM (set LLVM_CONFIG); the IR harness did not run")
    else:
        lbin = pathlib.Path(subprocess.run([llvm_config, "--bindir"], capture_output=True, text=True).stdout.strip())
        cxxflags = [f for f in subprocess.run([llvm_config, "--cxxflags"], capture_output=True, text=True).stdout.split()
                    if not f.startswith("-std=") and f != "-fno-exceptions"]
        libs = subprocess.run([llvm_config, "--ldflags", "--libs", "core", "support"], capture_output=True, text=True).stdout.split()
        (t / "harness.cpp").write_text(HARNESS)
        exes = {}
        for v in ("old", "new"):
            a = t / v / "dxmt/src/airconv"
            exe = t / ("h-" + v)
            inc = [f"-I{a}"] + ([f"-I{src.parent / 'include'}"] if (src.parent / "include").is_dir() else [])
            b = subprocess.run([str(lbin / "clang++"), "-std=c++20", "-O0", "-w"] + cxxflags + inc
                               + [str(t / "harness.cpp"), str(a / "air_operations.cpp"), str(a / "air_type.cpp")] + libs
                               + ["-Wl,-undefined,dynamic_lookup", "-o", str(exe)], capture_output=True, text=True)
            exes[v] = exe if b.returncode == 0 else None
            if b.returncode != 0:
                print(f"skip: the IR harness did not build against this LLVM ({v}): " + b.stderr.strip().splitlines()[-1][:200])
        if exes["old"] and exes["new"]:
            def run(v, fmt):
                return subprocess.run([str(exes[v]), fmt], capture_output=True, text=True)
            o40, o1000, o41 = run("old", "40"), run("old", "0x1000"), run("old", "41")
            n40, n1000, n41 = run("new", "40"), run("new", "0x1000"), run("new", "41")
            check("unpatched: DEC3N and UDEC3 abort (the device's two asserts)",
                  o40.returncode != 0 and '(0 && "unused")' in o40.stderr and o1000.returncode != 0 and "(value)" in o1000.stderr)
            check("patched DEC3N: verifies; shl 22/12/2, ashr 22, sitofp, fdiv 511, clamp at -1, w = 1",
                  n40.returncode == 0 and all(x in n40.stdout for x in ("shl i32 %7, 22", "shl i32 %7, 12", "shl i32 %7, 2",
                                                                         "ashr i32", "sitofp i32", "5.110000e+02",
                                                                         "fcmp olt float", "float 1.000000e+00, i64 3")),
                  n40.stdout + n40.stderr)
            check("patched UDEC3: verifies; three and/lshr lanes of 0x3ff, w = 1, int4 made float",
                  n1000.returncode == 0 and all(x in n1000.stdout for x in ("and i32 %7, 1023", "lshr i32 %7, 10", "lshr i32 %7, 20",
                                                                             "i32 1, i64 3", "sitofp <4 x i32>")),
                  n1000.stdout + n1000.stderr)
            check("UInt1010102Normalized (41) gives the same IR before and after", o41.returncode == 0 and o41.stdout == n41.stdout)

print("PASS: airconv DEC3N and UDEC3" if ok else "FAILED: airconv DEC3N and UDEC3")
sys.exit(0 if ok else 1)
