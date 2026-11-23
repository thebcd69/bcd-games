#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_CODESIGN_ROOTS = 1: wintrust trusts one pinned code-signing root for a signer's chain, nothing else (tools/patch-wine-wintrust-root-pin.py).

Round 707 (M3 S18; METAL GEAR SOLID V E4QKPA: WinVerifyTrust(steam_api64.dll) = 800b0109, root VeriSign Class 3 Public Primary Certification
Authority - G5 untrusted). Checks:
  - the certificate kept as tests/fixtures/certs/verisign-class3-g5.der is the G5 root and hashes to the script's pin;
  - the patch applies to the pinned softpub.c (the Wine tree of this checkout, WINE_SRC or ~/madeira-bcd/wine; skipped without one), once;
  - the helper, cut out of the patched file, compiled on the host against model chain types (CryptHashCertificate2 is CommonCrypto's SHA-256):
    key unset, 0 or 11: nothing changes; key 1: a chain ending in the G5 root whose only fault is the untrusted root loses that bit on the root,
    its simple chain and the chain, one log line; a different self-signed root, a G5 root with another fault too, a root that is not
    self-signed and an empty chain keep their status; the root store is never touched (the helper names no store function);
  - CI applies the patch after the wintrust diagnostics, the rebuilt 64-bit set applies it too, and runs this check.
"""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("pin", root / "tools/patch-wine-wintrust-root-pin.py")
pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin)
der = (root / "tests/fixtures/certs/verisign-class3-g5.der").read_bytes()
check("the kept certificate hashes to the pin (SHA-256 9acfab7e...e41ab7df)", hashlib.sha256(der).digest() == pin.PIN and
      pin.PIN.hex() == "9acfab7e43c8d880d06b262a94deeee4b4659989c3d0caf19baf6405e41ab7df")
check("and it is the G5 root (its subject in the DER)", b"VeriSign Class 3 Public Primary Certification Authority - G5" in der)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI applies the patch after the wintrust diagnostics and runs this check",
      "python3 tools/patch-wine-wintrust-root-pin.py wine" in wf and wf.index("python3 tools/patch-wine-wintrust-diag.py wine") <
      wf.index("python3 tools/patch-wine-wintrust-root-pin.py wine") and "python3 tests/host/check-wintrust-root-pin.py" in wf)
fresh = (root / "tools/build-wine-fresh-dlls.sh").read_text()
check("the rebuilt 64-bit set applies it after the diagnostics", "patch-wine-wintrust-root-pin.py @W@" in fresh and
      fresh.index("patch-wine-wintrust-diag.py @W@") < fresh.index("patch-wine-wintrust-root-pin.py @W@"))

wine = next((w for w in (Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, root / "wine", Path.home() / "madeira-bcd/wine")
             if w and (w / "dlls/wintrust/softpub.c").is_file()), None)
if not wine:
    print("SKIP: no Wine tree (WINE_SRC): the patch and the helper are not tried")
else:
    tmp = Path(tempfile.mkdtemp(prefix="madeira-root-pin-"))
    (tmp / "dlls/wintrust").mkdir(parents=True)
    src = subprocess.run(["git", "-C", str(wine), "show", "HEAD:dlls/wintrust/softpub.c"], capture_output=True, text=True).stdout or \
          (wine / "dlls/wintrust/softpub.c").read_text()
    (tmp / "dlls/wintrust/softpub.c").write_text(src)
    r1 = subprocess.run([sys.executable, str(root / "tools/patch-wine-wintrust-root-pin.py"), str(tmp)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(root / "tools/patch-wine-wintrust-root-pin.py"), str(tmp)], capture_output=True, text=True)
    check("the patch applies to the pinned softpub.c once", "applied" in r1.stdout and "already" in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
    patched = (tmp / "dlls/wintrust/softpub.c").read_text()
    start = patched.index("static const BYTE madeira_codesign_pin[32]")
    helper = patched[start:patched.index("\n}\n", patched.index("static void madeira_codesign_root_pin(")) + 3]
    check("the helper names no store function (the root store is not touched)", "CertAddEncoded" not in helper and "CertOpenStore" not in helper)
    program = r'''
#include <CommonCrypto/CommonDigest.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
typedef unsigned char BYTE; typedef unsigned int DWORD; typedef int BOOL; typedef long LONG; typedef unsigned short WCHAR;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define CERT_TRUST_IS_NOT_TIME_VALID 0x1
#define CERT_TRUST_IS_UNTRUSTED_ROOT 0x20
#define CERT_TRUST_IS_SELF_SIGNED 0x8
typedef struct { DWORD dwErrorStatus, dwInfoStatus; } CERT_TRUST_STATUS;
typedef struct { DWORD dwCertEncodingType; BYTE *pbCertEncoded; DWORD cbCertEncoded; } CERT_CONTEXT, *PCCERT_CONTEXT;
typedef struct { DWORD cbSize; PCCERT_CONTEXT pCertContext; CERT_TRUST_STATUS TrustStatus; } CERT_CHAIN_ELEMENT, *PCERT_CHAIN_ELEMENT;
typedef struct { DWORD cbSize; CERT_TRUST_STATUS TrustStatus; DWORD cElement; PCERT_CHAIN_ELEMENT *rgpElement; } CERT_SIMPLE_CHAIN, *PCERT_SIMPLE_CHAIN;
typedef struct { DWORD cbSize; CERT_TRUST_STATUS TrustStatus; DWORD cChain; PCERT_SIMPLE_CHAIN *rgpChain; } CERT_CHAIN_CONTEXT;
typedef const CERT_CHAIN_CONTEXT *PCCERT_CHAIN_CONTEXT;
static int lines;
#define ERR(...) (lines++)
static LONG InterlockedIncrement(LONG *v) { return ++*v; }
static DWORD GetEnvironmentVariableW(const WCHAR *name, WCHAR *value, DWORD n)
{
    const char *e = getenv("MADEIRA_CODESIGN_ROOTS"); size_t i, len;
    (void)name;
    if (!e) return 0;
    len = strlen(e);
    if (len >= n) return (DWORD)len + 1;
    for (i = 0; i <= len; i++) value[i] = (WCHAR)e[i];
    return (DWORD)len;
}
static BOOL CryptHashCertificate2(const void *alg, DWORD flags, void *reserved, const BYTE *data, DWORD size, BYTE *hash, DWORD *hash_size)
{
    (void)alg; (void)flags; (void)reserved;
    if (*hash_size < 32) return 0;
    CC_SHA256(data, size, hash); *hash_size = 32; return 1;
}
#define L
@HELPER@
int main(int argc, char **argv)
{
    static BYTE buf[4096]; size_t n; FILE *f = fopen(argv[1], "rb"); int what = atoi(argv[2]);
    n = fread(buf, 1, sizeof(buf), f); fclose(f);
    if (what == 1) buf[n - 1] ^= 1;   /* another root: not the pinned bytes */
    CERT_CONTEXT cert = { 1, buf, (DWORD)n };
    CERT_CHAIN_ELEMENT leaf = { sizeof(leaf), NULL, { 0, 0 } }, rootel = { sizeof(rootel), &cert, { CERT_TRUST_IS_UNTRUSTED_ROOT, CERT_TRUST_IS_SELF_SIGNED } };
    if (what == 2) rootel.TrustStatus.dwErrorStatus |= CERT_TRUST_IS_NOT_TIME_VALID;
    if (what == 3) rootel.TrustStatus.dwInfoStatus = 0;
    PCERT_CHAIN_ELEMENT els[2] = { &leaf, &rootel };
    CERT_SIMPLE_CHAIN simple = { sizeof(simple), { rootel.TrustStatus.dwErrorStatus, 0 }, what == 4 ? 0 : 2, els };
    PCERT_SIMPLE_CHAIN chains[1] = { &simple };
    CERT_CHAIN_CONTEXT chain = { sizeof(chain), { rootel.TrustStatus.dwErrorStatus, 0 }, 1, chains };
    madeira_codesign_root_pin(&chain);
    printf("%x %x %x %d\n", rootel.TrustStatus.dwErrorStatus, simple.TrustStatus.dwErrorStatus, chain.TrustStatus.dwErrorStatus, lines);
    return 0;
}
'''
    (tmp / "m.c").write_text(program.replace("@HELPER@", helper))
    b = subprocess.run(["cc", "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-Wno-deprecated-declarations", "-o", str(tmp / "m"), str(tmp / "m.c")],
                       capture_output=True, text=True)
    check("the helper compiles on the host against model chain types", b.returncode == 0, b.stderr)
    if b.returncode == 0:
        fx = str(root / "tests/fixtures/certs/verisign-class3-g5.der")

        def run(what, key=None):
            e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
            if key is not None:
                e["MADEIRA_CODESIGN_ROOTS"] = key
            return subprocess.run([str(tmp / "m"), fx, str(what)], capture_output=True, text=True, env=e).stdout.strip()
        for key in (None, "0", "11", "2"):
            check("key %r: the G5 chain stays untrusted" % (key,), run(0, key) == "20 20 20 0", run(0, key))
        check("key 1: the G5 root's untrusted-root bit is cleared on the root, its chain and the chain; one line", run(0, "1") == "0 0 0 1", run(0, "1"))
        check("key 1: another self-signed root keeps its fault", run(1, "1") == "20 20 20 0", run(1, "1"))
        check("key 1: the G5 root with another fault too (not time valid) is left as it was", run(2, "1") == "21 21 21 0", run(2, "1"))
        check("key 1: a root that is not self-signed is left as it was", run(3, "1") == "20 20 20 0", run(3, "1"))
        check("key 1: an empty chain is left as it was", run(4, "1") == "20 20 20 0", run(4, "1"))
    shutil.rmtree(tmp, ignore_errors=True)

print("PASS: wintrust code-signing root pin" if ok else "FAIL: wintrust code-signing root pin")
sys.exit(0 if ok else 1)
