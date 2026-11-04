#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_X64_NOCOPY_DATAIMP (opt-in, round 626, agent A2 S7; Poppy Playtime's launcher 6DS3FK build 614, WC6QN0 build 611).

ml1244/ml1245 bind a pure x64 importer's IAT slots for DATA exports of ARM64EC DLLs (msvcp140's std::cout) to the copy the DLL really
writes. A pure x64 image run without a pool copy (VPROT_X64DATA: MADEIRA_X64_IMAGE_NOCOPY or the NOCOPY fallback) is in no
ios_jit_mappings[] entry, so ios_x64_iat_bind_live_data returned at once and the launcher read std::cout's PE copy (NULL-class fault
at 0x4). With the key, build/ntdll-unix/virtual_ios.c notes such images at mapping, forgets them at delete_view, and the binder finds
them; off (the default) nothing is noted and the binder returns as before.

Checks: the switch (exactly "1"), the two marking sites and delete_view call the table only with the key on, the binder's early return
is unchanged for the key off; the real table and binder, cut from virtual_ios.c and compiled on the host (macOS, real mach_vm_region)
against a fake exporter and importer: key off = no slot changes even for a noted importer; key on = the data slot of a hybrid exporter
is rewritten to the live address, a slot into its code, into a non-hybrid module or outside every module is not; an importer that was
never noted, or was forgotten, is not touched; a copy-mode importer (in ios_jit_mappings) binds with the key off as before; the list
policy, catalog, row, rule and fixture; CI.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def cut(sig):
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


table = SRC[SRC.index("#define IOS_X64_NOCOPY_MAX 128"):]
table = table[:table.index("/* ml1244: bind an x64 importer's DATA imports to the copy that is actually live.")]
binder = cut("static void ios_x64_iat_bind_live_data( char *base, size_t size )")
check("the switch is exactly \"1\", read once", 'const char *s = getenv( "MADEIRA_X64_NOCOPY_DATAIMP" );' in table
      and "cached = (s && s[0] == '1' && !s[1]) ? 1 : 0;" in table)
check("both marking sites note the image only with the key on",
      SRC.count("if (ios_x64_nocopy_dataimp_enabled()) ios_x64_nocopy_note( (uintptr_t)ptr, total_size );") == 2
      and SRC.count("ios_x64_nocopy_note(") == 3)
dv = cut("static void delete_view( struct file_view *view )")
check("delete_view forgets a no-copy image (key on only)",
      "if ((view->protect & VPROT_X64DATA) && ios_x64_nocopy_dataimp_enabled()) ios_x64_nocopy_forget( (uintptr_t)view->base );" in dv)
check("the binder: a mapped importer exactly as before; an unmapped one only through the key and the table",
      "else if (ios_x64_nocopy_dataimp_enabled() && ios_x64_nocopy_find( rs, re, &pe, &pe_size )) nocopy = 1;\n    else return;" in binder
      and "if (ios_jit_mapping_machine( imp ) != 0x8664 || ios_jit_mapping_is_hybrid( imp )) return;" in binder
      and "|| (size_t)lfanew + sizeof(nt) > pe_size" in binder and "ios_jit_mappings[imp]" in binder)

