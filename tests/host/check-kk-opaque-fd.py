#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""In-process opaque fds on KosmicKrisp for X-Plane 12's Vulkan/OpenGL sharing (round 669; env.MADEIRA_KK_OPAQUE_FD = 1).

X-Plane 12's Windows renderer requires a Vulkan device that shares memory and semaphores with the OpenGL context of its plugins
(its Log.txt: "cannot run Vulkan X-Plane. X-Plane requires any of: baseline_desktop_win32, baseline_desktop_linux"). Wine builds
VK_KHR_external_memory_win32 / _semaphore_win32 on the host driver's opaque fds, and the OpenGL side imports them with Zink's
GL_EXT_memory_object_fd / _semaphore_fd. KosmicKrisp has no fds for Metal objects, so build/mesa-ios/playport-patches/0032 makes
in-process ones: the fd of an unlinked temporary file that holds a key naming the MTLHeap or MTLSharedEvent.

  1. the patch: BCD's header, every new path behind kk_opaque_fd_enabled() (the extension, the semaphore types, the memory import,
     the buffer and image properties), the old types used as they are without the key, a shared binary point instead of each
     side's own, images on such memory not losslessly compressed;
  2. the registry (kk_opaque_fd.c, cut out of the patch and compiled on the host with stand-ins for Metal's retain/release and the
     binary point): an export's fd, a dup of it and the copy that comes back through a unix socket (as through wineserver) name
     the same object, each lookup retains it once and counts the shared point once, another kind or a foreign fd names nothing,
     forget drops exactly the owner's entries and releases them, the table refuses the 1025th live handle;
  3. Wine (build/win32u-unix/vulkan_surface_ios.c, the function compiled on the host): without the key nothing is mapped; with
     MADEIRA_KK_OPAQUE_FD=1 memory and semaphore fds and their win32 forms map both ways, fences not;
  4. Zink (build/win32u-unix/opengl_ios.c): MADEIRA_ZINK_VULKAN=kosmickrisp takes the session's KosmicKrisp only when
     MADEIRA_VULKAN_LIBRARY names it, else MoltenVK with a line that says why;
  5. the list policy, the catalog, the X-Plane 12 row, the auto-tune rule and CI.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
P = R / "build/mesa-ios/playport-patches/0032-kk-in-process-opaque-fd-memory-and-semaphores.patch"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:1500]))
    ok = ok and bool(cond)


patch = P.read_text()


def new_file(name):
    """The full text of a file the patch adds."""
    m = re.search(r"^\+\+\+ b/(\S*" + re.escape(name) + r")\n@@ -0,0 \+1,\d+ @@\n((?:\+.*\n)+)", patch, re.M)
    return "".join(l[1:] + "\n" for l in m.group(2).splitlines()) if m else ""


def added(path):
    """The + lines of one file's hunks."""
    m = re.search(r"^\+\+\+ b/" + re.escape(path) + r"\n(.*?)(?=^diff |\Z)", patch, re.M | re.S)
    return "".join(l[1:] + "\n" for l in (m.group(1).splitlines() if m else []) if l.startswith("+"))


# 1. the patch
check("0020: BCD's mail header and subject", patch.startswith("From: BCD <190844990+thebcd69@users.noreply.github.com>\n"
      "Subject: [PATCH] kk: in-process opaque fd memory and semaphores (MADEIRA_KK_OPAQUE_FD=1)"))
reg_h, reg_c = new_file("kk_opaque_fd.h"), new_file("kk_opaque_fd.c")
check("0020 adds kk_opaque_fd.h/.c (MIT, BCD) and builds the .c", reg_h.startswith("/*\n * Copyright 2026 BCD contributors\n * SPDX-License-Identifier: MIT")
      and reg_c.startswith("/*\n * Copyright 2026 BCD contributors\n * SPDX-License-Identifier: MIT") and "+  'kk_opaque_fd.c',\n" in patch)
pd = added("src/kosmickrisp/vulkan/kk_physical_device.c")
check("the extension and the semaphore types only with the key; without it the old types as they are",
      ".KHR_external_memory_fd = kk_opaque_fd_enabled()," in pd
      and "const struct vk_sync_type *timeline_type = &kk_sync_type;\n   if (kk_opaque_fd_enabled()) {" in pd
      and "pdev->sync_binary_type = vk_sync_binary_get_type(timeline_type);\n   if (kk_opaque_fd_enabled()) {" in pd
      and "pdev->sync_types[st_idx++] = timeline_type;" in pd)
