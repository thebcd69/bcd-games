#!/usr/bin/env python3
"""DXMT's Direct3D 9: d3d9.customDriver overrides the adapter's driver file name (DXMT_CONFIG, empty = off).

madeira-bcd (build 623), as upstream Madeira's willfaust/dxmt f25e5568d. d3d9.customVendorId, customDeviceId and
customDeviceDesc already let a title see another card. Fallout 3 and Fallout: New Vegas also read the Driver field of
D3DADAPTER_IDENTIFIER9 and pick their render path from the description and the driver together: with DXMT's own driver name
New Vegas takes its basic path and crashes loading a save at 00B57AA9 (BCD's fallout-new-vegas notes: four logs, the same
address). nv4_disp.dll, as the d3d9.dll wrapper the game's players install reports, gives it NVIDIA's path. Nothing changes
unless the option is set (BCD's "NVIDIA GeForce 7900 GS" card sets it, app/Madeira/BCDGPUCards.swift). The adapter log line
marks the override like the other three. Idempotent; fails by name if an anchor moved. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/d3d9/d3d9_interface.cpp")
MARKER = "madeira-bcd: d3d9.customDriver"

EDITS = [
    ("  std::string desc;\n};\n",
     "  std::string desc;\n  std::string driver;   /* madeira-bcd: d3d9.customDriver */\n};\n"),
    ('    o.desc = cfg.getOption<std::string>("d3d9.customDeviceDesc", "");\n',
     '    o.desc = cfg.getOption<std::string>("d3d9.customDeviceDesc", "");\n'
     '    o.driver = cfg.getOption<std::string>("d3d9.customDriver", "");\n'),
    ('  if (!ov.desc.empty())\n    std::snprintf(pIdentifier->Description, MAX_DEVICE_IDENTIFIER_STRING, "%s", ov.desc.c_str());\n',
     '  if (!ov.desc.empty())\n    std::snprintf(pIdentifier->Description, MAX_DEVICE_IDENTIFIER_STRING, "%s", ov.desc.c_str());\n'
     '  if (!ov.driver.empty())   /* Fallout 3 / New Vegas pick their render path from the description and the driver */\n'
     '    std::snprintf(pIdentifier->Driver, MAX_DEVICE_IDENTIFIER_STRING, "%s", ov.driver.c_str());\n'),
    ('(ov.vendorId >= 0 || ov.deviceId >= 0 || !ov.desc.empty()) ? "  [overridden by DXMT_CONFIG d3d9.custom*]" : ""',
     '(ov.vendorId >= 0 || ov.deviceId >= 0 || !ov.desc.empty() || !ov.driver.empty()) ? "  [overridden by DXMT_CONFIG d3d9.custom*]" : ""'),
]
# The same change as upstream wrote it (willfaust/dxmt f25e5568).
UPSTREAM = [
    "  std::string desc;\n  std::string driver;\n};\n",
    '    o.driver = cfg.getOption<std::string>("d3d9.customDriver", "");\n',
    '  if (!ov.driver.empty())\n    std::snprintf(pIdentifier->Driver, MAX_DEVICE_IDENTIFIER_STRING, "%s", ov.driver.c_str());\n',
    '(ov.vendorId >= 0 || ov.deviceId >= 0 || !ov.desc.empty() || !ov.driver.empty()) ? "  [overridden by DXMT_CONFIG d3d9.custom*]" : ""',
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print(f"{PATH}: already patched")
        return
    # Since the dxmt pin of the 2026-11-14 upstream merge the fork carries willfaust/dxmt f25e5568 itself (the same four
    # changes, without this script's comments): nothing is left to do, on the pristine pin and on a patched tree alike.
    if all(s.count(up) == 1 for up in UPSTREAM) and all(s.count(old) == 0 for old, _ in EDITS[:1] + EDITS[3:]):
        print(f"{PATH}: d3d9.customDriver is upstream (willfaust/dxmt f25e5568); nothing to do")
        return
    for old, new in EDITS:
        if s.count(old) != 1:
            sys.exit(f"patch-dxmt-d3d9-custom-driver: anchor not found once in {PATH}: {old[:60]!r}")
        s = s.replace(old, new, 1)
    PATH.write_text(s)
    print(f"{PATH}: patched")


main()
