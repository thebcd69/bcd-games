#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# The C and Python text this script writes goes into files of Wine (dlls/winevulkan, dlls/win32u; copyright their authors, see the
# files' own headers) and is LGPL-2.1-or-later like Wine; the Wine lines it quotes to find its places are Wine's. It does for
# make_vulkan what Madeira's OpenGL change does for Wine's make_opengl (patches/wine-opengl-winios.patch, Connor Gow: the wow64
# thunks convert embedded guest pointers with ios_wow_host_ptr and keep handles and callbacks unchanged; no code taken) and uses
# 125hz's guest-window helpers of include/wine/unixlib.h. See THIRD-PARTY-NOTICES.md.
"""Vulkan for 32-bit programs: Wine's winevulkan and win32u Vulkan layer learn the iOS guest window.

A 32-bit program on iOS lives in a guest window: its address `a` is host address B + a (docs/WOW64.md). Wine's generated WoW64 thunks
for Vulkan turn the 32-bit pointers of an argument block into host pointers with UlongToPtr, which is only right where guest and host
addresses are the same number. This script, run on the checked-out wine tree at build time (the Wine pin and the caches keyed on it stay
as they are, like tools/patch-wine-vulkan-suboptimal.py), makes these changes:

  dlls/winevulkan/make_vulkan     the generator writes ios_wow_host_ptr / ios_wow_guest_ptr32 for addresses (pointers, strings,
                                  dispatchable handles) and keeps UlongToPtr / PtrToUlong for numbers (HWND, HINSTANCE, HANDLE,
                                  PFN_*); wine_vk_unwrap_handle reads a client address through wine_vk_client_ptr
  dlls/winevulkan/vulkan_thunks.c the same change applied to the generated file (tests/host/check-vulkan-wow64.py proves the
                                  transform equals what the changed generator writes)
  dlls/winevulkan/vulkan_private.h wine_vk_client_ptr, wine_vk_client_value; the command pool and find_next_struct32 use them
  dlls/winevulkan/vulkan.c        the two debug callback entry points are kept per process (this library is shared by every Windows
                                  process of the session), a debug callback on a thread that is not a Windows thread is dropped, object
                                  handles and user data go back as the values the program passed; the 32-bit entries of init and
                                  is_available_* convert their pointers
  dlls/win32u/vulkan.c            a mapping is checked against the caller's window (upstream tests "below 4 GB", never true here);
                                  host memory allocated for a 32-bit program's mapping is freed with the Vulkan memory (upstream
                                  11.4 to 11.20 leaks it: the pointer is never stored) and on every failure path; its size is
                                  rounded to the import alignment before it is allocated; a 32-bit program's vkCreateDevice is
                                  refused, with a log line, on a host that can place no mapping

Nothing here changes what a 64-bit caller gets: the wow64 thunks are a separate table (bound only for a 32-bit winevulkan.dll, which
only a session with vulkan = moltenvk has) and every `#ifdef _WIN64` block of the generated file is untouched; the helpers return
their argument when the caller has no window; the win32u branches are behind caller_zero_bits() (0 for a 64-bit process); a process
finds its own pair of debug callback entry points, which is the pair it registered. The one thing a 64-bit caller can notice is a
crash that does not happen: a debug message the Vulkan library raises on a thread of its own is dropped instead of dispatched.
With a window base of 0 (and on every platform but the iOS port) the helpers are the UlongToPtr / PtrToUlong they replace.

Idempotent per file (a second run only says so); fails by name when an anchor moved or a count is not the expected one.
Run from the repository root before build/ntdll-unix/build.sh and build/win32u-unix/build.sh:
    python3 tools/patch-wine-vulkan-wow64.py [wine-tree]
"""
import pathlib
import re
import sys

# ------------------------------------------------------------------------------------------------------------- the thunk transform
HANDLE_TYPES = ("HWND", "HINSTANCE", "HANDLE")
_HANDLE_CAST = re.compile(r"\((?:HWND|HINSTANCE|HANDLE|PFN_[A-Za-z0-9_]+)\)$")
_HOST_DECL = re.compile(r"^    (?:const )?([A-Za-z_][A-Za-z0-9_]*) (?:\*)*([A-Za-z_][A-Za-z0-9_]*)_host(?: = NULL)?;$")
_LOAD = re.compile(r"^(    )([A-Za-z_][A-Za-z0-9_]*)(_host = )UlongToPtr\(\*")
_STORE = re.compile(r"^(    \*.* = )PtrToUlong\(([A-Za-z_][A-Za-z0-9_]*)_host\);$")
UNWRAP_OLD = re.compile(r"\(\((Vk[A-Za-z0-9]+)\) \(uintptr_t\) handle\)")


