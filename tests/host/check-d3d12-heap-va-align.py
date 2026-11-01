#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""d3d12-heap-va-align (opt-in, round 626, agent C S1): a D3D12 heap starts on a 64 KB GPU address; no device.

Clair Obscur: Expedition 33 (Steam 1903340, Unreal Engine 5; Send Logs HWMBZ2 and MKX1Z2, build 625, iPad17,1) makes two
128 MB transient heaps with a placement alignment of 4 MB. Metal hands out about every other heap 32 KB off a 64 KB boundary of
the GPU address space (Direct3D 12 promises the boundary). UE's transient allocator aligns its sub-allocations to the address, so
a request of the heap's whole size does not fit the second heap, the allocator hands the render graph a NULL buffer and the render
thread reads address 8. With bcd.cfg / the game's file d3d12-heap-va-align = 1, madeira-d3d12 makes the Metal heap 64 KB larger,
measures where it starts (a 16 KB buffer at offset 0 of the empty heap) and places the application's offset 0 on the next 64 KB
address; 2 uses the heap's own alignment (at most 4 MB). Off (the default) nothing changes: the heap is SizeInBytes, the padding 0.

  * static: the key defaults to 0 and only 1 and 2 count; the heap struct records the padding and the Metal heap's size (zeroed at
    creation, the one place that makes a Metal heap sets both); both placement paths add the padding to the offset and test it
    against the Metal heap's size; the release takes back what was counted; the ml2201 lines; the catalog lists the key off; the
    Expedition 33 row and auto-tune's rule carry it; CI runs this check;
  * host: cuts the region between "heap-va-pad-test:begin" and "heap-va-pad-test:end" and the mode reader, compiles them against a
    model of UE's VA-aligned first-fit allocator and checks: today (no padding) a 128 MB request in a 128 MB heap at 0x...8000 does
    not fit and fits at 0x...0000; with the padding it fits at +0 on every base, the padding is 0 on an aligned base and never more
    than 48 KB for a 16 KB-granular heap; a 256 KB request in a 0x...8000 heap lands at +32768 (the first placement the logs show);
    the mode reader gives 0 for unset, 0, -1, 3 and 1/2 for 1/2, read once;
  * the whole runtime for syntax with the repo's llvm-mingw when its toolchain is present (the full tree).
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


def cut(sig):
    """The definition of a function (a forward declaration, which ends in ';' before any '{', is skipped)."""
    i = SRC.index(sig)
    while SRC.index(";", i) < SRC.index("{", i):
        i = SRC.index(sig, i + 1)
    j = SRC.index("{", i)
    depth = 0
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise SystemExit("unbalanced " + sig)


# --- static checks ---------------------------------------------------------
mode = cut("static int mad_heap_va_align_mode(void)")
check("d3d12-heap-va-align defaults to 0, is read once, and anything but 1 or 2 is off",
      'v = (int)mad_cfg_int_pe("d3d12-heap-va-align", 0); if (v < 0 || v > 2) v = 0;' in mode and "static int v = -1;" in mode)
st = SRC[SRC.index("struct mad_memheap {"):]
st = st[:st.index("};")]
check("the heap records the padding and the Metal heap's size", "UINT64 mpad, msize;" in st)
mk = cut("static HRESULT STDMETHODCALLTYPE device_CreateHeap(")
check("a heap is zeroed at creation (no key: padding 0)", "h = calloc(1, sizeof *h);" in mk)
check("the one place that makes a Metal heap sets its size first and asks Metal for that size",
      SRC.count("->mtl = ") == 1 and mk.index("h->msize = want;") < mk.index("h->mtl = MTLDevice_newPlacementHeap(hd->mtl_device, want, WMTResourceStorageModePrivate);"))
check("the size is SizeInBytes plus the boundary of the mode (0 when off)",
      "UINT64 va_align = mad_heap_va_align_for(mad_heap_va_align_mode(), desc->Alignment), want = desc->SizeInBytes + va_align;" in mk)
check("the padding is measured only when a boundary was asked, with a 16 KB buffer at offset 0, then released",
      "if (h->mtl && va_align) {" in mk and "pi.length = 16384;" in mk and "MTLHeap_newBufferAtOffset(h->mtl, &pi, 0)" in mk
      and "if (pi.gpu_address) h->mpad = mad_va_pad(pi.gpu_address, va_align);" in mk and "NSObject_release(pb);" in mk)
check("the bytes counted for the Metal gap are the Metal heap's", "InterlockedExchangeAdd64(&g_mheap_bytes, (LONG64)want);" in mk
      and "(LONG64)desc->SizeInBytes" not in mk)
