#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Fonts registry values survive a session that found no fonts; the wider repair is opt-in (build 632; Wine fork commits baaab873d + BCD's).

DirectWrite builds its system collection only from HKLM\\...\\Windows NT\\CurrentVersion\\Fonts. The prefix template's HKCU\\Software\\Wine\\
Fonts\\External Fonts lists Tahoma, Tahoma Bold, Marlett and Symbol under macOS paths, with the same value names as the Fonts values of
C:\\windows\\Fonts, and win32u's update_external_font_keys (once per session) deletes the Fonts value of every external entry it finds no face for:
one session whose font list came up empty deleted those values for good (upstream Madeira: an embedded browser then crashed in Chromium's font
fallback and D2D drew no text). There is no such log in BCD's archive; the prefix template has exactly that shape.

BCD takes the upstream commit and splits it: "a session that found no fonts leaves the keys alone" always applies; "a stale External Fonts entry that
names a font of the Windows directory is dropped from External Fonts only" and "every font of the Windows directory whose Fonts value is missing
gets it back" apply only with MADEIRA_FONT_KEYS_REPAIR=1. BCD seeds C:\\windows\\Fonts with arial, arialbd, verdana, georgia, cour, times, trebuc,
msgothic and msmincho (WineProcessBridge.m) and no Fonts value names them, so the second half would add them to DirectWrite's collection in every
session and change how text looks in games that work today.

Checks: update_external_font_keys is cut out of the Wine fork's win32u/font.c and compiled on the host twice with a fake registry and a fake face
list, once WITHOUT WINE_IOS (the function as upstream Wine has it: the iOS blocks are additions only) and once with it; 400 scenarios (the shape of
BCD's prefix template, seeded fonts, random subsets, missing and extra values, external faces) run through both:
 (1) a session with at least one face, repair off (the default): the three registry keys come out identical to the plain function's, scenario by scenario;
 (2) a session with no face at all: the iOS function touches nothing, the plain one deletes the external entries' Fonts values (so the scenarios do show
     the damage);
 (3) repair on: the result equals an independent model (stale Windows-font entries leave External Fonts only; missing scalable Windows-directory
     values are written to both Fonts keys), and with the template's shape it does add the seeded fonts, which is why it is opt-in.
