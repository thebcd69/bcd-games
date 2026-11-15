/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */
/*
 * Vulkan for 32-bit programs, on the host: Wine's winevulkan unix side and win32u's Vulkan layer, as BCD builds them
 * (tools/patch-wine-vulkan-wow64.py applied, build/win32u-unix/vulkan_ios.c around win32u's file), driven the way a 32-bit program
 * drives them on a device. tests/host/check-vulkan-wow64.py builds and runs it; nothing here needs a GPU.
 *
 * The model of the device:
 *   - a guest window: 4 GB reserved at a 4 GB-aligned host address B far above 4 GB. Everything the "program" owns (its
 *     structures, strings, client objects, the argument blocks of its unix calls) lives inside it and is named by 32-bit numbers;
 *     guest address a is host address B + a (docs/WOW64.md). This file is the program AND the 32-bit winevulkan.dll: it builds
 *     the argument blocks with the 32-bit layouts of the generated thunks (params32.h is cut out of the very vulkan_thunks.c
 *     that is compiled in) and enters the wow64 table as the WoW64 layer does, with only the block's own address converted;
 *   - nothing is mapped below 4 GB (true for this process as for iOS), so a guest pointer the unix side forgot to convert
 *     faults, and the fault handler says which step it was;
 *   - NtAllocateVirtualMemory with a guest ceiling places memory inside the window, 64 KB apart, committed in 16 KB host pages,
 *     and every allocation and free is counted; without a ceiling it is a host allocation, counted separately;
 *   - two Windows processes share the unix side, as on iOS: a 64-bit one and the 32-bit one, each with its own PEB and its own
 *     debug callback entry points;
 *   - the Vulkan library is tests/host/vulkan-wow64/mock_icd.c, loaded through MADEIRA_VULKAN_LIBRARY by BCD's own loader code,
 *     strict about imported host memory the way Metal is, and really copying bytes between mapped buffers.
 * MOCKVK_MODE picks the library's memory model: host (MoltenVK's), placed, none.
 *
 * Where this file plays the 32-bit winevulkan.dll (the client objects it allocates, the decoding of the two debug callback blocks) it
 * follows Wine's dlls/winevulkan/loader.c (LGPL-2.1-or-later, Copyright 2017 Roderick Colenbrander and Wine's authors).
 */
#include <dlfcn.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "vulkan_thunks.c"     /* the patched generated thunks: the 32-bit struct layouts and both unix call tables */

#include "mock_icd.h"
#include "params32.h"          /* struct p32_<function>: each thunk's own 32-bit argument block */

/* ----------------------------------------------------------------------------------------------------------------- reporting */
static int failures, checks, group_failures;
static const char *step = "start";