def _is_number_type(t):
    return t in HANDLE_TYPES or t.startswith("PFN_")


def transform_thunks(text):
    """vulkan_thunks.c as the changed make_vulkan writes it, from the file the unchanged one wrote.

    Returns (new_text, counts). Rules, the generator's own (make_vulkan, is_wow64_number / wow64_pointee_is_number):
      (HWND|HINSTANCE|HANDLE|PFN_x)UlongToPtr(v)      kept: a number
      X_host = UlongToPtr(*...) with `HANDLE X_host;`  kept (the outer call): the value read is a number
      *... = PtrToUlong(X_host)  with `HANDLE X_host;`  kept
      every other UlongToPtr( / PtrToUlong(           ios_wow_host_ptr( / ios_wow_guest_ptr32(
      ((VkX) (uintptr_t) handle)                      ((VkX) wine_vk_client_ptr(handle))   (wine_vk_unwrap_handle)
    """
    counts = {"host": 0, "guest": 0, "kept_host": 0, "kept_guest": 0, "unwrap": 0}
    out = []
    host_types = {}
    for line in text.split("\n"):
        if line == "{":
            host_types = {}
        m = _HOST_DECL.match(line)
        if m:
            host_types[m.group(2)] = m.group(1)

        keep_outer_load = False
        m = _LOAD.match(line)
        if m and _is_number_type(host_types.get(m.group(2), "")):
            keep_outer_load = True
        m = _STORE.match(line)
        keep_store = bool(m and _is_number_type(host_types.get(m.group(2), "")))

        if "UlongToPtr(" in line:
            pieces = line.split("UlongToPtr(")
            new = pieces[0]
            for idx, piece in enumerate(pieces[1:]):
                before = new
                if _HANDLE_CAST.search(before) or (keep_outer_load and idx == 0):
                    new += "UlongToPtr(" + piece
                    counts["kept_host"] += 1
                else:
                    new += "ios_wow_host_ptr(" + piece
                    counts["host"] += 1
            line = new
        if "PtrToUlong(" in line:
            n = line.count("PtrToUlong(")
            if keep_store:
                counts["kept_guest"] += n
            else:
                line = line.replace("PtrToUlong(", "ios_wow_guest_ptr32(")
                counts["guest"] += n
        if "(uintptr_t) handle)" in line:
            line, n = UNWRAP_OLD.subn(r"((\1) wine_vk_client_ptr(handle))", line)
            counts["unwrap"] += n
        out.append(line)
    return "\n".join(out), counts


# The numbers for Wine 11.4's committed vulkan_thunks.c (VK spec 1.4.344): a different count means the file is not the one this
# script was written for, and the transform must be looked at again before it is trusted.
THUNK_COUNTS_11_4 = {"host": 3082, "guest": 26, "kept_host": 13, "kept_guest": 3, "unwrap": 5}

