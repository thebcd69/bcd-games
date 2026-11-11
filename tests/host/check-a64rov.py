#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A pixel shader's 64-bit texture atomics whose result is read (build 647; madeira-d3d12/src/unix/madeira_ags.cpp, rewrite_a64rov_module).

The Last of Us Part I's transparency pass (MainPsOitAppend, the owner's logs of 2026-11-08 and 2026-11-09) keeps four layers per pixel
in four RWTexture2D<uint64_t> and reads each InterlockedMax's old value; Apple's converter refuses that on every target ("IR contains
unsupported instruction: dx.op.atomicBinOp.i64"), so the pass was skipped and the menu's curtain was drawn wrong. BCD rewrites such a
shader before the converter sees it: ordered load / combine / store on rasterizer-ordered textures. Checks:
  - the source: the pass is reached only for a pixel shader with a typed UAV marked UsedByAtomic64 (PSV0), only when a result is read,
    with the ROV and typed-UAV-load flags set and the 64-bit typed atomic ones cleared (module flags, SFI0, PSV0), under ags-rewrite;
  - the converter service logs it apart from AMD's AGS rewrite; CI runs this check;
  - when the host LLVM 15 build has its bitcode writer (~/madeira-bcd/toolchains/llvm-host-build, or LLVM_HOST_BUILD): the pass is
    compiled with the offline driver and run on BCD's own shaders (tests/fixtures/dxil, compiled from the .hlsl beside them with DXC
    v1.9.2609): the four-layer insert is rewritten whole and exactly, a pixel shader whose atomic nobody reads and a compute shader are
    left alone.
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


src = (root / "madeira-d3d12/src/unix/madeira_ags.cpp").read_text()
ir = (root / "madeira-d3d12/src/unix/madeira_ir_unix.mm").read_text()
fn = src[src.index("static int rewrite_a64rov_module("):src.index("}  // namespace")]
entry = src[src.index('extern "C" int madeira_ags_rewrite('):]

check("the pass works on pixel shaders only and needs a read result",
      'if (!kind || kind->getString() != "ps") return 0;' in fn and "if (!any_read) return 0;" in fn, fn[:800])
check("...only plain one-register 2D textures, nothing else on their handles; otherwise the shader stays as it was (-1)",
      "if (cnt != 1 || shape != 2)" in fn and 'say(note, "the texture at register %su%u is also used another way", "", lo); return -1;' in fn
      and "if (!ok || cl != kClassUAV || l != hi || idx != l) return false;" in src)
check("...each atomic becomes textureLoad.i32, the combined value, textureStore.i32 of both halves (mask 3), the old value returned",
      "b.CreateCall(load, {ConstantInt::get(i32, kOpTextureLoad), h, u32, x, y, z, u32, u32, u32});" in fn
      and "b.CreateCall(store, {ConstantInt::get(i32, kOpTextureStore), h, x, y, z, nlo, nhi, nlo, nlo, ConstantInt::get(i8, 3)});" in fn
      and "ci->replaceAllUsesWith(old);" in fn
      and "case 7: nv = b.CreateSelect(b.CreateICmpUGT(v, old), v, old); break;   /* UMax */" in fn)
check("...the textures become ROVs of uint2 (metadata, handle properties), the flags say so",
      "f[9] = ConstantAsMetadata::get(ConstantInt::getTrue(ctx));" in fn
      and "{ConstantInt::get(i32, w0 | 0x2000), ConstantInt::get(i32, (w1 & ~0xff00ull) | (2u << 8))}" in fn
      and "uint64_t add = kFlagROVs | kFlagUAVLoadAdditional;" in fn and "if (!left) fl &= ~kFlagAtomicInt64Typed;" in fn
      and "constexpr uint64_t kFlagUAVLoadAdditional = 1ull << 13, kFlagROVs = 1ull << 18;" in src)
check("the entry takes it for a pixel shader (PSV0 stage 0) with a typed UAV marked UsedByAtomic64, never for AGS or t2r shaders",
      "if (ri >= 25 && 4ull + ri + 8 <= sz && q[4 + 24] == 0) {" in entry
      and "a64 |= rd32(b) == 6u && (rd32(b + 20) & 1u);   /* UAVTyped, UsedByAtomic64 */" in entry
      and "bool want_a64 = !magic && !want_t2r && a64;" in entry)
