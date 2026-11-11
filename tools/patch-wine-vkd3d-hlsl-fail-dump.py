#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's HLSL compiler (vkd3d-utils) dumps the shader it refused, on request (night round 2026-11-11, G11 S3).

S.T.A.L.K.E.R. (Clear Sky, Shadow of Chernobyl, Call of Pripyat: xrEngine compiles ~100 Direct3D 9 shaders at run time) stops at the FIRST
shader vkd3d refuses and the log carries only that shader's first error ("<anonymous>:496:37: E5017 ..."). Each device round then finds one more
wall (`point`, then `register(ps, c0)`); the source of the failing shader is never in the log, so the next error stays unknown until the next
round. With env.MADEIRA_HLSL_FAIL_DUMP = 1 (off by default; a game's list row sets it) D3DCompile2VKD3D writes, for a compile that FAILED
(at most 3 per process, 800 lines and 64 KB each):
  [hlsl-fail] profile=.. entry=.. flags=.. bytes=..
  [hlsl-fail] <one line of the compiler's text>        (every line)
  [hlsl-fail]   496| <line 496 of the preprocessed source>     (the source as the compiler's parser reads it: includes and macros expanded;
                                                                the numbers are the ones the error lines cite)
The preprocessed text is produced by the same vkd3d_shader_preprocess() call D3DPreprocess uses, with the call's own macros and include
callbacks; with the key off nothing is read, run or printed, and the compile result, the messages and the blobs are exactly as before. The lines
go to the process's stderr like the existing `vkd3d:...:fixme:D3DCompile2VKD3D` lines, i.e. into the session log. The i386 farm builds it (the
64-bit d3dcompiler_47.dll is a committed binary). Idempotent; fails by name when the call moved.
Usage: patch-wine-vkd3d-hlsl-fail-dump.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: hlsl fail dump"
FILE = "libs/vkd3d/libs/vkd3d-utils/vkd3d_utils_main.c"
ANCHOR = "HRESULT WINAPI D3DCompile2VKD3D(const void *data, SIZE_T data_size, const char *filename,\n"
CALL = "    ret = vkd3d_shader_compile(&compile_info, &byte_code, &messages);\n"
HELPER = (
    "/* " + MARKER + " (tools/patch-wine-vkd3d-hlsl-fail-dump.py, night round 2026-11-11, S.T.A.L.K.E.R. Clear Sky): with\n"
    " * env.MADEIRA_HLSL_FAIL_DUMP = 1 a compile that FAILED writes the compiler's text and the preprocessed source (the text the parser reads, so the\n"
    " * line numbers are the ones the error lines cite) to stderr: 3 compiles a process, 800 lines and 64 KB each. Off: nothing is read or run. */\n"
    "static void madeira_hlsl_fail_dump(const struct vkd3d_shader_compile_info *compile_info,\n"
    "        const struct vkd3d_shader_preprocess_info *preprocess_info, const char *profile, const char *entry_point,\n"
    "        unsigned int flags, const char *messages)\n"
    "{\n"
    "    static const struct vkd3d_shader_compile_option options[] =\n"
    "    {\n"
    "        {VKD3D_SHADER_COMPILE_OPTION_API_VERSION, VKD3D_SHADER_API_VERSION_CURRENT},\n"
    "    };\n"
    "    static uint32_t dumps;\n"
    "    static int enabled = -1;\n"
    "    struct vkd3d_shader_preprocess_info preprocess = *preprocess_info;\n"
    "    struct vkd3d_shader_compile_info info = *compile_info;\n"
    "    struct vkd3d_shader_code code = {0};\n"
    "    const char *p, *end, *text;\n"
    "    char *preprocess_messages = NULL;\n"
    "    size_t len, left, advance, total = 0;\n"
    "    unsigned int line;\n"
    "    int ret;\n"
    "\n"
    "    if (enabled < 0)\n"
    "    {\n"
    "        const char *value = getenv(\"MADEIRA_HLSL_FAIL_DUMP\");\n"
    "\n"
    "        enabled = value && value[0] == '1';\n"
    "    }\n"
    "    if (!enabled || vkd3d_atomic_increment_u32(&dumps) > 3)\n"
    "        return;\n"
    "\n"
    "    ERR(\"[hlsl-fail] profile=%s entry=%s flags=%#x bytes=%\"PRIuPTR\"\\n\", debugstr_a(profile), debugstr_a(entry_point), flags,\n"
    "            (uintptr_t)compile_info->source.size);\n"
    "    for (p = messages; p && *p; p = end ? end + 1 : p + strlen(p))\n"
    "    {\n"
    "        end = strchr(p, '\\n');\n"
    "        len = end ? (size_t)(end - p) : strlen(p);\n"
    "        if (len)\n"
    "            ERR(\"[hlsl-fail] %.*s\\n\", (int)(len < 600 ? len : 600), p);\n"
    "    }\n"
    "\n"
    "    preprocess.next = NULL;\n"
    "    info.next = &preprocess;\n"
    "    info.target_type = VKD3D_SHADER_TARGET_NONE;\n"
    "    info.options = options;\n"
    "    info.option_count = ARRAY_SIZE(options);\n"
    "    if ((ret = vkd3d_shader_preprocess(&info, &code, &preprocess_messages)) < 0)\n"
    "    {\n"
    "        ERR(\"[hlsl-fail] the source could not be preprocessed (%d)\\n\", ret);\n"
    "        vkd3d_shader_free_messages(preprocess_messages);\n"
    "        return;\n"
    "    }\n"
    "    vkd3d_shader_free_messages(preprocess_messages);\n"
    "\n"
    "    text = code.code;\n"
    "    for (line = 1, p = text; p < text + code.size && line <= 800 && total < 65536; ++line)\n"
    "    {\n"
    "        left = (size_t)(text + code.size - p);\n"
    "        end = memchr(p, '\\n', left);\n"
    "        len = end ? (size_t)(end - p) : left;\n"
    "        advance = end ? len + 1 : left;\n"
    "        if (len && p[len - 1] == '\\r')\n"
    "            --len;\n"
    "        ERR(\"[hlsl-fail] %5u| %.*s\\n\", line, (int)(len < 600 ? len : 600), p);\n"
    "        total += len + 1;\n"
    "        p += advance;\n"
    "    }\n"
    "    if (p < text + code.size)\n"
    "        ERR(\"[hlsl-fail] ... the dump stops here (800 lines or 64 KB)\\n\");\n"
    "    vkd3d_shader_free_shader_code(&code);\n"
    "}\n"
    "\n"
)
CALL_NEW = (CALL +
            "    if (ret < 0) /* " + MARKER + " */\n"
            "        madeira_hlsl_fail_dump(&compile_info, &preprocess_info, profile, entry_point, flags, messages);\n")


def patch(src):
    if MARKER in src:
        if src.count(HELPER) != 1 or src.count(CALL_NEW) != 1:
            raise ValueError("partial or changed hlsl fail dump patch")
        return src
    if src.count(ANCHOR) != 1:
        raise ValueError("D3DCompile2VKD3D of vkd3d_utils_main.c not found once (vkd3d changed)")
    if src.count(CALL) != 1 or src.index(CALL) < src.index(ANCHOR):
        raise ValueError("the vkd3d_shader_compile call of D3DCompile2VKD3D not found once after the function (vkd3d changed)")
    return src.replace(ANCHOR, HELPER + ANCHOR, 1).replace(CALL, CALL_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-vkd3d-hlsl-fail-dump.py WINE-DIR")
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-vkd3d-hlsl-fail-dump: " + str(error))
    if result == source:
        print("patch-wine-vkd3d-hlsl-fail-dump: already in")
    else:
        path.write_text(result)
        print("patch-wine-vkd3d-hlsl-fail-dump: %s patched" % FILE)


if __name__ == "__main__":
    main()
