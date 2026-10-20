#!/usr/bin/env python3
"""Build a guest regression against compiler-produced ntdll data-slot offsets."""
from pathlib import Path
import os
import re
import struct
import subprocess

root = Path(__file__).resolve().parents[1]
bin_dir = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
image = root / 'wine/build-arm64ec/dlls/ntdll/arm64ec-windows/ntdll.dll'
data = (root / 'app/Madeira/arm64ec-windows/ntdll.dll').read_bytes()
pe, = struct.unpack_from('<I', data, 0x3c)
base, = struct.unpack_from('<Q', data, pe + 24 + 24)
image_size, = struct.unpack_from('<I', data, pe + 24 + 56)
symbols = subprocess.check_output([str(bin_dir / 'llvm-nm'), '-n', str(image)], text=True)

def rva(name):
    match = re.search(r'^([0-9a-fA-F]+) [dD] ' + re.escape(name) + '$', symbols, re.M)
    if not match:
        raise SystemExit('Missing unstripped ntdll data symbol: ' + name)
    value = int(match[1], 16) - base
    if value < 0 or value + 8 > image_size:
        raise SystemExit('Data symbol is outside the ntdll image: ' + name)
    return value

probe, recovery = rva('pWow64PrepareForException'), rva('pResetToConsistentState')
if probe // 4096 != recovery // 4096:
    raise SystemExit('Probe and recovery slot no longer share a 4KB page; update the regression')
out = root / 'app/Madeira/arm64ec-windows/ntdll-data-exception-x64.exe'
subprocess.run([str(bin_dir / 'x86_64-w64-mingw32-clang'), '-O2', '-Wall', '-Wextra', '-Werror',
                '-DNTDLL_DATA_PROBE_RVA=' + hex(probe), '-DNTDLL_IMAGE_SIZE=' + hex(image_size),
                str(root / 'tests/x64/ntdll-data-exception-x64.c'), '-o', str(out)], check=True,
               env={**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']})
print(f'{out.name}: probe RVA={probe:#x}, recovery slot RVA={recovery:#x}, image size={image_size:#x}')
