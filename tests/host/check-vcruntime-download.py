#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app's own download of Microsoft's VC++ runtime (VCRuntimeDownload.swift, BCDCab.c).

BCD's public builds carry no Microsoft binaries; the app downloads vc_redist.x64.exe from
Microsoft and takes the fourteen x64 DLLs out of it on the device. WineProcessBridge.m links
the bundle's copy when the build has one (the owner's builds), else the downloaded one.
Checks:
  1. BCDCab.c, built on the host with zlib (with AddressSanitizer and UBSan when clang has
     them) into a small driver:
     - synthetic cabinets (stored and MSZIP folders, reserved areas, back-references that
       reach across small blocks into the previous 32 KB) unpack byte for byte, in any
       order;
     - damaged ones (truncated; bad signature, version, offsets, counts, names, folders,
       blocks, checksums, deflate data; LZX and multi-cabinet sets) are refused with the
       right error and without a crash, and random mutations never crash;
  2. with the build's download (/tmp/vcr.exe, or VCRUNTIME_INSTALLER) the C driver and the
     app's Swift extractor both give the fourteen DLLs byte for byte as the build's own
     extraction (VCRUNTIME_REFERENCE, else this checkout's app/Madeira/x86_64-vcruntime,
     /tmp/vcx, ~/madeira-bcd's); skipped, with a note, when there is no download;
  3. Swift, compiled on the host with BCDCab.c: the PE checks (AMD64, PE32+, a DLL,
     Microsoft's signature attached) and the version; the download served by a URLProtocol
     (only https to aka.ms and download.visualstudio.microsoft.com, every hop; the SHA-256
     in the final URL; a 404; an oversized answer); the model's install, refused and failed
     reinstalls (the installed copy untouched), remove, and a bundled copy winning, in a
     temporary folder;
  4. WineProcessBridge.m's madeira_vcruntime_dir, compiled on the host: the bundle's folder
     when it holds vcruntime140.dll (the very path as before), else the download, else the
     bundle's folder; one log line naming the source. The session uses it for the overlay
     and for MADEIRA_WINE_VCRT and keeps MADEIRA_MFC and both exemptions; the settings
     catalog's note for MADEIRA_WINE_VCRT is unchanged;
  5. the licence headers, the Xcode project, the bridging header and the workflow.
The Swift part needs swiftc (--require-swift makes its absence a failure).
"""
import argparse
import hashlib
import importlib.util
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-3000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def note(text):
    print("note " + text)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
fetch = wf[wf.index("- name: Fetch the VC++ runtime DLLs from Microsoft"):]
fetch = fetch[:fetch.index("\n      - name:", 10)]
WANT = re.search(r'WANT="([^"]+)"', fetch).group(1).split()
assert len(WANT) == 14, WANT
swift_src = (APP / "VCRuntimeDownload.swift").read_text()
cab_c = (APP / "BCDCab.c").read_text()
cab_h = (APP / "BCDCab.h").read_text()
bridge = (APP / "WineProcessBridge.m").read_text()

# ---------------------------------------------------------------- 1. the cabinet reader
DRIVER_C = r'''
#include "BCDCab.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static unsigned char *load(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *d = malloc(n > 0 ? (size_t)n : 1);
    if (n > 0 && fread(d, 1, (size_t)n, f) != (size_t)n) { perror("read"); exit(2); }
    fclose(f);
    *size = (size_t)n;
    return d;
}

static void save(const char *dir, const char *name, const void *d, size_t n)
{
    char path[4096];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(d, 1, n, f) != n) { perror(path); exit(2); }
    fclose(f);
}

/* An exact-size heap copy, so AddressSanitizer sees any read past the end. */
static unsigned char *exact(const unsigned char *d, size_t n)
{
    unsigned char *c = malloc(n ? n : 1);
    memcpy(c, d, n);
    return c;
}

int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    const char *cmd = argv[1];
    size_t size;
    unsigned char *loaded = load(argv[2], &size);
    unsigned char *d = exact(loaded, size);
    free(loaded);
    if (!strcmp(cmd, "list")) {                       /* list CAB */
        bcd_cab *cab;
        int rc = bcd_cab_open(d, size, &cab);
        printf("open %d\n", rc);
        if (rc) return 0;
        printf("size %u count %u\n", bcd_cab_size(cab), bcd_cab_count(cab));
        for (uint32_t i = 0; i < bcd_cab_count(cab); i++)
            printf("file %u %u %s\n", i, bcd_cab_file_size(cab, i), bcd_cab_name(cab, i));
        bcd_cab_close(cab);
        return 0;
    }
    if (!strcmp(cmd, "extract")) {                    /* extract CAB DIR [index...]: files to DIR/<index> */
        bcd_cab *cab;
        int rc = bcd_cab_open(d, size, &cab);
        printf("open %d\n", rc);
        if (rc) return 0;
        uint32_t n = bcd_cab_count(cab);
        int given = argc - 4;
        for (uint32_t k = 0; k < (given > 0 ? (uint32_t)given : n); k++) {
            uint32_t i = given > 0 ? (uint32_t)atoi(argv[4 + k]) : k;
            uint32_t fs = bcd_cab_file_size(cab, i);
            unsigned char *buf = malloc(fs ? fs : 1);
            rc = bcd_cab_extract(cab, i, buf, fs);
            printf("file %u %d\n", i, rc);
            if (!rc) { char nm[32]; snprintf(nm, sizeof nm, "%u", i); save(argv[3], nm, buf, fs); }
            free(buf);
        }
        bcd_cab_close(cab);
        return 0;
    }
    if (!strcmp(cmd, "args")) {                       /* args CAB: argument errors */
        bcd_cab *cab;
        if (bcd_cab_open(d, size, &cab)) return 1;
        unsigned char buf[16];
        uint32_t fs = bcd_cab_file_size(cab, 0);
        printf("small %d\n", fs > 1 ? bcd_cab_extract(cab, 0, buf, 1) : -99);
        printf("index %d\n", bcd_cab_extract(cab, bcd_cab_count(cab), buf, sizeof buf));
        printf("null %d\n", bcd_cab_extract(NULL, 0, buf, sizeof buf));
        printf("name %s\n", bcd_cab_name(cab, bcd_cab_count(cab)) ? "set" : "null");
        bcd_cab *other = (bcd_cab *)(void *)buf;
        printf("open-null %d %s\n", bcd_cab_open(NULL, 100, &other), other ? "set" : "null");
        other = (bcd_cab *)(void *)buf;
        printf("open-short %d %s\n", bcd_cab_open(d, 35, &other), other ? "set" : "null");
        printf("open-noout %d\n", bcd_cab_open(d, size, NULL));
        bcd_cab_close(cab);
        bcd_cab_close(NULL);
        return 0;
    }
    if (!strcmp(cmd, "scan")) {                       /* scan FILE: cabinets by header */
        size_t from = 0;
        for (;;) {
            int64_t at = bcd_cab_find(d, size, from);
            if (at < 0) break;
            bcd_cab *cab;
            int rc = bcd_cab_open(d + at, size - (size_t)at, &cab);
            printf("candidate %lld open %d", (long long)at, rc);
            if (rc) { printf("\n"); from = (size_t)at + 1; continue; }
            printf(" size %u count %u\n", bcd_cab_size(cab), bcd_cab_count(cab));
            from = (size_t)at + bcd_cab_size(cab);
            bcd_cab_close(cab);
        }
        return 0;
    }
    if (!strcmp(cmd, "vcrt")) {                       /* vcrt EXE DIR: cabinets -> inner cabinets -> *.dll_amd64 */
        size_t from = 0;
        for (;;) {
            int64_t at = bcd_cab_find(d, size, from);
            if (at < 0) break;
            bcd_cab *outer;
            if (bcd_cab_open(d + at, size - (size_t)at, &outer)) { from = (size_t)at + 1; continue; }
            from = (size_t)at + bcd_cab_size(outer);
            for (uint32_t i = 0; i < bcd_cab_count(outer); i++) {
                uint32_t fs = bcd_cab_file_size(outer, i);
                unsigned char *buf = malloc(fs ? fs : 1);
                int rc = bcd_cab_extract(outer, i, buf, fs);
                if (rc) { printf("outer %s: %d %s\n", bcd_cab_name(outer, i), rc, bcd_cab_error(rc)); return 1; }
                bcd_cab *inner;
                if (fs >= 4 && !memcmp(buf, "MSCF", 4) && bcd_cab_open(buf, fs, &inner) == 0) {
                    for (uint32_t j = 0; j < bcd_cab_count(inner); j++) {
                        const char *name = bcd_cab_name(inner, j);
                        size_t len = strlen(name);
                        if (len <= 10 || len > 250) continue;
                        char base[256];
                        for (size_t k = 0; k <= len; k++) base[k] = (char)tolower((unsigned char)name[k]);
                        if (strcmp(base + len - 10, ".dll_amd64")) continue;
                        base[len - 6] = 0;
                        uint32_t js = bcd_cab_file_size(inner, j);
                        unsigned char *b2 = malloc(js ? js : 1);
                        rc = bcd_cab_extract(inner, j, b2, js);
                        if (rc) { printf("inner %s: %d %s\n", name, rc, bcd_cab_error(rc)); return 1; }
                        char path[4096];
                        snprintf(path, sizeof path, "%s/%s", argv[3], base);
                        if (access(path, F_OK) != 0) { save(argv[3], base, b2, js); printf("wrote %s from %s\n", base, bcd_cab_name(outer, i)); }
                        free(b2);
                    }
                    bcd_cab_close(inner);
                }
                free(buf);
            }
            bcd_cab_close(outer);
        }
        return 0;
    }
    if (!strcmp(cmd, "fuzz")) {                       /* fuzz CAB SEED N: mutated copies, never a crash */
        srand((unsigned)strtoul(argv[3], NULL, 10));
        int n = atoi(argv[4]), opened = 0, refused = 0, extracted = 0;
        unsigned char *m = malloc(size ? size : 1);
        for (int it = 0; it < n; it++) {
            memcpy(m, d, size);
            size_t len = size;
            int kind = rand() % 4, flips = 1 + rand() % 8;
            for (int k = 0; k < flips && size; k++) {
                size_t at = kind == 0 ? (size_t)(rand() % 64) : kind == 1 ? (size_t)rand() % (size < 4096 ? size : 4096)
                                                                           : (size_t)rand() % size;
                if (at < len) m[at] = (unsigned char)(kind == 3 ? rand() : m[at] ^ (1u << (rand() % 8)));
            }
            if (rand() % 5 == 0 && size) len = (size_t)rand() % size;
            unsigned char *c = exact(m, len);
            bcd_cab *cab;
            if (bcd_cab_open(c, len, &cab)) { refused++; free(c); continue; }
            opened++;
            uint32_t count = bcd_cab_count(cab);
            for (uint32_t k = 0; k < count; k++) {
                uint32_t i = (k + (uint32_t)it) % count;      /* a different order each time */
                uint32_t fs = bcd_cab_file_size(cab, i);
                if (fs > (64u << 20)) continue;
                unsigned char *buf = malloc(fs ? fs : 1);
                if (bcd_cab_extract(cab, i, buf, fs) == 0) extracted++;
                free(buf);
            }
            bcd_cab_close(cab);
            free(c);
        }
        free(m);
        printf("fuzz opened %d refused %d extracted %d\n", opened, refused, extracted);
        return 0;
    }
    return 2;
}
'''

E_ARG, E_FORMAT, E_UNSUPPORTED, E_DATA = -1, -2, -3, -4


def cab_checksum(data, seed=0):
    """The specification's CFDATA checksum: little-endian words XORed, the 1-3 bytes left
    over taken first byte highest."""
    s = seed
    n = len(data) // 4
    for (w,) in struct.iter_unpack("<I", data[:n * 4]):
        s ^= w
    rest = 0
    for b in data[n * 4:]:
        rest = (rest << 8) | b
    return s ^ rest


def mszip_blocks(data, size, history=True, final=True):
    """MSZIP: each block "CK" + a raw deflate stream that may refer back into the previous
    32 KB of the folder (given to zlib as its dictionary)."""
    out = []
    for i in range(0, len(data), size):
        chunk = data[i:i + size]
        hist = data[max(0, i - 32768):i] if history else b""
        c = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=hist) if hist else zlib.compressobj(9, zlib.DEFLATED, -15)
        out.append((b"CK" + c.compress(chunk) + c.flush(zlib.Z_FINISH if final else zlib.Z_SYNC_FLUSH), len(chunk)))
    return out


def stored_blocks(data, size):
    return [(data[i:i + size], len(data[i:i + size])) for i in range(0, len(data), size)]


def build_cab(folders, files, reserve=None, flags=0, checksums=True):
    """folders: [(typeCompress, [(payload, cbUncomp)...])]; files: [(name, iFolder, offset, size)];
    reserve: (header bytes, cbCFFolder, cbCFData). Returns the bytes and where things are."""
    head = 36 + (4 + len(reserve[0]) if reserve else 0)
    folder_res, data_res = (reserve[1], reserve[2]) if reserve else (0, 0)
    files_at = head + len(folders) * (8 + folder_res)
    entries, file_at = b"", []
    for name, folder, off, size in files:
        file_at.append(files_at + len(entries))
        entries += struct.pack("<IIHHHH", size, off, folder, 0x5A52, 0x6000, 0x20) + name + b"\0"
    pos = files_at + len(entries)
    folder_bytes, data, blocks = b"", b"", []
    for typ, fblocks in folders:
        folder_bytes += struct.pack("<IHH", pos, len(fblocks), typ) + b"\xEE" * folder_res
        for payload, unpacked in fblocks:
            tail = struct.pack("<HH", len(payload), unpacked)
            blocks.append(pos)
            data += struct.pack("<I", cab_checksum(tail, cab_checksum(payload)) if checksums else 0) + tail
            data += b"\xDD" * data_res + payload
            pos += 8 + data_res + len(payload)
    header = struct.pack("<4sIIIIIBBHHHHH", b"MSCF", 0, pos, 0, files_at, 0, 3, 1, len(folders), len(files),
                         flags | (4 if reserve else 0), 0x4243, 0)
    if reserve:
        header += struct.pack("<HBB", len(reserve[0]), reserve[1], reserve[2]) + reserve[0]
    blob = bytearray(header + folder_bytes + entries + data)
    assert len(blob) == pos
    return blob, {"folders_at": head, "files_at": files_at, "file_at": file_at, "blocks": blocks,
                  "data_reserve": data_res, "folder_stride": 8 + folder_res}


def rechecksum(blob, at, data_res=0):
    """Recomputes the checksum of the CFDATA at `at` after its bytes were changed."""
    packed = struct.unpack_from("<H", blob, at + 4)[0]
    body = bytes(blob[at + 8 + data_res:at + 8 + data_res + packed])
    struct.pack_into("<I", blob, at, cab_checksum(bytes(blob[at + 4:at + 8]), cab_checksum(body)))


rnd = random.Random(20261018)
noise = bytes(rnd.getrandbits(8) for _ in range(20000))
text = b"".join(b"%05d: the folder's output, with lines that repeat across the blocks\n" % i for i in range(2500))
folder0 = noise + noise[:15000] + text[:60000] + bytes(rnd.getrandbits(8) for _ in range(9000))   # 7000-byte blocks
folder1 = bytes(rnd.getrandbits(8) for _ in range(12000))                                         # stored, 5000-byte blocks
folder2 = text * 2                                                                                # 32 KB blocks
FILES = [(b"a.bin", 0, 0, 20000), (b"sub\\b.txt", 0, 20000, len(folder0) - 20000), (b"c.dat", 1, 0, 3000),
         (b"empty", 1, 3000, 0), (b"d.dat", 1, 3000, len(folder1) - 3000), (b"big.txt", 2, 0, len(folder2))]
DATA = [folder0, folder1, folder2]
EXPECT = [DATA[f][o:o + s] for _, f, o, s in FILES]


def good_cab(reserve=None, checksums=True):
    return build_cab([(1, mszip_blocks(folder0, 7000)), (0, stored_blocks(folder1, 5000)), (1, mszip_blocks(folder2, 32768))],
                     FILES, reserve=reserve, checksums=checksums)


cc = shutil.which("clang") or shutil.which("cc")
if not cc:
    check("a C compiler is available", False)
    print("FAILED: in-app VC++ runtime download")
    sys.exit(1)

tmp_root = tempfile.TemporaryDirectory(prefix="bcd-vcrt-")
T = Path(tmp_root.name)
(T / "driver.c").write_text(DRIVER_C)
drv = T / "cabdrv"
sanitize = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-sanitize-recover=undefined"]
build = [cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-I", str(APP), str(APP / "BCDCab.c"), str(T / "driver.c"),
         "-lz", "-o", str(drv)]
b = subprocess.run(build[:1] + sanitize + build[1:], capture_output=True, text=True)
if b.returncode != 0:
    note("no sanitizers in this clang; the driver runs without them")
    b = subprocess.run(build, capture_output=True, text=True)
check("BCDCab.c builds on the host with -Wall -Wextra -Werror", b.returncode == 0, b.stderr)
if b.returncode != 0:
    print("FAILED: in-app VC++ runtime download")
    sys.exit(1)


def run(*argv, env=None):
    r = subprocess.run([str(drv)] + [str(a) for a in argv], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


def extract_cab(blob, order=None, name="cab"):
    """(open rc, {index: rc}, {index: bytes}) for `blob`, files taken in `order`."""
    path, out = T / (name + ".cab"), T / (name + ".out")
    path.write_bytes(bytes(blob))
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    code, stdout, stderr = run("extract", path, out, *(order or []))
    if code != 0 or "ERROR: AddressSanitizer" in stderr or "runtime error" in stderr:
        return "crash", {}, {"stderr": stderr[-2000:]}
    opened = int(re.search(r"^open (-?\d+)", stdout, re.M).group(1))
    rcs = {int(i): int(rc) for i, rc in re.findall(r"^file (\d+) (-?\d+)", stdout, re.M)}
    got = {int(p.name): p.read_bytes() for p in out.iterdir()}
    return opened, rcs, got


blob, info = good_cab()
opened, rcs, got = extract_cab(blob)
check("a synthetic cabinet (MSZIP with 7 KB and 32 KB blocks, a stored folder, an empty file) unpacks byte for byte",
      opened == 0 and all(rc == 0 for rc in rcs.values()) and [got.get(i) for i in range(len(FILES))] == EXPECT,
      (opened, rcs))
code, stdout, _ = run("list", T / "cab.cab")
names = re.findall(r"^file (\d+) (\d+) (.*)$", stdout, re.M)
check("names and sizes are listed as stored (a backslash kept)",
      [(n.encode(), int(s)) for _, s, n in names] == [(f[0], f[3]) for f in FILES], stdout)
for order in (["5", "4", "3", "2", "1", "0"], ["2", "0", "5", "4", "1", "3", "0"], ["1", "1", "0"]):
    opened, rcs, got = extract_cab(blob, order)
    check("files taken in the order %s (going back starts the folder over)" % ",".join(order),
          opened == 0 and all(rc == 0 for rc in rcs.values()) and all(got[int(i)] == EXPECT[int(i)] for i in order), rcs)
blob_r, info_r = good_cab(reserve=(b"\x01" * 20, 3, 5))
opened, rcs, got = extract_cab(blob_r, name="reserve")
check("reserved areas in the header (20 bytes, as signed cabinets have), the folders and the blocks are skipped",
      opened == 0 and [got.get(i) for i in range(len(FILES))] == EXPECT, (opened, rcs))
opened, rcs, got = extract_cab(good_cab(checksums=False)[0], name="nosum")
check("blocks without a checksum (0) unpack", opened == 0 and [got.get(i) for i in range(len(FILES))] == EXPECT, (opened, rcs))


def needs_history(block):
    try:
        zlib.decompressobj(-15).decompress(block[2:])
        return False
    except zlib.error:
        return True


check("the test data really needs the history: a 7 KB block refers back 20 KB, across three blocks",
      needs_history(mszip_blocks(folder0, 7000)[3][0]))


def files_last_cab(name, cut=0):
    """The file table after the data (the specification does not fix the order); `cut`
    bytes of it lie past cbCabinet."""
    payload = folder1[:100]
    block = struct.pack("<IHH", cab_checksum(struct.pack("<HH", 100, 100), cab_checksum(payload)), 100, 100) + payload
    files_at = 36 + 8 + len(block)
    entry = struct.pack("<IIHHHH", 100, 0, 0, 0x5A52, 0x6000, 0x20) + name + b"\0"
    header = struct.pack("<4sIIIIIBBHHHHH", b"MSCF", 0, files_at + len(entry) - cut, 0, files_at, 0, 3, 1, 1, 1, 0, 0, 0)
    return bytearray(header + struct.pack("<IHH", 36 + 8, 1, 0) + block + entry[:len(entry) - cut])


opened, rcs, got = extract_cab(files_last_cab(b"last.bin"), name="last")
check("a file table after the data is read", opened == 0 and got.get(0) == folder1[:100], (opened, rcs))

# Damaged cabinets: (what, the bytes, expected open result, or (0, file index, expected extract result)).
base, bi = good_cab()
cb = len(base)


def patched(fmt, at, *values, blob=None):
    b2 = bytearray(blob if blob is not None else base)
    struct.pack_into(fmt, b2, at, *values)
    return b2


first_block = bi["blocks"][0]
stored_block = bi["blocks"][len(mszip_blocks(folder0, 7000))]
bad = [
    ("20 bytes of a header", base[:20], E_FORMAT),
    ("only the 36-byte header", base[:36], E_FORMAT),
    ("one byte short of cbCabinet", base[:-1], E_FORMAT),
    ("half of it", base[:cb // 2], E_FORMAT),
    ("another signature", patched("<4s", 0, b"MSCX"), E_FORMAT),
    ("reserved1 not 0", patched("<I", 4, 1), E_FORMAT),
    ("reserved3 not 0", patched("<I", 20, 7), E_FORMAT),
    ("version 1.2", patched("<B", 24, 2), E_FORMAT),
    ("cbCabinet below the header", patched("<I", 8, 20), E_FORMAT),
    ("coffFiles past the end", patched("<I", 16, cb + 10), E_FORMAT),
    ("coffFiles inside the header", patched("<I", 16, 10), E_FORMAT),
    ("coffFiles inside the folder table", patched("<I", 16, bi["files_at"] - 4), E_FORMAT),
    ("no folders", patched("<H", 26, 0), E_FORMAT),
    ("no files", patched("<H", 28, 0), E_FORMAT),
    ("more folders than fit before the files", patched("<H", 26, 40), E_FORMAT),
    ("more files than the table holds", patched("<H", 28, 400), E_FORMAT),
    ("an unknown flag", patched("<H", 30, 0x0008), E_FORMAT),
    ("a cabinet continued from another", patched("<H", 30, 0x0001), E_UNSUPPORTED),
    ("a cabinet continued in another", patched("<H", 30, 0x0002), E_UNSUPPORTED),
    ("a reserve flag without its fields", patched("<H", 30, 0x0004), E_FORMAT),
    ("a folder's data past the end", patched("<I", bi["folders_at"], cb + 100), E_FORMAT),
    ("a folder's data at 0", patched("<I", bi["folders_at"], 0), E_FORMAT),
    ("a folder with no blocks", patched("<H", bi["folders_at"] + 4, 0), E_FORMAT),
    ("a folder with more blocks than there are", patched("<H", bi["folders_at"] + 8 * 2 + 4, 900), E_FORMAT),
    ("a block that unpacks to 0 bytes (spanning)", patched("<H", first_block + 6, 0), E_FORMAT),
    ("a block that unpacks to more than 32 KB", patched("<H", first_block + 6, 40000), E_FORMAT),
    ("a block whose data runs past the end", patched("<H", bi["blocks"][-1] + 4, 60000), E_FORMAT),
    ("a stored block that unpacks to another size", patched("<H", stored_block + 6, 4999), E_FORMAT),
    ("a file past the end of its folder", patched("<I", bi["file_at"][1], len(folder0)), E_FORMAT),
    ("a file that starts past its folder", patched("<I", bi["file_at"][2] + 4, len(folder1) + 1), E_FORMAT),
    ("a file in a folder that does not exist", patched("<H", bi["file_at"][0] + 8, 3), E_FORMAT),
    ("a file continued from another cabinet", patched("<H", bi["file_at"][0] + 8, 0xFFFD), E_UNSUPPORTED),
    ("a file continued in the next cabinet", patched("<H", bi["file_at"][0] + 8, 0xFFFE), E_UNSUPPORTED),
    ("a file name with no end", build_cab([(0, stored_blocks(folder1, 5000))], [(b"x" * 300, 0, 0, 10)])[0], E_FORMAT),
    ("an empty file name", build_cab([(0, stored_blocks(folder1, 5000))], [(b"", 0, 0, 10)])[0], E_FORMAT),
    ("a name cut by the end of the cabinet", files_last_cab(b"last.bin", cut=1), E_FORMAT),
]
r_blob, r_info = good_cab(reserve=(b"\x02" * 20, 0, 0))
bad.append(("a header reserve longer than allowed", patched("<H", 36, 60001, blob=r_blob), E_FORMAT))
bad.append(("a header reserve past the end", patched("<H", 36, 59000, blob=r_blob), E_FORMAT))
chain = [(bytes([k & 0xFF]), 1) for k in range(60000)]
shared, s_info = build_cab([(0, chain), (0, [(b"y", 1)])], [(b"a", 0, 59990, 10), (b"b", 1, 0, 1)])
opened, rcs, got = extract_cab(shared, name="chain")
check("a folder of 60000 one-byte blocks, all the cabinet has room for, is read",
      opened == 0 and got.get(0) == bytes(k & 0xFF for k in range(59990, 60000)) and got.get(1) == b"y", (opened, rcs))
bad.append(("two folders sharing one chain of 60000 blocks (more than the cabinet holds)",
            patched("<IH", s_info["folders_at"] + 8, s_info["blocks"][0], 60000, blob=shared), E_FORMAT))

lzx = patched("<H", bi["folders_at"] + 6, 3)
lzx_window = patched("<H", bi["folders_at"] + 6, 0x1503)
quantum = patched("<H", bi["folders_at"] + 6, 2)
bad_sum = patched("<I", first_block, struct.unpack_from("<I", base, first_block)[0] ^ 0x10)
bad_ck = bytearray(base)
bad_ck[first_block + 9] = ord("X")
rechecksum(bad_ck, first_block)
garbage = bytearray(base)
for k in range(first_block + 10, first_block + 40):
    garbage[k] = 0xFF
rechecksum(garbage, first_block)
longer = patched("<H", first_block + 6, 7001)            # declares one byte more than it inflates to
rechecksum(longer, first_block)
not_final = build_cab([(1, mszip_blocks(folder0, 7000, final=False))], FILES[:2])[0]
no_history = build_cab([(1, [mszip_blocks(folder0, 7000)[3]])], [(b"far", 0, 0, 7000)])[0]
bad += [
    ("an LZX folder: listed, not unpacked", lzx, (0, 0, E_UNSUPPORTED)),
    ("an LZX folder with its window bits", lzx_window, (0, 1, E_UNSUPPORTED)),
    ("a Quantum folder", quantum, (0, 0, E_UNSUPPORTED)),
    ("a block with a wrong checksum", bad_sum, (0, 0, E_DATA)),
    ("an MSZIP block without its CK", bad_ck, (0, 0, E_DATA)),
    ("an MSZIP block of bad deflate data", garbage, (0, 0, E_DATA)),
    ("an MSZIP block shorter than it declares", longer, (0, 0, E_DATA)),
    ("MSZIP blocks with no final deflate block", not_final, (0, 0, E_DATA)),
    ("a back-reference before the folder's start", no_history, (0, 0, E_DATA)),
]
for what, blob_bad, want in bad:
    opened, rcs, got = extract_cab(blob_bad, name="bad")
    if isinstance(want, tuple):
        result = opened == 0 and rcs.get(want[1]) == want[2]
        if want[0] == 0 and what.startswith("an LZX"):
            result = result and all(rc == 0 for i, rc in rcs.items() if FILES[i][1] != 0)   # the stored folder still unpacks
    else:
        result = opened == want
    check("refused: %s" % what, opened != "crash" and result, (opened, rcs, got.get("stderr", "")))

code, stdout, stderr = run("args", T / "cab.cab")
check("bad arguments: a short buffer, a bad index, no handle, no data, a 35-byte cabinet",
      code == 0 and "small -1" in stdout and "index -1" in stdout and "null -1" in stdout and "name null" in stdout
      and "open-null -1 null" in stdout and "open-short -2 null" in stdout and "open-noout -1" in stdout, stdout + stderr)

decoy = bytearray(b"MSCF" + bytes(40))                     # a signature with nothing behind it
decoy += b"MSCF" + struct.pack("<IIIIIBBHHHHH", 0, 400, 0, 50, 0, 3, 1, 1, 1, 0, 0, 0)[0:] + bytes(400)
lead = bytes(rnd.getrandbits(8) for _ in range(3000)).replace(b"MSCF", b"MSCX")
scan_blob = lead + bytes(decoy) + bytes(base) + b"MSCF tail"
(T / "scan.bin").write_bytes(scan_blob)
code, stdout, _ = run("scan", T / "scan.bin")
real_at = len(lead) + len(decoy)
check("bcd_cab_find skips a bare signature; a plausible header that does not open is refused; the cabinet behind them is found",
      code == 0 and re.search(r"^candidate %d open 0 size %d count %d$" % (real_at, cb, len(FILES)), stdout, re.M) is not None
      and re.search(r"^candidate %d " % len(lead), stdout, re.M) is None
      and re.search(r"^candidate %d open -2$" % (len(lead) + 44), stdout, re.M) is not None, stdout)

fuzz_sets = [("synthetic", T / "reserve.cab", 500)]
(T / "reserve.cab").write_bytes(bytes(blob_r))
installer = Path(os.environ.get("VCRUNTIME_INSTALLER", "/tmp/vcr.exe"))
have_installer = installer.is_file() and installer.stat().st_size > 1_000_000
if have_installer:
    exe = installer.read_bytes()
    code, stdout, _ = run("scan", installer)
    cabs = [(int(a), int(s)) for a, s in re.findall(r"^candidate (\d+) open 0 size (\d+)", stdout, re.M)]
    if cabs:
        a, s = min(cabs, key=lambda c: c[1])                 # the small one (the setup's own UI files)
        (T / "ux.cab").write_bytes(exe[a:a + s])
        fuzz_sets.append(("the installer's first cabinet", T / "ux.cab", 150))
for what, path, n in fuzz_sets:
    code, stdout, stderr = run("fuzz", path, 7, n)
    check("%d random mutations of %s: refused or unpacked, never a crash" % (n, what),
          code == 0 and "fuzz opened" in stdout and "AddressSanitizer" not in stderr and "runtime error" not in stderr,
          stdout + stderr)


# ---------------------------------------------------------------- 2. the real installer
def file_version(data):
    key = "VS_VERSION_INFO".encode("utf-16-le")
    i = data.find(key)
    while i >= 0:
        fixed = i - 6 + 40
        if data[fixed:fixed + 4] == b"\xbd\x04\xef\xfe":
            ms, ls = struct.unpack_from("<II", data, fixed + 8)
            return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        i = data.find(key, i + 1)
    return None


def reference_files():
    """The build's own extraction: name -> path, and where it was found."""
    places = [Path(p) for p in [os.environ.get("VCRUNTIME_REFERENCE")] if p]
    places += [APP / "x86_64-vcruntime", Path("/tmp/vcx"), Path.home() / "madeira-bcd/app/Madeira/x86_64-vcruntime"]
    for place in places:
        if not place.is_dir():
            continue
        found = {n: place / n for n in WANT if (place / n).is_file()}
        if not found:                                         # cabextract's tree: <name>_amd64 files
            for p in sorted(place.rglob("*_amd64")):
                n = p.name[:-len("_amd64")].lower()
                if n in WANT and n not in found:
                    found[n] = p
        if found:
            return place, found
    return None, {}


c_out = T / "c-dlls"
if have_installer:
    c_out.mkdir()
    code, stdout, stderr = run("vcrt", installer, c_out)
    got_names = sorted(p.name for p in c_out.iterdir())
    check("the C reader takes every *.dll_amd64 out of %s (no _arm64 file)" % installer,
          code == 0 and set(WANT) <= set(got_names) and not any("arm64" in n for n in got_names), stdout + stderr)
    place, ref = reference_files()
    if not ref:
        note("no reference extraction found: the DLLs are not compared byte for byte")
    else:
        ref_version = file_version(ref["vcruntime140.dll"].read_bytes()) if "vcruntime140.dll" in ref else None
        our_version = file_version((c_out / "vcruntime140.dll").read_bytes()) if (c_out / "vcruntime140.dll").is_file() else None
        if ref_version != our_version:
            note("the reference in %s is version %s, the download %s: not compared" % (place, ref_version, our_version))
        else:
            missing = [n for n in WANT if n not in ref]
            same = [n for n in WANT if n in ref and (c_out / n).is_file() and (c_out / n).read_bytes() == ref[n].read_bytes()]
            check("the C reader's %d DLLs are byte for byte the build's own (%s, version %s)%s"
                  % (len(same), place, our_version, ("; the reference lacks " + ", ".join(missing)) if missing else ""),
                  len(same) == len(WANT) - len(missing) and len(same) > 0)
else:
    note("%s is not here (the build's download): the real installer is not unpacked" % installer)

# ---------------------------------------------------------------- 3. Swift
STUBS = r'''
import Foundation
final class LogStore {
    static let shared = LogStore()
    func log(_ message: String) { FileHandle.standardError.write(Data((message + "\n").utf8)) }
}
func wine_process_is_running() -> Int32 { ProcessInfo.processInfo.environment["FAKE_SESSION"] == "1" ? 1 : 0 }
'''

MAIN = r'''
import Foundation
import CryptoKit

final class Mock: URLProtocol {
    static let lock = NSLock()
    nonisolated(unsafe) static var seen: [String] = []
    nonisolated(unsafe) static var body = Data()
    nonisolated(unsafe) static var bodySHA = ""

    static func load(_ path: String) {
        body = (try? Data(contentsOf: URL(fileURLWithPath: path))) ?? Data()
        bodySHA = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
    }
    static func configuration() -> URLSessionConfiguration {
        let c = VCRuntimeDownloader.configuration()
        c.protocolClasses = [Mock.self]
        return c
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    private func redirect(to target: String) {
        let response = HTTPURLResponse(url: request.url!, statusCode: 301, httpVersion: "HTTP/1.1", headerFields: ["Location": target])!
        client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: URL(string: target)!), redirectResponse: response)
        client?.urlProtocolDidFinishLoading(self)
    }
    private func serve(_ status: Int, _ data: Data, length: Int? = nil) {
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Length": String(length ?? data.count)])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func startLoading() {
        let url = request.url!
        Mock.lock.lock(); Mock.seen.append(VCRuntimeSource.origin(url)); Mock.lock.unlock()
        let ms = "https://download.visualstudio.microsoft.com/download/pr/0d0e/"
        switch (url.host ?? "", url.lastPathComponent) {
        case ("aka.ms", "ok"): redirect(to: ms + Mock.bodySHA + "/VC_redist.x64.exe")
        case ("aka.ms", "nohash"): redirect(to: ms + "VC_redist.x64.exe")
        case ("aka.ms", "badhash"): redirect(to: ms + String(repeating: "0", count: 64) + "/VC_redist.x64.exe")
        case ("aka.ms", "evil"): redirect(to: "https://evil.example.net/VC_redist.x64.exe")
        case ("aka.ms", "lookalike"): redirect(to: "https://download.visualstudio.microsoft.com.evil.example.net/VC_redist.x64.exe")
        case ("aka.ms", "http"): redirect(to: "http://download.visualstudio.microsoft.com/download/pr/0d0e/VC_redist.x64.exe")
        case ("aka.ms", "port"): redirect(to: "https://download.visualstudio.microsoft.com:8443/download/pr/0d0e/VC_redist.x64.exe")
        case ("aka.ms", "missing"): redirect(to: ms + "missing")
        case ("aka.ms", "huge"): redirect(to: ms + "huge")
        case ("aka.ms", "second-hop"): redirect(to: ms + "second-hop")
        case ("download.visualstudio.microsoft.com", "missing"): serve(404, Data("not here".utf8))
        case ("download.visualstudio.microsoft.com", "huge"): serve(200, Data(count: 1 << 20), length: 200 << 20)
        case ("download.visualstudio.microsoft.com", "second-hop"): redirect(to: "https://evil.example.net/x.exe")
        case ("download.visualstudio.microsoft.com", _): serve(200, Mock.body)
        default: serve(200, Data("not Microsoft".utf8))
        }
    }
}

final class Box: @unchecked Sendable {
    var progress: [Double] = []
    var notes = 0
}

func describe(_ result: Result<String, Error>) -> String {
    switch result {
    case .success(let version): return "result ok \(version)"
    case .failure(let error): return "result error " + ((error as? VCRuntimeError).map { String(describing: $0) } ?? "\(error)")
    }
}

@MainActor func state(_ rt: VCRuntime) {
    print("status \(rt.status)")
    print("source \(rt.source.rawValue)")
    print("available \(rt.isAvailable ? 1 : 0)")
    print("busy \(rt.isBusy ? 1 : 0)")
    print("description \(rt.sourceDescription)")
    print("lastError \(rt.lastError ?? "-")")
    let excluded = (try? URL(fileURLWithPath: rt.root.path).resourceValues(forKeys: [.isExcludedFromBackupKey]))?.isExcludedFromBackup
    print("excluded \(excluded.map { $0 ? "1" : "0" } ?? "-")")
}

@main struct Driver {
    @MainActor static func main() async {
        let a = Array(CommandLine.arguments.dropFirst())
        switch a.first ?? "" {
        case "extract":
            do {
                var steps: [Double] = []
                try VCRuntimeInstaller.extract(installer: URL(fileURLWithPath: a[1]), into: URL(fileURLWithPath: a[2])) { steps.append($0) }
                let rising = zip(steps, steps.dropFirst()).allSatisfy { $0 <= $1 }
                print("ok progress=\(steps.count) rising=\(rising ? 1 : 0) last=\(steps.last ?? -1)")
            } catch { print("error \(error.localizedDescription)") }
        case "pe":
            for path in a.dropFirst() {
                let name = (path as NSString).lastPathComponent
                if let pe = PEImage((try? Data(contentsOf: URL(fileURLWithPath: path))) ?? Data()) {
                    print("\(name) machine=0x\(String(pe.machine, radix: 16)) pe32plus=\(pe.isPE32Plus ? 1 : 0) dll=\(pe.isDLL ? 1 : 0) signed=\(pe.hasCertificate ? 1 : 0) version=\(pe.fileVersion ?? "none")")
                } else {
                    print("\(name) not-pe")
                }
            }
        case "check":
            do {
                try VCRuntimeInstaller.check((try? Data(contentsOf: URL(fileURLWithPath: a[1]))) ?? Data(), name: a[2])
                print("ok")
            } catch { print("refused \(error.localizedDescription)") }
        case "allowed":
            for text in a.dropFirst() { print((URL(string: text).map(VCRuntimeSource.isAllowed) ?? false) ? "yes" : "no") }
        case "sha":
            print(URL(string: a[1]).flatMap(VCRuntimeSource.expectedSHA256) ?? "none")
        case "download":
            Mock.load(a[2])
            let out = URL(fileURLWithPath: a[3])
            let result: Result<URL, Error> = await withCheckedContinuation { done in
                let d = VCRuntimeDownloader(url: URL(string: "https://aka.ms/test/\(a[1])")!, configuration: Mock.configuration(),
                                            destination: out, progress: { _, _ in }, finished: { done.resume(returning: $0) })
                d.start()
            }
            switch result {
            case .success(let url): print("ok \(url.absoluteString)")
            case .failure(let error): print("error " + ((error as? VCRuntimeError).map { String(describing: $0) } ?? "\(error)"))
            }
            print("file \(FileManager.default.fileExists(atPath: out.path) ? 1 : 0)")
            print("seen " + Mock.seen.joined(separator: " "))
        case "model":
            let rt = VCRuntime(root: URL(fileURLWithPath: a[1], isDirectory: true), bundledFolder: URL(fileURLWithPath: a[2], isDirectory: true))
            let box = Box()
            let token = NotificationCenter.default.addObserver(forName: VCRuntime.didChangeNotification, object: rt, queue: nil) { _ in box.notes += 1 }
            switch a[3] {
            case "install-file":
                let result = await withCheckedContinuation { done in
                    rt.install(fromInstaller: URL(fileURLWithPath: a[4]), progress: { box.progress.append($0) }) { done.resume(returning: $0) }
                }
                print(describe(result))
            case "install-net":
                Mock.load(a[5])
                rt.downloadURL = URL(string: "https://aka.ms/test/\(a[4])")!
                rt.downloadConfiguration = Mock.configuration()
                let result = await withCheckedContinuation { done in
                    rt.install(progress: { box.progress.append($0) }) { done.resume(returning: $0) }
                }
                print(describe(result))
                print("seen " + Mock.seen.joined(separator: " "))
            case "remove":
                print("result \(rt.remove() ? "removed" : "refused")")
            default:
                break
            }
            let p = box.progress
            print("progress \(p.count) rising=\(zip(p, p.dropFirst()).allSatisfy { $0 <= $1 } ? 1 : 0) last=\(p.last ?? -1)")
            print("notifications \(box.notes)")
            NotificationCenter.default.removeObserver(token)
            state(rt)
        default:
            exit(2)
        }
    }
}
'''

swiftc = shutil.which("swiftc")
swift_ok = False
if not swiftc:
    check("Swift compiler available", not args.require_swift)
else:
    (T / "stubs.swift").write_text(STUBS)
    # WineMonoInstaller.sha256 (the shared file hash): the installer part of WineMono.swift, as check-wine-mono.py compiles it
    wm = (APP / "WineMono.swift").read_text()
    (T / "winemono.swift").write_text(wm[wm.index("import Foundation"):wm.index("// MARK: - Model")])
    (T / "main.swift").write_text(MAIN)
    obj = T / "BCDCab.o"
    b1 = subprocess.run([cc, "-c", "-O2", "-I", str(APP), str(APP / "BCDCab.c"), "-o", str(obj)], capture_output=True, text=True)
    b2 = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone", "-import-objc-header", str(APP / "BCDCab.h"),
                         str(APP / "VCRuntimeDownload.swift"), str(T / "winemono.swift"), str(T / "stubs.swift"), str(T / "main.swift"), str(obj), "-lz",
                         "-o", str(T / "swiftdrv")], capture_output=True, text=True) if b1.returncode == 0 else b1
    swift_ok = b2.returncode == 0
    check("VCRuntimeDownload.swift builds on the host with BCDCab.c (log and session state stubbed)", swift_ok, b2.stderr)


