#!/usr/bin/env python3
"""build/ntdll-unix/process_ios.c: the child-spawn log line names the child's Windows current directory; no Wine runs.

Terraria (Steam 105600, log YFYT0R, build 576) ends on its own "Asset could not be found: PixelShader" box. Which
directory a Steam-launched child starts in was in no log. spawn_process's one ERR line now ends in
`cwd=%s` with debugstr_us(&params->CurrentDirectory.DosPath). Log only: the call's other arguments, the thread
creation and the return value are unchanged. This check reads the C text: the line keeps its old fields in order,
the cwd field is last, and the number of conversions equals the number of arguments (a mismatch would print garbage
or crash inside the logger, in the one code path every child process takes).
"""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/process_ios.c').read_text()

start = src.index('ERR("spawn_process: creating child thread for')
depth = 0
end = start
for i in range(start, len(src)):
    c = src[i]
    if c == '(':
        depth += 1
    elif c == ')':
        depth -= 1
        if depth == 0:
            end = i
            break
call = src[start:end + 1]

fmt = re.search(r'ERR\("((?:[^"\\]|\\.)*)"', call).group(1)
assert fmt == ('spawn_process: creating child thread for %s '
               '(fd=%d, unixdir=%d, dup_unixdir=%d) cwd=%s\\n'), fmt

# The arguments after the format string, split at top-level commas.
rest = call[call.index('",', call.index('ERR("')) + 2:-1]
args, depth, cur = [], 0, ''
for c in rest:
    if c in '([':
        depth += 1
    elif c in ')]':
        depth -= 1
    if c == ',' and depth == 0:
        args.append(cur.strip())
        cur = ''
    else:
        cur += c
if cur.strip():
    args.append(cur.strip())

assert len(args) == len(re.findall(r'%[sd]', fmt)) == 5, args
assert args[0] == 'debugstr_us(&params->CommandLine)', args
assert args[1:4] == ['socketfd', 'unixdir', 'args->unixdir'], args
assert args[4] == 'debugstr_us(&params->CurrentDirectory.DosPath)', args

# Nothing else in the file may read the old text (log scanners live in tools/ and app/).
for folder in ('tools', 'app', 'tests'):
    for p in (root / folder).rglob('*'):
        if p.is_file() and p.suffix in ('.py', '.swift', '.m', '.h', '.c', '.sh'):
            try:
                text = p.read_text()
            except UnicodeDecodeError:
                continue
            if p.name != 'check-spawn-cwd-log.py' and 'creating child thread for' in text:
                # Round 624: auto-tune's signals are substrings of one line (nwjs-gpu-media-dlls-fatal reads the child's command line);
                # they stay in step as long as none names a field of the line's tail (fd=, unixdir=, dup_unixdir=, cwd=).
                if p.name in ('autotune_rules.py', 'AutoTuneRules.generated.swift'):
                    for m in re.finditer(r'creating child thread for', text):
                        near = text[m.start():m.start() + 200]
                        if any(f in near.split('\n')[0] for f in ('fd=', 'unixdir=', 'cwd=')):
                            raise SystemExit('%s: a signal reads the spawn line\'s tail; keep it in step with the cwd field' % p)
                    continue
                raise SystemExit('%s matches the spawn line; keep its pattern in step with the new field' % p)

print('check-spawn-cwd-log: ok (spawn line ends in cwd=%s, 5 conversions, 5 arguments)')
