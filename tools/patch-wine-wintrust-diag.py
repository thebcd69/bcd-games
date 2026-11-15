#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[wintrust]: say what WinVerifyTrust refused and which element of the certificate chain it stopped at (round 660, M072; log only).

METAL GEAR SOLID V (Steam; HP80QX, TJBXC6) prints "Failed to load SteamAPI" right after a signature check, and nothing in the log says what the check
looked at. The program is 64-bit. BCD's root store is Mozilla's web list; Microsoft's code-signing roots may be missing from it, but the logs cannot
show which certificate the check rejects or which file it checks. This adds three log lines, each capped at 8 per process, on failure paths only:

  wintrust_main.c WinVerifyTrust, when the result is not zero:
      [wintrust] madeira-bcd: WinVerifyTrust(ACTION-GUID) = 800b0109 (file L"C:\\...\\steam_api64.dll")
    the file only for a WINTRUST_DATA with a file (the old-style published-software and certificate callers do not pass one);
  softpub.c WINTRUST_CopyChain, when the last element of a signer's chain has an error status (CERT_E_UNTRUSTEDROOT, CERT_E_CHAINING, ...):
      [wintrust] madeira-bcd: chain error 800b0109: element 0 of 3: "Valve Corporation", error status 00000000, info status 00000000
      [wintrust] madeira-bcd: chain error 800b0109: element 2 of 3 (root): "VeriSign Class 3 Public Primary Certification Authority - G5", error status 00000020, ...
    at most 8 elements of a chain, at most 3 chains per process;
  softpub.c, where an image's digest differs from the catalog's: TRUST_E_BAD_DIGEST with the two digest sizes.

Nothing else changes: no result, no store, no certificate is touched. Keyless (a diagnostic on failure paths). The second step of the row
(a second certificate root store behind env.MADEIRA_ROOT_STORE = windows) waits until a log of this diagnostic names a root.

WHERE THIS REACHES: the 32-bit wintrust through the i386 farm, and the 64-bit wintrust.dll through the rebuilt set (tools/build-wine-fresh-dlls.sh,
env.MADEIRA_WIN64_FRESH = 1); the committed 64-bit wintrust.dll is a frozen binary. Idempotent; fails by name when an anchor moved.
Usage: patch-wine-wintrust-diag.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: [wintrust]"
MAIN = "dlls/wintrust/wintrust_main.c"
SOFTPUB = "dlls/wintrust/softpub.c"

MAIN_DECL_OLD = """    LONG err = ERROR_SUCCESS;
    WINTRUST_DATA *actionData = ActionData;

    TRACE("(%p, %s, %p)\\n", hwnd, debugstr_guid(ActionID), ActionData);
"""
MAIN_DECL_NEW = """    LONG err = ERROR_SUCCESS;
    WINTRUST_DATA *actionData = ActionData;
    const WCHAR *madeira_file = NULL;   /* madeira-bcd: [wintrust] (tools/patch-wine-wintrust-diag.py) */

    TRACE("(%p, %s, %p)\\n", hwnd, debugstr_guid(ActionID), ActionData);
"""
MAIN_STATE_OLD = """        switch (stateAction)
        {
        case WTD_STATEACTION_IGNORE:
            err = WINTRUST_DefaultVerifyAndClose(hwnd, ActionID, ActionData);
"""
MAIN_STATE_NEW = """        if (WVT_ISINSTRUCT(WINTRUST_DATA, actionData->cbStruct, pFile) && actionData->dwUnionChoice == WTD_CHOICE_FILE &&
            actionData->pFile && WVT_ISINSTRUCT(WINTRUST_FILE_INFO, actionData->pFile->cbStruct, pcwszFilePath))
            madeira_file = actionData->pFile->pcwszFilePath;   /* madeira-bcd: [wintrust] */
        switch (stateAction)
        {
        case WTD_STATEACTION_IGNORE:
            err = WINTRUST_DefaultVerifyAndClose(hwnd, ActionID, ActionData);
"""
MAIN_RET_OLD = """    if (err) SetLastError(err);
    TRACE("returning %08lx\\n", err);
    return err;
}

/***********************************************************************
 *		WinVerifyTrustEx (WINTRUST.@)
"""
MAIN_RET_NEW = """    if (err)
    {
        static LONG told;   /* madeira-bcd: [wintrust] */
        if (InterlockedIncrement(&told) <= 8)
            ERR("[wintrust] madeira-bcd: WinVerifyTrust(%s) = %08lx (file %s)\\n", debugstr_guid(ActionID), err,
                madeira_file ? debugstr_w(madeira_file) : "none");
    }
    if (err) SetLastError(err);
    TRACE("returning %08lx\\n", err);
    return err;
}

/***********************************************************************
 *		WinVerifyTrustEx (WINTRUST.@)
"""