# ----------------------------------------------------------------------------------------------------------------- anchored edits
MAKE_VULKAN = [
    # 1. the conversion a value takes: a number stays a number
    ('''    def value(self, prefix, conv, deref=False):
        if not conv or not self.needs_ptr32_type() or (not self.is_pointer() and self.type_name == "size_t"):
            return prefix + self.name
''', '''    def wow64_pointee_is_number(self):
        """ madeira-bcd (Vulkan for 32-bit programs): the type is a pointer-sized NUMBER (a window, module
        or kernel handle, a function address the unix side never calls), not an address the unix side
        reads. On iOS a 32-bit program lives in a guest window (wine/unixlib.h, ios_wow_host_ptr): an
        address gets the window's base added, a number must not. """
        return self.type_name in ["HWND", "HINSTANCE", "HANDLE"] or self.type_name.startswith("PFN")

    def is_wow64_number(self):
        """ madeira-bcd: this variable itself is such a number (not a pointer to one). """
        return not self.is_pointer() and not self.is_static_array() and self.wow64_pointee_is_number()

    def value(self, prefix, conv, deref=False):
        if not conv or not self.needs_ptr32_type() or (not self.is_pointer() and self.type_name == "size_t"):
            return prefix + self.name
'''),
    ('''        return f"{ptr}({cast_type})UlongToPtr({prefix}{self.name})"
''', '''        to_host = "UlongToPtr" if self.is_wow64_number() else "ios_wow_host_ptr"
        return f"{ptr}({cast_type}){to_host}({prefix}{self.name})"
'''),
    # 2. pointer members of a struct
    ('''        elif conv and direction == Direction.OUTPUT and self.is_pointer():
            return "{0}{1} = PtrToUlong({2}{1});\\n".format(output, self.name, input)
        elif conv and direction == Direction.INPUT and self.is_pointer():
            return "{0}{1} = UlongToPtr({2}{1});\\n".format(output, self.name, input)
''', '''        elif conv and direction == Direction.OUTPUT and self.is_pointer():
            return "{0}{1} = ios_wow_guest_ptr32({2}{1});\\n".format(output, self.name, input)
        elif conv and direction == Direction.INPUT and self.is_pointer():
            return "{0}{1} = ios_wow_host_ptr({2}{1});\\n".format(output, self.name, input)
'''),
    # 3. a pointer-sized value read from, and written back through, a pointer parameter
    ('''            elif self.is_pointer_size() and self.type_name != "size_t":
                return "    {0}_host = UlongToPtr(*{1});\\n".format(self.name, self.value(prefix, conv))
''', '''            elif self.is_pointer_size() and self.type_name != "size_t":
                to_host = "UlongToPtr" if self.wow64_pointee_is_number() else "ios_wow_host_ptr"
                return "    {0}_host = {2}(*{1});\\n".format(self.name, self.value(prefix, conv), to_host)
'''),
    ('''            elif self.is_pointer_size() and self.type_name != "size_t":
                return "    *{0} = PtrToUlong({1}_host);\\n".format(self.value(prefix, conv), self.name)
''', '''            elif self.is_pointer_size() and self.type_name != "size_t":
                to_guest = "PtrToUlong" if self.wow64_pointee_is_number() else "ios_wow_guest_ptr32"
                return "    *{0} = {2}({1}_host);\\n".format(self.value(prefix, conv), self.name, to_guest)
'''),
    # 4. the pNext chain of a 32-bit struct
    ('''                body += f"\\n    for (in_header = UlongToPtr(in->{self.operand.chain_next.name}); in_header; in_header = UlongToPtr(in_header->{self.operand.chain_next.name}))\\n"
''', '''                body += f"\\n    for (in_header = ios_wow_host_ptr(in->{self.operand.chain_next.name}); in_header; in_header = ios_wow_host_ptr(in_header->{self.operand.chain_next.name}))\\n"
'''),
    # 5. arrays of pointers and of dispatchable handles
    ('''                inp = f"({win_type} *)UlongToPtr(in[i])" if self.conv else "in[i]"
''', '''                inp = f"({win_type} *)ios_wow_host_ptr(in[i])" if self.conv else "in[i]"
'''),
    ('''                else:
                    body += u"        out[i] = UlongToPtr(in[i]);\\n"

            elif self.array.is_handle():
''', '''                else:
                    body += u"        out[i] = ios_wow_host_ptr(in[i]);\\n"

            elif self.array.is_handle():
'''),
    ('''                elif self.direction == Direction.INPUT:
                    input = "UlongToPtr(in[i])"
                else:
                    input = "PtrToUlong(in[i])"
''', '''                elif self.direction == Direction.INPUT:
                    input = "ios_wow_host_ptr(in[i])"
                else:
                    input = "ios_wow_guest_ptr32(in[i])"
'''),
    ('''                if self.direction == Direction.INPUT:
                    body += "        out[i] = UlongToPtr(in[i]);\\n"
                else:
                    body += "        out[i] = PtrToUlong(in[i]);\\n"
''', '''                if self.direction == Direction.INPUT:
                    body += "        out[i] = ios_wow_host_ptr(in[i]);\\n"
                else:
                    body += "        out[i] = ios_wow_guest_ptr32(in[i]);\\n"
'''),
    # 6. wine_vk_unwrap_handle: a dispatchable client object's address that arrives as a 64-bit number
    ('''                f.write(handle.host_handle("(({}) (uintptr_t) handle)".format(handle.name)))
''', '''                f.write(handle.host_handle("(({}) wine_vk_client_ptr(handle))".format(handle.name)))
'''),
]

