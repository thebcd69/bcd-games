# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# CMake toolchain for tools/build-dxcompiler.sh: DirectXShaderCompiler's dxcompiler.dll for x86-64 Windows, cross-compiled on the Mac
# with the tracked llvm-mingw (ucrt). MINGW_BIN names its bin directory.
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(MINGW_BIN "$ENV{MINGW_BIN}")
set(CMAKE_C_COMPILER   "${MINGW_BIN}/x86_64-w64-mingw32-clang")
set(CMAKE_CXX_COMPILER "${MINGW_BIN}/x86_64-w64-mingw32-clang++")
set(CMAKE_RC_COMPILER  "${MINGW_BIN}/x86_64-w64-mingw32-windres")
set(CMAKE_AR           "${MINGW_BIN}/x86_64-w64-mingw32-ar")
set(CMAKE_RANLIB       "${MINGW_BIN}/x86_64-w64-mingw32-ranlib")
set(CMAKE_DLLTOOL      "${MINGW_BIN}/x86_64-w64-mingw32-dlltool")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
