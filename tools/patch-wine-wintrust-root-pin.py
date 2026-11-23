#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A code-signing root Windows trusts, for one game's signature checks only (round 707, M3 S18; METAL GEAR SOLID V).

METAL GEAR SOLID V (Steam 287700; Send Log E4QKPA, build 686; HP80QX, TJBXC6, BMHZGA, P9C556, 6W1X19, 9JEMQC) checks the signature of its
steam_api64.dll with WinVerifyTrust and stops ("Failed to load SteamAPI") when it fails. Round 660's diagnostic (tools/patch-wine-wintrust-diag.py)
named the stop: WinVerifyTrust = 800b0109 (CERT_E_UNTRUSTEDROOT), chain "Valve Corporation" -> "VeriSign Class 3 Code Signing 2010 CA" -> root
"VeriSign Class 3 Public Primary Certification Authority - G5" with error status 0x20 (an untrusted root). BCD's root store is Mozilla's web list
(app/Madeira/cacert.pem), which has never held that root; Windows trusts it for code signing.

The owner's decision of 2026-11-22: that one root, for code signatures only, never for TLS, behind a key the game's row sets. So nothing is added to
the root store (Wine's chain engine does not read a root's usage restriction, so a root in the store would be a TLS root too). Instead, in
wintrust's own chain for a signer (WINTRUST_CreateChainForSigner, the Authenticode path: WinVerifyTrust and the catalog checks), when
env.MADEIRA_CODESIGN_ROOTS = 1 and the chain ends in a self-signed root whose only fault is CERT_TRUST_IS_UNTRUSTED_ROOT and whose SHA-256 is
the pinned one, that bit is cleared on the root element, its simple chain and the chain, and the policy that follows judges the chain as
Windows would. Every other root, every other error (an expired certificate, a bad signature, a revoked one) and every other caller of the
chain engine (TLS: winhttp, wininet, schannel) are untouched.

The pin: SHA-256 9A:CF:AB:7E:43:C8:D8:80:D0:6B:26:2A:94:DE:EE:E4:B4:65:99:89:C3:D0:CA:F1:9B:AF:64:05:E4:1A:B7:DF of the root "VeriSign Class
3 Public Primary Certification Authority - G5" (not before 2006-11-08, not after 2036-07-16, SHA-1 4E:B6:D5:78:49:9B:1C:CF:5F:58:1E:AD:56:BE:3D:9B:
67:44:A5:E5). Taken on 2026-11-22 from Microsoft's own root distribution (ctldl.windowsupdate.com/msdownload/update/v3/static/trustedr/en/,
the certificate by its SHA-1 name, and the SHA-1 is in the signed list authrootstl.cab), self-signature verified; DigiCert, which owns the
root today, publishes it on a page that refused an automated download, and its knowledge base lists the cross-certificate it signed. The
certificate is tests/fixtures/certs/verisign-class3-g5.der (tests/host/check-wintrust-root-pin.py hashes it against the pin).

WHERE THIS REACHES: the 32-bit wintrust through the i386 farm and the 64-bit wintrust.dll through the rebuilt set (tools/build-wine-fresh-dlls.sh,
env.MADEIRA_WIN64_FRESH = 1, which the MGSV row has); the committed 64-bit wintrust.dll is a frozen binary. After tools/patch-wine-wintrust-diag.py.
Idempotent; fails by name when an anchor moved, the file unchanged.
Usage: patch-wine-wintrust-root-pin.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: code-signing root pin"
SOFTPUB = "dlls/wintrust/softpub.c"
PIN = bytes.fromhex("9acfab7e43c8d880d06b262a94deeee4b4659989c3d0caf19baf6405e41ab7df")

