#!/usr/bin/env python3
"""The opt-in D3DKMT adapter (env.MADEIRA_KMT_ADAPTER = 1) and the [vkmt] trace
(build/win32u-unix/d3dkmt_ios.c, sysparams_ios.c) plus DXGI's UMD version
(tools/patch-dxgi-umd-version.py); no Wine runs.

1. Wiring, read from the files: build.sh compiles d3dkmt_ios.c instead of
   upstream d3dkmt.c; the wrapper renames exactly the entry points it wraps and
   every wrapper calls the upstream function; every changed answer is behind
   madeira_kmt_adapter_enabled(); EnumAdapters2 lists the adapter only with the
   switch and only when `gpus` is empty; the registry GPU keeps a fresh LUID
   and 4096 MB without it.
2. The kmt-test region compiled on the host with the KMTQAITYPE / WDDM enums
   taken from the wine pin's d3dkmthk.h ($WINE_SRC, default ./wine): every
   QueryAdapterInfo answer (values, structure sizes, short buffers, bad
   indices, types left to upstream), the driver version QWORD, the LUID
   (DXMT's bit_cast<LUID>(bswap64(registryID))) and the type names.
3. The switch, the Metal LUID lookup (fake MTLCreateSystemDefaultDevice /
   objc_msgSend resolved through dlsym, as on the phone) and the dedicated size
   (vram-mb from bcd.cfg / the game file, else 4096 MB) compiled and run.
4. The DXGI patch on a copy of dxmt/src/dxgi/dxgi_adapter.cpp ($DXMT_SRC,
   default ./dxmt): applies once, gated by MADEIRA_KMT_ADAPTER, falls back to
   ~0, refuses moved anchors; with an arm64ec llvm-mingw ($MINGW) it compiles.
   build-dxgi-dll.sh patches a copy, keeps the plain link on the pristine file,
   and checks the patch is in the result; the catalog lists the switch off.
"""
from pathlib import Path
import fnmatch, os, re, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
u = root / "build/win32u-unix"
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
dxmt = Path(os.environ.get("DXMT_SRC", root / "dxmt"))
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def func(src, sig):
    """The definition: the last occurrence (prototypes come first)."""
    body = src[src.rindex(sig):]
    return body[:body.index("\n}\n") + 3]


kmt = (u / "d3dkmt_ios.c").read_text()
sp = (u / "sysparams_ios.c").read_text()
build = (u / "build.sh").read_text()

# ---------------------------------------------------------------- 1. wiring
check("build.sh compiles d3dkmt_ios.c for upstream d3dkmt.c",
      re.search(r'\n        d3dkmt\)\n(?:            #[^\n]*\n)*            compile_one "\$BUILD_DIR/d3dkmt_ios.c" "d3dkmt"\n            continue', build)
      is not None)
wrapped = ["OpenAdapterFromLuid", "OpenAdapterFromHdc", "CloseAdapter", "CreateDevice", "DestroyDevice",
           "QueryAdapterInfo", "QueryStatistics", "QueryVideoMemoryInfo", "SetQueuedLimit", "SetVidPnSourceOwner",
           "CheckOcclusion", "CheckVidPnExclusiveOwnership", "Escape"]
inc = kmt.index('#include "../../wine/dlls/win32u/d3dkmt.c"')
renamed = re.findall(r"#define NtGdiDdDDI(\w+)\s+upstream_NtGdiDdDDI(\w+)", kmt[:inc])
check("the wrapper renames exactly the wrapped entry points", sorted(a for a, b in renamed if a == b) == sorted(wrapped)
      and len(renamed) == len(wrapped))
check("and undefines every rename after the include",
      all("#undef NtGdiDdDDI" + n + "\n" in kmt[inc:inc + 1500] for n in wrapped))
for n in wrapped:
    f = func(kmt, "NTSTATUS WINAPI NtGdiDdDDI%s(" % n)
    check("NtGdiDdDDI%s calls upstream_NtGdiDdDDI%s and is traced" % (n, n),
          ("upstream_NtGdiDdDDI%s(" % n) in f and "dprintf( 2, \"[vkmt] tid=%04x" in f)
qai = func(kmt, "NTSTATUS WINAPI NtGdiDdDDIQueryAdapterInfo(")
check("QueryAdapterInfo answers only with the switch, for a real adapter handle, else upstream",
      "if (madeira_kmt_adapter_enabled() && desc && desc->hAdapter && desc->pPrivateDriverData &&\n"
      "        get_d3dkmt_object( desc->hAdapter, D3DKMT_ADAPTER ))" in qai
      and "if (!ours) status = upstream_NtGdiDdDDIQueryAdapterInfo( desc );" in qai)
