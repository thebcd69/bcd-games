#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-pool-alloc-null.py: a pooled buffer that cannot be allocated is reclaimed and retried, then refused (night round 2026-11-11, X1 S2).

The FEX address band is full in 8HCFEP (GTA V Enhanced), KNQGPQ (Arctic Drive), EQWQ67 (NTE), Q7XZ5F (Dominus Gladiatus), N1MBVQ (RDR2): VirtualAlloc
returns NULL, IntrusivePooledAllocator registered a MemoryBuffer with Ptr == NULL and IREmitter::ResetWorkingList wrote the invalid-node header at
NULL+0x800010 (`[fex-fault] pc=libarm64ecfex.dll+0xf59f0 ... fault-addr=0x800010`) while FEX's read lock was held, so every other thread wedged.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the build's
order of BOTH modules (tests/host/fex_chain.py; ARM64EC and WOW64), a second run says "already in" and changes nothing, a half-patched tree and a moved
anchor are refused (nothing written); the nine edited files are the ones named; the claim API reports failure (nullopt), the six clients check it
(IR list, IREmitter, OpDispatchBuilder::ResetWorkingList, GenerateIR with the [ir-alloc] bail, the decoder, the JIT temp buffer). The header cut from the
patched tree is compiled on the host against a stand-in allocator that fails like the band does: (1) a claim that fails while idle buffers sit in the pool
takes them back and succeeds (the pristine header hands out NULL); (2) with every buffer owned the claim is refused cleanly, no NULL MemoryBuffer is
registered (the pristine header frees a NULL pointer later) and the next claim succeeds once an owner disowns; (3) DelayedDisownBuffer of a client whose claim
failed is harmless; (4) 8 threads claiming, writing a pattern, verifying and disowning under a tight limit never see a buffer reclaimed under them (freed
buffers are poisoned). The two module builds apply it without failing the build; auto-tune's rule; CI runs this check.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fex_chain

ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


NAME = "patch-fex-ios-pool-alloc-null.py"
MARK = "pool-alloc-null-v1 madeira-bcd"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
for sh, label, restore_marker in (("tools/build-xtajit64.sh", "ARM64EC", 'git -C FEX checkout -- "$CPUF"'), ("tools/build-xtajit-wow64.sh", "WOW64", "restore() {")):
    t = (root / sh).read_text()
    restore = t[t.index(restore_marker):]
    restore = restore[:restore.index("\n")]
    files = ["FEXCore/include/FEXCore/Utils/ThreadPoolAllocator.h", "FEXCore/Source/Interface/IR/IntrusiveIRList.h", "FEXCore/Source/Interface/IR/IREmitter.h",
             "FEXCore/Source/Interface/Core/OpcodeDispatcher.h", "FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp", "FEXCore/Source/Interface/Core/Core.cpp",
             "FEXCore/Source/Interface/Core/Frontend.h", "FEXCore/Source/Interface/Core/Frontend.cpp", "FEXCore/Source/Interface/Core/JIT/JIT.cpp"]
    check("the " + label + " build applies it once without failing the build and restores every file it edits",
          t.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and all(f in restore for f in files),
          [f for f in files if f not in restore])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-pool-alloc-null.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the fixes in 652 (fex-irlist-fault-window-stays gets fixedIn, fex-pool-claim-failed is new)",
      re.search(r'"id": "fex-irlist-fault-window-stays",.*?"fixedIn": 652', rules, re.S) is not None and '"id": "fex-pool-claim-failed"' in rules)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
POOL = "FEXCore/include/FEXCore/Utils/ThreadPoolAllocator.h"

STUBS = {
    "FEXCore/Utils/Allocator.h": r'''
#pragma once
#include <cstddef>
#include <cstdint>
namespace FEXCore::Allocator {
struct FEXAllocOperators {};
enum class ProtectOptions { None };
void* VirtualAlloc(size_t Size, bool Execute = false, bool Commit = true);
void VirtualFree(void* Ptr, size_t Size);
void VirtualName(const char* Name, void* Ptr, size_t Size);
bool VirtualProtect(void* Ptr, size_t Size, ProtectOptions O);
void* malloc(size_t);
void free(void*);
}
''',
    "FEXCore/Utils/LogManager.h": '#pragma once\n#include <cstdio>\nnamespace LogMan::Msg { template<class... A> void EFmt(const char*, A&&...) {} }\n#define LOGMAN_THROW_A_FMT(cond, ...) ((void)0)\n',
    "FEXCore/Utils/MathUtils.h": '#pragma once\n#include <cstdint>\ntemplate<class T> constexpr T AlignDown(T v, uint64_t a) { return v & ~(T)(a - 1); }\n',
    "FEXCore/Utils/TypeDefines.h": '#pragma once\nnamespace FEXCore::Utils { constexpr unsigned FEX_PAGE_SIZE = 4096; }\n',
    "FEXCore/fextl/list.h": '#pragma once\n#include <list>\nnamespace fextl { template<class T> using list = std::list<T>; }\n',
}

