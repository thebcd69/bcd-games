#!/bin/bash
# madeira-bcd (Windows Vulkan): Wine's winevulkan.dll and vulkan-1.dll (ARM64EC) from the wine submodule, into the bundle's vulkan folder
# (NOT the arm64ec-windows farm: that is linked into every prefix, and a program that probes for vulkan-1.dll must keep finding the stand-in).
# The same build as upstream Madeira's build/windows-runtime/build.sh does; build-modules.sh shares wine/build-arm64ec with the other modules.
#   usage (repository root):  bash tools/build-vulkan-dlls.sh
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$R/app/Madeira/vulkan/arm64ec-windows" bash "$R/build/wine-pe/build-modules.sh" winevulkan vulkan-1
ls -l "$R/app/Madeira/vulkan/arm64ec-windows"
