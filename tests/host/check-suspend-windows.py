#!/usr/bin/env python3
"""Nothing between thread_suspend and thread_resume in the samplers may take a lock.

Build 476, RDR2 (2026-10-09 16:55:52): the guest RIP profile (ml979) suspended
every thread and called pthread_from_mach_thread_np with it still suspended.
That call takes libpthread's thread-list lock; a thread suspended while it held
the lock (starting or ending a thread) was never resumed, so the profile, the
[xp] probe, the stack dumps and every later thread start and exit blocked: the
game froze. The watchdog logged (malloc, Swift) with its thread suspended.

This check reads build/ntdll-unix/server_ios.c, finds every thread_suspend and
the standalone thread_resume that closes its window, and fails when the window
calls anything that can take a lock. It does the same for the Windows server's two
captures in build/wineserver/mach_ios.c (round 660), where a getenv or an fprintf
inside the halt froze the server (Requiem, Counter-Strike Nexon).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'build/ntdll-unix/server_ios.c'

FORBIDDEN = (
    'pthread_from_mach_thread_np', 'pthread_getname_np', 'pthread_kill',
    'wine_log_write', 'wine_ui_log', 'dprintf', 'fprintf', 'printf(', 'ERR(', 'WARN(', 'TRACE(',
    'malloc', 'calloc', 'realloc', 'free(', 'dladdr', 'ios_ts_sym', 'ios_ts_map_for_teb',
    'pthread_mutex_lock', 'os_unfair_lock_lock',
)
RESUME = re.compile(r'^\s*thread_resume\s*\(.*\);\s*$')
CALL = re.compile(r'\bthread_suspend\s*\(')


def windows(lines):
    """(suspend line, resume line) pairs, 1-based; resume is the first
    standalone thread_resume after the suspend (a resume inside a one-line
    failure branch does not end the window)."""
    out = []
    for i, line in enumerate(lines):
        if not CALL.search(line) or line.lstrip().startswith(('*', '/*', '//')):
            continue
        for j in range(i + 1, min(i + 200, len(lines))):
            if RESUME.match(lines[j]):
                out.append((i + 1, j + 1))
                break
        else:
            raise SystemExit(f'{SRC.name}:{i + 1}: thread_suspend without a thread_resume within 200 lines')
    return out


def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group(0).count('\n'), text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def main():
    raw = SRC.read_text().splitlines()
    code = strip_comments('\n'.join(raw)).splitlines()
    found = windows(code)
    if len(found) < 6:
        raise SystemExit(f'expected at least 6 suspend windows in {SRC.name}, found {len(found)}')
    bad = []
    for s, r in found:
        skip = set()
        # `kr = thread_suspend(...); if (kr != KERN_SUCCESS) { ...; return; }`:
        # nothing is suspended inside that failure branch.
        m = re.search(r'(\w+)\s*=\s*thread_suspend\s*\(', code[s - 1])
        if m and re.match(r'\s*if\s*\(\s*%s\s*!=\s*KERN_SUCCESS\s*\)\s*\{' % m.group(1), code[s]):
            depth, n = 0, s
            while True:
                depth += code[n].count('{') - code[n].count('}')
                skip.add(n)
                n += 1
                if depth <= 0:
                    break
        for n in range(s, r - 1):          # lines after the suspend, before the resume
            if n in skip:
                continue
            text = code[n]
            if n == s - 1:                  # the suspend line itself: only what follows the call
                text = text[CALL.search(text).end():]
            for f in FORBIDDEN:
                if f in text:
                    bad.append(f'{SRC.name}:{n + 1}: {f} inside the suspend window {s}-{r}')
    if bad:
        raise SystemExit('\n'.join(bad))

    # The two windows this check was written for.
    src = SRC.read_text()
    prof = src.index('static void ios_guest_rip_profile')
    body = src[prof:src.index('\n}\n', prof)]
    lookup = body.index('pt = pthread_from_mach_thread_np( tl[k] );')
    assert lookup < body.index('if (thread_suspend( tl[k] ) != KERN_SUCCESS) continue;'), \
        'ml979: the pthread lookup must come before the suspend'
    assert 'ios_ts_teb( pt )' in body
    dog = src.index('Suspend thread for consistent state reading')
    seg = src[dog:src.index('dispatch_after(', dog)]
    assert seg.index('thread_resume(wine_mach_thread);') < seg.index('wine_log_write("[Wine WATCHDOG %ds] PC='), \
        'watchdog: resume before logging'
    assert seg.count('thread_resume(') == 1, 'watchdog: exactly one resume'
    print(f'suspend windows: {len(found)} checked, none takes a lock')
    check_server_captures()


# madeira-bcd (round 660, M001): the Windows server's own captures (build/wineserver/mach_ios.c). Wine's threads are threads of
# this process, so a getenv (the environment lock), a config file read, an fprintf (stdio's lock) or a malloc while a thread is
# halted waits for ever when the halted thread holds that lock, and the server stops with it: Requiem 643J42, 66HCT0, AJEJWV and
# Counter-Strike Nexon YK0CTE froze with the server thread in getenv under ios_fill_thread_context. The persistent hold of
# ios_thread_mach_hold is not a momentary halt and is not checked here.
MACH = ROOT / 'build/wineserver/mach_ios.c'
MACH_FORBIDDEN = FORBIDDEN + ('getenv', 'madeira_cfg_get', 'ios_ctx_fp_enabled(', 'ios_real_suspend_enabled(', 'rs_flag_ok(',
                              'ios_real_suspend_stats(', 'rs_tick(')
MACH_FUNCTIONS = ('int ios_fill_thread_context(', 'int ios_apply_resume_context(')


def check_server_captures(path=MACH):
    raw = path.read_text()
    code = strip_comments(raw)
    bad = []
    for head in MACH_FUNCTIONS:
        start = code.find(head)
        if start < 0:
            raise SystemExit(f'{path.name}: {head} not found')
        body = code[start:code.index('\n}\n', start)]
        lines = body.splitlines()
        s = next((i for i, l in enumerate(lines) if CALL.search(l)), None)
        r = next((i for i in range(len(lines) - 1, -1, -1) if RESUME.match(lines[i])), None) if s is not None else None
        if s is None or r is None or r <= s:
            raise SystemExit(f'{path.name}: {head} has no thread_suspend ... thread_resume window')
        for n in range(s, r):
            text = lines[n][CALL.search(lines[n]).end():] if n == s else lines[n]
            for f in MACH_FORBIDDEN:
                if f in text:
                    bad.append(f'{path.name}: {head.split("(")[0].split()[-1]}: {f} inside the halt: {text.strip()[:90]}')
    if bad:
        raise SystemExit('\n'.join(bad))
    fill = code[code.find(MACH_FUNCTIONS[0]):]
    fill = fill[:fill.index('\n}\n')]
    halt = fill.index('if (thread_suspend( port ))')
    assert fill.index('ctx_fp = ios_ctx_fp_enabled();') < halt and fill.index('keep_hold = ios_pending_persistent_hold == thread') < halt, \
        'ios_fill_thread_context: the switches are read before the halt'
    assert fill.index('fprintf( stderr, "[ctx-frame] ml716') > fill.rindex('thread_resume( port );') \
        and fill.index('fprintf( stderr, "[srv-getctx] ml715') > fill.rindex('thread_resume( port );'), \
        'ios_fill_thread_context: its two lines are printed after the halt'
    print(f'{path.name}: the server\'s two captures read no switch and print nothing while a thread is halted')


if __name__ == '__main__':
    main()