def swift(*argv, env=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([str(T / "swiftdrv")] + [str(x) for x in argv], capture_output=True, text=True, env=e)
    return r.stdout + ("" if r.returncode == 0 else "\nexit %d\n%s" % (r.returncode, r.stderr[-2000:]))


if swift_ok:
    out = swift("allowed", "https://aka.ms/vs/17/release/vc_redist.x64.exe",
                "https://download.visualstudio.microsoft.com/download/pr/x/y/VC_redist.x64.exe",
                "https://DOWNLOAD.visualstudio.microsoft.com:443/x", "http://download.visualstudio.microsoft.com/x",
                "https://download.visualstudio.microsoft.com:8443/x", "https://user:pw@aka.ms/x", "https://evil.example.net/x",
                "https://download.visualstudio.microsoft.com.evil.example.net/x", "https://microsoft.com/x",
                "https://download.microsoft.com/x", "ftp://aka.ms/x").split()
    check("allowed: https to aka.ms and download.visualstudio.microsoft.com on 443, nothing else",
          out == ["yes", "yes", "yes", "no", "no", "no", "no", "no", "no", "no", "no"], out)
    sha = "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b"
    check("the SHA-256 is read from Microsoft's download URL, and only a 64-hex-digit part counts",
          swift("sha", "https://download.visualstudio.microsoft.com/download/pr/990c720a-46ce-4dc7-87b9-c2eb615d34b9/"
                + sha.upper() + "/VC_redist.x64.exe").strip() == sha
          and swift("sha", "https://aka.ms/vs/17/release/vc_redist.x64.exe").strip() == "none")

    body = T / "body.bin"
    body.write_bytes(bytes(rnd.getrandbits(8) for _ in range(70000)))
    for scenario, want, hosts in [
            ("ok", r"^ok https://download\.visualstudio\.microsoft\.com/download/pr/0d0e/[0-9a-f]{64}/VC_redist\.x64\.exe$", "https://aka.ms https://download.visualstudio.microsoft.com"),
            ("evil", r'^error redirected\("https://evil\.example\.net"\)$', "https://aka.ms"),
            ("lookalike", r'^error redirected\("https://download\.visualstudio\.microsoft\.com\.evil\.example\.net"\)$', "https://aka.ms"),
            ("http", r'^error redirected\("http://download\.visualstudio\.microsoft\.com"\)$', "https://aka.ms"),
            ("port", r'^error redirected\("https://download\.visualstudio\.microsoft\.com:8443"\)$', "https://aka.ms"),
            ("second-hop", r'^error redirected\("https://evil\.example\.net"\)$', "https://aka.ms https://download.visualstudio.microsoft.com"),
            ("missing", r"^error http\(404\)$", "https://aka.ms https://download.visualstudio.microsoft.com"),
            ("huge", r"^error tooLarge$", "https://aka.ms https://download.visualstudio.microsoft.com")]:
        dest = T / ("download-" + scenario)
        out = swift("download", scenario, body, dest)
        lines = out.splitlines()
        good = bool(lines) and re.match(want, lines[0]) is not None and ("seen " + hosts) in lines
        good = good and (("file 1" in lines and dest.read_bytes() == body.read_bytes()) if scenario == "ok" else "file 0" in lines)
        check("download, %s: %s" % (scenario, "the file arrives from Microsoft's server" if scenario == "ok"
                                    else "refused before anything else is fetched, nothing kept"), good, out)

    ver = None
    if have_installer:
        s_out = T / "swift-dlls"
        out = swift("extract", installer, s_out)
        names = sorted(p.name for p in s_out.iterdir()) if s_out.is_dir() else []
        check("the app's extractor writes exactly the fourteen DLLs, nothing else (progress rising to 1)",
              out.startswith("ok ") and "rising=1" in out and "last=1.0" in out and names == sorted(WANT), out + " " + str(names))
        check("the app's extractor gives the same bytes as the C driver (so the build's, where compared above)",
              names and all((s_out / n).read_bytes() == (c_out / n).read_bytes() for n in names))
        ver = file_version((s_out / "vcruntime140.dll").read_bytes())
        pe = swift("pe", *[s_out / n for n in WANT])
        check("each DLL: AMD64, PE32+, a DLL, Microsoft's signature attached; version %s (as the version resource says)" % ver,
              pe.count(" machine=0x8664 pe32plus=1 dll=1 signed=1 version=%s\n" % ver) == 14, pe)

        dll = (s_out / "vcruntime140.dll").read_bytes()
        pe_off = struct.unpack_from("<I", dll, 0x3C)[0]
        cert_off = struct.unpack_from("<I", dll, pe_off + 24 + 112 + 4 * 8)[0]
        variants = {
            "arm64": patched("<H", pe_off + 4, 0xAA64, blob=dll),
            "not-a-dll": patched("<H", pe_off + 22, struct.unpack_from("<H", dll, pe_off + 22)[0] & ~0x2000, blob=dll),
            "pe32": patched("<H", pe_off + 24, 0x10B, blob=dll),
            "cut": dll[:-100],
            "cert-gone": patched("<II", pe_off + 24 + 112 + 32, 0, 0, blob=dll),
            "cert-revision": patched("<H", cert_off + 4, 0x0100, blob=dll),
            "noise": bytes(rnd.getrandbits(8) for _ in range(4096)),
        }
        want_msg = {"arm64": "not an x64 DLL", "not-a-dll": "not an x64 DLL", "pe32": "not an x64 DLL", "cut": "lost Microsoft's signature",
                    "cert-gone": "lost Microsoft's signature", "cert-revision": "lost Microsoft's signature", "noise": "not a Windows DLL"}
        out = swift("check", s_out / "vcruntime140.dll", "vcruntime140.dll")
        check("the real vcruntime140.dll passes the check", out.strip() == "ok", out)
        for name, data in variants.items():
            path = T / ("variant-" + name)
            path.write_bytes(bytes(data))
            out = swift("check", path, "vcruntime140.dll").strip()
            check("a DLL refused: %s" % name, out.startswith("refused ") and want_msg[name] in out, out)

    root, bundled = T / "model/VCRuntime", T / "model/bundle/x86_64-vcruntime"
    bundled.mkdir(parents=True)
    (bundled / ".gitkeep").write_text("placeholder\n")

    def model(step, *rest, env=None):
        out = swift("model", root, bundled, step, *rest, env=env)
        return out, dict(re.findall(r"^(\w+) (.*)$", out, re.M))

    def listing():
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def installed():
        folder = root / "x86_64"
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()} if folder.is_dir() else {}

    out, s = model("state")
    check("model, nothing installed: not installed, nothing for sessions",
          s.get("status") == "notInstalled" and s.get("source") == "none" and s.get("available") == "0"
          and s.get("description") == "Not installed", out)
    noise_exe = T / "noise.exe"
    noise_exe.write_bytes(b"MZ" + bytes(rnd.getrandbits(8) for _ in range(5000)))
    out, s = model("install-file", noise_exe)
    check("model, a file that is not the installer: refused, nothing left behind",
          s.get("result", "").startswith("error extraction(") and "installer did not hold concrt140.dll" in s.get("result", "")
          and s.get("status") == "notInstalled"
          and s.get("lastError", "-") != "-" and listing() == [], out)
    out, s = model("install-net", "evil", body)
    check("model, a redirect away from Microsoft: refused, nothing installed",
          s.get("result", "").startswith("error redirected") and s.get("status") == "notInstalled" and "evil" not in s.get("seen", "")
          and listing() == [], out)
    if have_installer:
        out, s = model("install-file", installer)
        files = installed()
        check("model, install: version %s, the downloaded copy used, the fourteen DLLs, no leftovers, out of backups" % ver,
              s.get("result") == "ok %s" % ver and s.get("status") == 'installed(version: "%s")' % ver and s.get("source") == "downloaded"
              and s.get("available") == "1" and s.get("description") == "Downloaded from Microsoft (version %s)" % ver
              and s.get("lastError") == "-" and s.get("excluded") == "1" and sorted(files) == sorted(WANT)
              and listing() == ["x86_64"]
              and all(files[n] == hashlib.sha256((c_out / n).read_bytes()).hexdigest() for n in WANT), out)
        check("model, install: progress rises to 1, and each change is announced",
              "rising=1" in s.get("progress", "") and s.get("progress", "").endswith("last=1.0") and int(s.get("notifications", "0")) >= 3, out)
        short = T / "short.exe"
        short.write_bytes(installer.read_bytes()[:6_000_000])
        out, s = model("install-file", short)
        check("model, a reinstall from a damaged installer fails and leaves the installed copy as it was",
              s.get("result", "").startswith("error extraction") and s.get("status") == 'installed(version: "%s")' % ver
              and installed() == files and listing() == ["x86_64"] and s.get("lastError", "-") != "-", out)
        out, s = model("install-file", installer, env={"FAKE_SESSION": "1"})
        check("model, no reinstall while a game runs", s.get("result") == "error sessionRunning" and installed() == files, out)
        out, s = model("remove", env={"FAKE_SESSION": "1"})
        check("model, no removal while a game runs", s.get("result") == "refused" and installed() == files
              and "Close the running game" in s.get("lastError", ""), out)
        out, s = model("install-net", "ok", installer)
        check("model, a reinstall through the download (redirect, SHA-256 from the URL): swapped in, same files, the download deleted",
              s.get("result") == "ok %s" % ver and installed() == files and listing() == ["x86_64"]
              and s.get("seen") == "https://aka.ms https://download.visualstudio.microsoft.com"
              and "rising=1" in s.get("progress", "") and s.get("progress", "").endswith("last=1.0"), out)
        out, s = model("install-net", "badhash", installer)
        check("model, a download whose SHA-256 is not the one its URL names: refused, the installed copy kept",
              s.get("result") == "error checksumMismatch" and installed() == files and listing() == ["x86_64"], out)
        (bundled / "vcruntime140.dll").write_bytes((c_out / "vcruntime140.dll").read_bytes())
        out, s = model("state")
        check("model, a build that bundles them: the bundled copy wins", s.get("source") == "bundled"
              and s.get("description") == "Included in this build" and s.get("status") == 'installed(version: "%s")' % ver, out)
        (bundled / "vcruntime140.dll").unlink()
        out, s = model("remove")
        check("model, remove: gone, nothing for sessions", s.get("result") == "removed" and s.get("status") == "notInstalled"
              and s.get("source") == "none" and not (root / "x86_64").exists() and listing() == [], out)
        out, s = model("install-net", "nohash", installer)
        check("model, a first install through the download (no SHA-256 in the URL)", s.get("result") == "ok %s" % ver
              and installed() == files, out)
        (root / "x86_64").rename(root / "x86_64.old")
        (root / "x86_64.partial").mkdir()
        (root / "x86_64.partial/concrt140.dll").write_bytes(b"MZ")
        (root / "vc_redist.x64.exe.download").write_bytes(b"part")
        out, s = model("state")
        check("model, the app killed in the middle of a two-step swap: the old copy is put back, the rest cleared",
              s.get("status") == 'installed(version: "%s")' % ver and installed() == files and listing() == ["x86_64"], out)
        (root / "x86_64").rename(root / "x86_64.removed")
        out, s = model("state")
        check("model, the app killed in the middle of a removal: the copy is gone",
              s.get("status") == "notInstalled" and listing() == [], out)
    else:
        note("no installer: the model's install, reinstall and remove are not run")

