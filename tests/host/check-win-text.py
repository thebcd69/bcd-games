#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Window class names in the log, and an error box's details pane with env.MADEIRA_WIN_TEXT = 1 (round 624, agent B S6).

FINAL FANTASY VII's launcher (MMJZ8K, build 622) showed a WinForms ThreadExceptionDialog and the log never said which exception: the details
sit in a read-only multi-line Edit control, and the [win-name] lines had class='' for every window. Wine's NtUserGetClassName returns the
character count and never sets the UNICODE_STRING's Length, so build/win32u-unix/driver_ios.c, which looped up to us.Length, printed no
class name at all (273,976 "[winios-tree] 0x... ''" lines in the archive).

Checks: the three class-name sites use the returned count; the [win-text] line is off unless MADEIRA_WIN_TEXT is exactly "1" (read once),
then only for a visible read-only multi-line Edit, only on the thread that owns it, once per control, 12 lines at most, 600 characters;
the list policy accepts 0/1, the catalog describes it (default off) and FINAL FANTASY VII's launcher row carries it from build 624.
"""
from pathlib import Path
import json, re

root = Path(__file__).resolve().parents[2]
drv = (root / 'build/win32u-unix/driver_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


check('the three class-name sites loop up to the count NtUserGetClassName returns (us.Length is never set)',
      'us.Length / (int)sizeof(WCHAR)' not in drv and 'kus.Length' not in drv
      and drv.count('if ((cn = NtUserGetClassName( hwnd, FALSE, &us )) > 0)') == 2
      and 'if ((kcn = NtUserGetClassName( kids[k], FALSE, &kus )) > 0)' in drv
      and drv.count('for (j = 0; j < cn && j < 63; j++)') == 2 and 'for (j = 0; j < kcn && j < 31; j++)' in drv)
block = drv[drv.index('static int text_on = -1;'):]
block = block[:block.index('"[win-text] #%u hwnd=%p parent=%p edit style=%08x len=%d text=\\"%s\\" rev=r624\\n"')]
check('the switch is read once and only the exact value 1 turns it on (default off)',
      drv.count('getenv( "MADEIRA_WIN_TEXT" )') == 1 and "text_on = e && e[0] == '1' && !e[1];" in block)
check('only a visible read-only multi-line Edit, on its own thread, once per control, 12 at most',
      'if (text_on && !strcmp( cls, "Edit" ) && text_n < ARRAY_SIZE(text_seen))' in block
      and '(wstyle & (WS_VISIBLE | 0x804)) == (WS_VISIBLE | 0x804)' in block
      and 'get_window_thread( hwnd, NULL ) == GetCurrentThreadId()' in block and 'static HWND text_seen[12];' in block
      and 'for (k = 0; k < text_n && text_seen[k] != hwnd; k++) ;' in block and 'text_seen[text_n++] = hwnd;' in block)
check('the text is asked for with WM_GETTEXT (a 100 ms timeout, abort if hung), bounded to 600 characters, line breaks shown as |',
      'NtUserMessageCall( hwnd, WM_GETTEXT, ARRAY_SIZE(buf) - 1, (LPARAM)buf, &p, NtUserSendMessageTimeout, FALSE );' in block
      and '{ .flags = SMTO_ABORTIFHUNG, .timeout = 100 }' in block and 'WCHAR buf[601];' in block
      and 'if (len < 0 || len > (LRESULT)ARRAY_SIZE(buf) - 1) len = 0;' in block and "'|'" in block)
check('[win-name] keeps its words (auto-tune rules read the text)',
      '"[win-name] #%u hwnd=%p class=\'%s\' text=\\"%s\\" rev=ml853\\n"' in drv)
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the list policy accepts MADEIRA_WIN_TEXT = 0 or 1', '"MADEIRA_WIN_TEXT",' in pol and 'case "MADEIRA_WIN_TEXT":' in pol
      and 'MADEIRA_WIN_TEXT is 0 or 1' in pol)
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog describes it, default off', '"env.MADEIRA_WIN_TEXT": {"category": "Windows, display & input"' in gen
      and re.search(r'ConfigOption\(key: "env\.MADEIRA_WIN_TEXT", title: "[^"]+", kind: \.bool, defaultValue: "0"', cat))
recs = json.loads((root / 'recommended/recommendations.json').read_text())
ff7 = [r for r in recs['rules'] if r['id'] == 'final-fantasy-vii-steam']
check('FINAL FANTASY VII\'s launcher row carries the switch from build 624',
      ff7 and {'line': 'env.MADEIRA_WIN_TEXT = 1', 'minBuild': 624} in ff7[0]['config']
      and ff7[0]['match']['programs'] == [{'name': 'ffvii_launcher.exe'}], ff7)
check('no other list row sets it', sum('MADEIRA_WIN_TEXT' in json.dumps(r['config']) for r in recs['rules']) == 1)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-win-text.py' in wf)
print('PASS: win-text' if ok else 'FAIL: win-text')
raise SystemExit(0 if ok else 1)