rel = cut("static ULONG STDMETHODCALLTYPE memheap_Release(ID3D12Heap *This)")
check("the release takes back the same bytes", "InterlockedExchangeAdd64(&g_mheap_bytes, -(LONG64)h->msize);" in rel
      and "-(LONG64)h->desc.SizeInBytes" not in rel)
at = cut("static HRESULT mad_create_resource_at(")
check("both placements (texture, buffer) move the application's offset by the padding",
      at.count("UINT64 moff = poff + ph->mpad;") == 2)
check("both test the Metal alignment and the Metal heap's size with that offset",
      at.count("if (psz && pal && !(moff % pal) && moff + psz <= ph->msize)") == 2
      and "poff + psz <= ph->desc.SizeInBytes" not in at)
check("and place at it, keeping the application's offset for the alias reports",
      "r->texture = MTLHeap_newTextureAtOffset(ph->mtl, &ti, moff);" in at and "r->buffer = MTLHeap_newBufferAtOffset(ph->mtl, &info, moff);" in at
      and at.count("r->placed_off = poff;") == 2)
check("ml2201: the padded heap's start is logged (capped), and a transient heap's start with the key off too",
      "[madeira-d3d12] ml2201 heap %p (%llu KB, align %llu): start GPU address %#llx, phase %#llx of %llu -> padded by %#llx" in mk
      and "said_va++ < 64" in mk
      and "[madeira-d3d12] ml2201 transient heap %p start GPU address %#llx, 64 KB phase %#x (Direct3D 12: 0)%s" in SRC)
catalog = (R / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog lists d3d12-heap-va-align, off by default",
      re.search(r'"d3d12-heap-va-align": \{"category": "Direct3D 12"[^}]*"kind": "int", "default": "0"', catalog) is not None)
swift = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the generated catalog carries the key (regenerate it in the full tree)",
      re.search(r'ConfigOption\(key: "d3d12-heap-va-align", [^\n]*kind: \.int, defaultValue: "0"', swift) is not None)
lists = json.loads((R / "recommended/recommendations.json").read_text())
carriers = sorted(r["id"] for r in lists["rules"] if any((c["line"] if isinstance(c, dict) else c).startswith("d3d12-heap-va-align") for c in r["config"]))
check("only Expedition 33's row sets it, from build 626", carriers == ["clair-obscur-expedition-33"]
      and all({"line": "d3d12-heap-va-align = 1", "minBuild": 626} in r["config"] for r in lists["rules"] if r["id"] in carriers), carriers)
rules = (R / "build/tools/autotune_rules.py").read_text()
rule = rules[rules.index('"id": "ue-transient-heap-misaligned-va"'):] if '"id": "ue-transient-heap-misaligned-va"' in rules else ""
rule = rule[:rule.index("\n    },")] if rule else ""
check("auto-tune's rule adds the line after the render thread's read of address 8 behind two 128 MB transient heaps",
      '"lines": ["d3d12-heap-va-align = 1"]' in rule and 'AV READ of 8' in rule and 'size 131072 KB dim 1 flags 0 -> hr 0' in rule
      and '"api": "d3d12"' in rule, rule[:400])
exp = R / "tests/fixtures/autotune/expedition-33-transient-heap-va-2026-10-31.expect"
check("the fixture is there and expects the line", exp.is_file() and json.loads(exp.read_text()).get("next") == ["d3d12-heap-va-align = 1"])
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-d3d12-heap-va-align.py" in workflow)

# --- the padding on the host -------------------------------------------------
cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
b = SRC.index("/* heap-va-pad-test:begin")
e = SRC.index("/* heap-va-pad-test:end */")
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t UINT64;
static int g_cfg_reads;
static long long mad_cfg_int_pe(const char *key, long long dflt) {
    const char *v = getenv("HEAPVA_KEY");
    g_cfg_reads++;
    if (strcmp(key, "d3d12-heap-va-align")) { puts("FAIL: another key was read"); exit(1); }
    return v && *v ? atoll(v) : dflt;
}
''' + SRC[b:e] + "\n" + mode + r'''

