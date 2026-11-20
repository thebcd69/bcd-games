#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's OpenGL and Vulkan test programs (tests/dxtests/gl_test.c, vulkan_test.c; the Start menu's "OpenGL test" and "Vulkan test").

The same spinning textured cube as the DirectX tests (dxtest.h), each 64-bit and 32-bit. check-bcd-tests.py builds the four programs
again and compares them with the tracked ones, checks their imports and their Start menu shortcuts; this check covers the rest:
  1. The Vulkan shaders: vk_shaders.h records the SHA-256 of the vk_cube.spvasm it was made from, and it is this one; each array is a
     SPIR-V 1.0 module (magic, version, a sane bound) with the one capability Shader, the GLSL450 memory model and its entry point
     (a vertex "main" with the three inputs at locations 0, 1, 2, the push-constant matrix ColMajor at offset 0 with a stride of 16,
     and Position; a fragment "main" with OriginUpperLeft and one combined image sampler at set 0, binding 0). With SPIRV-Tools'
     library at hand (the KosmicKrisp host build, or SPIRV_TOOLS_LIB), tools/gen-vk-test-shaders.py assembles and validates the
     assembly again for Vulkan 1.0 and must write the header byte for byte.
  2. The sources: the licence header; each loads its DLL at run time (opengl32.dll, vulkan-1.dll), reads the session's switch
     (MADEIRA_GL_BACKEND, MADEIRA_VULKAN_LIBRARY: the variables the app exports when the game's own file turns OpenGL or Vulkan on)
     and, when it is off and the API cannot start, shows a message that names the Windows desktop's Options > Direct3D > OpenGL or
     Vulkan instead of only failing; the calls each needs (pixel format, context, GLSL for desktop OpenGL and for OpenGL ES, buffers,
     texture, SwapBuffers; instance with VK_KHR_win32_surface, portability enumeration as a second try, device with
     VK_KHR_portability_subset when listed, swapchain with FIFO, render pass with depth, the SPIR-V, push constant, staging upload,
     one fence and a rendered semaphore per swapchain image, out-of-date handling); the verdict after two seconds and the read-back
     of two pixels; the window ends the program and what was made is released; window titles say BCD, never Madeira.
  3. Where they go: tools/build-dx-tests.sh builds the four (the Vulkan one with Wine's own header, wine/include/wine/vulkan.h,
     through -iquote only), the Start menu has the four entries in "BCD Tests" with the bitness in each description, and the
     workflow runs this check; the message box title of the DirectX tests is still "BCD DirectX test" (dxtest.h's default).
"""
import hashlib
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
DX = R / "tests/dxtests"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-3000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def note(text):
    print("note " + text)


HEADER = "SPDX-License-Identifier: GPL-3.0-only"

# ---------------------------------------------------------------- 1. the Vulkan shaders
asm = (DX / "vk_cube.spvasm").read_text()
hdr = (DX / "vk_shaders.h").read_text()
m = re.search(r"// vk_cube\.spvasm sha256 ([0-9a-f]{64})", hdr)
check("vk_shaders.h records the SHA-256 of the vk_cube.spvasm it was made from, and it is this one",
      m is not None and m.group(1) == hashlib.sha256(asm.encode()).hexdigest(), "run python3 tools/gen-vk-test-shaders.py")
check("vk_cube.spvasm has the two parts the generator splits on, the vertex shader first", re.findall(r"^;; ==== (\w+)$", asm, re.M) == ["vk_cube_vert", "vk_cube_frag"])
mods = {}
for name in ("vk_cube_vert", "vk_cube_frag"):
    mm = re.search(r"static const uint32_t %s\[(\d+)\] = \{(.*?)\};" % name, hdr, re.S)
    check("vk_shaders.h has %s" % name, mm is not None)
    if mm:
        words = [int(x, 16) for x in re.findall(r"0x([0-9a-f]{8})", mm.group(2))]
        check("%s: the array's length is its word count" % name, len(words) == int(mm.group(1)), (len(words), mm.group(1)))
        mods[name] = words


def instructions(words):
    """[(opcode, operands)] of a SPIR-V module after its five-word header; raises on a word count that runs past the end."""
    out, i = [], 5
    while i < len(words):
        count, op = words[i] >> 16, words[i] & 0xFFFF
        if count == 0 or i + count > len(words):
            raise ValueError("bad instruction at word %d" % i)
        out.append((op, words[i + 1:i + count]))
        i += count
    return out


def string(ops):
    raw = struct.pack("<%dI" % len(ops), *ops)
    return raw[:raw.index(b"\0")].decode()


OP = {"Capability": 17, "MemoryModel": 14, "EntryPoint": 15, "ExecutionMode": 16, "Decorate": 71, "MemberDecorate": 72, "Variable": 59,
      "TypeImage": 25, "TypeSampledImage": 27}
for name, words in mods.items():
    check("%s: SPIR-V magic, version 1.0 (what Vulkan 1.0 takes), a bound above every id" % name,
          len(words) > 5 and words[0] == 0x07230203 and words[1] == 0x00010000 and words[3] > 1 and words[4] == 0, [hex(w) for w in words[:5]])
    try:
        ins = instructions(words)
    except ValueError as e:
        check("%s: every instruction's word count fits the module" % name, False, e)
        continue
    check("%s: every instruction's word count fits the module" % name, True)
    caps = [o[0] for op, o in ins if op == OP["Capability"]]
    check("%s: the one capability is Shader" % name, caps == [1], caps)
    check("%s: Logical addressing, GLSL450 memory model" % name, [o for op, o in ins if op == OP["MemoryModel"]] == [[0, 1]])
    eps = [(o[0], string(o[2:])) for op, o in ins if op == OP["EntryPoint"]]
    deco = [o for op, o in ins if op == OP["Decorate"]]
    if name == "vk_cube_vert":
        check("vk_cube_vert: one entry point, a vertex shader called main", eps == [(0, "main")], eps)
        locs = sorted(o[2] for o in deco if o[1] == 30)   # Location
        check("vk_cube_vert: the three inputs at locations 0, 1, 2 and the two outputs at 0, 1", locs == [0, 0, 1, 1, 2], locs)
        check("vk_cube_vert: one output is the built-in Position", sum(1 for o in deco if o[1:] == [11, 0]) == 1)
        mdeco = [o for op, o in ins if op == OP["MemberDecorate"]]
        check("vk_cube_vert: the push constant's matrix is ColMajor at offset 0 with a stride of 16 (the C code's rows are its columns)",
              [0, 5] in [o[1:] for o in mdeco] and [0, 35, 0] in [o[1:] for o in mdeco] and [0, 7, 16] in [o[1:] for o in mdeco], mdeco)
        check("vk_cube_vert: a PushConstant variable (storage class 9)", any(op == OP["Variable"] and o[2] == 9 for op, o in ins))
    else:
        check("vk_cube_frag: one entry point, a fragment shader called main", eps == [(4, "main")], eps)
        check("vk_cube_frag: OriginUpperLeft (Vulkan's only origin)", any(op == OP["ExecutionMode"] and o[1] == 7 for op, o in ins))
        check("vk_cube_frag: a combined image sampler at descriptor set 0, binding 0",
              any(op == OP["TypeSampledImage"] for op, o in ins) and sum(1 for o in deco if o[1:] == [34, 0]) == 1 and sum(1 for o in deco if o[1:] == [33, 0]) == 1)
        check("vk_cube_frag: the colour output at location 0, the two inputs at 0 and 1", sorted(o[2] for o in deco if o[1] == 30) == [0, 0, 1])

gen = R / "tools/gen-vk-test-shaders.py"
sys.path.insert(0, str(R / "tools"))
spec = __import__("importlib.util").util.spec_from_file_location("genvk", gen)
genvk = __import__("importlib.util").util.module_from_spec(spec)
spec.loader.exec_module(genvk)
lib = genvk.find_library()
if lib:
    res = subprocess.run([sys.executable, str(gen), "--print"], capture_output=True, text=True)
    check("tools/gen-vk-test-shaders.py assembles and validates vk_cube.spvasm for Vulkan 1.0 (SPIRV-Tools)", res.returncode == 0, res.stderr)
    check("... and writes vk_shaders.h as it is (no pending change)", res.returncode == 0 and res.stdout == hdr)
else:
    note("SPIRV-Tools' library is not here (toolchains/kosmickrisp-host/llvm-22/lib or SPIRV_TOOLS_LIB): the assembly was not assembled again")
check("tools/gen-vk-test-shaders.py has the BCD licence header and validates for Vulkan 1.0",
      HEADER in gen.read_text()[:400] and "SPV_ENV_VULKAN_1_0 = 1" in gen.read_text() and "spvValidate" in gen.read_text())

# ---------------------------------------------------------------- 2. the sources
gl = (DX / "gl_test.c").read_text()
vk = (DX / "vulkan_test.c").read_text()
dxh = (DX / "dxtest.h").read_text()
for f, text in (("gl_test.c", gl), ("vulkan_test.c", vk), ("vk_cube.spvasm", asm), ("vk_shaders.h", hdr), ("../host/check-vk-gl-tests.py", Path(__file__).read_text())):
    check("%s has the BCD licence header" % f, HEADER in text[:400] and "LICENSE-BCD.md" in text[:400])
check("dxtest.h: the message box title is DXT_BOX_TITLE, \"BCD DirectX test\" unless a test names its own before the header",
      '#ifndef DXT_BOX_TITLE\n#define DXT_BOX_TITLE "BCD DirectX test"\n#endif' in dxh and "MessageBoxA(NULL, text, DXT_BOX_TITLE, MB_OK | MB_ICONERROR)" in dxh)
check("the OpenGL and Vulkan tests name their message boxes before they include dxtest.h",
      gl.index('#define DXT_BOX_TITLE "BCD OpenGL test"') < gl.index('#include "dxtest.h"') and vk.index('#define DXT_BOX_TITLE "BCD Vulkan test"') < vk.index('#include "dxtest.h"'))
OFF_GL = "Direct3D > \"\n                \"OpenGL > Desktop OpenGL"
check("gl: loads opengl32.dll at run time and takes WGL and OpenGL 1.1 from it, OpenGL 2.0 from wglGetProcAddress (a 1, 2, 3 or -1 answer counts as none)",
      'LoadLibraryA("opengl32.dll")' in gl and "GetProcAddress(dll, #name)" in gl and "p_wglGetProcAddress(#name)" in gl and "(INT_PTR)p_##name >= -1 && (INT_PTR)p_##name <= 3" in gl
      and "-lopengl32" not in (R / "tools/build-dx-tests.sh").read_text())
check("gl: reads MADEIRA_GL_BACKEND; off and no pixel format, context or DLL: the message names the Windows desktop's Options > Direct3D > OpenGL > Desktop OpenGL",
      'GetEnvironmentVariableA("MADEIRA_GL_BACKEND"' in gl and gl.count("opengl_off(") == 4 and "Windows desktop's Options" in gl and "Home > Windows desktop > the sliders button" in gl
      and OFF_GL in gl and "stop the Windows desktop and start it again" in gl)
check("gl: a pixel format with a depth buffer, double-buffered (ChoosePixelFormat, SetPixelFormat), wglCreateContext, wglMakeCurrent",
      all(x in gl for x in ("PFD_DOUBLEBUFFER", "pfd.cDepthBits = 24", "ChoosePixelFormat(dc, &pfd)", "SetPixelFormat(dc, format, &pfd)", "p_wglCreateContext(dc)", "p_wglMakeCurrent(dc, rc)")))
check("gl: GL_VERSION decides GLSL 1.10 (desktop) or GLSL ES 1.00 with a float precision (OpenGL ES); both texts are the same shaders",
      'es = !strncmp(version, "OpenGL ES", 9)' in gl and '"#version 110\\n"' in gl and '"#version 100\\nprecision mediump float;\\n"' in gl and "gl_FragColor = texture2D(tex, v_uv) * v_col" in gl)
check("gl: the attributes at the dxt_vertex offsets (position 0, uv 12, colour 20 normalised bytes), indexed 16-bit draw, depth test, no culling, a texture",
      all(x in gl for x in ("p_glVertexAttribPointer(0, 3, GL_FLOAT", "(const void *)12", "p_glVertexAttribPointer(2, 4, GL_UNSIGNED_BYTE, GL_TRUE", "p_glDrawElements(GL_TRIANGLES, 36, GL_UNSIGNED_SHORT",
                                "p_glEnable(GL_DEPTH_TEST)", "p_glDisable(GL_CULL_FACE)", "dxt_checker(texels)", "p_glTexImage2D(GL_TEXTURE_2D")))
check("gl: the matrix goes in with transpose GL_FALSE (GLSL reads the C rows as columns: the same product)", "p_glUniformMatrix4fv(u_mvp, 1, GL_FALSE, mvp.m)" in gl)
check("gl: SwapBuffers each frame, a swap interval of 1 where the driver has it, the verdict after two seconds, a glReadPixels read-back before the next swap",
      all(x in gl for x in ("SwapBuffers(dc)", "wglSwapIntervalEXT", "dxt_verdict(API, presented, failures", "dxt_seconds(&clock) >= 2.0f", "p_glReadPixels(DXT_W / 2, DXT_H / 2"))
      and gl.index("readback();") < gl.index("if (SwapBuffers(dc))"))
check("gl: runs while the window is open, then releases the program, buffers, texture and context",
      "while (dxt_pump())" in gl and all(x in gl[gl.index("while (dxt_pump())"):] for x in ("p_glDeleteProgram(prog)", "p_glDeleteBuffers(2, buf)", "p_glDeleteTextures(1, &tex)", "p_wglDeleteContext(rc)")))
OFF_VK = "Direct3D > \"\n                \"Vulkan (experimental)"
check("vulkan: loads vulkan-1.dll at run time and takes everything through vkGetInstanceProcAddr (no import library, VK_NO_PROTOTYPES)",
      'LoadLibraryA("vulkan-1.dll")' in vk and 'GetProcAddress(dll, "vkGetInstanceProcAddr")' in vk and "#define VK_NO_PROTOTYPES" in vk and "vkGetInstanceProcAddr_(vk.instance, #name)" in vk
      and vk.index("#define VK_NO_PROTOTYPES") < vk.index('#include "wine/vulkan.h"'))
check("vulkan: reads MADEIRA_VULKAN_LIBRARY; off and no DLL, no driver or no GPU: the message names the Windows desktop's Options > Direct3D > Vulkan (experimental)",
      'GetEnvironmentVariableA("MADEIRA_VULKAN_LIBRARY"' in vk and vk.count("vulkan_off(") >= 5 and "Windows desktop's Options" in vk and OFF_VK in vk
      and "VK_ERROR_INCOMPATIBLE_DRIVER" in vk and "stop the Windows desktop and start it again" in vk)
check("vulkan: logs the driver's file name only (the bundle path is not written)", "strrchr(lib, '/')" in vk and "base ? base + 1 : lib" in vk)
check("vulkan: an instance with VK_KHR_surface and VK_KHR_win32_surface, Vulkan 1.0; VK_KHR_portability_enumeration as a second try",
      all(x in vk for x in ('"VK_KHR_surface", "VK_KHR_win32_surface"', "app.apiVersion = VK_API_VERSION_1_0", '"VK_KHR_portability_enumeration"', "VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR")))
check("vulkan: a Win32 surface on the window, a queue family that draws and presents, a device with VK_KHR_swapchain and VK_KHR_portability_subset when listed",
      all(x in vk for x in ("vkCreateWin32SurfaceKHR_", "VK_QUEUE_GRAPHICS_BIT", "vkGetPhysicalDeviceSurfaceSupportKHR_", '"VK_KHR_swapchain"', '"VK_KHR_portability_subset"')))
check("vulkan: a FIFO swapchain (TRANSFER_SRC only when offered), a render pass with depth that ends in PRESENT_SRC, dynamic viewport and scissor",
      all(x in vk for x in ("VK_PRESENT_MODE_FIFO_KHR", "vk.can_read ? VK_IMAGE_USAGE_TRANSFER_SRC_BIT : 0", "att[0].finalLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR", "VK_DYNAMIC_STATE_VIEWPORT", "VK_DYNAMIC_STATE_SCISSOR",
                                "VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT")))
check("vulkan: the pipeline takes vk_shaders.h's SPIR-V, the vertex layout of dxt_vertex, the 64-byte matrix as a push constant, one combined image sampler",
      all(x in vk for x in ('#include "vk_shaders.h"', "mi.pCode = vk_cube_vert", "mi.pCode = vk_cube_frag", "{ 2, 0, VK_FORMAT_R8G8B8A8_UNORM, 20 }", "{ 1, 0, VK_FORMAT_R32G32_SFLOAT, 12 }",
                                "VK_SHADER_STAGE_VERTEX_BIT, 0, sizeof(dxt_mat)", "VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER")))
check("vulkan: the projection's y is turned for Vulkan's clip space (Direct3D's 0 to 1 depth is Vulkan's)", "proj.m[5] = -proj.m[5]" in vk)
check("vulkan: the texture goes through a staging buffer into an optimal image, with the two layout barriers",
      all(x in vk for x in ("vkCmdCopyBufferToImage_", "VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL", "VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL", "VK_IMAGE_TILING_OPTIMAL")))
check("vulkan: one frame in flight (a fence waited for before the next), a rendered semaphore a swapchain image, out-of-date and suboptimal make the swapchain again",
      all(x in vk for x in ("vkWaitForFences_(vk.dev, 1, &vk.done", "VkSemaphore rendered[MAX_IMAGES]", "submit.pSignalSemaphores = &vk.rendered[i]", "present.pWaitSemaphores = &vk.rendered[i]",
                                "VK_ERROR_OUT_OF_DATE_KHR", "rebuild(\"vkAcquireNextImageKHR: out of date\")", "suboptimal++ < 3", "si.oldSwapchain = old")))
check("vulkan: a minimised window (no size) keeps the old swapchain and waits a little instead of spinning", "if (made == 2) Sleep(50);" in vk and "if (!vk.extent.width || !vk.extent.height) return 2;" in vk)
check("vulkan: the verdict after two seconds, then two pixels of the next frame copied back (centre and corner) and logged once it has finished",
      all(x in vk for x in ("dxt_verdict(API, presented, failures", "dxt_seconds(&clock) >= 2.0f", "vkCmdCopyImageToBuffer_", "VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL", "log_readback()")))
check("vulkan: when the window is closed, the device is idle before anything is destroyed, and everything made is destroyed",
      "while (dxt_pump())" in vk and "vkDeviceWaitIdle_(vk.dev);\n        destroy_swapchain();" in vk and all(("vkDestroy%s_" % x) in vk for x in ("Pipeline", "PipelineLayout", "RenderPass", "Sampler", "Device", "SurfaceKHR", "Instance", "SwapchainKHR")))
titles = re.findall(r'"(BCD (?:OpenGL|Vulkan) test[^"]*)"', gl + vk)
check("the window titles say BCD OpenGL test / BCD Vulkan test with the bitness, never Madeira",
      'snprintf(title, sizeof title, "BCD OpenGL test (%d-bit)", DXT_BITS)' in gl and 'snprintf(title, sizeof title, "BCD Vulkan test (%d-bit)", DXT_BITS)' in vk
      and not any("Madeira" in t for t in titles) and "Madeira" not in gl and "Madeira" not in vk, titles)

# ---------------------------------------------------------------- 3. where they go
bs = (R / "tools/build-dx-tests.sh").read_text()
for line in ('build_one x86_64 gl-test-x64.exe          gl_test.c         "$SHIP" -lgdi32',
             'build_one x86_64 vulkan-test-x64.exe      vulkan_test.c     "$SHIP" -iquote "$WINE_INC"',
             'build_one i686   gl-test-x86.exe          gl_test.c         "$SHIP32" -lgdi32',
             'build_one i686   vulkan-test-x86.exe      vulkan_test.c     "$SHIP32" -iquote "$WINE_INC"'):
    check("tools/build-dx-tests.sh: %s" % " ".join(line.split()[1:4]), line in bs)
check("tools/build-dx-tests.sh finds Wine's header in the wine submodule's include folder, for \"...\" includes only",
      'WINE_INC="${WINE_INC:-$R/wine/include}"' in bs and "-I\"$WINE_INC\"" not in bs and '#include "wine/vulkan.h"' in vk)
start_c = (APP / "BCDStartMenu.c").read_text()
for name, exe, bits in (("OpenGL test", "gl-test-x64.exe", 64), ("OpenGL test (32-bit)", "gl-test-x86.exe", 32),
                        ("Vulkan test", "vulkan-test-x64.exe", 64), ("Vulkan test (32-bit)", "vulkan-test-x86.exe", 32)):
    api = name.split()[0]
    check("the Start menu: \"%s\" starts %s in BCD Tests" % (name, exe),
          re.search(r'\{ BCD_TESTS, +"%s", +"%s", +"A spinning cube drawn with %s \(%d-bit\)" \}' % (re.escape(name), re.escape(exe), api, bits), start_c) is not None)
check("the Start menu: the OpenGL and Vulkan tests follow the DirectX 12 test",
      start_c.index('"DirectX 12 test"') < start_c.index('"OpenGL test"') < start_c.index('"Vulkan test"') < start_c.index('"DirectDraw test"'))
gi = (R / ".gitignore").read_text()
check(".gitignore lets the 32-bit programs through (*-test-x86.exe)", "!app/Madeira/i386-windows/*-test-x86.exe" in gi)
for exe, folder in (("gl-test-x64.exe", "arm64ec-windows"), ("vulkan-test-x64.exe", "arm64ec-windows"), ("gl-test-x86.exe", "i386-windows"), ("vulkan-test-x86.exe", "i386-windows")):
    check("%s is in app/Madeira/%s" % (exe, folder), (APP / folder / exe).is_file())
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check, after the graphics, sound, video and input tests' check",
      "tests/host/check-vk-gl-tests.py" in wf and wf.index("tests/host/check-bcd-tests.py") < wf.index("tests/host/check-vk-gl-tests.py"))
bt = (R / "tests/host/check-bcd-tests.py").read_text()
check("check-bcd-tests.py builds and compares the four programs and checks that they import neither opengl32 nor vulkan-1",
      all('"%s"' % e in bt for e in ("gl-test-x64.exe", "gl-test-x86.exe", "vulkan-test-x64.exe", "vulkan-test-x86.exe")) and "|opengl32|vulkan-1)" in bt
      and '"vulkan_test.c": ["-iquote", WINE_INC]' in bt)

sys.exit(0 if ok else 1)
