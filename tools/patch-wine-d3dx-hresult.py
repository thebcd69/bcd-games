#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's D3DX10/D3DX11 run-time compile functions write the caller's *pHResult (night round 2026-11-11, G2 S1).

D3DX10CompileFromMemory, D3DX11CompileFromMemory and D3DX10CreateEffectFromMemory / FromFile / FromResource take a `HRESULT *pHResult`
that Microsoft documents as "the return value" ("may be NULL"; with a thread pump it must stay valid until the asynchronous work ends). Wine
returns the result but never wrote it, although the same tree's texture loaders, D3DX10PreprocessShaderFromMemory and D3DX11CompileFromFileW
all do (`hr = ...; if (hresult) *hresult = hr;`). BioShock (Steam 7670, 32-bit; Send Logs 01NGQE, FDVGYM, 57C6EQ, S20EB1) compiles shaders
with them at run time on its Renderer thread; 0.1 s after the first two compiles it reads a NULL object (EIP Bioshock.exe+0x528a20,
`mov eax,[ecx+8]` with ECX = 0) and the game ends. The game touches its `ShaderErrors` folder after each compile; a caller that passes `&hr`
and reads `hr` instead of the return value sees whatever its variable held before, which can send it down its compile-failed path although
the compile worked. Whether that is BioShock's cause is not proved (round 2026-11-11, G2); it is the documented contract either way.

The functions now write the result exactly where the tree's texture loaders do: after the compile or the effect creation, when the pointer
is not NULL. A call that fails an argument check (or a resource load) still returns without writing, as the texture loaders do. A caller that
passes NULL, or never reads the variable, sees no change; a caller that reads it gets what Windows gives it.
Idempotent; every anchor is checked before anything is written; fails by name when a function moved.
Usage: patch-wine-d3dx-hresult.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: d3dx hresult"
NOTE = "/* " + MARKER + " (patch-wine-d3dx-hresult.py) */\n"