mem = added("src/kosmickrisp/vulkan/kk_device_memory.c")
check("memory: an opaque fd import needs the key and a known handle, the heap comes retained, a successful import closes the fd",
      "if (!kk_opaque_fd_enabled() ||\n          !kk_ofd_lookup(fd_info->fd, KK_OFD_HEAP, &ofd_heap, NULL, NULL))\n         return vk_error(dev, VK_ERROR_INVALID_EXTERNAL_HANDLE);" in mem
      and "mem->bo->mtl_handle = ofd_heap ? ofd_heap : mtl_retain(metal_info->handle);" in mem
      and "if (ofd_heap)\n      close(fd_info->fd);" in mem and "kk_ofd_forget(mem->bo);" in mem
      and "kk_GetMemoryFdKHR" in mem)
check("buffers and images offer the opaque fd type only with the key",
      "case VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT:   /* madeira-bcd */\n      if (!kk_opaque_fd_enabled())\n         goto unsupported;" in added("src/kosmickrisp/vulkan/kk_buffer.c")
      and "if (!kk_opaque_fd_enabled())\n            return vk_errorf(pdev, VK_ERROR_FORMAT_NOT_SUPPORTED," in added("src/kosmickrisp/vulkan/kk_image.c"))
check("an image on opaque fd memory is not losslessly compressed (both devices place it alike)",
      "if (image->external_handle_types &\n       VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT)\n      layout->optimized_layout = false;" in added("src/kosmickrisp/vulkan/kk_image_layout.c"))
bin_c, sync_c = added("src/vulkan/runtime/vk_sync_binary.c"), added("src/vulkan/runtime/vk_sync.c")
check("a shared binary payload counts one point: the wait reads it, the signal increments it atomically, a reset too",
      "wait->wait_value = p_atomic_read(vk_sync_binary_point(binary));" in sync_c
      and "signal->signal_value = p_atomic_inc_return(vk_sync_binary_point(binary));" in sync_c
      and "p_atomic_inc(vk_sync_binary_point(binary));" in bin_c
      and "binary->shared ? &binary->shared->point : &binary->next_point" in added("src/vulkan/runtime/vk_sync_binary.h")
      and "if (p_atomic_dec_zero(&shared->refs))\n      free(shared);" in bin_c)
ks = added("src/kosmickrisp/vulkan/kk_sync.c")
check("semaphores: a timeline and a binary payload are not mixed up, an import closes the fd",
      ks.count("close(fd);") == 2 and "a binary semaphore's payload cannot become a timeline" in ks
      and "a timeline's payload has no binary point" in ks and "vk_sync_binary_share(binary, shared);" in ks)

