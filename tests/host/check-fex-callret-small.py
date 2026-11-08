#!/usr/bin/env python3
"""Check the opt-in small call-ret region overlay (tools/patch-fex-ios-callret-small.py).

Needs the FEX submodule and a host C++ compiler; no Wine, FEX build or game.
1. On the pin's tree after every overlay tools/build-xtajit64.sh applies before
   it (and on the older-pin chain's output), the overlay applies, is
   idempotent, and refuses a moved anchor or a partial overlay.
2. No code in Core.cpp or CallRetStack.h still sizes the region with the 8 MiB
   constant or places the default location with CALLRET_DEFAULT_OFFSET.
3. The patched MADEIRA_FEX_CALLRET_RESERVE_KB parsing (Module.cpp) and the
   patched CallRetStackBytes/CallRetDefaultOffset (InternalThreadState.h),
   compiled on the host from the patched text: unset, 0, below 64, 8192 or
   more, or the stack kept (MADEIRA_FEX_CALLRET_ELIDE=0) give today's 8 MiB
   and 4 MiB; 64 gives a 64 KiB reservation (56 KiB region, default in its
   middle), N rounds up to 64 KiB; every region with its two guard pages is a
   whole number of 64 KiB granules and its default location lies inside it.
4. Build wiring: applied after the alias-capacity overlay and before the
   build, every patched file restored after it; CI host step and module marker;
   catalog entry; list policy name and range.
"""
from pathlib import Path
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ev = load('fexpatch_eval', root / 'tests/host/fexpatch_eval.py')
overlay = load('callret_small', root / 'tools/patch-fex-ios-callret-small.py')

HEADER = 'FEXCore/include/FEXCore/Debug/InternalThreadState.h'
MODULE = 'Source/Windows/ARM64EC/Module.cpp'
CORE = 'FEXCore/Source/Interface/Core/Core.cpp'
STACK = 'Source/Windows/Common/CallRetStack.h'
EMITTER = 'FEXCore/Source/Interface/Core/ArchHelpers/Arm64Emitter.cpp'
assert set(overlay.EDITS) == {HEADER, MODULE, CORE, STACK, EMITTER}

build = (root / 'tools/build-xtajit64.sh').read_text()
APPLY = 'python3 "$R/tools/patch-fex-ios-callret-small.py" "$R/FEX"\n'


def after_pin_chain():
    """The overlays build-xtajit64.sh runs on every pin (after the older-pin `fi`), before this one."""
    gate = build.index('if grep -q "' + ev.PIN_MARKER + '"')
    start = build.index('\nfi\n', gate) + len('\nfi\n')
    stop = build.index(APPLY)
    chain = []
    for line in build[start:stop].splitlines():
        line = line.strip()
        if line.startswith('python3 "$R/tools/patch-fex-ios-'):
            chain.append(line.split(' || ')[0])
    return chain


def apply_chain(fex, chain):
    for line in chain:
        args = shlex.split(line.replace('$R/FEX', str(fex)).replace('$R', str(root)))
        subprocess.run([sys.executable] + args[1:], check=True, capture_output=True)


def pin_tree(tmp):
    fex = Path(tmp) / 'FEX'
    for rel in ev.OVERLAY_TREES:
        shutil.copytree(root / 'FEX' / rel, fex / rel)
    return fex


# 1. The overlay on its real input (the pin + the after-pin overlays) and on the older-pin chain's output.
inputs = {}
with tempfile.TemporaryDirectory() as tmp:
    chain = after_pin_chain()
    assert any('alias-capacity' in line for line in chain), chain
    fex = pin_tree(tmp)
    if ev.PIN_MARKER in (fex / MODULE).read_text():
        apply_chain(fex, chain)
        inputs['pin'] = {rel: (fex / rel).read_text() for rel in overlay.EDITS}
    old, applied = ev.chain_tree(root, Path(tmp) / 'old')
    apply_chain(old, chain)
    inputs['older pin'] = {rel: (old / rel).read_text() for rel in overlay.EDITS}
assert inputs