# (file, [(old, new), ...]); each old text occurs exactly once in the file
EDITS = [
    ("dlls/d3dx10_43/async.c", [
        ("        ID3D10Blob **error_messages, HRESULT *hresult)\n{\n"
         "    TRACE(\"data %s, data_size %Iu, filename %s, defines %p, include %p, entry_point %s, target %s, \"\n",
         "        ID3D10Blob **error_messages, HRESULT *hresult)\n{\n    HRESULT hr;\n\n"
         "    TRACE(\"data %s, data_size %Iu, filename %s, defines %p, include %p, entry_point %s, target %s, \"\n"),
        ("    return D3DCompile(data, data_size, filename, defines, include, entry_point, target,\n"
         "            sflags, eflags, shader, error_messages);\n}\n",
         "    hr = D3DCompile(data, data_size, filename, defines, include, entry_point, target,\n"
         "            sflags, eflags, shader, error_messages);\n"
         "    " + NOTE +
         "    if (hresult)\n        *hresult = hr;\n    return hr;\n}\n"),
    ]),
    ("dlls/d3dx11_43/async.c", [
        ("        ID3D10Blob **error_messages, HRESULT *hresult)\n{\n"
         "    TRACE(\"data %s, data_size %Iu, filename %s, defines %p, include %p, entry_point %s, target %s, \"\n",
         "        ID3D10Blob **error_messages, HRESULT *hresult)\n{\n    HRESULT hr;\n\n"
         "    TRACE(\"data %s, data_size %Iu, filename %s, defines %p, include %p, entry_point %s, target %s, \"\n"),
        ("    return D3DCompile(data, data_size, filename, defines, include, entry_point, target,\n"
         "            sflags, eflags, shader, error_messages);\n}\n",
         "    hr = D3DCompile(data, data_size, filename, defines, include, entry_point, target,\n"
         "            sflags, eflags, shader, error_messages);\n"
         "    " + NOTE +
         "    if (hresult)\n        *hresult = hr;\n    return hr;\n}\n"),
    ]),
    ("dlls/d3dx10_43/compiler.c", [
        # D3DX10CreateEffectFromMemory
        ("        ID3DX10ThreadPump *pump, ID3D10Effect **effect, ID3D10Blob **errors, HRESULT *hresult)\n{\n"
         "    TRACE(\"data %p, datasize %Iu, filename %s, defines %p, include %p, profile %s, shader_flags %#x,\"\n",
         "        ID3DX10ThreadPump *pump, ID3D10Effect **effect, ID3D10Blob **errors, HRESULT *hresult)\n{\n    HRESULT hr;\n\n"
         "    TRACE(\"data %p, datasize %Iu, filename %s, defines %p, include %p, profile %s, shader_flags %#x,\"\n"),
        ("    return create_effect(data, datasize, filename, defines, include, profile,\n"
         "            shader_flags, effect_flags, device, effect_pool, effect, errors);\n}\n",
         "    hr = create_effect(data, datasize, filename, defines, include, profile,\n"
         "            shader_flags, effect_flags, device, effect_pool, effect, errors);\n"
         "    " + NOTE +
         "    if (hresult)\n        *hresult = hr;\n    return hr;\n}\n"),
        # D3DX10CreateEffectFromFileW (FromFileA passes hresult on)
        ("        hr = create_effect(buffer, size, filename_a, defines, include, profile,\n"
         "                shader_flags, effect_flags, device, effect_pool, effect, errors);\n"
         "        free(buffer);\n",
         "        hr = create_effect(buffer, size, filename_a, defines, include, profile,\n"
         "                shader_flags, effect_flags, device, effect_pool, effect, errors);\n"
         "        free(buffer);\n"
         "        " + NOTE +
         "        if (hresult)\n            *hresult = hr;\n"),
        # D3DX10CreateEffectFromResourceA
        ("    return create_effect(data, size, filename, defines, include, profile,\n"
         "            shader_flags, effect_flags, device, effect_pool, effect, errors);\n}\n",
         "    hr = create_effect(data, size, filename, defines, include, profile,\n"
         "            shader_flags, effect_flags, device, effect_pool, effect, errors);\n"
         "    " + NOTE +
         "    if (hresult)\n        *hresult = hr;\n    return hr;\n}\n"),
        # D3DX10CreateEffectFromResourceW
        ("    hr = create_effect(data, size, filename, defines, include, profile,\n"
         "            shader_flags, effect_flags, device, effect_pool, effect, errors);\n"
         "    free(filename);\n    return hr;\n",
         "    hr = create_effect(data, size, filename, defines, include, profile,\n"
         "            shader_flags, effect_flags, device, effect_pool, effect, errors);\n"
         "    free(filename);\n"
         "    " + NOTE +
         "    if (hresult)\n        *hresult = hr;\n    return hr;\n"),
    ]),
]


def patch_file(rel, edits, src):
    if MARKER in src:
        done = sum(1 for _, new in edits if new in src)
        if done != len(edits) or src.count(MARKER) != sum(new.count(MARKER) for _, new in edits):
            raise ValueError("%s: partial or changed d3dx hresult patch" % rel)
        return src
    for old, new in edits:
        if src.count(old) != 1:
            raise ValueError("%s: an anchor of the d3dx hresult patch was not found once (Wine changed): %r" % (rel, old.splitlines()[0][:90]))
    for old, new in edits:
        src = src.replace(old, new, 1)
    return src


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-d3dx-hresult.py WINE-DIR")
    wine = Path(sys.argv[1])
    changed = []
    try:
        outputs = []
        for rel, edits in EDITS:
            src = (wine / rel).read_text()
            out = patch_file(rel, edits, src)
            outputs.append((rel, src, out))
    except ValueError as error:
        sys.exit("patch-wine-d3dx-hresult: " + str(error))
    for rel, src, out in outputs:
        if out != src:
            (wine / rel).write_text(out)
            changed.append(rel)
    if changed:
        print("patch-wine-d3dx-hresult: patched " + ", ".join(changed))
    else:
        print("patch-wine-d3dx-hresult: already in")


if __name__ == "__main__":
    main()
