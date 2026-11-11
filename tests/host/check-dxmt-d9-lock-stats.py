#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: pixel statistics of an A8R8G8B8 write at UnlockRect, log only (night round 2026-11-11, G1 S2).

Requiem: Avenging Angel (K21Z9Y, H3RW0Y) shows black rectangles where UI sprites should be; the open question is whether the Glide wrapper
writes black data or the draw turns good data black. tools/patch-dxmt-d9-lock-stats.py logs, with DXMT_DUMP_D9_SHADER set, one
[d9-stat] line for each of the first 400 writes of a 1024-pixel-or-larger A8R8G8B8/X8R8G8B8 rect, read from the CPU mirror before the
upload. This check applies it to a copy of the pinned d3d9_surface.cpp (or $DXMT_SRC_DIR), reads the result, and runs the block as plain
C++ on a fake surface: the counts and the FNV-1a hash against the same numbers computed here in Python, the pitch handling, the gates
(key, size, format, read-only, cap) and that nothing is written to the surface. SKIP of the source part when no dxmt sources exist.
"""
import os, pathlib, random, shutil, struct, subprocess, sys, tempfile


def pinned(p):
    """The file as the pinned DXMT commit has it. In a build the working copy already carries the workflow's DXMT patches (this one
    among them), so the patch's own tests (applies once, a moved anchor) are run on the pinned original (night round 2026-11-11)."""
    try:
        top = subprocess.run(['git', '-C', str(p.parent), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True).stdout.strip()
        rel = p.resolve().relative_to(pathlib.Path(top).resolve())
        text = subprocess.run(['git', '-C', top, 'show', 'HEAD:' + rel.as_posix()], capture_output=True, text=True, check=True).stdout
    except (OSError, ValueError, subprocess.CalledProcessError):
        return p
    q = pathlib.Path(tempfile.mkdtemp(prefix='pinned-')) / p.name
    q.write_text(text)
    return q

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        ok = False
        if detail:
            print('     ' + str(detail)[:1500])


script = R / 'tools/patch-dxmt-d9-lock-stats.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it before the i386 farm key (a tools/patch-dxmt-*.py: the farm key hashes it)',
      'python3 tools/patch-dxmt-d9-lock-stats.py' in wf and wf.index('python3 tools/patch-dxmt-d9-lock-stats.py') < wf.index('name: i386 farm revision'))