VULKAN_PRIVATE_H = [
    ('''static inline struct wine_cmd_pool *wine_cmd_pool_from_handle(VkCommandPool handle)
{
    struct vulkan_client_object *client = &command_pool_from_handle(handle)->obj;
    return (struct wine_cmd_pool *)(UINT_PTR)client->unix_handle;
}
''', '''/* madeira-bcd (Vulkan for 32-bit programs). On iOS a 32-bit program lives in a guest window: its address `a` is host address
 * B + a (wine/unixlib.h, ios_wow_host_ptr). The generated wow64 thunks convert the pointers of their argument blocks; these two
 * helpers are for a client object's address that travels as a 64-bit NUMBER. A caller without a window (every 64-bit caller, and
 * every platform but the iOS port) gets its argument back from both. */

/* The address of a client object that arrives as a 64-bit handle value: a VkCommandPool, or the object of a debug name, tag or
 * private data call. Nothing is mapped below 4 GB on iOS, so a non-zero value below 4 GB is a 32-bit caller's guest address. */
static inline void *wine_vk_client_ptr(UINT64 handle)
{
    if (handle && !(handle >> 32)) return ios_wow_host_ptr((ULONG)handle);
    return (void *)(UINT_PTR)handle;
}

/* The number the calling program knows a client address by (what it passed in): the address minus B when it lies in the
 * caller's window, else unchanged (a unix object's address, a host handle, a 64-bit caller's own pointer). */
static inline UINT64 wine_vk_client_value(UINT64 host)
{
#ifdef WINE_IOS
    ULONG_PTR base = ios_wow_host_base();
    if (base && host >= base && host - base < ((UINT64)1 << 32)) return host - base;
#endif
    return host;
}

static inline struct wine_cmd_pool *wine_cmd_pool_from_handle(VkCommandPool handle)
{
    struct vulkan_client_object *client = &((struct vk_command_pool *)wine_vk_client_ptr(handle))->obj;
    return (struct wine_cmd_pool *)(UINT_PTR)client->unix_handle;
}
'''),
    ('''    for (header = s; header; header = UlongToPtr(header->pNext))
''', '''    for (header = s; header; header = ios_wow_host_ptr(header->pNext))
'''),
]