HELPER_ANCHOR = """static DWORD WINTRUST_CreateChainForSigner(CRYPT_PROVIDER_DATA *data,
 DWORD signer, PWTD_GENERIC_CHAIN_POLICY_CREATE_INFO createInfo,
 PCERT_CHAIN_PARA chainPara)
{
"""
HELPER = """/* """ + MARKER + """ (tools/patch-wine-wintrust-root-pin.py, round 707 M3 S18): env.MADEIRA_CODESIGN_ROOTS = 1 (a game's own line, default
 * off) trusts the code-signing root Windows trusts and BCD's web root list lacks, VeriSign Class 3 Public Primary Certification Authority - G5,
 * pinned by its SHA-256, for this signer's chain only: the root store is not touched, so TLS never sees it. */
static const BYTE madeira_codesign_pin[32] = { @PIN@ };

static void madeira_codesign_root_pin(PCCERT_CHAIN_CONTEXT chain)
{
    static int on = -1;
    static LONG told;
    PCERT_SIMPLE_CHAIN simple;
    PCERT_CHAIN_ELEMENT root;
    BYTE hash[32];
    DWORD size = sizeof(hash);

    if (on < 0)
    {
        WCHAR value[4];
        DWORD n = GetEnvironmentVariableW(L"MADEIRA_CODESIGN_ROOTS", value, ARRAY_SIZE(value));
        on = n == 1 && value[0] == '1';
    }
    if (!on || !chain || chain->cChain < 1 || !chain->rgpChain[0]->cElement) return;
    simple = chain->rgpChain[0];
    root = simple->rgpElement[simple->cElement - 1];
    if (root->TrustStatus.dwErrorStatus != CERT_TRUST_IS_UNTRUSTED_ROOT || !(root->TrustStatus.dwInfoStatus & CERT_TRUST_IS_SELF_SIGNED)
        || !root->pCertContext)
        return;   /* a trusted root, or a root with another fault: as it was */
    if (!CryptHashCertificate2(L"SHA256", 0, NULL, root->pCertContext->pbCertEncoded, root->pCertContext->cbCertEncoded, hash, &size)
        || size != sizeof(hash) || memcmp(hash, madeira_codesign_pin, sizeof(hash)))
        return;
    root->TrustStatus.dwErrorStatus &= ~CERT_TRUST_IS_UNTRUSTED_ROOT;
    simple->TrustStatus.dwErrorStatus &= ~CERT_TRUST_IS_UNTRUSTED_ROOT;
    ((CERT_CHAIN_CONTEXT *)chain)->TrustStatus.dwErrorStatus &= ~CERT_TRUST_IS_UNTRUSTED_ROOT;
    if (InterlockedIncrement(&told) <= 3)
        ERR("[wintrust] """ + MARKER + """: the signature's root VeriSign Class 3 Public Primary Certification Authority - G5 is trusted "
            "for this code signature (env.MADEIRA_CODESIGN_ROOTS=1)\\n");
}

"""
CALL_ANCHOR = """            if (ret)
            {
                if (data->pasSigners[signer].pChainContext->cChain != 1)
"""
CALL = """            if (ret)
            {
                madeira_codesign_root_pin(data->pasSigners[signer].pChainContext);   /* """ + MARKER + """ */
                if (data->pasSigners[signer].pChainContext->cChain != 1)
"""


def main():
    wine = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = wine / SOFTPUB
    s = path.read_text()
    if MARKER in s:
        print("wintrust code-signing root pin: already applied")
        return
    for name, old in (("WINTRUST_CreateChainForSigner", HELPER_ANCHOR), ("the signer chain's creation", CALL_ANCHOR)):
        if s.count(old) != 1:
            sys.exit("patch-wine-wintrust-root-pin: %s: anchor found %d times (want 1) in %s" % (name, s.count(old), path))
    pin = ", ".join("0x%02x" % b for b in PIN)
    s = s.replace(HELPER_ANCHOR, HELPER.replace("@PIN@", pin) + HELPER_ANCHOR).replace(CALL_ANCHOR, CALL)
    path.write_text(s)
    print("wintrust code-signing root pin: applied")


if __name__ == "__main__":
    main()