# ---------------------------------------------------------------- 4. the bridge
fn = re.search(r"static NSString \*madeira_vcruntime_dir\(NSString \*bundle, NSString \*support\)\n\{\n.*?\n\}\n", bridge, re.S)
check("WineProcessBridge.m has madeira_vcruntime_dir", fn is not None)
if fn:
    harness = T / "bridge.m"
    harness.write_text("#import <Foundation/Foundation.h>\n#include <unistd.h>\n#include <stdio.h>\n" + fn.group(0) + r'''
int main(int argc, char **argv)
{
    if (argc < 3)
        return 2;
    @autoreleasepool {
        NSString *bundle = [NSString stringWithUTF8String:argv[1]];
        NSString *support = argv[2][0] ? [NSString stringWithUTF8String:argv[2]] : nil;
        NSString *dir = madeira_vcruntime_dir(bundle, support);
        NSString *before = [bundle stringByAppendingPathComponent:@"x86_64-vcruntime"];
        printf("%s\n%s\n", dir.UTF8String, [dir isEqualToString:before] ? "as-before" : "other");
    }
    return 0;
}
''')
    hb = subprocess.run([cc, "-x", "objective-c", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-framework", "Foundation",
                         str(harness), "-o", str(T / "bridge")], capture_output=True, text=True)
    check("madeira_vcruntime_dir builds on the host", hb.returncode == 0, hb.stderr)
    if hb.returncode == 0:
        def scenario(bundle_dll, download_dll, with_bundle_dir=True, with_support=True, download_other=False):
            base_dir = Path(tempfile.mkdtemp(dir=T))
            bundle, support = base_dir / "BCD.app", base_dir / "Application Support"
            if with_bundle_dir:
                (bundle / "x86_64-vcruntime").mkdir(parents=True)
                (bundle / "x86_64-vcruntime/.gitkeep").write_text("x")
                if bundle_dll:
                    (bundle / "x86_64-vcruntime/vcruntime140.dll").write_bytes(b"MZ")
            else:
                bundle.mkdir(parents=True)
            if download_dll or download_other:
                (support / "VCRuntime/x86_64").mkdir(parents=True)
                (support / "VCRuntime/x86_64" / ("vcruntime140.dll" if download_dll else "concrt140.dll")).write_bytes(b"MZ")
            r = subprocess.run([str(T / "bridge"), str(bundle), str(support) if with_support else ""], capture_output=True, text=True)
            out = r.stdout.splitlines()
            return out, r.stderr.splitlines(), bundle, support

        for what, kw, want_source, want_dir in [
                ("the bundle holds them (the owner's builds): the bundle's, the very path as before", dict(bundle_dll=True, download_dll=True), "bundled", "bundle"),
                ("only the download holds them: the download", dict(bundle_dll=False, download_dll=True), "downloaded", "support"),
                ("no bundle folder at all, a download: the download", dict(bundle_dll=False, download_dll=True, with_bundle_dir=False), "downloaded", "support"),
                ("neither: the bundle's folder as before", dict(bundle_dll=False, download_dll=False), "none", "bundle"),
                ("a download folder without vcruntime140.dll: not used", dict(bundle_dll=False, download_dll=False, download_other=True), "none", "bundle"),
                ("no Application Support path: the bundle's folder", dict(bundle_dll=False, download_dll=True, with_support=False), "none", "bundle")]:
            out, err, bundle, support = scenario(**kw)
            want_path = str(bundle / "x86_64-vcruntime") if want_dir == "bundle" else str(support / "VCRuntime/x86_64")
            check("session: %s" % what, out[:1] == [want_path] and err == ["[WineProc] VC++ runtime: %s" % want_source]
                  and (out[1:2] == ["as-before"]) == (want_dir == "bundle"), (out, err))