check("QueryAdapterInfo logs 64 calls and the first call of every type",
      "n <= 64 || first_of_type" in qai and "madeira_kmt_type_name( desc->Type )" in qai)
hdc = func(kmt, "NTSTATUS WINAPI NtGdiDdDDIOpenAdapterFromHdc(")
check("OpenAdapterFromHdc opens the adapter only with the switch, else the upstream stub",
      "if (madeira_kmt_adapter_enabled() && desc && madeira_kmt_adapter_luid( &luid ))" in hdc
      and "else status = upstream_NtGdiDdDDIOpenAdapterFromHdc( desc );" in hdc)
vmi = func(kmt, "NTSTATUS WINAPI NtGdiDdDDIQueryVideoMemoryInfo(")
check("QueryVideoMemoryInfo fills only an empty LOCAL budget, only with the switch",
      "if (!status && madeira_kmt_adapter_enabled() && desc->MemorySegmentGroup == D3DKMT_MEMORY_SEGMENT_GROUP_LOCAL &&\n"
      "        !desc->Budget)" in vmi)
check("the switch is env MADEIRA_KMT_ADAPTER, read once",
      'getenv( "MADEIRA_KMT_ADAPTER" )' in kmt and "static int enabled = -1;" in kmt)
check("madeira_cfg.h comes before Wine's headers (they poison strncpy)",
      kmt.index('#include "../madeira_cfg.h"') < inc)

e2 = func(sp, "NTSTATUS WINAPI NtGdiDdDDIEnumAdapters2(")
check("EnumAdapters2 lists the adapter only with the switch and no GPU in `gpus`",
      "if (!count && madeira_kmt_adapter_enabled() && madeira_kmt_adapter_luid( &kmt_luid ))" in e2
      and "desc->pAdapters[0].AdapterLuid = kmt_luid;" in e2 and "desc->NumAdapters = 1;" in e2
      and e2.index("madeira_kmt_adapter_enabled()") < e2.index("if (count > desc->NumAdapters)"))
check("EnumAdapters2 logs the size query and the result with tid",
      "D3DKMTEnumAdapters2(NULL) -> 0, room for %u adapters" in e2 and "if (logged++ < 8)" in e2)
dn = func(sp, "NTSTATUS WINAPI NtGdiDdDDIOpenAdapterFromDeviceName(")
check("OpenAdapterFromDeviceName opens the registry GPU's interface only with the switch",
      "if (!found && madeira_kmt_adapter_enabled())" in dn and "!strcmp( name + 4, kmt_id.path )" in dn)
gdi = sp[sp.index("static NTSTATUS d3dkmt_open_adapter_from_gdi_display_name("):]
gdi = gdi[:gdi.index("\n}\n")]
# Round 579: the choice moved, unchanged, into ios_virtual_luid_locked (shared with QueryDisplayConfig's virtual path,
# tests/host/check-vdcfg-path.py); the GDI-name function calls it under the display lock.
vl = func(sp, "static void ios_virtual_luid_locked( LUID *out )")
check("OpenAdapterFromGdiDisplayName's virtual adapter takes the LUID only with the switch",
      "if (madeira_kmt_adapter_enabled() && madeira_kmt_adapter_luid( &ios_virtual_adapter_luid ))" in vl
      and "else if (!list_empty( &gpus ) && first)" in vl
      and "pthread_mutex_lock( &display_lock );\n        ios_virtual_luid_locked( &luid_desc.AdapterLuid );" in gdi)
reg = func(sp, "static void ios_register_virtual_gpu(void)")
check("registry GPU: switch LUID, else a fresh one; switch size, else 4096 MB",
      "kmt = madeira_kmt_adapter_enabled() && madeira_kmt_adapter_luid( &gpu.luid );" in reg
      and "if (!kmt) NtAllocateLocallyUniqueId( &gpu.luid );" in reg
      and "ULONGLONG memory = (ULONGLONG)4096 * 1024 * 1024;" in reg   # round 603: a picked card (gpu-card) takes the size too
      and "if (kmt || ios_gpu_pick( &picked_vendor, &picked_device )) memory = madeira_kmt_dedicated_bytes();" in reg
      and "if (kmt)\n    {" in reg and "madeira_kmt_driver_version_qword( driver_vendor_to_version( pci.vendor ) )" in reg)
