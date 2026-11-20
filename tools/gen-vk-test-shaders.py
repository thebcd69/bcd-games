#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Makes tests/dxtests/vk_shaders.h, the SPIR-V of BCD's Vulkan test (tests/dxtests/vulkan_test.c), from tests/dxtests/vk_cube.spvasm.

The assembly holds two modules, each after a line ";; ==== <name>". Each is assembled with SPIRV-Tools' C library for Vulkan 1.0 (SPIR-V
1.0, what MoltenVK and KosmicKrisp take), validated for the same environment, and written as a `static const uint32_t <name>[]` array. The
header records the SHA-256 of the assembly it was made from, so tests/host/check-vk-gl-tests.py can tell when it is stale. Nothing here
needs glslang: the library is the one the KosmicKrisp host build makes (toolchains/kosmickrisp-host/llvm-22/lib), or SPIRV_TOOLS_LIB.

  python3 tools/gen-vk-test-shaders.py            writes the header
  python3 tools/gen-vk-test-shaders.py --print    prints it instead (the host check compares it with the file)
"""
import ctypes
import hashlib
import os
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
SRC = R / "tests/dxtests/vk_cube.spvasm"
OUT = R / "tests/dxtests/vk_shaders.h"
SPV_ENV_VULKAN_1_0 = 1


def find_library():
    cands = [os.environ.get("SPIRV_TOOLS_LIB", "")]
    for root in (R, Path.home() / "madeira-bcd"):
        cands.append(str(root / "toolchains/kosmickrisp-host/llvm-22/lib/libSPIRV-Tools-shared.dylib"))
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


class Binary(ctypes.Structure):
    _fields_ = [("code", ctypes.POINTER(ctypes.c_uint32)), ("wordCount", ctypes.c_size_t)]


class Position(ctypes.Structure):
    _fields_ = [("line", ctypes.c_size_t), ("column", ctypes.c_size_t), ("index", ctypes.c_size_t)]


class Diagnostic(ctypes.Structure):
    _fields_ = [("position", Position), ("error", ctypes.c_char_p), ("isTextSource", ctypes.c_bool)]


def modules(text):
    """The modules of the assembly: [(name, text)], in order."""
    parts = re.split(r"^;; ==== (\w+)\n", text, flags=re.M)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]


def assemble(lib_path, text):
    """[(name, words)] for the modules of `text`; raises with SPIRV-Tools' message when one does not assemble or validate."""
    lib = ctypes.CDLL(lib_path)
    lib.spvContextCreate.restype = ctypes.c_void_p
    lib.spvContextCreate.argtypes = [ctypes.c_int]
    lib.spvTextToBinary.restype = ctypes.c_int
    lib.spvTextToBinary.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.POINTER(Binary)),
                                    ctypes.POINTER(ctypes.POINTER(Diagnostic))]
    lib.spvValidate.restype = ctypes.c_int
    lib.spvValidate.argtypes = [ctypes.c_void_p, ctypes.POINTER(Binary), ctypes.POINTER(ctypes.POINTER(Diagnostic))]
    lib.spvBinaryDestroy.argtypes = [ctypes.POINTER(Binary)]
    lib.spvDiagnosticDestroy.argtypes = [ctypes.POINTER(Diagnostic)]
    lib.spvContextDestroy.argtypes = [ctypes.c_void_p]
    ctx = lib.spvContextCreate(SPV_ENV_VULKAN_1_0)
    out = []
    try:
        for name, body in modules(text):
            raw = body.encode()
            binary = ctypes.POINTER(Binary)()
            diag = ctypes.POINTER(Diagnostic)()
            rc = lib.spvTextToBinary(ctx, raw, len(raw), ctypes.byref(binary), ctypes.byref(diag))
            if rc != 0:
                msg = diag.contents.error.decode() if diag else "error %d" % rc
                line = diag.contents.position.line + 1 if diag else 0
                if diag:
                    lib.spvDiagnosticDestroy(diag)
                raise RuntimeError("%s does not assemble (line %d of its part): %s" % (name, line, msg))
            diag = ctypes.POINTER(Diagnostic)()
            rc = lib.spvValidate(ctx, binary, ctypes.byref(diag))
            if rc != 0:
                msg = diag.contents.error.decode() if diag else "error %d" % rc
                if diag:
                    lib.spvDiagnosticDestroy(diag)
                lib.spvBinaryDestroy(binary)
                raise RuntimeError("%s does not validate for Vulkan 1.0: %s" % (name, msg))
            words = [binary.contents.code[i] for i in range(binary.contents.wordCount)]
            lib.spvBinaryDestroy(binary)
            out.append((name, words))
    finally:
        lib.spvContextDestroy(ctx)
    return out


def header(text, mods):
    lines = [
        "// SPDX-License-Identifier: GPL-3.0-only",
        "// Copyright 2026 thebcd69 (BCD)",
        "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.",
        "//",
        "// vk_shaders.h: the SPIR-V of BCD's Vulkan test (vulkan_test.c). Made by tools/gen-vk-test-shaders.py from vk_cube.spvasm",
        "// (SPIRV-Tools, Vulkan 1.0, validated); do not edit it by hand.",
        "// vk_cube.spvasm sha256 %s" % hashlib.sha256(text.encode()).hexdigest(),
        "",
        "#ifndef BCD_VK_SHADERS_H",
        "#define BCD_VK_SHADERS_H",
        "",
        "#include <stdint.h>",
    ]
    for name, words in mods:
        lines.append("")
        lines.append("static const uint32_t %s[%d] = {" % (name, len(words)))
        for i in range(0, len(words), 8):
            lines.append("    " + ", ".join("0x%08x" % w for w in words[i:i + 8]) + ",")
        lines.append("};")
    lines += ["", "#endif /* BCD_VK_SHADERS_H */", ""]
    return "\n".join(lines)


def main():
    lib = find_library()
    if not lib:
        sys.exit("gen-vk-test-shaders: no libSPIRV-Tools-shared.dylib (toolchains/kosmickrisp-host/llvm-22/lib, or SPIRV_TOOLS_LIB)")
    text = SRC.read_text()
    try:
        mods = assemble(lib, text)
    except RuntimeError as e:
        sys.exit("gen-vk-test-shaders: %s" % e)
    result = header(text, mods)
    if "--print" in sys.argv:
        sys.stdout.write(result)
    else:
        OUT.write_text(result)
        print("wrote %s (%s)" % (OUT.relative_to(R), ", ".join("%s %d words" % (n, len(w)) for n, w in mods)))


if __name__ == "__main__":
    main()