HARNESS = r'''
#include <FEXCore/Utils/ThreadPoolAllocator.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <thread>
#include <vector>
using namespace FEXCore::Utils;
static std::mutex M;
static size_t used, limit = ~size_t(0);
static std::map<void*, size_t> live;
static int free_null_calls;
namespace FEXCore::Allocator {
void* VirtualAlloc(size_t Size, bool, bool) { std::lock_guard g(M); if (used + Size > limit) return nullptr; void* p = std::malloc(Size); std::memset(p, 0xAA, Size > 4096 ? 4096 : Size); used += Size; live[p] = Size; return p; }
void VirtualFree(void* Ptr, size_t Size) { std::lock_guard g(M); if (!Ptr) { free_null_calls++; return; } auto it = live.find(Ptr); if (it == live.end()) { std::printf("BAD FREE %p\n", Ptr); std::abort(); }
  std::memset(Ptr, 0xDD, it->second > 4096 ? 4096 : it->second); used -= it->second; live.erase(it); std::free(Ptr); }
void VirtualName(const char*, void*, size_t) {}
bool VirtualProtect(void*, size_t, ProtectOptions) { return true; }
void* malloc(size_t n) { return std::malloc(n); }
void free(void* p) { std::free(p); }
}
using Client = PoolBufferWithTimedRetirement<uint8_t*, 5000, 500>;
static const size_t MB = 1024 * 1024;
int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  {   // (1) idle (disowned) buffers are taken back when the arena is full
    PooledAllocatorVirtual Pool {"t"};
    limit = 64 * MB; used = 0;
    std::vector<Client*> idle;
    for (int i = 0; i < 4; i++) { auto* c = new Client(Pool, 16 * MB); c->ReownOrClaimBuffer(); c->DelayedDisownBuffer(); c->ReownOrClaimBuffer(); c->DelayedDisownBuffer(); idle.push_back(c); }
    Client big(Pool, 32 * MB);
    uint8_t* p = big.ReownOrClaimBuffer();
    std::printf("reclaim-claim-ok=%d\n", p != nullptr);
    if (p) { std::memset(p, 1, 32 * MB); big.DelayedDisownBuffer(); }
    for (auto* c : idle) delete c;
  }
  {   // (2) every buffer owned: refused cleanly, retried once an owner disowns
    PooledAllocatorVirtual Pool {"t"};
    limit = 64 * MB; used = 0; free_null_calls = 0;
    std::vector<Client*> owners;
    for (int i = 0; i < 4; i++) { auto* c = new Client(Pool, 16 * MB); c->ReownOrClaimBuffer(); owners.push_back(c); }
    Client late(Pool, 16 * MB);
    uint8_t* p = late.ReownOrClaimBuffer();
    std::printf("all-owned-refused=%d\n", p == nullptr);
    late.DelayedDisownBuffer();   // (3) never claimed: harmless
    std::printf("disown-after-failed-claim-ok=1\n");
    owners[0]->DelayedDisownBuffer(); owners[0]->DelayedDisownBuffer();
    uint8_t* q = late.ReownOrClaimBuffer();
    std::printf("claim-after-owner-disowns=%d\n", q != nullptr);
    if (q) late.DelayedDisownBuffer();
    for (auto* c : owners) delete c;
    std::printf("free-null-calls=%d\n", free_null_calls);
  }
  {   // (4) concurrency: nobody's buffer is reclaimed while it is in use
    PooledAllocatorVirtual Pool {"t"};
    limit = 48 * MB; used = 0;
    std::atomic<int> bad {0}, refused {0}, served {0};
    std::vector<std::thread> ts;
    for (int t = 0; t < 8; t++) ts.emplace_back([&, t] {
      Client c(Pool, (t % 2 ? 8 : 16) * MB);
      for (int i = 0; i < 3000; i++) {
        uint8_t* p = c.ReownOrClaimBuffer();
        if (!p) { refused++; for (volatile int k = 0; k < 200; k = k + 1) {} continue; }
        served++;
        const uint8_t pat = static_cast<uint8_t>(t * 31 + 7);
        std::memset(p, pat, 2048);
        for (volatile int k = 0; k < 4000; k = k + 1) {}
        for (int k = 0; k < 2048; k++) if (p[k] != pat) { bad++; break; }
        c.DelayedDisownBuffer();
      }
    });
    for (auto& t : ts) t.join();
    std::printf("stress bad=%d served=%d refused=%d\n", bad.load(), served.load(), refused.load());
  }
  return 0;
}
'''