ident = func(sp, "void madeira_kmt_identity( struct madeira_kmt_identity *id )")
check("identity = the registry GPU (ids, name, DriverVersion, path)",
      "ios_virtual_gpu_ids( &pci.vendor, &pci.device );" in ident and "ios_virtual_gpu_name( pci.vendor, pci.device )" in ident
      and "driver_vendor_to_version( pci.vendor )" in ident
      and 'PCI\\\\VEN_%04X&DEV_%04X&SUBSYS_00000000&REV_00\\\\%08X' in ident)

# ---------------------------------------------------------------- 2. answers
region = kmt[kmt.index("/* kmt-test:begin"):kmt.index("/* kmt-test:end */")]
hdr = wine / "include/ddk/d3dkmthk.h"
BASE = r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int UINT; typedef unsigned int DWORD; typedef int LONG; typedef uint16_t WCHAR;
typedef unsigned long long ULONGLONG; typedef int NTSTATUS;
typedef struct { DWORD LowPart; LONG HighPart; } LUID;
#define STATUS_SUCCESS 0
#define STATUS_INVALID_PARAMETER ((NTSTATUS)0xC000000D)
"""


def cc(src, exe, extra=()):
    c = exe.with_suffix(".c")
    c.write_text(src)
    r = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-I%s" % u, str(c), "-o", str(exe)]
                       + list(extra), capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-3000:])
    return r.returncode == 0


if not hdr.exists():
    print("note: %s not checked out (set WINE_SRC); answer checks skipped" % hdr)
else:
    h = hdr.read_text()
    enums = re.search(r"typedef enum _QAI_DRIVERVERSION\s*\{.*?\}\s*D3DKMT_DRIVERVERSION;", h, re.S).group(0)
    enums += "\n" + re.search(r"typedef enum _KMTQUERYADAPTERINFOTYPE\s*\{.*?\}\s*KMTQUERYADAPTERINFOTYPE;", h, re.S).group(0)
    harness = BASE + enums + '\n#include "madeira_kmt.h"\n' + region + r"""
static unsigned char buf[16384];
static struct madeira_kmt_identity id = { 0x10de, 0x2544, "NVIDIA GeForce RTX 3060", "35.0.15.6094",
    "PCI\\VEN_10DE&DEV_2544&SUBSYS_00000000&REV_00\\00000000", 4096ull << 20 };
