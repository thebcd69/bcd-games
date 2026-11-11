#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's d3dcompiler (D3DCompile2) names a failed run-time HLSL compile in the log (night round 2026-11-11, G2 S1 (d)).

BioShock (Steam 7670, 32-bit; Send Logs 01NGQE, FDVGYM) compiles shaders at run time on its Renderer thread and reads a NULL object 0.1 s after
the first two compiles; the compiler's verdict never reaches the log: vkd3d hands the message text to the caller and frees it, and the only
line the log has is `vkd3d:...:fixme:D3DCompile2VKD3D Ignoring flags 0x1000.` (round 630 designed this diagnostic as S11; it was never written).

D3DCompile2 (shared by d3dcompiler_33 .. _47, built from dlls/d3dcompiler_43/compiler.c) now passes a local message blob to vkd3d when the
caller gave none (vkd3d formats the text either way and only wraps it in a blob for a caller that asked) and, after the compile, writes
`err:d3dcompiler:D3DCompile2 d3dcompile: v<version> FAILED target=<profile> entry=<name> flags=.. eflags=.. src=<n> bytes hr=<hr> msgs=<n>
bytes #<k>`, the first 160 bytes of the source and the first 1600 bytes of the compiler's text (320 per line) for a compile that FAILED
(at most 24 per process); a compile that succeeded with a message text (warnings) gets the first line only, at most 3 per process. A compile
without text and without failure prints nothing. The local blob is released; the caller's result, blobs and return value are untouched.
The i386 farm builds it (the 64-bit d3dcompiler_43.dll is a committed binary). Idempotent; every anchor is checked before anything is
written; fails by name when the function moved.
Usage: patch-wine-d3dcompile-diag.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: d3dcompile diag"
FILE = "dlls/d3dcompiler_43/compiler.c"

HELPER_ANCHOR = "HRESULT WINAPI D3DCompile2(const void *data, SIZE_T data_size, const char *filename,\n"
HELPER = (
    "/* " + MARKER + " (tools/patch-wine-d3dcompile-diag.py, night round 2026-11-11, BioShock 01NGQE): the compiler's verdict of a run-time\n"
    " * HLSL compile never reached the log. A failed compile gets a header line, the first bytes of its source and the compiler's text (24 a\n"
    " * process at most); one that succeeded with a text (warnings) gets the header line only (3 a process at most). Log only. */\n"
    "static void madeira_compile_diag(HRESULT hr, ID3DBlob *messages, const void *data, SIZE_T data_size, const char *entry_point,\n"
    "        const char *profile, UINT flags, UINT effect_flags)\n"
    "{\n"
    "    static LONG failed_said, warned_said;\n"
    "    SIZE_T size = messages ? ID3D10Blob_GetBufferSize(messages) : 0, off;\n"
    "    const char *text = size ? ID3D10Blob_GetBufferPointer(messages) : NULL;\n"
    "    LONG n;\n"
    "\n"
    "    if (FAILED(hr))\n"
    "    {\n"
    "        if ((n = InterlockedIncrement(&failed_said)) > 24) return;\n"
    "    }\n"
    "    else if (size)\n"
    "    {\n"
    "        if ((n = InterlockedIncrement(&warned_said)) > 3) return;\n"
    "    }\n"
    "    else return;\n"
    "\n"
    "    ERR(\"d3dcompile: v%d %s target=%s entry=%s flags=%#x eflags=%#x src=%Iu bytes hr=%#lx msgs=%Iu bytes #%ld\\n\", D3D_COMPILER_VERSION,\n"
    "            FAILED(hr) ? \"FAILED\" : \"warnings\", debugstr_a(profile), debugstr_a(entry_point), flags, effect_flags, data_size, hr, size, n);\n"
    "    if (FAILED(hr) && data)\n"
    "        ERR(\"d3dcompile:   src=%s\\n\", debugstr_an(data, data_size < 160 ? (int)data_size : 160));\n"
    "    if (FAILED(hr) && text)\n"
    "    {\n"
    "        for (off = 0; off < size && off < 1600; off += 320)\n"
    "            ERR(\"d3dcompile:   %s\\n\", debugstr_an(text + off, size - off < 320 ? (int)(size - off) : 320));\n"
    "    }\n"
    "}\n"
    "\n"
)

