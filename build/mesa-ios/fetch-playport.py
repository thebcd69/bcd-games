#!/usr/bin/env python3
# Copyright 2026 intraducine. This file was taken from https://github.com/willfaust/Madeira (branch playport/kosmickrisp, commit 07471e3)
# under GPL-3.0-or-later. See THIRD-PARTY-NOTICES.md.
# Modified by BCD (thebcd69), 2026-11-14 (U6 counter-review H2): the archive is downloaded to a .part file and gets its name only
# after its SHA-256 matched (an archive of that name with another hash is still refused, as before), and it is unpacked into a
# temporary folder that becomes the source folder, with its pin marker, in one rename. Before, an interrupted download or
# unpacking left a file or folder that made every later run fail.
"""Fetch the immutable source used by the Playport patch series; never reset a checkout."""
from pathlib import Path
import hashlib
import shutil
import sys
import tarfile
import urllib.request

PIN = "b39d173ca9369c19fda2635be6655d308067e52d"
SHA256 = "1d50da0fbedaec06f182932d01e9c1cde1f391105bfe27b77ff09944fcea3dd6"
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
source = root / ("mesa-" + PIN)
marker = source / ".madeira-source-pin"
if source.exists():
    if not marker.exists() or marker.read_text().strip() != PIN:
        raise SystemExit("Source directory exists without the expected pin marker; choose another KK_WORK.")
    raise SystemExit(0)
archive = root / ("mesa-" + PIN + ".tar.gz")
def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
if archive.exists() and sha256(archive) != SHA256:
    raise SystemExit("Mesa source archive hash mismatch")
if not archive.exists():
    part = archive.with_name(archive.name + ".part")
    urllib.request.urlretrieve(f"https://gitlab.freedesktop.org/mesa/mesa/-/archive/{PIN}/mesa-{PIN}.tar.gz", part)
    if sha256(part) != SHA256:
        part.unlink()
        raise SystemExit("Mesa source archive hash mismatch")
    part.replace(archive)
unpack = root / (".mesa-" + PIN + ".unpack")
if unpack.exists():
    shutil.rmtree(unpack)
unpack.mkdir()
with tarfile.open(archive) as stream:
    stream.extractall(unpack, filter="data")
(unpack / source.name / marker.name).write_text(PIN + "\n")
(unpack / source.name).rename(source)
shutil.rmtree(unpack)
