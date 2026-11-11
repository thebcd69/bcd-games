#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-dns-refuse-crash.py: ws2_32 can refuse Valve's crash-upload host at once (night round 2026-11-11, T2 S-GM1; opt-in).

Garry's Mod (GYG6SG) and RDR2 (2PGRBB) in BCD Dock waited 150 s for crash.steampowered.com:443 on a network that drops it. Checks:
  - CI applies the patch after the DNS trace patch and before ntdll-unix is built, and runs this check;
  - the patch applies to a copy of the pinned dlls/ws2_32/unixlib.c once (the file path or the Wine directory as argument), a second run changes
    nothing, a moved wrapper refuses it by name and leaves the file alone; DEFAULT_REFUSE is 0 (opt-in);
  - the real wrapper, compiled on the host against a fake getaddrinfo: with the key unset or 0 every name, crash.steampowered.com included, reaches the
    lookup exactly as before; with MADEIRA_DNS_REFUSE_CRASH=1 the exact name (any case) is refused with EAI_NONAME, the lookup is never made, one
    `refused` line is logged and no thread is started; other names, a longer name that merely starts with it, steampowered.com and a NULL node are
    untouched; the bounded path (MADEIRA_DNS_TIMEOUT_MS) is untouched;
  - with the default flipped to 1 (the owner's question f13) the same wrapper refuses it unless MADEIRA_DNS_REFUSE_CRASH=0.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("dnsrefuse", root / "tools/patch-wine-dns-refuse-crash.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine so the DNS wrapper can refuse Valve's crash upload host (opt-in)\n")
check("CI runs this check and the patch after the DNS trace patch and before ntdll-unix is built",
      "python3 tests/host/check-wine-dns-refuse-crash.py\n          python3 tools/patch-wine-dns-refuse-crash.py wine/dlls/ws2_32/unixlib.c\n" in wf[step:step + 500]
      and wf.index("python3 tools/patch-wine-dns-trace.py wine/dlls/ws2_32/unixlib.c") < step < wf.index("run: bash build/ntdll-unix/build.sh"))
check("the policy default is opt-in (0)", P.DEFAULT_REFUSE == 0)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
i = cat.find('"env.MADEIRA_DNS_REFUSE_CRASH"')
check("the key is in the settings catalog (default off)", i > 0 and '"default": "0"' in cat[i:i + 700])

wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / P.FILE).is_file():
        wine = cand
        break
if not wine:
    print("SKIP: no wine source (check out the submodule or set WINE_SRC)")
    print("PASS: the DNS wrapper can refuse the crash host (static part)" if ok else "FAIL: dns refuse crash")
    sys.exit(0 if ok else 1)
pristine = (wine / P.FILE).read_text()
if P.MARKER in pristine:
    print("SKIP: the wine tree is already patched; the checks need the unpatched file")
    print("PASS: the DNS wrapper can refuse the crash host (static part)" if ok else "FAIL: dns refuse crash")
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    f = t / "w" / P.FILE
    f.parent.mkdir(parents=True)
    f.write_text(pristine)
    script = str(root / "tools/patch-wine-dns-refuse-crash.py")
    p1 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    patched = f.read_text()
    p2 = subprocess.run([sys.executable, script, str(f)], capture_output=True, text=True)
    check("applies once (the Wine directory or the file as argument), then says already patched",
          p1.returncode == 0 and "patched" in p1.stdout and p2.returncode == 0 and "already patched" in p2.stdout and f.read_text() == patched,
          p1.stdout + p1.stderr + p2.stdout + p2.stderr)
    f.write_text(pristine.replace("static int madeira_dns_getaddrinfo( const char *node,", "static int madeira_dns_getaddrinfo2( const char *node,", 1))
    moved = f.read_text()
    p3 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    check("a moved wrapper fails by name and leaves the file alone", p3.returncode != 0 and "madeira_dns_getaddrinfo of ws2_32/unixlib.c" in p3.stderr
          and f.read_text() == moved, p3.stdout + p3.stderr)
    check("nothing else changed", patched.replace(P.HELPER, "").replace(P.CALL_NEW, P.CALL_OLD) == pristine)

    start = patched.index("/* madeira-bcd: see tools/patch-wine-dns-trace.py. */")
    helper = patched[start:patched.index("#endif", start)]
    helper = re.sub(r"#include <[a-z/]+\.h>\n", "", helper)

    def build(helper_text, name):
        code = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <netdb.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>