# 2. the registry, compiled on the host
cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("SKIP the registry harness: no C compiler")
else:
    stubs = r'''
#include <pthread.h>
#include <stdint.h>
typedef pthread_mutex_t simple_mtx_t;
#define SIMPLE_MTX_INITIALIZER PTHREAD_MUTEX_INITIALIZER
#define simple_mtx_lock pthread_mutex_lock
#define simple_mtx_unlock pthread_mutex_unlock
struct vk_sync_binary_shared { uint64_t point; uint32_t refs; };
static int retains, releases, shared_refs, shared_unrefs;
static void *mtl_retain(void *o) { retains++; return o; }
static void mtl_release(void *o) { (void)o; releases++; }
static void vk_sync_binary_shared_ref(struct vk_sync_binary_shared *s) { s->refs++; shared_refs++; }
static void vk_sync_binary_shared_unref(struct vk_sync_binary_shared *s) { s->refs--; shared_unrefs++; }
'''
    body = reg_c.replace('#include "kk_opaque_fd.h"', reg_h.split("#define KK_OPAQUE_FD_H 1", 1)[1].rsplit("#endif", 1)[0])
    for inc in ('#include "kosmickrisp/bridge/mtl_bridge.h"', '#include "vk_sync_binary.h"', '#include "util/simple_mtx.h"'):
        body = body.replace(inc, "")
    main = r'''
#include <assert.h>
#include <sys/socket.h>
#include <sys/uio.h>
static int fails;
#define T(c, w) do { if (!(c)) { printf("FAIL %s\n", w); fails++; } } while (0)
static int pass_fd(int fd)   /* through a unix socket and back, as wineserver keeps and returns it */
{
    int sv[2], out = -1; char b = 'x'; struct iovec io = { &b, 1 };
    char c1[CMSG_SPACE(sizeof(int))], c2[CMSG_SPACE(sizeof(int))];
    struct msghdr m = { 0 }, r = { 0 };
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) return -1;
    m.msg_iov = &io; m.msg_iovlen = 1; m.msg_control = c1; m.msg_controllen = sizeof(c1);
    CMSG_FIRSTHDR(&m)->cmsg_level = SOL_SOCKET; CMSG_FIRSTHDR(&m)->cmsg_type = SCM_RIGHTS; CMSG_FIRSTHDR(&m)->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(CMSG_FIRSTHDR(&m)), &fd, sizeof(int));
    if (sendmsg(sv[0], &m, 0) != 1) return -1;
    r.msg_iov = &io; r.msg_iovlen = 1; r.msg_control = c2; r.msg_controllen = sizeof(c2);
    if (recvmsg(sv[1], &r, 0) == 1 && CMSG_FIRSTHDR(&r)) memcpy(&out, CMSG_DATA(CMSG_FIRSTHDR(&r)), sizeof(int));
    close(sv[0]); close(sv[1]);
    return out;
}
int main(int argc, char **argv)
{
    int heap_owner = 0, event_owner = 0, heap = 0x1111, event = 0x2222, other = 0x3333;
    struct vk_sync_binary_shared shared = { 5, 1 };
    void *got = NULL; uint64_t size = 0; struct vk_sync_binary_shared *sh = (void *)1;

    if (argc > 1) { printf("%d\n", kk_opaque_fd_enabled()); return 0; }

    int fd = kk_ofd_export(KK_OFD_HEAP, &heap_owner, &heap, 4096, NULL);
    T(fd >= 0 && retains == 1, "export: an fd, the heap retained once");
    int dup_fd = dup(fd), passed = pass_fd(fd);
    T(passed >= 0 && passed != fd, "the fd crosses a unix socket");
    T(kk_ofd_lookup(fd, KK_OFD_HEAP, &got, &size, &sh) && got == &heap && size == 4096 && sh == NULL && retains == 2,
      "lookup by the fd: the heap, its size, no shared point, retained for the caller");
    T(kk_ofd_lookup(dup_fd, KK_OFD_HEAP, &got, NULL, NULL) && got == &heap, "lookup by a dup");
    T(kk_ofd_lookup(passed, KK_OFD_HEAP, &got, NULL, NULL) && got == &heap, "lookup by the copy that came back through the socket");
    T(!kk_ofd_lookup(fd, KK_OFD_EVENT, &got, NULL, NULL), "another kind names nothing");
    int foreign = open("/dev/null", O_RDONLY);
    T(!kk_ofd_lookup(foreign, KK_OFD_HEAP, &got, NULL, NULL), "a foreign fd names nothing");

    int efd = kk_ofd_export(KK_OFD_EVENT, &event_owner, &event, 0, &shared);
    T(efd >= 0 && shared.refs == 2, "a binary payload's export holds the shared point");
    int ofd = kk_ofd_export(KK_OFD_EVENT, &event_owner, &other, 0, NULL);
    T(kk_ofd_lookup(efd, KK_OFD_EVENT, &got, NULL, &sh) && got == &event && sh == &shared && shared.refs == 3,
      "lookup of the binary payload: its event and its point, counted once for the caller");
    T(kk_ofd_lookup(ofd, KK_OFD_EVENT, &got, NULL, &sh) && got == &other && sh == NULL, "two exports, two keys");

    int before = releases;
    kk_ofd_forget(&heap_owner);
    T(releases == before + 1 && !kk_ofd_lookup(fd, KK_OFD_HEAP, &got, NULL, NULL), "forget drops the owner's entry and releases it");
    T(kk_ofd_lookup(efd, KK_OFD_EVENT, &got, NULL, NULL), "...and only the owner's");
    kk_ofd_forget(&event_owner);
    T(shared.refs == 3 - 1 && shared_unrefs == 1 && !kk_ofd_lookup(efd, KK_OFD_EVENT, &got, NULL, NULL)
      && !kk_ofd_lookup(ofd, KK_OFD_EVENT, &got, NULL, NULL), "forget drops both of the event owner's entries and its point reference");

    int i, last = 0, owner2 = 0;  /* address only; Xcode 27 clang -Wuninitialized-const-pointer */
    for (i = 0; i < 1024; i++) { last = kk_ofd_export(KK_OFD_HEAP, &owner2, &heap, 1, NULL); if (last < 0) break; close(last); }
    T(i == 1024, "1024 live handles");
    T(kk_ofd_export(KK_OFD_HEAP, &owner2, &heap, 1, NULL) < 0, "the 1025th is refused");
    kk_ofd_forget(&owner2);
    T(kk_ofd_export(KK_OFD_HEAP, &owner2, &heap, 1, NULL) >= 0, "after forget there is room again");
    printf("%s\n", fails ? "HARNESS FAILED" : "harness ok");
    return fails != 0;
}
'''
    src = "#include <fcntl.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <unistd.h>\n" + stubs + body + main
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = os.path.join(tmp, "t.c"), os.path.join(tmp, "t")
        Path(c).write_text(src)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", c, "-o", exe], capture_output=True, text=True)
        check("kk_opaque_fd.c compiles on the host with the stand-ins", b.returncode == 0, b.stderr[-2000:])
        if b.returncode == 0:
            env = dict(os.environ, TMPDIR=tmp)
            r = subprocess.run([exe], capture_output=True, text=True, env=env)
            check("the registry: fds, dups and socket copies name one object; kinds, foreign fds, forget and the table limit",
                  r.returncode == 0 and "harness ok" in r.stdout, r.stdout + r.stderr)
            check("the handle files are unlinked at once", not [f for f in os.listdir(tmp) if f.startswith("kk-ofd-")], os.listdir(tmp))
            seen = {}
            for value in (None, "", "0", "yes", "1", "10"):
                e = dict(os.environ)
                e.pop("MADEIRA_KK_OPAQUE_FD", None)
                if value is not None:
                    e["MADEIRA_KK_OPAQUE_FD"] = value
                s = subprocess.run([exe, "key"], capture_output=True, text=True, env=e)
                seen[value] = (s.stdout.strip(), "[kk-ofd] madeira-bcd MADEIRA_KK_OPAQUE_FD=1" in s.stderr)
            check("the key: only 1 turns it on, with one [kk-ofd] line",
                  seen == {None: ("0", False), "": ("0", False), "0": ("0", False), "yes": ("0", False), "1": ("1", True), "10": ("0", False)}, seen)

