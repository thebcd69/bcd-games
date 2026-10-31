#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 625 (the owner's item 29; Discord cluster W: Fallout 4, Resident Evil 7, Metro Exodus, Dying Light): a game whose display mode is
"Windowed" gets a Windows window with a title bar, and in a desktop session that window can be larger than BCD's screen, so part of the
picture is off it. Once the game has drawn and its swapchain window has stayed so for 5 s, the log says so once and the session line
says for 8 s what to choose in the game (env.MADEIRA_WINDOWED_HINT = 0: the log line only). Text only.

  - app/Madeira/Winios/winios_windowed.h: the test itself, compiled here with cases;
  - build/win32u-unix/driver_ios.c: the WindowPosChanged and SetWindowStyle hooks tell Winios.m every change of a swapchain window,
    before the log budget is asked (a change that is not logged still counts);
  - app/Madeira/Winios/Winios.m: the style and the outer size per slot, cleared when a slot is taken and at a session's start; the
    answer only in a desktop session (in a game session only the swapchain's picture is shown, never a frame);
  - app/Madeira/Library.swift: the watcher's guards, the 5 s, the once per start, the 8 s line, the reset at each start;
  - the settings catalog (bool, default 1) and CI.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


hdr = root / 'app/Madeira/Winios/winios_windowed.h'
check('the test is pure C with no Apple or Wine header', hdr.exists() and '#include' not in hdr.read_text()
      and '#define WINIOS_WS_CAPTION 0x00C00000u' in hdr.read_text())

# --- 1. The test, compiled on the host --------------------------------------------------------------------------------------------
cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
check('a C compiler is at hand', cc is not None)
if cc:
    cases = [
        # (style, window w, h, screen w, h, expected, what)
        (0x16CF0000, 1940, 1110, 1424, 656, 1, "Resident Evil 7's shape: a title-bar window of 1920x1080 on a 1424x656 screen"),
        (0x16CF0000, 1432, 690, 1424, 656, 1, "a title-bar window whose client is the screen: the frame and the bar push it past the edges"),
        (0x16000000, 1424, 656, 1424, 656, 0, 'borderless at the screen size (MGS4 after its launcher restyled the window)'),
        (0x16000000, 2560, 1440, 1424, 656, 0, 'borderless and larger: no title bar to blame (the picture is scaled, not cut)'),
        (0x16CF0000, 800, 600, 1424, 656, 0, 'a small title-bar window that fits (a launcher or a settings tool)'),
        (0x16CF0000, 1424, 656, 1424, 656, 0, 'a title-bar window exactly the screen size'),
        (0x16CF0000, 1425, 600, 1424, 656, 1, 'one pixel wider than the screen'),
        (0x16CF0000, 1000, 657, 1424, 656, 1, 'one pixel taller than the screen'),
        (0x16800000, 1940, 1110, 1424, 656, 0, 'WS_BORDER alone is not a title bar'),
        (0x16400000, 1940, 1110, 1424, 656, 0, 'WS_DLGFRAME alone is not a title bar'),
        (0x16CF0000, 0, 0, 1424, 656, 0, 'a window whose size is not known yet'),
        (0x16CF0000, 1940, 1110, 0, 0, 0, 'a screen whose size is not known'),
        (0x16CF0000, -5, 1110, 1424, 656, 0, 'a negative size'),
    ]
    body = '\n'.join('    n += check(%d, winios_window_too_big(0x%08xu, %d, %d, %d, %d), %d);' % (i, s, w, h, sw, sh, e)
                     for i, (s, w, h, sw, sh, e, _) in enumerate(cases))
    prog = f'''#include <stdio.h>
#include "{hdr}"
static int check(int i, int got, int want) {{ if (got != want) {{ printf("case %d: got %d want %d\\n", i, got, want); return 1; }} return 0; }}
int main(void) {{
    int n = 0;
{body}
    printf("%d failed\\n", n);
    return n != 0;
}}
'''
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / 't.c'
        c.write_text(prog)
        exe = Path(d) / 't'
        b = subprocess.run([cc, '-std=c99', '-Wall', '-Werror', '-o', str(exe), str(c)], capture_output=True, text=True)
        check('winios_windowed.h compiles with -Wall -Werror', b.returncode == 0, b.stderr[-800:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check('the %d cases give their answers: %s' % (len(cases), '; '.join(c[6] for c in cases)), r.returncode == 0, r.stdout[-800:])

# --- 2. The driver hooks -----------------------------------------------------------------------------------------------------------
drv = (root / 'build/win32u-unix/driver_ios.c').read_text()
check('driver_ios.c declares Winios.m\'s note weakly',
      'extern void winios_swapchain_note_frame( int slot, unsigned int style, int w, int h, int shown ) __attribute__((weak));' in drv)
style_fn = drv[drv.index('static void winios_drv_set_window_style('):]
style_fn = style_fn[:style_fn.index('\n}\n')]
check('a style change of a swapchain window is told before the log budget is asked (the size as it was)',
      'if (slot && offset == GWL_STYLE && winios_swapchain_note_frame)' in style_fn
      and 'winios_swapchain_note_frame( slot, (unsigned)style->styleNew, -1, -1, -1 );' in style_fn
      and style_fn.index('winios_swapchain_note_frame( slot') < style_fn.index('winios_swap_win_take( slot, WINIOS_SWAP_STYLE, &seq )'))
pos_fn = drv[drv.index('static void winios_swap_win_pos('):]
pos_fn = pos_fn[:pos_fn.index('\n}\n')]
check('every position change is told with the style, the outer size and whether the window is shown, before the log budget',
      'winios_swapchain_note_frame( slot, (unsigned)get_window_long( hwnd, GWL_STYLE ), (int)(r->window.right - r->window.left),' in pos_fn
      and '(int)(r->window.bottom - r->window.top), is_window_visible( hwnd ) ? 1 : -1 );' in pos_fn
      and pos_fn.index('winios_swapchain_note_frame(') < pos_fn.index('winios_swap_win_take( slot, WINIOS_SWAP_POS, &seq )'))

# --- 3. Winios.m -------------------------------------------------------------------------------------------------------------------
wm = (root / 'app/Madeira/Winios/Winios.m').read_text()
check('Winios.m includes the test', '#include "winios_windowed.h"' in wm)
note = wm[wm.index('void winios_swapchain_note_frame(int slot1, unsigned style, int w, int h, int shown) {'):]
note = note[:note.index('\n}\n')]
check('the note stores the style always, the size when given, the window to look at only when shown',
      'atomic_store_explicit(&g_swap_style[slot1 - 1], style, memory_order_relaxed);' in note
      and 'if (w >= 0 && h >= 0)' in note and 'if (shown == 1) atomic_store_explicit(&g_swap_last_shown, slot1, memory_order_relaxed);' in note)
take = wm[wm.index('void winios_note_swapchain_hwnd(void *hwnd) {'):]
take = take[:take.index('\n}\n')]
check('a slot taken by a new window starts with no style and no size',
      'atomic_store(&g_swap_style[i], 0);' in take and 'atomic_store(&g_swap_size[i], 0);' in take)
reset = wm[wm.index('static void winios_swapchain_hwnds_reset(void) {'):]
reset = reset[:reset.index('\n}\n')]
check('a session start forgets the window to look at', 'atomic_store(&g_swap_last_shown, 0);' in reset)
ans = wm[wm.index('int winios_windowed_too_big(int *win_w, int *win_h, int *scr_w, int *scr_h) {'):]
ans = ans[:ans.index('\n}\n')]
check('the answer: only in a desktop session, only for a window still in the table, the test on its style and size against the screen',
      'if (!g_compositor_view || g_comp_game) return 0;' in ans
      and '!atomic_load_explicit(&g_swap_hwnds[slot1 - 1], memory_order_relaxed)) return 0;' in ans
      and 'winios_screen_size(&sw, &sh);' in ans
      and 'return winios_window_too_big(style, (int)(size >> 32), (int)(size & 0xffffffffu), sw, sh);' in ans
      and wm.index('static BOOL g_comp_game;') < wm.index('int winios_windowed_too_big('))
hh = (root / 'app/Madeira/Winios/Winios.h').read_text()
check('Winios.h gives the app the answer', 'int winios_windowed_too_big(int *win_w, int *win_h, int *scr_w, int *scr_h);' in hh)

# --- 4. Library.swift --------------------------------------------------------------------------------------------------------------
src = (root / 'app/Madeira/Library.swift').read_text()
fn = src[src.index('    private func watchWindowedHint() {'):]
fn = fn[:fn.index('\n    }\n') + 7]
check('called once a poll, right after the no-input hint, while the Wine process runs',
      src.count('watchWindowedHint()') == 2 and '            watchNoInputHint()\n            watchWindowedHint()\n' in src)
check('once per start, after 60 presents, not while launching',
      'guard !launching, let id = current, windowedHintFor != id, madeira_get_present_count() > launchPresent + 60 else { return }' in fn
      and 'windowedHintFor = id' in fn)
check('the window must stay so for 5 s (a start often passes through such a window), and the session line must be free',
      'guard winios_windowed_too_big(&w, &h, &screenW, &screenH) != 0 else { windowedSince = nil; return }' in fn
      and 'guard let since = windowedSince else { windowedSince = Date(); return }' in fn
      and 'guard Date().timeIntervalSince(since) >= 5, sessionMessage.isEmpty else { return }' in fn)
check('the log line first, whatever follows; the screen text only with the key on; gone after 8 s unless replaced; no setting changed',
      fn.index('LogStore.shared.log("[window] hint: the game') < fn.index('guard MadeiraConfig.flag("MADEIRA_WINDOWED_HINT") else { return }')
      < fn.index('sessionMessage = words')
      and 'DispatchQueue.main.asyncAfter(deadline: .now() + 8)' in fn and 'if self?.sessionMessage == words { self?.sessionMessage = "" }' in fn
      and not re.search(r'MadeiraConfig\.set|UserDefaults|entry\.\w+\s*=', fn))
check('the words name the choice and the screen size',
      '"The window is larger than the screen. In the game\'s display settings choose Fullscreen or Borderless, \\(width)x\\(height)."' in src)
check('each start resets the 5 s clock',
      'windowedSince = nil' in src[src.index('MetalBackedView.touchesSinceLaunch = 0'):][:200])

# --- 5. Catalog and CI -------------------------------------------------------------------------------------------------------------
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the key is in the settings catalog (bool, default 1)',
      re.search(r'"env\.MADEIRA_WINDOWED_HINT": \{[^}]*?"kind": "bool", "default": "1"', cat, re.S) is not None)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-windowed-hint.py' in wf)
print('PASS: windowed hint' if ok else 'FAIL: windowed hint')
raise SystemExit(0 if ok else 1)