WINEVULKAN_C = [
    # per-process debug callback entry points
    ('''static UINT64 call_vulkan_debug_report_callback;
static UINT64 call_vulkan_debug_utils_callback;
''', '''/* madeira-bcd (Vulkan for 32-bit programs): the two debug callback entry points are addresses in the caller's own winevulkan.dll.
 * On iOS every Windows process of the session shares this library, and a 32-bit and a 64-bit winevulkan.dll (or two processes
 * that loaded it at different addresses) have different ones: one pair of globals handed a process another process's address.
 * They are kept per process (PEB); a process that initialises again replaces its own pair. With one process per library, as
 * everywhere else, the table has one entry and is the pair of globals it replaces. */
struct vk_process_callbacks
{
    void *peb;
    UINT64 report;
    UINT64 utils;
};
static struct vk_process_callbacks process_callbacks[64];
static unsigned int process_callbacks_count, process_callbacks_next;
static pthread_mutex_t process_callbacks_lock = PTHREAD_MUTEX_INITIALIZER;

static void set_process_callbacks(UINT64 report, UINT64 utils)
{
    void *peb = NtCurrentTeb()->Peb;
    unsigned int i;

    pthread_mutex_lock(&process_callbacks_lock);
    for (i = 0; i < process_callbacks_count; i++)
        if (process_callbacks[i].peb == peb) break;
    if (i == process_callbacks_count)
    {
        if (process_callbacks_count < ARRAY_SIZE(process_callbacks)) process_callbacks_count++;
        else i = process_callbacks_next++ % ARRAY_SIZE(process_callbacks);  /* full: the oldest entry goes */
    }
    process_callbacks[i].peb = peb;
    process_callbacks[i].report = report;
    process_callbacks[i].utils = utils;
    pthread_mutex_unlock(&process_callbacks_lock);
}

/* The calling thread's process's entry points. FALSE on a thread that is not a Windows thread (the host can report from one of its
 * own: a Metal completion queue has no TEB, and a user callback cannot run there) and for a process that never initialised. */
static BOOL get_process_callbacks(UINT64 *report, UINT64 *utils)
{
    TEB *teb = NtCurrentTeb();
    unsigned int i;
    BOOL found = FALSE;

    if (!teb) return FALSE;
    pthread_mutex_lock(&process_callbacks_lock);
    for (i = 0; i < process_callbacks_count; i++)
    {
        if (process_callbacks[i].peb != teb->Peb) continue;
        *report = process_callbacks[i].report;
        *utils = process_callbacks[i].utils;
        found = TRUE;
        break;
    }
    pthread_mutex_unlock(&process_callbacks_lock);
    return found;
}
'''),
    ('''    struct debug_utils_label dummy_label, *labels;
    UINT size, strings_len;
    char *ptr, *strings;
    ULONG ret_len;
    void *ret_ptr;
    unsigned int i;

    TRACE("%i, %u, %p, %p\\n", severity, message_types, callback_data, user_data);

    object = user_data;
''', '''    struct debug_utils_label dummy_label, *labels;
    UINT64 report_callback, utils_callback;
    UINT size, strings_len;
    char *ptr, *strings;
    ULONG ret_len;
    void *ret_ptr;
    unsigned int i;

    TRACE("%i, %u, %p, %p\\n", severity, message_types, callback_data, user_data);

    if (!get_process_callbacks(&report_callback, &utils_callback)) return VK_FALSE;

    object = user_data;
'''),
    ('''    params->dispatch.callback = call_vulkan_debug_utils_callback;
    params->user_callback = object->user_callback;
    params->user_data = object->user_data;
    params->severity = severity;
''', '''    params->dispatch.callback = utils_callback;
    params->user_callback = object->user_callback;
    params->user_data = wine_vk_client_value(object->user_data);
    params->severity = severity;
'''),
    ('''                WARN("handle conversion failed 0x%s\\n", wine_dbgstr_longlong(callback_data->pObjects[i].objectHandle));
                free(params);
                return VK_FALSE;
            }
        }
''', '''                WARN("handle conversion failed 0x%s\\n", wine_dbgstr_longlong(callback_data->pObjects[i].objectHandle));
                free(params);
                return VK_FALSE;
            }
            objects[i].object_handle = wine_vk_client_value(objects[i].object_handle);
        }
'''),
    ('''    struct vulkan_debug_report_callback *object;
    UINT strings_len;
    ULONG ret_len;
    void *ret_ptr;
    char *strings;

    TRACE("%#x, %#x, 0x%s, 0x%s, %d, %p, %p, %p\\n", flags, object_type, wine_dbgstr_longlong(object_handle),
        wine_dbgstr_longlong(location), code, layer_prefix, message, user_data);

    object = user_data;
''', '''    struct vulkan_debug_report_callback *object;
    UINT64 report_callback, utils_callback;
    UINT strings_len;
    ULONG ret_len;
    void *ret_ptr;
    char *strings;

    TRACE("%#x, %#x, 0x%s, 0x%s, %d, %p, %p, %p\\n", flags, object_type, wine_dbgstr_longlong(object_handle),
        wine_dbgstr_longlong(location), code, layer_prefix, message, user_data);

    if (!get_process_callbacks(&report_callback, &utils_callback)) return VK_FALSE;

    object = user_data;
'''),
    ('''    params->dispatch.callback = call_vulkan_debug_report_callback;
    params->user_callback = object->user_callback;
    params->user_data = object->user_data;
    params->flags = flags;
''', '''    params->dispatch.callback = report_callback;
    params->user_callback = object->user_callback;
    params->user_data = wine_vk_client_value(object->user_data);
    params->flags = flags;
'''),
    ('''    params->object_handle = object->instance->p_client_handle_from_host(object->instance, object_handle);
    if (!params->object_handle) params->object_type = VK_DEBUG_REPORT_OBJECT_TYPE_UNKNOWN_EXT;
''', '''    params->object_handle = wine_vk_client_value(object->instance->p_client_handle_from_host(object->instance, object_handle));
    if (!params->object_handle) params->object_type = VK_DEBUG_REPORT_OBJECT_TYPE_UNKNOWN_EXT;
'''),
    ('''    call_vulkan_debug_report_callback = params->call_vulkan_debug_report_callback;
    call_vulkan_debug_utils_callback = params->call_vulkan_debug_utils_callback;
''', '''    set_process_callbacks(params->call_vulkan_debug_report_callback, params->call_vulkan_debug_utils_callback);
'''),
    # the command pool's client object arrives as a 64-bit handle value
    ('''    struct vk_command_pool *client_command_pool = command_pool_from_handle(*client_command_pool_ptr);
''', '''    struct vk_command_pool *client_command_pool = wine_vk_client_ptr(*client_command_pool_ptr);
'''),
    # the hand-written 32-bit entries
    ('''    params.extensions = UlongToPtr(params32->extensions);
''', '''    params.extensions = ios_wow_host_ptr(params32->extensions);
'''),
    ('''    return is_available_instance_function(UlongToPtr(params->instance), UlongToPtr(params->name));
''', '''    return is_available_instance_function(ios_wow_host_ptr(params->instance), ios_wow_host_ptr(params->name));
'''),
    ('''    return is_available_device_function(UlongToPtr(params->device), UlongToPtr(params->name));
''', '''    return is_available_device_function(ios_wow_host_ptr(params->device), ios_wow_host_ptr(params->name));
'''),
]

