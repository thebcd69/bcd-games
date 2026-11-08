#!/usr/bin/env python3
"""Compile the production WoW reservation paths against a non-replacing VM model.

Fault injection checks split guard/body reservations, cleanup and collisions;
the existing combined, borrowed-guard and placeholder paths remain covered.
This is a host contract check, not an iOS kernel or guest integration test.

env.MADEIRA_WOW_GUARD_INSIDE = 1 (upstream Madeira v0.2.0, ml2020; BCD: default off): the same production functions run a second time in
a child process with the key on (the switch is read once): a window reserves exactly 4 GB in one mapping and no guard page is
mapped, owned or borrowed (guard_owned 0), the large-address-aware ceiling drops by one host page, a 2 GB ceiling and the 4 GB
placeholder adoption stay as they are, and the high-side placement of map_view keeps the first aligned GB only with the key on. With
the key unset every scenario above is byte for byte the old one.
"""
from pathlib import Path
import os
import subprocess
import tempfile


root = Path(__file__).resolve().parents[2]
source = (root / "build/ntdll-unix/virtual_ios.c").read_text()


def function(signature):
    start = source.index(signature)
    return source[start:source.index("\n}", start) + 2]


production = "\n\n".join(function(signature) for signature in (
    "static ULONG_PTR ios_wow_guard_size(",
    "static int ios_wow_guard_inside(",
    "static BOOL ios_laa_forced(",
    "static ULONG_PTR ios_wow_ceiling_for_charact(",
    "static ULONG_PTR ios_wow_slot_reservation(",
    "static int ios_wow_band_ok(",
    "static void ios_va_describe_range(",
    "static int ios_wow_guard_neighbour_blocked(",
    "static int ios_wow_window_try_separate_guard(",
    "static int ios_wow_child_in_hold_folder(",
    "static int ios_wow_window_try(",
))

