#!/usr/bin/env python3
# Copyright 2026 intraducine and 125hz. This file was taken from https://github.com/willfaust/Madeira (pull requests #353 and #462)
# under GPL-3.0-or-later with the Madeira Converter Exception. See THIRD-PARTY-NOTICES.md.
"""Run Wine's production extension query and the iOS Metal/Win32 mapping.

The host exports Vulkan loader entry points, with no vkCreateMetalSurfaceEXT
symbol. Metal support must come from the advertised extension list. No GPU.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
adapter = (root / 'build/win32u-unix/vulkan_ios.c').read_text()
assert '#include "../../wine/dlls/win32u/vulkan.c"' in adapter
assert '#include "vulkan_surface_ios.c"' in adapter


def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}\n', start) + 3
    return source[start:end]


loader = function((root / 'wine/dlls/win32u/vulkan.c').read_text(),
                  'static void vulkan_init_once(void)')
mapping = function((root / 'build/win32u-unix/vulkan_surface_ios.c').read_text(),
                   'static void ios_vulkan_map_instance_extensions(')
prelude = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define ERR(...) ((void)0)
#define WARN(...) ((void)0)
#define TRACE(...) ((void)0)
#define SONAME_LIBVULKAN "fixture"
#define RTLD_NOW 0
#define VK_INCOMPLETE 5
#define ALL_VK_INSTANCE_EXTS USE_VK_EXT(VK_KHR_surface) \
    USE_VK_EXT(VK_EXT_metal_surface) USE_VK_EXT(VK_EXT_headless_surface)
#define ALL_VK_CLIENT_INSTANCE_EXTS USE_VK_EXT(VK_KHR_surface) USE_VK_EXT(VK_KHR_win32_surface)
typedef int VkResult;
typedef struct { char extensionName[256]; uint32_t specVersion; } VkExtensionProperties;
typedef void (*PFN_vkVoidFunction)(void);
typedef PFN_vkVoidFunction (*PFN_vkGetInstanceProcAddr)(void *, const char *);
typedef PFN_vkGetInstanceProcAddr PFN_vkGetDeviceProcAddr;
typedef PFN_vkVoidFunction PFN_vkCreateInstance;
typedef VkResult (*PFN_vkEnumerateInstanceExtensionProperties)(const char *, uint32_t *, VkExtensionProperties *);
struct vulkan_instance_extensions {
    int has_VK_KHR_surface, has_VK_EXT_metal_surface;
    int has_VK_EXT_headless_surface, has_VK_KHR_win32_surface;
};
static void ios_vulkan_map_instance_extensions(struct vulkan_instance_extensions *);
static struct driver {
    void (*p_map_instance_extensions)(struct vulkan_instance_extensions *);
} lazydrv_funcs = {ios_vulkan_map_instance_extensions}, *driver_funcs;
static struct {
    PFN_vkGetInstanceProcAddr p_vkGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr p_vkGetDeviceProcAddr;
    struct vulkan_instance_extensions host_extensions, client_extensions;
} vulkan_funcs;
static void *vulkan_handle;
static PFN_vkGetInstanceProcAddr p_vkGetInstanceProcAddr;
static PFN_vkGetDeviceProcAddr p_vkGetDeviceProcAddr;
static PFN_vkCreateInstance p_vkCreateInstance;
static PFN_vkEnumerateInstanceExtensionProperties p_vkEnumerateInstanceExtensionProperties;
static const char *const *fake_list;
static int fake_has_gipa, fake_query_error, query_calls;
static VkResult fake_enumerate(const char *layer, uint32_t *count, VkExtensionProperties *props)
{
    uint32_t n = 0, i;
    assert(!layer);
    query_calls++;
    if (fake_query_error) return -3;
    while (fake_list[n]) n++;
    if (!props) { *count = n; return 0; }
    for (i = 0; i < n && i < *count; i++) strcpy(props[i].extensionName, fake_list[i]);
    *count = i;
    return 0;
}
static PFN_vkVoidFunction fake_gipa(void *instance, const char *name)
{
    assert(!instance);
    if (!strcmp(name, "vkEnumerateInstanceExtensionProperties"))
        return (PFN_vkVoidFunction)fake_enumerate;
    return NULL;
}
static void *dlopen(const char *name, int flags) { return (void *)1; }
static int dlclose(void *handle) { return 0; }
static void *dlsym(void *handle, const char *name)
{
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (void *)fake_gipa;
    if (fake_has_gipa && !strcmp(name, "vkGetInstanceProcAddr")) return (void *)fake_gipa;
    return NULL;
}
static void reset(void)
{
    memset(&vulkan_funcs, 0, sizeof(vulkan_funcs));
    query_calls = fake_query_error = 0;
}
'''
tests = r'''
int main(void)
{
    static const char *const metal[] = { "VK_KHR_surface", "VK_EXT_metal_surface", NULL };
    static const char *const headless[] = { "VK_KHR_surface", "VK_EXT_headless_surface", NULL };
    static const char *const neither[] = { "VK_KHR_surface", NULL };
    fake_has_gipa = 1;
    fake_list = metal;
    reset(); vulkan_init_once();
    assert(query_calls == 2 && vulkan_funcs.host_extensions.has_VK_EXT_metal_surface);
    assert(vulkan_funcs.client_extensions.has_VK_KHR_win32_surface);
    assert(!dlsym(vulkan_handle, "vkCreateMetalSurfaceEXT"));
    fake_list = headless;
    reset(); vulkan_init_once();
    assert(vulkan_funcs.host_extensions.has_VK_EXT_headless_surface);
    assert(!vulkan_funcs.client_extensions.has_VK_KHR_win32_surface);
    fake_list = neither;
    reset(); vulkan_init_once();
    assert(!vulkan_funcs.host_extensions.has_VK_EXT_metal_surface);
    assert(!vulkan_funcs.client_extensions.has_VK_KHR_win32_surface);
    fake_has_gipa = 0;
    fake_list = metal;
    reset(); vulkan_init_once();
    assert(!vulkan_handle && !query_calls);
    assert(!vulkan_funcs.client_extensions.has_VK_KHR_win32_surface);
    fake_has_gipa = 1;
    reset(); fake_query_error = 1; vulkan_init_once();
    assert(query_calls == 1 && !vulkan_funcs.client_extensions.has_VK_KHR_win32_surface);
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-vk-surface-') as tmp:
    c = Path(tmp) / 'check.c'
    exe = Path(tmp) / 'check'
    c.write_text(prelude + mapping + loader + tests)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-fsanitize=address,undefined',
                    str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS: production Vulkan query and iOS mapping use advertised Metal support; '
      'headless, absent extension, missing loader and query-error cases stay unavailable')
