#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""sampler-cache (on by default, 0 turns it off): identical CreateSampler descriptions share one Metal sampler
(madeira-d3d12/src/pe/madeira_d3d12.c; from upstream Madeira's codex/d3d12-consolidation, 5a3db65f and 3557bae9, where it is
always on).

A game that calls ID3D12Device::CreateSampler every frame from a handful of descriptions got a new Metal sampler each time (a
host call) and the device kept every one for the process's life. Now a description seen before gets the device's first sampler
for it. Every Direct3D 12 game calls CreateSampler, so this check is the proof that nothing a game can see changes:

  - the source: the switch is read once through mad_cfg_int_pe with default 1; the cache is per device, at the END of struct
    mad_device (no other member moves) and freed with the device; mad_sampler_info still zeroes the whole structure (the padding
    is part of the key); the unix side writes nothing but gpu_resource_id back (when the dxmt tree is present);
  - on the host, CreateSampler and its helpers cut from the source and compiled beside the function as it was before the cache
    (kept in this file), both over the same recording stand-ins:
      * sampler-cache = 0 (the way back): for every case (no description, a description, a LOD bias, a sampler-reduction padding
        byte, a backend that gives no sampler or no resource id, a device list that cannot grow, the desc-guard on) the new code
        makes the same calls in the same order with the same arguments and leaves the same descriptor as the old code;
      * the default: THE EQUIVALENCE. 30,000 random calls on two devices from 700 descriptions (more than the table holds), with
        LOD biases, reduction bytes and a backend that refuses some descriptions: after every call the descriptor the new code
        wrote names a sampler that was created from exactly the 24 description bytes of that call, as the old code's does, with
        the same metadata, and both zero the descriptor for the same calls. A Metal sampler is immutable and neither code ever
        releases one, so that is all a shader can observe;
      * the cache's own behaviour: one Metal sampler per distinct description; the LOD bias is not part of the key but reaches
        the descriptor; any key byte (the padding byte too) makes another sampler; devices do not share; a table of 512 goes back
        to one sampler per call; a sampler the list could not keep is neither cached nor bound; the log lines are printed once;
  - no recommended list or built-in rule sets the key (no game depends on a value of it);
  - the catalog entry (default 1), the credit, and CI runs this check; the whole runtime compiles in the full tree.
NOT covered: a device (the Metal call itself).
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()


def cut(start, end="\n}\n"):
    a = SRC.index(start)
    return SRC[a:SRC.index(end, a) + len(end)]


# --- the source ---------------------------------------------------------------
on_fn = cut("static int mad_smp_cache_on(void) {")
check("the switch is read through mad_cfg_int_pe, default 1 (on), 0 turns it off",
      'mad_cfg_int_pe("sampler-cache", 1) ? 1 : 0;' in on_fn and SRC.count('"sampler-cache"') == 1)
check("...once: the first reader publishes it with a compare-exchange from -1 and prints one line that says which",
      "static volatile LONG g_smp_cache_on = -1;" in SRC and "InterlockedCompareExchange(&g_smp_cache_on, cfg, -1);" in on_fn
      and "if (prev < 0)" in on_fn and "return on > 0;" in on_fn)
dev = cut("struct mad_device {", "\n};\n")
tail = [ln.strip() for ln in dev.strip().splitlines() if ln.strip() and not ln.strip().startswith(("/*", "*"))][-3:]
check("the table and its count are the last two members of struct mad_device (no other member moves)",
      tail == ["struct mad_smp_cache_ent *smp_cache;", "LONG smp_cache_n;", "};"], tail)
check("the key is the converted description up to the id the backend writes",
      "struct mad_smp_cache_ent { unsigned char key[offsetof(struct WMTSamplerInfo, gpu_resource_id)]; UINT64 id; obj_handle_t smp; };" in SRC
      and "#define MAD_SMP_CACHE_N 512" in SRC)
info = cut("static void mad_sampler_info(struct WMTSamplerInfo *si, UINT filter, UINT au, UINT av, UINT aw, UINT aniso, UINT cmp, UINT border, float minlod, float maxlod, const char *who) {")
check("mad_sampler_info zeroes the whole structure before it fills it (the padding is part of the key)",
      "memset(si, 0, sizeof *si);" in info and info.index("memset(si, 0, sizeof *si);") < info.index("si->min_filter ="))
