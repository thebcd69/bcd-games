#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""MADEIRA_WOW_HOLD_FOR (upstream Madeira v0.2.0 #549, BCD's form): the held 32-bit window is for the game's own programs; no Wine runs.

BCD's env.MADEIRA_WOW_PLACEHOLDER_SLOTS reserves a 32-bit window when the session starts. The first 32-bit program of the session takes it.
In a Dock session that is often Valve's SteamService.exe, started by the client seconds in, and the game's own 32-bit program, a minute
later, finds every candidate taken ("BOOT FAILED at stage 'guest-window-reserve'", 0xc0000017; GTA IV 97Y30K and 9P6C62, the Rockstar
chain in S59TH0). Now env.MADEIRA_WOW_HOLD_FOR (a Windows folder), or env.MADEIRA_WOW_HOLD_FOR_GAME = 1 (the folder BCD Dock already
exports as MADEIRA_STEAM_HOST_EXPECTED_INSTALL), makes the held placeholder adoptable only by a program whose image path lies inside it;
a program from elsewhere gets any other free slot, as before. Off unless a folder is named.

This check joins the two sides: process_ios.c's ios_child_image_path (X7, the accessor) compiled with the production child-slot table, and
virtual_ios.c's matcher compiled against it. The registry paths (adoption, the session-start parser, end to end with two processes) are
in check-wow-guard.py and check-lazy-windows.py.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
proc = (root / 'build/ntdll-unix/process_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(src, sig):
    a = src.index(sig)
    return src[a:src.index('\n}\n', a) + 3]


accessor = function(proc, 'const char *ios_child_image_path( void )')
table_decl = re.search(r'static struct \{ char name\[64\]; char path\[IOS_CHILD_PATH\];[^\n]*\} ios_child_slots\[IOS_CHILD_SLOTS\];', proc)[0]
matcher = function(virt, 'static int ios_wow_child_in_hold_folder(')
check('the accessor reads the calling boot thread\'s slot without a lock and returns NULL for a thread without a slot or an unused one',
      'int slot = ios_child_slot_current;' in accessor and 'if (slot < 0 || slot >= IOS_CHILD_SLOTS || !ios_child_slots[slot].used) return NULL;' in accessor and
      'return ios_child_slots[slot].path;' in accessor and 'pthread_mutex' not in accessor)
check('the accessor is the only new exported symbol of process_ios.c and virtual_ios.c declares it as that', proc.count('const char *ios_child_image_path( void )') == 1 and
      'extern const char *ios_child_image_path( void );   /* process_ios.c */' in matcher)
sess = function(virt, 'static void ios_wow_session_start(void)')
check('the session start reads the folder after the placeholders are reserved, only for SLOTS placeholders, never for a 32-bit main image or MADEIRA_WOW_PLACEHOLDERS=1',
      sess.index('ios_wow_reserve_placeholders( want );') < sess.index('getenv( "MADEIRA_WOW_HOLD_FOR" )') and
      'if (slots && !ios_main_image_i386 && ios_wow_env_ulong( "MADEIRA_WOW_PLACEHOLDERS", 0 ) != 1)' in sess)
check('MADEIRA_WOW_HOLD_FOR_GAME = 1 falls back to the Dock\'s MADEIRA_STEAM_HOST_EXPECTED_INSTALL, and only when no folder is named',
      'if ((!folder || !folder[0]) && ios_wow_env_ulong( "MADEIRA_WOW_HOLD_FOR_GAME", 0 ) == 1)' in sess and
      'folder = getenv( ios_wow_dock_folder_var );' in sess and
      'static const char ios_wow_dock_folder_var[] = "MADEIRA_STEAM_HOST_EXPECTED_INSTALL";' in virt)
check('a folder too long to match reliably leaves the window shared (as before)', 'if (folder[n]) { ios_wow_hold_for[0] = 0; return; }' in sess)

harness = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define IOS_CHILD_SLOTS 32
#define IOS_CHILD_PATH 260
@TABLE@
static _Thread_local int ios_child_slot_current = -1;
@ACCESSOR@
static char ios_wow_hold_for[260];
@MATCHER@
static void set_slot(int slot, const char *path) { ios_child_slots[slot].used = 1; snprintf(ios_child_slots[slot].path, IOS_CHILD_PATH, "%s", path); }
int main(void)
{
    const char *image;
    /* no slot for this thread */
    assert(ios_child_image_path() == NULL);
    set_slot(3, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto IV\\GTAIV.exe");
    ios_child_slot_current = 3;
    assert(!strcmp(ios_child_image_path(), "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto IV\\GTAIV.exe"));
    ios_child_slot_current = 4;   /* an unused slot */
    assert(ios_child_image_path() == NULL);
    ios_child_slot_current = 32; assert(ios_child_image_path() == NULL);
    ios_child_slot_current = -1;  assert(ios_child_image_path() == NULL);
    ios_child_slot_current = 3;

    /* no folder: every program is the game's */
    ios_wow_hold_for[0] = 0;
    assert(ios_wow_child_in_hold_folder(&image) == 1);
    strcpy(ios_wow_hold_for, "c:\\program files (x86)\\steam\\steamapps\\common\\grand theft auto iv\\");
    assert(ios_wow_child_in_hold_folder(&image) == 1 && strstr(image, "GTAIV.exe"));
    set_slot(5, "C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe");
    ios_child_slot_current = 5;
    assert(ios_wow_child_in_hold_folder(&image) == 0 && strstr(image, "SteamService.exe"));
    /* forward slashes, upper case, the \\?\ and \??\ prefixes */
    set_slot(6, "c:/PROGRAM FILES (X86)/Steam/steamapps/common/Grand Theft Auto IV/sub/x.exe"); ios_child_slot_current = 6;
    assert(ios_wow_child_in_hold_folder(&image) == 1);
    set_slot(7, "\\\\?\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto IV\\GTAIV.exe"); ios_child_slot_current = 7;
    assert(ios_wow_child_in_hold_folder(&image) == 1);
    set_slot(8, "\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto IV\\GTAIV.exe"); ios_child_slot_current = 8;
    assert(ios_wow_child_in_hold_folder(&image) == 1);
    /* a path shorter than the folder, a sibling folder that merely starts the same, an unknown child */
    set_slot(9, "C:\\Program Files (x86)\\Steam"); ios_child_slot_current = 9;
    assert(ios_wow_child_in_hold_folder(&image) == 0);
    set_slot(10, "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto IV Extra\\x.exe"); ios_child_slot_current = 10;
    assert(ios_wow_child_in_hold_folder(&image) == 0);
    ios_child_slot_current = -1;
    assert(ios_wow_child_in_hold_folder(&image) == 1 && !strcmp(image, "(unknown)"));
    puts("ok");
    return 0;
}
'''
harness = harness.replace('@TABLE@', table_decl).replace('@ACCESSOR@', accessor).replace('@MATCHER@', matcher)
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-Wno-unused-function',
                         '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production accessor (process_ios.c) and matcher (virtual_ios.c) compile together on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-2500:])
    if cc.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        check('no slot, unused slot, out-of-range slot: NULL; a slot: its path; the matcher: folder, slashes, case, prefixes, siblings, unknown',
              r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-1200:])

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-wow-hold-for.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