harness = r"""
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

typedef uintptr_t ULONG_PTR;
typedef unsigned short WORD;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define IMAGE_FILE_LARGE_ADDRESS_AWARE 0x0020
static const ULONG_PTR limit_4g = (ULONG_PTR)1 << 32, limit_2g = (ULONG_PTR)1 << 31;
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
typedef unsigned mach_msg_type_number_t;
typedef int mach_port_t;
typedef void *vm_region_info_t;
typedef struct { int protection, max_protection, shared; } vm_region_basic_info_data_64_t;
#define KERN_SUCCESS 0
#define VM_REGION_BASIC_INFO_64 1
#define VM_REGION_BASIC_INFO_COUNT_64 1
#define VM_PROT_NONE 0
#define MACH_PORT_NULL 0
#define IOS_WOW_WINDOW_SIZE ((ULONG_PTR)1 << 32)
#define IOS_WOW_CEF_POOLS_START ((ULONG_PTR)0x7400000000)
#define IOS_WOW_FEX_BAND_END ((ULONG_PTR)0x8000000000)
#define IOS_WOW_SPILL_TOP ((ULONG_PTR)0x7b00000000)
static int ios_wow_spill_walk;
static const ULONG_PTR host_page_size = 0x4000;
struct ios_wow_window { ULONG_PTR base; unsigned dead, leaked, guard_owned; };
struct ios_wow_placeholder { unsigned adopted, guard_owned, for_game; };
static struct ios_wow_window slot;
static struct ios_wow_placeholder placeholder;
static int have_slot, have_placeholder;
static ULONG_PTR base;
static char trace[64], logs[4096];
static size_t trace_len, log_len;
static int refuse_combined, refuse_guard, refuse_body, refuse_unmap;
static int reserved_count;
static ULONG_PTR reserved_size;
struct region { ULONG_PTR lo, size; int prot, owned; };
static struct region regions[8];
static unsigned region_count;

static void event(char name)
{
    assert(trace_len + 1 < sizeof(trace));
    trace[trace_len++] = name; trace[trace_len] = 0;
}

static void seed(ULONG_PTR lo, ULONG_PTR size, int prot, int owned)
{
    assert(region_count < sizeof(regions) / sizeof(regions[0]));
    regions[region_count++] = (struct region){ lo, size, prot, owned };
}

static void *anon_mmap_tryfixed(void *addr, size_t size, int prot, int flags)
{
    ULONG_PTR lo = (ULONG_PTR)addr;
    char name = size == IOS_WOW_WINDOW_SIZE + host_page_size ? 'C' :
                size == IOS_WOW_WINDOW_SIZE ? 'B' : 'G';
    assert(prot == PROT_NONE && flags == MAP_NORESERVE);
    assert(size == IOS_WOW_WINDOW_SIZE + host_page_size ||
           size == IOS_WOW_WINDOW_SIZE || size == host_page_size);
    assert(lo == base + (name == 'G' ? IOS_WOW_WINDOW_SIZE : 0));
    event(name);
    if ((name == 'C' && refuse_combined) || (name == 'G' && refuse_guard) ||
        (name == 'B' && refuse_body)) return MAP_FAILED;
    for (unsigned i = 0; i < region_count; ++i)
        if (lo < regions[i].lo + regions[i].size && regions[i].lo < lo + size)
            return MAP_FAILED;
    seed(lo, size, prot, 1);
    return addr;
}

static int test_unmap(void *addr, size_t size)
{
    event('U');
    assert((ULONG_PTR)addr == base + IOS_WOW_WINDOW_SIZE && size == host_page_size);
    for (unsigned i = 0; i < region_count; ++i)
        if (regions[i].lo == (ULONG_PTR)addr && regions[i].size == size)
        {
            assert(regions[i].owned && regions[i].prot == PROT_NONE);
            if (refuse_unmap) { errno = EINVAL; return -1; }
            regions[i] = regions[--region_count];
            return 0;
        }
    assert(!"unmapping a page the attempt does not own");
    return -1;
}
#define munmap test_unmap

static void mmap_add_reserved_area(void *addr, size_t size)
{
    event('R');
    assert((ULONG_PTR)addr == base &&
           (size == IOS_WOW_WINDOW_SIZE || size == IOS_WOW_WINDOW_SIZE + host_page_size));
    reserved_count++; reserved_size = size;
}

static int mach_task_self(void) { return 1; }
static int mach_vm_region(int task, mach_vm_address_t *addr, mach_vm_size_t *size,
                          int flavor, vm_region_info_t out,
                          mach_msg_type_number_t *count, mach_port_t *object)
{
    int found = -1;
    (void)task; (void)flavor; (void)count; (void)object;
    for (unsigned i = 0; i < region_count; ++i)
        if (regions[i].lo + regions[i].size > *addr &&
            (found < 0 || regions[i].lo < regions[found].lo)) found = (int)i;
    if (found < 0) return 1;
    *addr = regions[found].lo; *size = regions[found].size;
    *(vm_region_basic_info_data_64_t *)out =
        (vm_region_basic_info_data_64_t){ regions[found].prot, regions[found].prot, 0 };
    return KERN_SUCCESS;
}

static int test_dprintf(int fd, const char *fmt, ...)
{
    va_list args;
    (void)fd;
    va_start(args, fmt);
    int n = vsnprintf(logs + log_len, sizeof(logs) - log_len, fmt, args);
    va_end(args);
    assert(n >= 0 && (size_t)n < sizeof(logs) - log_len);
    log_len += (size_t)n;
    return n;
}
#define dprintf test_dprintf

static char ios_wow_hold_for[260];
static const char *test_image;
const char *ios_child_image_path(void) { return test_image; }
static struct ios_wow_window *ios_wow_slot_at_base(ULONG_PTR addr)
{ assert(addr == base); return have_slot ? &slot : NULL; }
static struct ios_wow_placeholder *ios_wow_placeholder_find(ULONG_PTR addr)
{ assert(addr == base); return have_placeholder ? &placeholder : NULL; }
""" + production + r"""

static void reset(void)
{
    base = 0x7200000000;
    region_count = 0; trace_len = 0; log_len = 0;
    trace[0] = logs[0] = 0;
    refuse_combined = refuse_guard = refuse_body = refuse_unmap = 0;
    reserved_count = 0; reserved_size = 0;
    have_slot = have_placeholder = ios_wow_spill_walk = 0;
    memset(&slot, 0, sizeof(slot)); memset(&placeholder, 0, sizeof(placeholder));
}

static int main_inside(void)
{
    unsigned owned;

    assert(ios_wow_guard_inside());
    /* an empty map: exactly one 4 GB mapping, one reserved area of 4 GB, no guard page mapped, owned or borrowed */
    reset();
    assert(ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "BR") && region_count == 1 && reserved_count == 1);
    assert(regions[0].lo == base && regions[0].size == IOS_WOW_WINDOW_SIZE && reserved_size == IOS_WOW_WINDOW_SIZE);
    assert(strstr(logs, "reserved exactly 4GB; the overrun guard is the window's top page"));
    /* the page above the window is not looked at (accessible, inaccessible or free): the old paths needed it */
    reset(); seed(base + IOS_WOW_WINDOW_SIZE, host_page_size, PROT_READ, 0);
    assert(ios_wow_window_try(base, &owned) && owned == 0 && !strcmp(trace, "BR") && reserved_size == IOS_WOW_WINDOW_SIZE);
    reset(); seed(base + IOS_WOW_WINDOW_SIZE, host_page_size, PROT_NONE, 0);
    assert(ios_wow_window_try(base, &owned) && owned == 0 && !strcmp(trace, "BR") && reserved_size == IOS_WOW_WINDOW_SIZE);
    /* a gap of exactly 4 GB is enough (no "4 GB + guard" needed) */
    reset(); seed(base - 0x10000, 0x10000, PROT_READ, 0); seed(base + IOS_WOW_WINDOW_SIZE, 0x10000, PROT_READ, 0);
    assert(ios_wow_window_try(base, &owned) && owned == 0 && !strcmp(trace, "BR"));
    /* a refused body, a region inside it, one at its first byte: rejected after ONE attempt, nothing reserved */
    reset(); refuse_body = 1;
    assert(!ios_wow_window_try(base, &owned) && owned == 0 && !strcmp(trace, "B") && !region_count && !reserved_count);
    assert(strstr(logs, "REJECTED: the 4GB range is not free"));
    reset(); seed(base + IOS_WOW_WINDOW_SIZE / 2, host_page_size, PROT_READ, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0 && !strcmp(trace, "B") && region_count == 1 && !reserved_count);
    assert(strstr(logs, "PARTIALLY OCCUPIED"));
    reset(); seed(base, 0x800000, PROT_READ | PROT_WRITE, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0 && !strcmp(trace, "B") && region_count == 1 && !reserved_count);
    /* the order of the gates is unchanged: a slot in use, an adopted or adoptable placeholder, the band bounds */
    for (unsigned state = 0; state < 3; ++state)
    {
        reset(); have_slot = 1; slot.dead = state == 1; slot.leaked = state == 2;
        assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len && !region_count && !reserved_count);
    }
    for (unsigned guard_owned = 0; guard_owned < 2; ++guard_owned)
    {
        reset(); have_placeholder = 1; placeholder.guard_owned = guard_owned;
        assert(ios_wow_window_try(base, &owned) && owned == guard_owned && placeholder.adopted && !trace_len && !region_count);
        assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    }
    reset(); base = IOS_WOW_CEF_POOLS_START;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    reset(); base = IOS_WOW_CEF_POOLS_START - IOS_WOW_WINDOW_SIZE;
    assert(ios_wow_window_try(base, &owned) && !owned && !strcmp(trace, "BR"));
    /* the last slot of the spill range: the old paths needed 4 GB + guard below the range's top and refused it, exactly 4 GB fits */
    reset(); ios_wow_spill_walk = 1; base = IOS_WOW_SPILL_TOP - IOS_WOW_WINDOW_SIZE;
    assert(ios_wow_window_try(base, &owned) && !owned && !strcmp(trace, "BR") && reserved_size == IOS_WOW_WINDOW_SIZE);
    reset(); ios_wow_spill_walk = 1; base = IOS_WOW_SPILL_TOP;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    reset(); base = UINTPTR_MAX - IOS_WOW_WINDOW_SIZE;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    /* the guest ceiling: a large-address-aware image (or the forced policy) loses its top host page, a 2 GB ceiling is untouched */
    assert(ios_wow_ceiling_for_charact(IMAGE_FILE_LARGE_ADDRESS_AWARE) == limit_4g - host_page_size - 1);
    assert(ios_wow_ceiling_for_charact(0) == limit_4g - host_page_size - 1);   /* MADEIRA_LAA unset: forced 4 GB */
    puts("PASS (guard inside): exactly 4 GB, no guard page, the ceiling one host page short");
    return 0;
}

static int main_laa_off(void)
{
    /* MADEIRA_LAA=0 with the key on: a program that is not large-address-aware keeps 2 GB - 1, exactly as without the key */
    assert(ios_wow_guard_inside());
    assert(ios_wow_ceiling_for_charact(0) == limit_2g - 1);
    assert(ios_wow_ceiling_for_charact(IMAGE_FILE_LARGE_ADDRESS_AWARE) == limit_4g - host_page_size - 1);
    puts("PASS (guard inside, MADEIRA_LAA=0): a 2 GB program keeps its ceiling");
    return 0;
}

int main(int argc, char **argv)
{
    unsigned owned;
    if (argc > 1 && !strcmp(argv[1], "inside")) return main_inside();
    if (argc > 1 && !strcmp(argv[1], "inside-laa0")) return main_laa_off();
    assert(!ios_wow_guard_inside());   /* the key unset: the old behaviour in every scenario below */
    assert(ios_wow_ceiling_for_charact(IMAGE_FILE_LARGE_ADDRESS_AWARE) == limit_4g - 1);
    assert(ios_wow_ceiling_for_charact(0) == limit_4g - 1);   /* MADEIRA_LAA unset: forced 4 GB */
    reset();
    assert(ios_wow_window_try(base, &owned) && owned == 1);
    assert(!strcmp(trace, "CR") && region_count == 1 && reserved_count == 1);
    assert(reserved_size == IOS_WOW_WINDOW_SIZE + host_page_size);

    reset(); refuse_combined = 1;
    assert(ios_wow_window_try(base, &owned) && owned == 1);
    assert(!strcmp(trace, "CGBR") && region_count == 2 && reserved_count == 1);
    assert(regions[0].lo == base + IOS_WOW_WINDOW_SIZE && regions[0].size == host_page_size);
    assert(regions[1].lo == base && regions[1].size == IOS_WOW_WINDOW_SIZE);
    assert(reserved_size == IOS_WOW_WINDOW_SIZE + host_page_size);

    reset(); refuse_combined = refuse_body = 1;
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CGBU") && !region_count && !reserved_count);
    assert(strstr(logs, "window body after failed reservations") &&
           strstr(logs, "free to end of VA"));

    reset(); seed(base + IOS_WOW_WINDOW_SIZE / 2, host_page_size, PROT_READ, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CGBU") && region_count == 1 && !reserved_count);
    assert(!regions[0].owned && regions[0].prot == PROT_READ);
    assert(strstr(logs, "PARTIALLY OCCUPIED"));

    reset(); seed(base, 0x800000, PROT_READ | PROT_WRITE, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CGBU") && region_count == 1 && !reserved_count);
    assert(regions[0].lo == base && regions[0].size == 0x800000 && !regions[0].owned);
    assert(strstr(logs, "OCCUPIED"));

    reset(); seed(base + IOS_WOW_WINDOW_SIZE, host_page_size, PROT_NONE, 0);
    assert(ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CGBR") && region_count == 2 && reserved_count == 1);
    assert(!regions[0].owned && reserved_size == IOS_WOW_WINDOW_SIZE);

    reset(); seed(base + IOS_WOW_WINDOW_SIZE, host_page_size, PROT_READ, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CG") && region_count == 1 && !reserved_count);
    assert(regions[0].prot == PROT_READ && !regions[0].owned);

    reset(); seed(base + IOS_WOW_WINDOW_SIZE, host_page_size / 2, PROT_NONE, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CG") && region_count == 1 && !reserved_count);

    reset(); refuse_combined = refuse_guard = 1;
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CG") && !region_count && !reserved_count);

    reset(); seed(base + IOS_WOW_WINDOW_SIZE, host_page_size, PROT_NONE, 0);
    seed(base + 0x10000, host_page_size, PROT_READ, 0);
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CGB") && region_count == 2 && !reserved_count);
    assert(strstr(logs, "PARTIALLY OCCUPIED"));

    reset(); refuse_combined = refuse_body = refuse_unmap = 1;
    assert(!ios_wow_window_try(base, &owned) && owned == 0);
    assert(!strcmp(trace, "CGBU") && region_count == 1 && !reserved_count);
    assert(regions[0].owned && strstr(logs, "failed to release own guard page"));

    for (unsigned state = 0; state < 3; ++state)
    {
        reset(); have_slot = 1; slot.dead = state == 1; slot.leaked = state == 2;
        assert(!ios_wow_window_try(base, &owned) && !owned);
        assert(!trace_len && !region_count && !reserved_count);
    }
    for (unsigned guard_owned = 0; guard_owned < 2; ++guard_owned)
    {
        reset(); have_placeholder = 1; placeholder.guard_owned = guard_owned;
        assert(ios_wow_window_try(base, &owned) && owned == guard_owned && placeholder.adopted);
        assert(!trace_len && !region_count && !reserved_count);
        assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
        slot.guard_owned = guard_owned;
        assert(ios_wow_slot_reservation(&slot) == IOS_WOW_WINDOW_SIZE +
               (guard_owned ? host_page_size : 0));
    }

    /* a placeholder held for the game's folder (upstream #549, MADEIRA_WOW_HOLD_FOR): only a program from there adopts it */
    strcpy(ios_wow_hold_for, "c:\\program files (x86)\\steam\\steamapps\\common\\gta iv\\");
    for (unsigned guard_owned = 0; guard_owned < 2; ++guard_owned)
    {
        reset(); have_placeholder = 1; placeholder.guard_owned = guard_owned; placeholder.for_game = 1;
        test_image = "C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe";
        assert(!ios_wow_window_try(base, &owned) && owned == 0 && !placeholder.adopted && !trace_len);
        assert(strstr(logs, "REJECTED: its placeholder is held for the game's folder") && strstr(logs, "SteamService.exe is outside it"));
        test_image = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\GTA IV\\GTAIV.exe";
        assert(ios_wow_window_try(base, &owned) && owned == guard_owned && placeholder.adopted && !trace_len && !region_count);
        reset(); have_placeholder = 1; placeholder.guard_owned = guard_owned; placeholder.for_game = 1;
        test_image = NULL;   /* a child the process table does not know is not refused */
        assert(ios_wow_window_try(base, &owned) && placeholder.adopted);
        reset(); have_placeholder = 1; placeholder.guard_owned = guard_owned;   /* not held for the game: any program */
        test_image = "C:\\somewhere\\else.exe";
        assert(ios_wow_window_try(base, &owned) && placeholder.adopted);
    }
    ios_wow_hold_for[0] = 0; test_image = NULL;

    reset(); base = IOS_WOW_CEF_POOLS_START;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    reset(); base = IOS_WOW_CEF_POOLS_START - IOS_WOW_WINDOW_SIZE;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    seed(base + IOS_WOW_WINDOW_SIZE, host_page_size, PROT_NONE, 0);
    assert(ios_wow_window_try(base, &owned) && !owned && !strcmp(trace, "BR"));
    reset(); ios_wow_spill_walk = 1; base = IOS_WOW_SPILL_TOP - IOS_WOW_WINDOW_SIZE;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    reset(); base = UINTPTR_MAX - IOS_WOW_WINDOW_SIZE;
    assert(!ios_wow_window_try(base, &owned) && !owned && !trace_len);
    assert(!ios_wow_window_try_separate_guard(base, UINTPTR_MAX) && !trace_len);

    puts("PASS: combined/split/borrowed guards, collision cleanup, ownership, placeholders and band bounds");
    return 0;
}
"""