# 3. Wine's mapping, compiled on the host
vs = (R / "build/win32u-unix/vulkan_surface_ios.c").read_text()
fn = vs[vs.index("static void ios_vulkan_map_device_extensions(struct vulkan_device_extensions *extensions)"):]
fn = fn[:fn.index("\n}\n") + 3]
if cc:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
struct vulkan_device_extensions { int has_VK_KHR_external_memory_win32, has_VK_KHR_external_memory_fd, has_VK_KHR_external_semaphore_win32,
    has_VK_KHR_external_semaphore_fd, has_VK_KHR_external_fence_win32, has_VK_KHR_external_fence_fd; };
''' + fn + r'''
int main(void)
{
    struct vulkan_device_extensions host = { 0, 1, 0, 1, 0, 1 }, client = { 1, 0, 1, 0, 1, 0 };
    ios_vulkan_map_device_extensions(&host);
    ios_vulkan_map_device_extensions(&client);
    printf("%d%d%d%d%d%d %d%d%d%d%d%d\n", host.has_VK_KHR_external_memory_win32, host.has_VK_KHR_external_memory_fd,
           host.has_VK_KHR_external_semaphore_win32, host.has_VK_KHR_external_semaphore_fd, host.has_VK_KHR_external_fence_win32,
           host.has_VK_KHR_external_fence_fd, client.has_VK_KHR_external_memory_win32, client.has_VK_KHR_external_memory_fd,
           client.has_VK_KHR_external_semaphore_win32, client.has_VK_KHR_external_semaphore_fd, client.has_VK_KHR_external_fence_win32,
           client.has_VK_KHR_external_fence_fd);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = os.path.join(tmp, "w.c"), os.path.join(tmp, "w")
        Path(c).write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", c, "-o", exe], capture_output=True, text=True)
        check("Wine's iOS device extension mapping compiles on the host", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            res = {}
            for value in (None, "0", "1"):
                e = dict(os.environ)
                e.pop("MADEIRA_KK_OPAQUE_FD", None)
                if value is not None:
                    e["MADEIRA_KK_OPAQUE_FD"] = value
                res[value] = subprocess.run([exe], capture_output=True, text=True, env=e).stdout.strip()
            check("Wine maps nothing without the key; with it memory and semaphore fds and win32 both ways, fences not",
                  res == {None: "010101 101010", "0": "010101 101010", "1": "111101 111110"}, res)

# 4. Zink
gl = (R / "build/win32u-unix/opengl_ios.c").read_text()
check("Zink takes the session's KosmicKrisp only with MADEIRA_ZINK_VULKAN=kosmickrisp and MADEIRA_VULKAN_LIBRARY naming it",
      'if (zink_vk && !strcmp( zink_vk, "kosmickrisp" ) && session_vk && strstr( session_vk, "/libvulkan_kosmickrisp.dylib" ))' in gl
      and 'snprintf( path, sizeof(path), "%s", session_vk );' in gl
      and "needs vulkan = kosmickrisp on in this " in gl and 'setenv( "ZINK_VULKAN_LIBRARY", path, 1 );' in gl)

# 5. policy, catalog, row, rule, CI
policy = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy knows both keys and their only values",
      '"MADEIRA_KK_OPAQUE_FD",' in policy and '"MADEIRA_ZINK_VULKAN",' in policy
      and 'guard value == "1" else {\n                    throw RecommendationsError.policy("\\(path): MADEIRA_KK_OPAQUE_FD is 1")' in policy
      and 'guard value == "kosmickrisp" else {\n                    throw RecommendationsError.policy("\\(path): MADEIRA_ZINK_VULKAN is kosmickrisp")' in policy)
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents both", 'ConfigOption(key: "env.MADEIRA_KK_OPAQUE_FD", title: "KosmicKrisp: share images and semaphores (opaque fds)"' in cat
      and 'ConfigOption(key: "env.MADEIRA_ZINK_VULKAN", title: "OpenGL (Zink) on KosmicKrisp"' in cat)
