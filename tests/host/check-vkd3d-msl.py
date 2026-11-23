#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""libbcd-dxbc-msl: Wine's vkd3d-shader, DXBC compute shader -> Metal Shading Language (build/vkd3d-msl, round 2026-11-22).

Lossless Scaling's frame generation shaders are DXBC (cs_5_0), read on the device out of the person's own Lossless.dll and
translated there by this library. This check builds it from Wine's libs/vkd3d plus BCD's patch and BCD's front door, and proves:

  1. build/vkd3d-msl/build.sh builds the macOS variant (and the iOS one when an iPhone SDK is present) from a copy of the tree, with
     no bison, flex, Homebrew or network; the licence and the source note come out beside the dylib; the dylib exports exactly
     bcd_dxbc_to_msl and bcd_dxbc_free, links only libSystem and carries no build-machine path.
  2. Two compute shaders this check assembles itself, as DXBC bytes with the right checksum (nothing from Lossless.dll is kept in
     the repository), translate to MSL with `kernel void shader_entry(`, the documented bindings (CBV b0-15 -> descriptors[0-15],
     SRV t0-31 -> [16-47], UAV u0-31 -> [48-79], sampler s0-15 -> [80-95]) and the thread group they declare; the Metal compiler of
     the iPhone and Mac SDKs accepts the MSL (skipped where the Metal toolchain is not installed). A thread group outside
     Direct3D 11's limits is refused.
  3. Garbage, empty, truncated and broken input (a program cut short, a container header or chunk table with a wrong field, a
     NULL pointer) returns a negative result without crashing, hanging or taking gigabytes; a failed call leaves *msl NULL and tg
     zero; NULL output pointers are accepted. BCD's reader of the thread group is also run alone on hand-made and random chunks
     (with the address sanitizer when the SDK has it).
  4. With --lsfg-dir <dir> (the person's own folder of <id>.dxbc files, local only, never kept here): every shader translates, the
     thread groups are reported, and the MSL is compared byte for byte with <dir>/../lsfg-out/<id>.metal (or
     <dir>/../../lsfg-out, or --lsfg-out), made by build/vkd3d-msl/host/xlate.c, when that folder exists.

SKIP (exit 0) when there is no Wine tree with libs/vkd3d (--wine, $WINE_SRC, ./wine or ~/madeira-bcd/wine) or no Mac toolchain.
"""
import argparse
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, errors="replace", **kw)


def finish():
    print("PASS: vkd3d MSL library" if ok else "FAILED: vkd3d MSL library")
    sys.exit(0 if ok else 1)


ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--wine", help="Wine's tree (default: $WINE_SRC, ./wine, ~/madeira-bcd/wine)")
ap.add_argument("--lsfg-dir", help="a folder of <id>.dxbc files (the owner's, local only)")
ap.add_argument("--lsfg-out", help="the folder of <id>.metal files xlate.c made for those (default: next to --lsfg-dir)")
args = ap.parse_args()

# ------------------------------------------------------------------------------------------------------------ the tree and tools
candidates = [Path(p) for p in (args.wine, os.environ.get("WINE_SRC")) if p] + [R / "wine", Path.home() / "madeira-bcd/wine"]
wine = next((p for p in candidates if (p / "libs/vkd3d/libs/vkd3d-shader/msl.c").is_file()), None)
if wine is None:
    print("SKIP: no Wine tree with libs/vkd3d (use --wine <dir> or $WINE_SRC)")
    finish()
if sys.platform != "darwin" or not shutil.which("xcrun"):
    print("SKIP: needs macOS with the Xcode command line tools (xcrun)")
    finish()
if run(["xcrun", "-sdk", "macosx", "clang", "--version"]).returncode != 0:
    print("SKIP: no macOS clang")
    finish()
print(f"     Wine tree: {wine}")

HERE = R / "build/vkd3d-msl"
LIB = "libbcd-dxbc-msl"
EXPORTS = {"_bcd_dxbc_to_msl", "_bcd_dxbc_free"}
tmp = Path(tempfile.mkdtemp(prefix="check-vkd3d-msl."))


# ------------------------------------------------------------------------------------------------------- DXBC (written here)
def _md5_block(state, words):
    k = [int(abs(math.sin(i + 1)) * 2 ** 32) & 0xFFFFFFFF for i in range(64)]
    s = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
    a, b, c, d = state
    for i in range(64):
        if i < 16:
            f, g = d ^ (b & (c ^ d)), i
        elif i < 32:
            f, g = c ^ (d & (b ^ c)), (5 * i + 1) % 16
        elif i < 48:
            f, g = b ^ c ^ d, (3 * i + 5) % 16
        else:
            f, g = c ^ (b | (~d & 0xFFFFFFFF)), (7 * i) % 16
        f = (f + a + k[i] + words[g]) & 0xFFFFFFFF
        a, d, c = d, c, b
        b = (b + (((f << s[i]) | (f >> (32 - s[i]))) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return [(x + y) & 0xFFFFFFFF for x, y in zip(state, (a, b, c, d))]


def dxbc_checksum(data):
    """The checksum of a DXBC container's bytes after the magic and the checksum itself: MD5 with DXBC's own padding (the
    length goes first and last), as vkd3d-shader's checksum.c does it."""
    n = len(data)
    state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    full = n // 64 * 64
    for off in range(0, full, 64):
        state = _md5_block(state, struct.unpack("<16I", data[off:off + 64]))
    tail = data[full:]
    bits, last = (n * 8) & 0xFFFFFFFF, (n * 2 | 1) & 0xFFFFFFFF
    if 64 - (len(tail) + 1) < 8:
        state = _md5_block(state, struct.unpack("<16I", tail + b"\x80" + bytes(63 - len(tail))))
        block = struct.pack("<I", bits) + bytes(56) + struct.pack("<I", last)
    else:
        block = struct.pack("<I", bits) + tail + b"\x80" + bytes(55 - len(tail)) + struct.pack("<I", last)
    state = _md5_block(state, struct.unpack("<16I", block))
    return struct.pack("<4I", *state)


def dxbc(chunks):
    """A DXBC container of (tag, payload) chunks, with the right sizes and checksum."""
    head = 32 + 4 * len(chunks)
    body, offsets = b"", []
    for tag, payload in chunks:
        offsets.append(head + len(body))
        body += tag + struct.pack("<I", len(payload)) + payload
    blob = b"DXBC" + bytes(16) + struct.pack("<III", 1, head + len(body), len(chunks)) + struct.pack(f"<{len(chunks)}I", *offsets) + body
    return blob[:4] + dxbc_checksum(blob[20:]) + blob[20:]


def cs_5_0(tokens, extra_chunks=True):
    """A cs_5_0 program (version and length tokens added) in a container with empty signatures, as fxc writes it."""
    words = [0x00050050, len(tokens) + 2] + list(tokens)
    chunks = [(b"ISGN", struct.pack("<II", 0, 8)), (b"OSGN", struct.pack("<II", 0, 8))] if extra_chunks else []
    return dxbc(chunks + [(b"SHEX", struct.pack(f"<{len(words)}I", *words))])


EXT_DIM_TEX2D, EXT_RET_FLOAT4 = 0x800000C2, 0x00155543   # extended opcode tokens: resource dimension texture2d, return type float4

# 8x8x1: reads t0, writes u0.
#   dcl_globalFlags refactoringAllowed / dcl_resource_texture2d (float,float,float,float) t0
#   dcl_uav_typed_texture2d (float,float,float,float) u0 / dcl_input vThreadID.xy / dcl_temps 1 / dcl_thread_group 8, 8, 1
#   mov r0.xy, vThreadID.xyxx / mov r0.zw, l(0, 0, 0, 0)
#   ld_indexable(texture2d)(float,float,float,float) r0.xyzw, r0.xyzw, t0.xyzw
#   store_uav_typed u0.xyzw, vThreadID.xyyy, r0.xyzw / ret
SHADER_COPY = [
    0x0100086A,
    0x04001858, 0x00107000, 0, 0x00005555,
    0x0400189C, 0x0011E000, 0, 0x00005555,
    0x0200005F, 0x00020032,
    0x02000068, 1,
    0x0400009B, 8, 8, 1,
    0x04000036, 0x00100032, 0, 0x00020046,
    0x08000036, 0x001000C2, 0, 0x00004002, 0, 0, 0, 0,
    0x8900002D, EXT_DIM_TEX2D, EXT_RET_FLOAT4, 0x001000F2, 0, 0x00100E46, 0, 0x00107E46, 0,
    0x060000A4, 0x0011E0F2, 0, 0x00020546, 0x00100E46, 0,
    0x0100003E,
]
# 16x4x1: constant buffer b0, texture t3, sampler s1, UAV u2.
#   dcl_constantbuffer cb0[1], immediateIndexed / dcl_sampler s1, mode_default
#   dcl_resource_texture2d (float,float,float,float) t3 / dcl_uav_typed_texture2d (float,float,float,float) u2
#   dcl_input vThreadID.xy / dcl_temps 1 / dcl_thread_group 16, 4, 1
#   utof r0.xy, vThreadID.xyxx / mul r0.xy, r0.xyxx, cb0[0].xyxx
#   sample_l_indexable(texture2d)(float,float,float,float) r0.xyzw, r0.xyxx, t3.xyzw, s1, l(0)
#   store_uav_typed u2.xyzw, vThreadID.xyyy, r0.xyzw / ret
SHADER_BINDINGS = [
    0x0100086A,
    0x04000059, 0x00208E46, 0, 1,
    0x0300005A, 0x00106000, 1,
    0x04001858, 0x00107000, 3, 0x00005555,
    0x0400189C, 0x0011E000, 2, 0x00005555,
    0x0200005F, 0x00020032,
    0x02000068, 1,
    0x0400009B, 16, 4, 1,
    0x04000056, 0x00100032, 0, 0x00020046,
    0x08000038, 0x00100032, 0, 0x00100046, 0, 0x00208046, 0, 0,
    0x8D000048, EXT_DIM_TEX2D, EXT_RET_FLOAT4, 0x001000F2, 0, 0x00100046, 0, 0x00107E46, 3, 0x00106000, 1, 0x00004001, 0,
    0x060000A4, 0x0011E0F2, 2, 0x00020546, 0x00100E46, 0,
    0x0100003E,
]
THREAD_GROUP_AT = {"copy": SHADER_COPY.index(0x0400009B), "bindings": SHADER_BINDINGS.index(0x0400009B)}

def with_checksum(blob):
    return blob[:4] + dxbc_checksum(blob[20:]) + blob[20:]


def broken_programs(shader):
    """Containers with a valid checksum around a cut copy of a shader's tokens: (name, bytes). Programs cut after k tokens, with the
    length token still the old one or made honest."""
    for k in range(len(shader) + 1):
        words = [0x00050050, len(shader) + 2] + shader[:k]
        yield f"short-{k:03d}", dxbc([(b"SHEX", struct.pack(f"<{len(words)}I", *words))])
    for k in range(len(shader) + 1):
        yield f"cut-{k:03d}", cs_5_0(shader[:k], extra_chunks=False)


def broken_containers(good):
    """Copies of a container with one header, chunk table or chunk header word replaced and the checksum made right again, so
    that it is vkd3d's container parser and BCD's chunk reader that meet the broken field (name, bytes)."""
    count = struct.unpack_from("<I", good, 28)[0]
    offsets = struct.unpack_from(f"<{count}I", good, 32)
    spots = [20, 24, 28] + [32 + 4 * i for i in range(count)] + [o + d for o in offsets for d in (0, 4)] + [offsets[-1] + 8, offsets[-1] + 12]
    for pos in spots:
        old = struct.unpack_from("<I", good, pos)[0]
        for i, v in enumerate((0, 1, 4, 8, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, old + 1, old - 1, old + 4, old - 4, old ^ 0x01000000)):
            blob = bytearray(good)
            struct.pack_into("<I", blob, pos, v & 0xFFFFFFFF)
            yield f"hdr-{pos:04d}-{i:02d}", with_checksum(bytes(blob))


# ------------------------------------------------------------------------------------------------------------ the host program
HARNESS = r"""
#include <dlfcn.h>
#include <mach/mach.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef int (*to_msl_fn)(const void *, size_t, char **, unsigned[3], char **);
typedef void (*free_fn)(void *);
static const char *volatile current = "-";
/* a hostile program must not take the machine's memory: past 1 GB resident the run ends, naming the input that did it */
static void *watch(void *unused)
{
    (void)unused;
    for (;;)
    {
        mach_task_basic_info_data_t info;
        mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
        usleep(2000);
        if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&info, &count) == KERN_SUCCESS && info.resident_size > ((size_t)1 << 30))
        {
            printf("MEMORY %s\n", current);
            fflush(stdout);
            _exit(3);
        }
    }
    return NULL;
}
/* harness <dylib> <out dir or -> [--nullout] <file | --nullin>... : one RES line per input, BAD lines for a broken contract */
int main(int argc, char **argv)
{
    int i, nullout = 0;
    void *h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    to_msl_fn to_msl;
    free_fn release;
    pthread_t watcher;
    if (!h) { printf("NODLOPEN %s\n", dlerror()); return 2; }
    pthread_create(&watcher, NULL, watch, NULL);
    to_msl = (to_msl_fn)dlsym(h, "bcd_dxbc_to_msl");
    release = (free_fn)dlsym(h, "bcd_dxbc_free");
    if (!to_msl || !release) { printf("NOSYMBOL\n"); return 2; }
    printf("HIDDEN vkd3d_shader_compile=%d\n", dlsym(h, "vkd3d_shader_compile") == NULL);
    setvbuf(stdout, NULL, _IOLBF, 0);
    for (i = 3; i < argc; ++i)
    {
        const char *name = argv[i], *base;
        char *msl = (char *)1, *log = (char *)1, *bytes = NULL, path[4096];
        unsigned tg[3] = {7, 7, 7};
        size_t size = 0, n;
        int rc;
        if (!strcmp(name, "--nullout")) { nullout = 1; continue; }
        if (!strcmp(name, "--nullin"))
        {
            printf("TRY nullin\n");
            rc = to_msl(NULL, 16, &msl, tg, &log);
            printf("RES nullin rc=%d tg=%u,%u,%u msl=%d log=%d\n", rc, tg[0], tg[1], tg[2], msl ? (int)strlen(msl) : -1, log ? (int)strlen(log) : -1);
            if (rc == 0 || msl || tg[0] || tg[1] || tg[2]) printf("BAD nullin NULL input must fail with msl NULL and tg zero\n");
            if (log) release(log);
            continue;
        }
        base = strrchr(name, '/');
        base = base ? base + 1 : name;
        {
            FILE *f = fopen(name, "rb");
            if (!f) { printf("NOFILE %s\n", name); continue; }
            fseek(f, 0, SEEK_END);
            size = (size_t)ftell(f);
            fseek(f, 0, SEEK_SET);
            bytes = malloc(size ? size : 1);   /* exactly size bytes: a read past the end is the allocator's to catch */
            if (size && fread(bytes, 1, size, f) != size) { printf("NOREAD %s\n", name); return 2; }
            fclose(f);
        }
        printf("TRY %s\n", base);
        current = strdup(base);
        if (nullout)
        {
            rc = to_msl(bytes, size, NULL, NULL, NULL);
            printf("RES %s rc=%d tg=0,0,0 msl=-1 log=-1\n", base, rc);
            free(bytes);
            continue;
        }
        rc = to_msl(bytes, size, &msl, tg, &log);
        printf("RES %s rc=%d tg=%u,%u,%u msl=%d log=%d\n", base, rc, tg[0], tg[1], tg[2], msl ? (int)strlen(msl) : -1, log ? (int)strlen(log) : -1);
        if (rc != 0 && (msl || tg[0] || tg[1] || tg[2])) printf("BAD %s a failed call must leave msl NULL and tg zero\n", base);
        if (rc == 0 && (!msl || !strlen(msl) || !tg[0] || !tg[1] || !tg[2])) printf("BAD %s a translation needs MSL text and a thread group\n", base);
        if (rc == 0 && argv[2][0] != '-')
        {
            FILE *o;
            n = strlen(base);
            snprintf(path, sizeof path, "%s/%.*s.metal", argv[2], (int)(n > 5 && !strcmp(base + n - 5, ".dxbc") ? n - 5 : n), base);
            o = fopen(path, "wb");
            fwrite(msl, 1, strlen(msl), o);
            fclose(o);
        }
        if (msl) release(msl);
        if (log) release(log);
        free(bytes);
    }
    dlclose(h);
    printf("DONE\n");
    return 0;
}
"""


PARSER_TEST = r"""
/* BCD's chunk reader (bcd_find_thread_group in bcd_dxbc_msl.c, included here) on shader chunks written word by word and on random
 * ones, each in a buffer of exactly its size so that a read past the end is what the allocator or the sanitizer reports. */
#include BCD_SRC
static int bad, cases;
static void expect(const char *name, const uint32_t *w, size_t bytes, int found, unsigned x, unsigned y, unsigned z)
{
    unsigned tg[3] = {0, 0, 0};
    uint8_t *chunk = malloc(bytes ? bytes : 1);
    int r;
    cases++;
    memcpy(chunk, w, bytes);
    r = bcd_find_thread_group(chunk, bytes, tg);
    if (r != found || (found && (tg[0] != x || tg[1] != y || tg[2] != z)))
    {
        printf("BAD %s: found %d tg %u,%u,%u\n", name, r, tg[0], tg[1], tg[2]);
        bad++;
    }
    free(chunk);
}
int main(void)
{
    static const uint32_t plain[] = {0x00050050, 7, 0x0400009B, 8, 8, 1, 0x0100003E};
    static const uint32_t custom[] = {0x00050050, 12, 0x35, 6, 1, 2, 3, 4, 0x0400009B, 4, 5, 6};
    static const uint32_t custom0[] = {0x00050050, 8, 0x35, 0, 0x0400009B, 8, 8, 1};
    static const uint32_t customhuge[] = {0x00050050, 8, 0x35, 0x7FFFFFFF, 0x0400009B, 8, 8, 1};
    static const uint32_t customff[] = {0x00050050, 8, 0x35, 0xFFFFFFFF, 0x0400009B, 8, 8, 1};
    static const uint32_t customlast[] = {0x00050050, 3, 0x35};
    static const uint32_t zerolen[] = {0x00050050, 8, 0x00000036, 0x0400009B, 8, 8, 1};
    static const uint32_t short3[] = {0x00050050, 6, 0x0300009B, 8, 8, 1};
    static const uint32_t pastchunk[] = {0x00050050, 1000, 0x0400009B, 8, 8, 1};
    static const uint32_t pastlength[] = {0x00050050, 2, 0x0400009B, 8, 8, 1};
    static const uint32_t flagged[] = {0x00050050, 6, 0x0400089B, 2, 3, 4};
    static const uint32_t twice[] = {0x00050050, 10, 0x0400009B, 1, 2, 3, 0x0400009B, 4, 5, 6};
    static const uint32_t skipbig[] = {0x00050050, 8, 0x7F000036, 0x0400009B, 8, 8, 1};
    static const uint32_t headeronly[] = {0x00050050, 2};
    uint64_t seed = 88172645463325252ull;
    unsigned long runs = 0, hits = 0;
    unsigned k;
    expect("plain", plain, sizeof plain, 1, 8, 8, 1);
    expect("custom data first", custom, sizeof custom, 1, 4, 5, 6);
    expect("custom data of length 0", custom0, sizeof custom0, 0, 0, 0, 0);
    expect("custom data of length 2^31-1", customhuge, sizeof customhuge, 0, 0, 0, 0);
    expect("custom data of length 2^32-1", customff, sizeof customff, 0, 0, 0, 0);
    expect("custom data without its length word", customlast, sizeof customlast, 0, 0, 0, 0);
    expect("an instruction of length 0", zerolen, sizeof zerolen, 0, 0, 0, 0);
    expect("dcl_thread_group of length 3", short3, sizeof short3, 0, 0, 0, 0);
    expect("length token past the chunk", pastchunk, sizeof pastchunk, 1, 8, 8, 1);
    expect("dcl_thread_group past the length token", pastlength, sizeof pastlength, 0, 0, 0, 0);
    expect("opcode flags above bit 10 are not the opcode", flagged, sizeof flagged, 1, 2, 3, 4);
    expect("the first thread group counts", twice, sizeof twice, 1, 1, 2, 3);
    expect("a long instruction is skipped by its length", skipbig, sizeof skipbig, 0, 0, 0, 0);
    expect("version and length only", headeronly, sizeof headeronly, 0, 0, 0, 0);
    expect("chunk of 0 bytes", plain, 0, 0, 0, 0, 0);
    expect("chunk of 7 bytes", plain, 7, 0, 0, 0, 0);
    expect("chunk of 27 bytes (a word and a bit short)", plain, 27, 1, 8, 8, 1);
    expect("chunk of 23 bytes: the thread group cut after y", plain, 23, 0, 0, 0, 0);
    for (k = 0; k < sizeof plain; k++)
        expect("plain, cut at every byte", plain, k, k >= 24 ? 1 : 0, 8, 8, 1);
    for (runs = 0; runs < 300000; runs++)
    {
        uint32_t w[40];
        unsigned tg[3], i, words;
        uint8_t *chunk;
        size_t bytes;
        for (i = 0; i < 40; i++)
        {
            seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
            switch (seed % 7)
            {
                case 0: w[i] = 0x0400009B; break;
                case 1: w[i] = 0x35; break;
                case 2: w[i] = ((uint32_t)(seed >> 8) & 0x7F000000u) | 0x36; break;
                case 3: w[i] = (uint32_t)(seed >> 16) % 12; break;
                case 4: w[i] = (uint32_t)(seed >> 8); break;
                case 5: w[i] = 0x0100003E; break;
                default: w[i] = (uint32_t)(seed >> 32) % 40; break;
            }
        }
        words = (unsigned)((seed >> 40) % 41);
        bytes = words * 4u + (unsigned)((seed >> 50) % 4);
        chunk = malloc(bytes ? bytes : 1);
        memcpy(chunk, w, bytes > sizeof w ? sizeof w : bytes);
        hits += (unsigned long)bcd_find_thread_group(chunk, bytes, tg);
        free(chunk);
    }
    printf("PARSER cases=%d runs=%lu found=%lu bad=%d\n", cases, runs, hits, bad);
    return bad ? 1 : 0;
}
"""


def parse_res(stdout):
    res = {}
    for line in stdout.splitlines():
        if line.startswith("RES "):
            _, name, rc, tg, msl, log = line.split()
            res[name] = {"rc": int(rc[3:]), "tg": tuple(int(x) for x in tg[3:].split(",")), "msl": int(msl[4:]), "log": int(log[4:])}
    return res


def harness_run(harness, dylib, out, files, timeout=300, scribble=True):
    # MallocScribble fills every fresh allocation with 0xaa, so a text without its NUL shows; not for a broken chunk count that makes
    # vkd3d ask for gigabytes (the freed block would be scribbled page by page)
    env = dict(os.environ, MallocGuardEdges="1", **({"MallocScribble": "1"} if scribble else {}))
    p = subprocess.run([str(harness), str(dylib), str(out)] + [str(f) for f in files], capture_output=True, text=True,
                       errors="replace", env=env, timeout=timeout)
    lines = p.stdout.splitlines()
    tries = [ln for ln in lines if ln.startswith("TRY ")]
    return p, parse_res(p.stdout), [ln for ln in lines if ln.startswith("BAD ")], (tries[-1] if tries else "")


try:
    # --------------------------------------------------------------------------------------------------------- 1. the builds
    check("the build script, the patch, BCD's file and the stubs are there",
          all((HERE / n).is_file() for n in ("build.sh", "bcd_dxbc_msl.c", "host/hlsl_stubs.c", "host/windows.h", "host/host-config.h"))
          and len(list(HERE.glob("0001-*.patch"))) == 1)
    src = (HERE / "bcd_dxbc_msl.c").read_text()
    check("bcd_dxbc_msl.c carries the LGPL header (it links into the LGPL library)",
          src.startswith("// SPDX-License-Identifier: LGPL-2.1-or-later\n// Copyright 2026 thebcd69 (BCD)\n"))
    check("BCD's file names the two exported functions, with default visibility, and nothing else",
          src.count("BCD_EXPORT") == 3 and "BCD_EXPORT int bcd_dxbc_to_msl(" in src and "BCD_EXPORT void bcd_dxbc_free(" in src)

    mac = tmp / "macos"
    r = run(["bash", HERE / "build.sh", wine, mac, "macos"], timeout=1800)
    check("build.sh builds the macOS variant (no bison, flex, Homebrew or network)", r.returncode == 0, r.stdout + r.stderr)
    dylib = mac / f"{LIB}.dylib"
    check("...the dylib, the licence and the source note are beside each other",
          dylib.is_file() and (mac / f"{LIB}.LICENSE.txt").is_file() and (mac / f"{LIB}.SOURCE.txt").is_file())
    if not dylib.is_file():
        finish()
    lic = (mac / f"{LIB}.LICENSE.txt").read_text()
    note = (mac / f"{LIB}.SOURCE.txt").read_text()
    check("...the licence is the LGPL: vkd3d's own notice, then the full text",
          "GNU Lesser General Public License" in lic and "Version 2.1, February 1999" in lic and "vkd3d" in lic.lower(), lic[:300])
    top = run(["git", "-C", wine, "rev-parse", "--show-toplevel"])
    own_repo = top.returncode == 0 and Path(top.stdout.strip()).resolve() == wine.resolve()   # not a folder inside some other repository
    head = run(["git", "-C", wine, "rev-parse", "HEAD"]).stdout.strip() if own_repo else ""
    check("...the source note is one line naming vkd3d's source, the Wine commit, the patch and BCD's files",
          len(note.strip().splitlines()) == 1 and "libs/vkd3d" in note and "0001-" in note and "bcd_dxbc_msl.c" in note
          and (not head or head in note), note)

    variants = [("macOS", dylib)]
    if run(["xcrun", "-sdk", "iphoneos", "--show-sdk-path"]).returncode == 0:
        ios = tmp / "ios"
        r = run(["bash", HERE / "build.sh", wine, ios, "ios"], timeout=1800)
        check("build.sh builds the iOS variant", r.returncode == 0, r.stdout + r.stderr)
        if (ios / f"{LIB}.dylib").is_file():
            variants.append(("iOS", ios / f"{LIB}.dylib"))
            vt = run(["otool", "-l", ios / f"{LIB}.dylib"]).stdout
            check("...it is arm64, for iOS (platform 2), minimum iOS 26.0, install name @rpath/" + LIB + ".dylib",
                  "platform 2" in vt and "minos 26.0" in vt and f"@rpath/{LIB}.dylib" in vt
                  and "arm64" in run(["lipo", "-archs", ios / f"{LIB}.dylib"]).stdout)
    else:
        print("skip the iOS build: no iPhone SDK")

    for label, path in variants:
        names = {ln.split()[-1] for ln in run(["nm", "-gU", path]).stdout.splitlines() if ln.strip()}
        check(f"{label}: the dylib exports exactly bcd_dxbc_to_msl and bcd_dxbc_free", names == EXPORTS, sorted(names))
        deps = [ln.split()[0] for ln in run(["otool", "-L", path]).stdout.splitlines()[1:]]
        check(f"{label}: it links nothing but libSystem (and its own install name)",
              all(d.startswith("/usr/lib/libSystem") or d == f"@rpath/{LIB}.dylib" for d in deps), deps)
        blob = path.read_bytes()
        check(f"{label}: no build-machine path is inside it",
              b"/Users/" not in blob and str(tmp).encode() not in blob and b"/var/folders" not in blob and b"/private/tmp" not in blob)
        check(f"{label}: the MSL target and BCD's additions are in it",
              b"Generating a MSL shader" in blob and b"shader_entry" in blob and b"threadgroup uint g" in blob)

    # ------------------------------------------------------------------------------------------ 2. the host program, two shaders
    harness = tmp / "harness"
    (tmp / "harness.c").write_text(HARNESS)
    r = run(["xcrun", "-sdk", "macosx", "clang", "-O1", "-Wall", "-Wextra", "-Werror", "-mmacosx-version-min=13.0", tmp / "harness.c",
             "-o", harness])
    check("the host program (dlopen + dlsym) compiles", r.returncode == 0, r.stderr)
    if r.returncode != 0:
        finish()

    fx = tmp / "fx"
    fx.mkdir()
    (fx / "copy.dxbc").write_bytes(cs_5_0(SHADER_COPY))
    (fx / "bindings.dxbc").write_bytes(cs_5_0(SHADER_BINDINGS))
    out = tmp / "out"
    out.mkdir()
    p, res, bad, _ = harness_run(harness, dylib, out, [fx / "copy.dxbc", fx / "bindings.dxbc"])
    check("the host program loads the macOS dylib; vkd3d's own API is not reachable through it",
          p.returncode == 0 and "HIDDEN vkd3d_shader_compile=1" in p.stdout, p.stdout + p.stderr)
    c, b = res.get("copy.dxbc", {}), res.get("bindings.dxbc", {})
    check("copy (reads t0, writes u0): result 0, thread group (8, 8, 1)", c.get("rc") == 0 and c.get("tg") == (8, 8, 1), p.stdout)
    raw = (out / "copy.metal").read_bytes() if (out / "copy.metal").exists() else b""
    msl = raw.decode("utf-8", "replace")
    check("...MSL with `kernel void shader_entry(`, descriptors[16] (t0) and descriptors[48] (u0), no other binding",
          "kernel void shader_entry(" in msl and "descriptors[16]" in msl and "descriptors[48]" in msl
          and not any(f"descriptors[{k}]" in msl for k in (0, 1, 17, 49, 80, 81)), msl[:600])
    check("...it is Metal source (metal_stdlib), ASCII to its last line, and the NUL is right after it (fresh memory is scribbled)",
          msl.startswith("/* Generated by vkd3d-shader") and "#include <metal_stdlib>" in msl and raw.isascii() and raw.endswith(b"}\n")
          and len(raw) == c.get("msl"), raw[-40:])
    check("bindings (b0, t3, s1, u2): result 0, thread group (16, 4, 1)", b.get("rc") == 0 and b.get("tg") == (16, 4, 1), p.stdout)
    msl2 = (out / "bindings.metal").read_bytes().decode("utf-8", "replace") if (out / "bindings.metal").exists() else ""
    check("...descriptors[0] (b0), [19] (t3), [50] (u2) and [81] (s1)",
          all(f"descriptors[{k}]" in msl2 for k in (0, 19, 50, 81)) and "kernel void shader_entry(" in msl2, msl2[:900])
    check("...no vkd3d note on stderr or stdout (its log callback is silenced)", p.stderr.strip() == "" and "Generating" not in p.stdout, p.stderr)
    check("the contract holds for both (no BAD line)", not bad, bad)

    # the Metal compiler of the SDK takes the MSL
    for sdk in ("iphoneos", "macosx"):
        if run(["xcrun", "-sdk", sdk, "metal", "--version"]).returncode != 0:
            print(f"skip the Metal compiler of the {sdk} SDK: the Metal toolchain is not installed")
            continue
        for name in ("copy", "bindings"):
            r = run(["xcrun", "-sdk", sdk, "metal", "-std=metal3.2", "-c", out / f"{name}.metal", "-o", tmp / f"{name}-{sdk}.air"])
            check(f"the Metal compiler ({sdk} SDK, metal3.2) accepts {name}'s MSL", r.returncode == 0, r.stderr)

    # NULL outputs, a shader without a thread group
    p, res, bad, _ = harness_run(harness, dylib, "-", ["--nullout", fx / "copy.dxbc"])
    check("NULL msl, tg and log pointers are accepted", res.get("copy.dxbc", {}).get("rc") == 0 and p.returncode == 0, p.stdout + p.stderr)
    nogroup = list(SHADER_COPY)
    i = THREAD_GROUP_AT["copy"]
    del nogroup[i:i + 4]
    (fx / "nogroup.dxbc").write_bytes(cs_5_0(nogroup))
    p, res, bad, _ = harness_run(harness, dylib, "-", [fx / "nogroup.dxbc"])
    check("a shader without dcl_thread_group fails (negative, msl NULL, tg zero) and says why",
          res.get("nogroup.dxbc", {}).get("rc", 0) < 0 and not bad, p.stdout + p.stderr)

    # ----------------------------------------------------------------------------------------- 3. garbage, truncated, mutated
    bad_dir = tmp / "garbage"
    bad_dir.mkdir()
    rnd = random.Random(20261122)
    good = (fx / "copy.dxbc").read_bytes()
    cases = {"empty": b"", "three-bytes": b"DXB", "magic-only": b"DXBC", "header-zeros": b"DXBC" + bytes(60),
             "random-4k": bytes(rnd.randrange(256) for _ in range(4096)), "all-ff": b"\xff" * 512,
             "wrong-magic": b"DXBD" + good[4:], "bad-checksum": good[:8] + bytes([good[8] ^ 1]) + good[9:],
             "ascii-text": b"this is not a shader\n" * 20,
             "chunk-offset-past-end": good[:32] + struct.pack("<I", 0x7FFFFFF0) + good[36:]}
    for k in range(0, len(good), 7):
        cases[f"cut-{k:04d}"] = good[:k]
    for n, data in cases.items():
        (bad_dir / (n + ".dxbc")).write_bytes(data)
    p, res, bad, last = harness_run(harness, dylib, "-", sorted(bad_dir.glob("*.dxbc")) + ["--nullin"])
    check(f"{len(cases)} garbage, empty and truncated inputs and a NULL pointer: no crash, every one negative",
          p.returncode == 0 and "DONE" in p.stdout and len(res) == len(cases) + 1 and all(v["rc"] < 0 for v in res.values()) and not bad,
          f"exit {p.returncode}, last {last}\n{p.stderr[-300:]}\n{[k for k, v in res.items() if v['rc'] >= 0]}")

    # BCD's own reader of the thread group, alone, on hand-made and random chunks (with the address sanitizer when the SDK has it)
    (tmp / "parser.c").write_text(PARSER_TEST)
    for san in (["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"], []):
        r = run(["xcrun", "-sdk", "macosx", "clang", "-O1", "-g", "-Wall", "-Wextra", "-Wno-parentheses", "-Wno-unused-function",
                 "-I", wine / "libs/vkd3d/include", "-DVKD3D_NO_WIN32_TYPES", "-DVKD3D_NO_VULKAN_H", f'-DBCD_SRC="{HERE / "bcd_dxbc_msl.c"}"',
                 "-Wl,-undefined,dynamic_lookup", *san, tmp / "parser.c", "-o", tmp / "parser"])
        if r.returncode == 0 or not san:
            break
    check("the thread group reader's test program compiles", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        r = run([tmp / "parser"], timeout=300, env=dict(os.environ, MallocGuardEdges="1"))
        check("the thread group reader" + (" (address and undefined-behaviour sanitizers)" if san else "")
              + ": " + r.stdout.strip().replace("PARSER cases=", "").replace(" runs=", " hand-made cases, ").replace(" found=", " random chunks (")
              .replace(" bad=0", " of them with a thread group): every answer as expected, no read past a chunk"),
              r.returncode == 0 and "PARSER" in r.stdout and "BAD" not in r.stdout, r.stdout + r.stderr[-800:])

    # a valid container (the checksum is right, so vkd3d's own parser meets it) whose shader program is cut short, or whose header or
    # chunk table is broken
    mut = tmp / "mutants"
    mut.mkdir()
    cut_names = []
    for n, data in list(broken_programs(SHADER_COPY)) + list(broken_containers(good)):
        (mut / (n + ".dxbc")).write_bytes(data)
        cut_names.append(n)
    try:
        p, res, bad, last = harness_run(harness, dylib, "-", sorted(mut.glob("*.dxbc")), timeout=600, scribble=False)
        survived = p.returncode == 0 and "DONE" in p.stdout and len(res) == len(cut_names)
        detail = f"exit {p.returncode}, last {last}\n{p.stderr[-300:]}"
    except subprocess.TimeoutExpired as e:
        survived, bad, res, detail = False, [], {}, f"no answer in 600 s: {e}"
    check(f"{len(cut_names)} programs cut short and containers with a broken field (checksum right): no crash, no hang, no memory hunger, "
          "the contract kept for each", survived and not bad, detail + "\n" + "\n".join(bad[:5]))
    shorts = [v["rc"] for k, v in res.items() if k.startswith("short-") and int(k[6:9]) < len(SHADER_COPY)]
    check("...a program shorter than its own length token is refused", bool(shorts) and all(rc < 0 for rc in shorts))

    # thread groups: the limits
    for name, group, valid in (("tg-1024x1x1", (1024, 1, 1), True), ("tg-1x1x64", (1, 1, 64), True), ("tg-32x32x1", (32, 32, 1), True),
                               ("tg-0x8x1", (0, 8, 1), False), ("tg-8x8x0", (8, 8, 0), False), ("tg-2048x1x1", (2048, 1, 1), False),
                               ("tg-8x8x64", (8, 8, 64), False), ("tg-1x1x65", (1, 1, 65), False), ("tg-big", (0xFFFFFFFF, 1, 1), False)):
        t = list(SHADER_COPY)
        i = THREAD_GROUP_AT["copy"]
        t[i + 1:i + 4] = group
        (fx / (name + ".dxbc")).write_bytes(cs_5_0(t))
        p, res, bad, _ = harness_run(harness, dylib, "-", [fx / (name + ".dxbc")])
        r = res.get(name + ".dxbc", {})
        check(f"thread group {group}: " + ("returned as declared" if valid else "refused (outside Direct3D 11's limits)"),
              not bad and (r.get("rc") == 0 and r.get("tg") == group if valid else r.get("rc", 0) < 0), p.stdout + p.stderr)

    # ------------------------------------------------------------------------------------------------------- 4. the owner's shaders
    if args.lsfg_dir:
        lsfg = Path(args.lsfg_dir)
        files = sorted(lsfg.glob("*.dxbc"), key=lambda f: int(f.stem) if f.stem.isdigit() else f.stem)
        check(f"--lsfg-dir: {len(files)} <id>.dxbc files", bool(files), lsfg)
        if files:
            ref = next((Path(p) for p in ([args.lsfg_out] if args.lsfg_out else [lsfg / "../lsfg-out", lsfg / "../../lsfg-out"])
                        if (Path(p) / (files[0].stem + ".metal")).is_file()), None)
            sums = 0
            for f in files:
                d = f.read_bytes()
                sums += d[4:20] == dxbc_checksum(d[20:])
            check(f"this check's DXBC checksum routine reproduces the stored checksum of all {len(files)} (it is what the fixtures stand on)",
                  sums == len(files), f"{sums} of {len(files)}")
            lout = tmp / "lsfg-out"
            lout.mkdir()
            p, res, bad, last = harness_run(harness, dylib, lout, files)
            done = [v for v in res.values() if v["rc"] == 0]
            check(f"lsfg: {len(done)} of {len(files)} translate (result 0), the contract kept",
                  p.returncode == 0 and len(done) == len(files) and not bad, f"{p.stderr[-300:]}\n{bad}\n{[k for k, v in res.items() if v['rc']]}")
            groups = {}
            for f in files:
                groups.setdefault(res.get(f.name, {}).get("tg"), []).append(f.stem)
            print("     thread groups: " + "; ".join(f"{g}: {len(ids)}" + (f" ({', '.join(ids)})" if len(ids) <= 6 else "") for g, ids in sorted(groups.items(), key=lambda x: -len(x[1]))))
            if ref is None:
                print("skip the byte comparison: no lsfg-out folder (--lsfg-out)")
            else:
                same = [f.stem for f in files if (lout / (f.stem + ".metal")).exists() and (ref / (f.stem + ".metal")).exists()
                        and (lout / (f.stem + ".metal")).read_bytes() == (ref / (f.stem + ".metal")).read_bytes()]
                check(f"lsfg: the MSL is byte for byte the spike's (xlate.c) for {len(same)} of {len(files)}",
                      len(same) == len(files), [f.stem for f in files if f.stem not in same])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

finish()
