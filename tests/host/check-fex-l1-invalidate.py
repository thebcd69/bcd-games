#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX LookupCache: an invalidation clears the L1 entry of the block under every dynamic L1 size (round 632, Ixion's dfcc61490).

A dynamic L1 cache grows and shrinks between MIN_L1_ENTRIES and MAX_L1_ENTRIES and an entry stays at the index the size of its time gave
it. InvalidateCache cleared only the index of the size in force, so an entry made under another size outlived the invalidation of its
block: when the cache came back to that size the address found the entry again and ran the block's old translation. The change is for
the Windows modules only (_WIN32) and keeps the old statement for every other build. No setting: it clears entries whose GuestCode is
exactly the address being invalidated and nothing else, so it can only stop a stale translation from running.

Checks (FEX tree: $FEX_SRC, FEX/ of this checkout, ~/madeira-bcd/FEX; skipped without it):
  - the source: one _WIN32 block in InvalidateCache, the old statement kept in the #else branch, the loop walks MIN_L1_ENTRIES up to the
    size in force when the cache is dynamic and visits only the size in force when it is not, nothing else in the file reads the L1 any
    differently (FindBlock, CacheBlockMapping and the mask accessors are unchanged);
  - the L1 part of InvalidateCache, cut out of the file and run on the host (the _WIN32 branch for the Windows build, none for the old statement):
      * the old statement leaves a stale entry behind for every (size it was made under, size at invalidation) pair that differs, so the
        harness reproduces the bug; the new block leaves none, for every size the cache goes to afterwards (all 5 x 5 x 5 size walks);
      * an entry for another block that shares an index survives (only exact matches are cleared);
      * with the dynamic cache off, 200,000 random insert/invalidate/lookup steps end in the same memory image, byte for byte, with both;
      * the new block never touches an index at or past the end of the array (AddressSanitizer + UBSan run);
  - CI runs this check.
The host run is skipped when no C++ compiler is found.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-l1-invalidate.py" in wf)

cands = ([Path(os.environ["FEX_SRC"]).expanduser()] if os.environ.get("FEX_SRC") else []) + [root / "FEX", Path("~/madeira-bcd/FEX").expanduser()]
fex = next((c for c in cands if (c / "FEXCore/Source/Interface/Core/LookupCache.h").is_file()), None)
if fex is None:
    print("skip the FEX part: no FEX tree (set FEX_SRC)")
    print("PASS: FEX L1 invalidation" if ok else "FAILED: FEX L1 invalidation")
    sys.exit(0 if ok else 1)

src = (fex / "FEXCore/Source/Interface/Core/LookupCache.h").read_text()
head = "  void InvalidateCache(uint64_t Address, const LookupCacheWriteLockToken& lk) {\n"
check("InvalidateCache is there once", src.count(head) == 1)
start = src.index(head)
body_end = src.index("\n  }\n", start)
body = src[start:body_end]
l1_a = body.index("    // Do L1\n")
l1_b = body.index("    if (!DisableL2Cache()) {")
block = body[l1_a:l1_b]

check("one _WIN32 block", block.count("#if defined(_WIN32)") == 1 and block.count("#else") == 1 and block.count("#endif") == 1)
win, old = block.split("#if defined(_WIN32)\n")[1].split("#else\n")
old = old.split("#endif\n")[0]
check("the loop starts at MIN_L1_ENTRIES when the cache is dynamic and at the size in force when it is not",
      "for (size_t Entries = DynamicL1Cache() ? MIN_L1_ENTRIES : L1PointerMask + 1; Entries <= L1PointerMask + 1; Entries <<= 1) {" in win)
check("it reads the index under that size", "[Address & (Entries - 1)]" in win)
check("the old statement is kept for every other build",
      "[Address & L1PointerMask];" in old and old.strip().startswith("{"))
check("an entry is cleared only when it holds exactly this block", block.count("if (L1Entry.GuestCode == Address) {") == 1
      and block.count("L1Entry.GuestCode = 0;") == 1 and "HostCode = 0" not in block)
check("FindBlock still reads one index", src.count("reinterpret_cast<LookupCacheEntry*>(L1Pointer)[Address & L1PointerMask]") == 3,
      src.count("reinterpret_cast<LookupCacheEntry*>(L1Pointer)[Address & L1PointerMask]"))
check("the dynamic sizing is untouched",
      "L1PointerMask = CurrentL1Entries - 1;" in src and "VirtualDontNeed(FirstZeroL1Entry, ZeroMemorySize, false)" in src)

cxx = shutil.which("clang++") or shutil.which("c++") or shutil.which("g++")
if not cxx:
    print("skip the host run: no C++ compiler")
    print("PASS: FEX L1 invalidation" if ok else "FAILED: FEX L1 invalidation")
    sys.exit(0 if ok else 1)

