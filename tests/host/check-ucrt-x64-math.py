#!/usr/bin/env python3
"""ucrtbase-x64math.dll: floor and sqrt as x64 code for x64 callers; no Wine runs.

Horizon Zero Dawn (build 492, 2026-10-10 20:41) makes 4-7.7 million x64->ARM64EC
calls a second, half of them to ucrtbase's floor and sqrt, whose native bodies
are one instruction each. tools/patch-wine-ucrtbase-x64-math.py gives the
exports floor, _o_floor, sqrt and _o_sqrt x64 bodies (EXP+#<export>, a
`#pragma makedep arm64ec_x64` source); tools/build-wine-extra-dlls.sh ships the
result as ucrtbase-x64math.dll next to the untouched ucrtbase.dll; and
app/Madeira/WineProcessBridge.m links it as system32\\ucrtbase.dll only when the
game's settings have env.MADEIRA_UCRT_X64_MATH = 1.

Checks:
  - the patch writes dlls/ucrtbase/x64math.c (the arm64ec_x64 pragma, the four
    EXP+# bodies, roundsd $9 for floor, sqrtsd for x >= 0 with negative x and
    NaN sent on to the native MSVCRT_sqrt) and appends it to SOURCES, changing
    nothing else in Makefile.in, and a second run changes nothing;
  - the bodies' bytes --verify expects are the encodings of those instructions,
    and, when an x86_64 assembler is at hand, what it assembles them to;
  - no 4-byte word of the bodies looks like an x18 access to the production
    x18 patcher (it walks the whole .text of the pool copy, x64 ranges too);
  - --verify accepts a DLL whose exports are the bodies in an X64 code range and
    refuses one with the linker's fast-forward sequences (a synthetic PE);
  - the build step ships ucrtbase-x64math.dll, never ucrtbase.dll, from a make
    of its own after the msvcrt patches are undone, verifies it first and
    restores the tree; the main make no longer runs with no targets;
  - WineProcessBridge.m relinks system32 and sysx64 ucrtbase.dll only for
    MADEIRA_UCRT_X64_MATH=1 in an ARM64EC session, and says so either way;
  - CI runs this check and verifies the shipped variant; the catalog documents
    the switch; no game list sets it yet (an experiment until a device test).
Optional: UCRT_X64MATH_DLL=<built ucrtbase.dll> also runs --verify on it.
"""
from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
script = root / 'tools/patch-wine-ucrtbase-x64-math.py'
sys.path.insert(0, str(script.parent))
import importlib.util
spec = importlib.util.spec_from_file_location('ucrtx64', script)
ucrtx64 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ucrtx64)

# ---- the patch --------------------------------------------------------------
wine_mk = root / 'wine/dlls/ucrtbase/Makefile.in'
assert wine_mk.is_file(), 'wine submodule missing: ' + str(wine_mk)
with tempfile.TemporaryDirectory(prefix='madeira-ucrt-x64-') as directory:
    tree = Path(directory)
    (tree / 'dlls/ucrtbase').mkdir(parents=True)
    shutil.copy(wine_mk, tree / 'dlls/ucrtbase/Makefile.in')
    before = (tree / 'dlls/ucrtbase/Makefile.in').read_text().split('\n')
    run = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    after = (tree / 'dlls/ucrtbase/Makefile.in').read_text().split('\n')
    src = (tree / 'dlls/ucrtbase/x64math.c').read_text()
    assert '#if 0\n#pragma makedep arm64ec_x64\n#endif' in src
    assert '#ifdef __arm64ec_x64__' in src and '#include "wine/asm.h"' in src
    for name, body in (('floor', 'X64_FLOOR'), ('_o_floor', 'X64_FLOOR'), ('sqrt', 'X64_SQRT'), ('_o_sqrt', 'X64_SQRT')):
        assert '__ASM_GLOBAL_FUNC( "EXP+#%s", %s )' % (name, body) in src, name
    assert '#define X64_FLOOR "roundsd $9,%xmm0,%xmm0\\n\\tret"' in src
    for piece in ('"xorpd %xmm1,%xmm1\\n\\t"', '"ucomisd %xmm1,%xmm0\\n\\t"', '"jb 1f\\n\\t"',
                  '"sqrtsd %xmm0,%xmm0\\n\\t"', '"ret\\n"', '"1:\\tjmp \\"#MSVCRT_sqrt\\""'):
        assert piece in src, piece
    i = before.index('SOURCES = \\')
    j = i + 1
    while before[j].endswith('\\'):
        j += 1
    assert after[:j] == before[:j] and after[j] == before[j] + ' \\' and after[j + 1] == '\tx64math.c', 'SOURCES append'
    assert after[j + 2:] == before[j + 1:], 'nothing else in Makefile.in changes'
    run = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True, text=True)
    assert run.returncode == 0 and 'already patched' in run.stdout
    assert (tree / 'dlls/ucrtbase/Makefile.in').read_text().split('\n') == after