rel = cut("static ULONG STDMETHODCALLTYPE device_Release(ID3D12Device *This) {")
check("the device frees its table (the samplers stay for the process's life, as before)",
      "free(d->smp_cache);" in rel and "d->samplers[" not in rel)
cs = cut("static void STDMETHODCALLTYPE device_CreateSampler(ID3D12Device *This,")
check("CreateSampler asks the switch once per call and the cache only when it is on",
      cs.count("mad_smp_cache_on()") == 1 and "if (cache_on) cached = mad_smp_cache_get(d, &si, &smp);" in cs
      and "if (!cache_on) mad_note_sampler(d, smp);" in cs)
check("the static samplers of a root signature are not cached (their own call, as before)",
      SRC.count("mad_smp_cache_get(") == 2 and SRC.count("mad_smp_cache_put(") == 2 and SRC.count("MTLDevice_newSamplerState(") == 2)
unix = R / "dxmt/src/winemetal/unix/winemetal_unix.c"
if unix.exists():
    u = unix.read_text()
    a = u.index("_MTLDevice_newSamplerState(void *obj) {")
    body = u[a:u.index("\n}\n", a)]
    writes = [ln.strip() for ln in body.splitlines() if "info->" in ln and "=" in ln.split("info->", 1)[1].split(";")[0]
              and not ln.strip().startswith("sampler_desc.") and "==" not in ln]
    check("the backend writes nothing into the description but gpu_resource_id", all("gpu_resource_id" in w for w in writes) and writes, writes)
else:
    print("note: dxmt is not in this tree; the backend's write-back is checked in the full tree")

# --- on the host ----------------------------------------------------------------
OLD = r'''
static void old_note_sampler(struct mad_device *d, obj_handle_t smp) {
    int kept = 0;
    if (!d || !smp) return;
    AcquireSRWLockExclusive(&d->list_lock);
    if (mad_grow((void **)&d->samplers, &d->samplers_cap, d->nsamplers + 1, sizeof *d->samplers)) {
        d->samplers[d->nsamplers++] = smp; kept = 1;
    }
    ReleaseSRWLockExclusive(&d->list_lock);
    if (!kept) NSObject_release(smp);
}

static void STDMETHODCALLTYPE old_CreateSampler(ID3D12Device *This,
        const D3D12_SAMPLER_DESC *desc, D3D12_CPU_DESCRIPTOR_HANDLE h) {
    struct mad_device *d = (struct mad_device *)This;
    struct mad_descriptor *e = (struct mad_descriptor *)h.ptr;
    if (!e) return;
    if (g_sd_state > 0) mad_dg_write(e, 1, "CreateSampler");   /* madeira-bcd: desc-guard */

    struct WMTSamplerInfo si;
    if (desc) {
        UINT border = (desc->BorderColor[0] > 0.5f) ? 2u : (desc->BorderColor[3] > 0.5f) ? 1u : 0u;
        mad_sampler_info(&si, desc->Filter, desc->AddressU, desc->AddressV, desc->AddressW, desc->MaxAnisotropy, desc->ComparisonFunc, border, desc->MinLOD, desc->MaxLOD, "CreateSampler");
    } else mad_sampler_info(&si, D3D12_FILTER_MIN_MAG_MIP_LINEAR, 3, 3, 3, 1, 0, 0, 0.0f, 1000.0f, "CreateSampler (no description)");

    obj_handle_t smp = MTLDevice_newSamplerState(d->mtl_device, &si);
    if (!smp || !si.gpu_resource_id) {
        d3d12_log("[madeira-d3d12] CreateSampler: the backend gave no argument-buffer sampler\n");
        memset(e, 0, sizeof *e);
        return;
    }
    /* IRDescriptorTableSetSampler: resource id first, LOD bias in the metadata. */
    float bias = desc ? desc->MipLODBias : 0.0f;
    UINT32 bias_bits;
    memcpy(&bias_bits, &bias, sizeof bias_bits);
    if (bias != 0.0f) {   /* madeira-bcd: shaders apply it since MSC's sampler-LOD-bias flag */
        static LONG said;
        if (InterlockedIncrement(&said) <= 8)
            d3d12_log("[madeira-d3d12] sampler with MipLODBias %.3f (filter 0x%x)\n", bias, (unsigned)desc->Filter);
    }
    e->gpu_va = si.gpu_resource_id;
    e->texture_view_id = 0;
    e->metadata = (UINT64)bias_bits;
    if (desc)   /* madeira-bcd: for targeted captures (g_smpdesc) */
        mad_smpdesc_put(si.gpu_resource_id, desc->Filter, desc->AddressU, desc->AddressV, desc->AddressW, desc->MaxAnisotropy,
                        desc->ComparisonFunc, (desc->BorderColor[0] > 0.5f) ? 2u : (desc->BorderColor[3] > 0.5f) ? 1u : 0u,
                        desc->MinLOD, desc->MaxLOD, bias);
    /* The sampler object itself is kept alive by the device for the process's
     * life. Samplers are few and immutable, and tying one to a descriptor slot
     * that the application may overwrite would need a lifetime story that this
     * milestone does not have. */
    old_note_sampler(d, smp);
}
'''
note_new = cut("static int mad_note_sampler(struct mad_device *d, obj_handle_t smp) {")
cache = SRC[SRC.index("#define MAD_SMP_CACHE_N 512"):SRC.index("static void STDMETHODCALLTYPE device_CreateSampler(ID3D12Device *This,")]

