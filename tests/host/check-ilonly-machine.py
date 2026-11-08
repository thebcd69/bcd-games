#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An AnyCPU IL-only .NET program is not run as a native ARM64 process (round 579); no Wine, no app build.

Wine's server marks an IL-only PE32 image without COMIMAGE_FLAGS_32BITREQUIRED (AnyCPU) ComPlusNativeReady, and the
process then runs as the NATIVE machine, ARM64 here. The native set (aarch64-windows) has no mscoree.dll, so the loader
stops with "mscoree.dll not found, IL-only binary ... cannot be loaded", c000007b (The Dishwasher: Vampire Smile, SSXDRW:
[Wine child] 64-bit image machine=0xaa64 for a PE32 whose header says 0x14c, virtual_map_module 0x4000000e). The server's
copy (build/wineserver/mapping_ios.c) now sets the flag only while the native machine's set has an mscoree.dll; without it
the image keeps its own machine (i386) and runs under WoW64 with the i386 mscoree.dll and Wine Mono, like the
32BITREQUIRED .NET programs (Panzer Paladin) already do.

Part A reads the sources: the flag has one writer, every consumer reads it from the mapping, the probe looks where the app
sets WINEDLLPATH, and nothing else in get_image_params changed against the pinned upstream (when the wine submodule is
there). Part B compiles the production probe and flag function: every CLR flag combination gives upstream's answer when the
native set has an mscoree.dll, upstream's answer minus ComPlusNativeReady when it has none, and 32BITREQUIRED images are the
same either way. Part C holds the workflow, the auto-tune rule and its fixture to this fix.
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def function(source, start):
    a = source.index(start); b = source.index("{", a); depth = 1; c = b + 1
    while depth:
        depth += (source[c] == "{") - (source[c] == "}"); c += 1
    return source[a:c]


mapping = (root / "build/wineserver/mapping_ios.c").read_text().replace("\r\n", "\n")

# ---- Part A: the sources
params = function(mapping, "static unsigned int get_image_params(")
probe = function(mapping, "static int ios_native_has_mscoree(")
flags = function(mapping, "static unsigned int ios_ilonly_image_flags(")
say = function(mapping, "static void ios_ilonly_say(")
clr_block = params[params.index("if (load_clr_header( &clr"):]
clr_block = clr_block[:clr_block.index("if (load_cfg_header(")]
check("the IL-only block still sets ComPlusILOnly itself and the three flags come from the one function",
      "mapping->image.image_flags |= IMAGE_FLAGS_ComPlusILOnly;" in clr_block and
      "mapping->image.image_flags |= ios_ilonly_image_flags( clr.Flags, native_ilonly );" in clr_block and
      "IMAGE_FLAGS_ComPlusNativeReady" not in clr_block)
check("the probe is asked with WINEDLLPATH and the server's native machine, only for a PE32 image",
      "int native_ilonly = !ios_ilonly_wow64_enabled() ||\n"
      "                                ios_native_has_mscoree( getenv( \"WINEDLLPATH\" ), native_machine );" in clr_block and
      clr_block.index("IMAGE_NT_OPTIONAL_HDR32_MAGIC") < clr_block.index("ios_native_has_mscoree("))
check("the log line is printed only when the flag was withheld (an image that is 32BITREQUIRED keeps quiet)",
      "if (!native_ilonly && !(clr.Flags & COMIMAGE_FLAGS_32BITREQUIRED))" in clr_block and "ios_ilonly_say(" in clr_block)
check("the diagnostic says what the loader never printed: the CLR flags, and is limited",
      "CLR flags=%#x" in say and "said++ < 6" in say and "[ilonly-machine]" in say)
check("ComPlusNativeReady is set in ONE place in the server's copy, and read from the mapping by every consumer",
      mapping.count("|= IMAGE_FLAGS_ComPlusNativeReady") == 1 and "flags |= IMAGE_FLAGS_ComPlusNativeReady;" in flags)
writers = []
for rel in subprocess.run(["git", "-C", str(root), "ls-files", "build", "app"], capture_output=True, text=True, check=True).stdout.split("\n"):
    f = root / rel
    if f.suffix in (".c", ".m", ".h", ".swift") and f.is_file():
        t = f.read_text(errors="replace")
        if re.search(r"\|=\s*IMAGE_FLAGS_ComPlusNativeReady|image_flags\s*=[^=;]*ComPlusNativeReady", t):
            writers.append(rel)
