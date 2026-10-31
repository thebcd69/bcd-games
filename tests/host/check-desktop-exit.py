#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Windows desktop says why a program ended at once (round 624, agent D2 S3 and S7).

Geometry Dash (Steam 322170, Send Log VDFZHN, build 623) was started six times from the Windows desktop: each time it loaded
opengl32.dll, asked BCD's OpenGL stub table for a pixel format, got none and ended with code 1 without a window, and nothing told
the player why (desktop sessions are never auto-tuned). Steam with Rockstar's launcher on the desktop (RX4AK5) filled the JIT pool
("[jit-pool] EXHAUSTED (image ...") and the launcher's next libraries could not load.

A first design read the session's log in order (the last "NtCreateUserProcess: image=" before a "[Wine child thread] child exited")
and was dropped: replayed over the 85 desktop-shell logs of the archive it named the wrong program (RockstarService.exe and
RockstarErrorHandler.exe "needed OpenGL" because another process had loaded opengl32; Watch Dogs, RDR2 and Resident Evil 6 crashes
followed a stub line of some process), and the app's log reader is paused during a session unless live logs are on. Now:

1. C, virtual_ios.c: the OpenGL stub table answers calls 0-2 (opengl32's DllMain) with success and every real wgl/gl call
   (code 3 and up) with STATUS_NOT_SUPPORTED, as before; the first such call on a thread marks the calling process
   (ios_child_slot_gl_call). The generic stub table never marks. Compiled on the host and driven from two threads.
2. C, process_ios.c: the child table keeps the mark with the process and with its end; madeira_child_ends_since(after) lists
   the ends after `after` (number, status, lived, gl, helper, path), continues where a full buffer stopped, gives the count
   alone for (unsigned)-1, drops what left the ring; a reused slot starts unmarked; madeira_child_report is unchanged.
   Compiled with AddressSanitizer and UndefinedBehaviorSanitizer.
3. Swift, Library.swift DesktopExitRules: the engine's text read, the [desktop-exit] line (a nonzero status within 60 s) and the
   OpenGL notice only for a program that made a real OpenGL call and ended with its own error code (not a crash status, not a
   helper, not Steam's gldriverquery probe); the C table's own text end to end.
4. Source: the pool's refusal counter in the EXHAUSTED branch, the bridge declarations, the library's wiring (Windows desktop
   sessions only, the session line only when it is empty and cleared only if unchanged), CI runs this check.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
CC = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
failures = 0


def require(condition, label, detail=''):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label + (('\n      ' + str(detail)[:1500]) if (detail and not condition) else ''))
    if not condition:
        failures += 1


def block(text, header_text):
    p = text.index(header_text)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


process = (root / 'build/ntdll-unix/process_ios.c').read_text(encoding='utf-8')
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text(encoding='utf-8')
library = (app / 'Library.swift').read_text(encoding='utf-8')
header = (app / 'WineProcessBridge.h').read_text(encoding='utf-8')
dock_rules = (app / 'DockGameEnd.swift').read_text(encoding='utf-8')
screen = (app / 'DockStartScreen.swift').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
screen_rules = screen[screen.index('// MARK: - Rules'):screen.index('// MARK: - Model')]
table = process[process.index('#define IOS_CHILD_SLOTS 32'):process.index("/* the machine of the child's main image, published to")]
stubs = virtual[virtual.index('static NTSTATUS ios_stub_unix_call(void *args) {'):virtual.index("/* DXMT's unix call table, statically linked")]

# ------------------------------------------------------------------ 1. C: the OpenGL stub table marks a real call, once a thread
c_stubs = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int NTSTATUS;
typedef NTSTATUS (*unixlib_entry_t)(void *);
#define STATUS_SUCCESS ((NTSTATUS)0)
#define STATUS_NOT_SUPPORTED ((NTSTATUS)0xC00000BB)
static _Thread_local void *fake_peb;
void *ios_jit_current_peb(void) { return fake_peb; }
static void *marks[16];
static int nmarks;
static pthread_mutex_t mark_lock = PTHREAD_MUTEX_INITIALIZER;
void ios_child_slot_gl_call(void *peb)
{
    pthread_mutex_lock(&mark_lock);
    if (nmarks < 16) marks[nmarks] = peb;
    nmarks++;
    pthread_mutex_unlock(&mark_lock);
}
''' + stubs + r'''
static int failed;
static void expect(int cond, const char *what) { printf("%s: %s\n", cond ? "PASS" : "FAIL", what); if (!cond) failed++; }
static void *other_thread(void *arg)
{
    fake_peb = arg;
    expect(ios_gl_stub_unix_call_table[5](NULL) == STATUS_NOT_SUPPORTED, "another thread's wglCreateContext (code 5) fails as before");
    expect(ios_gl_stub_unix_call_table[3](NULL) == STATUS_NOT_SUPPORTED, "and its next call too");
    return NULL;
}
int main(void)
{
    unsigned i;
    int all = 1;
    pthread_t t;
    pthread_once(&ios_stub_tables_once, ios_init_stub_tables);
    for (i = 0; i < IOS_STUB_TABLE_SIZE; i++)
        if (!ios_stub_unix_call_table[i] || !ios_gl_stub_unix_call_table[i]) all = 0;
    expect(all, "both tables are full (4096 entries, none NULL)");
    fake_peb = (void *)0x7ffd1000;
    expect(ios_gl_stub_unix_call_table[0](NULL) == STATUS_SUCCESS && ios_gl_stub_unix_call_table[1](NULL) == STATUS_SUCCESS
           && ios_gl_stub_unix_call_table[2](NULL) == STATUS_SUCCESS, "opengl32's DllMain calls (process_attach, thread_attach, process_detach) succeed");
    expect(nmarks == 0, "loading opengl32.dll alone marks nothing");
    expect(ios_stub_unix_call_table[3](NULL) == STATUS_NOT_SUPPORTED && ios_stub_unix_call_table[0](NULL) == STATUS_NOT_SUPPORTED && nmarks == 0,
           "the generic stub table (a module with no unix side) fails every call and never marks");
    expect(ios_gl_stub_unix_call_table[3](NULL) == STATUS_NOT_SUPPORTED, "get_pixel_formats (code 3) fails as before");
    expect(nmarks == 1 && marks[0] == (void *)0x7ffd1000, "and marks the calling process (its PEB) once");
    expect(ios_gl_stub_unix_call_table[3](NULL) == STATUS_NOT_SUPPORTED && ios_gl_stub_unix_call_table[4095](NULL) == STATUS_NOT_SUPPORTED
           && nmarks == 1, "later calls on the same thread fail as before and mark nothing more");
    pthread_create(&t, NULL, other_thread, (void *)0x7ffe2000);
    pthread_join(t, NULL);
    expect(nmarks == 2 && marks[1] == (void *)0x7ffe2000, "the first call on another thread marks its process once");
    return failed ? 1 : 0;
}
'''

# ------------------------------------------------------------------ 2. C: the child table keeps the mark; the new-ends reader
c_table = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length; unsigned short MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
''' + table + r'''
static int failed;
static void expect(int cond, const char *what) { printf("%s: %s\n", cond ? "PASS" : "FAIL", what); if (!cond) failed++; }
static int spawn(const char *path)
{
    static WCHAR buf[600];
    UNICODE_STRING u;
    size_t n = strlen(path), i;
    for (i = 0; i < n; i++) buf[i] = (unsigned char)path[i];
    u.Length = (unsigned short)(n * sizeof(WCHAR)); u.MaximumLength = u.Length; u.Buffer = buf;
    return ios_child_slot_take(&u);
}
static void *peb_of(int n) { return (void *)(uintptr_t)(0x10000 + 0x1000 * n); }
static int start(const char *path, int n)
{
    int slot = spawn(path);
    ios_child_slot_current = slot;
    ios_child_slot_bind_peb(peb_of(n));
    ios_child_slot_current = -1;
    return slot;
}
static void finish_child(int n, int status, double lived)
{
    unsigned at;
    ios_child_slot_exited(peb_of(n));
    expect(ios_child_slot_note_exit(peb_of(n), status) == 1, "the end is noted");
    at = (ios_child_ended_count - 1) % IOS_CHILD_ENDED;
    ios_child_ended[at].started = ios_child_ended[at].ended_at - lived;
}
static int lines_of(const char *text)
{
    int n = 0;
    for (; *text; text++) n += *text == '\n';
    return n;
}
int main(int argc, char **argv)
{
    static char text[16384];
    char small[120];
    unsigned base, next, again;
    int s_gd, s_launcher, s, k;

    base = madeira_child_ends_since((unsigned)-1, NULL, 0);
    expect(base == 0, "an empty table: no ends, count 0");
    expect(madeira_child_ends_since(0, text, sizeof text) == 0 && text[0] == 0, "no ends: no line, an empty string");

    /* VDFZHN: the desktop starts Geometry Dash; it asks the stub table for a pixel format and ends with code 1, 2.1 s later */
    s_gd = start("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Geometry Dash\\GeometryDash.exe", 1);
    s_launcher = start("C:\\Program Files\\Rockstar Games\\Launcher\\RockstarService.exe", 2);
    ios_child_slot_gl_call(peb_of(1));
    ios_child_slot_gl_call(NULL);
    ios_child_slot_gl_call(peb_of(77));
    expect(ios_child_slots[s_gd].gl == 1 && ios_child_slots[s_launcher].gl == 0, "only the process that made the call is marked (NULL and an unknown PEB: nothing)");
    finish_child(1, 1, 2.1);
    ios_child_slot_release(s_gd);
    finish_child(2, 1, 0.4);
    ios_child_slot_release(s_launcher);
    next = madeira_child_ends_since(base, text, sizeof text);
    expect(next == 2 && lines_of(text) == 2, "two ends since the start: two lines, the next `after` is 2");
    {
        static const char want[] = "0\t1\t2.1\t1\t0\tC:\\Program Files (x86)\\Steam\\steamapps\\common\\Geometry Dash\\GeometryDash.exe\n";
        expect(!strncmp(text, want, strlen(want)), "Geometry Dash: number 0, status 1, lived 2.1 s, gl 1, helper 0, the path last");
    }
    expect(strstr(text, "\n1\t1\t0.4\t0\t0\tC:\\Program Files\\Rockstar Games\\Launcher\\RockstarService.exe\n") != NULL,
           "RockstarService.exe ended with 1 too, without an OpenGL call: gl 0");
    expect(madeira_child_ends_since(next, text, sizeof text) == next && text[0] == 0, "asked again from the returned count: nothing new");
    expect(madeira_child_ends_since((unsigned)-1, text, sizeof text) == next && text[0] == 0, "(unsigned)-1: the count alone, no line");

    /* a reused slot starts unmarked */
    s = start("C:\\Games\\GL\\glgame.exe", 3);
    ios_child_slot_gl_call(peb_of(3));
    ios_child_slot_release(s);
    s = start("C:\\Games\\D3D\\d3dgame.exe", 4);
    expect(ios_child_slots[s].gl == 0, "a slot taken again does not keep the last process's mark");
    finish_child(4, (int)0xC0000005u, 3.0);
    ios_child_slot_release(s);
    again = madeira_child_ends_since(next, text, sizeof text);
    expect(again == 3 && !strcmp(text, "2\tc0000005\t3.0\t0\t0\tC:\\Games\\D3D\\d3dgame.exe\n"), "a crash status in hex, no mark");

    /* a helper by the engine's file-name rule */
    s = start("C:\\Program Files (x86)\\Steam\\steamerrorreporter64.exe", 5);
    finish_child(5, 0, 1.0);
    ios_child_slot_release(s);
    madeira_child_ends_since(again, text, sizeof text);
    expect(!strcmp(text, "3\t0\t1.0\t0\t1\tC:\\Program Files (x86)\\Steam\\steamerrorreporter64.exe\n"), "a helper: the helper field is 1");

    /* a full buffer: what fits, and the rest next time */
    base = madeira_child_ends_since((unsigned)-1, NULL, 0);
    for (k = 0; k < 3; k++)
    {
        char path[64];
        snprintf(path, sizeof path, "C:\\Games\\X\\prog%d.exe", k);
        s = start(path, 10 + k);
        finish_child(10 + k, k + 1, 1.0);
        ios_child_slot_release(s);
    }
    next = madeira_child_ends_since(base, small, 60);
    expect(next == base + 1 && lines_of(small) == 1 && strstr(small, "prog0.exe\n") && strlen(small) < 60,
           "a buffer for one line: one line, the next `after` is the end that did not fit");
    next = madeira_child_ends_since(next, small, sizeof small);
    expect(next == base + 3 && lines_of(small) == 2 && strstr(small, "prog1.exe\n") && strstr(small, "prog2.exe\n"), "the rest the next time");
    expect(madeira_child_ends_since(base, small, 10) == base && small[0] == 0, "a buffer too small for any line: nothing written, the same `after`");
    expect(madeira_child_ends_since(base, NULL, 100) == base + 3, "no buffer: the count, nothing written");

    /* more ends than the ring holds since the last look: only the ring's */
    base = madeira_child_ends_since((unsigned)-1, NULL, 0);
    for (k = 0; k < IOS_CHILD_ENDED + 6; k++)
    {
        char path[64];
        snprintf(path, sizeof path, "C:\\Games\\R\\r%d.exe", k);
        s = start(path, 200 + k);
        finish_child(200 + k, 2, 1.0);
        ios_child_slot_release(s);
    }
    next = madeira_child_ends_since(base, text, sizeof text);
    expect(next == base + IOS_CHILD_ENDED + 6 && lines_of(text) == IOS_CHILD_ENDED && !strstr(text, "\\r5.exe\n") && strstr(text, "\\r6.exe\n")
           && strstr(text, "\\r69.exe\n"), "70 ends since the last look: the last 64 (the ring), oldest first");

    /* madeira_child_report is unchanged: its E line has six fields, the path last */
    {
        static char report[65536];
        const char *e;
        madeira_child_report(report, sizeof report);
        e = strstr(report, "E\t");
        expect(e && !strncmp(e, "E\t0\t2\t", 6) && strstr(e, "\tC:\\Games\\R\\r6.exe\n"), "madeira_child_report's E line is as before");
    }
    if (argc > 1)
    {
        /* the VDFZHN pair again, for the Swift rules end to end */
        FILE *f;
        base = madeira_child_ends_since((unsigned)-1, NULL, 0);
        s = start("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Geometry Dash\\GeometryDash.exe", 300);
        ios_child_slot_gl_call(peb_of(300));
        finish_child(300, 1, 2.1);
        ios_child_slot_release(s);
        s = start("C:\\Program Files (x86)\\Steam\\bin\\gldriverquery64.exe", 301);
        ios_child_slot_gl_call(peb_of(301));
        finish_child(301, 1, 0.6);
        ios_child_slot_release(s);
        madeira_child_ends_since(base, text, sizeof text);
        f = fopen(argv[1], "w");
        if (f) { fputs(text, f); fclose(f); }
    }
    return failed ? 1 : 0;
}
'''

# ------------------------------------------------------------------ 3. Swift: the rules
rules = block(library, 'enum DesktopExitRules {')
swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
typealias D = DesktopExitRules
func end(_ path: String, _ status: UInt32, lived: Double, gl: Bool, helper: Bool = false) -> D.End {
    D.End(number: 0, status: status, lived: lived, openGL: gl, helper: helper, path: path)
}
let gd = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Geometry Dash\\GeometryDash.exe"

@main struct Checks {
    static func main() {
        // ---- the engine's text
        let ends = D.parse("0\t1\t2.1\t1\t0\t\(gd)\n1\tc0000005\t3.0\t0\t1\tC:\\Games\\X\\crashpad_handler.exe\n")
        require(ends.count == 2 && ends[0] == D.End(number: 0, status: 1, lived: 2.1, openGL: true, helper: false, path: gd),
                "a line: number, status in hex, lived, gl, helper, the path last")
        require(ends[1].status == 0xC0000005 && ends[1].helper && !ends[1].openGL && ends[1].number == 1, "the second line")
        require(D.parse("x\t1\t2\t0\t0\tC:\\a.exe\n0\tzz\t1\t0\t0\tC:\\a.exe\n0\t1\tnan\t0\t0\tC:\\a.exe\n0\t1\t2\t0\n").isEmpty,
                "a malformed line is skipped")
        require(D.parse("").isEmpty && D.parse("0\t1\t2\t0\t0\tC:\\a b\\c\td.exe\n").first?.path == "C:\\a b\\c\td.exe",
                "an empty text, and a tab in the path stays in the path")
        require(ends[0].fileName == "GeometryDash.exe" && end("", 1, lived: 1, gl: false).fileName == "?"
                && end("C:/Games/x.exe", 1, lived: 1, gl: false).fileName == "x.exe", "the file name, its case kept, never the folder")

        // ---- the OpenGL notice: VDFZHN, and what must not get it
        let v = end(gd, 1, lived: 2.1, gl: true)
        require(D.needsOpenGL(v), "VDFZHN: Geometry Dash asked for OpenGL and ended with 1 after 2.1 s: the notice")
        require(D.openGLWords(v.fileName) == "GeometryDash.exe needs OpenGL, which BCD does not have yet, so it closes. This is not a setting.",
                "its words")
        require(D.needsOpenGL(end(gd, 0xFF, lived: 59.9, gl: true)) && D.needsOpenGL(end(gd, 0x8007054F, lived: 1, gl: true)),
                "an error box first (up to 60 s) and another code of its own: the notice")
        require(!D.needsOpenGL(end(gd, 1, lived: 2.1, gl: false)),
                "no real OpenGL call (loading opengl32.dll alone, RockstarService.exe in the replay): no notice")
        require(!D.needsOpenGL(end(gd, 0, lived: 2.1, gl: true)), "ended with 0: no notice")
        require(!D.needsOpenGL(end(gd, 0xC0000005, lived: 3, gl: true)) && !D.needsOpenGL(end(gd, 0xFFFFFFFF, lived: 3, gl: true))
                && !D.needsOpenGL(end(gd, 0x80000003, lived: 3, gl: true)) && !D.needsOpenGL(end(gd, 0xC0000135, lived: 3, gl: true)),
                "a crash status after an OpenGL call: no notice, the log line only")
        var same = true
        for status: UInt32 in [0, 1, 2, 0xFF, 0x2329, 0xEAC9, 0x8007054F, 0x80000003, 0x80000004, 0xC0000005, 0xC0000135, 0xC0000409,
                               0xE06D7363, 0xFFFFFFFF, 0xFFFFFC8F, 0x7FFFFFFF, 0x40000015] where D.isCrash(status) != DockGameEndRules.isCrash(status) {
            same = false
        }
        require(same, "isCrash says what BCD Dock's game-end watch says (DockGameEndRules.isCrash), status by status")
        require(!D.needsOpenGL(end(gd, 1, lived: 60, gl: true)) && !D.needsOpenGL(end(gd, 1, lived: 3600, gl: true)),
                "a program that ran 60 s or more: no notice")
        require(!D.needsOpenGL(end("C:\\Games\\X\\UnityCrashHandler64.exe", 1, lived: 1, gl: true, helper: true)), "a helper: no notice")
        require(!D.needsOpenGL(end("C:\\Program Files (x86)\\Steam\\bin\\gldriverquery64.exe", 1, lived: 0.6, gl: true))
                && !D.needsOpenGL(end("C:\\Program Files (x86)\\Steam\\bin\\GLDriverQuery.exe", 2, lived: 0.6, gl: true)),
                "Steam's own OpenGL probe (gldriverquery, started by Steam itself): no notice")

        // ---- the log line
        require(D.logLine(v) == "[desktop-exit] exe=GeometryDash.exe code=0x1 seconds=2.1 gl=1 notice=opengl", "VDFZHN's line")
        require(D.logLine(end("C:\\Games\\D3D\\d3dgame.exe", 0xC0000005, lived: 3, gl: false)) == "[desktop-exit] exe=d3dgame.exe code=0xc0000005 seconds=3.0 gl=0",
                "a crash at once: the line, no notice")
        require(D.logLine(end("C:\\Games\\X\\crashpad_handler.exe", 1, lived: 1, gl: false, helper: true)) == "[desktop-exit] exe=crashpad_handler.exe code=0x1 seconds=1.0 gl=0 helper=1",
                "a helper is named so")
        require(D.logLine(end(gd, 0, lived: 2, gl: true)) == nil && D.logLine(end(gd, 1, lived: 60, gl: true)) == nil,
                "ended with 0, or after 60 s or more: no line")
        require(D.maxLines == 32 && D.quickSeconds == 60 && D.noticeSeconds == 12, "32 lines a session, 60 s, 12 s on the session line")
        require(D.poolWords == "The Windows desktop is out of room for programs: what runs on it now is too big for this device. Close a program, or start the game from BCD's library.",
                "the pool's words")

        // ---- the C table's own text, end to end
        if CommandLine.arguments.count > 1, let text = try? String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8) {
            let all = D.parse(text)
            require(all.count == 2 && D.needsOpenGL(all[0]) && all[0].fileName == "GeometryDash.exe" && !D.needsOpenGL(all[1]),
                    "the C table's text: Geometry Dash gets the notice, Steam's gldriverquery64.exe does not (\(all))")
        } else {
            require(false, "the C table's text was written")
        }
        exit(failures == 0 ? 0 : 1)
    }
}
'''

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    env_vars = dict(os.environ, ASAN_OPTIONS='symbolize=0')
    for name, code in (('stubs', c_stubs), ('table', c_table)):
        (tmp / f'{name}.c').write_text(code)
        r = subprocess.run([CC, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(tmp / name),
                            str(tmp / f'{name}.c')], capture_output=True, text=True)
        if r.returncode:
            sys.stdout.write(r.stderr[-3000:])
            require(False, f'the {name} part compiles (-Wall -Werror, ASan, UBSan)')
            continue
        args = [str(tmp / name)] + ([str(tmp / 'ends.txt')] if name == 'table' else [])
        r = subprocess.run(args, capture_output=True, text=True, env=env_vars)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1
    (tmp / 'screen.swift').write_text('import Foundation\n' + screen_rules)
    (tmp / 'dock.swift').write_text(dock_rules)
    (tmp / 'rules.swift').write_text('import Foundation\n' + rules + '\n')
    (tmp / 'main.swift').write_text(swift)
    r = subprocess.run([SWIFTC, '-parse-as-library', '-o', str(tmp / 'rules'), str(tmp / 'screen.swift'), str(tmp / 'dock.swift'),
                        str(tmp / 'rules.swift'), str(tmp / 'main.swift')], capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the Swift rules compile')
    else:
        r = subprocess.run([str(tmp / 'rules'), str(tmp / 'ends.txt')], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1

# ------------------------------------------------------------------ 4. source: the counter, the bridge, the wiring
refusal = virtual[virtual.index('if (offset == (size_t)-1)\n            {\n                /* madeira-bcd (round 624, Mechabellum J1QBHE'):]
refusal = refusal[:refusal.index('return -1;')]
require(refusal.count('__atomic_add_fetch( &ios_jit_pool_refusals, 1, __ATOMIC_RELAXED );') == 1
        and refusal.index('unsigned long exhausted_seq =') < refusal.index('&ios_jit_pool_refusals') < refusal.index('[jit-pool] EXHAUSTED (image'),
        'every refused image copy counts, in the EXHAUSTED branch, also those the log no longer prints')
require(virtual.count('&ios_jit_pool_refusals') == 2 and 'unsigned long madeira_jit_pool_refusals( void ) { return __atomic_load_n( &ios_jit_pool_refusals, __ATOMIC_RELAXED ); }' in virtual,
        'the counter has one writer and one reader')
gl_branch = virtual[virtual.index('} else if (match && strstr(match, "opengl32")) {'):]
gl_branch = gl_branch[:gl_branch.index('} else {')]
require('funcs64 = funcs_wow64 = (const void *)ios_gl_stub_unix_call_table;' in gl_branch, 'opengl32 still gets the OpenGL stub table for both bitnesses')
require('unsigned int madeira_child_ends_since(unsigned int after, char *buf, int len);' in header
        and 'unsigned long madeira_jit_pool_refusals(void);' in header, 'the bridge declares both functions as the engine defines them')
require('unsigned int madeira_child_ends_since( unsigned int after, char *buf, int len )' in process, 'process_ios.c defines the reader')
crash_test = 'status & 0xC000_0000 == 0xC000_0000 || status == 0x8000_0003'
require(crash_test in block(rules, 'static func isCrash(_ status: UInt32) -> Bool') and crash_test in block(dock_rules, 'static func isCrash(_ status: UInt32) -> Bool'),
        "the rules' crash test is BCD Dock's, word for word")
session_rules = library[library.index('// MARK: - Rules (session end)'):library.index('enum LibraryError: LocalizedError {')]
require('enum DesktopExitRules {' in session_rules and 'DockGameEndRules' not in rules.replace('DockGameEndRules.isCrash, the same test', ''),
        'the rules sit in the session-end rules region and need nothing outside it (check-session-end.py compiles that region alone)')

begin = block(library, 'func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)')
require('desktopEndsAfter = entry.desktop == true ? madeira_child_ends_since(UInt32.max, nil, 0) : nil' in begin
        and 'desktopPoolBase = madeira_jit_pool_refusals(); desktopPoolNoticed = false' in begin
        and 'desktopNamed = []; desktopExitLines = 0; desktopNotices = []' in begin,
        'a session begins: the watch starts after the ends so far, only for the Windows desktop; the pool counted from here')
poll = block(library, 'private func poll()')
running = poll[poll.index('if wine_process_is_running() != 0 {'):poll.index('} else if sawProcess && wineserver_is_running() == 0 { finish() }')]
require(running.index('watchNoInputHint()') < running.index('watchDesktopExits()'), 'the watch runs in the session poll while the program runs')
watch = block(library, 'private func watchDesktopExits()')
require(watch.startswith('private func watchDesktopExits() {\n        guard let after = desktopEndsAfter, activeEntry?.desktop == true else { return }'),
        'only a Windows desktop session')
require('desktopExitLines < DesktopExitRules.maxLines, let line = DesktopExitRules.logLine(end)' in watch
        and 'if DesktopExitRules.needsOpenGL(end), desktopNamed.insert(end.fileName.lowercased()).inserted' in watch,
        'a line for each end worth one (32 at most), the OpenGL notice once per program')
require('if !desktopPoolNoticed, refusals > desktopPoolBase {' in watch and watch.count('desktopNotices.append(') == 2,
        'the pool notice once a session, at the first refusal after the start')
require('guard !desktopNotices.isEmpty, sessionMessage.isEmpty else { return }' in watch
        and 'if self?.sessionMessage == words { self?.sessionMessage = "" }' in watch,
        'the session line only when it is empty, cleared only if it still says the notice')
require(not re.search(r'library\.error|\.alert\(|setDisplayActive|wine_session_|finish\(\)', watch), 'the watch ends nothing and opens no alert')
fin = block(library, 'func finish()') if 'func finish()' in library else ''
require('desktopEndsAfter = nil; desktopNotices = []' in library[library.index('current = nil; activeEntry = nil; menu = false; sessionMessage = ""'):][:200],
        'the session ends: nothing more to watch, no notice left')
preview = (root / 'app/BCDPreview/BCDPreviewSession.swift').read_text(encoding='utf-8')


def swift_words(src, marker):
    rest = src[src.index(marker) + len(marker):]
    out, first = [], True
    for line in rest.splitlines():
        t = line.strip()
        if not first and not t.startswith('+'):
            break
        out += re.findall(r'"((?:[^"\\]|\\.)*)"', t)
        first = False
    return ''.join(out)


gl_words = swift_words(rules, 'static func openGLWords(_ exe: String) -> String {\n        ').replace('\\(exe)', 'GeometryDash.exe')
require(swift_words(preview, 'static let desktopOpenGLWords = ') == gl_words
        and swift_words(preview, 'static let desktopPoolWords = ') == swift_words(rules, 'static let poolWords = ')
        and 'tab.hasPrefix("message-")' in preview,
        "the preview app's copies of the two notices (message-opengl, message-pool) say exactly what DesktopExitRules says")
require('ASAN_OPTIONS=symbolize=0 python3 tests/host/check-desktop-exit.py' in workflow, 'CI runs this check')

print(f'\n{"PASS" if failures == 0 else "FAIL"}: desktop exit watch ({failures} failure(s))')
sys.exit(1 if failures else 0)
