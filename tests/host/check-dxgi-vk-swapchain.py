#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXGI swap chains for a Direct3D 12 device that presents through Vulkan (dxmt 91cf47a3 by 125hz, upstream merge 2026-11-14).

DXMT's DXGI made a swap chain only for a device of its own (IMTLDXGIDevice); any other device ended CreateSwapChainForHwnd with
"Unsupported device type". A device that offers vkd3d-proton's IDXGIVkSwapChainFactory now gets its swap chain from that factory
(dxgi_swapchain_vk.cpp), unless MADEIRA_DXGI_VK_SWAPCHAIN=0. Only the branch that failed before changes: this reads dxmt/src/dxgi
(or $DXMT_SRC_DIR) and proves that a device of DXMT's own (every Direct3D 10/11 game, and BCD's Direct3D 12) is never asked for the
Vulkan factory, and that both builds of dxgi.dll compile the new file (meson for the 32-bit one, tools/build-dxgi-dll.sh for
dxgi-src.dll, which would otherwise fail to link and not ship). SKIP (exit 0) without the dxmt sources."""
import os
import pathlib
import re
import sys

R = pathlib.Path(__file__).resolve().parents[2]
G = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src/dxgi"
if not (G / "dxgi_factory.cpp").exists():
    print(f"SKIP: no dxmt sources at {G}")
    sys.exit(0)
ok = True


def check(name, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    ok &= bool(cond)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dxgi-vk-swapchain.py" in wf)
f = (G / "dxgi_factory.cpp").read_text()
vk = (G / "dxgi_swapchain_vk.cpp").read_text() if (G / "dxgi_swapchain_vk.cpp").exists() else ""
check("the new file and its interface header are there", bool(vk) and (G / "dxgi_vk_interfaces.h").exists())
check("the Vulkan factory is asked for only after the device refused IMTLDXGIDevice, and only while the switch is on",
      re.search(r"if \(FAILED\(pDevice->QueryInterface\(IID_PPV_ARGS\(&metal_dxgi_device\)\)\) &&\s*"
                r"\(!VkSwapChainEnabled\(\) \|\|\s*FAILED\(pDevice->QueryInterface\(IID_PPV_ARGS\(&vk_swapchain_factory\)\)\)\)\) \{", f) is not None)
a = f.find("if (metal_dxgi_device == nullptr)")
check("CreateVkSwapChain is reached only without a device of DXMT's own (one call site)",
      a > 0 and f.count("CreateVkSwapChain(") == 2 and f[a:a + 120].count("return CreateVkSwapChain(") == 1)
check("MADEIRA_DXGI_VK_SWAPCHAIN=0 restores the refusal; read once",
      'static const bool enabled = env::getEnvVar("MADEIRA_DXGI_VK_SWAPCHAIN") != "0";' in vk)
check("the Vulkan loader is looked up, never loaded (no LoadLibrary in the new file)", "LoadLibrary" not in vk and "GetModuleHandle" in vk)
check("meson builds it into dxgi.dll (the 32-bit farm)", "'dxgi_swapchain_vk.cpp'," in (G / "meson.build").read_text())
sh = (R / "tools/build-dxgi-dll.sh").read_text()
check("tools/build-dxgi-dll.sh compiles and links it into dxgi-src.dll when the pin has it, and builds an older pin as before",
      'if [ -f "$G/dxgi_swapchain_vk.cpp" ]; then' in sh and 'compile "$G/dxgi_swapchain_vk.cpp" "$OUT/obj/dxgi_swapchain_vk.o"' in sh
      and '${VK_OBJ:+"$VK_OBJ"}' in sh)
if not ok:
    print("FAILED: dxgi-vk-swapchain")
    sys.exit(1)
print("PASS: DXGI swap chains through vkd3d-proton's factory only where creation failed before; both dxgi.dll builds compile the file")