check("...and fixes the container: SFI0 ROVs and typed UAV loads, PSV0's UsedByAtomic64 dropped when no 64-bit atomic is left",
      "fl |= kSfi0ROVs | kSfi0TypedUAVLoadAdditional;" in entry and "if (!a64_left) fl &= ~kSfi0AtomicInt64Typed;" in entry
      and "if (!a64_left && bsz >= 24 && rd32(b) == 6u) wr32(b + 20, rd32(b + 20) & ~1u);" in entry)
check("the converter service runs it under ags-rewrite (bcd.cfg ags-rewrite = 0 turns it off) and logs it as a DXIL rewrite",
      "int rc = mad_ags_enabled() ? madeira_ags_rewrite(src, src_len, &ags_buf, &ags_len, note, sizeof note) : 0;" in ir
      and 'dprintf(2, "[madeira-ir] ml1149 %s %s: %s%s\\n", strstr(note, "64-bit texture atomic") ? "DXIL" : "AGS", nm + 4, note,' in ir)
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-a64rov.py" in ci)

fx = root / "tests/fixtures/dxil"
for name in ("oit_append_a64", "a64_unread_ps", "a64_read_cs"):
    check("fixture %s.dxil is there with the .hlsl it was compiled from" % name,
          (fx / (name + ".dxil")).is_file() and (fx / (name + ".hlsl")).is_file())

# ---- the pass itself, on the host
llvm = Path(os.environ.get("LLVM_HOST_BUILD", Path.home() / "madeira-bcd/toolchains/llvm-host-build"))
llvm_src = llvm.parent / "llvm-project/llvm/include"
libs = ["BitWriter", "Analysis", "ProfileData", "DebugInfoDWARF", "Object", "MCParser", "MC", "TextAPI", "DebugInfoCodeView",
        "BitReader", "Core", "Remarks", "BitstreamReader", "BinaryFormat", "Support", "Demangle"]
cxx = shutil.which("clang++")
have = cxx and llvm_src.is_dir() and all((llvm / "lib" / ("libLLVM%s.a" % l)).is_file() for l in libs)
if not have:
    print("skip the host run: no LLVM 15 host build with its bitcode writer (set LLVM_HOST_BUILD; ninja LLVMBitWriter there)")