blk = bridge[bridge.index('const char *wineVcrt = getenv("MADEIRA_WINE_VCRT");'):bridge.index('LOG("Symlinked %d MS VC++ Runtime DLLs')]
check("the session takes the folder once, right after MADEIRA_WINE_VCRT, for x64 (ARM64EC) sessions only",
      re.match(r'const char \*wineVcrt = getenv\("MADEIRA_WINE_VCRT"\);\n\s+NSString \*vcrtDir = use_arm64ec \? '
               r'madeira_vcruntime_dir\(bundlePath,[^\n]*\n\s+NSSearchPathForDirectoriesInDomains\(NSApplicationSupportDirectory, '
               r'NSUserDomainMask, YES\)\.firstObject\) : nil;\n\s+NSMutableDictionary<NSString \*, NSString \*> \*vcrtHome = '
               r'\[NSMutableDictionary dictionary\];[^\n]*\n\s+NSUInteger vcrtFromDocs = 0;\n\s+NSArray<NSString \*> \*vcrtDlls = '
               r'use_arm64ec \? madeira_vcruntime_dlls\(fm, vcrtDir,\n(?:.*\n)*?'
               r'\s+if \(use_arm64ec && wineVcrt && wineVcrt\[0\] == \'1\'\) \{', blk) is not None)
