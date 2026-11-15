/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */
/*
 * A stand-in Vulkan library for tests/host/check-vulkan-wow64.py (no GPU, no Metal).
 *
 * win32u's Vulkan layer loads it through MADEIRA_VULKAN_LIBRARY exactly as it loads MoltenVK on a device. It implements the calls
 * the harness drives and nothing else, and it is strict where the real thing is strict:
 *   - the extension set is MoltenVK 1.4.2's as far as memory mapping goes (MOCKVK_MODE unset or "host"): VK_KHR_external_memory,
 *     VK_EXT_external_memory_host and VK_KHR_map_memory2, no VK_EXT_map_memory_placed; memory types 0 private, 1 shared
 *     (host-visible, coherent), 2 lazily allocated; minImportedHostPointerAlignment 16384, the iOS page size;
 *     MOCKVK_MODE=placed adds VK_EXT_map_memory_placed; MOCKVK_MODE=none has neither way to place a mapping;
 *   - an imported host pointer must be 16 KB aligned, its size a multiple of 16 KB, the memory type host-visible, and every page of
 *     the range readable and writable (it is read and written here), as Metal's newBufferWithBytesNoCopy needs;
 *   - a host-visible allocation that is not imported lives in this library's own heap (far above 4 GB), like a Metal buffer;
 *   - buffers are bound to memory by offset, and vkQueueSubmit really copies the bytes vkCmdCopyBuffer asked for, so data written
 *     through one mapping must come out of the other;
 *   - every pointer it is given is read: a guest address that was not converted faults here, under the harness's fault handler.
 * What it saw is published through mockvk_state().
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "windef.h"
#include "winbase.h"
#include "wine/vulkan.h"
#include "mock_icd.h"

#define MOCK_PAGE 16384u

static struct mockvk_state st;

__attribute__((visibility("default"))) struct mockvk_state *mockvk_state(void) { return &st; }

struct mock_instance { uint64_t magic; VkDebugUtilsMessengerCreateInfoEXT utils[4]; int utils_count;
                       VkDebugReportCallbackCreateInfoEXT report[4]; int report_count; };
struct mock_physdev { uint64_t magic; };
struct mock_queue { uint64_t magic; struct mock_device *device; };
struct mock_device { uint64_t magic; struct mock_instance *instance; struct mock_queue queue; };
struct mock_memory { uint64_t size; uint32_t type; uint8_t *base; uint8_t *view; int imported; int mapped; };
struct mock_buffer { uint64_t magic; uint64_t size; struct mock_memory *memory; uint64_t offset; };
struct mock_image { uint64_t magic; uint32_t width, height; uint8_t *pixels; struct mock_memory *memory; };   /* RGBA8, its own storage, like a Metal texture */
struct mock_fence { uint64_t magic; int signaled; };
enum mock_op_kind { OP_COPY_BUFFER, OP_CLEAR_IMAGE, OP_IMAGE_TO_BUFFER };
struct mock_op { enum mock_op_kind kind; struct mock_buffer *src, *dst; struct mock_image *image; VkBufferCopy region;
                 uint8_t color[4]; VkBufferImageCopy image_region; };
struct mock_cmdbuf { uint64_t magic; struct mock_op ops[16]; int op_count; };
struct mock_pool { int dummy; };
#define BUFFER_MAGIC 0xb0ffe7ull
#define IMAGE_MAGIC  0x1a6eull
#define FENCE_MAGIC  0xfe7ceull

static struct mock_physdev the_physdev = { 0x01CDC0DE };
static struct mock_instance *the_instance;   /* the newest one: debug callbacks go through it */

static int mode(void)
{
    const char *e = getenv( "MOCKVK_MODE" );
    if (e && !strcmp( e, "placed" )) return MOCKVK_PLACED;
    if (e && !strcmp( e, "none" )) return MOCKVK_NONE;
    return MOCKVK_HOST;
}

static void copy_name( char *dst, const char *src )
{
    if (!src) { dst[0] = 0; return; }
    snprintf( dst, MOCKVK_NAME, "%s", src );  /* reads the caller's pointer */
}

static uint8_t *storage( struct mock_memory *m ) { return m->view ? m->view : m->base; }

/* ---------------------------------------------------------------------------------------------------------------- instance */
static const char *const instance_exts[] = { "VK_KHR_surface", "VK_EXT_metal_surface", "VK_KHR_get_physical_device_properties2",
                                             "VK_KHR_external_memory_capabilities", "VK_EXT_debug_utils", "VK_EXT_debug_report" };