WIN32U_VULKAN_C = [
    # the allocation behind a 32-bit program's host-visible memory
    ('''struct device_memory
{
    struct vulkan_device_memory obj;
    VkDeviceSize size;
    void *vm_map;
''', '''struct device_memory
{
    struct vulkan_device_memory obj;
    VkDeviceSize size;
    void *vm_map;
    void *host_alloc; /* madeira-bcd: allocate_external_host_memory's allocation (a 32-bit program's mapping), freed with the memory */
'''),
    ('''static VkResult allocate_external_host_memory( struct vulkan_device *device, VkMemoryAllocateInfo *alloc_info, uint32_t mem_flags,
                                               VkImportMemoryHostPointerInfoEXT *import_info )
{
''', '''/* madeira-bcd (Vulkan for 32-bit programs): *host_alloc receives the allocation the host memory was imported from, or NULL when
 * nothing was imported; the caller owns it (win32u_vkFreeMemory, or the failure path of win32u_vkAllocateMemory). Upstream
 * (11.4 to 11.20) kept the pointer in a local only, so the caller's address space lost the range for good with every freed or
 * failed allocation: a 32-bit program has 2 to 4 GB of it. The size is rounded to the import alignment before it is allocated,
 * so the whole imported range is committed whatever the host's page size (16 KB on iOS, 4 KB pages in Windows terms). */
static VkResult allocate_external_host_memory( struct vulkan_device *device, VkMemoryAllocateInfo *alloc_info, uint32_t mem_flags,
                                               VkImportMemoryHostPointerInfoEXT *import_info, void **host_alloc )
{
'''),
    ('''    uint32_t i, align = physical_device->external_memory_align - 1;
    SIZE_T alloc_size = alloc_info->allocationSize;
    static int once;
    void *mapping = NULL;
    VkResult res;

    if (!once++) FIXME( "Using VK_EXT_external_memory_host\\n" );

    if (NtAllocateVirtualMemory( GetCurrentProcess(), &mapping, caller_zero_bits(), &alloc_size, MEM_COMMIT, PAGE_READWRITE ))
    {
        ERR( "NtAllocateVirtualMemory failed\\n" );
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }

    if ((res = device->p_vkGetMemoryHostPointerPropertiesEXT( device->host.device, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,
                                                              mapping, &props )))
    {
        ERR( "vkGetMemoryHostPointerPropertiesEXT failed: %d\\n", res );
        return res;
    }
''', '''    uint32_t i, align = physical_device->external_memory_align - 1;
    SIZE_T alloc_size = ((SIZE_T)alloc_info->allocationSize + align) & ~(SIZE_T)align;
    static int once;
    void *mapping = NULL;
    VkResult res;

    *host_alloc = NULL;
    if (!once++) FIXME( "Using VK_EXT_external_memory_host\\n" );

    if (NtAllocateVirtualMemory( GetCurrentProcess(), &mapping, caller_zero_bits(), &alloc_size, MEM_COMMIT, PAGE_READWRITE ))
    {
        static int logged;
        if (!logged++)
            ERR( "[winios-vk] no room in the 32-bit program's address space for a Vulkan allocation of %s bytes (host-visible "
                 "memory is placed inside it): vkAllocateMemory fails\\n", wine_dbgstr_longlong( alloc_info->allocationSize ) );
        ERR( "NtAllocateVirtualMemory failed\\n" );
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }

    if ((res = device->p_vkGetMemoryHostPointerPropertiesEXT( device->host.device, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,
                                                              mapping, &props )))
    {
        ERR( "vkGetMemoryHostPointerPropertiesEXT failed: %d\\n", res );
        alloc_size = 0;
        NtFreeVirtualMemory( GetCurrentProcess(), &mapping, &alloc_size, MEM_RELEASE );
        return res;
    }
'''),
    ('''        if (i == physical_device->memory_properties.memoryTypeCount)
        {
            FIXME( "Not found compatible memory type\\n" );
            alloc_size = 0;
            NtFreeVirtualMemory( GetCurrentProcess(), &mapping, &alloc_size, MEM_RELEASE );
        }
    }
''', '''        if (i == physical_device->memory_properties.memoryTypeCount)
        {
            FIXME( "Not found compatible memory type\\n" );
            alloc_size = 0;
            NtFreeVirtualMemory( GetCurrentProcess(), &mapping, &alloc_size, MEM_RELEASE );
            mapping = NULL;
        }
    }
'''),
    ('''        import_info->pNext = alloc_info->pNext;
        alloc_info->pNext = import_info;
        alloc_info->allocationSize = (alloc_info->allocationSize + align) & ~align;
    }

    return VK_SUCCESS;
}
''', '''        import_info->pNext = alloc_info->pNext;
        alloc_info->pNext = import_info;
        alloc_info->allocationSize = (alloc_info->allocationSize + align) & ~align;
        *host_alloc = mapping;
    }

    return VK_SUCCESS;
}
'''),
    ('''    struct device_memory *memory;
    BOOL nt_shared = FALSE;
    uint32_t mem_flags;
    void *mapping = NULL;
    VkResult res;

    for (next = &prev->pNext; *next; prev = *next, next = &(*next)->pNext)
''', '''    struct device_memory *memory;
    BOOL nt_shared = FALSE;
    uint32_t mem_flags;
    void *mapping = NULL, *host_alloc = NULL;
    VkResult res;

    for (next = &prev->pNext; *next; prev = *next, next = &(*next)->pNext)
'''),
    ('''        (res = allocate_external_host_memory( device, alloc_info, mem_flags, &host_pointer_info )))
        return res;

    if (!(memory = calloc( 1, sizeof(*memory) ))) return VK_ERROR_OUT_OF_HOST_MEMORY;
''', '''        (res = allocate_external_host_memory( device, alloc_info, mem_flags, &host_pointer_info, &host_alloc )))
        return res;

    if (!(memory = calloc( 1, sizeof(*memory) )))
    {
        if (host_alloc)
        {
            SIZE_T alloc_size = 0;
            NtFreeVirtualMemory( GetCurrentProcess(), &host_alloc, &alloc_size, MEM_RELEASE );
        }
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
'''),
    ('''    memory->size = alloc_info->allocationSize;
    memory->vm_map = mapping;
    instance->p_insert_object( instance, &memory->obj.obj );
''', '''    memory->size = alloc_info->allocationSize;
    memory->vm_map = mapping;
    memory->host_alloc = host_alloc;
    instance->p_insert_object( instance, &memory->obj.obj );
'''),
    ('''    WARN( "Failed to allocate memory, res %d\\n", res );
    if (host_device_memory) device->p_vkFreeMemory( device->host.device, host_device_memory, NULL );
''', '''    WARN( "Failed to allocate memory, res %d\\n", res );
    if (host_device_memory) device->p_vkFreeMemory( device->host.device, host_device_memory, NULL );
    if (host_alloc)
    {
        SIZE_T alloc_size = 0;
        NtFreeVirtualMemory( GetCurrentProcess(), &host_alloc, &alloc_size, MEM_RELEASE );
    }
'''),
    ('''    if (memory->vm_map)
    {
        SIZE_T alloc_size = 0;
        NtFreeVirtualMemory( GetCurrentProcess(), &memory->vm_map, &alloc_size, MEM_RELEASE );
    }

    if (memory->semaphore) device->p_vkDestroySemaphore( device->host.device, memory->semaphore, NULL );
''', '''    if (memory->vm_map)
    {
        SIZE_T alloc_size = 0;
        NtFreeVirtualMemory( GetCurrentProcess(), &memory->vm_map, &alloc_size, MEM_RELEASE );
    }
    if (memory->host_alloc)
    {
        /* after the host has let go of the import (p_vkFreeMemory above) */
        SIZE_T alloc_size = 0;
        NtFreeVirtualMemory( GetCurrentProcess(), &memory->host_alloc, &alloc_size, MEM_RELEASE );
    }

    if (memory->semaphore) device->p_vkDestroySemaphore( device->host.device, memory->semaphore, NULL );
'''),
    # a mapping must lie in the caller's window
    ('''#ifdef _WIN64
    if (NtCurrentTeb()->WowTebOffset && res == VK_SUCCESS && (UINT_PTR)*data >> 32)
    {
        FIXME( "returned mapping %p does not fit 32-bit pointer\\n", *data );
''', '''#ifdef _WIN64
    /* madeira-bcd (Vulkan for 32-bit programs): "fits a 32-bit pointer" means "lies in the caller's guest window" on iOS, where a
     * 32-bit program's address `a` is host address B + a. The pointer is taken to the program's 32-bit number and back
     * (wine/unixlib.h): it comes back unchanged only when it lies in the window. Where guest and host addresses are the same
     * number the two helpers are PtrToUlong and ULongToPtr, and this is upstream's "below 4 GB" test. */
    if (NtCurrentTeb()->WowTebOffset && res == VK_SUCCESS && ios_wow_host_ptr( ios_wow_guest_ptr32( *data ) ) != *data)
    {
        static int logged;
        if (!logged++)
            ERR( "[winios-vk] a mapping at %p is outside the 32-bit program's address space: vkMapMemory fails (the memory "
                 "was not allocated through VK_EXT_external_memory_host or VK_EXT_map_memory_placed)\\n", *data );
        FIXME( "returned mapping %p does not fit 32-bit pointer\\n", *data );
'''),
    # refuse a device whose memory a 32-bit program could never map
    ('''    /* We need to cache all queues within the device as each requires wrapping since queues are dispatchable objects. */
    for (queue_count = 0, i = 0; i < create_info->queueCreateInfoCount; i++) queue_count += create_info->pQueueCreateInfos[i].queueCount;
''', '''#ifdef WINE_IOS
    /* madeira-bcd (Vulkan for 32-bit programs): host-visible memory must be mapped inside the 32-bit program's address space,
     * through VK_EXT_map_memory_placed or VK_EXT_external_memory_host (init_physical_device). A host with neither would hand
     * out mappings the program cannot address: every vkMapMemory would fail later. Refuse the device here, by name. */
    if (caller_zero_bits() && !physical_device->map_placed_align && !physical_device->external_memory_align)
    {
        VkPhysicalDeviceProperties properties = {0};
        instance->p_vkGetPhysicalDeviceProperties( physical_device->host.physical_device, &properties );
        ERR( "[winios-vk] Vulkan for a 32-bit program is refused on %s: the Vulkan library has neither VK_EXT_map_memory_placed "
             "nor VK_EXT_external_memory_host (with VK_KHR_external_memory), so no memory could be mapped into the program's "
             "address space; vkCreateDevice returns VK_ERROR_FEATURE_NOT_PRESENT\\n", debugstr_a(properties.deviceName) );
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
#endif

    /* We need to cache all queues within the device as each requires wrapping since queues are dispatchable objects. */
    for (queue_count = 0, i = 0; i < create_info->queueCreateInfoCount; i++) queue_count += create_info->pQueueCreateInfos[i].queueCount;
'''),
]

