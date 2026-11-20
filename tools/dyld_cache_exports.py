# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The exported symbols of every library in a dyld shared cache (a simulator runtime's), read the way dyld reads them (2026-11-20).

Xcode's dsc_extractor writes cache dylibs without their export tries, and their symbol tables miss most exports, so this reads the
cache files directly: the mappings of every file of the cache (main and subcaches), the image list, each image's load commands, and
its export trie through __LINKEDIT's address. Re-exported libraries are followed by the checker, not here.

  DyldCache(<dir holding dyld_sim_shared_cache_arm64*>).exports("/usr/lib/libobjc.A.dylib")  -> set of names, or None
"""
import mmap
import struct
from pathlib import Path

LC_SEGMENT_64 = 0x19
LC_DYLD_INFO_ONLY = 0x80000022
LC_DYLD_EXPORTS_TRIE = 0x80000033
LC_REEXPORT_DYLIB = 0x8000001F


def uleb(buf, i):
    v = shift = 0
    while True:
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, i
        shift += 7


class DyldCache:
    def __init__(self, cache_dir):
        files = sorted(p for p in Path(cache_dir).glob("dyld*_shared_cache_arm64*") if p.suffix not in (".map", ".atlas", ".symbols"))
        if not files:
            raise FileNotFoundError(f"no dyld shared cache in {cache_dir}")
        self.maps = []       # (vm start, vm end, mmap, file offset)
        self._open = []
        main = None
        for f in files:
            fh = open(f, "rb")
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            self._open.append((fh, mm))
            if not mm[:7] == b"dyld_v1":
                continue
            moff, mcount = struct.unpack_from("<II", mm, 0x10)
            for k in range(mcount):
                addr, size, foff, _maxp, _initp = struct.unpack_from("<QQQII", mm, moff + 32 * k)
                self.maps.append((addr, addr + size, mm, foff))
            if f.name.endswith("arm64"):
                main = mm
        if main is None:
            raise FileNotFoundError(f"no main cache file in {cache_dir}")
        off, count = struct.unpack_from("<II", main, 0x18)
        if not count:
            off, count = struct.unpack_from("<II", main, 0x1C0)
        self.images = {}
        for k in range(count):
            addr, _mt, _ino, pathoff, _pad = struct.unpack_from("<QQQII", main, off + 32 * k)
            end = main.find(b"\0", pathoff)
            self.images[main[pathoff:end].decode()] = addr
        self._cache = {}

    def read(self, addr, n):
        for start, end, mm, foff in self.maps:
            if start <= addr < end:
                o = foff + (addr - start)
                return mm[o:o + n]
        raise ValueError(f"address {addr:#x} is in no mapping")

    def _commands(self, addr):
        hdr = self.read(addr, 32)
        magic, _cpu, _sub, _ft, ncmds, sizeofcmds, _fl, _r = struct.unpack("<IiiIIIII", hdr)
        if magic != 0xFEEDFACF:
            raise ValueError("not a 64-bit Mach-O header")
        lc = self.read(addr + 32, sizeofcmds)
        i, out = 0, []
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", lc, i)
            out.append((cmd, lc[i:i + size]))
            i += size
        return out

    def info(self, install):
        """(exported names, re-exported install paths) of one library, or None when the cache has no such library."""
        if install in self._cache:
            return self._cache[install]
        addr = self.images.get(install)
        if addr is None:
            self._cache[install] = None
            return None
        linkedit = None
        trie = None
        reexports = []
        for cmd, body in self._commands(addr):
            if cmd == LC_SEGMENT_64 and body[8:24].rstrip(b"\0") == b"__LINKEDIT":
                vmaddr, _vmsize, fileoff = struct.unpack_from("<QQQ", body, 24)
                linkedit = (vmaddr, fileoff)
            elif cmd == LC_DYLD_EXPORTS_TRIE:
                trie = struct.unpack_from("<II", body, 8)
            elif cmd == LC_DYLD_INFO_ONLY:
                eo, es = struct.unpack_from("<II", body, 40)
                if es:
                    trie = (eo, es)
            elif cmd == LC_REEXPORT_DYLIB:
                noff = struct.unpack_from("<I", body, 8)[0]
                reexports.append(body[noff:].split(b"\0", 1)[0].decode())
        names = set()
        if trie and trie[1] and linkedit:
            data = self.read(linkedit[0] + (trie[0] - linkedit[1]), trie[1])
            stack = [(0, b"")]
            seen = set()
            while stack:
                node, prefix = stack.pop()
                if node in seen or node >= len(data):
                    continue
                seen.add(node)
                tsize, i = uleb(data, node)
                if tsize:
                    names.add(prefix.decode("utf-8", "replace"))
                i += tsize
                nchild = data[i]
                i += 1
                for _ in range(nchild):
                    end = data.index(b"\0", i)
                    edge = data[i:end]
                    child, i = uleb(data, end + 1)
                    stack.append((child, prefix + edge))
        self._cache[install] = (frozenset(names), tuple(reexports))
        return self._cache[install]

    def exports(self, install, _seen=None):
        """Every name `install` gives its clients: its own exports and its re-exported libraries', or None when it is absent."""
        got = self.info(install)
        if got is None:
            return None
        names, reexports = got
        seen = _seen or set()
        seen.add(install)
        out = set(names)
        for r in reexports:
            if r not in seen:
                sub = self.exports(r, seen)
                if sub:
                    out |= sub
        return out