print('PASS: the patch adds x64math.c (four EXP+# x64 bodies) to ucrtbase SOURCES and nothing else; idempotent')

# ---- encodings --------------------------------------------------------------
ROUNDSD_9 = bytes.fromhex('660f3a0bc009')   # roundsd $9,%xmm0,%xmm0
XORPD_11 = bytes.fromhex('660f57c9')        # xorpd %xmm1,%xmm1
UCOMISD_10 = bytes.fromhex('660f2ec1')      # ucomisd %xmm1,%xmm0
JB_5 = bytes.fromhex('7205')                # jb +5 (over sqrtsd and ret)
SQRTSD_00 = bytes.fromhex('f20f51c0')       # sqrtsd %xmm0,%xmm0
RET, JMP32 = b'\xc3', b'\xe9'
assert ucrtx64.FLOOR_BYTES == ROUNDSD_9 + RET
assert ucrtx64.SQRT_BYTES == XORPD_11 + UCOMISD_10 + JB_5 + SQRTSD_00 + RET + JMP32
assert len(SQRTSD_00 + RET) == 5, 'jb skips exactly sqrtsd and ret'
assert set(ucrtx64.EXPORTS) == {'floor', '_o_floor', 'sqrt', '_o_sqrt'}


def coff_text(obj):
    d = obj.read_bytes()
    if struct.unpack_from('<H', d, 0)[0] != 0x8664:
        return None
    nsec, = struct.unpack_from('<H', d, 2)
    out = b''
    for k in range(nsec):
        s = 20 + 40 * k
        name = d[s:s + 8].rstrip(b'\0')
        size, ptr = struct.unpack_from('<II', d, s + 16)
        if name == b'.text':
            out += d[ptr:ptr + size]
    return out


asm = ('.text\nf:\n\troundsd $9,%xmm0,%xmm0\n\tret\n'
       's:\n\txorpd %xmm1,%xmm1\n\tucomisd %xmm1,%xmm0\n\tjb 1f\n\tsqrtsd %xmm0,%xmm0\n\tret\n1:\tjmp elsewhere\n')
assembled = None
with tempfile.TemporaryDirectory(prefix='madeira-ucrt-x64-as-') as directory:
    folder = Path(directory)
    (folder / 'b.s').write_text(asm)
    for cc in (os.environ.get('X64_CC'), 'x86_64-w64-mingw32-clang', 'clang'):
        if not cc or not shutil.which(cc):
            continue
        r = subprocess.run([cc, '-target', 'x86_64-w64-windows-gnu', '-c', str(folder / 'b.s'), '-o', str(folder / 'b.o')],
                           capture_output=True)
        if r.returncode == 0:
            assembled = coff_text(folder / 'b.o')
            if assembled is not None:
                break
if assembled is None:
    print('SKIP: no x86_64 COFF assembler here; the encodings rest on the table above (CI --verify reads the built DLL)')
else:
    want = ROUNDSD_9 + RET + XORPD_11 + UCOMISD_10 + JB_5 + SQRTSD_00 + RET + JMP32
    assert assembled.startswith(want), assembled.hex()
    print('PASS: an x86_64 assembler encodes the bodies as --verify expects')

# ---- the x18 patcher --------------------------------------------------------
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


defines = native[native.index('#define X18_ROLE_NONE 0'):native.index('static int ios_insn_x18_role(uint32_t insn)')]
code = '#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n' + defines
code += function(native, 'static int ios_insn_x18_role(uint32_t insn)')
bodies = []
for pad in (b'\0', b'\xcc', b'\x90'):
    floor_fn = ucrtx64.FLOOR_BYTES + pad * (16 - len(ucrtx64.FLOOR_BYTES))
    # the jmp's rel32 differs per build: test a few backward displacements
    for rel in (0xfffa5cd8, 0xfffa5cb8, 0xfff00000, 0xffffff00):
        sqrt_fn = ucrtx64.SQRT_BYTES + struct.pack('<I', rel)
        sqrt_fn += pad * (32 - len(sqrt_fn))
        bodies.append(floor_fn * 2 + sqrt_fn * 2)