static VkResult fill_exts( const char *const *names, uint32_t have, uint32_t *count, VkExtensionProperties *props )
{
    uint32_t i, n;
    if (!props) { *count = have; return VK_SUCCESS; }
    n = *count < have ? *count : have;
    for (i = 0; i < n; i++)
    {
        memset( &props[i], 0, sizeof(props[i]) );
        strcpy( props[i].extensionName, names[i] );
        props[i].specVersion = 1;
    }
    *count = n;
    return n < have ? VK_INCOMPLETE : VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkEnumerateInstanceExtensionProperties( const char *layer, uint32_t *count, VkExtensionProperties *props )
{
    return fill_exts( instance_exts, sizeof(instance_exts) / sizeof(instance_exts[0]), count, props );
}

static VkResult VKAPI_CALL mock_vkEnumerateInstanceVersion( uint32_t *version )
{
    *version = VK_MAKE_API_VERSION( 0, 1, 4, 0 );
    return VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkCreateInstance( const VkInstanceCreateInfo *info, const VkAllocationCallbacks *alloc, VkInstance *ret )
{
    struct mock_instance *instance = calloc( 1, sizeof(*instance) );
    const VkBaseInStructure *next;
    uint32_t i;

    instance->magic = 0x01CDC0DE;
    st.instance_creates++;
    copy_name( st.app_name, info->pApplicationInfo ? info->pApplicationInfo->pApplicationName : NULL );
    copy_name( st.engine_name, info->pApplicationInfo ? info->pApplicationInfo->pEngineName : NULL );
    st.instance_ext_count = info->enabledExtensionCount < MOCKVK_EXTS ? info->enabledExtensionCount : MOCKVK_EXTS;
    for (i = 0; i < st.instance_ext_count; i++) copy_name( st.instance_exts[i], info->ppEnabledExtensionNames[i] );
    for (next = info->pNext; next; next = next->pNext)
    {
        if (next->sType == VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT && instance->utils_count < 4)
            instance->utils[instance->utils_count++] = *(const VkDebugUtilsMessengerCreateInfoEXT *)next;
        if (next->sType == VK_STRUCTURE_TYPE_DEBUG_REPORT_CALLBACK_CREATE_INFO_EXT && instance->report_count < 4)
            instance->report[instance->report_count++] = *(const VkDebugReportCallbackCreateInfoEXT *)next;
    }
    the_instance = instance;
    *ret = (VkInstance)instance;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyInstance( VkInstance instance, const VkAllocationCallbacks *alloc )
{
    st.instance_destroys++;
    if (the_instance == (void *)instance) the_instance = NULL;
    free( instance );
}

static VkResult VKAPI_CALL mock_vkEnumeratePhysicalDevices( VkInstance instance, uint32_t *count, VkPhysicalDevice *devices )
{
    if (!devices) { *count = 1; return VK_SUCCESS; }
    if (!*count) return VK_INCOMPLETE;
    devices[0] = (VkPhysicalDevice)&the_physdev;
    *count = 1;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkGetPhysicalDeviceProperties( VkPhysicalDevice dev, VkPhysicalDeviceProperties *props )
{
    memset( props, 0, sizeof(*props) );
    props->apiVersion = VK_MAKE_API_VERSION( 0, 1, 4, 0 );
    props->vendorID = 0x106b;
    props->deviceType = VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU;
    strcpy( props->deviceName, MOCKVK_DEVICE_NAME );
}

static void VKAPI_CALL mock_vkGetPhysicalDeviceProperties2( VkPhysicalDevice dev, VkPhysicalDeviceProperties2 *props )
{
    VkBaseOutStructure *next;
    mock_vkGetPhysicalDeviceProperties( dev, &props->properties );
    for (next = props->pNext; next; next = next->pNext)
    {
        if (next->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT)
            ((VkPhysicalDeviceExternalMemoryHostPropertiesEXT *)next)->minImportedHostPointerAlignment = MOCK_PAGE;
        if (next->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_PROPERTIES_EXT)
            ((VkPhysicalDeviceMapMemoryPlacedPropertiesEXT *)next)->minPlacedMemoryMapAlignment = MOCK_PAGE;
    }
}

static void VKAPI_CALL mock_vkGetPhysicalDeviceFeatures( VkPhysicalDevice dev, VkPhysicalDeviceFeatures *features )
{
    memset( features, 0, sizeof(*features) );
    features->samplerAnisotropy = VK_TRUE;
}

static void VKAPI_CALL mock_vkGetPhysicalDeviceFeatures2( VkPhysicalDevice dev, VkPhysicalDeviceFeatures2 *features )
{
    VkBaseOutStructure *next;
    mock_vkGetPhysicalDeviceFeatures( dev, &features->features );
    for (next = features->pNext; next; next = next->pNext)
    {
        if (next->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_FEATURES_EXT)
        {
            VkPhysicalDeviceMapMemoryPlacedFeaturesEXT *placed = (void *)next;
            placed->memoryMapPlaced = placed->memoryUnmapReserve = mode() == MOCKVK_PLACED;
            placed->memoryMapRangePlaced = VK_FALSE;
        }
    }
}

static void VKAPI_CALL mock_vkGetPhysicalDeviceMemoryProperties( VkPhysicalDevice dev, VkPhysicalDeviceMemoryProperties *props )
{
    memset( props, 0, sizeof(*props) );
    props->memoryHeapCount = 1;
    props->memoryHeaps[0].size = (VkDeviceSize)4 << 30;
    props->memoryHeaps[0].flags = VK_MEMORY_HEAP_DEVICE_LOCAL_BIT;
    props->memoryTypeCount = 3;     /* MoltenVK on an Apple GPU: private, shared, memoryless */
    props->memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
    props->memoryTypes[1].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                          VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
    props->memoryTypes[2].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_LAZILY_ALLOCATED_BIT;
}

static void VKAPI_CALL mock_vkGetPhysicalDeviceQueueFamilyProperties( VkPhysicalDevice dev, uint32_t *count, VkQueueFamilyProperties *props )
{
    if (!props) { *count = 1; return; }
    if (!*count) return;
    memset( props, 0, sizeof(*props) );
    props->queueFlags = VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT;
    props->queueCount = 1;
    *count = 1;
}

static VkResult VKAPI_CALL mock_vkEnumerateDeviceExtensionProperties( VkPhysicalDevice dev, const char *layer, uint32_t *count,
                                                                      VkExtensionProperties *props )
{
    static const char *const host[] = { "VK_KHR_swapchain", "VK_KHR_external_memory", "VK_EXT_external_memory_host", "VK_KHR_map_memory2" };
    static const char *const placed[] = { "VK_KHR_swapchain", "VK_KHR_external_memory", "VK_EXT_external_memory_host", "VK_KHR_map_memory2",
                                          "VK_EXT_map_memory_placed" };
    static const char *const none[] = { "VK_KHR_swapchain", "VK_KHR_map_memory2" };
    switch (mode())
    {
    case MOCKVK_PLACED: return fill_exts( placed, 5, count, props );
    case MOCKVK_NONE: return fill_exts( none, 2, count, props );
    default: return fill_exts( host, 4, count, props );
    }
}

/* ------------------------------------------------------------------------------------------------------------------ surface */
static VkResult VKAPI_CALL mock_vkCreateMetalSurfaceEXT( VkInstance instance, const VkMetalSurfaceCreateInfoEXT *info,
                                                         const VkAllocationCallbacks *alloc, VkSurfaceKHR *surface )
{
    st.surface_layer = (uintptr_t)info->pLayer;
    st.surface_creates++;
    *surface = (VkSurfaceKHR)(uintptr_t)malloc( 16 );
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroySurfaceKHR( VkInstance instance, VkSurfaceKHR surface, const VkAllocationCallbacks *alloc )
{
    st.surface_destroys++;
    free( (void *)(uintptr_t)surface );
}

static VkResult VKAPI_CALL mock_vkGetPhysicalDeviceSurfaceSupportKHR( VkPhysicalDevice dev, uint32_t family, VkSurfaceKHR surface, VkBool32 *supported )
{
    *supported = VK_TRUE;
    return VK_SUCCESS;
}

/* -------------------------------------------------------------------------------------------------------------------- debug */
static VkResult VKAPI_CALL mock_vkCreateDebugUtilsMessengerEXT( VkInstance handle, const VkDebugUtilsMessengerCreateInfoEXT *info,
                                                                const VkAllocationCallbacks *alloc, VkDebugUtilsMessengerEXT *ret )
{
    struct mock_instance *instance = (void *)handle;
    if (instance->utils_count >= 4) return VK_ERROR_OUT_OF_HOST_MEMORY;
    instance->utils[instance->utils_count++] = *info;
    *ret = (VkDebugUtilsMessengerEXT)(uintptr_t)(0x7700000000ull + instance->utils_count);
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyDebugUtilsMessengerEXT( VkInstance handle, VkDebugUtilsMessengerEXT messenger, const VkAllocationCallbacks *alloc )
{
}

static VkResult VKAPI_CALL mock_vkCreateDebugReportCallbackEXT( VkInstance handle, const VkDebugReportCallbackCreateInfoEXT *info,
                                                                const VkAllocationCallbacks *alloc, VkDebugReportCallbackEXT *ret )
{
    struct mock_instance *instance = (void *)handle;
    if (instance->report_count >= 4) return VK_ERROR_OUT_OF_HOST_MEMORY;
    instance->report[instance->report_count++] = *info;
    *ret = (VkDebugReportCallbackEXT)(uintptr_t)(0x7800000000ull + instance->report_count);
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyDebugReportCallbackEXT( VkInstance handle, VkDebugReportCallbackEXT callback, const VkAllocationCallbacks *alloc )
{
}

/* the application's own message, handed to every report callback, as the real thing does */
static void VKAPI_CALL mock_vkDebugReportMessageEXT( VkInstance handle, VkDebugReportFlagsEXT flags, VkDebugReportObjectTypeEXT type,
                                                     uint64_t object, size_t location, int32_t code, const char *prefix, const char *message )
{
    struct mock_instance *instance = (void *)handle;
    int i;

    st.report_object = object;
    copy_name( st.report_message, message );
    for (i = 0; i < instance->report_count; i++)
        instance->report[i].pfnCallback( flags, type, object, location, code, prefix, message, instance->report[i].pUserData );
}

static void fire_utils( struct mock_instance *instance, VkObjectType type, uint64_t object, const char *name, const char *message )
{
    VkDebugUtilsObjectNameInfoEXT obj = { .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT, .objectType = type,
                                          .objectHandle = object, .pObjectName = name };
    VkDebugUtilsMessengerCallbackDataEXT data = { .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CALLBACK_DATA_EXT,
                                                  .pMessageIdName = "mockvk", .messageIdNumber = 660, .pMessage = message,
                                                  .objectCount = object ? 1 : 0, .pObjects = object ? &obj : NULL };
    int i;
    if (!instance) return;
    for (i = 0; i < instance->utils_count; i++)
        st.utils_returns += instance->utils[i].pfnUserCallback( VK_DEBUG_UTILS_MESSAGE_SEVERITY_INFO_BIT_EXT,
                                                                VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT, &data,
                                                                instance->utils[i].pUserData ) ? 1 : 0;
}

/* the harness's way to make the library report on its own (a thread of the host's, a message with no object) */
__attribute__((visibility("default"))) void mockvk_fire( const char *message )
{
    fire_utils( the_instance, VK_OBJECT_TYPE_UNKNOWN, 0, NULL, message );
}

/* ------------------------------------------------------------------------------------------------------------------- device */
static VkResult VKAPI_CALL mock_vkCreateDevice( VkPhysicalDevice phys, const VkDeviceCreateInfo *info, const VkAllocationCallbacks *alloc, VkDevice *ret )
{
    struct mock_device *device = calloc( 1, sizeof(*device) );
    const VkBaseInStructure *next;
    uint32_t i;

    device->magic = device->queue.magic = 0x01CDC0DE;
    device->queue.device = device;
    device->instance = the_instance;
    st.device_creates++;
    st.device_ext_count = info->enabledExtensionCount < MOCKVK_EXTS ? info->enabledExtensionCount : MOCKVK_EXTS;
    for (i = 0; i < st.device_ext_count; i++) copy_name( st.device_exts[i], info->ppEnabledExtensionNames[i] );
    st.device_queue_count = info->queueCreateInfoCount ? info->pQueueCreateInfos[0].queueCount : 0;
    st.device_queue_priority = info->queueCreateInfoCount ? info->pQueueCreateInfos[0].pQueuePriorities[0] : 0;
    st.device_anisotropy = st.device_placed_feature = 0;
    for (next = info->pNext; next; next = next->pNext)
    {
        if (next->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2)
            st.device_anisotropy = ((const VkPhysicalDeviceFeatures2 *)next)->features.samplerAnisotropy;
        if (next->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_FEATURES_EXT)
            st.device_placed_feature = ((const VkPhysicalDeviceMapMemoryPlacedFeaturesEXT *)next)->memoryMapPlaced;
    }
    st.last_device = (uintptr_t)device;
    *ret = (VkDevice)device;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyDevice( VkDevice device, const VkAllocationCallbacks *alloc )
{
    st.device_destroys++;
    free( device );
}

static void VKAPI_CALL mock_vkGetDeviceQueue( VkDevice handle, uint32_t family, uint32_t index, VkQueue *queue )
{
    struct mock_device *device = (void *)handle;
    *queue = (VkQueue)&device->queue;
}

static void VKAPI_CALL mock_vkGetDeviceQueue2( VkDevice handle, const VkDeviceQueueInfo2 *info, VkQueue *queue )
{
    mock_vkGetDeviceQueue( handle, info->queueFamilyIndex, info->queueIndex, queue );
}

static VkResult VKAPI_CALL mock_vkDeviceWaitIdle( VkDevice device ) { return VK_SUCCESS; }
static VkResult VKAPI_CALL mock_vkQueueWaitIdle( VkQueue queue ) { st.wait_idles++; return VK_SUCCESS; }

/* ------------------------------------------------------------------------------------------------------------------- memory */
static VkResult VKAPI_CALL mock_vkGetMemoryHostPointerPropertiesEXT( VkDevice device, VkExternalMemoryHandleTypeFlagBits type,
                                                                     const void *pointer, VkMemoryHostPointerPropertiesEXT *props )
{
    st.host_pointer_queries++;
    props->memoryTypeBits = 1u << 1;    /* the host-visible types, as MVKDevice::getMemoryHostPointerProperties answers */
    return VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkAllocateMemory( VkDevice device, const VkMemoryAllocateInfo *info, const VkAllocationCallbacks *alloc,
                                                  VkDeviceMemory *ret )
{
    const VkImportMemoryHostPointerInfoEXT *import = NULL;
    const VkBaseInStructure *next;
    struct mock_memory *memory;

    st.alloc_calls++;
    st.last_alloc_type = info->memoryTypeIndex;
    st.last_alloc_size = info->allocationSize;
    st.last_alloc_flags_seen = 0;
    for (next = info->pNext; next; next = next->pNext)
    {
        if (next->sType == VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT) import = (const void *)next;
        if (next->sType == VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO)
            st.last_alloc_flags_seen = 0x80000000u | ((const VkMemoryAllocateFlagsInfo *)next)->flags;
    }
    st.last_alloc_imported = import != NULL;
    st.last_import_pointer = import ? (uintptr_t)import->pHostPointer : 0;

    if (st.fail_next_alloc)
    {
        st.fail_next_alloc = 0;
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    if (import)
    {
        uint8_t *p = import->pHostPointer;
        uint64_t off;

        if (info->memoryTypeIndex != 1) return VK_ERROR_INVALID_EXTERNAL_HANDLE;      /* "Imported memory must be host-visible" */
        if (((uintptr_t)p & (MOCK_PAGE - 1)) || (info->allocationSize & (MOCK_PAGE - 1)) || !info->allocationSize)
        {
            st.import_misaligned++;                 /* newBufferWithBytesNoCopy returns nil */
            return VK_ERROR_OUT_OF_DEVICE_MEMORY;
        }
        for (off = 0; off < info->allocationSize; off += 4096)   /* the whole range is the caller's, committed and writable */
        {
            volatile uint8_t *b = p + off;
            *b = *b;
        }
    }
    memory = calloc( 1, sizeof(*memory) );
    memory->size = info->allocationSize;
    memory->type = info->memoryTypeIndex;
    memory->imported = import != NULL;
    if (import) memory->base = import->pHostPointer;
    else if (posix_memalign( (void **)&memory->base, MOCK_PAGE, (info->allocationSize + MOCK_PAGE - 1) & ~(uint64_t)(MOCK_PAGE - 1) ))
    {
        free( memory );
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    if (!import) memset( memory->base, 0, info->allocationSize );
    st.live_memories++;
    st.imported_allocs += memory->imported;
    *ret = (VkDeviceMemory)(uintptr_t)memory;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkFreeMemory( VkDevice device, VkDeviceMemory handle, const VkAllocationCallbacks *alloc )
{
    struct mock_memory *memory = (void *)(uintptr_t)handle;
    if (!memory) return;
    st.free_calls++;
    st.live_memories--;
    if (!memory->imported) free( memory->base );
    free( memory );
}

static VkResult map_memory( struct mock_memory *memory, VkDeviceSize offset, VkMemoryMapFlags flags, const void *chain, void **data )
{
    const VkBaseInStructure *next;

    st.map_calls++;
    if (memory->type != 1) return VK_ERROR_MEMORY_MAP_FAILED;       /* "Private GPU-only memory cannot be mapped to host memory." */
    if (memory->mapped) return VK_ERROR_MEMORY_MAP_FAILED;          /* "Memory is already mapped." */
    if (flags & VK_MEMORY_MAP_PLACED_BIT_EXT)
    {
        void *placed = NULL;
        for (next = chain; next; next = next->pNext)
            if (next->sType == VK_STRUCTURE_TYPE_MEMORY_MAP_PLACED_INFO_EXT) placed = ((const VkMemoryMapPlacedInfoEXT *)next)->pPlacedAddress;
        if (mode() != MOCKVK_PLACED || !placed || ((uintptr_t)placed & (MOCK_PAGE - 1))) return VK_ERROR_MEMORY_MAP_FAILED;
        memcpy( placed, memory->base, memory->size );      /* the memory's pages now appear at the placed address */
        memory->view = placed;
        st.placed_maps++;
    }
    memory->mapped = 1;
    *data = storage( memory ) + offset;
    st.last_map_result = (uintptr_t)*data;
    return VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkMapMemory( VkDevice device, VkDeviceMemory handle, VkDeviceSize offset, VkDeviceSize size,
                                             VkMemoryMapFlags flags, void **data )
{
    return map_memory( (void *)(uintptr_t)handle, offset, flags, NULL, data );
}

static VkResult VKAPI_CALL mock_vkMapMemory2KHR( VkDevice device, const VkMemoryMapInfoKHR *info, void **data )
{
    st.map2_calls++;
    return map_memory( (void *)(uintptr_t)info->memory, info->offset, info->flags, info->pNext, data );
}

static void unmap_memory( struct mock_memory *memory, VkMemoryUnmapFlagsKHR flags )
{
    st.unmap_calls++;
    if (memory->view)
    {
        memcpy( memory->base, memory->view, memory->size );     /* the pages leave the placed address; the range stays reserved */
        memory->view = NULL;
        if (flags & VK_MEMORY_UNMAP_RESERVE_BIT_EXT) st.reserve_unmaps++;
    }
    memory->mapped = 0;
}

static void VKAPI_CALL mock_vkUnmapMemory( VkDevice device, VkDeviceMemory handle )
{
    unmap_memory( (void *)(uintptr_t)handle, 0 );
}

static VkResult VKAPI_CALL mock_vkUnmapMemory2KHR( VkDevice device, const VkMemoryUnmapInfoKHR *info )
{
    unmap_memory( (void *)(uintptr_t)info->memory, info->flags );
    return VK_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------------------------ buffers */
static VkResult VKAPI_CALL mock_vkCreateBuffer( VkDevice device, const VkBufferCreateInfo *info, const VkAllocationCallbacks *alloc, VkBuffer *ret )
{
    struct mock_buffer *buffer = calloc( 1, sizeof(*buffer) );
    const VkBaseInStructure *next;

    buffer->magic = BUFFER_MAGIC;
    buffer->size = info->size;
    st.last_buffer_size = info->size;
    st.last_buffer_external = 0;
    for (next = info->pNext; next; next = next->pNext)
        if (next->sType == VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO)
            st.last_buffer_external = ((const VkExternalMemoryBufferCreateInfo *)next)->handleTypes;
    *ret = (VkBuffer)(uintptr_t)buffer;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyBuffer( VkDevice device, VkBuffer buffer, const VkAllocationCallbacks *alloc )
{
    free( (void *)(uintptr_t)buffer );
}

static void VKAPI_CALL mock_vkGetBufferMemoryRequirements( VkDevice device, VkBuffer handle, VkMemoryRequirements *reqs )
{
    struct mock_buffer *buffer = (void *)(uintptr_t)handle;
    reqs->size = buffer->size;
    reqs->alignment = 16;
    reqs->memoryTypeBits = 0x3;
}

static VkResult VKAPI_CALL mock_vkBindBufferMemory( VkDevice device, VkBuffer handle, VkDeviceMemory memory, VkDeviceSize offset )
{
    struct mock_buffer *buffer = (void *)(uintptr_t)handle;
    buffer->memory = (void *)(uintptr_t)memory;
    buffer->offset = offset;
    if (offset + buffer->size > buffer->memory->size) return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    return VK_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------------------------- images */
static void VKAPI_CALL mock_vkGetPhysicalDeviceFormatProperties( VkPhysicalDevice dev, VkFormat format, VkFormatProperties *props )
{
    memset( props, 0, sizeof(*props) );
    if (format == VK_FORMAT_R8G8B8A8_UNORM)
        props->optimalTilingFeatures = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
}

static VkResult VKAPI_CALL mock_vkCreateImage( VkDevice device, const VkImageCreateInfo *info, const VkAllocationCallbacks *alloc, VkImage *ret )
{
    struct mock_image *image = calloc( 1, sizeof(*image) );
    const VkBaseInStructure *next;

    image->magic = IMAGE_MAGIC;
    image->width = info->extent.width;
    image->height = info->extent.height;
    image->pixels = calloc( (size_t)image->width * image->height, 4 );
    st.last_image_format = info->format;
    st.last_image_usage = info->usage;
    st.last_image_external = 0;
    for (next = info->pNext; next; next = next->pNext)
        if (next->sType == VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO)
            st.last_image_external = ((const VkExternalMemoryImageCreateInfo *)next)->handleTypes;
    st.image_creates++;
    *ret = (VkImage)(uintptr_t)image;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyImage( VkDevice device, VkImage handle, const VkAllocationCallbacks *alloc )
{
    struct mock_image *image = (void *)(uintptr_t)handle;
    if (!image) return;
    st.image_destroys++;
    free( image->pixels );
    free( image );
}

static void VKAPI_CALL mock_vkGetImageMemoryRequirements( VkDevice device, VkImage handle, VkMemoryRequirements *reqs )
{
    struct mock_image *image = (void *)(uintptr_t)handle;
    reqs->size = (VkDeviceSize)image->width * image->height * 4;
    reqs->alignment = 16;
    reqs->memoryTypeBits = 0x3;
}

static VkResult VKAPI_CALL mock_vkBindImageMemory( VkDevice device, VkImage handle, VkDeviceMemory memory, VkDeviceSize offset )
{
    struct mock_image *image = (void *)(uintptr_t)handle;
    image->memory = (void *)(uintptr_t)memory;
    st.image_memory_type = image->memory->type;
    return VK_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------------------------- fences */
static VkResult VKAPI_CALL mock_vkCreateFence( VkDevice device, const VkFenceCreateInfo *info, const VkAllocationCallbacks *alloc, VkFence *ret )
{
    struct mock_fence *fence = calloc( 1, sizeof(*fence) );
    fence->magic = FENCE_MAGIC;
    fence->signaled = !!(info->flags & VK_FENCE_CREATE_SIGNALED_BIT);
    *ret = (VkFence)(uintptr_t)fence;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyFence( VkDevice device, VkFence fence, const VkAllocationCallbacks *alloc )
{
    st.fence_destroys++;
    free( (void *)(uintptr_t)fence );
}

static VkResult VKAPI_CALL mock_vkWaitForFences( VkDevice device, uint32_t count, const VkFence *fences, VkBool32 wait_all, uint64_t timeout )
{
    uint32_t i;
    st.fence_waits++;
    st.fence_timeout = timeout;
    for (i = 0; i < count; i++)
    {
        struct mock_fence *fence = (void *)(uintptr_t)fences[i];
        if (fence->magic != FENCE_MAGIC) return VK_ERROR_DEVICE_LOST;       /* not this library's fence: the handle was not unwrapped */
        if (!fence->signaled) return VK_TIMEOUT;
    }
    return VK_SUCCESS;
}

/* ----------------------------------------------------------------------------------------------------------- mapped ranges */
static VkResult ranges( uint32_t count, const VkMappedMemoryRange *list, uint32_t *counter )
{
    uint32_t i;
    for (i = 0; i < count; i++)
    {
        struct mock_memory *memory = (void *)(uintptr_t)list[i].memory;
        if (memory->type != 1 || !memory->mapped) return VK_ERROR_MEMORY_MAP_FAILED;
        st.range_size = list[i].size;
        (*counter)++;
    }
    return VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkFlushMappedMemoryRanges( VkDevice device, uint32_t count, const VkMappedMemoryRange *list )
{
    return ranges( count, list, &st.flushes );
}

static VkResult VKAPI_CALL mock_vkInvalidateMappedMemoryRanges( VkDevice device, uint32_t count, const VkMappedMemoryRange *list )
{
    return ranges( count, list, &st.invalidates );
}

/* ----------------------------------------------------------------------------------------------------------------- commands */
static VkResult VKAPI_CALL mock_vkCreateCommandPool( VkDevice device, const VkCommandPoolCreateInfo *info, const VkAllocationCallbacks *alloc,
                                                     VkCommandPool *ret )
{
    st.pool_flags = info->flags;
    st.last_pool = (uintptr_t)calloc( 1, sizeof(struct mock_pool) );
    *ret = (VkCommandPool)st.last_pool;
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkDestroyCommandPool( VkDevice device, VkCommandPool pool, const VkAllocationCallbacks *alloc )
{
    st.pool_destroys++;
    free( (void *)(uintptr_t)pool );
}

static VkResult VKAPI_CALL mock_vkAllocateCommandBuffers( VkDevice device, const VkCommandBufferAllocateInfo *info, VkCommandBuffer *buffers )
{
    uint32_t i;
    st.cmdbuf_pool_seen = (uintptr_t)info->commandPool;
    for (i = 0; i < info->commandBufferCount; i++)
    {
        struct mock_cmdbuf *cb = calloc( 1, sizeof(*cb) );
        cb->magic = 0x01CDC0DE;
        buffers[i] = (VkCommandBuffer)cb;
        st.last_cmdbuf = (uintptr_t)cb;
    }
    return VK_SUCCESS;
}

static void VKAPI_CALL mock_vkFreeCommandBuffers( VkDevice device, VkCommandPool pool, uint32_t count, const VkCommandBuffer *buffers )
{
    uint32_t i;
    for (i = 0; i < count; i++) { st.cmdbuf_frees++; free( buffers[i] ); }
}

static VkResult VKAPI_CALL mock_vkBeginCommandBuffer( VkCommandBuffer handle, const VkCommandBufferBeginInfo *info )
{
    struct mock_cmdbuf *cb = (void *)handle;
    cb->op_count = 0;
    st.begin_flags = info->flags;
    return VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkEndCommandBuffer( VkCommandBuffer handle ) { return VK_SUCCESS; }

static void VKAPI_CALL mock_vkCmdCopyBuffer( VkCommandBuffer handle, VkBuffer src, VkBuffer dst, uint32_t count, const VkBufferCopy *regions )
{
    struct mock_cmdbuf *cb = (void *)handle;
    uint32_t i;
    for (i = 0; i < count && cb->op_count < 16; i++)
    {
        struct mock_op *op = &cb->ops[cb->op_count++];
        op->kind = OP_COPY_BUFFER;
        op->src = (void *)(uintptr_t)src;
        op->dst = (void *)(uintptr_t)dst;
        op->region = regions[i];
    }
}

static void VKAPI_CALL mock_vkCmdPipelineBarrier( VkCommandBuffer handle, VkPipelineStageFlags src_stage, VkPipelineStageFlags dst_stage,
                                                  VkDependencyFlags flags, uint32_t memory_count, const VkMemoryBarrier *memory,
                                                  uint32_t buffer_count, const VkBufferMemoryBarrier *buffers,
                                                  uint32_t image_count, const VkImageMemoryBarrier *images )
{
    uint32_t i;
    for (i = 0; i < image_count; i++)
    {
        struct mock_image *image = (void *)(uintptr_t)images[i].image;
        st.image_barriers += image->magic == IMAGE_MAGIC && images[i].subresourceRange.aspectMask == VK_IMAGE_ASPECT_COLOR_BIT &&
                             images[i].subresourceRange.levelCount == 1;
        st.last_barrier_new_layout = images[i].newLayout;
    }
    for (i = 0; i < buffer_count; i++)
    {
        struct mock_buffer *buffer = (void *)(uintptr_t)buffers[i].buffer;
        st.buffer_barriers += buffer->magic == BUFFER_MAGIC && buffers[i].size == VK_WHOLE_SIZE;
    }
}

static void VKAPI_CALL mock_vkCmdClearColorImage( VkCommandBuffer handle, VkImage image, VkImageLayout layout, const VkClearColorValue *color,
                                                  uint32_t count, const VkImageSubresourceRange *ranges_ )
{
    struct mock_cmdbuf *cb = (void *)handle;
    struct mock_op *op;
    int i;
    if (cb->op_count >= 16 || !count || ranges_[0].aspectMask != VK_IMAGE_ASPECT_COLOR_BIT) return;
    op = &cb->ops[cb->op_count++];
    op->kind = OP_CLEAR_IMAGE;
    op->image = (void *)(uintptr_t)image;
    for (i = 0; i < 4; i++) op->color[i] = (uint8_t)(color->float32[i] * 255.0f + 0.5f);
}

static void VKAPI_CALL mock_vkCmdCopyImageToBuffer( VkCommandBuffer handle, VkImage image, VkImageLayout layout, VkBuffer buffer,
                                                    uint32_t count, const VkBufferImageCopy *regions )
{
    struct mock_cmdbuf *cb = (void *)handle;
    uint32_t i;
    for (i = 0; i < count && cb->op_count < 16; i++)
    {
        struct mock_op *op = &cb->ops[cb->op_count++];
        op->kind = OP_IMAGE_TO_BUFFER;
        op->image = (void *)(uintptr_t)image;
        op->dst = (void *)(uintptr_t)buffer;
        op->image_region = regions[i];
    }
}

static VkResult VKAPI_CALL mock_vkQueueSubmit( VkQueue queue, uint32_t count, const VkSubmitInfo *submits, VkFence fence )
{
    uint32_t i, j;
    int k;

    st.submits++;
    for (i = 0; i < count; i++)
        for (j = 0; j < submits[i].commandBufferCount; j++)
        {
            struct mock_cmdbuf *cb = (void *)submits[i].pCommandBuffers[j];
            if (cb->magic != 0x01CDC0DE) return VK_ERROR_DEVICE_LOST;       /* not one of this library's command buffers */
            for (k = 0; k < cb->op_count; k++)
            {
                struct mock_op *op = &cb->ops[k];
                uint32_t n;
                switch (op->kind)
                {
                case OP_COPY_BUFFER:
                    memcpy( storage( op->dst->memory ) + op->dst->offset + op->region.dstOffset,
                            storage( op->src->memory ) + op->src->offset + op->region.srcOffset, op->region.size );
                    break;
                case OP_CLEAR_IMAGE:
                    for (n = 0; n < op->image->width * op->image->height; n++) memcpy( op->image->pixels + 4 * n, op->color, 4 );
                    break;
                case OP_IMAGE_TO_BUFFER:      /* the GPU writes into the buffer's memory: for a 32-bit program, into its own address space */
                    if (op->image_region.imageExtent.width != op->image->width || op->image_region.imageExtent.height != op->image->height ||
                        op->image_region.imageSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || op->image_region.imageSubresource.layerCount != 1)
                        return VK_ERROR_DEVICE_LOST;
                    memcpy( storage( op->dst->memory ) + op->dst->offset + op->image_region.bufferOffset, op->image->pixels,
                            (size_t)op->image->width * op->image->height * 4 );
                    break;
                }
                st.copies_executed++;
            }
        }
    if (fence)
    {
        struct mock_fence *f = (void *)(uintptr_t)fence;
        if (f->magic != FENCE_MAGIC) return VK_ERROR_DEVICE_LOST;
        f->signaled = 1;
        st.fence_signals++;
    }
    return VK_SUCCESS;
}

static VkResult VKAPI_CALL mock_vkSetDebugUtilsObjectNameEXT( VkDevice handle, const VkDebugUtilsObjectNameInfoEXT *info )
{
    struct mock_device *device = (void *)handle;
    if (st.named_count < MOCKVK_NAMED)
    {
        st.named_type[st.named_count] = info->objectType;
        st.named_object[st.named_count] = info->objectHandle;
        copy_name( st.named_name[st.named_count], info->pObjectName );
        st.named_count++;
    }
    /* and say so through the messengers, with the object: the handle must come back as the application's own */
    fire_utils( device->instance, info->objectType, info->objectHandle, info->pObjectName, "object named" );
    return VK_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------------------------- lookup */
#define F(name) { #name, (PFN_vkVoidFunction)mock_##name }
static const struct { const char *name; PFN_vkVoidFunction func; } instance_funcs[] =
{
    F(vkEnumerateInstanceExtensionProperties), F(vkEnumerateInstanceVersion), F(vkCreateInstance), F(vkDestroyInstance),
    F(vkEnumeratePhysicalDevices), F(vkGetPhysicalDeviceProperties), F(vkGetPhysicalDeviceProperties2),
    { "vkGetPhysicalDeviceProperties2KHR", (PFN_vkVoidFunction)mock_vkGetPhysicalDeviceProperties2 },
    F(vkGetPhysicalDeviceFeatures), F(vkGetPhysicalDeviceFeatures2),
    { "vkGetPhysicalDeviceFeatures2KHR", (PFN_vkVoidFunction)mock_vkGetPhysicalDeviceFeatures2 },
    F(vkGetPhysicalDeviceMemoryProperties), F(vkGetPhysicalDeviceQueueFamilyProperties), F(vkEnumerateDeviceExtensionProperties),
    F(vkGetPhysicalDeviceFormatProperties),
    F(vkCreateMetalSurfaceEXT), F(vkDestroySurfaceKHR), F(vkGetPhysicalDeviceSurfaceSupportKHR),
    F(vkCreateDebugUtilsMessengerEXT), F(vkDestroyDebugUtilsMessengerEXT), F(vkCreateDebugReportCallbackEXT),
    F(vkDestroyDebugReportCallbackEXT), F(vkDebugReportMessageEXT), F(vkCreateDevice),
};
static const struct { const char *name; PFN_vkVoidFunction func; } device_funcs[] =
{
    F(vkDestroyDevice), F(vkGetDeviceQueue), F(vkGetDeviceQueue2), F(vkDeviceWaitIdle), F(vkQueueWaitIdle),
    F(vkGetMemoryHostPointerPropertiesEXT), F(vkAllocateMemory), F(vkFreeMemory), F(vkMapMemory), F(vkMapMemory2KHR),
    F(vkUnmapMemory), F(vkUnmapMemory2KHR), F(vkCreateBuffer), F(vkDestroyBuffer), F(vkGetBufferMemoryRequirements),
    F(vkBindBufferMemory), F(vkCreateCommandPool), F(vkDestroyCommandPool), F(vkAllocateCommandBuffers), F(vkFreeCommandBuffers),
    F(vkBeginCommandBuffer), F(vkEndCommandBuffer), F(vkCmdCopyBuffer), F(vkQueueSubmit), F(vkSetDebugUtilsObjectNameEXT),
    F(vkCreateImage), F(vkDestroyImage), F(vkGetImageMemoryRequirements), F(vkBindImageMemory), F(vkCreateFence), F(vkDestroyFence),
    F(vkWaitForFences), F(vkFlushMappedMemoryRanges), F(vkInvalidateMappedMemoryRanges), F(vkCmdPipelineBarrier),
    F(vkCmdClearColorImage), F(vkCmdCopyImageToBuffer),
};
#undef F

__attribute__((visibility("default"))) PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr( VkDevice device, const char *name )
{
    size_t i;
    for (i = 0; i < sizeof(device_funcs) / sizeof(device_funcs[0]); i++)
        if (!strcmp( device_funcs[i].name, name )) return device_funcs[i].func;
    /* the placed map needs the device to have been created for it; MoltenVK has no vkMapMemory2KHR placed path at all */
    return NULL;
}

__attribute__((visibility("default"))) PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr( VkInstance instance, const char *name )
{
    size_t i;
    if (!strcmp( name, "vkGetInstanceProcAddr" )) return (PFN_vkVoidFunction)vkGetInstanceProcAddr;
    if (!strcmp( name, "vkGetDeviceProcAddr" )) return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
    for (i = 0; i < sizeof(instance_funcs) / sizeof(instance_funcs[0]); i++)
        if (!strcmp( instance_funcs[i].name, name )) return instance_funcs[i].func;
    return vkGetDeviceProcAddr( NULL, name );
}