check("MADEIRA_WINE_VCRT=1 keeps the builtins for that folder's names; the overlay links from it",
      "for (NSString *dll in vcrtDlls) {" in blk and "madeira_vcruntime_dlls(fm, vcrtDir," in blk and "vcrtHome, &vcrtFromDocs)" in blk
      and "NSString *src = vcrtHome[[dll lowercaseString]];" in blk
      and "createSymbolicLinkAtPath:dst withDestinationPath:src" in blk)
check("the bundle's x86_64-vcruntime is named only in madeira_vcruntime_dir",
      bridge.count('@"x86_64-vcruntime"') == 1 and '@"x86_64-vcruntime"' in (fn.group(0) if fn else ""))
check("MADEIRA_MFC and both exemptions (vcruntime140.dll, msvcp140.dll) are as they were",
      "BOOL mfcOff = mfcEnv && mfcEnv[0] == '0';" in blk and 'if ([[dll lowercaseString] isEqualToString:@"vcruntime140.dll"]) {' in blk
      and 'if ([[dll lowercaseString] isEqualToString:@"msvcp140.dll"]) {' in blk and blk.count("vcrtSkipped++;") == 3)
sys.path.insert(0, str(R / "build/tools"))                  # the generator imports its neighbours
gen_spec = importlib.util.spec_from_file_location("gen_catalog", R / "build/tools/gen-config-catalog.py")
gen = importlib.util.module_from_spec(gen_spec)
gen_spec.loader.exec_module(gen)
lines = bridge.split("\n")
site = next(i for i, l in enumerate(lines) if 'getenv("MADEIRA_WINE_VCRT")' in l)