PRELUDE = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdbool.h>
typedef unsigned int UINT; typedef uint32_t UINT32; typedef uint64_t UINT64; typedef int LONG; typedef uint64_t obj_handle_t;
typedef struct { int readers, writer; } SRWLOCK;
#define SRWLOCK_INIT {0, 0}
#define STDMETHODCALLTYPE
typedef struct ID3D12Device ID3D12Device;
typedef struct { UINT Filter, AddressU, AddressV, AddressW; float MipLODBias; UINT MaxAnisotropy, ComparisonFunc; float BorderColor[4]; float MinLOD, MaxLOD; } D3D12_SAMPLER_DESC;
typedef struct { size_t ptr; } D3D12_CPU_DESCRIPTOR_HANDLE;
#define D3D12_FILTER_MIN_MAG_MIP_LINEAR 0x15
/* the layout of DXMT's WMTSamplerInfo: 23 bytes of description, one padding byte, the id the backend writes */
struct WMTSamplerInfo { uint8_t min_filter, mag_filter, mip_filter, r_address_mode, s_address_mode, t_address_mode, border_color, compare_function;
                        float lod_min_clamp, lod_max_clamp; uint32_t max_anisotroy; bool normalized_coords, lod_average, support_argument_buffers;
                        uint64_t gpu_resource_id; };
_Static_assert(sizeof(struct WMTSamplerInfo) == 32 && offsetof(struct WMTSamplerInfo, gpu_resource_id) == 24, "WMTSamplerInfo");
struct mad_descriptor { UINT64 gpu_va, texture_view_id, metadata; };
struct mad_smp_cache_ent;
static SRWLOCK g_smp_cache_lock = SRWLOCK_INIT;
struct mad_device { obj_handle_t mtl_device; obj_handle_t *samplers; unsigned nsamplers, samplers_cap; SRWLOCK list_lock;
                    struct mad_smp_cache_ent *smp_cache; LONG smp_cache_n; };

