#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""Wine's DxDiag provider (dlls/dxdiagn) initialises without DirectShow's device enumerator.

The prefix registers CLSID_DxDiagProvider at C:\\windows\\system32\\dxdiagn.dll, and BCD ships Wine's dxdiagn
for 64-bit programs as an opt-in DLL (arm64ec-windows/dxdiagn-optin.dll, env.MADEIRA_OPTIN_DLLS =
dxdiagn,ddraw). Far Cry 4's hardware detection (Ubisoft's systemdetection64.dll) creates the provider and
calls IDxDiagProvider::Initialize; with Wine's file as it is, Initialize failed:

    err:module:load_dll [dll-missing] ... L"C:\\windows\\system32\\devenum.dll" status=c0000135
    err:ole:com_get_class_object no class object {62be5d10-60eb-11d0-bd3b-00a0c911ce86} could be created
    OutputDebugStringA: "[Gear::SystemDetection::Hardware] ERROR: Can't init the IDxDiagProvider.."

and the game then read a null pointer, as it did with no provider at all. build_information_tree fills ten
sections in turn and gives up on the first that fails; the DirectShow filters section starts with
CoCreateInstance(CLSID_SystemDeviceEnum), which needs devenum.dll, which the 64-bit set does not ship.
Every other section copes with a missing component (display devices fall back when Direct3D 9 cannot
start, sound devices are listed only when there are some); this one ended the whole tree.

The patch makes the DirectShow section behave like the others: when the device enumerator cannot be
created, or the filter enumeration fails, the section stays empty (what it found so far is kept),
Initialize goes on to the next section, and one ERR line says so ("[dxdiagn] madeira-bcd: ..."), because
the app runs with WINEDEBUG=err+all. Nothing else in the file changes; the file is built only into
dxdiagn.dll, and BCD ships that only as an opt-in DLL.

Wine's dlls/dxdiagn is LGPL 2.1 or later; the patched file carries a "Modified by BCD" notice, and this
script is the source of the change.

Usage:
  patch-wine-dxdiagn-directshow.py WINE_SRC        patch the tree (idempotent)
  patch-wine-dxdiagn-directshow.py --verify DLL    check a built DLL: ARM64EC/AMD64 PE, carries the marker
tools/build-wine-extra-dlls.sh applies it around the build and restores the tree afterwards.
"""
import os
import struct
import sys

MARKER = "dxdiagn-directshow-v1 madeira-bcd"
FILE = "dlls/dxdiagn/provider.c"

NOTICE_ANCHOR = '#include "dxdiag_private.h"\n'
NOTICE = '''/*
 * Modified by BCD, 2026-11-05 (tools/patch-wine-dxdiagn-directshow.py): the DirectShow filters section stays
 * empty when DirectShow's device enumerator is missing, instead of failing IDxDiagProvider::Initialize.
 * Everything else in this file is as Wine's; this file stays under the license above.
 */

'''

EDITS = (
    # no device enumerator (devenum.dll): an empty section, not a failed Initialize
    ('''    hr = CoCreateInstance(&CLSID_SystemDeviceEnum, NULL, CLSCTX_INPROC_SERVER,
                          &IID_ICreateDevEnum, (void **)&pCreateDevEnum);
    if (FAILED(hr))
        return hr;
''', '''    hr = CoCreateInstance(&CLSID_SystemDeviceEnum, NULL, CLSCTX_INPROC_SERVER,
                          &IID_ICreateDevEnum, (void **)&pCreateDevEnum);
    if (FAILED(hr))
    {
        ERR("[dxdiagn] madeira-bcd: no DirectShow device enumerator (%#lx); the DirectShow filters section stays empty (%s)\\n",
            hr, madeira_dxdiagn_marker);
        return S_OK;
    }
'''),
    # a filter enumeration that fails keeps what it found
    ('''cleanup:
    if (pEmCat) IEnumMoniker_Release(pEmCat);
    ICreateDevEnum_Release(pCreateDevEnum);
    return hr;
}
''', '''cleanup:
    if (pEmCat) IEnumMoniker_Release(pEmCat);
    ICreateDevEnum_Release(pCreateDevEnum);
    if (FAILED(hr))
    {
        ERR("[dxdiagn] madeira-bcd: DirectShow filter enumeration failed (%#lx); the section keeps what it found\\n", hr);
        return S_OK;
    }
    return hr;
}
'''),
    # the marker, before the function that names it
    ('''static HRESULT build_directshowfilters_tree(IDxDiagContainerImpl_Container *node)
{''', '''static const char madeira_dxdiagn_marker[] __attribute__((used)) = "''' + MARKER + '''";

static HRESULT build_directshowfilters_tree(IDxDiagContainerImpl_Container *node)
{'''),
)


def patch(wine):
    path = os.path.join(wine, FILE)
    if not os.path.isfile(path):
        sys.exit("patch-wine-dxdiagn-directshow: no " + path)
    text = open(path).read()
    if MARKER in text:
        print("already patched")
        return
    if text.count(NOTICE_ANCHOR) != 1:
        sys.exit("patch-wine-dxdiagn-directshow: notice anchor found %d times in %s" % (text.count(NOTICE_ANCHOR), FILE))
    for old, new in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-dxdiagn-directshow: anchor found %d times in %s:\n%s" % (text.count(old), FILE, old[:400]))
        text = text.replace(old, new)
    text = text.replace(NOTICE_ANCHOR, NOTICE + NOTICE_ANCHOR, 1)
    open(path, "w").write(text)
    print("patched %s (%d edits)" % (path, len(EDITS)))


def verify(path):
    d = open(path, "rb").read()
    if d[:2] != b"MZ":
        sys.exit("verify: %s is not a PE image" % path)
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    machine = struct.unpack_from("<H", d, pe + 4)[0]
    bad = []
    if MARKER.encode() not in d:
        bad.append("no '%s' string: not built from the patched tree" % MARKER)
    if machine not in (0xaa64, 0x8664):
        bad.append("machine %#x is neither ARM64 nor AMD64 (ARM64EC images carry 0x8664 or 0xaa64)" % machine)
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: DirectShow patch present" % path)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify(sys.argv[2])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)