check("no other file sets the flag (%s)" % writers, writers == ["build/wineserver/mapping_ios.c"])
check("the consumers still read it from the image info: server map_image_view and the ios child bring-up",
      "(view->image.image_flags & IMAGE_FLAGS_ComPlusNativeReady) ?" in mapping and
      "(args->pe_info.image_flags & IMAGE_FLAGS_ComPlusNativeReady)" in
      (root / "build/ntdll-unix/process_ios.c").read_text())
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
check("the app sets WINEDLLPATH to the bundle, the directory that holds aarch64-windows (what the probe searches)",
      'setenv("WINEDLLPATH", bundlePath.UTF8String, 1);' in bridge and '"aarch64-windows"' in bridge)
check("the probe looks for <WINEDLLPATH entry>/aarch64-windows/mscoree.dll, ARM64 native only",
      '"%s/aarch64-windows/mscoree.dll"' in probe and "native != IMAGE_FILE_MACHINE_ARM64" in probe)

wine_mapping = root / "wine/server/mapping.c"
if wine_mapping.is_file():
    up = wine_mapping.read_text()
    upstream = """        mapping->image.image_flags |= IMAGE_FLAGS_ComPlusILOnly;
        if (nt.opt.hdr32.Magic == IMAGE_NT_OPTIONAL_HDR32_MAGIC)
        {
            if (!(clr.Flags & COMIMAGE_FLAGS_32BITREQUIRED))
                mapping->image.image_flags |= IMAGE_FLAGS_ComPlusNativeReady;
            if (clr.Flags & COMIMAGE_FLAGS_32BITPREFERRED)
                mapping->image.image_flags |= IMAGE_FLAGS_ComPlusPrefer32bit;
        }"""
    check("the pinned upstream server/mapping.c has the block this replaces (so the copy is current)", upstream in up)
    check("and the same process-machine rule that reads the flag", "(view->image.image_flags & IMAGE_FLAGS_ComPlusNativeReady) ?" in up)
else:
    print("skip the pinned upstream comparison (wine submodule not checked out)")

# ---- Part A2: the kill switch (upstream Madeira v0.2.0, #516): env.MADEIRA_ILONLY_WOW64 = 0 restores the native promotion
sw = function(mapping, "static int ios_ilonly_wow64_enabled(")
check("the switch is read from bcd.cfg's env. line first (the server starts before the bridge exports env. lines), then the environment; "
      "only \"0\" turns it off", 'madeira_cfg_get( "env.MADEIRA_ILONLY_WOW64", buf, sizeof(buf) )' in sw and
      'getenv( "MADEIRA_ILONLY_WOW64" )' in sw and "enabled = !(v && !strcmp( v, \"0\" ));" in sw)
check("the bcd.cfg reader is included before Wine's headers (winnt.h poisons strncpy) with the unused-function warning off, like "
      "storage_device_ios.c", mapping.index('#include "../madeira_cfg.h"') < mapping.index('#include "ntstatus.h"') and
      mapping.index('#include "config.h"') < mapping.index('#include "../madeira_cfg.h"') and
      '#pragma GCC diagnostic ignored "-Wunused-function"' in mapping[:mapping.index('#include "ntstatus.h"')])
check("with the switch off the flag is upstream's again, and the probe is not even asked",
      clr_block.index("!ios_ilonly_wow64_enabled() ||") < clr_block.index("ios_native_has_mscoree("))

# ---- Part B: the production functions
code = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <assert.h>
typedef unsigned int DWORD;
#define IMAGE_FILE_MACHINE_I386  0x014c
#define IMAGE_FILE_MACHINE_AMD64 0x8664
#define IMAGE_FILE_MACHINE_ARM64 0xaa64
#define COMIMAGE_FLAGS_ILONLY        0x00000001
#define COMIMAGE_FLAGS_32BITREQUIRED 0x00000002
#define COMIMAGE_FLAGS_32BITPREFERRED 0x00020000
#define IMAGE_FLAGS_ComPlusNativeReady 0x01
#define IMAGE_FLAGS_ComPlusILOnly      0x02
#define IMAGE_FLAGS_ComPlusPrefer32bit 0x20
static unsigned short native_machine = IMAGE_FILE_MACHINE_ARM64;
""" + probe + "\n" + flags + "\n" + say + r"""

/* upstream: the three statements of get_image_params, verbatim in effect */
static unsigned int upstream_flags( DWORD clr_flags )
{
    unsigned int f = IMAGE_FLAGS_ComPlusILOnly;
    if (!(clr_flags & COMIMAGE_FLAGS_32BITREQUIRED)) f |= IMAGE_FLAGS_ComPlusNativeReady;
    if (clr_flags & COMIMAGE_FLAGS_32BITPREFERRED) f |= IMAGE_FLAGS_ComPlusPrefer32bit;
    return f;
}

