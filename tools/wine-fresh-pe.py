#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Finish a rebuilt Wine module the way the shipped ones are finished, and say whether it is the committed file (tools/build-wine-fresh-dlls.sh).

  wine-fresh-pe.py finish BUILT OUT STRIP [--keep-symbols] [--pad HEX]
      copy BUILT to OUT, strip it (llvm-strip; --strip-debug when --keep-symbols, a full strip otherwise) and pad it with zeros to
      SizeOfImage + the pad (default 0x10000, as tools/build-wine-extra-dlls.sh and build/wine-pe/build-modules.sh do; ntdll 0x50000)
  wine-fresh-pe.py same COMMITTED BUILT [STRIP]
      exit 0 when BUILT is the same image as COMMITTED, 1 when it is not, 2 on an error; prints one line. With STRIP (llvm-strip) a file that still carries
      DWARF sections (the committed bthprops, cryptnet, windowscodecs, wininet, winspool) is compared without them
  wine-fresh-pe.py symbols FILE
      exit 0 when FILE still has a COFF symbol table (the committed 64-bit Wine files keep theirs after strip --strip-debug), 1 when not

"Same" is tools/patch-wine-ntdll-sync.py --same-as (byte for byte, ignoring the time stamps) plus four things that change no behaviour and that the
committed files and a rebuild differ in anyway, each shown by llvm-readobj on the 119 committed ARM64EC files (K6W, round 2026-11-15):
  - the COFF header's and the debug directory's time stamps, the export directory's time stamp and the image checksum (the --same-as rule);
  - the 2-byte hint of every by-name import: the committed files were linked against another ntdll.spec / kernelbase.spec name order, so the
    hint is another index; the loader checks the hint and falls back to a search by name (wine dlls/ntdll/loader.c find_named_export), and
    llvm-readobj --coff-imports of the two files differs in the "Symbol: name (hint)" lines only;
  - the bytes between a section's VirtualSize and its raw size (padding the linker leaves as it finds it);
  - trailing zero padding after the last byte of the image (the padding of the committed file and of a finished rebuild may differ in length).