FILES = [
    # (path under the wine tree, marker that says "already done", edits or None for the thunk transform)
    ("dlls/winevulkan/make_vulkan", "wow64_pointee_is_number", MAKE_VULKAN),
    ("dlls/winevulkan/vulkan_thunks.c", "wine_vk_client_ptr(handle)", None),
    ("dlls/winevulkan/vulkan_private.h", "wine_vk_client_value", VULKAN_PRIVATE_H),
    ("dlls/winevulkan/vulkan.c", "set_process_callbacks", WINEVULKAN_C),
    ("dlls/win32u/vulkan.c", "host_alloc", WIN32U_VULKAN_C),
]


def patch_tree(root, check_counts=True, quiet=False):
    """Apply every change under `root` (a wine tree). Returns the list of files written."""
    root = pathlib.Path(root)
    written = []
    for rel, marker, edits in FILES:
        path = root / rel
        if not path.is_file():
            sys.exit(f"patch-wine-vulkan-wow64: {path} is missing")
        s = path.read_text()
        if marker in s:
            if not quiet:
                print(f"{rel}: the 32-bit Vulkan change is already there")
            continue
        if edits is None:
            s, counts = transform_thunks(s)
            if check_counts and counts != THUNK_COUNTS_11_4:
                sys.exit(f"patch-wine-vulkan-wow64: {rel} is not Wine 11.4's file: conversions {counts}, want {THUNK_COUNTS_11_4}")
            if "UlongToPtr(" in s and not counts["kept_host"]:
                sys.exit(f"patch-wine-vulkan-wow64: {rel}: inconsistent transform")
        else:
            for n, (old, new) in enumerate(edits, 1):
                if s.count(old) != 1:
                    sys.exit(f"patch-wine-vulkan-wow64: {rel}: edit {n} anchor found {s.count(old)} times (want 1):\n{old}")
                s = s.replace(old, new)
        if marker not in s:
            sys.exit(f"patch-wine-vulkan-wow64: {rel}: the marker {marker!r} is not in the result")
        path.write_text(s)
        written.append(rel)
        if not quiet:
            print(f"{rel}: 32-bit Vulkan change written")
    return written


def main(argv):
    patch_tree(argv[1] if len(argv) > 1 else "wine")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