for label, before in inputs.items():
    assert 'callret-elide-v1 madeira-bcd' in before[MODULE], label
    patched = overlay.patch(before)
    assert all(patched[rel] != before[rel] for rel in overlay.EDITS), label
    assert overlay.patch(patched) == patched, f'{label}: not idempotent'
    assert not overlay.leftover(patched), (label, overlay.leftover(patched))
    for rel, edits in overlay.EDITS.items():
        for old_text, new_text in edits:
            moved = dict(before)
            cut = re.search(r'[A-Za-z0-9_]', old_text).start() + 1   # split the anchor's first word
            moved[rel] = moved[rel].replace(old_text, old_text[:cut] + ' ' + old_text[cut:], 1)
            try:
                overlay.patch(moved)
            except ValueError:
                pass
            else:
                raise AssertionError(f'{label} {rel}: moved anchor accepted')
        if len(edits) > 1:
            partial = dict(patched)
            partial[rel] = partial[rel].replace(edits[0][1], edits[0][0], 1)
            try:
                overlay.patch(partial)
            except ValueError:
                pass
            else:
                raise AssertionError(f'{label} {rel}: partial overlay accepted')
    assert overlay.MARKER in patched[MODULE]

# The tool itself: refuses a tree without the elided stack, applies and re-applies on the real one.
with tempfile.TemporaryDirectory() as tmp:
    fex = Path(tmp) / 'FEX'
    before = next(iter(inputs.values()))
    for rel, text in before.items():
        (fex / rel).parent.mkdir(parents=True, exist_ok=True)
        (fex / rel).write_text(text)
    tool = [sys.executable, str(root / 'tools/patch-fex-ios-callret-small.py'), str(fex)]
    assert subprocess.run(tool, capture_output=True, text=True).returncode == 0
    first = {rel: (fex / rel).read_text() for rel in overlay.EDITS}
    assert subprocess.run(tool, capture_output=True, text=True).returncode == 0
    assert first == {rel: (fex / rel).read_text() for rel in overlay.EDITS}, 'second run changed the tree'
    (fex / MODULE).write_text(before[MODULE].replace('callret-elide-v1 madeira-bcd', 'callret-elide-vX'))
    for rel in overlay.EDITS:
        if rel != MODULE:
            (fex / rel).write_text(before[rel])
    assert subprocess.run(tool, capture_output=True, text=True).returncode != 0, 'a tree without the elided stack was patched'

# 2. Every remaining use of the constants in the two files is a comment.
patched = overlay.patch(next(iter(inputs.values())))
for rel in (CORE, STACK):
    code = ev.strip_comments(patched[rel])
    assert 'CALLRET_STACK_SIZE' not in code and 'CALLRET_DEFAULT_OFFSET' not in code, rel
    assert 'FEXCore::Core::CallRetStackBytes()' in code, rel
stack = ev.strip_comments(patched[STACK])
for needle in ('CallRetStackAllocSize = FEXCore::Core::CallRetStackBytes() + 2 * FEXCore::Utils::FEX_PAGE_SIZE',
               'VirtualName("FEXMem_CallRetStacks", CallRetStackAlloc, CallRetStackAllocSize)',
               'VirtualTHPControl(CallRetStackAlloc, CallRetStackAllocSize,',
               'VirtualAlloc(Thread->CallRetStackBase, FEXCore::Core::CallRetStackBytes(), MEM_COMMIT',
               'ZeroScrub(Thread->CallRetStackBase, FEXCore::Core::CallRetStackBytes())',
               'Base + FEXCore::Core::CallRetDefaultOffset()'):
    assert needle in stack, needle
core = ev.strip_comments(patched[CORE])
assert 'VirtualDontNeed(Thread->CallRetStackBase, FEXCore::Core::CallRetStackBytes())' in core
assert 'crend = crbase + FEXCore::Core::CallRetStackBytes()' in core
assert 'default_loc = crbase + FEXCore::Core::CallRetDefaultOffset()' in core
assert 'CREnd = CRBase + FEXCore::Core::CallRetStackBytes()' in core