import json
rows = {r["id"]: r for r in json.loads((R / "recommended/recommendations.json").read_text())["rules"]}
xp = [l if isinstance(l, dict) else {"line": l} for l in rows["x-plane-12"]["config"]]
check("X-Plane 12's row: zink, kosmickrisp, the two lines from build 669",
      {"line": "gl-backend = zink", "minBuild": 632} in xp and {"line": "vulkan = kosmickrisp", "minBuild": 668} in xp
      and {"line": "env.MADEIRA_KK_OPAQUE_FD = 1", "minBuild": 669} in xp and {"line": "env.MADEIRA_ZINK_VULKAN = kosmickrisp", "minBuild": 669} in xp)
rules = (R / "build/tools/autotune_rules.py").read_text()
check("the auto-tune rule gives both lines after X-Plane's profile refusal on KosmicKrisp",
      '"id": "xplane-vulkan-gl-bridge-sharing",\n        "lines": ["env.MADEIRA_KK_OPAQUE_FD = 1", "env.MADEIRA_ZINK_VULKAN = kosmickrisp"],' in rules
      and 's("[guest-log]", "cannot run Vulkan X-Plane")' in rules)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check, and the KosmicKrisp cache key covers the patch folder",
      "run: python3 tests/host/check-kk-opaque-fd.py" in wf and "'build/mesa-ios/playport-patches/*.patch'" in wf)

print("PASS: KosmicKrisp opaque fds" if ok else "FAILED: KosmicKrisp opaque fds")
sys.exit(0 if ok else 1)