#define CHECK(cond, ...) \
    do { checks++; if (!(cond)) { failures++; group_failures++; printf( "FAIL " ); printf( __VA_ARGS__ ); \
                                  printf( "   [line %d: %s]\n", __LINE__, #cond ); } } while (0)

static void group( const char *text )
{
    if (!group_failures) printf( "ok   %s\n", text );
    group_failures = 0;
    fflush( stdout );
}

static void on_fault( int sig, siginfo_t *info, void *ctx )
{
    char line[512];
    uintptr_t addr = (uintptr_t)info->si_addr;
    int n = snprintf( line, sizeof(line), "FAIL fault (signal %d) at address %#lx during: %s%s\n", sig, (unsigned long)addr, step,
                      addr < 0x100000000ull ? "   <- an address below 4 GB: a guest pointer that was not converted to a host pointer"
                                            : "" );
    write( 1, line, n );
    _exit( 2 );
}

/* -------------------------------------------------------------------------------------------------------------- Wine's debug */
static char logbuf[1 << 16];
static size_t loglen;
static int verbose;

/* ERR and FIXME lines are kept (the refusals are ERR lines), WARN and TRACE are off, as in a session's log */
#define DEBUG_FLAGS ((1 << __WINE_DBCL_ERR) | (1 << __WINE_DBCL_FIXME))

unsigned char __cdecl __wine_dbg_get_channel_flags( struct __wine_debug_channel *channel )
{
    channel->flags = DEBUG_FLAGS;
    return DEBUG_FLAGS;
}

const char * __cdecl __wine_dbg_strdup( const char *str ) { return strdup( str ); }

int __cdecl __wine_dbg_output( const char *str )
{
    size_t n = strlen( str );
    if (loglen + n < sizeof(logbuf) - 1) { memcpy( logbuf + loglen, str, n ); loglen += n; logbuf[loglen] = 0; }
    if (verbose) fputs( str, stderr );
    return n;
}

int __cdecl __wine_dbg_header( enum __wine_debug_class cls, struct __wine_debug_channel *channel, const char *function )
{
    char head[128];
    channel->flags = DEBUG_FLAGS;
    if (!(DEBUG_FLAGS & (1 << cls))) return -1;
    snprintf( head, sizeof(head), "%s:%s:%s ", cls == __WINE_DBCL_ERR ? "err" : "fixme", channel->name, function );
    return __wine_dbg_output( head );
}

/* -------------------------------------------------------------------------------------------------------- the guest window */
#define WINDOW_SIZE ((uint64_t)1 << 32)
#define GUEST_HEAP_START 0x00400000u
#define GUEST_HEAP_END   0x08000000u
#define GUEST_VM_START   0x10000000u

static uint8_t *B;                         /* host address of guest address 0 */
static uint32_t guest_brk = GUEST_HEAP_START;

static void *H( uint32_t g ) { return g ? B + g : NULL; }

static int in_window( const void *host )
{
    return (const uint8_t *)host >= B && (const uint8_t *)host < B + WINDOW_SIZE;
}

static uint32_t galloc( size_t size )
{
    uint32_t g = guest_brk;
    guest_brk += (size + 15) & ~(size_t)15;
    if (guest_brk > GUEST_HEAP_END) { printf( "FAIL the harness's guest heap is full\n" ); exit( 3 ); }
    memset( H( g ), 0, size );
    return g;
}

static uint32_t gstr( const char *s )
{
    uint32_t g = galloc( strlen( s ) + 1 );
    strcpy( H( g ), s );
    return g;
}

/* ------------------------------------------------------------------------------------------- the two Windows processes */
struct fake_process
{
    PEB peb;
    TEB teb;
    int wow;
    UINT64 report_cb, utils_cb;      /* the entry points of this process's winevulkan.dll */
};
static struct fake_process proc32, proc64;
static struct fake_process *cur;
static int foreign_thread;            /* the calling thread is one of the host's own: no TEB */

static void enter( struct fake_process *p ) { cur = p; }

TEB * WINAPI NtCurrentTeb(void) { return foreign_thread ? NULL : &cur->teb; }

/* build/ntdll-unix/virtual_ios.c: the calling process's window base, 0 for a 64-bit process */
ULONG_PTR ios_wow_base(void) { return cur && cur->wow ? (ULONG_PTR)B : 0; }

/* build/win32u-unix/syscall_ios.c: the calling process's allocation ceiling, a guest number; 0 for a 64-bit process */
static ULONG_PTR guest_ceiling = 0xffffffff;
ULONG_PTR win32u_zero_bits(void) { return cur && cur->wow ? guest_ceiling : 0; }

/* ---------------------------------------------------------------------------------------------------- virtual memory */
#define HOST_PAGE 0x4000u            /* iOS */
struct vm_alloc { uint8_t *addr; size_t size; int live; int in_window; };
static struct vm_alloc vm_allocs[1024];
static int vm_count, vm_window_live, vm_window_total, vm_window_frees, vm_host_total, vm_calls;
static uint32_t vm_next = GUEST_VM_START;
static size_t vm_last_window_size, vm_last_window_request;
static int vm_refuse_next;           /* the next window allocation finds no room */

NTSTATUS WINAPI NtAllocateVirtualMemory( HANDLE process, PVOID *ret, ULONG_PTR zero_bits, SIZE_T *size_ptr, ULONG type, ULONG protect )
{
    size_t size = (*size_ptr + 0xfff) & ~(size_t)0xfff;                 /* Windows pages */
    size_t host = (size + HOST_PAGE - 1) & ~(size_t)(HOST_PAGE - 1);    /* what is really committed */
    struct vm_alloc *a = &vm_allocs[vm_count];
    uint8_t *p;

    vm_calls++;
    if (*ret || vm_count == 1024) { printf( "FAIL NtAllocateVirtualMemory: a case the harness has no model for\n" ); exit( 3 ); }
    if (!zero_bits)
    {
        p = mmap( NULL, host, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 );
        if (p == MAP_FAILED) return STATUS_NO_MEMORY;
        vm_host_total++;
        a->in_window = 0;
    }
    else
    {
        uint64_t limit = zero_bits >= 32 ? zero_bits : (0xffffffffu >> zero_bits);
        uint32_t g = vm_next;

        if (!ios_wow_base()) { printf( "FAIL a guest ceiling from a process without a window\n" ); exit( 3 ); }
        if (vm_refuse_next || (uint64_t)g + host - 1 > limit) { vm_refuse_next = 0; return STATUS_NO_MEMORY; }
        vm_next += (host + 0xffff) & ~(size_t)0xffff;                    /* 64 KB allocation granularity */
        p = mmap( B + g, host, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 );
        if (p == MAP_FAILED) return STATUS_NO_MEMORY;
        vm_window_live++;
        vm_window_total++;
        vm_last_window_size = host;
        vm_last_window_request = *size_ptr;
        a->in_window = 1;
    }
    a->addr = p; a->size = host; a->live = 1;
    vm_count++;
    *ret = p;
    *size_ptr = size;
    return STATUS_SUCCESS;
}

NTSTATUS WINAPI NtFreeVirtualMemory( HANDLE process, PVOID *addr, SIZE_T *size_ptr, ULONG type )
{
    int i;
    for (i = 0; i < vm_count; i++)
    {
        struct vm_alloc *a = &vm_allocs[i];
        if (!a->live || a->addr != *addr) continue;
        a->live = 0;
        if (a->in_window)
        {
            /* back to reserved, no access: a later touch faults */
            mmap( a->addr, a->size, PROT_NONE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 );
            vm_window_live--;
            vm_window_frees++;
        }
        else munmap( a->addr, a->size );
        return STATUS_SUCCESS;
    }
    CHECK( 0, "NtFreeVirtualMemory of %p, which is not a live allocation\n", *addr );
    return STATUS_INVALID_PARAMETER;
}

/* --------------------------------------------------- user callbacks: the WoW64 layer, user32 and winevulkan.dll's entries */
static struct
{
    int calls, kind;                 /* kind 1: debug utils, 2: debug report */
    UINT64 dispatch, user_callback, user_data;
    UINT32 object_type, object_count, flags;
    UINT64 object_handle;
    char object_name[64], message[64], id_name[64];
    int block_in_window;
} cb;

NTSTATUS KeUserModeCallback( ULONG id, const void *args, ULONG len, void **ret_ptr, ULONG *ret_len )
{
    static VkBool32 answer;
    const struct dispatch_callback_params *dispatch;
    const void *block = args;

    CHECK( id == NtUserCallDispatchCallback, "KeUserModeCallback id %u\n", (unsigned)id );
    CHECK( !foreign_thread, "a user callback was dispatched on a thread without a TEB\n" );
    if (cur->wow)
    {
        /* the WoW64 layer copies the block onto the 32-bit stack; the 32-bit side reads it there */
        uint32_t g = galloc( len );
        memcpy( H( g ), args, len );
        block = H( g );
    }
    dispatch = block;
    {
        int calls = cb.calls;
        memset( &cb, 0, sizeof(cb) );
        cb.calls = calls + 1;
    }
    cb.block_in_window = in_window( block );
    cb.dispatch = dispatch->callback;
    if (dispatch->callback == cur->utils_cb)
    {
        /* dlls/winevulkan/loader.c, call_vulkan_debug_utils_callback */
        const struct wine_vk_debug_utils_params *params = block;
        UINT size = sizeof(*params) + sizeof(struct debug_utils_label) * (params->queue_label_count + params->cmd_buf_label_count) +
                    sizeof(struct debug_utils_object) * params->object_count;
        const char *ptr = (const char *)(params + 1), *strings = (const char *)params + size;
        UINT i;

        cb.kind = 1;
        cb.user_callback = params->user_callback;
        cb.user_data = params->user_data;
        cb.object_count = params->object_count;
        if (params->message_id_name_len) snprintf( cb.id_name, sizeof(cb.id_name), "%s", strings );
        strings += params->message_id_name_len;
        if (params->message_len) snprintf( cb.message, sizeof(cb.message), "%s", strings );
        strings += params->message_len;
        for (i = 0; i < params->queue_label_count + params->cmd_buf_label_count; i++)
        {
            strings += ((const struct debug_utils_label *)ptr)->label_name_len;
            ptr += sizeof(struct debug_utils_label);
        }
        for (i = 0; i < params->object_count; i++)
        {
            const struct debug_utils_object *object = (const struct debug_utils_object *)ptr;
            if (!i)
            {
                cb.object_type = object->object_type;
                cb.object_handle = object->object_handle;
                if (object->object_name_len) snprintf( cb.object_name, sizeof(cb.object_name), "%s", strings );
            }
            strings += object->object_name_len;
            ptr += sizeof(*object);
        }
    }
    else if (dispatch->callback == cur->report_cb)
    {
        /* call_vulkan_debug_report_callback */
        const struct wine_vk_debug_report_params *params = block;
        const char *strings = (const char *)(params + 1);

        cb.kind = 2;
        cb.user_callback = params->user_callback;
        cb.user_data = params->user_data;
        cb.flags = params->flags;
        cb.object_type = params->object_type;
        cb.object_handle = params->object_handle;
        strings += params->layer_len;
        if (params->message_len) snprintf( cb.message, sizeof(cb.message), "%s", strings );
    }
    answer = VK_FALSE;
    *ret_ptr = &answer;
    *ret_len = sizeof(answer);
    return STATUS_SUCCESS;
}

/* --------------------------------------------------------------------------------------------------------------- unix calls */
static void call32( unsigned int code, uint32_t gparams, const char *what )
{
    step = what;
    __wine_unix_call_wow64_funcs[code]( H( gparams ) );     /* the WoW64 layer converts this one pointer */
}

static void call64( unsigned int code, void *params, const char *what )
{
    step = what;
    __wine_unix_call_funcs[code]( params );
}

static struct mockvk_state *mock;
static void (*mock_fire)( const char * );
extern uintptr_t stub_layer_hwnd;
extern int stub_layer_acquires, stub_layer_releases;

static int has_name( char names[][MOCKVK_NAME], uint32_t count, const char *name )
{
    uint32_t i;
    for (i = 0; i < count; i++) if (!strcmp( names[i], name )) return 1;
    return 0;
}

/* a client object, as dlls/winevulkan/loader.c's vulkan_client_object_create makes it, in the program's own memory */
static uint32_t client_object32( size_t size )
{
    uint32_t g = galloc( size );
    ((struct vulkan_client_object *)H( g ))->loader_magic = VULKAN_ICD_MAGIC_VALUE;
    return g;
}

#define P32(name, var) uint32_t var##_g = galloc( sizeof(struct p32_##name) ); struct p32_##name *var = H( var##_g )

/* =============================================================================================== the 32-bit program's session */
static uint32_t instance32, physdev32, device32, queue32;
#define UTILS_FN32   0x00501230u     /* the program's own callback functions and their user data: guest numbers */
#define UTILS_DATA32 0x00504560u
#define REPORT_FN32  0x00507890u
#define REPORT_DATA32 0x0050abc0u

static void init32(void)
{
    struct { UINT64 report; UINT64 utils; ULONG extensions; } *params;
    uint32_t g = galloc( sizeof(*params) ), ext = galloc( sizeof(struct vulkan_instance_extensions) );
    struct vulkan_instance_extensions *extensions = H( ext );
    P32( vkEnumerateInstanceVersion, ver );
    uint32_t version = galloc( 4 );
    NTSTATUS status;

    enter( &proc32 );
    params = H( g );
    params->report = proc32.report_cb;
    params->utils = proc32.utils_cb;
    params->extensions = ext;
    step = "init (32-bit)";
    status = __wine_unix_call_wow64_funcs[unix_init]( params );
    CHECK( !status, "init failed: %#x\n", (unsigned)status );
    CHECK( extensions->has_VK_KHR_surface && extensions->has_VK_KHR_win32_surface, "the client extensions were not written into the program's memory\n" );
    CHECK( extensions->has_VK_EXT_debug_utils && extensions->has_VK_EXT_debug_report, "debug extensions missing\n" );
    CHECK( !extensions->has_VK_EXT_metal_surface, "VK_EXT_metal_surface must not reach a Windows program\n" );

    ver->pApiVersion = version;
    call32( unix_vkEnumerateInstanceVersion, ver_g, "vkEnumerateInstanceVersion" );
    CHECK( ver->result == VK_SUCCESS && *(uint32_t *)H( version ) >= VK_API_VERSION_1_1, "version %#x\n", *(uint32_t *)H( version ) );

    group( "32-bit: init reads its block with the 32-bit layout and writes the extension list through a guest pointer" );
}

static void create_instance32(void)
{
    static const char *const names[] = { "VK_KHR_surface", "VK_KHR_win32_surface", "VK_EXT_debug_utils", "VK_EXT_debug_report" };
    uint32_t app_g = galloc( sizeof(VkApplicationInfo32) ), info_g = galloc( sizeof(VkInstanceCreateInfo32) );
    uint32_t dbg_g = galloc( sizeof(VkDebugUtilsMessengerCreateInfoEXT32) ), names_g = galloc( 4 * sizeof(PTR32) );
    uint32_t slot_g = galloc( sizeof(PTR32) ), count_g = galloc( 4 ), list_g = galloc( 4 * sizeof(PTR32) );
    VkApplicationInfo32 *app = H( app_g );
    VkInstanceCreateInfo32 *info = H( info_g );
    VkDebugUtilsMessengerCreateInfoEXT32 *dbg = H( dbg_g );
    struct VkInstance_T *client;
    P32( vkCreateInstance, params );
    P32( vkEnumeratePhysicalDevices, enumerate );
    P32( is_available, avail );
    unsigned int i;

    for (i = 0; i < 4; i++) ((PTR32 *)H( names_g ))[i] = gstr( names[i] );
    app->sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    app->pApplicationName = gstr( "vk32 harness" );
    app->pEngineName = gstr( "guest engine" );
    app->apiVersion = VK_API_VERSION_1_1;
    dbg->sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT;
    dbg->messageSeverity = dbg->messageType = 0xffff;
    dbg->pfnUserCallback = UTILS_FN32;
    dbg->pUserData = UTILS_DATA32;
    info->sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    info->pNext = dbg_g;
    info->pApplicationInfo = app_g;
    info->enabledExtensionCount = 4;
    info->ppEnabledExtensionNames = names_g;

    /* loader.c vkCreateInstance: the client object with room for 8 physical devices */
    instance32 = client_object32( offsetof(struct VkInstance_T, physical_device[8]) );
    client = H( instance32 );
    client->physical_device_count = 8;
    for (i = 0; i < 8; i++) client->physical_device[i].obj.loader_magic = VULKAN_ICD_MAGIC_VALUE;
    client->extensions.has_VK_KHR_surface = client->extensions.has_VK_KHR_win32_surface = 1;
    client->extensions.has_VK_EXT_debug_utils = client->extensions.has_VK_EXT_debug_report = 1;
    *(PTR32 *)H( slot_g ) = instance32;

    params->pCreateInfo = info_g;
    params->pInstance = slot_g;
    call32( unix_vkCreateInstance, params_g, "vkCreateInstance" );
    CHECK( params->result == VK_SUCCESS, "vkCreateInstance: %d\n", params->result );
    CHECK( !strcmp( mock->app_name, "vk32 harness" ) && !strcmp( mock->engine_name, "guest engine" ),
           "the library read application '%s', engine '%s'\n", mock->app_name, mock->engine_name );
    CHECK( has_name( mock->instance_exts, mock->instance_ext_count, "VK_EXT_metal_surface" ) &&
           !has_name( mock->instance_exts, mock->instance_ext_count, "VK_KHR_win32_surface" ),
           "the host instance did not get VK_EXT_metal_surface in place of VK_KHR_win32_surface\n" );
    CHECK( has_name( mock->instance_exts, mock->instance_ext_count, "VK_KHR_external_memory_capabilities" ),
           "a 32-bit program's instance needs VK_KHR_external_memory_capabilities on the host\n" );
    CHECK( *(PTR32 *)H( slot_g ) == instance32, "the instance handle came back as %#x, not the program's %#x\n", *(PTR32 *)H( slot_g ), instance32 );
    CHECK( client->obj.unix_handle && client->physical_device_count == 1, "the client instance was not filled in (unix_handle %#llx, %u devices)\n",
           (unsigned long long)client->obj.unix_handle, client->physical_device_count );
    group( "32-bit: vkCreateInstance converts the create info, its chain, the string array and the in/out handle" );

    enumerate->instance = instance32;
    enumerate->pPhysicalDeviceCount = count_g;
    enumerate->pPhysicalDevices = 0;
    call32( unix_vkEnumeratePhysicalDevices, enumerate_g, "vkEnumeratePhysicalDevices (count)" );
    CHECK( enumerate->result == VK_SUCCESS && *(uint32_t *)H( count_g ) == 1, "count %u\n", *(uint32_t *)H( count_g ) );
    enumerate->pPhysicalDevices = list_g;
    call32( unix_vkEnumeratePhysicalDevices, enumerate_g, "vkEnumeratePhysicalDevices" );
    physdev32 = ((PTR32 *)H( list_g ))[0];
    CHECK( enumerate->result == VK_SUCCESS && physdev32 == instance32 + offsetof(struct VkInstance_T, physical_device[0]),
           "the physical device handle is %#x, not the guest address of the client object (%#x)\n", physdev32,
           (unsigned)(instance32 + offsetof(struct VkInstance_T, physical_device[0])) );

    avail->handle = instance32;
    avail->name = gstr( "vkCreateDevice" );
    step = "is_available_instance_function";
    CHECK( __wine_unix_call_wow64_funcs[unix_is_available_instance_function]( avail ) == 1, "vkCreateDevice is not available\n" );
    avail->name = gstr( "vkNoSuchFunction" );
    CHECK( __wine_unix_call_wow64_funcs[unix_is_available_instance_function]( avail ) == 0, "an unknown function is available\n" );
    group( "32-bit: dispatchable handles go out as guest addresses (physical device), is_available_* reads guest pointers" );
}

static void properties32(void)
{
    uint32_t props_g = galloc( sizeof(VkPhysicalDeviceProperties232) ), ext_g = galloc( sizeof(VkPhysicalDeviceExternalMemoryHostPropertiesEXT32) );
    uint32_t mem_g = galloc( sizeof(VkPhysicalDeviceMemoryProperties32) );
    VkPhysicalDeviceProperties232 *props = H( props_g );
    VkPhysicalDeviceExternalMemoryHostPropertiesEXT32 *ext = H( ext_g );
    VkPhysicalDeviceMemoryProperties32 *mem = H( mem_g );
    P32( vkGetPhysicalDeviceProperties2, params );
    P32( vkGetPhysicalDeviceMemoryProperties, memparams );

    props->sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
    props->pNext = ext_g;
    ext->sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT;
    params->physicalDevice = physdev32;
    params->pProperties = props_g;
    call32( unix_vkGetPhysicalDeviceProperties2, params_g, "vkGetPhysicalDeviceProperties2" );
    CHECK( !strcmp( props->properties.deviceName, MOCKVK_DEVICE_NAME ), "device name '%s'\n", props->properties.deviceName );
    CHECK( props->pNext == ext_g, "the output chain's pNext was changed to %#x\n", props->pNext );
    CHECK( ext->minImportedHostPointerAlignment == 16384, "the chained output struct was not written: alignment %llu\n",
           (unsigned long long)ext->minImportedHostPointerAlignment );

    memparams->physicalDevice = physdev32;
    memparams->pMemoryProperties = mem_g;
    call32( unix_vkGetPhysicalDeviceMemoryProperties, memparams_g, "vkGetPhysicalDeviceMemoryProperties" );
    CHECK( mem->memoryTypeCount == 3 && (mem->memoryTypes[1].propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) &&
           mem->memoryHeapCount == 1 && mem->memoryHeaps[0].size == (VkDeviceSize)4 << 30, "memory properties wrong\n" );
    group( "32-bit: output structs and an output pNext chain are written through guest pointers" );
}

/* VK_SUCCESS, or the refusal when the library can place no mapping */
static VkResult create_device32(void)
{
    uint32_t info_g = galloc( sizeof(VkDeviceCreateInfo32) ), queue_g = galloc( sizeof(VkDeviceQueueCreateInfo32) );
    uint32_t feat_g = galloc( sizeof(VkPhysicalDeviceFeatures232) ), prio_g = galloc( sizeof(float) ), names_g = galloc( sizeof(PTR32) );
    uint32_t slot_g = galloc( sizeof(PTR32) ), qslot_g = galloc( sizeof(PTR32) );
    VkDeviceCreateInfo32 *info = H( info_g );
    VkDeviceQueueCreateInfo32 *queue = H( queue_g );
    VkPhysicalDeviceFeatures232 *feat = H( feat_g );
    struct VkDevice_T *client;
    P32( vkCreateDevice, params );
    P32( vkGetDeviceQueue, getqueue );

    *(float *)H( prio_g ) = 0.75f;
    *(PTR32 *)H( names_g ) = gstr( "VK_KHR_swapchain" );
    feat->sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    feat->features.samplerAnisotropy = VK_TRUE;
    queue->sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    queue->queueCount = 1;
    queue->pQueuePriorities = prio_g;
    info->sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    info->pNext = feat_g;
    info->queueCreateInfoCount = 1;
    info->pQueueCreateInfos = queue_g;
    info->enabledExtensionCount = 1;
    info->ppEnabledExtensionNames = names_g;

    device32 = client_object32( offsetof(struct VkDevice_T, queues[1]) );
    client = H( device32 );
    client->queues[0].obj.loader_magic = VULKAN_ICD_MAGIC_VALUE;
    client->extensions.has_VK_KHR_swapchain = 1;
    *(PTR32 *)H( slot_g ) = device32;

    params->physicalDevice = physdev32;
    params->pCreateInfo = info_g;
    params->pDevice = slot_g;
    call32( unix_vkCreateDevice, params_g, "vkCreateDevice" );
    if (params->result != VK_SUCCESS) return params->result;

    CHECK( *(PTR32 *)H( slot_g ) == device32 && client->obj.unix_handle, "the device handle or client object is wrong\n" );
    CHECK( mock->device_queue_count == 1 && mock->device_queue_priority == 0.75f && mock->device_anisotropy == VK_TRUE,
           "the library read queue count %u, priority %f, anisotropy %u\n", mock->device_queue_count, mock->device_queue_priority, mock->device_anisotropy );
    CHECK( has_name( mock->device_exts, mock->device_ext_count, "VK_KHR_swapchain" ), "VK_KHR_swapchain was not enabled on the host\n" );

    getqueue->device = device32;
    getqueue->pQueue = qslot_g;
    call32( unix_vkGetDeviceQueue, getqueue_g, "vkGetDeviceQueue" );
    queue32 = *(PTR32 *)H( qslot_g );
    CHECK( queue32 == device32 + offsetof(struct VkDevice_T, queues[0]), "the queue handle is %#x, not the guest address of the client queue\n", queue32 );
    return VK_SUCCESS;
}

static uint64_t create_buffer32( uint64_t size )
{
    uint32_t info_g = galloc( sizeof(VkBufferCreateInfo32) ), slot_g = galloc( 8 );
    VkBufferCreateInfo32 *info = H( info_g );
    P32( vkCreateBuffer, params );

    info->sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
    info->size = size;
    info->usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    params->device = device32;
    params->pCreateInfo = info_g;
    params->pBuffer = slot_g;
    call32( unix_vkCreateBuffer, params_g, "vkCreateBuffer" );
    CHECK( params->result == VK_SUCCESS && mock->last_buffer_size == size, "vkCreateBuffer: %d, size seen %llu\n", params->result,
           (unsigned long long)mock->last_buffer_size );
    return *(uint64_t *)H( slot_g );
}

static VkResult allocate32( uint64_t size, uint32_t type, int with_chain, uint64_t *memory )
{
    uint32_t info_g = galloc( sizeof(VkMemoryAllocateInfo32) ), flags_g = galloc( sizeof(VkMemoryAllocateFlagsInfo32) ), slot_g = galloc( 8 );
    VkMemoryAllocateInfo32 *info = H( info_g );
    VkMemoryAllocateFlagsInfo32 *flags = H( flags_g );
    P32( vkAllocateMemory, params );

    info->sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    info->allocationSize = size;
    info->memoryTypeIndex = type;
    if (with_chain)
    {
        flags->sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO;
        flags->flags = VK_MEMORY_ALLOCATE_DEVICE_MASK_BIT;
        flags->deviceMask = 1;
        info->pNext = flags_g;
    }
    params->device = device32;
    params->pAllocateInfo = info_g;
    params->pMemory = slot_g;
    call32( unix_vkAllocateMemory, params_g, "vkAllocateMemory" );
    *memory = *(uint64_t *)H( slot_g );
    return params->result;
}

static void free32( uint64_t memory )
{
    P32( vkFreeMemory, params );
    params->device = device32;
    params->memory = memory;
    call32( unix_vkFreeMemory, params_g, "vkFreeMemory" );
}

static VkResult map32( uint64_t memory, uint64_t offset, uint32_t *pointer )
{
    uint32_t slot_g = galloc( sizeof(PTR32) );
    P32( vkMapMemory, params );
    params->device = device32;
    params->memory = memory;
    params->offset = offset;
    params->size = VK_WHOLE_SIZE;
    params->ppData = slot_g;
    call32( unix_vkMapMemory, params_g, "vkMapMemory" );
    *pointer = *(PTR32 *)H( slot_g );
    return params->result;
}

static VkResult map2_32( uint64_t memory, uint64_t offset, uint32_t *pointer )
{
    uint32_t slot_g = galloc( sizeof(PTR32) ), info_g = galloc( sizeof(VkMemoryMapInfo32) );
    VkMemoryMapInfo32 *info = H( info_g );
    P32( vkMapMemory2KHR, params );
    info->sType = VK_STRUCTURE_TYPE_MEMORY_MAP_INFO;
    info->memory = memory;
    info->offset = offset;
    info->size = VK_WHOLE_SIZE;
    params->device = device32;
    params->pMemoryMapInfo = info_g;
    params->ppData = slot_g;
    call32( unix_vkMapMemory2KHR, params_g, "vkMapMemory2KHR" );
    *pointer = *(PTR32 *)H( slot_g );
    return params->result;
}

static void unmap32( uint64_t memory )
{
    P32( vkUnmapMemory, params );
    params->device = device32;
    params->memory = memory;
    call32( unix_vkUnmapMemory, params_g, "vkUnmapMemory" );
}

static void bind32( uint64_t buffer, uint64_t memory, uint64_t offset )
{
    P32( vkBindBufferMemory, params );
    params->device = device32;
    params->buffer = buffer;
    params->memory = memory;
    params->memoryOffset = offset;
    call32( unix_vkBindBufferMemory, params_g, "vkBindBufferMemory" );
    CHECK( params->result == VK_SUCCESS, "vkBindBufferMemory: %d\n", params->result );
}

static uint32_t pool32, cmdbuf32;

static void commands32(void)
{
    uint32_t info_g = galloc( sizeof(VkCommandPoolCreateInfo32) ), slot_g = galloc( 8 ), alloc_g = galloc( sizeof(VkCommandBufferAllocateInfo32) );
    uint32_t list_g = galloc( sizeof(PTR32) );
    VkCommandPoolCreateInfo32 *info = H( info_g );
    VkCommandBufferAllocateInfo32 *alloc = H( alloc_g );
    P32( vkCreateCommandPool, params );
    P32( vkAllocateCommandBuffers, allocate );

    /* loader.c vkCreateCommandPool: the handle IS the client object's address, as a 64-bit number */
    pool32 = client_object32( sizeof(struct vulkan_client_object) + 2 * sizeof(PTR32) );
    *(uint64_t *)H( slot_g ) = pool32;
    info->sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    info->flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    params->device = device32;
    params->pCreateInfo = info_g;
    params->pCommandPool = slot_g;
    call32( unix_vkCreateCommandPool, params_g, "vkCreateCommandPool" );
    CHECK( params->result == VK_SUCCESS && ((struct vulkan_client_object *)H( pool32 ))->unix_handle && *(uint64_t *)H( slot_g ) == pool32,
           "vkCreateCommandPool: %d\n", params->result );
    CHECK( mock->pool_flags == VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, "pool flags %#x\n", mock->pool_flags );

    cmdbuf32 = client_object32( sizeof(struct vulkan_client_object) + 2 * sizeof(PTR32) );
    *(PTR32 *)H( list_g ) = cmdbuf32;
    alloc->sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    alloc->commandPool = pool32;
    alloc->commandBufferCount = 1;
    allocate->device = device32;
    allocate->pAllocateInfo = alloc_g;
    allocate->pCommandBuffers = list_g;
    call32( unix_vkAllocateCommandBuffers, allocate_g, "vkAllocateCommandBuffers" );
    CHECK( allocate->result == VK_SUCCESS && ((struct vulkan_client_object *)H( cmdbuf32 ))->unix_handle, "vkAllocateCommandBuffers: %d\n", allocate->result );
    CHECK( mock->cmdbuf_pool_seen == mock->last_pool, "the library got pool %#llx, not its own %#llx: the client pool's address was not found\n",
           (unsigned long long)mock->cmdbuf_pool_seen, (unsigned long long)mock->last_pool );
    group( "32-bit: a command pool's client object is reached through its 64-bit handle value, command buffers through a handle array" );
}

static void copy32( uint64_t src, uint64_t dst, uint64_t size )
{
    uint32_t begin_g = galloc( sizeof(VkCommandBufferBeginInfo32) ), region_g = galloc( sizeof(VkBufferCopy32) );
    uint32_t submit_g = galloc( sizeof(VkSubmitInfo32) ), list_g = galloc( sizeof(PTR32) );
    VkCommandBufferBeginInfo32 *begin = H( begin_g );
    VkBufferCopy32 *region = H( region_g );
    VkSubmitInfo32 *submit = H( submit_g );
    P32( vkBeginCommandBuffer, b );
    P32( vkCmdCopyBuffer, c );
    P32( vkEndCommandBuffer, e );
    P32( vkQueueSubmit, s );
    P32( vkQueueWaitIdle, w );

    begin->sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    begin->flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    b->commandBuffer = cmdbuf32;
    b->pBeginInfo = begin_g;
    call32( unix_vkBeginCommandBuffer, b_g, "vkBeginCommandBuffer" );
    CHECK( b->result == VK_SUCCESS && mock->begin_flags == VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, "vkBeginCommandBuffer\n" );

    region->size = size;
    c->commandBuffer = cmdbuf32;
    c->srcBuffer = src;
    c->dstBuffer = dst;
    c->regionCount = 1;
    c->pRegions = region_g;
    call32( unix_vkCmdCopyBuffer, c_g, "vkCmdCopyBuffer" );
    e->commandBuffer = cmdbuf32;
    call32( unix_vkEndCommandBuffer, e_g, "vkEndCommandBuffer" );

    *(PTR32 *)H( list_g ) = cmdbuf32;
    submit->sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    submit->commandBufferCount = 1;
    submit->pCommandBuffers = list_g;
    s->queue = queue32;
    s->submitCount = 1;
    s->pSubmits = submit_g;
    call32( unix_vkQueueSubmit, s_g, "vkQueueSubmit" );
    CHECK( s->result == VK_SUCCESS, "vkQueueSubmit: %d\n", s->result );
    w->queue = queue32;
    call32( unix_vkQueueWaitIdle, w_g, "vkQueueWaitIdle" );
    CHECK( w->result == VK_SUCCESS, "vkQueueWaitIdle: %d\n", w->result );
}

/* the mapped-memory path of MoltenVK's extension set: host memory inside the window, imported */
static void memory32_host(void)
{
    uint64_t buf_a = create_buffer32( 20000 ), buf_b = create_buffer32( 20000 ), mem_a = 0, mem_b = 0, mem_c = 0;
    uint32_t ptr_a = 0, ptr_b = 0, ptr_c = 0, reqs_g = galloc( sizeof(VkMemoryRequirements32) );
    VkMemoryRequirements32 *reqs = H( reqs_g );
    int live0 = vm_window_live, host0 = vm_host_total, imported0 = mock->imported_allocs, i;
    uint64_t import_a, import_b;
    uint8_t *a, *b;
    VkResult res;
    P32( vkGetBufferMemoryRequirements, getreqs );
    P32( vkDestroyBuffer, destroy );

    CHECK( mock->last_buffer_external == VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,
           "a 32-bit program's buffer is created without the host-allocation external type (%#x)\n", mock->last_buffer_external );
    getreqs->device = device32;
    getreqs->buffer = buf_a;
    getreqs->pMemoryRequirements = reqs_g;
    call32( unix_vkGetBufferMemoryRequirements, getreqs_g, "vkGetBufferMemoryRequirements" );
    CHECK( reqs->size == 20000 && reqs->alignment == 16 && reqs->memoryTypeBits == 3, "memory requirements wrong\n" );

    res = allocate32( 20000, 1, 1, &mem_a );
    import_a = mock->last_import_pointer;
    CHECK( res == VK_SUCCESS && mem_a, "vkAllocateMemory (host-visible): %d\n", res );
    CHECK( mock->last_alloc_imported, "host-visible memory of a 32-bit program was not imported as host memory\n" );
    CHECK( in_window( (void *)(uintptr_t)import_a ), "the imported memory at %#llx is outside the program's window [%p, +4 GB)\n",
           (unsigned long long)import_a, B );
    CHECK( !(import_a & 0x3fff) && mock->last_alloc_size == 32768 && !mock->import_misaligned,
           "the import is not 16 KB aligned and sized (pointer %#llx, size %llu)\n", (unsigned long long)import_a, (unsigned long long)mock->last_alloc_size );
    CHECK( vm_last_window_size == 32768 && vm_last_window_request == 32768,
           "the allocation behind the import asks for %zu bytes (%zu committed), the import is 32768: on a host with 4 KB pages the tail would not be committed\n",
           vm_last_window_request, vm_last_window_size );
    CHECK( mock->last_alloc_flags_seen == (0x80000000u | VK_MEMORY_ALLOCATE_DEVICE_MASK_BIT), "the program's own pNext chain was lost (%#x)\n", mock->last_alloc_flags_seen );
    CHECK( vm_window_live == live0 + 1 && vm_host_total == host0, "window allocations %d (+1 expected), host allocations %d (+0)\n",
           vm_window_live - live0, vm_host_total - host0 );
    res = allocate32( 20000, 1, 0, &mem_b );
    import_b = mock->last_import_pointer;
    CHECK( res == VK_SUCCESS && mock->last_alloc_imported && in_window( (void *)(uintptr_t)import_b ) && import_b != import_a, "second allocation\n" );
    group( "32-bit: host-visible memory is allocated inside the program's window, 16 KB aligned, and imported (VK_EXT_external_memory_host)" );

    bind32( buf_a, mem_a, 0 );
    bind32( buf_b, mem_b, 4096 );
    res = map32( mem_a, 0, &ptr_a );
    CHECK( res == VK_SUCCESS && ptr_a && H( ptr_a ) == (void *)(uintptr_t)import_a, "vkMapMemory gave the program %#x (host %p), the memory is at %#llx\n",
           ptr_a, H( ptr_a ), (unsigned long long)import_a );
    res = map2_32( mem_b, 4096, &ptr_b );
    CHECK( res == VK_SUCCESS && ptr_b && H( ptr_b ) == (void *)(uintptr_t)(import_b + 4096), "vkMapMemory2KHR gave the program %#x\n", ptr_b );
    a = H( ptr_a ); b = H( ptr_b );
    for (i = 0; i < 20000; i++) a[i] = (uint8_t)(i * 7 + 3);
    memset( b, 0, 8192 );
    copy32( buf_a, buf_b, 8192 );
    CHECK( mock->copies_executed >= 1 && !memcmp( a, b, 8192 ), "bytes written through one mapping did not arrive in the other after the copy\n" );
    group( "32-bit: vkMapMemory and vkMapMemory2KHR return 32-bit pointers the program can use; data crosses the library and comes back" );

    unmap32( mem_a );
    unmap32( mem_b );
    free32( mem_a );
    free32( mem_b );
    CHECK( vm_window_live == live0, "%d window allocation(s) of freed Vulkan memory were never released (upstream's leak)\n", vm_window_live - live0 );
    CHECK( mock->live_memories == 0, "the library still holds %u memory object(s)\n", mock->live_memories );
    group( "32-bit: vkFreeMemory gives the window range back" );

    /* the library refuses the allocation: the window range must not be lost */
    mock->fail_next_alloc = 1;
    res = allocate32( 65536, 1, 0, &mem_c );
    CHECK( res != VK_SUCCESS && mock->last_alloc_imported, "the forced failure did not happen (%d)\n", res );
    CHECK( vm_window_live == live0, "a failed vkAllocateMemory left %d window allocation(s) behind\n", vm_window_live - live0 );
    /* no room in the window: a clean error and a log line */
    vm_refuse_next = 1;
    loglen = 0; logbuf[0] = 0;
    res = allocate32( 65536, 1, 0, &mem_c );
    CHECK( res == VK_ERROR_OUT_OF_HOST_MEMORY && strstr( logbuf, "[winios-vk] no room in the 32-bit program's address space" ),
           "a full window: result %d, log '%s'\n", res, logbuf );
    CHECK( vm_window_live == live0 && mock->live_memories == 0, "state after the refused allocations\n" );
    group( "32-bit: a failed allocation (the library's, or no room in the window) leaves nothing behind and says why" );

    /* device-local memory: not imported, nothing placed in the window, and not mappable */
    imported0 = mock->imported_allocs;
    res = allocate32( 1 << 20, 0, 0, &mem_c );
    CHECK( res == VK_SUCCESS && !mock->last_alloc_imported && mock->imported_allocs == imported0 && vm_window_live == live0,
           "device-local memory took room in the window\n" );
    res = map32( mem_c, 0, &ptr_c );
    CHECK( res == VK_ERROR_MEMORY_MAP_FAILED, "mapping private memory: %d\n", res );
    free32( mem_c );
    group( "32-bit: device-local memory costs the program no address space" );

    destroy->device = device32;
    destroy->buffer = buf_a;
    call32( unix_vkDestroyBuffer, destroy_g, "vkDestroyBuffer" );
    destroy->buffer = buf_b;
    call32( unix_vkDestroyBuffer, destroy_g, "vkDestroyBuffer" );
}

/* a library with VK_EXT_map_memory_placed: Wine's placed path, the mapping put into the window at map time */
static void memory32_placed(void)
{
    uint64_t buf_a = create_buffer32( 20000 ), buf_b = create_buffer32( 20000 ), mem_a = 0, mem_b = 0;
    uint32_t ptr_a = 0, ptr_b = 0;
    int live0 = vm_window_live, i;
    uint8_t *a, *b;
    VkResult res;

    CHECK( mock->device_placed_feature == VK_TRUE && has_name( mock->device_exts, mock->device_ext_count, "VK_EXT_map_memory_placed" ),
           "the placed-map feature was not enabled on the host device\n" );
    res = allocate32( 20000, 1, 0, &mem_a );
    CHECK( res == VK_SUCCESS && !mock->last_alloc_imported && vm_window_live == live0, "with placed maps nothing is imported at allocation\n" );
    res = allocate32( 20000, 1, 0, &mem_b );
    CHECK( res == VK_SUCCESS, "second allocation: %d\n", res );
    bind32( buf_a, mem_a, 0 );
    bind32( buf_b, mem_b, 0 );
    res = map32( mem_a, 0, &ptr_a );
    CHECK( res == VK_SUCCESS && ptr_a && in_window( H( ptr_a ) ) && mock->placed_maps == 1 && vm_window_live == live0 + 1,
           "a placed map: result %d, pointer %#x, placed maps %u, window allocations +%d\n", res, ptr_a, mock->placed_maps, vm_window_live - live0 );
    res = map32( mem_b, 0, &ptr_b );
    CHECK( res == VK_SUCCESS && ptr_b && in_window( H( ptr_b ) ), "second placed map\n" );
    a = H( ptr_a ); b = H( ptr_b );
    for (i = 0; i < 20000; i++) a[i] = (uint8_t)(i * 5 + 1);
    copy32( buf_a, buf_b, 20000 );
    CHECK( !memcmp( a, b, 20000 ), "bytes written through a placed mapping did not arrive in the other\n" );
    unmap32( mem_a );
    unmap32( mem_b );
    CHECK( vm_window_live == live0 && mock->reserve_unmaps == 2, "after unmapping: %d window allocation(s) left, %u reserving unmaps\n",
           vm_window_live - live0, mock->reserve_unmaps );
    free32( mem_a );
    free32( mem_b );
    CHECK( vm_window_live == live0 && mock->live_memories == 0, "after freeing\n" );
    group( "32-bit, a library with VK_EXT_map_memory_placed: mappings are placed inside the window and released at unmap" );
}

/* The call list of the 32-bit device probe (upstream Madeira's tests/x64/vulkan-smoke.c with -DVULKAN_CLEAR_TEST, built for i386:
 * a 16x16 RGBA8 image is cleared on the GPU, copied into a mapped buffer, and all 1024 bytes are compared), call for call, so that
 * everything the probe asks of the unix side has run here first. It uses the session's device, queue, pool and command buffer. */
static void probe32(void)
{
    enum { WIDTH = 16, HEIGHT = 16, BYTES = WIDTH * HEIGHT * 4 };
    uint32_t props_g = galloc( sizeof(VkPhysicalDeviceProperties32) ), feat_g = galloc( sizeof(VkPhysicalDeviceFeatures) );
    uint32_t count_g = galloc( 4 ), exts_g = galloc( 16 * sizeof(VkExtensionProperties) ), fam_g = galloc( 4 * sizeof(VkQueueFamilyProperties) );
    uint32_t fmt_g = galloc( sizeof(VkFormatProperties) ), info_g = galloc( sizeof(VkImageCreateInfo32) ), slot_g = galloc( 8 );
    uint32_t reqs_g = galloc( sizeof(VkMemoryRequirements32) ), range_g = galloc( sizeof(VkMappedMemoryRange32) );
    uint32_t barrier_g = galloc( sizeof(VkImageMemoryBarrier32) ), bbar_g = galloc( sizeof(VkBufferMemoryBarrier32) );
    uint32_t color_g = galloc( sizeof(VkClearColorValue) ), sub_g = galloc( sizeof(VkImageSubresourceRange) );
    uint32_t copy_g = galloc( sizeof(VkBufferImageCopy32) ), begin_g = galloc( sizeof(VkCommandBufferBeginInfo32) );
    uint32_t finfo_g = galloc( sizeof(VkFenceCreateInfo32) ), fslot_g = galloc( 8 ), submit_g = galloc( sizeof(VkSubmitInfo32) );
    uint32_t list_g = galloc( sizeof(PTR32) ), flist_g = galloc( 8 );
    VkPhysicalDeviceProperties32 *props = H( props_g );
    VkPhysicalDeviceFeatures *feat = H( feat_g );
    VkExtensionProperties *exts = H( exts_g );
    VkQueueFamilyProperties *fam = H( fam_g );
    VkFormatProperties *fmt = H( fmt_g );
    VkImageCreateInfo32 *info = H( info_g );
    VkMemoryRequirements32 *reqs = H( reqs_g );
    VkMappedMemoryRange32 *range = H( range_g );
    VkImageMemoryBarrier32 *barrier = H( barrier_g );
    VkBufferMemoryBarrier32 *bbar = H( bbar_g );
    VkClearColorValue *color = H( color_g );
    VkImageSubresourceRange *sub = H( sub_g );
    VkBufferImageCopy32 *copy = H( copy_g );
    VkCommandBufferBeginInfo32 *begin = H( begin_g );
    VkFenceCreateInfo32 *finfo = H( finfo_g );
    VkSubmitInfo32 *submit = H( submit_g );
    uint64_t image, image_memory = 0, buffer, buffer_memory = 0, fence;
    uint32_t bytes_g = 0, i, mismatches = 0, live0 = vm_window_live;
    int placed = getenv( "MOCKVK_MODE" ) && !strcmp( getenv( "MOCKVK_MODE" ), "placed" );
    const uint8_t *bytes;
    VkResult res;
    P32( vkGetPhysicalDeviceProperties, pprops );
    P32( vkGetPhysicalDeviceFeatures, pfeat );
    P32( vkEnumerateDeviceExtensionProperties, pexts );
    P32( vkGetPhysicalDeviceQueueFamilyProperties, pfam );
    P32( vkGetPhysicalDeviceFormatProperties, pfmt );
    P32( vkCreateImage, pimage );
    P32( vkGetImageMemoryRequirements, preqs );
    P32( vkBindImageMemory, pbind );
    P32( vkFlushMappedMemoryRanges, pflush );
    P32( vkInvalidateMappedMemoryRanges, pinval );
    P32( vkBeginCommandBuffer, pbegin );
    P32( vkCmdPipelineBarrier, pbar );
    P32( vkCmdClearColorImage, pclear );
    P32( vkCmdCopyImageToBuffer, pcopy );
    P32( vkEndCommandBuffer, pend );
    P32( vkCreateFence, pfence );
    P32( vkQueueSubmit, psubmit );
    P32( vkWaitForFences, pwait );
    P32( vkDestroyFence, pdfence );
    P32( vkDestroyBuffer, pdbuffer );
    P32( vkDestroyImage, pdimage );

    /* what the probe prints first */
    pprops->physicalDevice = physdev32; pprops->pProperties = props_g;
    call32( unix_vkGetPhysicalDeviceProperties, pprops_g, "probe: vkGetPhysicalDeviceProperties" );
    pfeat->physicalDevice = physdev32; pfeat->pFeatures = feat_g;
    call32( unix_vkGetPhysicalDeviceFeatures, pfeat_g, "probe: vkGetPhysicalDeviceFeatures" );
    CHECK( !strcmp( props->deviceName, MOCKVK_DEVICE_NAME ) && props->vendorID == 0x106b && feat->samplerAnisotropy == VK_TRUE,
           "probe: GPU name '%s', vendor %#x\n", props->deviceName, props->vendorID );
    pexts->physicalDevice = physdev32; pexts->pPropertyCount = count_g;
    call32( unix_vkEnumerateDeviceExtensionProperties, pexts_g, "probe: vkEnumerateDeviceExtensionProperties (count)" );
    CHECK( pexts->result == VK_SUCCESS && *(uint32_t *)H( count_g ) >= 2 && *(uint32_t *)H( count_g ) <= 16, "probe: %u device extensions\n", *(uint32_t *)H( count_g ) );
    pexts->pProperties = exts_g;
    call32( unix_vkEnumerateDeviceExtensionProperties, pexts_g, "probe: vkEnumerateDeviceExtensionProperties" );
    CHECK( pexts->result == VK_SUCCESS && !strcmp( exts[0].extensionName, "VK_KHR_swapchain" ), "probe: first device extension '%s'\n", exts[0].extensionName );
    pfam->physicalDevice = physdev32; pfam->pQueueFamilyPropertyCount = count_g;
    *(uint32_t *)H( count_g ) = 4; pfam->pQueueFamilyProperties = fam_g;
    call32( unix_vkGetPhysicalDeviceQueueFamilyProperties, pfam_g, "probe: vkGetPhysicalDeviceQueueFamilyProperties" );
    CHECK( *(uint32_t *)H( count_g ) == 1 && fam[0].queueCount == 1 && (fam[0].queueFlags & VK_QUEUE_GRAPHICS_BIT), "probe: queue families\n" );
    pfmt->physicalDevice = physdev32; pfmt->format = VK_FORMAT_R8G8B8A8_UNORM; pfmt->pFormatProperties = fmt_g;
    call32( unix_vkGetPhysicalDeviceFormatProperties, pfmt_g, "probe: vkGetPhysicalDeviceFormatProperties" );
    CHECK( (fmt->optimalTilingFeatures & (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT)) ==
           (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT), "probe: RGBA8 transfer features %#x\n", fmt->optimalTilingFeatures );

    /* the image, in device-local memory: nothing of it is in the program's address space */
    info->sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    info->imageType = VK_IMAGE_TYPE_2D;
    info->format = VK_FORMAT_R8G8B8A8_UNORM;
    info->extent.width = WIDTH; info->extent.height = HEIGHT; info->extent.depth = 1;
    info->mipLevels = info->arrayLayers = 1;
    info->samples = VK_SAMPLE_COUNT_1_BIT;
    info->tiling = VK_IMAGE_TILING_OPTIMAL;
    info->usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    pimage->device = device32; pimage->pCreateInfo = info_g; pimage->pImage = slot_g;
    call32( unix_vkCreateImage, pimage_g, "probe: vkCreateImage" );
    image = *(uint64_t *)H( slot_g );
    CHECK( pimage->result == VK_SUCCESS && image && mock->last_image_format == VK_FORMAT_R8G8B8A8_UNORM &&
           mock->last_image_usage == (VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT), "probe: vkCreateImage %d\n", pimage->result );
    preqs->device = device32; preqs->image = image; preqs->pMemoryRequirements = reqs_g;
    call32( unix_vkGetImageMemoryRequirements, preqs_g, "probe: vkGetImageMemoryRequirements" );
    CHECK( reqs->size == BYTES && (reqs->memoryTypeBits & 1), "probe: image memory requirements\n" );
    res = allocate32( reqs->size, 0, 0, &image_memory );
    CHECK( res == VK_SUCCESS && !mock->last_alloc_imported && vm_window_live == live0, "probe: the image's device-local memory: %d\n", res );
    pbind->device = device32; pbind->image = image; pbind->memory = image_memory;
    call32( unix_vkBindImageMemory, pbind_g, "probe: vkBindImageMemory" );
    CHECK( pbind->result == VK_SUCCESS && mock->image_memory_type == 0, "probe: vkBindImageMemory %d\n", pbind->result );

    /* the read-back buffer, in host-visible memory, mapped */
    buffer = create_buffer32( BYTES );
    res = allocate32( BYTES, 1, 0, &buffer_memory );
    CHECK( res == VK_SUCCESS && (placed || (mock->last_alloc_imported && in_window( (void *)(uintptr_t)mock->last_import_pointer ))),
           "probe: the buffer's host-visible memory: %d\n", res );
    bind32( buffer, buffer_memory, 0 );
    res = map32( buffer_memory, 0, &bytes_g );
    CHECK( res == VK_SUCCESS && bytes_g && in_window( H( bytes_g ) ), "probe: vkMapMemory %d, pointer %#x\n", res, bytes_g );
    memset( H( bytes_g ), 0x5a, BYTES );      /* the read-back cannot pass on untouched bytes */
    range->sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
    range->memory = buffer_memory;
    range->size = VK_WHOLE_SIZE;
    pflush->device = device32; pflush->memoryRangeCount = 1; pflush->pMemoryRanges = range_g;
    call32( unix_vkFlushMappedMemoryRanges, pflush_g, "probe: vkFlushMappedMemoryRanges" );
    CHECK( pflush->result == VK_SUCCESS && mock->flushes == 1 && mock->range_size == VK_WHOLE_SIZE, "probe: vkFlushMappedMemoryRanges %d\n", pflush->result );

    /* clear, copy, wait */
    begin->sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    begin->flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    pbegin->commandBuffer = cmdbuf32; pbegin->pBeginInfo = begin_g;
    call32( unix_vkBeginCommandBuffer, pbegin_g, "probe: vkBeginCommandBuffer" );
    sub->aspectMask = VK_IMAGE_ASPECT_COLOR_BIT; sub->levelCount = 1; sub->layerCount = 1;
    barrier->sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    barrier->dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier->newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier->srcQueueFamilyIndex = barrier->dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier->image = image;
    barrier->subresourceRange = *sub;
    pbar->commandBuffer = cmdbuf32;
    pbar->srcStageMask = VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT; pbar->dstStageMask = VK_PIPELINE_STAGE_TRANSFER_BIT;
    pbar->imageMemoryBarrierCount = 1; pbar->pImageMemoryBarriers = barrier_g;
    call32( unix_vkCmdPipelineBarrier, pbar_g, "probe: vkCmdPipelineBarrier (image, to transfer-dst)" );
    color->float32[0] = 1.0f; color->float32[1] = 0.0f; color->float32[2] = 1.0f; color->float32[3] = 1.0f;
    pclear->commandBuffer = cmdbuf32; pclear->image = image; pclear->imageLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    pclear->pColor = color_g; pclear->rangeCount = 1; pclear->pRanges = sub_g;
    call32( unix_vkCmdClearColorImage, pclear_g, "probe: vkCmdClearColorImage" );
    barrier->srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; barrier->dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    barrier->oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL; barrier->newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    pbar->srcStageMask = VK_PIPELINE_STAGE_TRANSFER_BIT;
    call32( unix_vkCmdPipelineBarrier, pbar_g, "probe: vkCmdPipelineBarrier (image, to transfer-src)" );
    copy->imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT; copy->imageSubresource.layerCount = 1;
    copy->imageExtent.width = WIDTH; copy->imageExtent.height = HEIGHT; copy->imageExtent.depth = 1;
    pcopy->commandBuffer = cmdbuf32; pcopy->srcImage = image; pcopy->srcImageLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    pcopy->dstBuffer = buffer; pcopy->regionCount = 1; pcopy->pRegions = copy_g;
    call32( unix_vkCmdCopyImageToBuffer, pcopy_g, "probe: vkCmdCopyImageToBuffer" );
    bbar->sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
    bbar->srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; bbar->dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    bbar->srcQueueFamilyIndex = bbar->dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    bbar->buffer = buffer; bbar->size = VK_WHOLE_SIZE;
    pbar->dstStageMask = VK_PIPELINE_STAGE_HOST_BIT;
    pbar->imageMemoryBarrierCount = 0; pbar->pImageMemoryBarriers = 0;
    pbar->bufferMemoryBarrierCount = 1; pbar->pBufferMemoryBarriers = bbar_g;
    call32( unix_vkCmdPipelineBarrier, pbar_g, "probe: vkCmdPipelineBarrier (buffer, for the host)" );
    CHECK( mock->image_barriers == 2 && mock->buffer_barriers == 1 && mock->last_barrier_new_layout == VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
           "probe: the library saw %u image and %u buffer barriers with its own handles\n", mock->image_barriers, mock->buffer_barriers );
    pend->commandBuffer = cmdbuf32;
    call32( unix_vkEndCommandBuffer, pend_g, "probe: vkEndCommandBuffer" );

    finfo->sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    pfence->device = device32; pfence->pCreateInfo = finfo_g; pfence->pFence = fslot_g;
    call32( unix_vkCreateFence, pfence_g, "probe: vkCreateFence" );
    fence = *(uint64_t *)H( fslot_g );
    CHECK( pfence->result == VK_SUCCESS && fence, "probe: vkCreateFence %d\n", pfence->result );
    *(PTR32 *)H( list_g ) = cmdbuf32;
    submit->sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    submit->commandBufferCount = 1; submit->pCommandBuffers = list_g;
    psubmit->queue = queue32; psubmit->submitCount = 1; psubmit->pSubmits = submit_g; psubmit->fence = fence;
    call32( unix_vkQueueSubmit, psubmit_g, "probe: vkQueueSubmit with a fence" );
    CHECK( psubmit->result == VK_SUCCESS && mock->fence_signals == 1, "probe: vkQueueSubmit %d\n", psubmit->result );
    *(uint64_t *)H( flist_g ) = fence;
    pwait->device = device32; pwait->fenceCount = 1; pwait->pFences = flist_g; pwait->waitAll = VK_TRUE; pwait->timeout = 10000000000ull;
    call32( unix_vkWaitForFences, pwait_g, "probe: vkWaitForFences" );
    CHECK( pwait->result == VK_SUCCESS && mock->fence_waits == 1 && mock->fence_timeout == 10000000000ull,
           "probe: vkWaitForFences %d, timeout seen %llu\n", pwait->result, (unsigned long long)mock->fence_timeout );
    pinval->device = device32; pinval->memoryRangeCount = 1; pinval->pMemoryRanges = range_g;
    call32( unix_vkInvalidateMappedMemoryRanges, pinval_g, "probe: vkInvalidateMappedMemoryRanges" );
    CHECK( pinval->result == VK_SUCCESS && mock->invalidates == 1, "probe: vkInvalidateMappedMemoryRanges %d\n", pinval->result );

    bytes = H( bytes_g );
    for (i = 0; i < BYTES; i++) mismatches += bytes[i] != (i % 4 == 1 ? 0 : 255);
    CHECK( !mismatches, "probe: %u of %u bytes read back through the 32-bit mapping are not the clear colour ff00ffff (first %02x%02x%02x%02x)\n",
           mismatches, (unsigned)BYTES, bytes[0], bytes[1], bytes[2], bytes[3] );

    /* the probe's clean-up, in its order */
    pdfence->device = device32; pdfence->fence = fence;
    call32( unix_vkDestroyFence, pdfence_g, "probe: vkDestroyFence" );
    unmap32( buffer_memory );
    pdbuffer->device = device32; pdbuffer->buffer = buffer;
    call32( unix_vkDestroyBuffer, pdbuffer_g, "probe: vkDestroyBuffer" );
    pdimage->device = device32; pdimage->image = image;
    call32( unix_vkDestroyImage, pdimage_g, "probe: vkDestroyImage" );
    free32( buffer_memory );
    free32( image_memory );
    CHECK( mock->fence_destroys == 1 && mock->image_destroys == 1 && mock->live_memories == 0 && vm_window_live == live0,
           "probe: clean-up left %u memory object(s), %d window allocation(s)\n", mock->live_memories, vm_window_live - (int)live0 );
    group( "32-bit: the device probe's whole call list (image cleared on the GPU, copied to a mapped buffer, 1024 bytes read back) passes" );
}

static void name_object32( VkObjectType type, uint64_t handle, const char *name, uint64_t host_handle, const char *what )
{
    uint32_t info_g = galloc( sizeof(VkDebugUtilsObjectNameInfoEXT32) );
    VkDebugUtilsObjectNameInfoEXT32 *info = H( info_g );
    int calls = cb.calls;
    P32( vkSetDebugUtilsObjectNameEXT, params );

    info->sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT;
    info->objectType = type;
    info->objectHandle = handle;
    info->pObjectName = gstr( name );
    params->device = device32;
    params->pNameInfo = info_g;
    mock->named_count = 0;
    call32( unix_vkSetDebugUtilsObjectNameEXT, params_g, what );
    CHECK( params->result == VK_SUCCESS && mock->named_count == 1 && !strcmp( mock->named_name[0], name ), "%s: the name did not arrive\n", what );
    CHECK( mock->named_object[0] == host_handle, "%s: the library got object %#llx, its own handle is %#llx\n", what,
           (unsigned long long)mock->named_object[0], (unsigned long long)host_handle );
    CHECK( cb.calls == calls + 1 && cb.kind == 1, "%s: the debug messenger was not called\n", what );
    CHECK( cb.dispatch == proc32.utils_cb, "%s: dispatched to %#llx, the 32-bit winevulkan.dll's entry is %#llx\n", what,
           (unsigned long long)cb.dispatch, (unsigned long long)proc32.utils_cb );
    CHECK( cb.user_callback == UTILS_FN32 && cb.user_data == UTILS_DATA32, "%s: callback %#llx / user data %#llx are not the program's own numbers\n",
           what, (unsigned long long)cb.user_callback, (unsigned long long)cb.user_data );
    CHECK( cb.object_count == 1 && cb.object_type == type && cb.object_handle == handle,
           "%s: the callback's object handle is %#llx, the program's handle is %#llx\n", what, (unsigned long long)cb.object_handle, (unsigned long long)handle );
    CHECK( !strcmp( cb.object_name, name ) && !strcmp( cb.message, "object named" ) && cb.block_in_window, "%s: the callback's strings\n", what );
}

static void debug32(void)
{
    uint32_t info_g = galloc( sizeof(VkDebugReportCallbackCreateInfoEXT32) ), slot_g = galloc( 8 );
    VkDebugReportCallbackCreateInfoEXT32 *info = H( info_g );
    int calls;
    P32( vkCreateDebugReportCallbackEXT, create );
    P32( vkDebugReportMessageEXT, report );

    name_object32( VK_OBJECT_TYPE_DEVICE, device32, "the device", mock->last_device, "name a device" );
    name_object32( VK_OBJECT_TYPE_COMMAND_POOL, pool32, "the pool", mock->last_pool, "name a command pool" );
    name_object32( VK_OBJECT_TYPE_COMMAND_BUFFER, cmdbuf32, "the command buffer", mock->last_cmdbuf, "name a command buffer" );
    group( "32-bit: object handles passed as 64-bit numbers are unwrapped through the window, and come back in a debug callback as the program's own" );

    info->sType = VK_STRUCTURE_TYPE_DEBUG_REPORT_CALLBACK_CREATE_INFO_EXT;
    info->flags = VK_DEBUG_REPORT_ERROR_BIT_EXT;
    info->pfnCallback = REPORT_FN32;
    info->pUserData = REPORT_DATA32;
    create->instance = instance32;
    create->pCreateInfo = info_g;
    create->pCallback = slot_g;
    call32( unix_vkCreateDebugReportCallbackEXT, create_g, "vkCreateDebugReportCallbackEXT" );
    CHECK( create->result == VK_SUCCESS && *(uint64_t *)H( slot_g ), "vkCreateDebugReportCallbackEXT: %d\n", create->result );

    calls = cb.calls;
    report->instance = instance32;
    report->flags = VK_DEBUG_REPORT_ERROR_BIT_EXT;
    report->objectType = VK_DEBUG_REPORT_OBJECT_TYPE_DEVICE_EXT;
    report->object = device32;
    report->pLayerPrefix = gstr( "harness" );
    report->pMessage = gstr( "a report" );
    call32( unix_vkDebugReportMessageEXT, report_g, "vkDebugReportMessageEXT" );
    CHECK( cb.calls == calls + 1 && cb.kind == 2 && cb.dispatch == proc32.report_cb, "the report callback was not dispatched to the 32-bit entry\n" );
    CHECK( cb.user_callback == REPORT_FN32 && cb.user_data == REPORT_DATA32 && cb.object_handle == device32 && !strcmp( cb.message, "a report" ),
           "report callback: fn %#llx data %#llx object %#llx message '%s'\n", (unsigned long long)cb.user_callback,
           (unsigned long long)cb.user_data, (unsigned long long)cb.object_handle, cb.message );
    CHECK( mock->report_object == mock->last_device, "the library got report object %#llx\n", (unsigned long long)mock->report_object );
    group( "32-bit: debug report callbacks carry the program's function, user data and object handle" );
}

static void surface32(void)
{
    uint32_t info_g = galloc( sizeof(VkWin32SurfaceCreateInfoKHR32) ), slot_g = galloc( 8 );
    VkWin32SurfaceCreateInfoKHR32 *info = H( info_g );
    P32( vkCreateWin32SurfaceKHR, create );
    P32( vkDestroySurfaceKHR, destroy );

    info->sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR;
    info->hinstance = 0x00400000;
    info->hwnd = 0x00020066;          /* the Sims window of log VXCFEJ */
    create->instance = instance32;
    create->pCreateInfo = info_g;
    create->pSurface = slot_g;
    call32( unix_vkCreateWin32SurfaceKHR, create_g, "vkCreateWin32SurfaceKHR" );
    CHECK( create->result == VK_SUCCESS && *(uint64_t *)H( slot_g ), "vkCreateWin32SurfaceKHR: %d\n", create->result );
    CHECK( stub_layer_hwnd == 0x00020066, "the surface driver was asked for window %#lx: a window handle is a number, the window base must not be added\n",
           (unsigned long)stub_layer_hwnd );
    CHECK( mock->surface_creates == 1 && mock->surface_layer, "no Metal surface was created\n" );
    destroy->instance = instance32;
    destroy->surface = *(uint64_t *)H( slot_g );
    call32( unix_vkDestroySurfaceKHR, destroy_g, "vkDestroySurfaceKHR" );
    CHECK( mock->surface_destroys == 1 && stub_layer_releases == 1, "the surface was not destroyed\n" );
    group( "32-bit: a window handle stays the number it is (HWND, HINSTANCE), the surface goes to BCD's Metal surface driver" );
}

static void destroy32(void)
{
    uint32_t list_g = galloc( sizeof(PTR32) );
    P32( vkFreeCommandBuffers, freecb );
    P32( vkDestroyCommandPool, pool );
    P32( vkDestroyDevice, device );
    P32( vkDestroyInstance, instance );

    *(PTR32 *)H( list_g ) = cmdbuf32;
    freecb->device = device32;
    freecb->commandPool = pool32;
    freecb->commandBufferCount = 1;
    freecb->pCommandBuffers = list_g;
    call32( unix_vkFreeCommandBuffers, freecb_g, "vkFreeCommandBuffers" );
    pool->device = device32;
    pool->commandPool = pool32;
    call32( unix_vkDestroyCommandPool, pool_g, "vkDestroyCommandPool" );
    CHECK( mock->cmdbuf_frees == 1 && mock->pool_destroys == 1, "command buffer or pool not destroyed\n" );
    device->device = device32;
    call32( unix_vkDestroyDevice, device_g, "vkDestroyDevice" );
    instance->instance = instance32;
    call32( unix_vkDestroyInstance, instance_g, "vkDestroyInstance" );
    CHECK( mock->device_destroys >= 1 && mock->instance_destroys >= 1, "device or instance not destroyed\n" );
    CHECK( vm_window_live == 0, "%d window allocation(s) outlive the 32-bit program's Vulkan objects\n", vm_window_live );
    group( "32-bit: everything is destroyed through guest handles and no window memory is left" );
}

/* ============================================================================================== the 64-bit program's session */
#define UTILS_FN64   0x71a0b0c0d010ull
#define UTILS_DATA64 0x71a0b0c0d020ull

static void session64( int after_32bit_init )
{
    static const char *const names[] = { "VK_KHR_surface", "VK_EXT_debug_utils" };
    static const char *const device_names[] = { "VK_KHR_swapchain" };
    static const float priority = 1.0f;
    VkDebugUtilsMessengerCreateInfoEXT dbg = { .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT,
                                               .messageSeverity = 0xffff, .messageType = 0xffff,
                                               .pfnUserCallback = (void *)(uintptr_t)UTILS_FN64, .pUserData = (void *)(uintptr_t)UTILS_DATA64 };
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "a 64-bit program", .apiVersion = VK_API_VERSION_1_1 };
    VkInstanceCreateInfo info = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pNext = &dbg, .pApplicationInfo = &app,
                                  .enabledExtensionCount = 2, .ppEnabledExtensionNames = names };
    VkDeviceQueueCreateInfo queue_info = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueCount = 1, .pQueuePriorities = &priority };
    VkDeviceCreateInfo device_info = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
                                       .enabledExtensionCount = 1, .ppEnabledExtensionNames = device_names };
    VkMemoryAllocateInfo alloc = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = 20000, .memoryTypeIndex = 1 };
    VkDebugUtilsObjectNameInfoEXT name = { .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT, .objectType = VK_OBJECT_TYPE_DEVICE,
                                           .pObjectName = "a 64-bit device" };
    struct VkInstance_T *instance = calloc( 1, offsetof(struct VkInstance_T, physical_device[8]) );
    struct VkDevice_T *device = calloc( 1, offsetof(struct VkDevice_T, queues[1]) );
    struct vkCreateInstance_params create = { .pCreateInfo = &info };
    struct vkEnumeratePhysicalDevices_params enumerate = {0};
    struct vkCreateDevice_params create_device = { .pCreateInfo = &device_info };
    struct vkAllocateMemory_params allocate = { .pAllocateInfo = &alloc };
    struct vkMapMemory_params map = { .size = VK_WHOLE_SIZE };
    struct vkUnmapMemory_params unmap = {0};
    struct vkFreeMemory_params freemem = {0};
    struct vkSetDebugUtilsObjectNameEXT_params setname = { .pNameInfo = &name };
    struct vkDestroyDevice_params destroy_device = {0};
    struct vkDestroyInstance_params destroy_instance = {0};
    VkInstance instance_handle = instance;
    VkDevice device_handle = device;
    VkPhysicalDevice physdev = NULL;
    VkDeviceMemory memory = 0;
    uint32_t count = 1;
    void *data = NULL;
    int calls0 = vm_calls, window0 = vm_window_total, cbcalls, i;

    enter( &proc64 );
    instance->obj.loader_magic = VULKAN_ICD_MAGIC_VALUE;
    instance->physical_device_count = 8;
    for (i = 0; i < 8; i++) instance->physical_device[i].obj.loader_magic = VULKAN_ICD_MAGIC_VALUE;
    instance->extensions.has_VK_KHR_surface = instance->extensions.has_VK_EXT_debug_utils = 1;
    create.pInstance = &instance_handle;
    call64( unix_vkCreateInstance, &create, "vkCreateInstance (64-bit)" );
    CHECK( create.result == VK_SUCCESS && instance_handle == instance, "64-bit vkCreateInstance: %d\n", create.result );
    enumerate.instance = instance; enumerate.pPhysicalDeviceCount = &count; enumerate.pPhysicalDevices = &physdev;
    call64( unix_vkEnumeratePhysicalDevices, &enumerate, "vkEnumeratePhysicalDevices (64-bit)" );
    CHECK( enumerate.result == VK_SUCCESS && physdev == &instance->physical_device[0], "64-bit physical device handle\n" );

    device->obj.loader_magic = device->queues[0].obj.loader_magic = VULKAN_ICD_MAGIC_VALUE;
    device->extensions.has_VK_KHR_swapchain = 1;
    create_device.physicalDevice = physdev; create_device.pDevice = &device_handle;
    call64( unix_vkCreateDevice, &create_device, "vkCreateDevice (64-bit)" );
    CHECK( create_device.result == VK_SUCCESS, "64-bit vkCreateDevice: %d (a 64-bit program is never refused)\n", create_device.result );
    CHECK( !has_name( mock->device_exts, mock->device_ext_count, "VK_EXT_external_memory_host" ) &&
           !has_name( mock->device_exts, mock->device_ext_count, "VK_EXT_map_memory_placed" ),
           "a 64-bit program's device got a 32-bit program's memory extension\n" );

    allocate.device = device; allocate.pMemory = &memory;
    call64( unix_vkAllocateMemory, &allocate, "vkAllocateMemory (64-bit)" );
    CHECK( allocate.result == VK_SUCCESS && !mock->last_alloc_imported && mock->last_alloc_size == 20000,
           "a 64-bit program's memory was imported or resized (imported %u, size %llu)\n", mock->last_alloc_imported, (unsigned long long)mock->last_alloc_size );
    map.device = device; map.memory = memory; map.ppData = &data;
    call64( unix_vkMapMemory, &map, "vkMapMemory (64-bit)" );
    CHECK( map.result == VK_SUCCESS && data == (void *)(uintptr_t)mock->last_map_result && !in_window( data ),
           "a 64-bit program's mapping is %p, the library's is %#llx\n", data, (unsigned long long)mock->last_map_result );
    memset( data, 0x5a, 20000 );
    unmap.device = device; unmap.memory = memory;
    call64( unix_vkUnmapMemory, &unmap, "vkUnmapMemory (64-bit)" );
    freemem.device = device; freemem.memory = memory;
    call64( unix_vkFreeMemory, &freemem, "vkFreeMemory (64-bit)" );
    CHECK( vm_calls == calls0 && vm_window_total == window0, "a 64-bit program's Vulkan session called NtAllocateVirtualMemory %d time(s), %d in a window\n",
           vm_calls - calls0, vm_window_total - window0 );
    group( after_32bit_init ? "64-bit, in the same session as the 32-bit program: nothing imported, nothing allocated, mappings are the library's own pointers"
                            : "64-bit: nothing imported, nothing allocated, mappings are the library's own pointers" );

    cbcalls = cb.calls;
    name.objectHandle = (uint64_t)(uintptr_t)device;
    setname.device = device;
    mock->named_count = 0;
    call64( unix_vkSetDebugUtilsObjectNameEXT, &setname, "vkSetDebugUtilsObjectNameEXT (64-bit)" );
    CHECK( setname.result == VK_SUCCESS && mock->named_object[0] == mock->last_device, "64-bit: the device handle was not unwrapped\n" );
    CHECK( cb.calls == cbcalls + 1 && cb.dispatch == proc64.utils_cb, "64-bit: the callback was dispatched to %#llx, this process's entry is %#llx%s\n",
           (unsigned long long)cb.dispatch, (unsigned long long)proc64.utils_cb,
           cb.dispatch == proc32.utils_cb ? " (that is the 32-bit process's: one pair of globals for the whole session)" : "" );
    CHECK( cb.user_callback == UTILS_FN64 && cb.user_data == UTILS_DATA64 && cb.object_handle == (uint64_t)(uintptr_t)device && !cb.block_in_window,
           "64-bit: callback values changed (fn %#llx, data %#llx, object %#llx)\n", (unsigned long long)cb.user_callback,
           (unsigned long long)cb.user_data, (unsigned long long)cb.object_handle );
    group( "64-bit: debug callbacks go to this process's own entry point with its own 64-bit values" );

    /* the library reports from a thread of its own (a Metal completion queue): no TEB, no Windows process to call into */
    cbcalls = cb.calls;
    foreign_thread = 1;
    step = "a debug message on a thread without a TEB";
    mock_fire( "from a host thread" );
    foreign_thread = 0;
    CHECK( cb.calls == cbcalls, "a debug message on a host thread was dispatched as a user callback\n" );
    group( "a debug message from a thread that is not a Windows thread is dropped instead of dispatched" );

    destroy_device.device = device;
    call64( unix_vkDestroyDevice, &destroy_device, "vkDestroyDevice (64-bit)" );
    destroy_instance.instance = instance;
    call64( unix_vkDestroyInstance, &destroy_instance, "vkDestroyInstance (64-bit)" );
    free( device );
    free( instance );
}