# 3. The patched parsing and helpers, compiled on the host from the patched text.
module = patched[MODULE]
m = re.search(r'\n(  \{\n    /\* callret-small-v1 madeira-bcd:.*?\n  \}\n)#endif\n', module, re.S)
assert m, 'the Module.cpp block'
block = m.group(1)
header = patched[HEADER]
h = re.search(r'\n(inline size_t CallRetStackBytes\(\) \{.*?\n\}\n\ninline size_t CallRetDefaultOffset\(\) \{.*?\n\}\n)', header, re.S)
assert h, 'the InternalThreadState.h helpers'
size = int(re.search(r'CALLRET_STACK_SIZE \{(0x[0-9a-f]+)\}', header).group(1), 16)
default = int(re.search(r'CALLRET_DEFAULT_OFFSET \{(0x[0-9a-f]+)\}', header).group(1), 16)
assert size == 8 << 20 and default == 4 << 20
program = f'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define FEX_IOS_HOST 1
#define ARCHITECTURE_arm64ec 1
extern "C" uint32_t IosCallRetReserveBytes = 0;
extern "C" uint32_t IosCallRetStackElided = 0;
namespace FEXCore::Utils {{ constexpr uint64_t FEX_PAGE_SIZE = 4096; }}
namespace LogMan::Msg {{ template <typename... A> void IFmt(const char*, A...) {{}} }}
namespace FEXCore::Core {{
struct InternalThreadState {{
  static constexpr size_t CALLRET_STACK_SIZE {{{size:#x}}};
  static constexpr size_t CALLRET_DEFAULT_OFFSET {{{default:#x}}};
}};
{h.group(1)}
}}
int main(int argc, char** argv) {{
  IosCallRetStackElided = (uint32_t)atoi(argv[1]);
{block}
  printf("%llu %llu\\n", (unsigned long long)FEXCore::Core::CallRetStackBytes(),
         (unsigned long long)FEXCore::Core::CallRetDefaultOffset());
  return 0;
}}
'''
cxx = shutil.which('clang++') or shutil.which('c++')
assert cxx, 'a host C++ compiler is needed'
with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'parse.cpp'
    exe = Path(tmp) / 'parse'
    src.write_text(program)
    r = subprocess.run([cxx, '-std=gnu++20', '-Wall', '-Werror', '-o', str(exe), str(src)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]

    def run(value, elided=1):
        env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_FEX_CALLRET_RESERVE_KB'}
        if value is not None:
            env['MADEIRA_FEX_CALLRET_RESERVE_KB'] = value
        out = subprocess.run([str(exe), str(elided)], env=env, capture_output=True, text=True, check=True).stdout.split()
        return int(out[0]), int(out[1])

    today = (size, default)
    for value in (None, '', '0', '1', '16', '63', '-64', '8192', '9000', '65536', 'abc'):
        assert run(value) == today, value
    assert run('64', elided=0) == today and run('1024', elided=0) == today, 'kept stack must keep 8 MiB'
    page = 4096
    for kb, total in ((64, 64 << 10), (65, 128 << 10), (100, 128 << 10), (128, 128 << 10), (1000, 1024 << 10),
                      (8191, 8192 << 10)):
        got_size, got_default = run(str(kb))
        assert got_size == total - 2 * page, (kb, hex(got_size))
        assert (got_size + 2 * page) % (64 << 10) == 0
        assert got_default == got_size // 2 and got_default % 16 == 0
        # GetInfoThread: [Base - page, Base + size + page) holds the default location.
        base = 0x7d00001000
        alloc_base, alloc_end, location = base - page, base + got_size + page, base + got_default
        assert alloc_base <= base <= location < base + got_size < alloc_end
        assert alloc_end - alloc_base == total
    assert run('64') == ((64 << 10) - 2 * page, ((64 << 10) - 2 * page) // 2)

# 4. Build wiring.
assert build.count(APPLY) == 1
i = build.index(APPLY)
assert build.index('python3 "$R/tools/patch-fex-ios-alias-capacity.py" "$R/FEX"') < i < build.index('\nbuild\n', i)
assert build.rindex('\nfi\n', 0, i) > build.index('if grep -q "' + ev.PIN_MARKER + '"'), 'must run on every pin'
assert ' || ' not in build[i:build.index('\n', i)], 'an opt-in key the lists rely on must not be dropped silently'
restore = [line for line in build.splitlines() if line.startswith('git -C FEX checkout --')]
assert len(restore) == 1 and all(f' {rel}' in restore[0] for rel in overlay.EDITS), 'every patched file must be restored'
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
assert 'run: python3 tests/host/check-fex-callret-small.py' in workflow
assert "b'callret-small-v1 madeira-bcd' not in module" in workflow
catalog = (root / 'build/tools/gen-config-catalog.py').read_text()
assert '"env.MADEIRA_FEX_CALLRET_RESERVE_KB"' in catalog and '"tools/patch-fex-ios-callret-small.py"' in catalog
assert 'key: "env.MADEIRA_FEX_CALLRET_RESERVE_KB"' in (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
assert '"MADEIRA_FEX_CALLRET_RESERVE_KB",' in policy and '(64...8191).contains(n)' in policy

print(f'call-ret small region: overlay idempotent on {", ".join(inputs)} after {len(after_pin_chain())} after-pin overlays, '
      'drift and partial refused, no 8 MiB use left in Core.cpp/CallRetStack.h; host-compiled parsing: unset/0/<64/>=8192/'
      'stack kept = 8 MiB + 4 MiB, 64 KB = one 64 KiB granule (56 KiB region, middle default), N rounds up to 64 KiB; '
      'wired after alias-capacity, restored, CI, catalog, list policy')
