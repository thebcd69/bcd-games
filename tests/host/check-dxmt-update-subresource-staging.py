#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT UpdateSubresource(1) on a STAGING buffer is dropped instead of crashing the encode thread (night round 2026-11-11, G4 S12).

FIFA 23 (HPM5D7, KAXT96, build 626) died 22 s in on DXMT's encode thread at d3d11.dll+0x98818, the buffer lambda of UpdateSubresource1
reading an empty captured buffer (`ldr x19,[x8,#8]`, x8 = 0): StagingBuffer::buffer() is an empty Rc. tools/patch-dxmt-update-subresource-
staging.py takes the buffer before the staging allocation and the blit encoder switch and returns when it is empty, with a capped
[update-subresource-staging] log line. This check applies the patch to a copy of the pinned dxmt/src (or $DXMT_SRC_DIR) and reads the
result; check-dxmt-patch-chain.py applies and re-applies the whole chain. SKIP of that part when no dxmt sources exist.
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


script = R / 'tools/patch-dxmt-update-subresource-staging.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it, after the CopyStructureCount patch and before the i386 farm key',
      'python3 tools/patch-dxmt-update-subresource-staging.py' in wf
      and wf.index('python3 tools/patch-dxmt-copy-structure-count.py') < wf.index('python3 tools/patch-dxmt-update-subresource-staging.py')
      < wf.index('name: i386 farm revision'))
check('CI runs this check', 'python3 tests/host/check-dxmt-update-subresource-staging.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if not (src / 'd3d11/d3d11_context_impl.cpp').exists():
    alt = pathlib.Path.home() / 'madeira-bcd/dxmt/src'
    src = alt if (alt / 'd3d11/d3d11_context_impl.cpp').exists() else src
target = pinned(src / 'd3d11/d3d11_context_impl.cpp')
if target.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d11').mkdir(parents=True)
        shutil.copy(target, t / 'dxmt/src/d3d11/d3d11_context_impl.cpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned source and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        s = (t / 'dxmt/src/d3d11/d3d11_context_impl.cpp').read_text()
        fn = s[s.index('UpdateSubresource1(\n      ID3D11Resource *pDstResource'):]
        fn = fn[:fn.index('DiscardResource(')]
        guard = fn.index('if (!dst_rc)')
        check('the empty-buffer test comes before the staging allocation, the blit encoder switch and the command',
              fn.index('auto dst_rc = bindable->buffer();') < guard < fn.index('AllocateStagingBuffer(copy_len, 16)')
              < fn.index('SwitchToBlitEncoder(CommandBufferState::UpdateBlitEncoderActive)') < fn.index('EmitOP('), fn)
        check('the emitted command captures the buffer it tested', 'dst = std::move(dst_rc), copy_offset, copy_len' in fn)
        check('the encoder body is unchanged (a buffer to buffer copy of the staged bytes)',
              'cmd.type = WMTBlitCommandCopyFromBufferToBuffer;' in fn and 'cmd.src = staging_buffer;' in fn
              and 'cmd.dst = dst_buffer->buffer();' in fn and 'cmd.dst_offset = copy_offset + dst_offset;' in fn)
        check('the log line is capped (8, then powers of two) and says what the buffer is',
              '(n <= 8 || !(n & (n - 1)))' in fn and '[update-subresource-staging] skipped #' in fn and 'return;' in fn[guard:guard + 400])
        check('the dynamic-buffer paths above it are untouched (they return before the guard)',
              fn.index('GetDynamicBuffer(pDstResource') < guard and fn.count('Map(pDstResource') == 2)
        check('<atomic> is included for the counter', '#include <atomic>' in s)
        # a moved anchor refuses the patch and leaves the file alone
        moved = t / 'moved'
        (moved / 'dxmt/src/d3d11').mkdir(parents=True)
        mf = moved / 'dxmt/src/d3d11/d3d11_context_impl.cpp'
        mf.write_text(target.read_text().replace('auto [staging_buffer, offset] = AllocateStagingBuffer(copy_len, 16);',
                                                 'auto [staging_buffer, offset] = AllocateStagingBuffer(copy_len, 32);'))
        r3 = subprocess.run([sys.executable, str(script)], cwd=moved, capture_output=True, text=True)
        check('a moved anchor refuses the patch and leaves the file alone',
              r3.returncode != 0 and 'anchor not found once' in r3.stderr and 'madeira-bcd: UpdateSubresource on a staging buffer' not in mf.read_text(), r3.stderr)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

# The CI compiles only the 32-bit d3d11.dll; the 64-bit one is upstream's committed binary, and FIFA 23 is 64-bit. The patch reaches it
# through d3d11-src.dll (tools/build-d3d11-dll.sh, on a copy), which env.MADEIRA_D3D11_SRC = 1 selects.
sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll is built with the patch and refused without it',
      '"patch-dxmt-update-subresource-staging.py" "[update-subresource-staging] skipped #"' in sh
      and '(cd "$OUT/patchroot" && python3 "$R/tools/${BCD_PATCHES[$i]}") || fail' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries the patch',
      '"[update-subresource-staging] skipped #"' in wf[wf.index('for m in "[copy-structure-count] skipped #"'):][:600])
rules = (R / 'build/tools/autotune_rules.py').read_text()
blk = rules[rules.index('"id": "dxmt-update-subresource-staging-null"'):] if '"id": "dxmt-update-subresource-staging-null"' in rules else ''
blk = blk[:blk.index('\n    },\n')] if blk else ''
check('auto-tune answers the crash with d3d11-src.dll (no "fixed in" claim: the 64-bit default DLL is unchanged)',
      '"lines": ["env.MADEIRA_D3D11_SRC = 1"]' in blk and '"fixedIn"' not in blk.split('"evidence"')[0] and 'd3d11.dll+0x98818' in blk, blk[:300])
check('its fixture is there and proposes that line',
      (R / 'tests/fixtures/autotune/fifa-23-update-subresource-staging-2026-11-03.expect').is_file()
      and 'dxmt-update-subresource-staging-null' in (R / 'tests/fixtures/autotune/fifa-23-update-subresource-staging-2026-11-03.expect').read_text())
print('PASS: DXMT UpdateSubresource on a staging buffer' if ok else 'FAILED: DXMT UpdateSubresource on a staging buffer')
sys.exit(0 if ok else 1)