/* the recording stand-ins: every call the two functions make leaves one line */
static char g_trace[1 << 20]; static size_t g_tn; static int g_quiet;
static void T(const char *fmt, ...) { va_list ap; if (g_quiet) return; va_start(ap, fmt); g_tn += (size_t)vsnprintf(g_trace + g_tn, sizeof g_trace - g_tn, fmt, ap); va_end(ap); }
static int g_sd_state, g_key = -1, g_cfg_reads, g_grow_fail, g_backend_null, g_backend_noid, g_pad;
static unsigned g_made; static UINT64 g_next_id = 0x1000;
/* the model of what a shader can observe: the 24 description bytes each sampler id was created from */
static unsigned char (*g_canon)[24]; static int g_refuse_some;
static void AcquireSRWLockExclusive(SRWLOCK *l) { if (l->readers || l->writer) T("LOCK-ERROR\n"); l->writer = 1; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { if (!l->writer) T("LOCK-ERROR\n"); l->writer = 0; }
static void AcquireSRWLockShared(SRWLOCK *l) { if (l->writer) T("LOCK-ERROR\n"); l->readers++; }
static void ReleaseSRWLockShared(SRWLOCK *l) { if (!l->readers) T("LOCK-ERROR\n"); l->readers--; }
static LONG InterlockedCompareExchange(volatile LONG *p, LONG x, LONG c) { LONG o = *p; if (o == c) *p = x; return o; }
static LONG InterlockedExchange(LONG *p, LONG x) { LONG o = *p; *p = x; return o; }
static LONG InterlockedIncrement(LONG *p) { return ++*p; }
static void mad_dg_write(struct mad_descriptor *e, int n, const char *who) { T("dg_write %d %s\n", n, who); (void)e; }
static long long mad_cfg_int_pe(const char *key, long long dflt) { g_cfg_reads++; if (strcmp(key, "sampler-cache")) T("CFG-ERROR %s\n", key); return g_key < 0 ? dflt : g_key; }
static void d3d12_log(const char *fmt, ...) { va_list ap; if (g_quiet) return; T("log "); va_start(ap, fmt); g_tn += (size_t)vsnprintf(g_trace + g_tn, sizeof g_trace - g_tn, fmt, ap); va_end(ap); }
static void mad_sampler_info(struct WMTSamplerInfo *si, UINT filter, UINT au, UINT av, UINT aw, UINT aniso, UINT cmp, UINT border, float minlod, float maxlod, const char *who) {
    memset(si, 0, sizeof *si);
    si->min_filter = (filter >> 4) & 1; si->mag_filter = (filter >> 2) & 1; si->mip_filter = filter & 1;
    si->s_address_mode = (uint8_t)au; si->t_address_mode = (uint8_t)av; si->r_address_mode = (uint8_t)aw; si->border_color = (uint8_t)border;
    si->compare_function = (uint8_t)cmp; si->lod_min_clamp = minlod; si->lod_max_clamp = maxlod; si->max_anisotroy = aniso;
    si->normalized_coords = true; si->support_argument_buffers = true;
    if (g_pad) ((unsigned char *)si)[offsetof(struct WMTSamplerInfo, support_argument_buffers) + 1] = (unsigned char)g_pad;   /* sampler-reduction = 3 */
    T("info f=%x %u/%u/%u an=%u cmp=%u b=%u lod=%g..%g %s\n", filter, au, av, aw, aniso, cmp, border, (double)minlod, (double)maxlod, who);
}
static obj_handle_t MTLDevice_newSamplerState(obj_handle_t dev, struct WMTSamplerInfo *si) {
    unsigned char k[24]; char hex[49]; int i;
    memcpy(k, si, 24); for (i = 0; i < 24; i++) sprintf(hex + 2 * i, "%02x", k[i]);
    g_made++;
    if (g_refuse_some) {   /* a backend that refuses some descriptions, the same ones whoever asks */
        unsigned h = 2166136261u; for (i = 0; i < 24; i++) h = (h ^ k[i]) * 16777619u;
        if (h % 53 == 0) return 0;
        if (h % 47 == 0) { si->gpu_resource_id = 0; return 0xA000 + g_made; }
    }
    if (g_canon) memcpy(g_canon[g_next_id - 0x1000], k, 24);
    if (g_backend_null) { T("newSampler dev=%llx key=%s -> 0\n", (unsigned long long)dev, hex); return 0; }
    si->gpu_resource_id = g_backend_noid ? 0 : g_next_id++;
    T("newSampler dev=%llx key=%s -> h=%llx id=%llx\n", (unsigned long long)dev, hex, (unsigned long long)(0xA000 + g_made), (unsigned long long)si->gpu_resource_id);
    return 0xA000 + g_made;
}
static int mad_grow(void **v, unsigned *cap, unsigned need, size_t el) {
    if (g_grow_fail) { T("grow REFUSED\n"); return 0; }
    if (need > *cap) { *v = realloc(*v, (size_t)(need + 16) * el); *cap = need + 16; }
    T("grow %u\n", need); return 1;
}
static void NSObject_release(obj_handle_t o) { T("release %llx\n", (unsigned long long)o); }
static void mad_smpdesc_put(UINT64 id, UINT filter, UINT au, UINT av, UINT aw, UINT aniso, UINT cmp, UINT border, float minlod, float maxlod, float bias) {
    T("smpdesc id=%llx f=%x %u/%u/%u an=%u cmp=%u b=%u lod=%g..%g bias=%g\n", (unsigned long long)id, filter, au, av, aw, aniso, cmp, border, (double)minlod, (double)maxlod, (double)bias);
}
'''
MAIN = r'''
static void dump(const char *what, struct mad_descriptor *e) { T("%s: va=%llx view=%llx meta=%llx\n", what, (unsigned long long)e->gpu_va, (unsigned long long)e->texture_view_id, (unsigned long long)e->metadata); }
static D3D12_SAMPLER_DESC desc_n(unsigned n) {
    D3D12_SAMPLER_DESC d = { 0x15, 1, 1, 1, 0.0f, 1, 0, { 0, 0, 0, 0 }, 0.0f, 1000.0f };
    d.Filter = (n & 1) ? 0x55 : 0x15; d.AddressU = 1 + (n >> 1) % 5; d.AddressV = 1 + (n >> 4) % 5; d.MaxAnisotropy = 1 + (n >> 7);
    return d;
}
typedef void (*create_fn)(ID3D12Device *, const D3D12_SAMPLER_DESC *, D3D12_CPU_DESCRIPTOR_HANDLE);
/* the cases a CreateSampler call can meet, run through one implementation on a fresh device */
static void scenario(create_fn create) {
    struct mad_device dev; struct mad_descriptor e; D3D12_CPU_DESCRIPTOR_HANDLE h = { (size_t)&e };
    D3D12_SAMPLER_DESC a = desc_n(3), b = desc_n(3), c = desc_n(9);
    unsigned i;
    memset(&dev, 0, sizeof dev); dev.mtl_device = 0xD0;
    memset(&e, 0xee, sizeof e);
    create((ID3D12Device *)&dev, NULL, (D3D12_CPU_DESCRIPTOR_HANDLE){ 0 }); T("-- null handle\n");
    create((ID3D12Device *)&dev, NULL, h); dump("no description", &e);
    create((ID3D12Device *)&dev, &a, h); dump("a", &e);
    create((ID3D12Device *)&dev, &b, h); dump("a again", &e);
    b.MipLODBias = -0.75f; create((ID3D12Device *)&dev, &b, h); dump("a with a bias", &e);
    create((ID3D12Device *)&dev, &c, h); dump("c", &e);
    g_pad = 0xA1; create((ID3D12Device *)&dev, &a, h); dump("a with a reduction mode in the padding", &e); g_pad = 0;
    g_sd_state = 1; create((ID3D12Device *)&dev, &a, h); dump("a under desc-guard", &e); g_sd_state = 0;
    g_backend_null = 1; memset(&e, 0xee, sizeof e); create((ID3D12Device *)&dev, &c, h); dump("backend gives nothing", &e); g_backend_null = 0;
    g_backend_noid = 1; memset(&e, 0xee, sizeof e); create((ID3D12Device *)&dev, &c, h); dump("backend gives no id", &e); g_backend_noid = 0;
    g_grow_fail = 1; memset(&e, 0xee, sizeof e); create((ID3D12Device *)&dev, &c, h); dump("the list cannot grow", &e); g_grow_fail = 0;
    for (i = 0; i < 40; i++) { D3D12_SAMPLER_DESC d = desc_n(i % 7); create((ID3D12Device *)&dev, &d, h); }
    dump("after 40 calls from 7 descriptions", &e);
    T("device list: %u samplers\n", dev.nsamplers);
    free(dev.samplers); free(dev.smp_cache);
}
int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "";
    if (!strcmp(mode, "old")) { scenario(old_CreateSampler); fputs(g_trace, stdout); return 0; }
    if (!strcmp(mode, "new")) { g_key = argc > 2 ? atoi(argv[2]) : -1; scenario(device_CreateSampler); fputs(g_trace, stdout); printf("cfg reads: %d\n", g_cfg_reads); return 0; }
    if (!strcmp(mode, "equiv")) {   /* equiv old | equiv new: what every descriptor names after every call, as text */
        create_fn create = !strcmp(argv[2], "old") ? old_CreateSampler : device_CreateSampler;
        struct mad_device dev[2]; struct mad_descriptor e; D3D12_CPU_DESCRIPTOR_HANDLE h = { (size_t)&e };
        unsigned long long seed = 0x9E3779B97F4A7C15ull; unsigned n, zeroed = 0;
        memset(dev, 0, sizeof dev); dev[0].mtl_device = 0xD1; dev[1].mtl_device = 0xD2;
        g_canon = calloc(70000, 24); g_refuse_some = 1; g_quiet = 1; g_key = -1;   /* the default: nothing in bcd.cfg */
        for (n = 0; n < 30000; n++) {
            unsigned r, pick; D3D12_SAMPLER_DESC d;
            seed = seed * 6364136223846793005ull + 1442695040888963407ull; r = (unsigned)(seed >> 33);
            pick = (r & 7) < 5 ? (r >> 3) % 12 : (r >> 3) % 700;          /* a few descriptions over and over, and a long tail */
            d = desc_n(pick); d.MinLOD = (float)(pick / 64); d.MaxLOD = 1000.0f - (float)(pick % 5);
            d.ComparisonFunc = pick % 9 == 0 ? 4 : 0; if (pick % 9 == 0) d.Filter |= 0x80;
            d.BorderColor[3] = pick % 7 == 0 ? 1.0f : 0.0f; d.AddressW = 1 + pick % 5;
            d.MipLODBias = (r >> 20) % 4 == 0 ? -0.5f * (float)((r >> 22) % 4) : 0.0f;   /* the bias varies per call, not per description */
            g_pad = (r >> 25) % 16 == 0 ? 0xA1 + (int)((r >> 29) & 1) : 0;                 /* a reduction byte now and then */
            memset(&e, 0xee, sizeof e);
            create((ID3D12Device *)&dev[(r >> 30) & 1], (r >> 31) ? NULL : &d, h);
            if (!e.gpu_va) { zeroed++; printf("%u zero %llx %llx\n", n, (unsigned long long)e.texture_view_id, (unsigned long long)e.metadata); }
            else {
                const unsigned char *k = g_canon[e.gpu_va - 0x1000]; char hex[49]; int i;
                for (i = 0; i < 24; i++) sprintf(hex + 2 * i, "%02x", k[i]);
                printf("%u dev%u %s %llx %llx\n", n, (r >> 30) & 1, hex, (unsigned long long)e.texture_view_id, (unsigned long long)e.metadata);
            }
        }
        fprintf(stderr, "%u Metal samplers made, %u in the devices' lists, %u descriptors zeroed\n", g_made, dev[0].nsamplers + dev[1].nsamplers, zeroed);
        return 0;
    }
    if (!strcmp(mode, "on")) {
        struct mad_device d1, d2; struct mad_descriptor e, e2; D3D12_CPU_DESCRIPTOR_HANDLE h = { (size_t)&e }, h2 = { (size_t)&e2 };
        D3D12_SAMPLER_DESC a = desc_n(3), b = desc_n(3), c;
        unsigned i, made, fails = 0; UINT64 id_a;
        #define CHECK(x) do { if (!(x)) { printf("FAILED line %d: %s\n", __LINE__, #x); fails++; } } while (0)
        memset(&d1, 0, sizeof d1); memset(&d2, 0, sizeof d2); d1.mtl_device = 0xD1; d2.mtl_device = 0xD2; g_key = 1;
        device_CreateSampler((ID3D12Device *)&d1, &a, h); id_a = e.gpu_va; CHECK(g_made == 1 && d1.nsamplers == 1 && d1.smp_cache_n == 1 && id_a);
        for (i = 0; i < 1000; i++) { device_CreateSampler((ID3D12Device *)&d1, &b, h); CHECK(e.gpu_va == id_a && e.metadata == 0); }
        CHECK(g_made == 1 && d1.nsamplers == 1 && d1.smp_cache_n == 1);                       /* 1001 calls, one Metal sampler, one list entry */
        b.MipLODBias = 1.5f; device_CreateSampler((ID3D12Device *)&d1, &b, h);
        { float f; UINT32 bits = (UINT32)e.metadata; memcpy(&f, &bits, 4); CHECK(g_made == 1 && e.gpu_va == id_a && f == 1.5f); }   /* the bias: same sampler, its own metadata */
        device_CreateSampler((ID3D12Device *)&d1, NULL, h); device_CreateSampler((ID3D12Device *)&d1, NULL, h); CHECK(g_made == 2 && e.gpu_va != id_a);   /* no description: cached too */
        g_pad = 0xA1; device_CreateSampler((ID3D12Device *)&d1, &a, h); CHECK(g_made == 3 && e.gpu_va != id_a);   /* the padding byte is part of the key */
        g_pad = 0xA2; device_CreateSampler((ID3D12Device *)&d1, &a, h); CHECK(g_made == 4);
        g_pad = 0xA1; device_CreateSampler((ID3D12Device *)&d1, &a, h); CHECK(g_made == 4); g_pad = 0;
        device_CreateSampler((ID3D12Device *)&d2, &a, h2); CHECK(g_made == 5 && e2.gpu_va != id_a && d2.smp_cache_n == 1 && d2.nsamplers == 1);   /* another device: its own */
        made = g_made;                                                                         /* every field of the description is in the key */
        c = a; c.Filter ^= 4; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.AddressU = 3; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.AddressV = 3; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.AddressW = 3; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.MaxAnisotropy = 8; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.ComparisonFunc = 4; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.BorderColor[3] = 1.0f; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.MinLOD = 2.0f; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        c = a; c.MaxLOD = 4.0f; device_CreateSampler((ID3D12Device *)&d1, &c, h);
        CHECK(g_made == made + 9);
        g_backend_null = 1; memset(&e, 0xee, sizeof e); c = a; c.MaxLOD = 5.0f; made = d1.nsamplers;      /* nothing from the backend: nothing cached */
        device_CreateSampler((ID3D12Device *)&d1, &c, h); CHECK(!e.gpu_va && !e.metadata && d1.nsamplers == made); g_backend_null = 0;
        i = (unsigned)d1.smp_cache_n; device_CreateSampler((ID3D12Device *)&d1, &c, h); CHECK(e.gpu_va && d1.smp_cache_n == (LONG)i + 1);
        g_grow_fail = 1; memset(&e, 0xee, sizeof e); c = a; c.MaxLOD = 6.0f; i = (unsigned)d1.smp_cache_n;   /* the list cannot keep it: released, not cached, not bound */
        device_CreateSampler((ID3D12Device *)&d1, &c, h); CHECK(!e.gpu_va && d1.smp_cache_n == (LONG)i && strstr(g_trace, "release")); g_grow_fail = 0;
        device_CreateSampler((ID3D12Device *)&d1, &c, h); CHECK(e.gpu_va && d1.smp_cache_n == (LONG)i + 1);
        for (i = 0; i < 600; i++) { c = a; c.MaxLOD = 100.0f + (float)i; device_CreateSampler((ID3D12Device *)&d1, &c, h); }   /* more descriptions than the table holds */
        CHECK(d1.smp_cache_n == 512);
        made = g_made; c = a; c.MaxLOD = 100.0f + 599.0f; device_CreateSampler((ID3D12Device *)&d1, &c, h); CHECK(g_made == made + 1 && e.gpu_va);   /* past the table: one per call, as before */
        made = g_made; device_CreateSampler((ID3D12Device *)&d1, &a, h); CHECK(g_made == made && e.gpu_va == id_a);            /* the cached ones still hit */
        CHECK(g_cfg_reads == 1);
        { const char *p = g_trace; int said_on = 0, said_hit = 0; while ((p = strstr(p, "log [madeira-d3d12] sampler-cache"))) { if (!strncmp(p + 33, " = 1:", 5)) said_on++; else said_hit++; p++; }
          CHECK(said_on == 1 && said_hit == 1); }
        CHECK(!strstr(g_trace, "LOCK-ERROR") && !strstr(g_trace, "CFG-ERROR"));
        free(d1.samplers); free(d1.smp_cache); free(d2.samplers); free(d2.smp_cache);
        printf(fails ? "FAILED\n" : "on: one Metal sampler per description and device (%u made for more than 2,600 calls)\n", g_made);
        return fails ? 1 : 0;
    }
    return 2;
}
'''
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(PRELUDE + OLD + note_new + cache + cs + MAIN)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("CreateSampler, the cache and the list helper compile on the host beside the earlier code", r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            old = subprocess.run([str(exe), "old"], capture_output=True, text=True)
            check("the earlier code runs the cases (the reference)", old.returncode == 0 and old.stdout.count("newSampler") >= 45
                  and "LOCK-ERROR" not in old.stdout, old.stdout[-500:])
            new = subprocess.run([str(exe), "new", "0"], capture_output=True, text=True)
            body, reads = new.stdout.rsplit("cfg reads: ", 1) if "cfg reads: " in new.stdout else (new.stdout, "?")
            said = [ln for ln in body.splitlines() if ln.startswith("log [madeira-d3d12] sampler-cache")]
            body0 = "\n".join(ln for ln in body.splitlines() if not ln.startswith("log [madeira-d3d12] sampler-cache")) + "\n"
            check("sampler-cache = 0: the same calls, in the same order, with the same arguments and descriptors as before the cache",
                  new.returncode == 0 and body0 == old.stdout,
                  "\n".join(a + "\n   vs " + b for a, b in zip(old.stdout.splitlines(), body0.splitlines()) if a != b)[:1500])
            check("sampler-cache = 0: the configuration is read once and one line says the sharing is off",
                  reads.strip() == "1" and len(said) == 1 and "sampler-cache = 0: every CreateSampler call makes its own Metal sampler" in said[0], said)
            eo = subprocess.run([str(exe), "equiv", "old"], capture_output=True, text=True)
            en = subprocess.run([str(exe), "equiv", "new"], capture_output=True, text=True)
            print("     before the cache: " + eo.stderr.strip())
            print("     with the cache:   " + en.stderr.strip())
            lo, ln_ = eo.stdout.splitlines(), en.stdout.splitlines()
            firstdiff = next((a + "  vs  " + b for a, b in zip(lo, ln_) if a != b), "")
            check("the default (cache on): after each of 30,000 random calls the descriptor names a sampler made from that call's own description, "
                  "with the same metadata, exactly as before the cache; the same calls end with a zeroed descriptor",
                  eo.returncode == 0 and en.returncode == 0 and len(lo) == 30000 and lo == ln_, firstdiff)
            mo = [int(x) for x in re.findall(r"\d+", eo.stderr)]
            mn = [int(x) for x in re.findall(r"\d+", en.stderr)]
            check("...and it did so with far fewer Metal samplers (the table was full part of the time: more than 512 descriptions per device)",
                  len(mo) == 3 and len(mn) == 3 and mo[0] == 30000 and mn[0] < mo[0] // 3 and mn[1] <= mn[0] and any(" zero " in x for x in lo), (mo, mn))
            on = subprocess.run([str(exe), "on"], capture_output=True, text=True)
            print("     " + (on.stdout.strip().splitlines()[-1] if on.stdout.strip() else on.stderr.strip()[-300:]))
            check("the cache itself: descriptions share a sampler per device; bias, padding, a full table and the failure paths behave",
                  on.returncode == 0, on.stdout[-1500:])
            dflt = subprocess.run([str(exe), "new"], capture_output=True, text=True)
            check("nothing in bcd.cfg: the cache is on (one line says so)",
                  dflt.returncode == 0 and dflt.stdout.count("log [madeira-d3d12] sampler-cache = 1: identical CreateSampler descriptions share one Metal sampler") == 1)
else:
    print("note: no host C compiler; the functions were not run")

# --- nobody sets it ---------------------------------------------------------------
rules = json.loads((R / "recommended/recommendations.json").read_text())["rules"]
setters = []
for rule in rules:
    for item in rule.get("config", []):
        line = item["line"] if isinstance(item, dict) else item
        if line.split("=")[0].strip() == "sampler-cache":
            setters.append(rule["id"])
check("no recommended list sets sampler-cache (no game depends on a value of it)", not setters, setters)
tlou = [r for r in rules if r["id"] == "the-last-of-us-part-1"]
check("...and The Last of Us Part I's list is there to be looked at", len(tlou) == 1 and len(tlou[0]["config"]) > 20)
for gen in ("app/Madeira/GameRecommendations.swift", "app/Madeira/AutoTuneRules.generated.swift"):
    p = R / gen
    if p.exists():
        check("no line of %s sets it" % gen, '"sampler-cache =' not in p.read_text() and "sampler-cache = " not in p.read_text())

# --- catalog, credit, CI -------------------------------------------------------------
cat = (R / "build/tools/gen-config-catalog.py").read_text()
entry = cat[cat.index('"sampler-cache": {'):] if '"sampler-cache": {' in cat else ""
entry = entry[:entry.index('"sources"')] if entry else ""
check("the settings catalog lists it: Direct3D 12, a bool, on by default", '"category": "Direct3D 12"' in entry and '"kind": "bool"' in entry
      and '"default": "1"' in entry, entry[:300])
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("the credit: THIRD-PARTY-NOTICES names the upstream commits and their author", "`5a3db65`" in notices and "`3557bae`" in notices and "| Shitiz Garg | " in notices)
head = SRC[:3000]
check("...and the file's header carries the Portions line for the code taken", "Shitiz Garg" in head and "codex/d3d12-consolidation" in head)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-sampler-cache.py" in wf)

# --- the whole runtime, for syntax, with the repo's toolchain --------------------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0, r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

sys.exit(0 if ok else 1)
