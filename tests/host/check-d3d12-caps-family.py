#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_D3D12_CAPS_BY_FAMILY = 1 (opt-in): D3D12 capability answers by GPU family.

The D3D12 runtime claims 64-bit atomics (OPTIONS9, OPTIONS11) and wave operations (OPTIONS1) on
every GPU; Metal has 64-bit atomics on Apple9 (A17 Pro, A18, M3 and later; Macs) and the SIMD-group
reductions wave operations need from Apple7 (A14, M1) (gameplay.md section 2.3 of the iOS 18
audit). With the switch the answers follow the conversion target's family.

Compiles the runtime's own code (the family values, mad_resolve_target, the two family helpers,
the switch) against a stubbed device and checks:
  - the switch: unset, "0" or anything but "1" is off; "1" is on;
  - per family: Apple6 (A13) no wave operations and no 64-bit atomics; Apple7 and Apple8 wave
    operations only; Apple9 and later and a Mac both;
and, from the source, that CheckFeatureSupport changes an answer only under the switch, only to
FALSE, and leaves everything else as it was (WaveOps still from mad_wave_ops_answer, so
d3d12-wave-ops = 0 keeps working). The catalog lists the switch, off by default; CI runs this.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
catalog = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def case(name):
    a = src.index(f"case {name}: {{")
    return src[a:src.index("\n    }\n", a)]


o1, o9, o11 = case("D3D12_FEATURE_D3D12_OPTIONS1"), case("D3D12_FEATURE_D3D12_OPTIONS9"), case("D3D12_FEATURE_D3D12_OPTIONS11")
check("OPTIONS1: WaveOps still comes from mad_wave_ops_answer; the switch can only turn it off",
      "o->WaveOps = mad_wave_ops_answer();" in o1
      and "if (o->WaveOps && mad_caps_by_family() && !mad_family_wave_ops((struct mad_device *)This))\n            o->WaveOps = FALSE;" in o1
      and o1.count("o->WaveOps") == 3)
check("OPTIONS9: under the switch and below Apple9 the all-zero answer, else TRUE as before",
      "if (mad_caps_by_family() && !mad_family_atomics64((struct mad_device *)This))\n            return S_OK;" in o9
      and o9.index("memset(o, 0, sizeof *o);") < o9.index("mad_caps_by_family()") < o9.index("o->AtomicInt64OnTypedResourceSupported = TRUE;"))
check("OPTIONS11: TRUE as before, FALSE only under the switch below Apple9",
      o11.index("o->AtomicInt64OnDescriptorHeapResourceSupported = TRUE;")
      < o11.index("if (mad_caps_by_family() && !mad_family_atomics64((struct mad_device *)This))"))
check("the catalog lists env.MADEIRA_D3D12_CAPS_BY_FAMILY, off by default",
      re.search(r'ConfigOption\(key: "env\.MADEIRA_D3D12_CAPS_BY_FAMILY", [^\n]*kind: \.bool, defaultValue: "0"', catalog) is not None)
check("CI runs this check", "run: python3 tests/host/check-d3d12-caps-family.py" in workflow)