Then the sources: every addition is inside #ifdef WINE_IOS, the guard comes before the first registry call, and the catalog/workflow wiring.
"""
from pathlib import Path
import os
import random
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


font_c = root / 'wine/dlls/win32u/font.c'
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-font-keys-repair.py' in wf)

if not font_c.is_file():
    print('SKIP: no wine sources (check out the submodule)')
    print('PASS' if ok else 'FAIL')
    raise SystemExit(0 if ok else 1)

src = font_c.read_text()
a = src.index('struct external_key\n{')
b = src.index('\n}\n', src.index('static void update_external_font_keys(void)\n{')) + 3
region = src[a:b]

# ---- the sources
upd = region[region.index('static void update_external_font_keys(void)'):]
sw = region[region.index('static BOOL ios_font_keys_repair_enabled(void)'):]
sw = sw[:sw.index('\n}\n')]
check("the repair switch is opt-in: only MADEIRA_FONT_KEYS_REPAIR=1 turns it on", "enabled = e && e[0] == '1';" in sw and 'getenv( "MADEIRA_FONT_KEYS_REPAIR" )' in sw, sw)
check('the no-fonts guard does not depend on the switch and comes before the first registry call',
      '    if (!faces)' in upd and 'if (repair && !faces)' not in upd
      and upd.index('    if (!faces)') < upd.index('reg_create_key( NULL, fonts_winnt_config_keyW'))
check('the two other halves are behind "repair": the stale-entry queue and the restore loop',
      'if (repair && ios_windows_font_file( face ) && (key = malloc( sizeof(*key) )))' in upd and '\n    if (repair)\n    {' in upd)

# every difference between the function and the plain one is inside "#ifdef WINE_IOS ... #endif"
def strip_ios(text):
    out, skip = [], 0
    for line in text.split('\n'):
        if re.match(r'\s*#\s*ifdef\s+WINE_IOS\b', line):
            skip += 1
            continue
        if skip and re.match(r'\s*#\s*(endif)\b', line):
            skip -= 1
            continue
        if not skip:
            out.append(line)
    return '\n'.join(out)
plain = strip_ios(upd)
check('without WINE_IOS the function has no mention of the repair, the faces count or the log (the additions are all inside #ifdef WINE_IOS)',
      'repair' not in plain and 'faces' not in plain.replace('->faces', '') and 'dprintf' not in plain and 'list_only' not in plain, plain[:200])

# ---- the host build, twice
harness = r'''
#define _DARWIN_C_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "wine/list.h"
typedef uint16_t WCHAR; typedef int BOOL; typedef unsigned int DWORD; typedef unsigned int ULONG; typedef unsigned int UINT; typedef void *HKEY;
#define TRUE 1
#define FALSE 0
#define MAX_PATH 260
#define LF_FULLFACESIZE 64
#define REG_SZ 1
#define ARRAYSIZE(a) (sizeof(a) / sizeof((a)[0]))
#define ADDFONT_EXTERNAL_FONT 0x01
#define ADDFONT_EXTERNAL_FOUND 0x80
typedef struct { ULONG Type; ULONG DataOffset; } KEY_VALUE_FULL_INFORMATION;
typedef struct { ULONG Type; } KEY_VALUE_PARTIAL_INFORMATION;
struct gdi_font_face { struct list entry; unsigned int flags; WCHAR *file; WCHAR *full_name; BOOL scalable; };
struct gdi_font_family { struct list faces; };
static struct gdi_font_family the_family;
static int family_name_tree, wine_fonts_key_;
#define WINE_RB_FOR_EACH_ENTRY(elem, tree, type, field) for ((elem) = &the_family; (elem); (elem) = NULL)
static HKEY wine_fonts_key = &wine_fonts_key_;
#ifdef UNICODE_NEVER
#endif
static const WCHAR nt_prefixW[] = {'\\','?','?','\\'};
static const WCHAR true_type_suffixW[] = {' ','(','T','r','u','e','T','y','p','e',')',0};
static const WCHAR fonts_win9x_config_keyW[] = {'W','9','x',0};
static const WCHAR fonts_winnt_config_keyW[] = {'W','N','T',0, 0};

/* ---- WCHAR helpers */
static WCHAR lw(WCHAR c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int lstrlenW(const WCHAR *s) { int n = 0; while (s[n]) n++; return n; }
static WCHAR *lstrcpyW(WCHAR *d, const WCHAR *s) { int i = 0; do d[i] = s[i]; while (s[i++]); return d; }
static WCHAR *lstrcatW(WCHAR *d, const WCHAR *s) { lstrcpyW(d + lstrlenW(d), s); return d; }
static size_t wcslen_(const WCHAR *s) { return lstrlenW(s); }
static WCHAR *wcsrchr_(const WCHAR *s, WCHAR c) { const WCHAR *r = NULL; do if (*s == c) r = s; while (*s++); return (WCHAR *)r; }
static WCHAR *wcschr_(const WCHAR *s, WCHAR c) { for (; *s; s++) if (*s == c) return (WCHAR *)s; return c ? NULL : (WCHAR *)s; }
static int wcsicmp_(const WCHAR *a, const WCHAR *b) { while (*a && lw(*a) == lw(*b)) a++, b++; return (int)lw(*a) - (int)lw(*b); }
static int wcsnicmp_(const WCHAR *a, const WCHAR *b, size_t n) { while (n && *a && lw(*a) == lw(*b)) a++, b++, n--; return n ? (int)lw(*a) - (int)lw(*b) : 0; }
#define wcsrchr wcsrchr_
#define wcschr wcschr_
#define wcsicmp wcsicmp_
#define wcsnicmp wcsnicmp_
static int facename_compare(const WCHAR *a, const WCHAR *b, int len) { (void)len; return wcsicmp_(a, b); }
static void asciiz_to_unicode(WCHAR *d, const char *s) { while ((*d++ = (unsigned char)*s++)); }

/* ---- a face list and a registry of three keys, filled from the scenario */
#define NFACE 64
static struct gdi_font_face faces_[NFACE]; static int nfaces;
#define NKV 128
struct kv { WCHAR name[96]; WCHAR data[300]; int used; };
static struct kv winnt_[NKV], win9x_[NKV], ext_[NKV];
static struct kv *key_of(HKEY k) { return (struct kv *)k; }
static void fonts_win_dir(WCHAR *path) { asciiz_to_unicode(path, "\\??\\C:\\windows\\fonts\\"); }
static void get_fonts_win_dir_path(const WCHAR *file, WCHAR *path) { fonts_win_dir(path); if (file) lstrcatW(path, file); }
static WCHAR *get_nt_path(const WCHAR *path)
{
    WCHAR *ret;
    if (path[0] && path[1] == ':')
    {
        ret = malloc((wcslen_(path) + 1 + ARRAYSIZE(nt_prefixW)) * sizeof(WCHAR));
        memcpy(ret, nt_prefixW, sizeof(nt_prefixW));
        lstrcpyW(ret + ARRAYSIZE(nt_prefixW), path);
    }
    else { ret = malloc((wcslen_(path) + 1) * sizeof(WCHAR)); lstrcpyW(ret, path); }
    return ret;
}
static struct gdi_font_face *find_face_from_full_name(const WCHAR *name)
{
    int i;
    for (i = 0; i < nfaces; i++) if (!wcsicmp_(faces_[i].full_name, name)) return &faces_[i];
    return NULL;
}
static HKEY reg_create_key(HKEY parent, const WCHAR *name, ULONG size, DWORD options, DWORD *disp)
{
    (void)parent; (void)options; (void)disp;
    if (name == fonts_winnt_config_keyW) return winnt_;
    if (name == fonts_win9x_config_keyW) return win9x_;
    (void)size;
    return ext_;
}
static int reg_enum_value(HKEY hkey, DWORD idx, KEY_VALUE_FULL_INFORMATION *info, size_t size, WCHAR *value, size_t value_size)
{
    struct kv *k = key_of(hkey);
    DWORD seen = 0; int i;
    (void)size; (void)value_size;
    for (i = 0; i < NKV; i++)
    {
        if (!k[i].used) continue;
        if (seen++ != idx) continue;
        info->Type = REG_SZ;
        info->DataOffset = 512;
        lstrcpyW(value, k[i].name);
        lstrcpyW((WCHAR *)((char *)info + 512), k[i].data);
        return TRUE;
    }
    return FALSE;
}
static void reg_delete_value(HKEY hkey, const WCHAR *name)
{
    struct kv *k = key_of(hkey); int i;
    for (i = 0; i < NKV; i++) if (k[i].used && !wcsicmp_(k[i].name, name)) k[i].used = 0;
}
static void set_reg_value(HKEY hkey, const WCHAR *name, DWORD type, const void *data, DWORD len)
{
    struct kv *k = key_of(hkey); int i;
    (void)type; (void)len;
    for (i = 0; i < NKV; i++) if (k[i].used && !wcsicmp_(k[i].name, name)) { lstrcpyW(k[i].data, data); return; }
    for (i = 0; i < NKV; i++) if (!k[i].used) { k[i].used = 1; lstrcpyW(k[i].name, name); lstrcpyW(k[i].data, data); return; }
    abort();
}
static DWORD query_reg_value(HKEY hkey, const WCHAR *name, KEY_VALUE_PARTIAL_INFORMATION *info, size_t size)
{
    struct kv *k = key_of(hkey); int i;
    (void)info; (void)size;
    for (i = 0; i < NKV; i++) if (k[i].used && !wcsicmp_(k[i].name, name)) return (lstrlenW(k[i].data) + 1) * 2;
    return 0;
}
static void NtClose(HKEY h) { (void)h; }

''' + region + r'''

/* ---- scenarios: lines "face <flags> <scalable> <full name>|<file>", "winnt <name>|<data>", "win9x ...", "ext ...", "run" */
static WCHAR *w(const char *s) { WCHAR *r = malloc((strlen(s) + 1) * 2); asciiz_to_unicode(r, s); return r; }
static void put(struct kv *k, const char *name, const char *data)
{
    int i;
    for (i = 0; i < NKV; i++) if (!k[i].used) { k[i].used = 1; { WCHAR *n = w(name), *d = w(data); lstrcpyW(k[i].name, n); lstrcpyW(k[i].data, d); free(n); free(d); } return; }
    abort();
}
static void dump(const char *tag, struct kv *k)
{
    int i;
    for (i = 0; i < NKV; i++)
        if (k[i].used)
        {
            char n[96], d[300]; int j;
            for (j = 0; (n[j] = (char)k[i].name[j]); j++) ;
            for (j = 0; (d[j] = (char)k[i].data[j]); j++) ;
            printf("%s %s = %s\n", tag, n, d);
        }
}
int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "r");
    char line[1024];
    list_init(&the_family.faces);
    while (fgets(line, sizeof(line), f))
    {
        char *nl = strchr(line, '\n'); if (nl) *nl = 0;
        if (!strncmp(line, "face ", 5))
        {
            unsigned int flags; int scal; char name[200], file[400];
            if (sscanf(line + 5, "%u %d %199[^|]|%399[^\n]", &flags, &scal, name, file) != 4) abort();
            faces_[nfaces].flags = flags; faces_[nfaces].scalable = scal; faces_[nfaces].full_name = w(name); faces_[nfaces].file = w(file);
            list_add_tail(&the_family.faces, &faces_[nfaces].entry);
            nfaces++;
        }
        else if (!strncmp(line, "winnt ", 6) || !strncmp(line, "win9x ", 6) || !strncmp(line, "ext ", 4))
        {
            char *sp = strchr(line, ' '), *bar = strchr(sp + 1, '|');
            *bar = 0;
            put(line[0] == 'w' && line[3] == 'n' ? winnt_ : line[0] == 'w' ? win9x_ : ext_, sp + 1, bar + 1);
        }
        else if (!strcmp(line, "run"))
        {
            update_external_font_keys();
            printf("--\n");
            dump("winnt", winnt_); dump("win9x", win9x_); dump("ext", ext_);
            printf("==\n");
            memset(winnt_, 0, sizeof(winnt_)); memset(win9x_, 0, sizeof(win9x_)); memset(ext_, 0, sizeof(ext_));
            nfaces = 0; list_init(&the_family.faces);
        }
    }
    return 0;
}
'''

# ---- scenarios
WIN = '\\??\\C:\\windows\\fonts\\'
random.seed(8)
TEMPLATE_HKLM = [('Courier (TrueType)', 'courier.ttf'), ('Fixedsys (TrueType)', 'fixedsys.ttf'), ('Marlett (TrueType)', 'marlett.ttf'),
                 ('MS Sans Serif (TrueType)', 'ms_sans_serif.ttf'), ('Symbol (TrueType)', 'symbol.ttf'), ('System (TrueType)', 'system.ttf'),
                 ('Tahoma (TrueType)', 'tahoma.ttf'), ('Tahoma Bold (TrueType)', 'tahomabd.ttf'), ('Webdings (TrueType)', 'webdings.ttf'),
                 ('Wingdings (TrueType)', 'wingding.ttf')]
TEMPLATE_EXT = [('Tahoma (TrueType)', 'Z:\\System\\Library\\Fonts\\Supplemental\\Tahoma.ttf'),
                ('Tahoma Bold (TrueType)', 'Z:\\System\\Library\\Fonts\\Supplemental\\Tahoma Bold.ttf'),
                ('Marlett (TrueType)', 'Z:\\Applications\\Wine Stable.app\\Contents\\Resources\\wine\\share\\wine\\fonts\\marlett.ttf'),
                ('Symbol (TrueType)', 'Z:\\Applications\\Wine Stable.app\\Contents\\Resources\\wine\\share\\wine\\fonts\\symbol.ttf'),
                ('Arial (TrueType)', 'Z:\\System\\Library\\Fonts\\Supplemental\\Arial.ttf'),
                ('Arial Bold (TrueType)', 'Z:\\System\\Library\\Fonts\\Supplemental\\Arial Bold.ttf'),
                ('Apple Symbols (TrueType)', 'Z:\\System\\Library\\Fonts\\Apple Symbols.ttf'),
                ('Hiragino Sans W3', 'Z:\\System\\Library\\Fonts\\Hiragino.ttc')]
WINFACES = [('Tahoma', 'tahoma.ttf', 1), ('Tahoma Bold', 'tahomabd.ttf', 1), ('Marlett', 'marlett.ttf', 1), ('Symbol', 'symbol.ttf', 1),
            ('Webdings', 'webdings.ttf', 1), ('Wingdings', 'wingding.ttf', 1), ('Courier', 'courier.ttf', 0), ('System', 'system.ttf', 0),
            ('Arial', 'arial.ttf', 1), ('Arial Bold', 'arialbd.ttf', 1), ('Verdana', 'verdana.ttf', 1), ('Georgia', 'georgia.ttf', 1),
            ('Courier New', 'cour.ttf', 1), ('Times New Roman', 'times.ttf', 1), ('Trebuchet MS', 'trebuc.ttf', 1), ('MS Gothic', 'msgothic.ttc', 1),
            ('MS Mincho', 'msmincho.ttc', 1)]
EXTFACES = [('Apple Symbols', 'Z:\\System\\Library\\Fonts\\Apple Symbols.ttf', 1), ('Hiragino Sans W3', 'Z:\\System\\Library\\Fonts\\Hiragino.ttc', 1),
            ('Menlo Regular', 'Z:\\System\\Library\\Fonts\\Menlo.ttc', 1), ('Old Bitmap', 'Z:\\fonts\\old.fon', 0)]


def scenario(kind, i):
    """text of one scenario"""
    lines = []
    if kind == 'template':          # BCD's prefix template with the seeded fonts loaded
        faces = [f for f in WINFACES if f[0] in ('Tahoma', 'Tahoma Bold', 'Marlett', 'Symbol', 'Webdings', 'Wingdings', 'Courier', 'System')]
        faces += [f for f in WINFACES if f[0] in ('Arial', 'Arial Bold', 'Verdana', 'Georgia', 'Courier New', 'Times New Roman', 'Trebuchet MS',
                                                  'MS Gothic', 'MS Mincho')]
        hklm = list(TEMPLATE_HKLM)
        ext = list(TEMPLATE_EXT)
        extf = [EXTFACES[0], EXTFACES[1]]
    elif kind == 'empty':           # no face at all, the template's registry
        faces, hklm, ext, extf = [], list(TEMPLATE_HKLM), list(TEMPLATE_EXT), []
    elif kind == 'damaged':         # the Windows fonts' values are gone already
        faces = [f for f in WINFACES if random.random() < 0.7]
        hklm = [h for h in TEMPLATE_HKLM if random.random() < 0.3]
        ext, extf = list(TEMPLATE_EXT), [f for f in EXTFACES if random.random() < 0.5]
    else:                           # random
        faces = [f for f in WINFACES if random.random() < 0.5]
        hklm = [h for h in TEMPLATE_HKLM if random.random() < 0.6]
        ext = [e for e in TEMPLATE_EXT if random.random() < 0.6]
        extf = [f for f in EXTFACES if random.random() < 0.5]
        if random.random() < 0.15:
            faces, extf = [], []
    for name, f, scal in faces:
        lines.append('face 0 %d %s|%s%s' % (scal, name, WIN, f))
    for name, path, scal in extf:
        lines.append('face 1 %d %s|%s' % (scal, name, '\\??\\' + path))
        reg = name + (' (TrueType)' if scal else '')
        if random.random() < 0.5 and not any(k.lower() == reg.lower() for k, _ in ext):   # already registered under the same path
            ext.append((reg, path))
    for k, v in hklm:
        lines.append('winnt %s|%s' % (k, v))
        lines.append('win9x %s|%s' % (k, v))
    for k, v in ext:
        lines.append('ext %s|%s' % (k, v))
    lines.append('run')
    return lines


kinds = ['template'] * 2 + ['empty'] * 2 + ['damaged'] * 100 + ['random'] * 296
scen = [scenario(k, i) for i, k in enumerate(kinds)]


def parse(output):
    runs = []
    for chunk in output.split('==\n'):
        if '--\n' not in chunk:
            continue
        body = chunk.split('--\n', 1)[1]
        runs.append(sorted(l for l in body.splitlines() if l))
    return runs


def model(lines, repair, ios):
    """an independent model of update_external_font_keys for one scenario"""
    faces, st = [], {'winnt': {}, 'win9x': {}, 'ext': {}}
    for l in lines:
        if l.startswith('face '):
            flags, scal, rest = l[5:].split(' ', 2)
            name, f = rest.split('|', 1)
            faces.append(dict(flags=int(flags), scal=int(scal), name=name, file=f))
        elif l.split(' ', 1)[0] in st:
            k, rest = l.split(' ', 1)
            n, d = rest.split('|', 1)
            st[k][n] = d
    low = lambda s: s.lower()
    if ios and not faces:
        return st
    def find(n):
        return next((f for f in faces if low(f['name']) == low(n)), None)
    def nt(p):
        return '\\??\\' + p if len(p) > 1 and p[1] == ':' else p
    queue_full, queue_list, found = [], [], set()
    for name, data in list(st['ext'].items()):
        base = name[:-len(' (TrueType)')] if low(name).endswith(' (truetype)') else name
        face = find(base)
        if face:
            if low(face['file']) == low(nt(data)):
                found.add(id(face))
                continue
            if not face['flags'] & 1:
                if ios and repair and low(face['file']).startswith(WIN.lower()) and '\\' not in face['file'][len(WIN):] and len(face['file']) > len(WIN):
                    queue_list.append(name)
                continue
        queue_full.append(name)
    for name in queue_list:
        st['ext'].pop(name, None)
    for name in queue_full:
        for k in ('win9x', 'winnt', 'ext'):
            st[k].pop(name, None)
    for f in faces:
        if not f['flags'] & 1 or id(f) in found:
            continue
        value = f['name'] + (' (TrueType)' if f['scal'] else '')
        file = f['file'][4:] if f['file'][5:6] == ':' else f['file']
        file = file.split('\\')[-1] if f['file'][0] != '\\' else file
        for k in ('winnt', 'win9x', 'ext'):
            st[k][value] = file
    if ios and repair:
        for f in faces:
            if not f['scal'] or f['flags'] & 1:
                continue
            if f['file'].lower().startswith(WIN.lower()) and '\\' not in f['file'][len(WIN):] and len(f['file']) > len(WIN):
                value = f['name'] + ' (TrueType)'
                if not any(low(value) == low(n) for n in st['winnt']):
                    st['winnt'][value] = f['file'][len(WIN):]
                    st['win9x'][value] = f['file'][len(WIN):]
    return st


def flat(st):
    return sorted('%s %s = %s' % (k, n, d) for k in st for n, d in st[k].items())


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    (t / 'scen.txt').write_text('\n'.join(l for s in scen for l in s) + '\n')
    c = t / 'f.c'
    c.write_text(harness)
    inc = ['-I', str(root / 'wine/include')]
    exes = {}
    for name, flag in (('plain', []), ('ios', ['-DWINE_IOS=1'])):
        exe = t / name
        r = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
                            '-Wno-incompatible-pointer-types-discards-qualifiers', '-Wno-pointer-sign', '-fsanitize=address,undefined'] + flag + inc +
                           ['-o', str(exe), str(c)], capture_output=True, text=True)
        check(f'update_external_font_keys compiles on the host ({name}, -Wall, AddressSanitizer)', r.returncode == 0, r.stderr[-2500:])
        exes[name] = exe
    if len(exes) == 2 and all(e.exists() for e in exes.values()):
        def run(name, repair):
            env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_FONT_KEYS_REPAIR'}
            if repair is not None:
                env['MADEIRA_FONT_KEYS_REPAIR'] = repair
            r = subprocess.run([str(exes[name]), str(t / 'scen.txt')], capture_output=True, text=True, env=env, timeout=300)
            return r.returncode, parse(r.stdout), r.stderr
        rp, plain_runs, _ = run('plain', None)
        ri, ios_off, err_off = run('ios', None)
        r0, ios_zero, _ = run('ios', '0')
        r1, ios_on, err_on = run('ios', '1')
        check('all four runs end cleanly (AddressSanitizer)', rp == ri == r0 == r1 == 0 and len(plain_runs) == len(scen) == len(ios_off) == len(ios_on),
              (rp, ri, r0, r1, len(plain_runs), len(ios_off), err_off[-600:]))
        # the plain function against the model (so the harness itself is honest)
        bad_plain = [i for i, s_ in enumerate(scen) if plain_runs[i] != flat(model(s_[:-1], False, False))]
        check('the plain function agrees with the model on all %d scenarios' % len(scen), not bad_plain, str(bad_plain[:5]))
        nof = [i for i, s_ in enumerate(scen) if not any(l.startswith('face ') for l in s_)]
        withf = [i for i in range(len(scen)) if i not in nof]
        check('(1) repair off, at least one face (%d scenarios): the three keys come out exactly as the plain function leaves them' % len(withf),
              all(ios_off[i] == plain_runs[i] for i in withf) and all(ios_zero[i] == plain_runs[i] for i in withf),
              [i for i in withf if ios_off[i] != plain_runs[i]][:5])
        untouched = []
        for i in nof:
            want = sorted('%s %s = %s' % (l.split(' ', 1)[0], l.split(' ', 1)[1].split('|')[0], l.split('|', 1)[1]) for l in scen[i] if l.split(' ', 1)[0] in ('winnt', 'win9x', 'ext'))
            untouched.append(ios_off[i] == want and ios_on[i] == want)
        check('(2) no face at all (%d scenarios): the iOS function touches nothing, with the repair on or off' % len(nof), nof and all(untouched), untouched[:5])
        damage = [i for i in nof if plain_runs[i] != ios_off[i]]
        check('    and the plain function does delete there (the damage the guard prevents)', len(damage) == len(nof) and
              all('winnt Tahoma (TrueType)' not in ' '.join(plain_runs[i]) for i in nof[:1]) and any('winnt Tahoma (TrueType)' in ' '.join(ios_off[i]) for i in nof[:1]),
              [(i, len(plain_runs[i]), len(ios_off[i])) for i in nof[:3]])
        bad_on = [i for i, s_ in enumerate(scen) if ios_on[i] != flat(model(s_[:-1], True, True))]
        check('(3) repair on: all %d scenarios agree with the model (stale Windows-font entries leave External Fonts only; missing scalable '
              'Windows-directory values are written to both Fonts keys)' % len(scen), not bad_on, str(bad_on[:5]))
        tpl = [i for i, k in enumerate(kinds) if k == 'template'][0]
        seeded = [l for l in ios_on[tpl] if l.startswith('winnt ') and any(x in l for x in ('Arial (TrueType)', 'Verdana', 'Georgia', 'Times New Roman', 'MS Mincho'))]
        seeded_off = [l for l in ios_off[tpl] if l.startswith('winnt ') and any(x in l for x in ('Arial (TrueType)', 'Verdana', 'Georgia', 'Times New Roman', 'MS Mincho'))]
        check("    with the template's shape the repair adds the seeded fonts to the Fonts key (why it is opt-in), and the default does not",
              len(seeded) >= 5 and not seeded_off, (seeded, seeded_off))
        check('[fonts] log lines: the summary names the switch, the guard names the empty session',
              'MADEIRA_FONT_KEYS_REPAIR=1' in err_on and 'MADEIRA_FONT_KEYS_REPAIR=0' in err_off and 'found no fonts' in err_off)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