CHAIN_OLD = """static DWORD WINTRUST_CopyChain(CRYPT_PROVIDER_DATA *data, DWORD signerIdx)
{
"""
CHAIN_HELPER = """/* madeira-bcd: [wintrust] (tools/patch-wine-wintrust-diag.py, round 660 M072): the elements of a chain whose last element has an error, at most 8 of
 * a chain and 3 chains a process: the subject of each and its trust status. Log only. */
static void madeira_wintrust_chain(const CERT_SIMPLE_CHAIN *chain, DWORD error)
{
    static LONG told;
    DWORD i;

    if (!error || InterlockedIncrement(&told) > 3) return;
    for (i = 0; i < chain->cElement && i < 8; i++)
    {
        const CERT_CHAIN_ELEMENT *element = chain->rgpElement[i];
        char name[96] = "?";

        if (element->pCertContext)
            CertGetNameStringA(element->pCertContext, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, NULL, name, sizeof(name));
        ERR("[wintrust] madeira-bcd: chain error %08lx: element %lu of %lu%s: %s, error status %08lx, info status %08lx\\n", error,
            i, chain->cElement, i == chain->cElement - 1 ? " (root)" : "", debugstr_a(name), element->TrustStatus.dwErrorStatus,
            element->TrustStatus.dwInfoStatus);
    }
}

"""
CHAIN_RET_OLD = """    data->pasSigners[signerIdx].pasCertChain[simpleChain->cElement - 1].dwError
     = WINTRUST_TrustStatusToError(
     simpleChain->rgpElement[simpleChain->cElement - 1]->
     TrustStatus.dwErrorStatus);
    return err;
}
"""
CHAIN_RET_NEW = """    data->pasSigners[signerIdx].pasCertChain[simpleChain->cElement - 1].dwError
     = WINTRUST_TrustStatusToError(
     simpleChain->rgpElement[simpleChain->cElement - 1]->
     TrustStatus.dwErrorStatus);
    madeira_wintrust_chain(simpleChain, data->pasSigners[signerIdx].pasCertChain[simpleChain->cElement - 1].dwError);
    return err;
}
"""
DIGEST_OLD = """    err = (hash_size == indirect->Digest.cbData &&
           !memcmp(hash_data, indirect->Digest.pbData, hash_size)) ? S_OK : TRUST_E_BAD_DIGEST;
"""
DIGEST_NEW = DIGEST_OLD + """    if (err)
    {
        static LONG told;   /* madeira-bcd: [wintrust] */
        if (InterlockedIncrement(&told) <= 8)
            ERR("[wintrust] madeira-bcd: image digest differs from the catalog's (%08lx): computed %lu bytes, catalog %lu bytes\\n", err,
                (DWORD)hash_size, (DWORD)indirect->Digest.cbData);
    }
"""

EDITS = {
    MAIN: [(MAIN_DECL_OLD, MAIN_DECL_NEW), (MAIN_STATE_OLD, MAIN_STATE_NEW), (MAIN_RET_OLD, MAIN_RET_NEW)],
    SOFTPUB: [(CHAIN_OLD, CHAIN_HELPER + CHAIN_OLD), (CHAIN_RET_OLD, CHAIN_RET_NEW), (DIGEST_OLD, DIGEST_NEW)],
}


def main():
    wine = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    texts = {f: (wine / f).read_text() for f in EDITS}
    have = [MARKER in t for t in texts.values()]
    if all(have):
        print("wintrust diagnostics: already applied")
        return
    if any(have):
        sys.exit("patch-wine-wintrust-diag: only one of wintrust_main.c and softpub.c carries the patch")
    for f, edits in EDITS.items():
        for old, _ in edits:
            if texts[f].count(old) != 1:
                sys.exit("patch-wine-wintrust-diag: an anchor was not found exactly once in %s: %r" % (wine / f, old.strip().splitlines()[0]))
    for f, edits in EDITS.items():
        t = texts[f]
        for old, new in edits:
            t = t.replace(old, new)
        (wine / f).write_text(t)
    print("wintrust diagnostics: applied")


if __name__ == "__main__":
    main()