static void init64(void)
{
    struct vulkan_instance_extensions extensions = {0};
    struct init_params params = { .call_vulkan_debug_report_callback = proc64.report_cb, .call_vulkan_debug_utils_callback = proc64.utils_cb,
                                  .extensions = &extensions };
    NTSTATUS status;

    enter( &proc64 );
    step = "init (64-bit)";
    status = __wine_unix_call_funcs[unix_init]( &params );
    CHECK( !status && extensions.has_VK_KHR_win32_surface, "64-bit init: %#x\n", (unsigned)status );
}

int main( int argc, char **argv )
{
    struct sigaction sa = { .sa_sigaction = on_fault, .sa_flags = SA_SIGINFO };
    const char *lib = getenv( "MADEIRA_VULKAN_LIBRARY" ), *mode = getenv( "MOCKVK_MODE" );
    uint8_t *reserve;
    void *handle;
    VkResult res;

    if (argc > 1 && !strcmp( argv[1], "--layout" ))    /* for the comparison with the real i386 DLL's debug info */
    {
        dump_layout();
        return 0;
    }
    verbose = argc > 1 && !strcmp( argv[1], "-v" );
    if (!mode) mode = "host";
    sigaction( SIGSEGV, &sa, NULL );
    sigaction( SIGBUS, &sa, NULL );

    reserve = mmap( NULL, 3 * WINDOW_SIZE, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0 );
    if (reserve == MAP_FAILED) { printf( "FAIL cannot reserve the guest window\n" ); return 3; }
    B = (uint8_t *)(((uintptr_t)reserve + WINDOW_SIZE - 1) & ~(uintptr_t)(WINDOW_SIZE - 1));
    if (mprotect( B + GUEST_HEAP_START, GUEST_HEAP_END - GUEST_HEAP_START, PROT_READ | PROT_WRITE ))
    { printf( "FAIL cannot commit the guest heap\n" ); return 3; }
    printf( "     guest window B = %p (guest address a is host address B + a), library model: %s\n", B, mode );

    if (!lib || !(handle = dlopen( lib, RTLD_NOW )) || !(mock = ((struct mockvk_state *(*)(void))dlsym( handle, "mockvk_state" ))()) ||
        !(mock_fire = dlsym( handle, "mockvk_fire" )))
    { printf( "FAIL MADEIRA_VULKAN_LIBRARY does not name the stand-in Vulkan library\n" ); return 3; }

    proc32.wow = 1;
    proc32.teb.Peb = &proc32.peb;
    proc32.teb.WowTebOffset = 0x2000;
    proc32.report_cb = 0x10012340;                 /* addresses inside the 32-bit winevulkan.dll (image base 0x10000000) */
    proc32.utils_cb = 0x10015670;
    proc64.teb.Peb = &proc64.peb;
    proc64.report_cb = 0x71f5a0001230ull;          /* and inside the 64-bit one */
    proc64.utils_cb = 0x71f5a0004560ull;

    /* a Dock session: the 64-bit side comes up first, then the 32-bit game */
    init64();
    init32();
    group( "both processes initialise the one unix side, the 64-bit one first" );

    create_instance32();
    properties32();
    res = create_device32();
    if (!strcmp( mode, "none" ))
    {
        CHECK( res == VK_ERROR_FEATURE_NOT_PRESENT, "vkCreateDevice for a 32-bit program on a library that can place no mapping: %d\n", res );
        CHECK( strstr( logbuf, "[winios-vk] Vulkan for a 32-bit program is refused on \"" MOCKVK_DEVICE_NAME "\"" ) &&
               strstr( logbuf, "neither VK_EXT_map_memory_placed nor VK_EXT_external_memory_host" ), "the refusal's log line is missing: '%s'\n", logbuf );
        CHECK( mock->device_creates == 0, "the host device was created anyway\n" );
        group( "32-bit, a library with no way to place a mapping: vkCreateDevice is refused with VK_ERROR_FEATURE_NOT_PRESENT and a log line" );
        device32 = 0;
        {
            P32( vkDestroyInstance, instance );
            instance->instance = instance32;
            call32( unix_vkDestroyInstance, instance_g, "vkDestroyInstance" );
        }
    }
    else
    {
        CHECK( res == VK_SUCCESS, "vkCreateDevice: %d\n", res );
        if (!strcmp( mode, "placed" ))
            CHECK( !has_name( mock->device_exts, mock->device_ext_count, "VK_EXT_external_memory_host" ), "placed maps and host imports were both enabled\n" );
        else
            CHECK( has_name( mock->device_exts, mock->device_ext_count, "VK_EXT_external_memory_host" ) &&
                   has_name( mock->device_exts, mock->device_ext_count, "VK_KHR_external_memory" ),
                   "VK_EXT_external_memory_host and VK_KHR_external_memory were not enabled on the host device for a 32-bit program\n" );
        group( "32-bit: vkCreateDevice converts its chain and arrays, enables the host's memory-placing extension, and returns guest handles" );
        commands32();
        if (!strcmp( mode, "placed" )) memory32_placed(); else memory32_host();
        probe32();
        debug32();
        surface32();
        destroy32();
    }

    session64( 1 );
    enter( &proc32 );
    CHECK( vm_window_live == 0, "window allocations at the end: %d\n", vm_window_live );

    printf( "%s %d checks, %d failed (library model: %s)\n", failures ? "FAIL" : "ok  ", checks, failures, mode );
    return failures ? 1 : 0;
}