with tempfile.TemporaryDirectory(prefix="madeira-wow-guard-") as tmp:
    c = Path(tmp) / "check.c"
    executable = Path(tmp) / "check"
    c.write_text(harness)
    command = [os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    # Linux PIE/ASan address placement can collide on this cloud host.
    if os.uname().sysname == "Linux":
        command.append("-no-pie")
    subprocess.run(command + [str(c), "-o", str(executable)], check=True)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
    subprocess.run([str(executable)], check=True, env=clean)
    # the key on (one process each: the switch is read once)
    subprocess.run([str(executable), "inside"], check=True, env=dict(clean, MADEIRA_WOW_GUARD_INSIDE="1"))
    subprocess.run([str(executable), "inside-laa0"], check=True, env=dict(clean, MADEIRA_WOW_GUARD_INSIDE="1", MADEIRA_LAA="0"))
    # anything but "1" is off
    for value in ("0", "", "11", "on", "yes"):
        subprocess.run([str(executable)], check=True, env=dict(clean, MADEIRA_WOW_GUARD_INSIDE=value))

# the sources: the key sits in four places, one each, and the high side keeps its old order with the key off
ceiling_fn = function("static ULONG_PTR ios_wow_ceiling_for_charact(")
check_ok = ("if (laa && ios_wow_guard_inside()) return limit_4g - ios_wow_guard_size() - 1;" in ceiling_fn and
            ceiling_fn.rindex("ios_wow_guard_inside()") < ceiling_fn.rindex("return (laa ? limit_4g : limit_2g) - 1;"))
assert check_ok, "the ceiling hook"
assert source.count("ios_wow_guard_inside()") == 3, source.count("ios_wow_guard_inside()")   # the calls: ceiling, window_try, map_view
hi = source[source.index("void *hs = wow_hi_start;"):]
hi = hi[:hi.index("if (++hi_n <= 16")]
assert "if (ios_wow_guard_inside() && ios_jumbo_aligned_enabled() && !((ULONG_PTR)hs & 0xffffffffULL) &&" in hi
assert "hs = (char *)hs + 0x40000000ULL;" in hi and hi.count("map_reserved_area(") == 2 and hi.count("map_free_area(") == 2
assert hi.index("ptr = map_reserved_area( hs, wow_hi_end") < hi.index("if (!ptr && hs != wow_hi_start)")
print("PASS (sources): the key is read in the ceiling, the window reservation and the high-side placement only")