DECL_OLD = ("    struct d3dcompiler_include_from_file include_from_file;\n"
            "    ID3DBlob *dummy_blob;\n"
            "    HRESULT hr;\n"
            "\n"
            "    TRACE(\"data %p, data_size %Iu, filename %s, macros %p, include %p, entry_point %s, \"\n"
            "            \"profile %s, flags %#x, effect_flags %#x, secondary_flags %#x, secondary_data %p, \"\n")
DECL_NEW = DECL_OLD.replace("    ID3DBlob *dummy_blob;\n", "    ID3DBlob *dummy_blob, *madeira_msgs = NULL, **madeira_out; /* " + MARKER + " */\n")

CALL_OLD = ("    hr = vkd3d_D3DCompile2VKD3D(data, data_size, filename, macros, include, entry_point, profile, flags, effect_flags,\n"
            "            secondary_flags, secondary_data, secondary_data_size, shader_blob, messages_blob, D3D_COMPILER_VERSION);\n"
            "    if (SUCCEEDED(hr) && shader_blob == &dummy_blob)\n"
            "        ID3D10Blob_Release(dummy_blob);\n"
            "    return hr;\n")
CALL_NEW = ("    /* " + MARKER + ": vkd3d formats its text for every caller; a caller that gave no message blob gets a local one here. */\n"
            "    madeira_out = messages_blob ? messages_blob : &madeira_msgs;\n"
            "    hr = vkd3d_D3DCompile2VKD3D(data, data_size, filename, macros, include, entry_point, profile, flags, effect_flags,\n"
            "            secondary_flags, secondary_data, secondary_data_size, shader_blob, madeira_out, D3D_COMPILER_VERSION);\n"
            "    madeira_compile_diag(hr, *madeira_out, data, data_size, entry_point, profile, flags, effect_flags);\n"
            "    if (madeira_msgs)\n"
            "        ID3D10Blob_Release(madeira_msgs);\n"
            "    if (SUCCEEDED(hr) && shader_blob == &dummy_blob)\n"
            "        ID3D10Blob_Release(dummy_blob);\n"
            "    return hr;\n")


def patch(src):
    if MARKER in src:
        if src.count(HELPER) != 1 or src.count(DECL_NEW) != 1 or src.count(CALL_NEW) != 1 or CALL_OLD in src:
            raise ValueError("partial or changed d3dcompile diagnostic patch")
        return src
    if src.count(HELPER_ANCHOR) != 1:
        raise ValueError("D3DCompile2 of d3dcompiler_43/compiler.c not found once (Wine changed)")
    if src.count(DECL_OLD) != 1:
        raise ValueError("the local declarations of D3DCompile2 changed (Wine changed)")
    if src.count(CALL_OLD) != 1:
        raise ValueError("the vkd3d_D3DCompile2VKD3D call of D3DCompile2 changed (Wine changed)")
    if not (src.index(HELPER_ANCHOR) < src.index(DECL_OLD) < src.index(CALL_OLD)):
        raise ValueError("D3DCompile2 is no longer in the expected order (Wine changed)")
    src = src.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1)
    src = src.replace(DECL_OLD, DECL_NEW, 1)
    return src.replace(CALL_OLD, CALL_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-d3dcompile-diag.py WINE-DIR")
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-d3dcompile-diag: " + str(error))
    if result == source:
        print("patch-wine-d3dcompile-diag: already in")
    else:
        path.write_text(result)
        print("patch-wine-d3dcompile-diag: %s patched" % FILE)


if __name__ == "__main__":
    main()