blob = b''.join(bodies)
code += 'static const unsigned char blob[] = {' + ','.join(str(b) for b in blob) + '};\n'
code += r'''
int main( void )
{
    size_t i;
    for (i = 0; i + 4 <= sizeof(blob); i += 4)
    {
        uint32_t w;
        memcpy( &w, blob + i, 4 );
        if (ios_insn_x18_role( w ) != X18_ROLE_NONE) { fprintf( stderr, "word at %zu (0x%08x) looks like an x18 access\n", i, w ); return 1; }
    }
    puts( "PASS: no word of the x64 bodies looks like an x18 access to the x18 patcher" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-ucrt-x18-') as directory:
    folder = Path(directory)
    (folder / 'check.c').write_text(code)
    exe = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    subprocess.run([cc, '-std=gnu11', '-Wall', '-Wno-unused-function', '-Wno-unused-const-variable',
                    '-Wno-tautological-compare', '-O1', str(folder / 'check.c'), '-o', str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    print(out.stdout, end='')
    assert out.returncode == 0, out.stderr

# ---- --verify on synthetic images ---------------------------------------------


def synth(bodies_x64):
    """A PE32+ image: .text 0x1000 (ARM64EC 0x1000-0x2000, X64 0x2000-0x3000),
    .rdata 0x3000 with exports, load config and CHPE code map."""
    img = bytearray(0x4000)
    struct.pack_into('<H', img, 0, 0x5a4d)
    struct.pack_into('<I', img, 0x3c, 0x80)
    pe = 0x80
    struct.pack_into('<IHH', img, pe, 0x4550, 0xa641, 2)
    struct.pack_into('<H', img, pe + 20, 240)   # SizeOfOptionalHeader
    opt = pe + 24
    struct.pack_into('<H', img, opt, 0x20b)
    struct.pack_into('<Q', img, opt + 24, 0x180000000)
    struct.pack_into('<I', img, opt + 56, 0x4000)   # SizeOfImage
    sec = opt + 240
    for k, (name, va, size) in enumerate(((b'.text', 0x1000, 0x2000), (b'.rdata', 0x3000, 0x1000))):
        s = sec + 40 * k
        img[s:s + 8] = name.ljust(8, b'\0')
        struct.pack_into('<IIII', img, s + 8, size, va, size, va)
    names = ['_o_floor', '_o_sqrt', 'floor', 'sqrt']
    rvas = {'floor': 0x2000, '_o_floor': 0x2010, 'sqrt': 0x2020, '_o_sqrt': 0x2040}
    for n, rva in rvas.items():
        body = ucrtx64.EXPORTS[n] if bodies_x64 else bytes.fromhex('488bc448895820555de9') + b'\0' * 4
        img[rva:rva + len(body)] = body
    e = 0x3000
    struct.pack_into('<IIIIIIIIII', img, e, 0, 0, 0, 0, 1, 4, 4, e + 0x40, e + 0x50, e + 0x60)
    for k, n in enumerate(names):
        struct.pack_into('<I', img, e + 0x40 + 4 * k, rvas[n])
        struct.pack_into('<I', img, e + 0x50 + 4 * k, e + 0x80 + 16 * k)
        struct.pack_into('<H', img, e + 0x60 + 2 * k, k)
        img[e + 0x80 + 16 * k:e + 0x80 + 16 * k + len(n)] = n.encode()
    struct.pack_into('<II', img, opt + 112, e, 0x100)               # export directory
    cfg = 0x3200
    struct.pack_into('<II', img, opt + 112 + 10 * 8, cfg, 0x140)    # load config
    struct.pack_into('<Q', img, cfg + 0xC8, 0x180000000 + 0x3400)    # CHPEMetadataPointer
    struct.pack_into('<III', img, 0x3400, 1, 0x3500, 2)              # version, CodeMap, count
    struct.pack_into('<IIII', img, 0x3500, 0x1000 | 1, 0x1000, 0x2000 | 2, 0x1000)
    return bytes(img)


with tempfile.TemporaryDirectory(prefix='madeira-ucrt-verify-') as directory:
    good = Path(directory) / 'good.dll'
    bad = Path(directory) / 'stock.dll'
    good.write_bytes(synth(True))
    bad.write_bytes(synth(False))
    r = subprocess.run([sys.executable, str(script), '--verify', str(good)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, str(script), '--verify', str(bad)], capture_output=True, text=True)
    assert r.returncode != 0 and 'expected 660f3a0bc009c3' in r.stderr, r.stdout + r.stderr
built = os.environ.get('UCRT_X64MATH_DLL')
if built:
    r = subprocess.run([sys.executable, str(script), '--verify', built], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    print('PASS: --verify accepts ' + built)
print('PASS: --verify accepts x64 bodies in an X64 range and refuses fast-forward sequences')

# ---- the build step ----------------------------------------------------------
sh = (root / 'tools/build-wine-extra-dlls.sh').read_text()
assert 'if [ "${MADEIRA_UCRT_X64_MATH_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/ucrtbase/Makefile.in" ]; then' in sh
import re
# NTS: ntdll-sync.dll, XSE: ntdll-xsec.dll, FBT: kernelbase-fibertrace.dll, WER: wer-optin.dll, WTY: wintypes-optin.dll, SDL: setupapi-optin.dll
# (round 624 added ZMP, kernelbase-optin.dll, and DCO, dcomp-optin.dll; the order of the variables does not matter)
guard = re.search(r'\[ -n "\$todo((?:\$[A-Z]+)+)" \] \|\| \{ echo "nothing to build"; exit 0; \}', sh)
assert guard and {'$XI', '$WGI', '$UCX', '$NTS', '$XSE', '$FBT', '$WER', '$WTY', '$OPT', '$SDL'} <= set(re.findall(r'\$[A-Z]+', guard.group(1))), \
    'the "nothing to build" guard must count every extra DLL'
assert '[ -n "$targets" ] && make -C "$B" -k -j"$JOBS" $targets' in sh, 'no make of "all" when only ucrtbase is wanted'
step = sh[sh.index('if [ -n "$UCX" ]; then'):]
step = step[:step.index('\nfi\n') + 4]
restore = sh.index('git -C "$R/wine" checkout -- dlls/msvcrt/main.c')
assert restore < sh.index('if [ -n "$UCX" ]; then'), 'built after the msvcrt patches are undone'
assert 'python3 "$R/tools/patch-wine-ucrtbase-x64-math.py" "$R/wine"' in step
assert 'make -C "$B" -j"$JOBS" dlls/ucrtbase/arm64ec-windows/ucrtbase.dll' in step
assert step.index('--verify "$uc"') < step.index('cp "$uc" "$SHIP/ucrtbase-x64math.dll.tmp"'), 'verified before it ships'
assert 'mv "$SHIP/ucrtbase-x64math.dll.tmp" "$SHIP/ucrtbase-x64math.dll"' in step
assert '"$SHIP/ucrtbase.dll' not in sh, 'the shipped ucrtbase.dll is never written'
assert 'git -C "$R/wine" checkout -- dlls/ucrtbase/Makefile.in' in step and 'rm -f "$R/wine/dlls/ucrtbase/x64math.c"' in step
assert 'ucrtbase' not in sh[sh.index('WANT="'):sh.index('for n in $(seq 24 42)')], 'not in the extra-DLL list'
print('PASS: the build ships ucrtbase-x64math.dll beside ucrtbase.dll, verified, from a restored tree')

# ---- the app ------------------------------------------------------------------
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *ucrtX64 = getenv("MADEIRA_UCRT_X64_MATH");'):]
blk = blk[:blk.index('\n            }\n')]
assert "if (use_arm64ec && ucrtX64 && ucrtX64[0] == '1') {" in blk
assert '@"ucrtbase-x64math.dll"' in blk and '@"ucrtbase.dll"' in blk
assert 'for (NSString *dir in @[ sys32Dir, [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"] ])' in blk
assert 'createSymbolicLinkAtPath:dst withDestinationPath:mathDll' in blk
assert 'MADEIRA_UCRT_X64_MATH=1: ucrtbase.dll -> ucrtbase-x64math.dll' in blk
assert 'MADEIRA_UCRT_X64_MATH=1 but this build has no ucrtbase-x64math.dll' in blk
farm = bridge.index('LOG("Symlinked %d DLLs from %{public}s to %{public}s"')
assert farm < bridge.index('const char *ucrtX64 = getenv("MADEIRA_UCRT_X64_MATH");'), 'relinked after the farm'
print('PASS: only MADEIRA_UCRT_X64_MATH=1 in an ARM64EC session links ucrtbase.dll to the variant')

# ---- CI, catalog, game lists ---------------------------------------------------
ci = (root / '.github/workflows/build-ipa.yml').read_text()
assert 'run: python3 tests/host/check-ucrt-x64-math.py' in ci
assert 'python3 tools/patch-wine-ucrtbase-x64-math.py --verify app/Madeira/arm64ec-windows/ucrtbase-x64math.dll' in ci
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
assert '"env.MADEIRA_UCRT_X64_MATH"' in cat
gen = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
assert 'key: "env.MADEIRA_UCRT_X64_MATH"' in gen
rec = (root / 'recommended/recommendations.json').read_text()   # the game lists (2026-10-18)
assert 'MADEIRA_UCRT_X64_MATH' not in rec, 'an experiment: no game list sets it before a device test'
print('PASS: CI runs the check and verifies the shipped variant; the catalog documents the switch; no list sets it')
