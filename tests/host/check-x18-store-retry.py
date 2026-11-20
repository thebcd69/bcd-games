#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A store of x18 (opengl32's NtCurrentTeb() into a params struct) takes two tries (round 677, agent G3 S3).

iOS zeroes x18 when it preempts a thread. The x18 patcher (build/ntdll-unix/virtual_ios.c, ios_jit_patch_x18) runs a store of
x18 through "load the TEB into x18, then the store": a preemption between the two stores 0 without faulting, and the GL call
reaches the unix side with teb == NULL ("[fault_rip] ... region=0x1", addr 0x1238/0x1248; Alien Tenant KFW5H1 16 times in 5
minutes, YJBGY1; Harem Heaven D4SD4B, Buckshot Roulette 43BDZ0, Mewgenics ZQX5PG). From build 678 the store site is:
    mrs x18 ; and x18,#~7 ; ldr x18,[x18,#slot] ; <store> ; cbnz x18, done ; mrs ; and ; ldr ; <store> ; done: b back
x18 still non-zero after the first store means the stored value was the TEB; zero means iOS cleared it, so the store runs once
more (no writeback form is taken: storing twice is storing once). A slot that really holds 0 stores 0 twice and returns.

Compiles the production patcher (ios_insn_x18_role*, the data map, ios_jit_x18_tramp_need, ios_jit_patch_x18) and checks:
  1. the budget: 40 bytes for a store site, 32 for every other site, stores only for opengl32 (stores = 1);
  2. the words of a store site, also read back by llvm-objdump when it is here (cbnz's target is the branch back);
  3. on this arm64 Mac: the patched code runs. TPIDRRO_EL0 & ~7 is Darwin's TSD base, so a pthread key's slot stands in for the
     TEB slot: the store writes the key's value; with the value 0 it writes 0 and returns (no loop);
  4. an image without stores (stores = 0) leaves a `str x18` alone, as before.
"""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
vi = (R / "build/ntdll-unix/virtual_ios.c").read_text()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


def function(src, signature):
    start = src.index(signature)
    return src[start:src.index("\n}", start) + 2] + "\n"


patcher = function(vi, "int ios_jit_patch_x18(char *text_rw, char *text_rx, size_t text_size,")
check(patcher.index("else if (role == X18_ROLE_RT)") < patcher.index("/* Stores, LDP, and arithmetic: the operands must all survive")
      and "0xB5000000 | (5 << 5) | 18" in patcher and "for (int pass = 0; pass < 2; pass++)" in patcher,
      "the patcher has the two-try store form ahead of its generic one")

cc = shutil.which(os.environ.get("CC", "cc"))
if not cc:
    print("SKIP: no C compiler")
else:
    defines = vi[vi.index("#define X18_ROLE_NONE 0"):vi.index("static int ios_insn_x18_role(uint32_t insn)")]
    data_word = re.search(r"#define IOS_X18_DATA_WORD\(dm, off\).*\n", vi).group(0)
    parts = [defines, data_word,
             function(vi, "static int ios_insn_x18_role(uint32_t insn)"),
             function(vi, "static int ios_insn_x18_role_ex(uint32_t insn, int stores)"),
             function(vi, "static int ios_x18_arith_dest(uint32_t insn, int role)"),
             function(vi, "static uint32_t ios_insn_replace_x18(uint32_t insn, int role, int scratch)"),
             function(vi, "static unsigned char *ios_x18_build_data_map( const char *text, size_t text_size )"),
             function(vi, "size_t ios_jit_x18_tramp_need( const char *text, size_t text_size, int stores )"),
             patcher]
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/mman.h>
#if defined(__APPLE__)
#include <libkern/OSCacheControl.h>
#endif
#define ERR(...) fprintf(stderr, __VA_ARGS__)
int ios_teb_tls_slot_offset;
@PARTS@
#define STR_X18_X0  0xF9000012u   /* str x18, [x0] */
#define RET         0xD65F03C0u
static int fails;
#define EXPECT(c, ...) do { if (!(c)) { fprintf(stderr, __VA_ARGS__); fails++; } } while (0)

int main(int argc, char **argv)
{
    /* 1. budget */
    uint32_t sites[] = { STR_X18_X0, 0xA90007F2u /* stp x18, x1, [sp] */, 0xF81F8012u /* stur x18, [x0, #-8] */,
                         0xF9400252u /* ldr x18, [x18]: an old-form site */, 0xD503201Fu };
    size_t n0 = ios_jit_x18_tramp_need((const char *)sites, sizeof sites, 0);
    size_t n1 = ios_jit_x18_tramp_need((const char *)sites, sizeof sites, 1);
    printf("need %zu %zu\n", n0, n1);

    /* 2 + 3. patch a two-word function (str x18, [x0] ; ret) with its trampoline right behind it, then call it */
    size_t pg = (size_t)getpagesize();
    int flags = MAP_PRIVATE | MAP_ANON;
#if defined(__APPLE__) && defined(MAP_JIT)
    flags |= MAP_JIT;
#endif
    char *mem = mmap(NULL, pg, PROT_READ | PROT_WRITE | PROT_EXEC, flags, -1, 0);
    if (mem == MAP_FAILED) { printf("nojit\n"); return fails ? 1 : 0; }
#if defined(__APPLE__) && defined(MAP_JIT)
    pthread_jit_write_protect_np(0);
#endif
    uint32_t *text = (uint32_t *)mem, *tramp = (uint32_t *)(mem + 64);
    text[0] = STR_X18_X0; text[1] = RET;
    pthread_key_t key;
    pthread_key_create(&key, NULL);
    ios_teb_tls_slot_offset = (int)(key * 8);
    int patched = ios_jit_patch_x18((char *)text, (char *)text, 8, (char *)tramp, (char *)tramp, 128, 1);
    printf("patched %d text0 %08x\n", patched, text[0]);
    printf("words");
    for (int i = 0; i < 12; i++) printf(" %08x", tramp[i]);
    printf("\n");
    FILE *f = fopen(argv[1], "wb"); fwrite(tramp, 4, 10, f); fclose(f);
#if defined(__APPLE__) && defined(MAP_JIT)
    pthread_jit_write_protect_np(1);
    sys_icache_invalidate(mem, pg);
#else
    __builtin___clear_cache(mem, mem + pg);
#endif
#if defined(__aarch64__) && defined(__APPLE__)
    void (*fn)(uint64_t *) = (void (*)(uint64_t *))(void *)mem;
    static int teb_stand_in;
    uint64_t out = 0x5555;
    pthread_setspecific(key, &teb_stand_in);
    for (int i = 0; i < 100000; i++) { out = 0x5555; fn(&out); if (out != (uint64_t)(uintptr_t)&teb_stand_in) break; }
    printf("run %d\n", out == (uint64_t)(uintptr_t)&teb_stand_in);
    pthread_setspecific(key, NULL);
    out = 0x5555; fn(&out);
    printf("zero %d\n", out == 0);
#else
    printf("run skip\nzero skip\n");
#endif

    /* 4. stores = 0: a str x18 is not a site at all */
    char *mem2 = malloc(256);
    uint32_t *t2 = (uint32_t *)mem2;
    t2[0] = STR_X18_X0; t2[1] = RET;
    int p0 = ios_jit_patch_x18((char *)t2, (char *)t2, 8, mem2 + 64, mem2 + 64, 128, 0);
    printf("stores0 %d %08x\n", p0, t2[0]);
    return fails ? 1 : 0;
}
'''.replace("@PARTS@", "".join(parts))
    with tempfile.TemporaryDirectory(prefix="x18-store-") as d:
        d = Path(d)
        (d / "t.c").write_text(harness)
        exe = d / "t"
        r = subprocess.run([cc, "-std=gnu11", "-O1", "-w", str(d / "t.c"), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the patcher compiles on the host", r.stderr[-2000:])
        if r.returncode == 0:
            r = subprocess.run([str(exe), str(d / "tramp.bin")], capture_output=True, text=True, timeout=60)
            out = dict(l.split(" ", 1) for l in r.stdout.splitlines() if " " in l)
            check(out.get("need") == "%d %d" % (32 * 1 + 0x1000, 32 * 1 + 40 * 3 + 0x1000),
                  "budget: 40 bytes for each of the three store sites with stores = 1, 32 for the old site, the page of slack", out.get("need"))
            m = re.match(r"(\d+) text0 ([0-9a-f]{8})", out.get("patched", ""))
            check(m is not None and m.group(1) == "1" and int(m.group(2), 16) >> 26 == 0x5,
                  "the store is replaced by one B to its trampoline", out.get("patched"))
            words = [int(w, 16) for w in out.get("words", "").split()]
            if len(words) >= 10:
                key_slot = words[2]
                one = [0xD53BD060 | 18, 0x927DF000 | (18 << 5) | 18, key_slot, 0xF9000012]
                check(words[0:4] == one and words[5:9] == one and (words[2] & 0xFFC003FF) == 0xF9400252,
                      "two tries: mrs / and / ldr slot / the store, twice", ["%08x" % w for w in words[:10]])
                check(words[4] == (0xB5000000 | (5 << 5) | 18), "between them: cbnz x18, +5 words (to the branch back)", "%08x" % words[4])
                back = words[9]
                imm26 = back & 0x3FFFFFF
                delta = (imm26 - (1 << 26) if imm26 & (1 << 25) else imm26) * 4
                check(back >> 26 == 0x5 and 64 + 9 * 4 + delta == 4, "the last word branches back to the instruction after the store", "%08x" % back)
            objdump = shutil.which("llvm-objdump") or (subprocess.run(["xcrun", "-f", "llvm-objdump"], capture_output=True, text=True).stdout.strip()
                                                      if shutil.which("xcrun") else "")
            if objdump and (d / "tramp.bin").exists():
                dis = subprocess.run([objdump, "-D", "-b", "binary", "--triple=aarch64", str(d / "tramp.bin")], capture_output=True, text=True)
                if dis.returncode != 0:
                    dis = subprocess.run([objdump, "-D", "--triple=aarch64", "--arch=aarch64", "-b", "binary", str(d / "tramp.bin")],
                                         capture_output=True, text=True)
                txt = dis.stdout
                if "cbnz" in txt:
                    check(txt.count("mrs\tx18, TPIDRRO_EL0") == 2 and txt.count("str\tx18, [x0]") == 2 and re.search(r"cbnz\tx18, 0x24", txt) is not None,
                          "llvm-objdump reads the same: two TEB loads, two stores, cbnz x18 to offset 0x24 (the branch back)", txt[-1500:])
                else:
                    print("skip: llvm-objdump could not read the raw words here")
            if platform.machine() == "arm64" and sys.platform == "darwin":
                check(out.get("run") == "1", "run on this Mac: 100,000 calls each stored the slot's value (the TEB stand-in)", out.get("run"))
                check(out.get("zero") == "1", "a slot holding 0: the store writes 0 and the call returns (no loop)", out.get("zero"))
            else:
                print("skip: not an arm64 Mac, the trampoline is not run")
            check(out.get("stores0", "").startswith("0 f9000012"), "stores = 0: str x18 is no site, the text is unchanged", out.get("stores0"))
            if r.returncode != 0:
                check(False, "the harness ran", r.stderr[-1500:])

print(("PASS" if not failures else "FAILED") + ": a store of x18 takes two tries (opengl32, gl-backend sessions)")
sys.exit(1 if failures else 0)