#define ALIGN 65536ull
#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
static UINT64 align_up(UINT64 v, UINT64 a) { return (v + a - 1) & ~(a - 1); }
/* UE-like: first fit in GPU-address space; the heap offset, or -1 when it does not fit */
static long long ue_alloc(UINT64 heap_va, UINT64 heap_size, UINT64 used, UINT64 size, UINT64 alignment) {
    UINT64 va = align_up(heap_va + used, alignment);
    if (va + size > heap_va + heap_size) return -1;
    return (long long)(va - heap_va);
}
int main(int argc, char **argv) {
    if (argc > 1) {   /* the mode reader alone: print what it gives, twice (read once) */
        int a = mad_heap_va_align_mode(), b2 = mad_heap_va_align_mode();
        printf("%d %d %d\n", a, b2, g_cfg_reads);
        return 0;
    }
    const UINT64 heap = 128ull << 20, req = 128ull << 20;
    /* Metal's heap addresses in HWMBZ2: ...0000 and ...8000 alternate */
    const UINT64 bases[] = { 0x10012d60000ull, 0x10012d68000ull, 0x10013d70000ull, 0x10014d78000ull, 0x10014d74000ull, 0x10014d7c000ull };
    unsigned i;
    for (i = 0; i < sizeof bases / sizeof *bases; i++) {
        UINT64 base = bases[i], phase = base & (ALIGN - 1), pad = mad_va_pad(base, ALIGN);
        long long today = ue_alloc(base, heap, 0, req, ALIGN), fixed = ue_alloc(base + pad, heap, 0, req, ALIGN);
        printf("base %#llx phase %#llx: today %s; padded by %#llx: %s\n", (unsigned long long)base, (unsigned long long)phase,
               today < 0 ? "does not fit" : "fits", (unsigned long long)pad, fixed == 0 ? "fits at +0" : "STILL DOES NOT FIT");
        T(phase ? today < 0 : today == 0);                 /* the model shows the bug on a misaligned base only */
        T(fixed == 0);                                     /* the padding always fixes it */
        T(((base + pad) & (ALIGN - 1)) == 0);
        T(phase ? pad == ALIGN - phase : pad == 0);
        T(pad <= ALIGN - 16384);                           /* Metal heaps are 16 KB granular: at most 48 KB, inside the extra 64 KB */
        T(pad + req <= heap + mad_heap_va_align_for(1, 4194304));   /* the request fits the larger Metal heap */
    }
    T(ue_alloc(0x10012d68000ull, heap, 0, 256 << 10, ALIGN) == 32768);   /* the first placement the logs show in such a heap */
    T(((0x10012d68000ull + mad_va_pad(0x10012d68000ull, 4ull << 20)) & ((4ull << 20) - 1)) == 0);   /* mode 2: a 4 MB boundary */
    T(mad_va_pad(0x10012c00000ull, 4ull << 20) == 0);
    /* the boundary per mode: off, below 64 KB and unknown modes ask nothing */
    T(mad_heap_va_align_for(0, 4194304) == 0); T(mad_heap_va_align_for(1, 0) == 0); T(mad_heap_va_align_for(2, 0) == 0);
    T(mad_heap_va_align_for(1, 4096) == 0); T(mad_heap_va_align_for(3, 4194304) == 0); T(mad_heap_va_align_for(-1, 4194304) == 0);
    T(mad_heap_va_align_for(1, 65536) == 65536); T(mad_heap_va_align_for(1, 4194304) == 65536);
    T(mad_heap_va_align_for(2, 65536) == 65536); T(mad_heap_va_align_for(2, 4194304) == 4194304);
    T(mad_heap_va_align_for(2, 8ull << 20) == 4194304); T(mad_heap_va_align_for(2, 65536 * 3) == 65536);   /* not a power of two: 64 KB */
    puts("padding: fits at +0 on every base, 0 on an aligned one, at most 48 KB; boundaries per mode as documented");
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the padding and the mode reader compile on the host", r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print("     " + "\n     ".join(run.stdout.strip().splitlines()))
            check("the model: today's misaligned heap does not fit the request, the padded one always does", run.returncode == 0)
            env0 = {k: v for k, v in os.environ.items() if k != "HEAPVA_KEY"}
            for value, want in ((None, "0"), ("0", "0"), ("", "0"), ("-1", "0"), ("3", "0"), ("1", "1"), ("2", "2")):
                env = dict(env0)
                if value is not None:
                    env["HEAPVA_KEY"] = value
                got = subprocess.run([str(exe), "mode"], capture_output=True, text=True, env=env).stdout.split()
                check("key %s: mode %s, read once" % ("unset" if value is None else repr(value), want), got == [want, want, "1"], got)
else:
    print("note: no host C compiler; the padding was not run")

# --- the whole runtime, for syntax, with the repo's toolchain -----------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0, r.stderr[-2000:])
else:
    print("note: no llvm-mingw toolchain here (the full tree has it); the runtime was not compiled")

print("PASS: d3d12-heap-va-align" if ok else "FAILED: d3d12-heap-va-align")
sys.exit(0 if ok else 1)