static int fake_calls;
static int fake_gai( const char *node, const char *service, const struct addrinfo *hints, struct addrinfo **res )
{
    fake_calls++;
    *res = calloc( 1, sizeof(struct addrinfo) );
    (*res)->ai_family = AF_INET;
    return 0;
}
#define getaddrinfo fake_gai
void *objc_autoreleasePoolPush(void) { return (void *)1; }
void objc_autoreleasePoolPop(void *p) { (void)p; }
@HELPER@
int main( int argc, char **argv )
{
    int i;
    for (i = 1; i < argc; i++)
    {
        struct addrinfo *res = (struct addrinfo *)0x1;
        int before = fake_calls, ret = madeira_dns_getaddrinfo( strcmp( argv[i], "NULL" ) ? argv[i] : NULL, "443", NULL, &res );
        printf( "%s ret=%d lookups=%d res=%s\n", argv[i], ret, fake_calls - before, res ? "set" : "null" );
    }
    return 0;
}
'''.replace("@HELPER@", helper_text)
        (t / (name + ".c")).write_text(code)
        r = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / name), str(t / (name + ".c"))],
                           capture_output=True, text=True)
        return t / name if r.returncode == 0 else r.stderr

    exe = build(helper, "h")
    check("the patched wrapper compiles on the host", isinstance(exe, Path), exe)
    if isinstance(exe, Path):
        def run(names, key=None, e=None):
            env = dict(os.environ)
            env.pop("MADEIRA_DNS_REFUSE_CRASH", None)
            env.pop("MADEIRA_DNS_TIMEOUT_MS", None)
            if key is not None:
                env["MADEIRA_DNS_REFUSE_CRASH"] = key
            env.update(e or {})
            p = subprocess.run([str(exe)] + names, capture_output=True, text=True, env=env)
            return p.returncode, p.stdout.splitlines(), p.stderr.splitlines()
        names = ["crash.steampowered.com", "CRASH.SteamPowered.COM", "crash.steampowered.com.example", "steampowered.com", "api.steampowered.com", "NULL"]
        rc, out, err = run(names)
        check("key unset (default off): every name, the crash host included, reaches the lookup; no refusal line",
              rc == 0 and all(" lookups=1 " in l or l.startswith("NULL") for l in out) and not [l for l in err if "refused" in l], (out, err))
        rc, out, err = run(names, "0")
        check("key 0: the same", rc == 0 and all(" lookups=1 " in l or l.startswith("NULL") for l in out) and not [l for l in err if "refused" in l], (out, err))
        rc, out, err = run(names, "1")
        refused = [l for l in out if l.startswith(("crash.steampowered.com ", "CRASH.SteamPowered.COM "))]
        check("key 1: the exact name, in any case, is refused with EAI_NONAME and the lookup is never made",
              rc == 0 and len(refused) == 2 and all(re.fullmatch(r"\S+ ret=%d lookups=0 res=null" % 8 if sys.platform == "darwin" else r"\S+ ret=-2 lookups=0 res=null", l)
                                                    for l in refused), (out, err))
        check("key 1: two `refused` lines, one per call, with the name", len([l for l in err if "refused #" in l and "EAI_NONAME at once (MADEIRA_DNS_REFUSE_CRASH)" in l]) == 2, err)
        others = [l for l in out if not l.startswith(("crash.steampowered.com ", "CRASH.SteamPowered.COM "))]
        check("key 1: another name, a longer name that starts with it, steampowered.com and a NULL node are untouched",
              len(others) == 4 and all("ret=0 lookups=1 res=set" in l for l in others if not l.startswith("NULL")) and others[-1].startswith("NULL ret=0 lookups=1"), others)
        rc, out, err = run(["crash.steampowered.com"], "1", {"MADEIRA_DNS_TIMEOUT_MS": "300"})
        check("key 1 with the bounded lookup on: still refused at once, no thread", rc == 0 and out and "lookups=0" in out[0], (out, err))
        flipped = build(helper.replace("e && e[0] ? e[0] == '1' : 0;", "e && e[0] ? e[0] == '1' : 1;"), "hflip")
        check("with the default flipped to 1 the same wrapper compiles", isinstance(flipped, Path), flipped)
        if isinstance(flipped, Path):
            def runf(key=None):
                env = dict(os.environ)
                env.pop("MADEIRA_DNS_REFUSE_CRASH", None)
                if key is not None:
                    env["MADEIRA_DNS_REFUSE_CRASH"] = key
                return subprocess.run([str(flipped), "crash.steampowered.com"], capture_output=True, text=True, env=env).stdout
            check("default on: refused unless MADEIRA_DNS_REFUSE_CRASH=0", "lookups=0" in runf() and "lookups=0" in runf("1") and "lookups=1" in runf("0"),
                  (runf(), runf("1"), runf("0")))

    xcrun = shutil.which("xcrun")
    if not xcrun or not (wine / "build-macos/include/config.h").is_file():
        print("SKIP: the iOS-SDK syntax check needs Xcode and the full tree's wine/build-macos/include (the Mac)")
    else:
        src = t / "unixlib.c"
        src.write_text(patched)
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        nb = root / "build/ntdll-unix"
        wb = wine / "build-macos"
        cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
               "-Wno-implicit-function-declaration", "-Wno-int-conversion", "-include", str(wb / "include/config.h"),
               "-include", str(nb / "shims/wine_ios_exit.h"), "-I" + str(nb / "shims"), "-I" + str(root / "build/madsync"), "-DHAVE_LINUX_NTSYNC_H=1",
               "-I" + str(wb / "include"), "-I" + str(wine / "include"), "-I" + str(wine / "dlls/ws2_32"), "-D__WINESRC__", "-D_NTSYSTEM_",
               "-D_ACRTIMP=", "-DWINBASEAPI=", "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-D__wine_unix_call_funcs=ws2_32_unix_call_funcs",
               "-D__wine_unix_call_wow64_funcs=ws2_32_unix_call_wow64_funcs", "-Wall", str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        check("the patched unixlib.c passes clang -fsyntax-only with the ntdll unix build's flags (no error, no warning)",
              r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: the DNS wrapper can refuse the crash host" if ok else "FAIL: dns refuse crash")
sys.exit(0 if ok else 1)
