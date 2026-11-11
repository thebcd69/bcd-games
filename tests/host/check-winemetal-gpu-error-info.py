#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""winemetal describes a failed command buffer so a culprit can be told from a victim (diagnostic, log only; night round 2026-11-11, T3 S14).

Sekiro (FB6729, ZA4PSP, build 630) stalls 10-33 s while every command buffer fails with kIOGPUCommandBufferCallbackErrorInnocentVictim and
the buffer that caused the recovery is in no log. tools/patch-winemetal-gpu-error-info.py makes _MTLCommandBuffer_error (called once for each
failed buffer) print "[gpu-error] madeira-bcd #N (victim|other): code= domain= underlying= label= gpu=[,] kernel=[,] thermal= low-power= errors=
victims= since-first=" for the first 8 errors and every 64th after. This check applies it to a copy of the pinned winemetal_unix.c (or
$DXMT_SRC_DIR), reads the result, and runs the helper on the host (Objective-C, a fake command buffer, real NSError objects). SKIP of the
source part when no dxmt sources exist.
"""
import os, pathlib, shutil, subprocess, sys, tempfile


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


script = R / 'tools/patch-winemetal-gpu-error-info.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it with the other winemetal patches (not a tools/patch-dxmt-*.py: the i386 farm key is not touched), before the farm key',
      'python3 tools/patch-winemetal-gpu-error-info.py' in wf
      and wf.index('python3 tools/patch-winemetal-null-fragment.py') < wf.index('python3 tools/patch-winemetal-gpu-error-info.py') < wf.index('name: i386 farm revision'))
check('CI runs this check', 'python3 tests/host/check-winemetal-gpu-error-info.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if not (src / 'winemetal/unix/winemetal_unix.c').exists():
    alt = pathlib.Path.home() / 'madeira-bcd/dxmt/src'
    src = alt if (alt / 'winemetal/unix/winemetal_unix.c').exists() else src
target = pinned(src / 'winemetal/unix/winemetal_unix.c')
if target.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/winemetal/unix').mkdir(parents=True)
        shutil.copy(target, t / 'dxmt/src/winemetal/unix/winemetal_unix.c')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned source and a second run says "already patched"',
              r1.returncode == 0 and 'describes' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        s = (t / 'dxmt/src/winemetal/unix/winemetal_unix.c').read_text()
        a = s.index('madeira_gpu_error_note(id<MTLCommandBuffer> cb, NSError *err) {')
        helper = s[s.rindex('static void\n', 0, a):s.index('\n}\n', a) + 3]
        fn = s[s.index('_MTLCommandBuffer_error(void *obj) {'):]
        fn = fn[:fn.index('\n}\n')]
        check('_MTLCommandBuffer_error still returns the same NSError, fetched once, and only then describes it (only when there is one)',
              fn.count('[(id<MTLCommandBuffer>)params->handle error]') == 1 and 'params->ret = (obj_handle_t)err;' in fn
              and fn.index('params->ret = (obj_handle_t)err;') < fn.index('madeira_gpu_error_note(') and 'if (err)' in fn and 'return STATUS_SUCCESS;' in fn)
        check('the helper only reads (no stores into the buffer or the error; the counters are its own statics)',
              'setLabel' not in helper and 'commit' not in helper and 'static unsigned total, victims;' in helper)
        prog = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <time.h>
''' + helper + r'''
@interface FakeCB : NSObject
@property(copy) NSString *label;
@end
@implementation FakeCB
- (double)GPUStartTime { return 0.0; }
- (double)GPUEndTime { return 0.0; }
- (double)kernelStartTime { return 12.5; }
- (double)kernelEndTime { return 12.75; }
@end
int main(void) { @autoreleasepool {
  FakeCB *cb = [FakeCB new];
  NSError *under = [NSError errorWithDomain:@"IOGPUCommandQueueErrorDomain" code:5 userInfo:nil];
  NSError *victim = [NSError errorWithDomain:@"MTLCommandBufferErrorDomain" code:1 userInfo:@{
      NSLocalizedDescriptionKey: @"Discarded (victim of GPU error/recovery) (00000005:kIOGPUCommandBufferCallbackErrorInnocentVictim)",
      NSUnderlyingErrorKey: under}];
  NSError *culprit = [NSError errorWithDomain:@"MTLCommandBufferErrorDomain" code:3 userInfo:@{
      NSLocalizedDescriptionKey: @"Caused GPU Timeout Error (00000002:kIOGPUCommandBufferCallbackErrorTimeout)"}];
  cb.label = @"frame 15231";
  madeira_gpu_error_note((id<MTLCommandBuffer>)cb, culprit);
  madeira_gpu_error_note((id<MTLCommandBuffer>)cb, victim);
  cb.label = nil;
  for (int i = 0; i < 200; i++) madeira_gpu_error_note((id<MTLCommandBuffer>)cb, victim);
} return 0; }
'''
        (t / 'g.m').write_text(prog)
        b = subprocess.run(['clang', '-x', 'objective-c', '-fblocks', '-w', '-framework', 'Foundation', '-framework', 'Metal', '-o', str(t / 'g'), str(t / 'g.m')],
                           capture_output=True, text=True)
        if b.returncode != 0:
            print('skip: the helper did not build on this host: ' + b.stderr.strip()[-300:])
        else:
            r = subprocess.run([str(t / 'g')], capture_output=True, text=True)
            lines = [l for l in r.stderr.splitlines() if l.startswith('[gpu-error]')]
            check('the first error is a culprit-shaped one ("other", code 3, the label, the GPU times 0 = never ran, the kernel times) with the thermal state and no victims yet',
                  len(lines) > 0 and lines[0].startswith('[gpu-error] madeira-bcd #1 (other): code=3 domain=MTLCommandBufferErrorDomain underlying=-1 label=frame 15231 '
                                                         'gpu=[0.000,0.000] kernel=[12.500,12.750] thermal=') and ' errors=1 victims=0 since-first=0.' in lines[0], r.stderr[:600])
            check('the second is a victim with the underlying IOGPU code 5 and the running counts; a buffer with no label reads (none)',
                  len(lines) > 2 and lines[1].startswith('[gpu-error] madeira-bcd #2 (victim): code=1 domain=MTLCommandBufferErrorDomain underlying=5 label=frame 15231 ')
                  and ' errors=2 victims=1 since-first=' in lines[1] and ' #3 (victim)' in lines[2] and ' label=(none) ' in lines[2], r.stderr[:900])
            check('the first 8 errors are logged, then every 64th (#64, #128, #192 of 202): 11 lines, the last at #192',
                  len(lines) == 11 and ' #64 (' in lines[8] and ' #128 (' in lines[9] and ' #192 (' in lines[10] and ' errors=192 victims=191 ' in lines[10], '\n'.join(lines))
            check('the thermal state and the low-power flag are named', all((' thermal=' in l and ' low-power=' in l) for l in lines))
        moved = t / 'moved'
        (moved / 'dxmt/src/winemetal/unix').mkdir(parents=True)
        mf = moved / 'dxmt/src/winemetal/unix/winemetal_unix.c'
        mf.write_text(target.read_text().replace('  params->ret = (obj_handle_t)[(id<MTLCommandBuffer>)params->handle error];\n',
                                                 '  params->ret = (obj_handle_t)[(id<MTLCommandBuffer>)params->handle error]; /* x */\n'))
        r3 = subprocess.run([sys.executable, str(script)], cwd=moved, capture_output=True, text=True)
        check('a moved anchor refuses the patch and leaves the file alone',
              r3.returncode != 0 and 'not found exactly once' in r3.stderr and 'madeira-bcd: gpu-error info' not in mf.read_text(), r3.stderr)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

print('PASS: winemetal GPU error info' if ok else 'FAILED: winemetal GPU error info')
sys.exit(0 if ok else 1)