def build_and_run(header_text, tmp, tag):
    inc = Path(tmp) / (tag + "-inc")
    for rel, text in STUBS.items():
        (inc / rel).parent.mkdir(parents=True, exist_ok=True)
        (inc / rel).write_text(text)
    (inc / "FEXCore/Utils").mkdir(parents=True, exist_ok=True)
    (inc / "FEXCore/Utils/ThreadPoolAllocator.h").write_text(header_text)
    src = Path(tmp) / (tag + ".cpp")
    src.write_text(HARNESS)
    exe = Path(tmp) / tag
    cc = shutil.which("clang++") or shutil.which("c++")
    c = subprocess.run([cc, "-std=c++20", "-O1", "-pthread", "-I", str(inc), "-o", str(exe), str(src)], capture_output=True, text=True)
    if c.returncode != 0:
        return None, c.stdout + c.stderr
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    return r.stdout.splitlines(), r.stdout + r.stderr + ("" if r.returncode == 0 else "\n(exit %d)" % r.returncode)


def edited(a, b, rel):
    return (a / rel).read_text() != (b / rel).read_text()


if not (fex / POOL).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    for kind in ("ec", "wow"):
        with tempfile.TemporaryDirectory(prefix="madeira-fex-pool-") as tmp:
            tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
            res = fex_chain.run_chain(root, kind, tree, before=NAME)
            check(kind + ": the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
            before = Path(tmp) / "before"
            shutil.copytree(tree / "FEXCore", before / "FEXCore")
            r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
            once = {p: p.read_text() for p in (tree / "FEXCore").rglob("*") if p.is_file() and p.suffix in (".h", ".cpp")}
            r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
            same = all(p.read_text() == t for p, t in once.items())
            check(kind + ": it applies once; a second run says so and changes nothing", r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and same,
                  r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            res = fex_chain.run_chain(root, kind, tree, after=NAME)
            check(kind + ": the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
            changed = sorted(str(p.relative_to(tree / "FEXCore")) for p in (tree / "FEXCore").rglob("*") if p.is_file() and p.suffix in (".h", ".cpp")
                             and (before / "FEXCore" / p.relative_to(tree / "FEXCore")).is_file() and p.read_text() != (before / "FEXCore" / p.relative_to(tree / "FEXCore")).read_text()
                             and MARK in p.read_text())
            check(kind + ": exactly the nine named files carry the change (" + str(len(changed)) + ")", len(changed) == 9, changed)
            if kind == "ec":
                T = lambda rel: (tree / rel).read_text()
                pool = T(POOL)
                check("the claim API reports failure: ClaimBuffer / ReownOrClaimBuffer / ClaimBufferImpl return std::optional, ReownOrClaimBufferWithSize returns Ptr == 0",
                      pool.count("std::optional<ContainerType::iterator> ClaimBuffer(") == 1 and pool.count("std::optional<ContainerType::iterator> ReownOrClaimBuffer(") == 1
                      and pool.count("std::optional<ContainerType::iterator> ClaimBufferImpl(") == 1 and ".Ptr = Type {}," in pool)
                check("the virtual allocators neither name nor protect a NULL pointer", "if (Name && Result) {" in pool and "if (!Ptr) {   // " + MARK in pool)
                check("the six clients check it: IR list, IREmitter, ResetWorkingList, GenerateIR bail, decoder, JIT temp buffer",
                      "bool ReownOrClaimBuffer() {" in T("FEXCore/Source/Interface/IR/IntrusiveIRList.h") and "bool ReownOrClaimBuffer() {" in T("FEXCore/Source/Interface/IR/IREmitter.h")
                      and "bool OpDispatchBuilder::ResetWorkingList() {" in T("FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp")
                      and "if (!Thread->OpDispatcher->ResetWorkingList()) {" in T("FEXCore/Source/Interface/Core/Core.cpp")
                      and "[ir-alloc] rev=r652 no IR buffer" in T("FEXCore/Source/Interface/Core/Core.cpp") and "Thread->FrontendDecoder->AllocFailed()" in T("FEXCore/Source/Interface/Core/Core.cpp")
                      and "HitAllocFailure = DecodedBuffer == nullptr;" in T("FEXCore/Source/Interface/Core/Frontend.cpp")
                      and "if (!TempCodeBufferInfo.Ptr) {" in T("FEXCore/Source/Interface/Core/JIT/JIT.cpp"))
                cc = shutil.which("clang++") or shutil.which("c++")
                if not cc:
                    print("skip: no host C++ compiler")
                else:
                    new_out, new_log = build_and_run(pool, tmp, "new")
                    old_out, old_log = build_and_run((before / POOL).read_text() if (before / POOL).is_file() else (fex / POOL).read_text(), tmp, "old")
                    check("the patched header compiles against the stand-in allocator", new_out is not None, new_log)
                    if new_out:
                        d = dict(l.split("=", 1) for l in new_out if "=" in l and not l.startswith("stress"))
                        check("(1) a failing allocation takes the idle buffers back and the claim succeeds", d.get("reclaim-claim-ok") == "1", new_out)
                        check("(2) with every buffer owned the claim is refused (NULL) and registers no NULL buffer", d.get("all-owned-refused") == "1" and d.get("free-null-calls") == "0", new_out)
                        check("(3) DelayedDisownBuffer of a client whose claim failed is harmless", d.get("disown-after-failed-claim-ok") == "1", new_out)
                        check("    ...and the next claim succeeds once an owner has disowned", d.get("claim-after-owner-disowns") == "1", new_out)
                        st = [l for l in new_out if l.startswith("stress")]
                        check("(4) 8 threads claiming, writing, verifying and disowning under a tight limit: no buffer was reclaimed under its user (served some, bad=0)",
                              len(st) == 1 and " bad=0 " in st[0] and re.search(r"served=([1-9]\d*)", st[0]) is not None, new_out)
                    if old_out is not None:
                        d = dict(l.split("=", 1) for l in old_out if "=" in l and not l.startswith("stress"))
                        check("the PRISTINE header hands out NULL where the reclaim succeeds, and the same scenario then dies in the pool or frees a NULL buffer (the defect reproduced)",
                              d.get("reclaim-claim-ok") == "0" and ("(exit -" in old_log or d.get("free-null-calls") == "1"), old_log)
                    else:
                        check("the pristine header compiles in the stand-in (for the comparison)", False, old_log)

    # a half-patched tree and a moved anchor are refused, nothing written
    with tempfile.TemporaryDirectory(prefix="madeira-fex-pool-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        p = tree / "FEXCore/Source/Interface/Core/Frontend.cpp"
        p.write_text(p.read_text() + "\n// " + MARK + "\n")
        r = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a half-patched tree is refused", r.returncode != 0 and "partial state" in r.stderr, r.stdout + r.stderr)
    with tempfile.TemporaryDirectory(prefix="madeira-fex-pool-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        p = tree / "FEXCore/Source/Interface/Core/JIT/JIT.cpp"
        p.write_text(p.read_text().replace("TempAllocator.ReownOrClaimBufferWithSize(DesiredBufferRange);", "TempAllocator.ReownOrClaimBufferWithSize(DesiredBufferRange + 0);"))
        snapshot = {q: q.read_text() for q in (tree / "FEXCore").rglob("*") if q.is_file() and q.suffix in (".h", ".cpp")}
        r = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r.returncode != 0 and "not found once" in r.stderr and all(q.read_text() == t for q, t in snapshot.items()),
              r.stdout + r.stderr)

print("PASS: FEX pooled buffers" if ok else "FAILED: FEX pooled buffers")
sys.exit(0 if ok else 1)