int main(int argc, char **argv)
{
    char a[1100], b[1100], c[1100], longp[1200];
    const char *d = argv[1];
    int combos = 0;

    /* flags: every combination of the CLR flag bits (and the neighbours the code must ignore) */
    for (DWORD clr = 0; clr < 0x40000; clr += 1)
    {
        if (clr & ~(COMIMAGE_FLAGS_ILONLY | COMIMAGE_FLAGS_32BITREQUIRED | COMIMAGE_FLAGS_32BITPREFERRED | 0x4 | 0x8 | 0x10000))
            continue;
        unsigned int up = upstream_flags( clr ), on = ios_ilonly_image_flags( clr, 1 ), off = ios_ilonly_image_flags( clr, 0 );
        if (on != up) { printf("FAIL: native set has mscoree, flags %#x: %#x != upstream %#x\n", clr, on, up); return 1; }
        if (off != (up & ~IMAGE_FLAGS_ComPlusNativeReady)) { printf("FAIL: no mscoree, flags %#x: %#x\n", clr, off); return 1; }
        if ((clr & COMIMAGE_FLAGS_32BITREQUIRED) && on != off) { printf("FAIL: 32BITREQUIRED differs, flags %#x\n", clr); return 1; }
        if (!(off & IMAGE_FLAGS_ComPlusILOnly)) { printf("FAIL: ILOnly lost, flags %#x\n", clr); return 1; }
        combos++;
    }

    /* the probe: <dir>/aarch64-windows/mscoree.dll decides; the other machines' sets do not count */
    snprintf( a, sizeof(a), "%s/with", d );       /* has aarch64-windows/mscoree.dll */
    snprintf( b, sizeof(b), "%s/others", d );     /* arm64ec-windows and i386-windows have one, aarch64-windows has none */
    snprintf( c, sizeof(c), "%s/empty", d );
    if (ios_native_has_mscoree( a, IMAGE_FILE_MACHINE_ARM64 ) != 1) { puts("FAIL: aarch64 set with mscoree.dll"); return 1; }
    if (ios_native_has_mscoree( b, IMAGE_FILE_MACHINE_ARM64 ) != 0) { puts("FAIL: only the other sets have one"); return 1; }
    if (ios_native_has_mscoree( c, IMAGE_FILE_MACHINE_ARM64 ) != 0) { puts("FAIL: empty directory"); return 1; }
    char two[3300];
    snprintf( two, sizeof(two), "%s:%s", b, a );
    if (ios_native_has_mscoree( two, IMAGE_FILE_MACHINE_ARM64 ) != 1) { puts("FAIL: second WINEDLLPATH entry"); return 1; }
    snprintf( two, sizeof(two), "%s:%s:%s", b, c, "/nonexistent" );
    if (ios_native_has_mscoree( two, IMAGE_FILE_MACHINE_ARM64 ) != 0) { puts("FAIL: three entries, none with one"); return 1; }
    snprintf( two, sizeof(two), "::%s::", a );
    if (ios_native_has_mscoree( two, IMAGE_FILE_MACHINE_ARM64 ) != 1) { puts("FAIL: empty entries around one"); return 1; }
    /* cannot be told: upstream's answer (1) */
    if (ios_native_has_mscoree( NULL, IMAGE_FILE_MACHINE_ARM64 ) != 1) { puts("FAIL: no path"); return 1; }
    if (ios_native_has_mscoree( "", IMAGE_FILE_MACHINE_ARM64 ) != 1) { puts("FAIL: empty path"); return 1; }
    if (ios_native_has_mscoree( c, IMAGE_FILE_MACHINE_AMD64 ) != 1) { puts("FAIL: an AMD64 native machine"); return 1; }
    if (ios_native_has_mscoree( c, 0 ) != 1) { puts("FAIL: unknown native machine"); return 1; }
    /* a path too long for the file name buffer is "not found", not an overflow */
    memset( longp, 'x', sizeof(longp) - 1 ); longp[0] = '/'; longp[sizeof(longp) - 1] = 0;
    if (ios_native_has_mscoree( longp, IMAGE_FILE_MACHINE_ARM64 ) != 0) { puts("FAIL: a too-long path"); return 1; }
    /* the original string is left alone (strtok_r works on a copy) */
    snprintf( two, sizeof(two), "%s:%s", b, a ); char keep[3300]; strcpy( keep, two );
    ios_native_has_mscoree( two, IMAGE_FILE_MACHINE_ARM64 );
    if (strcmp( keep, two )) { puts("FAIL: the WINEDLLPATH string was changed"); return 1; }

    /* the diagnostic prints at most six times */
    for (int i = 0; i < 20; i++) ios_ilonly_say( 0x1, IMAGE_FILE_MACHINE_I386 );
    printf("flags ok over %d CLR flag combinations; probe ok\n", combos);
    return 0;
}
"""
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    for sub in ("with/aarch64-windows", "others/arm64ec-windows", "others/i386-windows", "others/aarch64-windows", "empty"):
        (t / sub).mkdir(parents=True)
    (t / "with/aarch64-windows/mscoree.dll").write_bytes(b"MZ")
    (t / "others/arm64ec-windows/mscoree.dll").write_bytes(b"MZ")
    (t / "others/i386-windows/mscoree.dll").write_bytes(b"MZ")
    (t / "others/aarch64-windows/kernel32.dll").write_bytes(b"MZ")
    c = t / "check.c"; exe = t / "check"; c.write_text(code)
    subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-Werror=implicit-function-declaration", str(c), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe), str(t)], capture_output=True, text=True)
    lines = out.stdout.strip().splitlines()
    check("production functions: upstream's flags with an mscoree.dll, without ComPlusNativeReady when there is none; the probe "
          "(%s)" % (lines[-1] if lines else out.stderr[-300:]), out.returncode == 0 and "flags ok" in out.stdout)
    said = [l for l in out.stderr.splitlines() if l.startswith("[ilonly-machine]")]
    check("the diagnostic is printed %d times for 20 calls" % len(said), len(said) == 6 and "CLR flags=0x1, machine=0x14c" in said[0])

# ---- Part B2: the production switch function against the production bcd.cfg reader
swcode = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "madeira_cfg.h"
""" + sw + r"""
int main(void) { printf("%d\n", ios_ilonly_wow64_enabled()); return 0; }
"""
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    c = t / "sw.c"; exe = t / "sw"; c.write_text(swcode)
    r = subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-I", str(root / "build"), "-Werror=implicit-function-declaration",
                        str(c), "-o", str(exe)], capture_output=True, text=True)
    check("the production switch function compiles with the production bcd.cfg reader" +
          ("" if r.returncode == 0 else ": " + r.stderr[-400:]), r.returncode == 0)
    if r.returncode == 0:
        docs = t / "docs"; docs.mkdir()
        game = t / "game.cfg"

        def answer(cfg, env=None, gamecfg=None):
            (docs / "bcd.cfg").unlink(missing_ok=True)
            if cfg is not None:
                (docs / "bcd.cfg").write_text(cfg)
            if gamecfg is not None:
                game.write_text(gamecfg)
            e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
            e["MADEIRA_DOCS_DIR"] = str(docs)
            if env is not None:
                e["MADEIRA_ILONLY_WOW64"] = env
            if gamecfg is not None:
                e["MADEIRA_CFG_GAME"] = str(game)
            return subprocess.run([str(exe)], capture_output=True, text=True, env=e).stdout.strip()

        check("nothing set: on (the round 579 behaviour stays the default)", answer(None) == "1")
        check("bcd.cfg without the key: on", answer("swap-mb = 1024\n") == "1")
        check("env.MADEIRA_ILONLY_WOW64 = 0 in bcd.cfg: off", answer("env.MADEIRA_ILONLY_WOW64 = 0\n") == "0")
        check("env.MADEIRA_ILONLY_WOW64 = 1 in bcd.cfg: on", answer("env.MADEIRA_ILONLY_WOW64 = 1\n") == "1")
        check("the process environment alone: 0 is off", answer(None, env="0") == "0")
        check("the process environment alone: anything but 0 is on", all(answer(None, env=v) == "1" for v in ("1", "", "00", "no")))
        check("a game's own file wins over bcd.cfg (off in the game file)", answer("env.MADEIRA_ILONLY_WOW64 = 1\n", gamecfg="env.MADEIRA_ILONLY_WOW64 = 0\n") == "0")
        check("a game's own file wins over bcd.cfg (on in the game file)", answer("env.MADEIRA_ILONLY_WOW64 = 0\n", gamecfg="env.MADEIRA_ILONLY_WOW64 = 1\n") == "1")

# ---- Part C: workflow, auto-tune, fixture
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "python3 tests/host/check-ilonly-machine.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
rid = rules.find('"id": "ilonly-native-no-mscoree"')
block = rules[rid:]
block = block[:block.find("\n    },\n")]
check("auto-tune knows the failure: no line, fixed in 579, Wine's own loader line; the fix's line excludes it",
      rid >= 0 and '"lines": []' in block and '"fixedIn": 579' in block and
      'err:module:fixup_imports_ilonly mscoree.dll not found, IL-only binary' in block and
      '"none": [s("[ilonly-machine]")]' in block)
fx = root / "tests/fixtures/autotune/dishwasher-ilonly-2026-10-21"
expect = fx.with_suffix(".expect").read_text(); log = fx.with_suffix(".log").read_text()
check("its fixture (SSXDRW) is matched by the rule and stays 'known'",
      '"ilonly-native-no-mscoree"' in expect and '"known"' in expect and "fixup_imports_ilonly mscoree.dll not found" in log)
sys.exit(0 if ok else 1)