HARNESS = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef struct { uint16_t e_magic; uint16_t e_pad[29]; int32_t e_lfanew; } IMAGE_DOS_HEADER;
typedef struct { uint32_t VirtualAddress, Size; } IMAGE_DATA_DIRECTORY;
typedef struct { uint16_t Magic; uint8_t pad[106]; uint32_t NumberOfRvaAndSizes; IMAGE_DATA_DIRECTORY DataDirectory[16]; } IMAGE_OPTIONAL_HEADER64;
typedef struct { uint32_t Signature; uint8_t FileHeader[20]; IMAGE_OPTIONAL_HEADER64 OptionalHeader; } IMAGE_NT_HEADERS64;
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define IMAGE_DIRECTORY_ENTRY_IAT 12
static int g_bound_lines;
static int test_dprintf( int fd, const char *fmt, ... ) { (void)fd; if (strstr( fmt, "ml1245 %s slot %p" )) g_bound_lines++; return 0; }   /* one per bound slot */
#define dprintf test_dprintf
/* the fake module table: 0 = a hybrid (ARM64EC) exporter, 1 = a non-hybrid x64 module, 2 = a copy-mode x64 importer */
static struct { void *pe_base; size_t size; } ios_jit_mappings[8];
static int ios_jit_mapping_count;
static int fake_machine[8] = { 0x8664, 0x8664, 0x8664 }, fake_hybrid[8] = { 1, 0, 0 };
static int ios_jit_mapping_machine( int i ) { return fake_machine[i]; }
static int ios_jit_mapping_is_hybrid( int i ) { return fake_hybrid[i]; }
static int ios_safe_read( uintptr_t a, void *buf, size_t n ) { memcpy( buf, (void *)a, n ); return 1; }
static void *ios_jit_current_peb( void ) { return NULL; }
#define CODE_END 0x1000
static int ios_img_off_is_exec( void *pe, size_t size, uintptr_t off ) { (void)pe; (void)size; return off < CODE_END; }
#define LIVE_DELTA 0x100000000ull
static void *ios_jit_translate_addr_for_owner( void *addr, void *owner )
{
    uintptr_t a = (uintptr_t)addr, b = (uintptr_t)ios_jit_mappings[0].pe_base;
    (void)owner;
    return (a >= b && a < b + ios_jit_mappings[0].size) ? (void *)(a + LIVE_DELTA) : addr;
}
''' + table + "\n" + binder + r'''
#define IMG 0x8000
static unsigned char *make_importer( uint64_t *slots, int n )
{
    unsigned char *img = aligned_alloc( 0x4000, IMG );
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)img; IMAGE_NT_HEADERS64 *nt;
    memset( img, 0, IMG );
    dos->e_magic = 0x5a4d; dos->e_lfanew = 0x80;
    nt = (IMAGE_NT_HEADERS64 *)(img + 0x80);
    nt->Signature = 0x4550; nt->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC; nt->OptionalHeader.NumberOfRvaAndSizes = 16;
    nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IAT].VirtualAddress = 0x4000;
    nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IAT].Size = n * 8;
    memcpy( img + 0x4000, slots, n * 8 );
    return img;
}
int main( int argc, char **argv )
{
    const char *mode = argc > 1 ? argv[1] : "";
    unsigned char *exporter = aligned_alloc( 0x4000, 0x8000 ), *other = aligned_alloc( 0x4000, 0x8000 );
    uint64_t data = (uintptr_t)exporter + 0x2000, code = (uintptr_t)exporter + 0x100, oth = (uintptr_t)other + 0x2000, none = 0x1234560000ull;
    uint64_t slots[4] = { data, code, oth, none };
    unsigned char *imp = make_importer( slots, 4 );
    uint64_t *iat = (uint64_t *)(imp + 0x4000);
    ios_jit_mappings[0].pe_base = exporter; ios_jit_mappings[0].size = 0x8000;
    ios_jit_mappings[1].pe_base = other; ios_jit_mappings[1].size = 0x8000;
    ios_jit_mapping_count = 2;
    if (!strcmp( mode, "noted" )) ios_x64_nocopy_note( (uintptr_t)imp, IMG );
    if (!strcmp( mode, "forgotten" )) { ios_x64_nocopy_note( (uintptr_t)imp, IMG ); ios_x64_nocopy_forget( (uintptr_t)imp ); }
    if (!strcmp( mode, "copy" )) { ios_jit_mappings[2].pe_base = imp; ios_jit_mappings[2].size = IMG; ios_jit_mapping_count = 3; }
    ios_x64_iat_bind_live_data( (char *)imp + 0x4000, 0x4000 );
    printf( "data=%s code=%s other=%s none=%s lines=%d\n",
            iat[0] == data ? "same" : iat[0] == data + LIVE_DELTA ? "live" : "?",
            iat[1] == code ? "same" : "changed", iat[2] == oth ? "same" : "changed", iat[3] == none ? "same" : "changed", g_bound_lines );
    return 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc and sys.platform == "darwin":
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the table and the binder compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            base = {k: v for k, v in os.environ.items() if k != "MADEIRA_X64_NOCOPY_DATAIMP"}

            def run(mode, key):
                env = dict(base)
                if key is not None:
                    env["MADEIRA_X64_NOCOPY_DATAIMP"] = key
                return subprocess.run([str(exe), mode], capture_output=True, text=True, env=env).stdout.strip()

            same = "data=same code=same other=same none=same lines=0"
            for key in (None, "0", "yes", "11"):
                check("key %s: a noted no-copy importer is left as today" % ("unset" if key is None else repr(key)), run("noted", key) == same, run("noted", key))
            check("key 1: the data slot of the ARM64EC exporter gets the live address; code, x64 and unknown targets do not",
                  run("noted", "1") == "data=live code=same other=same none=same lines=1", run("noted", "1"))
            check("key 1: an importer never noted is not touched", run("plain", "1") == same, run("plain", "1"))
            check("key 1: a forgotten (unmapped) importer is not touched", run("forgotten", "1") == same, run("forgotten", "1"))
            check("key off: a copy-mode importer (in the module table) binds exactly as before",
                  run("copy", None) == "data=live code=same other=same none=same lines=1", run("copy", None))
else:
    print("note: not on macOS or no C compiler; the binder was not run")

pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 1", '"MADEIRA_X64_NOCOPY_DATAIMP",' in pol and 'case "MADEIRA_X64_NOCOPY_DATAIMP":' in pol)
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents it, off by default",
      re.search(r'key: "env\.MADEIRA_X64_NOCOPY_DATAIMP"[^\n]*kind: \.bool, defaultValue: "0"', cat) is not None)