else:
    off = root / "madeira-d3d12/tests/offline/atomic64"
    extra = ["-L/usr/local/lib", "-L/opt/homebrew/lib", "-lzstd", "-lz", "-lcurses"] if sys.platform == "darwin" else ["-lzstd", "-lz", "-ltinfo"]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        common = ["-std=c++17", "-O1", "-fno-rtti", "-I", str(llvm_src), "-I", str(llvm / "include")]
        arch = [str(llvm / "lib" / ("libLLVM%s.a" % l)) for l in libs]
        b1 = subprocess.run([cxx] + common + [str(root / "madeira-d3d12/src/unix/madeira_ags.cpp"), str(off / "rw_main.cpp")] + arch + extra
                            + ["-o", str(t / "rw")], capture_output=True, text=True)
        b2 = subprocess.run([cxx] + common + [str(off / "bcdis.cpp")] + arch + extra + ["-o", str(t / "bcdis")], capture_output=True, text=True)
        check("the pass and the IR printer build against the host LLVM 15", b1.returncode == 0 and b2.returncode == 0, b1.stderr[-800:] + b2.stderr[-800:])

        def parts(path):
            d = path.read_bytes()
            n = struct.unpack_from("<I", d, 28)[0]
            out = {}
            for i in range(n):
                o = struct.unpack_from("<I", d, 32 + 4 * i)[0]
                out[d[o:o + 4]] = d[o + 8:o + 8 + struct.unpack_from("<I", d, o + 4)[0]]
            return out

        def ir_text(path):
            p = parts(path)[b"DXIL"]
            bo, bs = struct.unpack_from("<II", p, 16)
            (t / "m.bc").write_bytes(p[8 + bo:8 + bo + bs])
            r = subprocess.run([str(t / "bcdis"), str(t / "m.bc")], capture_output=True, text=True)
            return r.stdout if r.returncode == 0 else "PRINT FAILED " + r.stderr

        if b1.returncode == 0 and b2.returncode == 0:
            r = subprocess.run([str(t / "rw"), str(fx / "oit_append_a64.dxil"), str(t / "out.dxil")], capture_output=True, text=True)
            check("the four-layer insert is rewritten: four atomics, four ROV textures, nothing left",
                  r.returncode == 0 and "rc=1 note='rewrote 4 64-bit texture atomic(s) whose result is read into ordered load/store on 4 "
                  "rasterizer-ordered texture(s) (Metal's raster order groups)'" in r.stdout, r.stdout + r.stderr)
            if (t / "out.dxil").is_file():
                orig, new = ir_text(fx / "oit_append_a64.dxil"), ir_text(t / "out.dxil")
                check("...the original has the four 64-bit atomics, three of them read",
                      orig.count("call i64 @dx.op.atomicBinOp.i64(i32 78,") == 4, orig[:600])
                check("...the result has no 64-bit atomic, four loads and four two-component stores",
                      "atomicBinOp" not in new and new.count("@dx.op.textureLoad.i32(i32 66,") == 4
                      and len(re.findall(r"@dx\.op\.textureStore\.i32\(i32 67, .*, i8 3\)", new)) == 4, new[:1200])
                check("...each store writes max(old, v), and what the next layer gets is computed from the loaded value",
                      len(re.findall(r"icmp ugt i64 ", new)) == 4 and new.count("select i1 ") >= 4
                      and len(re.findall(r"call i64 @dx\.op\.binary\.i64\(i32 40, i64 %\d+, i64 %\d+\)", new)) == 3, new[:1200])
                check("...the four textures are ROVs of uint2 in the metadata and in their handles' properties",
                      new.count('%"class.RasterizerOrderedTexture2D<vector<unsigned int, 2> >"* undef') == 4
                      and len(re.findall(r'RasterizerOrderedTexture2D<vector<unsigned int, 2> >"\* undef, !"[^"]*", i32 4, i32 [2-5], i32 1, i32 2, '
                                         r'i1 false, i1 false, i1 true', new)) == 4
                      and "ResourceProperties { i32 4098, i32 261 }" not in new and new.count("ResourceProperties { i32 12290, i32 517 }") >= 4, new[-1500:])
                m = re.search(r"!dx\.entryPoints = !\{(!\d+)\}", new)
                flags = None
                if m:
                    ep = re.search(re.escape(m.group(1)) + r" = !\{[^\n]*, (!\d+)\}\n", new)
                    pr = ep and re.search(re.escape(ep.group(1)) + r" = !\{i32 0, i64 (\d+)", new)
                    flags = int(pr.group(1)) if pr else None
                check("...the module flags: ROVs and typed UAV loads on, 64-bit typed atomics off, 64-bit integers kept",
                      flags is not None and flags & (1 << 18) and flags & (1 << 13) and not flags & (1 << 27) and flags & (1 << 20), flags)
                pa = parts(t / "out.dxil")
                sfi0 = struct.unpack_from("<Q", pa[b"SFI0"], 0)[0]
                check("...SFI0: ROVs and typed UAV loads on, 64-bit typed atomics off", sfi0 & 0x1000 and sfi0 & 0x800 and not sfi0 & 0x400000, hex(sfi0))
                psv = pa[b"PSV0"]
                ri = struct.unpack_from("<I", psv, 0)[0]
                cnt, bsz = struct.unpack_from("<II", psv, 4 + ri)
                binds = [struct.unpack_from("<6I", psv, 4 + ri + 8 + k * bsz) for k in range(cnt)]
                check("...PSV0: the four typed UAVs no longer marked UsedByAtomic64",
                      len([b for b in binds if b[0] == 6]) == 4 and all(b[5] == 0 for b in binds if b[0] == 6), binds)
            for name, why in (("a64_unread_ps", "a pixel shader whose 64-bit atomic nobody reads"), ("a64_read_cs", "a compute shader")):
                r = subprocess.run([str(t / "rw"), str(fx / (name + ".dxil")), str(t / "x.dxil")], capture_output=True, text=True)
                check("%s is left alone" % why, r.returncode == 0 and r.stdout.startswith("rc=0 "), r.stdout + r.stderr)

print("PASS: 64-bit texture atomics with a read result" if ok else "FAILED: 64-bit texture atomics with a read result")
sys.exit(0 if ok else 1)
