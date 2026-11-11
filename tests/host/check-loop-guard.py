#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""dxil-loop-guard (build 648; madeira-d3d12/src/unix/madeira_ags.cpp, madeira_ir_unix.mm, pe/madeira_d3d12.c).

The Last of Us Part I hangs the GPU every time a new game loads (the owner's starts of 2026-11-09 11:25, 13:15 and 13:39):
"[FAULTED] ... CS_ComputeVolProbeBufferWriteCache", a k-d tree walk over light probes with a stack, then the queued command
buffers are discarded and the game stops on its own ray-cast check. A sound tree ends in at most 65536 steps (16-bit node
indices); a tree whose data reads wrong never does. dxil-loop-guard = <hash>[,<hash>...] (the FNV-1a 64 bytecode hashes [dxil-dump]
prints) caps every loop of those shaders whose back edges are conditional exits at 65536 iterations. Checks:
  - the pass: back edges by dominance, the loop body, only latches whose other way leaves the loop, a counter phi in the header,
    the back edge's condition and-ed (or or-ed) with "next < cap"; anything else is left and counted;
  - the converter service: the list is read once from the configuration, a shader is matched by the hash of its bytecode, the
    conversion cache keys on it, the capped container is converted, its buffer freed, the result logged;
  - the PE runtime's shader cache keys a listed shader apart (the same hash), every other shader keeps its key;
  - with the host LLVM 15 build: BCD's own k-d tree shader (tests/fixtures/dxil/loop_guard_cs.hlsl) gets its three loops capped,
    a shader without loops is left alone;
  - CI runs this check.
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


ags = (root / "madeira-d3d12/src/unix/madeira_ags.cpp").read_text()
unix = (root / "madeira-d3d12/src/unix/madeira_ir_unix.mm").read_text()
pe = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
fn = ags[ags.index("static int rewrite_loop_guard_module("):ags.index("static uint8_t *container_with_bitcode(")]

check("the pass finds back edges by dominance and each loop's body (what reaches a latch without passing the header)",
      "if (!dt.dominates(s, &b)) continue;   /* a back edge: its target dominates its source */" in fn
      and "for (BasicBlock *p : predecessors(x)) work.push_back(p);" in fn, fn[:900])
check("...caps only loops whose every latch is a conditional branch whose other way leaves the loop; others are counted as left",
      "if (!br || !br->isConditional() || br->getSuccessor(0) == br->getSuccessor(1)) { fit = false; break; }" in fn
      and "if (in_body(other)) { fit = false; break; }" in fn and "if (!fit) { (*n_left)++; continue; }" in fn)
check("...a counter phi in the header (0 from outside, next from each latch), the back edge taken only while next < cap",
      'PHINode *cnt = PHINode::Create(i32, 2, "bcd.lg", &h->front());' in fn
      and 'Value *more = b.CreateICmpULT(nx, ConstantInt::get(i32, cap), "bcd.lg.more");' in fn
      and "br->setCondition(br->getSuccessor(0) == h ? b.CreateAnd(c, more) : b.CreateOr(c, b.CreateNot(more)));" in fn
      and "for (auto &e : next) if (e.first == p) v = e.second;" in fn)
check("the converter service reads the list once (16-hex-digit hashes) and matches the bytecode's FNV-1a 64",
      'if (!madeira_cfg_get("dxil-loop-guard", v, sizeof v)) return;' in unix and "if (e != p && e - p == 16) g_lg_want[g_lg_n++] = h;" in unix
      and "uint64_t h = 0xcbf29ce484222325ull;" in unix and "h ^= b[i]; h *= 0x100000001b3ull;" in unix
      and "#define MAD_LOOP_GUARD_CAP 65536u" in unix)
check("...keys the conversion cache on it, converts the capped container, frees it and logs the pass's note",
      "env.ags_rewrite = (uint32_t)mad_ags_enabled() | (lg_want ? 2u : 0u);" in unix
      and "int lrc = madeira_loop_guard_rewrite(cur, cur_len, MAD_LOOP_GUARD_CAP, &lg_buf, &lg_len, lnote, sizeof lnote);" in unix
      and "if (lrc == 1) input = g_ir.IRObjectCreateFromDXIL((const uint8_t *)lg_buf, lg_len, IRBytecodeOwnershipNone);" in unix
      and "free(lg_buf);   /* madeira-bcd: dxil-loop-guard */" in unix
      and 'dprintf(2, "[madeira-ir] dxil-loop-guard %016llx: %s\\n", (unsigned long long)lg_hash, lnote);' in unix)
check("...on what the converter would take: the AGS rewrite's output when there is one, else the game's bytecode",
      "const void *cur = rc == 1 ? (const void *)ags_buf : (const void *)src;" in unix)
key = pe[pe.index("static void mad_sc_key("):pe.index("static void mad_sc_key(") + 4000]
check("the PE shader cache keys a listed shader apart (same hash, case-insensitive list), every other key unchanged",
      'if (mad_loop_guard_listed(a->dxil, a->dxil_len)) mad_sc_feed(&h, "dxil-loop-guard 65536", 21);' in key
      and 'mad_cfg_str_pe("dxil-loop-guard", raw, sizeof raw);' in pe and "list[k] = (char)tolower((unsigned char)raw[k]);" in pe
      and "for (i = 0; i < n; i++) { hh ^= b[i]; hh *= 0x100000001b3ull; }" in pe)
check("...and [dxil-dump] prints the same FNV-1a 64 of the bytecode",
      "for (q = 0; q < len; q++) { hh ^= b[q]; hh *= 0x100000001b3ull; }" in pe)
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-loop-guard.py" in ci)
fx = root / "tests/fixtures/dxil"
check("the fixture is there with the .hlsl it was compiled from", (fx / "loop_guard_cs.dxil").is_file() and (fx / "loop_guard_cs.hlsl").is_file())

llvm = Path(os.environ.get("LLVM_HOST_BUILD", Path.home() / "madeira-bcd/toolchains/llvm-host-build"))
llvm_src = llvm.parent / "llvm-project/llvm/include"
libs = ["BitWriter", "Analysis", "ProfileData", "DebugInfoDWARF", "Object", "MCParser", "MC", "TextAPI", "DebugInfoCodeView",
        "BitReader", "Core", "Remarks", "BitstreamReader", "BinaryFormat", "Support", "Demangle"]
cxx = shutil.which("clang++")
if not (cxx and llvm_src.is_dir() and all((llvm / "lib" / ("libLLVM%s.a" % l)).is_file() for l in libs)):
    print("skip the host run: no LLVM 15 host build with its bitcode writer (set LLVM_HOST_BUILD; ninja LLVMBitWriter there)")
else:
    off = root / "madeira-d3d12/tests/offline/atomic64"
    extra = ["-L/usr/local/lib", "-L/opt/homebrew/lib", "-lzstd", "-lz", "-lcurses"] if sys.platform == "darwin" else ["-lzstd", "-lz", "-ltinfo"]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        common = ["-std=c++17", "-O1", "-fno-rtti", "-I", str(llvm_src), "-I", str(llvm / "include")]
        arch = [str(llvm / "lib" / ("libLLVM%s.a" % l)) for l in libs]
        b1 = subprocess.run([cxx] + common + [str(root / "madeira-d3d12/src/unix/madeira_ags.cpp"), str(off / "lg_main.cpp")] + arch + extra
                            + ["-o", str(t / "lg")], capture_output=True, text=True)
        b2 = subprocess.run([cxx] + common + [str(off / "bcdis.cpp")] + arch + extra + ["-o", str(t / "bcdis")], capture_output=True, text=True)
        check("the pass and the IR printer build against the host LLVM 15", b1.returncode == 0 and b2.returncode == 0, b1.stderr[-800:] + b2.stderr[-800:])
        if b1.returncode == 0 and b2.returncode == 0:
            r = subprocess.run([str(t / "lg"), str(fx / "loop_guard_cs.dxil"), str(t / "out.dxil")], capture_output=True, text=True)
            check("BCD's k-d tree shader gets its three loops capped",
                  r.returncode == 0 and "rc=1 note='3 loop(s) capped at 65536 iterations, 0 left as they were'" in r.stdout, r.stdout + r.stderr)
            if (t / "out.dxil").is_file():
                d = (t / "out.dxil").read_bytes()
                n = struct.unpack_from("<I", d, 28)[0]
                parts = {}
                for i in range(n):
                    o = struct.unpack_from("<I", d, 32 + 4 * i)[0]
                    parts[d[o:o + 4]] = d[o + 8:o + 8 + struct.unpack_from("<I", d, o + 4)[0]]
                p = parts[b"DXIL"]
                bo, bs = struct.unpack_from("<II", p, 16)
                (t / "m.bc").write_bytes(p[8 + bo:8 + bo + bs])
                ir = subprocess.run([str(t / "bcdis"), str(t / "m.bc")], capture_output=True, text=True).stdout
                check("...three counter phis that start at 0, each latch compares next with 65536",
                      len(re.findall(r"%bcd\.lg\d* = phi i32 \[ %bcd\.lg\.next\d*, %\d+ \], \[ 0, %\d+ \]", ir)) == 3
                      and len(re.findall(r"icmp ult i32 %bcd\.lg\.next\d*, 65536", ir)) == 3, ir[:1500])
                check("...the container keeps its parts but the hash and the old bitcode's debug copies",
                      b"HASH" not in parts and b"ILDB" not in parts and b"PSV0" in parts and b"SFI0" in parts, list(parts))
            r = subprocess.run([str(t / "lg"), str(fx / "oit_append_a64.dxil"), str(t / "x.dxil")], capture_output=True, text=True)
            check("a shader without loops is left alone", r.returncode == 0 and r.stdout.startswith("rc=0 "), r.stdout + r.stderr)

print("PASS: dxil-loop-guard" if ok else "FAILED: dxil-loop-guard")
sys.exit(0 if ok else 1)