recs = json.loads((root / "recommended/recommendations.json").read_text())
carriers = sorted(r["id"] for r in recs["rules"] if "MADEIRA_X64_NOCOPY_DATAIMP" in json.dumps(r["config"]))
pp = next((r for r in recs["rules"] if r["id"] == "poppy-playtime"), None)
# Round 630 (agent G1 S4): the REDlauncher of The Witcher 3 and Cyberpunk 2077 (7Q6RTY) stops the same way; their rows carry it too.
check("only Poppy Playtime's, (round 630) Cyberpunk 2077's and The Witcher 3's rows set it, Poppy Playtime's from build 626",
      carriers == ["cyberpunk-2077", "poppy-playtime", "the-witcher-3"] and pp is not None
      and {"line": "env.MADEIRA_X64_NOCOPY_DATAIMP = 1", "minBuild": 626} in pp["config"], carriers)
rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "x64-nocopy-unbound-data-import"'):] if '"id": "x64-nocopy-unbound-data-import"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune adds the line after the launcher's read of std::cout's PE copy",
      '"lines": ["env.MADEIRA_X64_NOCOPY_DATAIMP = 1"]' in r and "mapped without a pool copy" in r and "msvcp140.dll+0xe" in r, r[:400])   # round 630: any stream object
exp = root / "tests/fixtures/autotune/poppy-playtime-nocopy-cout-2026-10-31.expect"
check("the fixture is there and expects the line", exp.is_file() and json.loads(exp.read_text()).get("next") == ["env.MADEIRA_X64_NOCOPY_DATAIMP = 1"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-x64-nocopy-dataimp.py" in wf)
print("PASS: no-copy data imports" if ok else "FAILED: no-copy data imports")
sys.exit(0 if ok else 1)