check('CI runs this check', 'python3 tests/host/check-dxmt-d9-lock-stats.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if not (src / 'd3d9/d3d9_surface.cpp').exists():
    alt = pathlib.Path.home() / 'madeira-bcd/dxmt/src'
    src = alt if (alt / 'd3d9/d3d9_surface.cpp').exists() else src
target = pinned(src / 'd3d9/d3d9_surface.cpp')
if target.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d9').mkdir(parents=True)
        shutil.copy(target, t / 'dxmt/src/d3d9/d3d9_surface.cpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned source and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        s = (t / 'dxmt/src/d3d9/d3d9_surface.cpp').read_text()
        fn = s[s.index('MTLD3D9Surface::UnlockRect() {'):]
        fn = fn[:fn.index('\n}\n')]
        a = fn.index('/* madeira-bcd: d9-lock-stats')
        check('the statistics come right after the lock-policy line and before the upload, which may reclaim the mirror',
              fn.index('d9LogLockPolicy(m_desc, m_locked_w') < a < fn.index('stageTextureUpload(')
              and a < fn.index('noteLevelUploaded('))
        block = fn[a:fn.index('\n  if (m_buffer == nullptr && m_cpu_ptr != nullptr && m_texture != nullptr', a)]
        check('only under DXMT_DUMP_D9_SHADER, only a writable A8R8G8B8/X8R8G8B8 rect of 1024 pixels or more, the first 400, no new env name',
              'env::getEnvVar("DXMT_DUMP_D9_SHADER")' in block and '!m_locked_readonly' in block and 'm_locked_w * m_locked_h >= 1024' in block
              and 'D3DFMT_A8R8G8B8' in block and 'D3DFMT_X8R8G8B8' in block and '< 400' in block and 'MADEIRA_' not in block)
        check('read-only: the block stores nothing into the surface (no assignment to the mirror, no memcpy into it)',
              'std::memcpy(&px, row' in block and 'm_cpu_ptr =' not in block and '*(uint8_t' not in block and 'const uint8_t *base' in block)
        check('<atomic> and <cstring> are included', '#include <atomic>' in s and '#include <cstring>' in s)
        # --- run the block on the host
        prog = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
#include <vector>
enum D3DFORMAT : unsigned { D3DFMT_A8R8G8B8 = 21, D3DFMT_X8R8G8B8 = 22, D3DFMT_R5G6B5 = 23 };
enum D3DPOOL : unsigned { D3DPOOL_DEFAULT = 0, D3DPOOL_MANAGED = 1, D3DPOOL_SYSTEMMEM = 2 };
struct Desc { D3DFORMAT Format; D3DPOOL Pool; unsigned Usage, Width, Height; };
namespace env { std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? v : ""; } }
namespace str { template <typename... A> std::string format(const A &...a) { std::stringstream s; (s << ... << a); return s.str(); } }
struct Logger { static void info(const std::string &m) { std::printf("%s\n", m.c_str()); } };
struct Surface {
  Desc m_desc; void *m_cpu_ptr = nullptr; unsigned m_pitch = 0; bool m_locked_readonly = false;
  unsigned m_locked_x = 0, m_locked_y = 0, m_locked_w = 0, m_locked_h = 0;
  void unlock() {
    ''' + block.strip() + r'''
  }
};
int main(int argc, char **argv) {
  // argv: file w h x y pitch fmt readonly nomirror reps   (the file holds the surface bytes: pitch * height)
  FILE *f = fopen(argv[1], "rb"); std::vector<uint8_t> mem(1 << 22); size_t n = fread(mem.data(), 1, mem.size(), f); fclose(f);
  Surface s; s.m_desc = {(D3DFORMAT)atoi(argv[7]), D3DPOOL_SYSTEMMEM, 0, 640, 480};
  s.m_cpu_ptr = atoi(argv[9]) ? nullptr : mem.data(); s.m_pitch = atoi(argv[6]); s.m_locked_readonly = atoi(argv[8]);
  s.m_locked_w = atoi(argv[2]); s.m_locked_h = atoi(argv[3]); s.m_locked_x = atoi(argv[4]); s.m_locked_y = atoi(argv[5]);
  int reps = atoi(argv[10]);
  for (int i = 0; i < reps; ++i) s.unlock();
  uint8_t sum = 0; for (size_t i = 0; i < n; ++i) sum ^= mem[i];
  std::printf("xor=%u\n", sum);
  return 0;
}
'''
        cc = shutil.which('clang++') or shutil.which('c++')
        (t / 'stat.cpp').write_text(prog)
        b = subprocess.run([cc, '-std=c++20', '-w', '-o', str(t / 'stat'), str(t / 'stat.cpp')], capture_output=True, text=True) if cc else None
        if b is None or b.returncode != 0:
            check('the block compiles on the host', False, (b.stderr if b else 'no C++ compiler'))
        else:
            def model(buf, pitch, x, y, w, h):
                nz = rgb0 = a0 = a255 = 0
                hsh, hashed = 0xcbf29ce484222325, 0
                for yy in range(h):
                    row = buf[(y + yy) * pitch + x * 4:(y + yy) * pitch + x * 4 + w * 4]
                    for (px,) in struct.iter_unpack('<I', row):
                        a = px >> 24
                        nz += px != 0
                        rgb0 += a != 0 and (px & 0xffffff) == 0
                        a0 += a == 0
                        a255 += a == 255
                    for byte in row:
                        if hashed >= 4096:
                            break
                        hsh = ((hsh ^ byte) * 0x100000001b3) & 0xffffffffffffffff
                        hashed += 1
                return nz, rgb0, a0, a255, hsh

            def run(buf, w, h, x, y, pitch, fmt=21, ro=0, key='1', null=False, reps=1):
                p = t / 'surf.bin'
                p.write_bytes(bytes(buf))
                env = {k: v for k, v in os.environ.items() if k != 'DXMT_DUMP_D9_SHADER'}
                if key is not None:
                    env['DXMT_DUMP_D9_SHADER'] = key
                args = [str(t / 'stat'), str(p), str(w), str(h), str(x), str(y), str(pitch), str(fmt), str(ro), str(int(null)), str(reps)]
                return subprocess.run(args, capture_output=True, text=True, env=env).stdout

            pitch = 640 * 4
            black = bytearray(pitch * 480)
            for i in range(0, len(black), 4):
                black[i:i + 4] = struct.pack('<I', 0xFF000000)                       # opaque black everywhere
            out = run(black, 328, 62, 10, 20, pitch)
            nz, rgb0, a0, a255, hsh = model(black, pitch, 10, 20, 328, 62)
            check('an opaque black 328x62 sprite (Requiem\'s UI sprite): rgb0 = every pixel, a255 = every pixel, the hash matches the Python model',
                  f'[d9-stat] madeira-bcd #1 pool=2 usage=0 640x480 rect=10,20 328x62 nz={nz} rgb0={rgb0} a0={a0} a255={a255} fnv={hsh:x}' in out
                  and rgb0 == 328 * 62 and a255 == 328 * 62, out)
            rnd = random.Random(7)
            mixed = bytearray(rnd.randrange(256) for _ in range(pitch * 480))
            for i in range(0, 4000, 4):
                mixed[i:i + 4] = struct.pack('<I', rnd.choice((0, 0xFF000000, 0x00FF00FF, 0x80102030)))
            out = run(mixed, 190, 57, 33, 7, pitch)
            nz, rgb0, a0, a255, hsh = model(mixed, pitch, 33, 7, 190, 57)
            check('a mixed sprite at an offset with a real pitch: counts and hash equal the model (rows follow the pitch, not the width)',
                  f'rect=33,7 190x57 nz={nz} rgb0={rgb0} a0={a0} a255={a255} fnv={hsh:x}' in out, out)
            clear = bytearray(pitch * 480)
            out = run(clear, 64, 32, 0, 0, pitch, fmt=22)
            nz, rgb0, a0, a255, hsh = model(clear, pitch, 0, 0, 64, 32)
            check('X8R8G8B8 is read too; a fully transparent rect shows a0 = w*h and nz = 0', f'nz=0 rgb0=0 a0={64 * 32} a255=0 fnv={hsh:x}' in out and nz == 0, out)
            check('no line without the key, with key 0, for a read-only lock, a 16-bit format, a 31x32 rect (under 1024 pixels) or no mirror',
                  all('[d9-stat]' not in o for o in (
                      run(black, 64, 64, 0, 0, pitch, key=None), run(black, 64, 64, 0, 0, pitch, key='0'), run(black, 64, 64, 0, 0, pitch, ro=1),
                      run(black, 64, 64, 0, 0, pitch, fmt=23), run(black, 31, 32, 0, 0, pitch), run(black, 64, 64, 0, 0, pitch, null=True))))
            out = run(black, 64, 64, 0, 0, pitch, reps=450)
            check('the first 400 writes are logged and no more; the surface is not changed (xor of the bytes as before)',
                  out.count('[d9-stat]') == 400 and '#400 ' in out and '#401 ' not in out and 'xor=0' in out, out[-300:])
        moved = t / 'moved'
        (moved / 'dxmt/src/d3d9').mkdir(parents=True)
        mf = moved / 'dxmt/src/d3d9/d3d9_surface.cpp'
        mf.write_text(target.read_text().replace('  d9LogLockPolicy(m_desc, m_locked_w, m_locked_h, m_locked_readonly, m_locked_no_dirty_update, defer_no_dirty);',
                                                 '  d9LogLockPolicy(m_desc, m_locked_w, m_locked_h, m_locked_readonly, m_locked_no_dirty_update, defer_no_dirty, 0);'))
        r3 = subprocess.run([sys.executable, str(script)], cwd=moved, capture_output=True, text=True)
        check('a moved anchor refuses the patch and leaves the file alone',
              r3.returncode != 0 and 'not found exactly once' in r3.stderr and 'madeira-bcd: d9-lock-stats' not in mf.read_text(), r3.stderr)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

print('PASS: DXMT D3D9 lock statistics' if ok else 'FAILED: DXMT D3D9 lock statistics')
sys.exit(0 if ok else 1)