switch = src[src.index("static int mad_caps_by_family(void) {"):]
switch = switch[:switch.index("\n}\n") + 3]
families = src[src.index("#define MAD_IR_FAMILY_APPLE6  1006"):]
families = families[:families.index("return g_target.family >= MAD_IR_FAMILY_APPLE9;\n}\n") + len("return g_target.family >= MAD_IR_FAMILY_APPLE9;\n}\n")]
harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef uint32_t DWORD;
#define MADEIRA_IR_OS_MACOS 2
#define MADEIRA_IR_OS_IOS 1
#define d3d12_log(...) do { } while (0)
struct mad_device { int mtl_device; };
static int gpu_family;   /* 1006..1010 = Apple6..Apple10, 2002 = a Mac */
static int MTLDevice_supportsFamily(int dev, int f)
{ (void)dev; return gpu_family == 2002 ? f == 2002 : (f < 2000 && f <= gpu_family); }
static DWORD GetEnvironmentVariableA(const char *n, char *buf, DWORD size)
{ const char *v = getenv(n); if (!v) return 0; snprintf(buf, size, "%s", v); return (DWORD)strlen(v); }
/* build 646: the runtime's config reader, answered here by the harness (dxil-target-latest only) */
static int cfg_latest;
static long long mad_cfg_int_pe(const char *k, long long d)
{ return (k && !strcmp(k, "dxil-target-latest")) ? cfg_latest : d; }
""" + switch + families + r"""
int main(int argc, char **argv)
{
    static const struct { int family; int wave, atomics; const char *gpu; } table[] = {
        { 1006, 0, 0, "Apple6 (A13)" }, { 1007, 1, 0, "Apple7 (A14, M1)" }, { 1008, 1, 0, "Apple8 (A15, A16, M2)" },
        { 1009, 1, 1, "Apple9 (A17 Pro, A18, M3, M4)" }, { 1010, 1, 1, "Apple10 (A19, M5)" }, { 2002, 1, 1, "Mac" },
    };
    struct mad_device d = { 0 };
    int fails = 0;
    unsigned i;
    printf("switch=%d\n", mad_caps_by_family());
    for (i = 0; i < sizeof(table) / sizeof(table[0]); i++) {
        memset(&g_target, 0, sizeof(g_target));
        gpu_family = table[i].family;
        if (mad_family_wave_ops(&d) != table[i].wave || mad_family_atomics64(&d) != table[i].atomics) {
            printf("FAIL %s: wave %d atomics %d\n", table[i].gpu, mad_family_wave_ops(&d), mad_family_atomics64(&d));
            fails++;
        }
    }
    /* build 646, dxil-target-latest: off keeps Apple9 / iOS 18.0 on an Apple10 phone; on gives Apple10 / iOS 26.0, and an Apple9 phone
     * keeps its family with iOS 26.0 */
    {
        static const struct { int latest, gpu; unsigned want; const char *ver; } t2[] = {
            { 0, 1010, 1009, "18.0" }, { 1, 1010, 1010, "26.0" }, { 1, 1009, 1009, "26.0" }, { 1, 2002, 5001, "15.0" },
        };
        for (i = 0; i < sizeof(t2) / sizeof(t2[0]); i++) {
            memset(&g_target, 0, sizeof(g_target));
            cfg_latest = t2[i].latest; gpu_family = t2[i].gpu;
            mad_resolve_target(&d);
            if (g_target.family != t2[i].want || strcmp(g_target.os_version, t2[i].ver)) {
                printf("FAIL dxil-target-latest=%d on %d: family %u iOS %s\n", t2[i].latest, t2[i].gpu, g_target.family, g_target.os_version);
                fails++;
            }
        }
        cfg_latest = 0;
    }
    puts(fails ? "FAILED" : "ok");
    return fails != 0;
}
"""
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the family harness")
else:
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / "caps.c"
        c.write_text(harness)
        exe = Path(t) / "caps"
        b = subprocess.run([cc, "-std=c11", "-Wall", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the runtime's family code compiles against a stubbed device", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            def run(value):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_D3D12_CAPS_BY_FAMILY"}
                if value is not None:
                    env["MADEIRA_D3D12_CAPS_BY_FAMILY"] = value
                return subprocess.run([str(exe)], capture_output=True, text=True, env=env, timeout=60).stdout
            outs = {v: run(v) for v in (None, "0", "10", "1")}
            check("the switch: unset, 0 and 10 are off; 1 is on",
                  all("switch=0" in outs[v] for v in (None, "0", "10")) and "switch=1" in outs["1"])
            check("per family: Apple6 neither; Apple7/8 wave operations only; Apple9 and later and a Mac both",
                  all(o.strip().endswith("ok") for o in outs.values()))
            if not all(o.strip().endswith("ok") for o in outs.values()):
                print(outs["1"])

print("PASS: D3D12 caps by family" if ok else "FAILED: D3D12 caps by family")
sys.exit(0 if ok else 1)