def catalog_note(text):
    m = re.search(r'key: "env\.MADEIRA_WINE_VCRT".*?note: ("(?:[^"\\]|\\.)*")', text)
    return m.group(1) if m else None


rendered = gen.render([{"key": "env.MADEIRA_WINE_VCRT", "kind": "text", "default": "", "category": "App & front end",
                        "note": gen.comment_near(lines, site), "sources": ["app/Madeira/WineProcessBridge.m"]}])
current = catalog_note((APP / "ConfigCatalog.generated.swift").read_text())
check("the settings catalog's note for MADEIRA_WINE_VCRT still comes out the same",
      current is not None and catalog_note(rendered) == current, (catalog_note(rendered), current))
fn_text = fn.group(0) if fn else ""
check("the Swift side names the bridge's folder, marker and the workflow's fourteen DLLs, in its order",
      'appendingPathComponent("VCRuntime", isDirectory: true)' in swift_src
      # round 625: the folder name is the package's (VCRuntimePackage.x64 "x86_64"; the 32-bit MFC's "x86", check-mfc32.py)
      and 'root.appendingPathComponent(package.folderName, isDirectory: true)' in swift_src and 'folderName: "x86_64",' in swift_src
      and '@"VCRuntime/x86_64"' in fn_text and 'static let marker = "vcruntime140.dll"' in swift_src
      and '@"vcruntime140.dll"' in fn_text and 'Bundle.main.bundleURL.appendingPathComponent("x86_64-vcruntime"' in swift_src
      and re.findall(r'"(\w+\.dll)"', swift_src[swift_src.index("static let dllNames"):swift_src.index("static let marker")]) == WANT)

