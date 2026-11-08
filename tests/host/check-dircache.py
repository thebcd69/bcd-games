#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Adapted from upstream Madeira's tests/host/check-dircache.py (Ixion, Madeira 0.2.0; GPL-3.0-or-later with the Madeira Converter Exception):
# the cache harness is theirs; the switch checks, the opt-in default and the BCD wiring are BCD's.
# Copyright 2026 Ixion. This file was taken from https://github.com/willfaust/Madeira (pull request #461)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""Case-insensitive lookups can be answered from cached directory listings (MADEIRA_DIRCACHE=1, OFF by default; build 632); no Wine.

find_file_in_dir answers a name it cannot stat in its exact case by asking for the volume's case sensitivity and reading the whole directory,
and lookup_unix_name does both again for the reparse name ("name?"). Source-engine games probe thousands of absent paths; upstream measured
Team Fortress 2 on an iPhone 15 Pro Max at ten minutes to a map, 55 s with the cache. BCD's own [file-census] lines (round 624, 1,359 lines
in 501 logs) show the same cost in games of every engine: 48 logs have a ten-second window in which the lookups took 20 % or more of the
window, up to 95 % (Spider-Man Remastered, Midnight, Euro Truck Simulator 2, The Witcher 3, Garry's Mod, No More Room in Hell, DNF, Prey).
The listings are kept per directory (dircache_find) and trusted for a short window while nothing changed through Wine; after that one stat of
the directory decides whether it is read again.

BCD takes the Wine fork commit as it is and adds one: the cache is OFF unless MADEIRA_DIRCACHE=1 (upstream: on, =0 turns it off). A session
without the key takes exactly the path it took before: find_file_in_dir tests dircache_enabled() first, the banner prints only when the cache is
on, and dircache_changed() does nothing while it is off.

The runtime part cuts the cache out of dlls/ntdll/unix/file.c and compiles it on the host with stand-ins (and AddressSanitizer), then checks it
against a brute-force case-insensitive search of real directories: exact and other-case names, absent names, names created and deleted by another
writer (seen at once after dircache_changed, within the trust window otherwise, at once with a window of 0), a renamed directory, a directory that
cannot be listed or is not a directory (left to the scan), a flush past the cache's size limit, and four threads looking up while a fifth creates
and deletes files. A second harness checks the switch: unset, "", "0", "00", "11" and "true" leave it off with no log line and no generation
count; "1" turns it on with the banner and the trust window.

In the source: find_file_in_dir asks the cache only for root lookups that may ignore case and are not short names, before its own shortcut, and
only when the switch is on; the cache locks with pthread_mutex_lock (process_exiting is shared by every pseudo-process); open_unix_file and every
delete, mkdir, rename and link in the iOS wineserver report changes; the catalog lists both switches with default 0; lists may set the switch
(0 or 1) and the two Source-engine rows that exist (Counter-Strike: Source, No More Room in Hell) do from build 632.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
if not (root / "wine/dlls/ntdll/unix/file.c").is_file():
    print("SKIP: no wine sources (check out the submodule)")
    sys.exit(0)
src = (root / "wine/dlls/ntdll/unix/file.c").read_text()
fd = (root / "build/wineserver/fd_ios.c").read_text()
ok = True


def check(what, cond, detail=None):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if detail is not None and not cond:
        print("     " + str(detail))
    ok &= bool(cond)


start = src.index("/* Madeira: directory listings kept in memory for case-insensitive lookups.")
end = src.index("#endif /* WINE_IOS */", start)
cut = src[start:end]
check("cache found in file.c", "static int dircache_find(" in cut and "void madeira_dircache_changed(void)" in cut)
check("locked with pthread_mutex_lock only",
      cut.count("pthread_mutex_lock( &dircache_mutex )") == 3 and not re.search(r"(?<!pthread_)mutex_lock\(", cut))

# the real find_file_in_dir: in the build tools/patch-wine-file-census.py puts a timing wrapper of the same name in front of it
ffd = ""
i = src.find("static NTSTATUS find_file_in_dir(")
while i >= 0:
    body = src[i:]
    body = body[:body.index("\n}\n")]
    if "/* try a shortcut for this directory */" in body:
        ffd = body
        break
    i = src.find("static NTSTATUS find_file_in_dir(", i + 1)
check("find_file_in_dir (the one with the shortcut) is found", bool(ffd))
hook = ffd.index("dircache_find( unix_name, pos, name, length, unix_name + pos + 1 )")
check("find_file_in_dir asks the cache before its shortcut, for root lookups that ignore case, and tests the switch first",
      hook < ffd.index("/* try a shortcut for this directory */")
      and "dircache_enabled() && !check_case && root_fd == AT_FDCWD && pos > 1 && unix_name[0] == '/'" in ffd[:hook])
check("short names (PROGRA~1) skip the cache",
      "!(length >= 8 && name[4] == '~' && is_legal_8dot3_name( name, length ))" in ffd[:hook])
sw = cut[cut.index("static BOOL dircache_enabled(void)"):]
sw = sw[:sw.index("\n}\n")]
check("the switch is opt-in: only MADEIRA_DIRCACHE=1 turns it on, the banner prints only then",
      "dircache_on = v && v[0] == '1' && !v[1];" in sw and "if (dircache_on)\n            ERR( \"[dircache] on," in sw and "[dircache] off" not in cut)
chg = cut[cut.index("static void dircache_changed(void)"):]
chg = chg[:chg.index("\n}\n")]
check("dircache_changed() does nothing while the cache is off", "if (dircache_on <= 0) return;" in chg and chg.index("return;") < chg.index("__atomic_add_fetch"))

ouf = src[src.index("NTSTATUS open_unix_file("):]
ouf = ouf[:ouf.index("\n}\n")]
check("open_unix_file reports a create", "disposition != FILE_OPEN && disposition != FILE_OVERWRITE) dircache_changed();" in ouf)

check("server declares the hook weak", "extern void madeira_dircache_changed(void) __attribute__((weak));" in fd)
def follows(call, within=200):
    """every occurrence of call is followed by dircache_changed() soon after"""
    spots = [m.start() for m in re.finditer(re.escape(call), fd)]
    return bool(spots) and all("dircache_changed();" in fd[s:s + within] for s in spots)
check("server reports delete on close", follows("else unlink( fd->unix_name );", 60))
check("server reports the read-only link replacement", follows("if (unlink( name ) == -1) goto keep;", 60))
check("server reports mkdir", follows("if (mkdir( name, *mode ) == -1)", 300))
check("server reports renames and links",
      follows("if (rename( fd->unix_name, reparse_name ))", 200)
      and follows("if (rename( fd->unix_name, base_name ) < 0)", 160)
      and follows("if (link( fd->unix_name, name ))", 160)
      and follows("    if (rename( fd->unix_name, name ))\n", 200))

gen = (root / "build/tools/gen-config-catalog.py").read_text()
for key in ("env.MADEIRA_DIRCACHE", "env.MADEIRA_DIRCACHE_TTL_MS"):
    i = gen.find('"%s": {' % key)
    check("the catalog generator describes %s with its default (0, resp. 1000)" % key, i >= 0 and ('"default": "0"' if key.endswith("DIRCACHE") else '"default": "1000"') in gen[i:i + 900])
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("lists may set MADEIRA_DIRCACHE, and only to 0 or 1", '"MADEIRA_DIRCACHE",' in pol and "MADEIRA_DIRCACHE is 0 or 1" in pol)
import json
rec = json.loads((root / "recommended/recommendations.json").read_text())
rows = {r["id"]: r for r in rec["rules"]}
for rid in ("counter-strike-source", "no-more-room-in-hell"):
    lines = [l for l in rows[rid]["config"] if isinstance(l, dict)]
    check("the %s row turns the cache on from build 632" % rid, {"line": "env.MADEIRA_DIRCACHE = 1", "minBuild": 632} in lines)
check("no other row sets it", sorted(r["id"] for r in rec["rules"] if "MADEIRA_DIRCACHE" in json.dumps(r["config"])) == ["counter-strike-source", "no-more-room-in-hell"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "python3 tests/host/check-dircache.py" in wf)

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <dirent.h>
#include <limits.h>
#include <sys/stat.h>
typedef uint16_t WCHAR; typedef int BOOL; typedef uint64_t ULONGLONG;
#define TRUE 1
#define FALSE 0
#define MAX_DIR_ENTRY_LEN 255
#define ERR(...) fprintf(stderr, __VA_ARGS__)
static WCHAR ntdll_towupper(WCHAR c) { return (c >= 'a' && c <= 'z') ? c - 32 : (c >= 0xe0 && c <= 0xfe && c != 0xf7) ? c - 32 : c; }
static int wcsnicmp(const WCHAR *a, const WCHAR *b, int n)
{
    int r = 0;
    for (; n > 0; n--, a++, b++) if ((r = ntdll_towupper(*a) - ntdll_towupper(*b)) || !*a) break;
    return r;
}
/* UTF-8 (up to 3 bytes) to UTF-16, enough for the names below */
static int ntdll_umbstowcs(const char *s, int len, WCHAR *out, int max)
{
    int n = 0, i = 0;
    while (i < len && n < max)
    {
        unsigned char c = s[i];
        if (c < 0x80) { out[n++] = c; i++; }
        else if ((c & 0xe0) == 0xc0) { out[n++] = ((c & 0x1f) << 6) | (s[i+1] & 0x3f); i += 2; }
        else { out[n++] = ((c & 0x0f) << 12) | ((s[i+1] & 0x3f) << 6) | (s[i+2] & 0x3f); i += 3; }
    }
    return n;
}
#include "cut.c"

static int fails;
#define CHECK(what, cond) do { if (!(cond)) { printf("FAIL %s (line %d)\n", what, __LINE__); fails++; } } while (0)

static int w(const char *s, WCHAR *out) { return ntdll_umbstowcs(s, strlen(s), out, MAX_DIR_ENTRY_LEN); }

/* what find_file_in_dir's scan would answer */
static int brute(const char *dir, const char *name, char *out)
{
    WCHAR want[256], have[256];
    int wl = w(name, want), r = 0;
    DIR *d = opendir(dir);
    struct dirent *de;
    if (!d) return -1;
    while ((de = readdir(d)))
    {
        int hl = w(de->d_name, have);
        if (hl == wl && !wcsnicmp(have, want, wl)) { strcpy(out, de->d_name); r = 1; break; }
    }
    closedir(d);
    return r;
}

static int find(const char *dir, const char *name, char *out)
{
    WCHAR wn[256];
    int wl = w(name, wn);
    return dircache_find(dir, strlen(dir), wn, wl, out);
}

static void touch(const char *dir, const char *name)
{
    char p[PATH_MAX];
    snprintf(p, sizeof(p), "%s/%s", dir, name);
    close(open(p, O_CREAT | O_WRONLY, 0644));
}

static void rmf(const char *dir, const char *name)
{
    char p[PATH_MAX];
    snprintf(p, sizeof(p), "%s/%s", dir, name);
    unlink(p);
}

static const char *base;
static volatile int stop;

static void *reader(void *arg)
{
    char out[300];
    static const char *names[] = { "Stay.TXT", "stay.txt", "churn-1", "CHURN-2", "absent" };
    unsigned i;
    (void)arg;
    for (i = 0; !stop; i++)
    {
        const char *n = names[i % 5];
        int r = find(base, n, out);
        if (i % 5 < 2 && r != 1) { printf("FAIL thread: %s r=%d\n", n, r); __atomic_add_fetch(&fails, 1, __ATOMIC_RELAXED); }
        if (i % 5 == 4 && r != 0) { printf("FAIL thread: absent r=%d\n", r); __atomic_add_fetch(&fails, 1, __ATOMIC_RELAXED); }
        if (r == 1 && strcasecmp(out, n)) { printf("FAIL thread: %s -> %s\n", n, out); __atomic_add_fetch(&fails, 1, __ATOMIC_RELAXED); }
    }
    return NULL;
}

int main(int argc, char **argv)
{
    char out[300], out2[300], dir[PATH_MAX], sub[PATH_MAX];
    const char *t = argv[1];
    int i, r, rb;
    (void)argc;

    dircache_on = 1;
    dircache_ttl_ms = 60000;   /* only changes and explicit windows below expire entries */

    snprintf(dir, sizeof(dir), "%s/Game", t);
    mkdir(dir, 0755);
    touch(dir, "Foo.TXT"); touch(dir, "bar"); touch(dir, "Caf\xc3\xa9.vmt");
    snprintf(sub, sizeof(sub), "%s/Materials", dir);
    mkdir(sub, 0755);

    /* against brute force: hits in any case, misses, the reparse name */
    {
        static const char *q[] = { "Foo.TXT", "foo.txt", "FOO.TXT", "bar", "BAR", "materials", "MATERIALS",
                                   "caf\xc3\xa9.VMT", "CAF\xc3\x89.vmt", "missing", "foo.txt?", "fo", "Foo.TXTx", "." };
        for (i = 0; i < (int)(sizeof(q) / sizeof(q[0])); i++)
        {
            r = find(dir, q[i], out);
            rb = brute(dir, q[i], out2);
            if (r != rb || (r == 1 && strcmp(out, out2))) { printf("FAIL %s: cache %d '%s', scan %d '%s'\n", q[i], r, r == 1 ? out : "", rb, rb == 1 ? out2 : ""); fails++; }
        }
    }
    CHECK("one read for all of them", dircache_stats.reads == 1);

    /* another writer: unseen within the window, seen after a change report */
    touch(dir, "New.txt");
    CHECK("an outside create is not seen inside the window", find(dir, "new.txt", out) == 0);
    dircache_changed();
    CHECK("a reported change is seen at once", find(dir, "new.txt", out) == 1 && !strcmp(out, "New.txt"));
    rmf(dir, "New.txt");
    dircache_changed();
    CHECK("a reported delete is seen at once", find(dir, "NEW.TXT", out) == 0);

    /* a window of 0 checks the directory every time */
    dircache_ttl_ms = 0;
    touch(dir, "Later.bin");
    CHECK("window 0 sees an outside create", find(dir, "later.BIN", out) == 1 && !strcmp(out, "Later.bin"));
    {
        unsigned reads = dircache_stats.reads, checks = dircache_stats.checks;
        CHECK("window 0, unchanged: a check, no read", find(dir, "bar", out) == 1 && dircache_stats.reads == reads && dircache_stats.checks == checks + 1);
    }
    rmf(dir, "Later.bin");
    CHECK("window 0 sees an outside delete", find(dir, "later.bin", out) == 0);
    dircache_ttl_ms = 60000;

    /* a short window expires on its own */
    dircache_ttl_ms = 30;
    find(dir, "bar", out);
    touch(dir, "Late.dat");
    usleep(60 * 1000);
    CHECK("an expired window sees an outside create", find(dir, "late.dat", out) == 1);
    rmf(dir, "Late.dat");
    dircache_ttl_ms = 60000;

    /* the same path, now another directory */
    {
        char moved[PATH_MAX];
        snprintf(moved, sizeof(moved), "%s/Game-old", t);
        rename(dir, moved);
        mkdir(dir, 0755);
        touch(dir, "Other.cfg");
        dircache_changed();
        CHECK("a directory replaced under the same path is read again", find(dir, "other.cfg", out) == 1 && find(dir, "foo.txt", out) == 0);
    }

    /* left to the scan */
    {
        char locked[PATH_MAX], file[PATH_MAX];
        snprintf(locked, sizeof(locked), "%s/Locked", t);
        mkdir(locked, 0755);
        touch(locked, "x");
        chmod(locked, 0311);     /* searchable, not readable */
        r = find(locked, "X", out);
        CHECK("a directory that cannot be listed is left to the scan", geteuid() == 0 || r == -1);
        CHECK("and stays left to it", geteuid() == 0 || find(locked, "X", out) == -1);
        chmod(locked, 0755);
        snprintf(file, sizeof(file), "%s/Other.cfg", dir);
        CHECK("a file is not a directory", find(file, "a", out) == -1);
        snprintf(file, sizeof(file), "%s/nowhere", t);
        CHECK("a missing directory is left to the scan", find(file, "a", out) == -1);
    }

    /* past the limit the whole cache is dropped, and lookups still work */
    {
        char d2[PATH_MAX];
        unsigned flushes = dircache_stats.flushes;
        snprintf(d2, sizeof(d2), "%s/many", t);
        mkdir(d2, 0755);
        for (i = 0; i <= DIRCACHE_MAX_DIRS; i++)
        {
            char p[PATH_MAX];
            snprintf(p, sizeof(p), "%s/d%d", d2, i);
            mkdir(p, 0755);
            find(p, "nothing", out);
        }
        CHECK("a flush past the directory limit", dircache_stats.flushes == flushes + 1 && dircache_dirs <= DIRCACHE_MAX_DIRS);
        CHECK("lookups after a flush", find(dir, "OTHER.CFG", out) == 1);
    }

    /* threads: readers while a writer creates, deletes and reports */
    {
        pthread_t th[4];
        char churn[PATH_MAX];
        snprintf(churn, sizeof(churn), "%s/Churn", t);
        mkdir(churn, 0755);
        touch(churn, "Stay.TXT");
        base = churn;
        dircache_ttl_ms = 5;
        for (i = 0; i < 4; i++) pthread_create(&th[i], NULL, reader, NULL);
        for (i = 0; i < 3000; i++)
        {
            touch(churn, (i & 1) ? "churn-1" : "churn-2");
            dircache_changed();
            rmf(churn, (i & 1) ? "churn-2" : "churn-1");
            dircache_changed();
        }
        stop = 1;
        for (i = 0; i < 4; i++) pthread_join(th[i], NULL);
    }

    printf("stats: lookups %u found %u absent %u scanned %u checks %u reads %u flushes %u\n",
           dircache_stats.lookups, dircache_stats.found, dircache_stats.absent, dircache_stats.fallback,
           dircache_stats.checks, dircache_stats.reads, dircache_stats.flushes);
    printf(fails ? "FAIL %d runtime checks\n" : "ok   runtime checks\n", fails);
    return fails != 0;
}
'''

cc = shutil.which("clang") or shutil.which("cc")
if not cc:
    check("a C compiler", False)
else:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "cut.c").write_text(cut)
        (Path(d) / "h.c").write_text(harness)
        exe = Path(d) / "h"
        res = subprocess.run([cc, "-O1", "-g", "-fsanitize=address,undefined", "-Wall", "-Wno-unused-function",
                              "-o", str(exe), str(Path(d) / "h.c"), "-lpthread"], capture_output=True, text=True)
        check("harness compiles", res.returncode == 0)
        if res.returncode:
            print(res.stderr[-3000:])
        else:
            work = Path(d) / "fs"
            work.mkdir()
            run = subprocess.run([str(exe), str(work)], capture_output=True, text=True, timeout=300)
            print(run.stdout, end="")
            if run.returncode:
                print(run.stderr[-3000:])
            check("runtime checks pass", run.returncode == 0)

# the switch: a second main() over the same cut
prelude = harness[:harness.index('#include "cut.c"')]
main2 = r'''
#include "cut.c"
int main(void)
{
    unsigned before, after;
    int en = dircache_enabled();
    before = dircache_gen;
    dircache_changed();
    after = dircache_gen;
    printf("enabled=%d ttl=%u gen_moved=%d\n", en, (unsigned)dircache_ttl_ms, after != before);
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "cut.c").write_text(cut)
        (Path(d) / "s.c").write_text(prelude + main2)
        exe = Path(d) / "s"
        res = subprocess.run([cc, "-O1", "-g", "-fsanitize=address,undefined", "-Wall", "-Wno-unused-function", "-o", str(exe), str(Path(d) / "s.c"), "-lpthread"],
                             capture_output=True, text=True)
        check("switch harness compiles", res.returncode == 0)
        if res.returncode:
            print(res.stderr[-3000:])
        else:
            def sw(value, ttl=None):
                env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_DIRCACHE")}
                if value is not None:
                    env["MADEIRA_DIRCACHE"] = value
                if ttl is not None:
                    env["MADEIRA_DIRCACHE_TTL_MS"] = ttl
                r = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
                return r.stdout.strip(), r.stderr.strip()
            for value in (None, "", "0", "00", "11", "true", "yes", " 1"):
                out, err = sw(value)
                check("MADEIRA_DIRCACHE=%r: off, no log line, no generation count" % (value,), out == "enabled=0 ttl=1000 gen_moved=0" and err == "", (out, err))
            out, err = sw("1")
            check("MADEIRA_DIRCACHE=1: on, one banner with the trust window, and a change report moves the generation",
                  out == "enabled=1 ttl=1000 gen_moved=1" and err == "[dircache] on, trust window 1000 ms (MADEIRA_DIRCACHE=1)", (out, err))
            out, err = sw("1", "250")
            check("MADEIRA_DIRCACHE_TTL_MS sets the window", out == "enabled=1 ttl=250 gen_moved=1" and "250 ms" in err, (out, err))
            out, err = sw(None, "250")
            check("the window alone does not turn the cache on", out.startswith("enabled=0") and err == "", (out, err))

sys.exit(0 if ok else 1)