Everything else, in the code, the data, the exports, the imports (names), the sections, is compared byte for byte.
"""
import os
import struct
import subprocess
import sys
import tempfile


class PE:
    def __init__(self, data):
        d = self.d = bytes(data)
        if d[:2] != b'MZ':
            raise ValueError('not MZ')
        self.pe = struct.unpack_from('<I', d, 0x3c)[0]
        if d[self.pe:self.pe + 4] != b'PE\0\0':
            raise ValueError('not PE')
        c = self.pe + 4
        (self.machine, self.nsec, _ts, self.symtab, self.nsyms, self.opt_size, _ch) = struct.unpack_from('<HHIIIHH', d, c)
        self.opt = o = c + 20
        self.is64 = struct.unpack_from('<H', d, o)[0] == 0x20b
        ddoff = o + (112 if self.is64 else 96)
        self.size_of_image = struct.unpack_from('<I', d, o + 56)[0]
        self.size_of_headers = struct.unpack_from('<I', d, o + 60)[0]
        ndd = struct.unpack_from('<I', d, ddoff - 4)[0]
        self.dd = [struct.unpack_from('<II', d, ddoff + 8 * i) for i in range(min(ndd, 16))]
        s = o + self.opt_size
        self.sections = []   # (name, va, vsize, rptr, rsize)
        for i in range(self.nsec):
            name = d[s + 40 * i:s + 40 * i + 8].rstrip(b'\0').decode('latin1')
            vsize, va, rsize, rptr = struct.unpack_from('<IIII', d, s + 40 * i + 8)
            self.sections.append((name, va, vsize, rptr, rsize))

    def rva2off(self, rva):
        for _n, va, vs, rp, rs in self.sections:
            if va <= rva < va + max(vs, rs):
                return rva - va + rp
        return rva if rva < self.size_of_headers else None

    def end_of_sections(self):
        return max([s[3] + s[4] for s in self.sections] + [self.size_of_headers])


def zero_stamps(p, buf):
    buf[p.pe + 8:p.pe + 12] = b'\0\0\0\0'
    buf[p.opt + 64:p.opt + 68] = b'\0\0\0\0'                      # the checksum
    if len(p.dd) > 6 and p.dd[6][0]:                                # debug directory entries
        o = p.rva2off(p.dd[6][0])
        for i in range(p.dd[6][1] // 28):
            buf[o + 28 * i + 4:o + 28 * i + 8] = b'\0\0\0\0'
    if p.dd and p.dd[0][0]:                                         # export directory
        o = p.rva2off(p.dd[0][0])
        if o is not None:
            buf[o + 4:o + 8] = b'\0\0\0\0'


def zero_hints(p, buf):
    width = 8 if p.is64 else 4
    tables = []
    if len(p.dd) > 1 and p.dd[1][0]:
        o = p.rva2off(p.dd[1][0])
        while o is not None:
            ilt, _ts, _fc, name, iat = struct.unpack_from('<IIIII', p.d, o)
            if not (ilt or name or iat):
                break
            tables.append(ilt or iat)
            o += 20
    if len(p.dd) > 13 and p.dd[13][0]:
        o = p.rva2off(p.dd[13][0])
        while o is not None:
            v = struct.unpack_from('<IIIIIIII', p.d, o)
            if not any(v):
                break
            tables.append(v[4])
            o += 32
    for t in tables:
        o = p.rva2off(t)
        while o is not None:
            e = int.from_bytes(p.d[o:o + width], 'little')
            if not e:
                break
            if not (e >> (width * 8 - 1)):
                h = p.rva2off(e & 0x7fffffff)
                if h is not None:
                    buf[h:h + 2] = b'\0\0'
            o += width


def zero_fill(p, buf):
    for _n, _va, vs, rp, rs in p.sections:
        if rs > vs:
            buf[rp + vs:rp + rs] = b'\0' * (rs - vs)


def normal(data, hints):
    p = PE(data)
    buf = bytearray(p.d)
    zero_stamps(p, buf)
    zero_fill(p, buf)
    if hints:
        zero_hints(p, buf)
    return p, bytes(buf)


def drop_debug(data, strip):
    """a committed file that still carries DWARF sections (long section names such as /4): the same file without them, as the rebuild has none"""
    if not strip or not any(s[0].startswith('/') for s in PE(data).sections):
        return data
    with tempfile.TemporaryDirectory() as t:
        tmp = os.path.join(t, 'x.dll')
        open(tmp, 'wb').write(data)
        subprocess.run([strip, '--strip-debug', tmp], check=True, capture_output=True)
        return open(tmp, 'rb').read()


def same(a, b, strip=None):
    da, db = open(a, 'rb').read(), open(b, 'rb').read()
    PE(da)
    PE(db)   # an unreadable file is an error, not a difference
    da, db = drop_debug(da, strip), drop_debug(db, strip)
    if len(da) < len(db):
        da, db = db, da
    if any(da[len(db):]):                       # the longer one continues with more than zeros
        return False, 'sizes differ and the longer file does not end in zeros'
    db = db + b'\0' * (len(da) - len(db))
    pa, na = normal(da, True)
    pb, nb = normal(db, True)
    if pa.sections != pb.sections:
        return False, 'the section tables differ'
    if na == nb:
        return True, 'same image'
    secs = []
    for n, _va, vs, rp, _rs in pa.sections:
        if na[rp:rp + vs] != nb[rp:rp + vs]:
            secs.append(n)
    return False, 'differs in ' + (' '.join(secs) if secs else 'headers or the symbol table')


def has_symbols(path):
    p = PE(open(path, 'rb').read())
    return p.symtab != 0 and p.nsyms != 0


def finish(built, out, strip, keep_symbols, pad):
    data = open(built, 'rb').read()
    with tempfile.TemporaryDirectory() as t:
        tmp = os.path.join(t, os.path.basename(out))
        open(tmp, 'wb').write(data)
        subprocess.run([strip] + (['--strip-debug'] if keep_symbols else []) + [tmp], check=True, capture_output=True)
        d = open(tmp, 'rb').read()
    p = PE(d)
    target = p.size_of_image + pad
    if len(d) < target:
        d += b'\0' * (target - len(d))
    open(out + '.tmp', 'wb').write(d)
    os.replace(out + '.tmp', out)


def main(argv):
    if len(argv) >= 4 and argv[1] == 'finish':
        pad = 0x10000
        if '--pad' in argv:
            pad = int(argv[argv.index('--pad') + 1], 16)
        finish(argv[2], argv[3], argv[4], '--keep-symbols' in argv, pad)
        return 0
    if len(argv) in (4, 5) and argv[1] == 'same':
        ok, why = same(argv[2], argv[3], argv[4] if len(argv) == 5 else None)
        print(('SAME ' if ok else 'DIFF ') + why)
        return 0 if ok else 1
    if len(argv) == 3 and argv[1] == 'symbols':
        return 0 if has_symbols(argv[2]) else 1
    print(__doc__)
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Exception as e:   # an unreadable file is an error, never "same"
        print('ERROR', e)
        sys.exit(2)