# ---------------------------------------------------------------- 5. the rest
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only", "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
for name, text in (("BCDCab.c", cab_c), ("BCDCab.h", cab_h), ("VCRuntimeDownload.swift", swift_src)):
    check("%s carries the LICENSE-BCD.md header" % name, text.split("\n")[:3] == HEADER)
me = Path(__file__).read_text().split("\n")
check("this check carries the LICENSE-BCD.md header", me[1:4] == ["#" + h[2:] for h in HEADER])
literals = re.findall(r'"((?:[^"\\\n]|\\.)*)"', re.sub(r"(?m)^\s*//.*$", "", swift_src))
check("no user-visible string says Madeira", not [s for s in literals if "madeira" in s.lower()],
      [s for s in literals if "madeira" in s.lower()])
check("nothing else is downloaded and nothing is run: Microsoft's two links (x64; round 625 the 32-bit MFC's x86), no process, no dlopen",
      re.findall(r'URL\(string: "([^"]+)"\)', swift_src) == ["https://aka.ms/vs/17/release/vc_redist.x64.exe",
                                                           "https://aka.ms/vs/17/release/vc_redist.x86.exe"]
      and not re.search(r"\bProcess\(|posix_spawn|dlopen|NSTask|\bsystem\(", swift_src + cab_c))
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
sources = re.search(r"A3000002 /\* Sources \*/ = \{.*?files = \((.*?)\);", project, re.S).group(1)
check("the Xcode project builds BCDCab.c and VCRuntimeDownload.swift in the app and lists BCDCab.h",
      "/* BCDCab.c in Sources */" in sources and "/* VCRuntimeDownload.swift in Sources */" in sources
      and "path = BCDCab.h;" in project and "path = BCDCab.c;" in project and "path = VCRuntimeDownload.swift;" in project
      and len(set(re.findall(r"\b(A[12]000B0\d)\b", project))) == 5)
check("the bridging header gives Swift the cabinet reader",
      '#include "BCDCab.h"' in (APP / "Madeira-Bridging-Header.h").read_text())
check("CI runs this check after the build's own download", "python3 tests/host/check-vcruntime-download.py" in wf
      and wf.index("python3 tests/host/check-vcruntime-download.py") > wf.index("- name: Fetch the VC++ runtime DLLs from Microsoft"))

tmp_root.cleanup()
print("PASS: in-app VC++ runtime download" if ok else "FAILED: in-app VC++ runtime download")
sys.exit(0 if ok else 1)