HARNESS = r'''
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

struct Sim {
  struct LookupCacheEntry {
    uintptr_t HostCode;
    uintptr_t GuestCode;
  };
  static constexpr size_t MIN_L1_ENTRIES = 8 * 1024;
  static constexpr size_t MAX_L1_ENTRIES = 128 * 1024;

  bool Dyn;
  std::vector<LookupCacheEntry> Mem;
  uintptr_t L1Pointer;
  uintptr_t L1PointerMask;
  size_t Cur;

  explicit Sim(bool dyn) : Dyn(dyn), Mem(MAX_L1_ENTRIES) {
    std::memset(Mem.data(), 0, Mem.size() * sizeof(LookupCacheEntry));
    L1Pointer = reinterpret_cast<uintptr_t>(Mem.data());
    Cur = dyn ? MIN_L1_ENTRIES : MAX_L1_ENTRIES;
    L1PointerMask = Cur - 1;
  }
  bool DynamicL1Cache() const { return Dyn; }

  void Insert(uint64_t A) {
    auto& E = reinterpret_cast<LookupCacheEntry*>(L1Pointer)[A & L1PointerMask];
    E.GuestCode = A;
    E.HostCode = 0x1000 + (A & 0xffff);
  }
  bool Lookup(uint64_t A) const {
    return reinterpret_cast<LookupCacheEntry*>(L1Pointer)[A & L1PointerMask].GuestCode == A;
  }
  void SetSize(size_t N) {
    while (Cur < N) { Cur <<= 1; L1PointerMask = Cur - 1; }
    while (Cur > N) {
      Cur >>= 1; L1PointerMask = Cur - 1;
      // the real cache gives the dropped entries back to the OS (they read as zero afterwards)
      std::memset(&Mem[Cur], 0, (MAX_L1_ENTRIES - Cur) * sizeof(LookupCacheEntry));
    }
  }
  void Invalidate(uint64_t Address) {
@BLOCK@
  }
};

static uint64_t Hash(const Sim& S) {
  uint64_t H = 1469598103934665603ull;
  const auto* P = reinterpret_cast<const unsigned char*>(S.Mem.data());
  for (size_t i = 0; i < S.Mem.size() * sizeof(Sim::LookupCacheEntry); i++) { H ^= P[i]; H *= 1099511628211ull; }
  return H;
}

int main() {
  const size_t Sizes[] = {8 * 1024, 16 * 1024, 32 * 1024, 64 * 1024, 128 * 1024};
  const uint64_t Addrs[] = {0x10005, 0x20005, 0x12345, 0x7fff0005, 0x1fffffff, 0x3a000, 0x1e000};
  int stale = 0, walks = 0;
  for (uint64_t X : Addrs)
    for (size_t S : Sizes)
      for (size_t T : Sizes)
        for (size_t U : Sizes) {
          Sim C(true);
          C.SetSize(S);
          C.Insert(X);
          C.SetSize(T);
          C.Invalidate(X);
          C.SetSize(U);
          walks++;
          if (C.Lookup(X)) stale++;
        }
  std::printf("walks=%d\nstale=%d\n", walks, stale);

  // an entry for another block sharing the index survives
  int survive = 1;
  for (size_t S : Sizes) {
    Sim C(true);
    C.SetSize(S);
    const uint64_t X = 0x10005, Y = X + S;  // same index at this size, another block
    C.Insert(Y);
    C.Invalidate(X);
    if (!C.Lookup(Y)) survive = 0;
  }
  std::printf("collision_survives=%d\n", survive);

  // dynamic cache off: random steps, memory image hash
  Sim C(false);
  uint64_t R = 88172645463325252ull;
  int hits = 0;
  for (int i = 0; i < 200000; i++) {
    R ^= R << 13; R ^= R >> 7; R ^= R << 17;
    const uint64_t A = (R >> 8) & 0x3ffffff;
    switch (R & 3) {
    case 0: C.Insert(A); break;
    case 1: C.Invalidate(A); break;
    case 2: C.Invalidate(A & ~0xfull); C.Insert(A | 1); break;
    default: hits += C.Lookup(A); break;
    }
  }
  std::printf("static_hash=%llu\nstatic_hits=%d\n", (unsigned long long)Hash(C), hits);
  return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="madeira-l1inval-") as tmp:
    t = Path(tmp)
    # -D_WIN32 on a macOS host breaks the system headers: the block's one #if is renamed for the host run
    (t / "h.cpp").write_text(HARNESS.replace("@BLOCK@", block.replace("#if defined(_WIN32)", "#if defined(HARNESS_WIN32)")))
    out = {}
    for label, flags in (("new", ["-DHARNESS_WIN32"]), ("old", [])):
        exe = t / label
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wno-unused-parameter"] + flags + [str(t / "h.cpp"), "-o", str(exe)],
                           capture_output=True, text=True)
        check(f"the {label} block compiles on the host", r.returncode == 0, r.stderr)
        if r.returncode:
            continue
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        check(f"the {label} harness runs", run.returncode == 0, run.stderr)
        out[label] = dict(l.split("=") for l in run.stdout.split())
    if "new" in out and "old" in out:
        n, o = out["new"], out["old"]
        check("625 size walks per address, 7 addresses", n["walks"] == o["walks"] == str(7 * 125))
        check("the old statement leaves a stale entry in some walks (the bug is reproduced: %s of %s walks)" % (o["stale"], o["walks"]), int(o["stale"]) > 0, o)
        check("the new block leaves none in any walk", n["stale"] == "0", n)
        check("an entry of another block that shares the index survives (new and old)", n["collision_survives"] == o["collision_survives"] == "1", (n, o))
        check("with the dynamic cache off the memory image is the same, byte for byte, and the lookups agree",
              n["static_hash"] == o["static_hash"] and n["static_hits"] == o["static_hits"], (n, o))
    san = t / "san"
    r = subprocess.run([cxx, "-std=c++20", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-DHARNESS_WIN32",
                        str(t / "h.cpp"), "-o", str(san)], capture_output=True, text=True)
    if r.returncode == 0:
        run = subprocess.run([str(san)], capture_output=True, text=True)
        check("the new block stays inside the array (AddressSanitizer + UBSan)", run.returncode == 0, run.stderr[-1500:])
    else:
        print("skip the sanitizer run: this compiler cannot build with -fsanitize=address,undefined")

print("PASS: FEX L1 invalidation" if ok else "FAILED: FEX L1 invalidation")
sys.exit(0 if ok else 1)