static void ascii( const WCHAR *w, char *out, int n ) { int i; for (i = 0; i < n - 1 && w[i]; i++) out[i] = (char)w[i]; out[i] = 0; }
static void q( const char *tag, UINT type, UINT size, UINT in )
{
    NTSTATUS st = 0; int a; unsigned long long v = 0;
    memset( buf, 0xcc, sizeof(buf) );
    memcpy( buf, &in, sizeof(in) );
    a = madeira_kmt_answer( type, buf, size, &id, &st );
    memcpy( &v, buf, size >= 8 ? 8 : 4 );
    if (size < 8) v &= 0xffffffffull;
    printf( "%s %d %x %llx\n", tag, a, (unsigned)st, v );
}
int main(void)
{
    char s1[64], s2[64], s3[64], s4[64];
    LUID l;
    q( "drv", KMTQAITYPE_DRIVERVERSION, 4, 0 );
    q( "drv_render", KMTQAITYPE_DRIVERVERSION_RENDER, 4, 0 );
    q( "drv_short", KMTQAITYPE_DRIVERVERSION, 2, 0 );
    q( "umd", KMTQAITYPE_UMD_DRIVER_VERSION, 8, 0 );
    q( "umd_short", KMTQAITYPE_UMD_DRIVER_VERSION, 4, 0 );
    q( "kmd", KMTQAITYPE_KMD_DRIVER_VERSION, 8, 0 );
    q( "type", KMTQAITYPE_ADAPTERTYPE, 4, 0 );
    q( "type_render", KMTQAITYPE_ADAPTERTYPE_RENDER, 4, 0 );
    q( "physcount", KMTQAITYPE_PHYSICALADAPTERCOUNT, 4, 0 );
    q( "w12", KMTQAITYPE_WDDM_1_2_CAPS, 12, 0 );
    q( "w13", KMTQAITYPE_WDDM_1_3_CAPS, 4, 0 );
    q( "w20", KMTQAITYPE_WDDM_2_0_CAPS, 4, 0 );
    q( "w27", KMTQAITYPE_WDDM_2_7_CAPS, 4, 0 );
    q( "w29", KMTQAITYPE_WDDM_2_9_CAPS, 4, 0 );
    q( "w30", KMTQAITYPE_WDDM_3_0_CAPS, 4, 0 );
    q( "w31", KMTQAITYPE_WDDM_3_1_CAPS, 4, 0 );
    q( "addr", KMTQAITYPE_ADAPTERADDRESS, 12, 0 );
    q( "ids", KMTQAITYPE_PHYSICALADAPTERDEVICEIDS, 28, 0 );
    { UINT *p = (UINT *)buf; printf( "ids_fields %x %x %u %u %u %u\n", p[1], p[2], p[3], p[4], p[5], p[6] ); }
    q( "ids_index1", KMTQAITYPE_PHYSICALADAPTERDEVICEIDS, 28, 1 );
    q( "desc", KMTQAITYPE_DRIVER_DESCRIPTION, 8192, 0 );
    ascii( (WCHAR *)buf, s1, sizeof(s1) ); printf( "desc_str %s|%d\n", s1, ((WCHAR *)buf)[4095] );
    q( "desc_short", KMTQAITYPE_DRIVER_DESCRIPTION, 8190, 0 );
    q( "reg", KMTQAITYPE_ADAPTERREGISTRYINFO, 2080, 0 );
    ascii( (WCHAR *)buf, s1, 64 ); ascii( (WCHAR *)buf + 260, s2, 64 ); ascii( (WCHAR *)buf + 520, s3, 64 );
    ascii( (WCHAR *)buf + 780, s4, 64 ); printf( "reg_str %s|%s|%s|%s\n", s1, s2, s3, s4 );
    q( "node0", KMTQAITYPE_NODEMETADATA, 80, 0 );
    { UINT *p = (UINT *)buf; ascii( (WCHAR *)(buf + 8), s1, 32 ); printf( "node0_fields %u %u %s %u %u %u\n", p[0], p[1], s1, p[18], buf[76], buf[77] ); }
    q( "node1", KMTQAITYPE_NODEMETADATA, 80, 1 );
    { UINT *p = (UINT *)buf; ascii( (WCHAR *)(buf + 8), s1, 32 ); printf( "node1_fields %u %u %s\n", p[0], p[1], s1 ); }
    q( "node2", KMTQAITYPE_NODEMETADATA, 80, 2 );
    q( "node_adapter1", KMTQAITYPE_NODEMETADATA, 80, 0x10000 );
    q( "seg", KMTQAITYPE_GETSEGMENTSIZE, 24, 0 );
    { ULONGLONG *p = (ULONGLONG *)buf; printf( "seg_fields %llu %llu %llu\n", p[0], p[1], p[2] ); }
    q( "group", KMTQAITYPE_GETSEGMENTGROUPSIZE, 56, 0 );
    { ULONGLONG *p = (ULONGLONG *)(buf + 8); printf( "group_fields %llu %llu %llu %llu %llu %llu\n", p[0], p[1], p[2], p[3], p[4], p[5] ); }
    q( "group_index1", KMTQAITYPE_GETSEGMENTGROUPSIZE, 56, 1 );
    q( "umdname", KMTQAITYPE_UMDRIVERNAME, 524, 0 );
    q( "checkupdate", KMTQAITYPE_CHECKDRIVERUPDATESTATUS, 4, 0 );
    q( "guid", KMTQAITYPE_ADAPTERGUID, 16, 0 );
    q( "perfdata", KMTQAITYPE_ADAPTERPERFDATA, 64, 0 );
    q( "perfcaps", KMTQAITYPE_ADAPTERPERFDATA_CAPS, 40, 0 );
    q( "perfcaps_short", KMTQAITYPE_ADAPTERPERFDATA_CAPS, 32, 0 );
    printf( "qword %llx %llx %llx %llx\n", madeira_kmt_driver_version_qword( "35.0.15.6094" ),
            madeira_kmt_driver_version_qword( "32.0.15.6094" ), madeira_kmt_driver_version_qword( "1.2" ),
            madeira_kmt_driver_version_qword( "99999.1.2.3" ) );
    madeira_kmt_luid_from_registry_id( 0x0000000100000a2bull, &l );
    printf( "luid %08x %08x\n", (unsigned)l.HighPart, (unsigned)l.LowPart );
    printf( "names %u=%s %u=%s %u=%s %u=%s %u=%s %s\n", KMTQAITYPE_DRIVERVERSION, madeira_kmt_type_name( KMTQAITYPE_DRIVERVERSION ),
            KMTQAITYPE_UMD_DRIVER_VERSION, madeira_kmt_type_name( KMTQAITYPE_UMD_DRIVER_VERSION ),
            KMTQAITYPE_WDDM_2_7_CAPS, madeira_kmt_type_name( KMTQAITYPE_WDDM_2_7_CAPS ),
            KMTQUITYPE_GPUVERSION, madeira_kmt_type_name( KMTQUITYPE_GPUVERSION ),
            KMTQAITYPE_WDDM_3_1_CAPS, madeira_kmt_type_name( KMTQAITYPE_WDDM_3_1_CAPS ), madeira_kmt_type_name( 500 ) );
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as t:
        exe = Path(t) / "answers"
        built = cc(harness, exe, ["-fsanitize=address,undefined"])
        check("kmt-test region compiles on the host (-Wall, ASan/UBSan)", built)
        if built:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check("answer program runs clean", r.returncode == 0 and not r.stderr)
            out = {}
            for line in r.stdout.splitlines():
                k, _, v = line.partition(" ")
                out[k] = v
            ver = (35 << 48) | (0 << 32) | (15 << 16) | 6094
            inv = "%x" % 0xC000000D
            check("DRIVERVERSION(_RENDER) = WDDM 3.1 (3100)", out["drv"] == "1 0 c1c" and out["drv_render"] == "1 0 c1c")
            check("UMD / KMD driver version = 35.0.15.6094 as a<<48|b<<32|c<<16|d",
                  out["umd"] == "1 0 %x" % ver and out["kmd"] == "1 0 %x" % ver)
            check("short buffers: STATUS_INVALID_PARAMETER",
                  out["drv_short"].startswith("1 " + inv) and out["umd_short"].startswith("1 " + inv)
                  and out["desc_short"].startswith("1 " + inv))
            check("ADAPTERTYPE(_RENDER) = RenderSupported|DisplaySupported, not software",
                  out["type"] == "1 0 3" and out["type_render"] == "1 0 3")
            check("one physical adapter", out["physcount"] == "1 0 1")
            check("WDDM caps: 1.3 = 0, 2.0 = 64-bit atomics|GpuMmu, 2.7 = HwSch supported|enabled|default, "
                  "2.9 = HwSch stable|enabled, 3.0 / 3.1 = 0",
                  out["w13"] == "1 0 0" and out["w20"] == "1 0 3" and out["w27"] == "1 0 7" and out["w29"] == "1 0 6"
                  and out["w30"] == "1 0 0" and out["w31"] == "1 0 0")
            check("WDDM 1.2 caps: DMA-buffer preemption (100/100), flags 0x1d7",
                  out["w12"] == "1 0 %x" % (100 | (100 << 32)))
            check("ADAPTERADDRESS = bus 0, device 0, function 0", out["addr"] == "1 0 0")
            check("PHYSICALADAPTERDEVICEIDS = 10de:2544, subsys / rev 0, PCI bus", out["ids"].startswith("1 0 ")
                  and out["ids_fields"] == "10de 2544 0 0 0 5")
            check("PHYSICALADAPTERDEVICEIDS for adapter 1: invalid", out["ids_index1"].startswith("1 " + inv))
            check("DRIVER_DESCRIPTION = NVIDIA GeForce RTX 3060, NUL-terminated",
                  out["desc"].startswith("1 0 ") and out["desc_str"] == "NVIDIA GeForce RTX 3060|0")
            check("ADAPTERREGISTRYINFO = name / name / Integrated RAMDAC / name",
                  out["reg"].startswith("1 0 ") and out["reg_str"] ==
                  "NVIDIA GeForce RTX 3060|NVIDIA GeForce RTX 3060|Integrated RAMDAC|NVIDIA GeForce RTX 3060")
            check("NODEMETADATA node 0 = 3D engine, GPU MMU", out["node0"].startswith("1 0 ")
                  and out["node0_fields"] == "0 1 3D 0 1 0")
            check("NODEMETADATA node 1 = copy engine", out["node1_fields"] == "1 6 Copy")
            check("NODEMETADATA node 2 / adapter 1: invalid",
                  out["node2"].startswith("1 " + inv) and out["node_adapter1"].startswith("1 " + inv))
            mb = 4096 << 20
            check("GETSEGMENTSIZE = dedicated 4096 MB only", out["seg_fields"] == "%d 0 0" % mb)
            check("GETSEGMENTGROUPSIZE = legacy + local 4096 MB, nothing non-local",
                  out["group_fields"] == "%d 0 0 %d 0 0" % (mb, mb) and out["group_index1"].startswith("1 " + inv))
            check("UMDRIVERNAME, CHECKDRIVERUPDATESTATUS, ADAPTERGUID stay upstream's",
                  all(out[k].startswith("0 ") for k in ("umdname", "checkupdate", "guid")))
            check("ADAPTERPERFDATA and ADAPTERPERFDATA_CAPS (40-byte caller) answered; a short caps buffer refused",
                  out["perfdata"].startswith("1 0") and out["perfcaps"].startswith("1 0")
                  and out["perfcaps_short"].startswith("1 " + inv))
            check("version QWORD: 32.0.15.6094, short and oversized strings",
                  out["qword"] == "%x %x %x %x" % (ver, (32 << 48) | (15 << 16) | 6094, (1 << 48) | (2 << 32),
                                                   (0xffff << 48) | (1 << 32) | (2 << 16) | 3))
            rid = 0x0000000100000a2b
            v = int.from_bytes(rid.to_bytes(8, "little"), "big")   # bswap64
            check("LUID = bit_cast<LUID>(bswap64(registryID)) as DXMT", out["luid"] == "%08x %08x" % (v >> 32, v & 0xffffffff))
            check("type names follow the header's values",
                  out["names"] == "13=DRIVERVERSION 18=UMD_DRIVER_VERSION 70=WDDM_2_7_CAPS 64=GPUVERSION 80=WDDM_3_1_CAPS ?")

# ---------------------------------------------------------------- 3. switch, Metal LUID, dedicated size
sw = kmt[kmt.index("int madeira_kmt_adapter_enabled(void)"):kmt.index("/* first 16 calls, failures up to the 64th */")]
harness3 = r"""
#include <dlfcn.h>
#include <pthread.h>
#include <strings.h>
#include "../madeira_cfg.h"
""" + BASE + r"""
enum { KMTQAITYPE_DUMMY };
#include "madeira_kmt.h"
static void madeira_kmt_luid_from_registry_id( unsigned long long id, LUID *luid )
{ unsigned long long v = __builtin_bswap64( id ); luid->LowPart = (DWORD)v; luid->HighPart = (LONG)(v >> 32); }
#ifdef FAKE_METAL
static int fake_device, created, released;
__attribute__((visibility("default"))) void *MTLCreateSystemDefaultDevice(void) { created++; return &fake_device; }
__attribute__((visibility("default"))) void *sel_registerName( const char *n ) { return (void *)n; }
__attribute__((visibility("default"))) unsigned long long objc_msgSend( void *self, void *sel )
{
    if (self != &fake_device) abort();
    if (!strcmp( sel, "registryID" )) return 0x0000000100000a2bull;
    if (!strcmp( sel, "release" )) released++;
    return 0;
}
#endif
""" + sw + r"""
int main(void)
{
    LUID l = {0, 0};
    int on = madeira_kmt_adapter_enabled(), got = madeira_kmt_adapter_luid( &l ), again = madeira_kmt_adapter_luid( &l );
#ifdef FAKE_METAL
    printf( "on=%d luid=%d/%d %08x:%08x created=%d released=%d mb=%llu\n", on, got, again, (unsigned)l.HighPart,
            (unsigned)l.LowPart, created, released, madeira_kmt_dedicated_bytes() >> 20 );
#else
    printf( "on=%d luid=%d/%d mb=%llu\n", on, got, again, madeira_kmt_dedicated_bytes() >> 20 );
#endif
    return 0;
}
"""
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    fake, nometal = t / "fake", t / "nometal"
    b1 = cc(harness3, fake, ["-DFAKE_METAL", "-rdynamic", "-pthread", "-ldl"])
    b2 = cc(harness3, nometal, ["-pthread", "-ldl"])
    check("switch / LUID / dedicated-size code compiles on the host", b1 and b2)
    if b1 and b2:
        docs = t / "docs"
        docs.mkdir()

        def run(exe, env, cfg=None, game=None):
            e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
            e["MADEIRA_DOCS_DIR"] = str(docs)
            if cfg is None:
                (docs / "bcd.cfg").unlink(missing_ok=True)
            else:
                (docs / "bcd.cfg").write_text(cfg)
            if game is not None:
                (t / "game.cfg").write_text(game)
                e["MADEIRA_CFG_GAME"] = str(t / "game.cfg")
            e.update(env)
            r = subprocess.run([str(exe)], capture_output=True, text=True, env=e)
            return r.stdout.strip(), r.stderr
        out, err = run(fake, {})
        check("off by default: no line, LUID still computable", out.startswith("on=0 luid=1/1") and "kmt-adapter=1" not in err)
        for v in ("1", "on", "TRUE", "yes"):
            out, err = run(fake, {"MADEIRA_KMT_ADAPTER": v})
            check("MADEIRA_KMT_ADAPTER=%s turns it on and logs once" % v,
                  out.startswith("on=1") and err.count("[vkmt] madeira-bcd kmt-adapter=1") == 1)
        out, _ = run(fake, {"MADEIRA_KMT_ADAPTER": "0"})
        check("MADEIRA_KMT_ADAPTER=0 stays off", out.startswith("on=0"))
        rid = 0x0000000100000a2b
        v = int.from_bytes(rid.to_bytes(8, "little"), "big")
        out, err = run(fake, {"MADEIRA_KMT_ADAPTER": "1"})
        check("LUID from MTLCreateSystemDefaultDevice().registryID via dlsym, once, device released",
              "luid=1/1 %08x:%08x created=1 released=1" % (v >> 32, v & 0xffffffff) in out
              and err.count("[vkmt] adapter LUID") == 1)
        out, err = run(nometal, {"MADEIRA_KMT_ADAPTER": "1"})
        check("no Metal: no LUID, logged, no adapter", "luid=0/0" in out and "no Metal device registryID" in err)
        out, _ = run(fake, {}, cfg=None)
        check("dedicated: 4096 MB without vram-mb", out.endswith("mb=4096"))
        out, _ = run(fake, {}, cfg="vram-mb = 3072\n")
        check("dedicated: vram-mb from bcd.cfg", out.endswith("mb=3072"))
        out, _ = run(fake, {}, cfg="vram-mb = 3072\n", game="vram-mb = 4096\n")
        check("dedicated: the game file wins", out.endswith("mb=4096"))
        out, _ = run(fake, {}, cfg="vram-mb = 100\n")
        check("dedicated: below 256 MB ignored, as winemetal", out.endswith("mb=4096"))

# ---------------------------------------------------------------- 4. DXGI
adapter = dxmt / "src/dxgi/dxgi_adapter.cpp"


def patch(path):
    r = subprocess.run([sys.executable, str(root / "tools/patch-dxgi-umd-version.py"), str(path)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


if adapter.exists() and "UINT64 GetUmdDriverVersion()" in adapter.read_text():
    # willfaust/dxmt#13 (dxmt db546ee) carries the UMD version lookup itself; the
    # fork's patch is then a no-op. It reports what the D3DKMT adapter answers and
    # ~0 without one -- with MADEIRA_KMT_ADAPTER unset win32u has no Madeira adapter
    # to answer, so other games still see ~0 as before.
    up = adapter.read_text()
    cis = up[up.index("CheckInterfaceSupport(const GUID &guid, LARGE_INTEGER *umd_version) final {"):]
    cis = cis[:cis.index("    return hr;\n  }")]
    check("upstream CheckInterfaceSupport takes the version from the adapter's own KMT handle",
          "umd_version->QuadPart = GetUmdDriverVersion();" in cis)
    helper = up[up.index("UINT64 GetUmdDriverVersion() {"):]
    helper = helper[:helper.index("\n  }\n")]
    check("upstream: ~0 without a KMT handle or a version", "return ~0ull;" in helper and "local_kmt_" in helper)
    check("upstream asks KMTQAITYPE_UMD_DRIVER_VERSION", "KMTQAITYPE_UMD_DRIVER_VERSION" in helper)
    with tempfile.TemporaryDirectory() as t:
        cpp = Path(t) / "dxgi_adapter.cpp"
        shutil.copy(adapter, cpp)
        rc, out = patch(cpp)
        check("fork patch is a no-op on the upstream lookup", rc == 0 and cpp.read_text() == up)
elif not adapter.exists():
    print("note: %s not checked out (set DXMT_SRC); DXGI patch checks skipped" % adapter)
else:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        cpp = t / "dxgi_adapter.cpp"
        shutil.copy(adapter, cpp)
        rc, out = patch(cpp)
        check("DXGI patch applies", rc == 0 and "UMD version from D3DKMT" in out)
        src = cpp.read_text()
        once = src
        rc, out = patch(cpp)
        check("second run: already patched, unchanged", rc == 0 and "already patched" in out and cpp.read_text() == once)
        cis = src[src.index("CheckInterfaceSupport(const GUID &guid, LARGE_INTEGER *umd_version) final {"):]
        cis = cis[:cis.index("    return hr;\n  }")]
        check("CheckInterfaceSupport takes the version from the adapter's own KMT handle",
              "umd_version->QuadPart = madeira_dxgi_umd_version(local_kmt_);" in cis and "QuadPart = ~0ull;" not in cis)
        check("CheckInterfaceSupport logs its first 4 calls", "madeira_calls.fetch_add(1) < 4" in cis)
        helper = src[src.index("static UINT64 madeira_dxgi_umd_version("):]
        helper = helper[:helper.index("\n}\n")]
        check("without MADEIRA_KMT_ADAPTER, or on failure, ~0 as upstream",
              "if (!madeira_kmt_adapter_switch())\n    return ~0ull;" in helper
              and "return (status == 0 && version) ? version : ~0ull;" in helper
              and 'GetEnvironmentVariableA("MADEIRA_KMT_ADAPTER"' in src)
        check("asks KMTQAITYPE_UMD_DRIVER_VERSION (18) with an 8-byte buffer",
              "{kmt, 18 /* KMTQAITYPE_UMD_DRIVER_VERSION */,\n                                          &version, sizeof(version)}" in helper)
        if hdr.exists():
            check("18 is KMTQAITYPE_UMD_DRIVER_VERSION in the wine header",
                  re.search(r"KMTQAITYPE_ADAPTERTYPE = 15,\s*KMTQAITYPE_OUTPUTDUPLCONTEXTSCOUNT,\s*KMTQAITYPE_WDDM_1_2_CAPS,"
                            r"\s*KMTQAITYPE_UMD_DRIVER_VERSION,", hdr.read_text()) is not None)
        bad = t / "moved.cpp"
        bad.write_text(adapter.read_text().replace("umd_version->QuadPart = ~0ull;", "umd_version->QuadPart = 0;"))
        before = bad.read_text()
        rc, out = patch(bad)
        check("moved anchor: exit 1, file untouched, anchor named", rc == 1 and bad.read_text() == before and "anchor for" in out)
        mingw = os.environ.get("MINGW")
        cxx = Path(mingw) / "arm64ec-w64-mingw32-clang++" if mingw else shutil.which("arm64ec-w64-mingw32-clang++")
        if not cxx or not Path(cxx).exists():
            print("note: no arm64ec llvm-mingw ($MINGW); compile step skipped")
        else:
            g = dxmt / "src/dxgi"
            r = subprocess.run([str(cxx), "-std=c++20", "-O2", "-c", "-o", str(t / "a.o"), str(cpp),
                                "-I%s" % g, "-I%s" % (dxmt / "src/dxmt"), "-I%s" % (dxmt / "src/util"),
                                "-I%s" % (dxmt / "src/winemetal"), "-I%s" % (dxmt / "src/airconv"),
                                "-I%s" % (dxmt / "include"), "-I%s" % (dxmt / "libs"), "-DNOMINMAX",
                                "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1", "-DDXMT_PAGE_SIZE=4096", "-fblocks",
                                "-Wno-microsoft-exception-spec"], capture_output=True, text=True)
            check("patched dxgi_adapter.cpp compiles for arm64ec", r.returncode == 0 and (t / "a.o").exists())
            if r.returncode:
                print(r.stderr[-2000:])

sh = (root / "tools/build-dxgi-dll.sh").read_text()
check("build-dxgi-dll.sh patches a copy of dxgi_adapter.cpp",
      'cp "$G/dxgi_adapter.cpp" "$OUT/src/dxgi_adapter.cpp"' in sh
      and 'tools/patch-dxgi-umd-version.py" "$OUT/src/dxgi_adapter.cpp"' in sh)
check("the plain (recipe) link keeps the pristine adapter, the shipped one the patched",
      'compile "$G/dxgi_adapter.cpp" "$OUT/obj/dxgi_adapter_plain.o"' in sh
      and 'link_dll "$OUT/obj/dxgi_factory_plain.o" "$OUT/plain/dxgi.dll" "$OUT/obj/dxgi_adapter_plain.o"' in sh
      and 'link_dll "$OUT/obj/dxgi_factory.o" "$OUT/dxgi.dll" "$OUT/obj/dxgi_adapter.o"' in sh)
check("build-dxgi-dll.sh fails (warns) when the patch is not in the result",
      'grep -aq "CheckInterfaceSupport: UMD version" "$OUT/dxgi.dll" || fail' in sh)
check("the new script stays out of the i386 farm's tools/patch-dxmt-*.py cache key",
      not fnmatch.fnmatch("tools/patch-dxgi-umd-version.py", "tools/patch-dxmt-*.py"))
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("catalog lists env.MADEIRA_KMT_ADAPTER, off by default",
      re.search(r'key: "env.MADEIRA_KMT_ADAPTER".*kind: \.bool, defaultValue: "0"', cat) is not None)
check("catalog keeps vram-mb in DXMT with its note",
      re.search(r'key: "vram-mb".*category: "Direct3D 9/10/11 \(DXMT\)", note: "ml1095: bcd.cfg vram-mb = N[."]', cat)   # round 660 (M117): the note goes on
      is not None)

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
