// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// BCD's Vulkan test: the spinning textured cube of the DirectX tests (dxtest.h), drawn with Vulkan 1.0 through vulkan-1.dll the
// way a Windows game does: an instance with VK_KHR_win32_surface, the first GPU, a swapchain on the window, a render pass with a
// depth buffer, one graphics pipeline (the SPIR-V of vk_shaders.h, made from vk_cube.spvasm), a texture uploaded through a staging
// buffer, the matrix as a push constant, and one frame in flight. One source, two programs: vulkan-test-x64.exe (Start menu:
// "Vulkan test") and vulkan-test-x86.exe ("Vulkan test (32-bit)"). After two seconds of drawing it logs "PASS" or "FAIL" with the
// frame count and rate (dxt_verdict) and puts the word into the window title; it then copies two pixels of the next frame back
// (where the cube is and a corner) and logs whether the cube is really in the picture.
//
// A session has Vulkan only when the game's own file says `vulkan = moltenvk` or `kosmickrisp` (the Windows desktop's Options >
// Direct3D > Vulkan); without it vulkan-1.dll is Wine's stand-in, which finds no driver, or, for a 32-bit program, there is none.
// The test then says how to turn Vulkan on instead of only failing. It is built with llvm-mingw (tools/build-dx-tests.sh), with
// Wine's own Vulkan header (wine/include/wine/vulkan.h, the one winevulkan is built with) and no import library: vulkan-1.dll is
// loaded at run time.

#define DXT_BOX_TITLE "BCD Vulkan test"
#define VK_NO_PROTOTYPES
#include "dxtest.h"
#include "wine/vulkan.h"
#include "vk_shaders.h"

#define API "Vulkan"

/* The Vulkan functions, all taken through vkGetInstanceProcAddr (device functions too: they go through the loader's dispatch). */
#define VK_GLOBAL_FUNCS(X) X(vkCreateInstance) X(vkEnumerateInstanceExtensionProperties)
#define VK_INSTANCE_FUNCS(X) \
    X(vkDestroyInstance) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceProperties) X(vkGetPhysicalDeviceQueueFamilyProperties) \
    X(vkGetPhysicalDeviceMemoryProperties) X(vkGetPhysicalDeviceFormatProperties) X(vkEnumerateDeviceExtensionProperties) \
    X(vkCreateWin32SurfaceKHR) X(vkDestroySurfaceKHR) X(vkGetPhysicalDeviceSurfaceSupportKHR) X(vkGetPhysicalDeviceSurfaceCapabilitiesKHR) \
    X(vkGetPhysicalDeviceSurfaceFormatsKHR) X(vkCreateDevice) \
    X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkCreateSwapchainKHR) X(vkDestroySwapchainKHR) X(vkGetSwapchainImagesKHR) \
    X(vkAcquireNextImageKHR) X(vkQueuePresentKHR) X(vkCreateImageView) X(vkDestroyImageView) X(vkCreateImage) X(vkDestroyImage) \
    X(vkGetImageMemoryRequirements) X(vkAllocateMemory) X(vkFreeMemory) X(vkBindImageMemory) X(vkCreateBuffer) X(vkDestroyBuffer) \
    X(vkGetBufferMemoryRequirements) X(vkBindBufferMemory) X(vkMapMemory) X(vkUnmapMemory) X(vkCreateRenderPass) X(vkDestroyRenderPass) \
    X(vkCreateFramebuffer) X(vkDestroyFramebuffer) X(vkCreateShaderModule) X(vkDestroyShaderModule) X(vkCreatePipelineLayout) \
    X(vkDestroyPipelineLayout) X(vkCreateGraphicsPipelines) X(vkDestroyPipeline) X(vkCreateDescriptorSetLayout) \
    X(vkDestroyDescriptorSetLayout) X(vkCreateDescriptorPool) X(vkDestroyDescriptorPool) X(vkAllocateDescriptorSets) \
    X(vkUpdateDescriptorSets) X(vkCreateSampler) X(vkDestroySampler) X(vkCreateCommandPool) X(vkDestroyCommandPool) \
    X(vkAllocateCommandBuffers) X(vkFreeCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer) X(vkResetCommandBuffer) \
    X(vkCmdBeginRenderPass) X(vkCmdEndRenderPass) X(vkCmdBindPipeline) X(vkCmdBindVertexBuffers) X(vkCmdBindIndexBuffer) \
    X(vkCmdBindDescriptorSets) X(vkCmdPushConstants) X(vkCmdSetViewport) X(vkCmdSetScissor) X(vkCmdDrawIndexed) \
    X(vkCmdPipelineBarrier) X(vkCmdCopyBufferToImage) X(vkCmdCopyImageToBuffer) X(vkCreateSemaphore) X(vkDestroySemaphore) \
    X(vkCreateFence) X(vkDestroyFence) X(vkWaitForFences) X(vkResetFences) X(vkQueueSubmit) X(vkQueueWaitIdle) X(vkDeviceWaitIdle)

#define VK_DECLARE(name) static PFN_##name name##_;
VK_GLOBAL_FUNCS(VK_DECLARE)
VK_INSTANCE_FUNCS(VK_DECLARE)
#undef VK_DECLARE
static PFN_vkGetInstanceProcAddr vkGetInstanceProcAddr_;

#define MAX_IMAGES 8

static struct {
    VkInstance instance;
    VkPhysicalDevice gpu;
    VkPhysicalDeviceProperties props;
    VkPhysicalDeviceMemoryProperties mem;
    VkSurfaceKHR surface;
    VkDevice dev;
    uint32_t family;
    VkQueue queue;
    VkFormat depth_format;
    /* the swapchain and what depends on it (made again when the window's surface changes) */
    VkSwapchainKHR swapchain;
    VkSurfaceFormatKHR format;
    VkExtent2D extent;
    int can_read;   /* the swapchain's images take VK_IMAGE_USAGE_TRANSFER_SRC_BIT */
    uint32_t count;
    VkImage images[MAX_IMAGES];
    VkImageView views[MAX_IMAGES];
    VkFramebuffer framebuffers[MAX_IMAGES];
    VkSemaphore rendered[MAX_IMAGES];   /* one a swapchain image: a semaphore a present waits for is not signalled again before it is done */
    VkImage depth;
    VkDeviceMemory depth_mem;
    VkImageView depth_view;
    /* the rest */
    VkRenderPass pass;
    VkDescriptorSetLayout set_layout;
    VkPipelineLayout layout;
    VkPipeline pipeline;
    VkCommandPool pool;
    VkCommandBuffer cmd;
    VkSemaphore acquired;
    VkFence done;
    VkBuffer vbuf, ibuf, readbuf;
    VkDeviceMemory vmem, imem, readmem;
    VkImage tex;
    VkDeviceMemory tex_mem;
    VkImageView tex_view;
    VkSampler sampler;
    VkDescriptorPool dpool;
    VkDescriptorSet set;
} vk;

static const char *vk_result(VkResult r)
{
    switch ((int)r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_SUBOPTIMAL_KHR: return "VK_SUBOPTIMAL_KHR";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
    case VK_ERROR_LAYER_NOT_PRESENT: return "VK_ERROR_LAYER_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    case VK_ERROR_TOO_MANY_OBJECTS: return "VK_ERROR_TOO_MANY_OBJECTS";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
    case VK_ERROR_SURFACE_LOST_KHR: return "VK_ERROR_SURFACE_LOST_KHR";
    case VK_ERROR_NATIVE_WINDOW_IN_USE_KHR: return "VK_ERROR_NATIVE_WINDOW_IN_USE_KHR";
    case VK_ERROR_OUT_OF_DATE_KHR: return "VK_ERROR_OUT_OF_DATE_KHR";
    }
    return "an error";
}

/* A Vulkan call that must work: on failure its name and VkResult are logged and shown, and the program ends. */
#define VKCHECK(call, what) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) { \
        char m_[200]; snprintf(m_, sizeof m_, "%s failed (%s, %d)", what, vk_result(r_), (int)r_); dxt_fail(API, m_); return 0; } } while (0)

/* Vulkan is off in this session (no vulkan-1.dll, or Wine's stand-in that has no driver): say how to turn it on. */
static void vulkan_off(const char *why)
{
    dxt_log(API " (%d-bit): Vulkan is not on in this session (%s); the Windows desktop's Options > Direct3D > Vulkan turns it on", DXT_BITS, why);
    MessageBoxA(NULL,
                "Vulkan is not on in this session.\n\n"
                "Turn it on in the Windows desktop's Options (Home > Windows desktop > the sliders button) > Direct3D > "
                "Vulkan (experimental). Then stop the Windows desktop and start it again, and run this test once more.\n\n"
                "A game gets Vulkan the same way, from its own Options.",
                DXT_BOX_TITLE, MB_OK | MB_ICONINFORMATION);
}

static int has_extension(const VkExtensionProperties *ext, uint32_t n, const char *name)
{
    uint32_t i;
    for (i = 0; i < n; i++)
        if (!strcmp(ext[i].extensionName, name)) return 1;
    return 0;
}

static uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags want)
{
    uint32_t i;
    for (i = 0; i < vk.mem.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (vk.mem.memoryTypes[i].propertyFlags & want) == want) return i;
    return UINT32_MAX;
}

/* A buffer with memory of the given properties, bound; 1 on success. */
static int make_buffer(VkDeviceSize size, VkBufferUsageFlags usage, VkMemoryPropertyFlags props, VkBuffer *buf, VkDeviceMemory *mem)
{
    VkBufferCreateInfo bi;
    VkMemoryRequirements req;
    VkMemoryAllocateInfo ai;

    memset(&bi, 0, sizeof bi);
    bi.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
    bi.size = size;
    bi.usage = usage;
    bi.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    VKCHECK(vkCreateBuffer_(vk.dev, &bi, NULL, buf), "vkCreateBuffer");
    vkGetBufferMemoryRequirements_(vk.dev, *buf, &req);
    memset(&ai, 0, sizeof ai);
    ai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = memory_type(req.memoryTypeBits, props);
    if (ai.memoryTypeIndex == UINT32_MAX) { dxt_fail(API, "no memory type for a buffer"); return 0; }
    VKCHECK(vkAllocateMemory_(vk.dev, &ai, NULL, mem), "vkAllocateMemory (buffer)");
    VKCHECK(vkBindBufferMemory_(vk.dev, *buf, *mem, 0), "vkBindBufferMemory");
    return 1;
}

/* A 2D image in device-local memory, bound, and a view of it; 1 on success. */
static int make_image(uint32_t w, uint32_t h, VkFormat format, VkImageUsageFlags usage, VkImageAspectFlags aspect,
                      VkImage *image, VkDeviceMemory *mem, VkImageView *view)
{
    VkImageCreateInfo ii;
    VkMemoryRequirements req;
    VkMemoryAllocateInfo ai;
    VkImageViewCreateInfo vi;

    memset(&ii, 0, sizeof ii);
    ii.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    ii.imageType = VK_IMAGE_TYPE_2D;
    ii.format = format;
    ii.extent.width = w;
    ii.extent.height = h;
    ii.extent.depth = 1;
    ii.mipLevels = 1;
    ii.arrayLayers = 1;
    ii.samples = VK_SAMPLE_COUNT_1_BIT;
    ii.tiling = VK_IMAGE_TILING_OPTIMAL;
    ii.usage = usage;
    ii.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VKCHECK(vkCreateImage_(vk.dev, &ii, NULL, image), "vkCreateImage");
    vkGetImageMemoryRequirements_(vk.dev, *image, &req);
    memset(&ai, 0, sizeof ai);
    ai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (ai.memoryTypeIndex == UINT32_MAX) ai.memoryTypeIndex = memory_type(req.memoryTypeBits, 0);
    if (ai.memoryTypeIndex == UINT32_MAX) { dxt_fail(API, "no memory type for an image"); return 0; }
    VKCHECK(vkAllocateMemory_(vk.dev, &ai, NULL, mem), "vkAllocateMemory (image)");
    VKCHECK(vkBindImageMemory_(vk.dev, *image, *mem, 0), "vkBindImageMemory");
    memset(&vi, 0, sizeof vi);
    vi.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
    vi.image = *image;
    vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vi.format = format;
    vi.subresourceRange.aspectMask = aspect;
    vi.subresourceRange.levelCount = 1;
    vi.subresourceRange.layerCount = 1;
    VKCHECK(vkCreateImageView_(vk.dev, &vi, NULL, view), "vkCreateImageView");
    return 1;
}

static void barrier(VkCommandBuffer cmd, VkImage image, VkImageAspectFlags aspect, VkImageLayout from, VkImageLayout to,
                    VkAccessFlags src_access, VkAccessFlags dst_access, VkPipelineStageFlags src_stage, VkPipelineStageFlags dst_stage)
{
    VkImageMemoryBarrier b;
    memset(&b, 0, sizeof b);
    b.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    b.srcAccessMask = src_access;
    b.dstAccessMask = dst_access;
    b.oldLayout = from;
    b.newLayout = to;
    b.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange.aspectMask = aspect;
    b.subresourceRange.levelCount = 1;
    b.subresourceRange.layerCount = 1;
    vkCmdPipelineBarrier_(cmd, src_stage, dst_stage, 0, 0, NULL, 0, NULL, 1, &b);
}

/* ------------------------------------------------------------------ the swapchain and what depends on it */

static void destroy_swapchain(void)
{
    uint32_t i;
    for (i = 0; i < vk.count; i++) {
        if (vk.framebuffers[i]) vkDestroyFramebuffer_(vk.dev, vk.framebuffers[i], NULL);
        if (vk.views[i]) vkDestroyImageView_(vk.dev, vk.views[i], NULL);
        if (vk.rendered[i]) vkDestroySemaphore_(vk.dev, vk.rendered[i], NULL);
        vk.framebuffers[i] = VK_NULL_HANDLE;
        vk.views[i] = VK_NULL_HANDLE;
        vk.rendered[i] = VK_NULL_HANDLE;
    }
    vk.count = 0;
    if (vk.depth_view) vkDestroyImageView_(vk.dev, vk.depth_view, NULL);
    if (vk.depth) vkDestroyImage_(vk.dev, vk.depth, NULL);
    if (vk.depth_mem) vkFreeMemory_(vk.dev, vk.depth_mem, NULL);
    vk.depth_view = VK_NULL_HANDLE;
    vk.depth = VK_NULL_HANDLE;
    vk.depth_mem = VK_NULL_HANDLE;
    if (vk.swapchain) vkDestroySwapchainKHR_(vk.dev, vk.swapchain, NULL);
    vk.swapchain = VK_NULL_HANDLE;
}

/* 1: made; 0: failed (said so); 2: the window has no size now (minimised), nothing made. */
static int make_swapchain(int first)
{
    VkSurfaceCapabilitiesKHR caps;
    VkSurfaceFormatKHR formats[64];
    uint32_t n = 64, i;
    VkSwapchainCreateInfoKHR si;
    VkSwapchainKHR old = vk.swapchain;
    static const VkCompositeAlphaFlagBitsKHR alphas[] = { VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR, VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR,
                                                          VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR, VK_COMPOSITE_ALPHA_POST_MULTIPLIED_BIT_KHR };

    VKCHECK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR_(vk.gpu, vk.surface, &caps), "vkGetPhysicalDeviceSurfaceCapabilitiesKHR");
    if (caps.currentExtent.width == UINT32_MAX) {
        vk.extent.width = DXT_W;
        vk.extent.height = DXT_H;
        if (vk.extent.width < caps.minImageExtent.width) vk.extent.width = caps.minImageExtent.width;
        if (vk.extent.width > caps.maxImageExtent.width) vk.extent.width = caps.maxImageExtent.width;
        if (vk.extent.height < caps.minImageExtent.height) vk.extent.height = caps.minImageExtent.height;
        if (vk.extent.height > caps.maxImageExtent.height) vk.extent.height = caps.maxImageExtent.height;
    } else {
        vk.extent = caps.currentExtent;
    }
    if (!vk.extent.width || !vk.extent.height) return 2;
    {
        VkResult r = vkGetPhysicalDeviceSurfaceFormatsKHR_(vk.gpu, vk.surface, &n, formats);
        if (r != VK_SUCCESS && r != VK_INCOMPLETE) VKCHECK(r, "vkGetPhysicalDeviceSurfaceFormatsKHR");
    }
    if (!n) { dxt_fail(API, "the surface has no formats"); return 0; }
    vk.format = formats[0];
    if (n == 1 && formats[0].format == VK_FORMAT_UNDEFINED) {
        vk.format.format = VK_FORMAT_B8G8R8A8_UNORM;
        vk.format.colorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
    }
    for (i = 0; i < n; i++)
        if (formats[i].format == VK_FORMAT_B8G8R8A8_UNORM || formats[i].format == VK_FORMAT_R8G8B8A8_UNORM) { vk.format = formats[i]; break; }
    vk.can_read = (caps.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) != 0;

    memset(&si, 0, sizeof si);
    si.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR;
    si.surface = vk.surface;
    si.minImageCount = caps.minImageCount + 1;
    if (caps.maxImageCount && si.minImageCount > caps.maxImageCount) si.minImageCount = caps.maxImageCount;
    if (si.minImageCount > MAX_IMAGES) si.minImageCount = MAX_IMAGES;
    si.imageFormat = vk.format.format;
    si.imageColorSpace = vk.format.colorSpace;
    si.imageExtent = vk.extent;
    si.imageArrayLayers = 1;
    si.imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | (vk.can_read ? VK_IMAGE_USAGE_TRANSFER_SRC_BIT : 0);
    si.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
    si.preTransform = caps.currentTransform;
    si.compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    for (i = 0; i < 4; i++)
        if (caps.supportedCompositeAlpha & alphas[i]) { si.compositeAlpha = alphas[i]; break; }
    si.presentMode = VK_PRESENT_MODE_FIFO_KHR;   /* the one every implementation has; it holds the frame rate to the display */
    si.clipped = VK_TRUE;
    si.oldSwapchain = old;
    VKCHECK(vkCreateSwapchainKHR_(vk.dev, &si, NULL, &vk.swapchain), "vkCreateSwapchainKHR");
    if (old) {
        VkSwapchainKHR made = vk.swapchain;
        vk.swapchain = old;
        destroy_swapchain();   /* the old one's images, views and framebuffers */
        vk.swapchain = made;
    }
    {   /* an implementation may make more images than asked for: all of them must fit, since vkAcquireNextImageKHR may hand any out */
        uint32_t have = 0;
        VKCHECK(vkGetSwapchainImagesKHR_(vk.dev, vk.swapchain, &have, NULL), "vkGetSwapchainImagesKHR (count)");
        if (!have || have > MAX_IMAGES) {
            char m[120];
            snprintf(m, sizeof m, "the swapchain has %u images (this test takes 1 to %d)", have, MAX_IMAGES);
            dxt_fail(API, m);
            return 0;
        }
        vk.count = have;
        VKCHECK(vkGetSwapchainImagesKHR_(vk.dev, vk.swapchain, &vk.count, vk.images), "vkGetSwapchainImagesKHR");
    }
    if (first)
        dxt_log(API ": swapchain %ux%u, %u images, format %d (colour space %d), FIFO, read-back %s", vk.extent.width, vk.extent.height, vk.count,
                (int)vk.format.format, (int)vk.format.colorSpace, vk.can_read ? "possible" : "not offered (no TRANSFER_SRC)");
    if (!make_image(vk.extent.width, vk.extent.height, vk.depth_format, VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT,
                    VK_IMAGE_ASPECT_DEPTH_BIT | (vk.depth_format == VK_FORMAT_D24_UNORM_S8_UINT || vk.depth_format == VK_FORMAT_D32_SFLOAT_S8_UINT
                                                 ? VK_IMAGE_ASPECT_STENCIL_BIT : 0),
                    &vk.depth, &vk.depth_mem, &vk.depth_view))
        return 0;
    for (i = 0; i < vk.count; i++) {
        VkImageViewCreateInfo vi;
        VkFramebufferCreateInfo fi;
        VkSemaphoreCreateInfo sem;
        VkImageView att[2];

        memset(&vi, 0, sizeof vi);
        vi.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
        vi.image = vk.images[i];
        vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
        vi.format = vk.format.format;
        vi.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        vi.subresourceRange.levelCount = 1;
        vi.subresourceRange.layerCount = 1;
        VKCHECK(vkCreateImageView_(vk.dev, &vi, NULL, &vk.views[i]), "vkCreateImageView (swapchain)");
        att[0] = vk.views[i];
        att[1] = vk.depth_view;
        memset(&fi, 0, sizeof fi);
        fi.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO;
        fi.renderPass = vk.pass;
        fi.attachmentCount = 2;
        fi.pAttachments = att;
        fi.width = vk.extent.width;
        fi.height = vk.extent.height;
        fi.layers = 1;
        VKCHECK(vkCreateFramebuffer_(vk.dev, &fi, NULL, &vk.framebuffers[i]), "vkCreateFramebuffer");
        memset(&sem, 0, sizeof sem);
        sem.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
        VKCHECK(vkCreateSemaphore_(vk.dev, &sem, NULL, &vk.rendered[i]), "vkCreateSemaphore");
    }
    return 1;
}

/* The window's surface changed (or was lost for a moment): wait for the GPU, then a new swapchain. 0 when that failed (said so);
 * while the window has no size (minimised) the old one stays and this waits a little, so the loop does not spin. */
static unsigned rebuilds;
static int rebuild(const char *why)
{
    int made;
    vkDeviceWaitIdle_(vk.dev);
    made = make_swapchain(0);
    if (++rebuilds <= 3) dxt_log(API ": swapchain made again (%s)%s", why, made == 2 ? ": the window has no size now, waiting" : "");
    if (made == 2) Sleep(50);
    else if (rebuilds > 10) Sleep(16);   /* a surface that keeps going out of date is not a reason to spin */
    return made != 0;
}

/* ------------------------------------------------------------------ set-up */

/* The render pass: the swapchain image (cleared, then presented) and the depth buffer (cleared, not kept). It is made before the
 * swapchain, whose format it needs: the first make_swapchain call only reads the format, so the pass is made from a probe. */
static int make_render_pass(void)
{
    VkAttachmentDescription att[2];
    VkAttachmentReference colour = { 0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL }, depth = { 1, VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL };
    VkSubpassDescription sub;
    VkSubpassDependency dep;
    VkRenderPassCreateInfo ri;

    memset(att, 0, sizeof att);
    att[0].format = vk.format.format;
    att[0].samples = VK_SAMPLE_COUNT_1_BIT;
    att[0].loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
    att[0].storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    att[0].stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    att[0].stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    att[0].initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    att[0].finalLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    att[1].format = vk.depth_format;
    att[1].samples = VK_SAMPLE_COUNT_1_BIT;
    att[1].loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
    att[1].storeOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    att[1].stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    att[1].stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    att[1].initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    att[1].finalLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
    memset(&sub, 0, sizeof sub);
    sub.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    sub.colorAttachmentCount = 1;
    sub.pColorAttachments = &colour;
    sub.pDepthStencilAttachment = &depth;
    memset(&dep, 0, sizeof dep);
    dep.srcSubpass = VK_SUBPASS_EXTERNAL;
    dep.dstSubpass = 0;
    dep.srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_EARLY_FRAGMENT_TESTS_BIT | VK_PIPELINE_STAGE_LATE_FRAGMENT_TESTS_BIT;
    dep.dstStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_EARLY_FRAGMENT_TESTS_BIT;
    dep.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
    memset(&ri, 0, sizeof ri);
    ri.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO;
    ri.attachmentCount = 2;
    ri.pAttachments = att;
    ri.subpassCount = 1;
    ri.pSubpasses = &sub;
    ri.dependencyCount = 1;
    ri.pDependencies = &dep;
    VKCHECK(vkCreateRenderPass_(vk.dev, &ri, NULL, &vk.pass), "vkCreateRenderPass");
    return 1;
}

static int make_pipeline(void)
{
    VkShaderModuleCreateInfo mi;
    VkShaderModule vs = VK_NULL_HANDLE, fs = VK_NULL_HANDLE;
    VkPipelineShaderStageCreateInfo stages[2];
    VkVertexInputBindingDescription bind = { 0, sizeof(dxt_vertex), VK_VERTEX_INPUT_RATE_VERTEX };
    VkVertexInputAttributeDescription attrs[3] = {
        { 0, 0, VK_FORMAT_R32G32B32_SFLOAT, 0 },
        { 1, 0, VK_FORMAT_R32G32_SFLOAT, 12 },
        { 2, 0, VK_FORMAT_R8G8B8A8_UNORM, 20 },
    };
    VkPipelineVertexInputStateCreateInfo vi;
    VkPipelineInputAssemblyStateCreateInfo ia;
    VkPipelineViewportStateCreateInfo vp;
    VkPipelineRasterizationStateCreateInfo rs;
    VkPipelineMultisampleStateCreateInfo ms;
    VkPipelineDepthStencilStateCreateInfo ds;
    VkPipelineColorBlendAttachmentState cba;
    VkPipelineColorBlendStateCreateInfo cb;
    VkDynamicState dyn_states[2] = { VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR };
    VkPipelineDynamicStateCreateInfo dyn;
    VkGraphicsPipelineCreateInfo pi;
    VkDescriptorSetLayoutBinding slb;
    VkDescriptorSetLayoutCreateInfo sli;
    VkPushConstantRange pcr = { VK_SHADER_STAGE_VERTEX_BIT, 0, sizeof(dxt_mat) };
    VkPipelineLayoutCreateInfo li;
    VkResult r;

    memset(&slb, 0, sizeof slb);
    slb.binding = 0;
    slb.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    slb.descriptorCount = 1;
    slb.stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT;
    memset(&sli, 0, sizeof sli);
    sli.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
    sli.bindingCount = 1;
    sli.pBindings = &slb;
    VKCHECK(vkCreateDescriptorSetLayout_(vk.dev, &sli, NULL, &vk.set_layout), "vkCreateDescriptorSetLayout");
    memset(&li, 0, sizeof li);
    li.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
    li.setLayoutCount = 1;
    li.pSetLayouts = &vk.set_layout;
    li.pushConstantRangeCount = 1;
    li.pPushConstantRanges = &pcr;
    VKCHECK(vkCreatePipelineLayout_(vk.dev, &li, NULL, &vk.layout), "vkCreatePipelineLayout");

    memset(&mi, 0, sizeof mi);
    mi.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
    mi.codeSize = sizeof vk_cube_vert;
    mi.pCode = vk_cube_vert;
    VKCHECK(vkCreateShaderModule_(vk.dev, &mi, NULL, &vs), "vkCreateShaderModule (vertex)");
    mi.codeSize = sizeof vk_cube_frag;
    mi.pCode = vk_cube_frag;
    VKCHECK(vkCreateShaderModule_(vk.dev, &mi, NULL, &fs), "vkCreateShaderModule (fragment)");
    memset(stages, 0, sizeof stages);
    stages[0].sType = stages[1].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    stages[0].stage = VK_SHADER_STAGE_VERTEX_BIT;
    stages[0].module = vs;
    stages[0].pName = "main";
    stages[1].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
    stages[1].module = fs;
    stages[1].pName = "main";

    memset(&vi, 0, sizeof vi);
    vi.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
    vi.vertexBindingDescriptionCount = 1;
    vi.pVertexBindingDescriptions = &bind;
    vi.vertexAttributeDescriptionCount = 3;
    vi.pVertexAttributeDescriptions = attrs;
    memset(&ia, 0, sizeof ia);
    ia.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
    ia.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    memset(&vp, 0, sizeof vp);
    vp.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
    vp.viewportCount = 1;
    vp.scissorCount = 1;
    memset(&rs, 0, sizeof rs);
    rs.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
    rs.polygonMode = VK_POLYGON_MODE_FILL;
    rs.cullMode = VK_CULL_MODE_NONE;
    rs.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
    rs.lineWidth = 1.0f;
    memset(&ms, 0, sizeof ms);
    ms.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
    ms.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
    memset(&ds, 0, sizeof ds);
    ds.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
    ds.depthTestEnable = VK_TRUE;
    ds.depthWriteEnable = VK_TRUE;
    ds.depthCompareOp = VK_COMPARE_OP_LESS;
    memset(&cba, 0, sizeof cba);
    cba.colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT;
    memset(&cb, 0, sizeof cb);
    cb.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
    cb.attachmentCount = 1;
    cb.pAttachments = &cba;
    memset(&dyn, 0, sizeof dyn);
    dyn.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
    dyn.dynamicStateCount = 2;
    dyn.pDynamicStates = dyn_states;
    memset(&pi, 0, sizeof pi);
    pi.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
    pi.stageCount = 2;
    pi.pStages = stages;
    pi.pVertexInputState = &vi;
    pi.pInputAssemblyState = &ia;
    pi.pViewportState = &vp;
    pi.pRasterizationState = &rs;
    pi.pMultisampleState = &ms;
    pi.pDepthStencilState = &ds;
    pi.pColorBlendState = &cb;
    pi.pDynamicState = &dyn;
    pi.layout = vk.layout;
    pi.renderPass = vk.pass;
    pi.subpass = 0;
    r = vkCreateGraphicsPipelines_(vk.dev, VK_NULL_HANDLE, 1, &pi, NULL, &vk.pipeline);
    vkDestroyShaderModule_(vk.dev, vs, NULL);
    vkDestroyShaderModule_(vk.dev, fs, NULL);
    VKCHECK(r, "vkCreateGraphicsPipelines");
    return 1;
}

/* The cube's vertex and index buffers (host-visible: written once), the texture through a staging buffer, its sampler and set. */
static int make_resources(void)
{
    dxt_vertex verts[24];
    unsigned short idx[36];
    unsigned texels[DXT_TEX * DXT_TEX];
    VkBuffer staging = VK_NULL_HANDLE;
    VkDeviceMemory staging_mem = VK_NULL_HANDLE;
    const VkMemoryPropertyFlags host = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    void *p;
    VkCommandBufferBeginInfo bi;
    VkBufferImageCopy copy;
    VkSubmitInfo submit;
    VkSamplerCreateInfo smp;
    VkDescriptorPoolSize ps = { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1 };
    VkDescriptorPoolCreateInfo dpi;
    VkDescriptorSetAllocateInfo dsa;
    VkDescriptorImageInfo dii;
    VkWriteDescriptorSet w;

    dxt_cube(verts, idx);
    if (!make_buffer(sizeof verts, VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, host, &vk.vbuf, &vk.vmem)) return 0;
    VKCHECK(vkMapMemory_(vk.dev, vk.vmem, 0, sizeof verts, 0, &p), "vkMapMemory (vertices)");
    memcpy(p, verts, sizeof verts);
    vkUnmapMemory_(vk.dev, vk.vmem);
    if (!make_buffer(sizeof idx, VK_BUFFER_USAGE_INDEX_BUFFER_BIT, host, &vk.ibuf, &vk.imem)) return 0;
    VKCHECK(vkMapMemory_(vk.dev, vk.imem, 0, sizeof idx, 0, &p), "vkMapMemory (indices)");
    memcpy(p, idx, sizeof idx);
    vkUnmapMemory_(vk.dev, vk.imem);
    if (!make_buffer(8, VK_BUFFER_USAGE_TRANSFER_DST_BIT, host, &vk.readbuf, &vk.readmem)) return 0;   /* two pixels of the read-back */

    /* the texture: the checkerboard (grey, so its bytes are the same in RGBA and BGRA) into a staging buffer, copied into the image */
    dxt_checker(texels);
    if (!make_buffer(sizeof texels, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, host, &staging, &staging_mem)) return 0;
    VKCHECK(vkMapMemory_(vk.dev, staging_mem, 0, sizeof texels, 0, &p), "vkMapMemory (texture)");
    memcpy(p, texels, sizeof texels);
    vkUnmapMemory_(vk.dev, staging_mem);
    if (!make_image(DXT_TEX, DXT_TEX, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
                    VK_IMAGE_ASPECT_COLOR_BIT, &vk.tex, &vk.tex_mem, &vk.tex_view))
        return 0;
    memset(&bi, 0, sizeof bi);
    bi.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VKCHECK(vkBeginCommandBuffer_(vk.cmd, &bi), "vkBeginCommandBuffer (upload)");
    barrier(vk.cmd, vk.tex, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            0, VK_ACCESS_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
    memset(&copy, 0, sizeof copy);
    copy.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
    copy.imageSubresource.layerCount = 1;
    copy.imageExtent.width = DXT_TEX;
    copy.imageExtent.height = DXT_TEX;
    copy.imageExtent.depth = 1;
    vkCmdCopyBufferToImage_(vk.cmd, staging, vk.tex, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    barrier(vk.cmd, vk.tex, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
            VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT);
    VKCHECK(vkEndCommandBuffer_(vk.cmd), "vkEndCommandBuffer (upload)");
    memset(&submit, 0, sizeof submit);
    submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    submit.commandBufferCount = 1;
    submit.pCommandBuffers = &vk.cmd;
    VKCHECK(vkQueueSubmit_(vk.queue, 1, &submit, VK_NULL_HANDLE), "vkQueueSubmit (upload)");
    VKCHECK(vkQueueWaitIdle_(vk.queue), "vkQueueWaitIdle (upload)");
    vkDestroyBuffer_(vk.dev, staging, NULL);
    vkFreeMemory_(vk.dev, staging_mem, NULL);

    memset(&smp, 0, sizeof smp);
    smp.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
    smp.magFilter = VK_FILTER_LINEAR;
    smp.minFilter = VK_FILTER_LINEAR;
    smp.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
    smp.addressModeU = smp.addressModeV = smp.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    smp.maxLod = 0.0f;
    VKCHECK(vkCreateSampler_(vk.dev, &smp, NULL, &vk.sampler), "vkCreateSampler");
    memset(&dpi, 0, sizeof dpi);
    dpi.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
    dpi.maxSets = 1;
    dpi.poolSizeCount = 1;
    dpi.pPoolSizes = &ps;
    VKCHECK(vkCreateDescriptorPool_(vk.dev, &dpi, NULL, &vk.dpool), "vkCreateDescriptorPool");
    memset(&dsa, 0, sizeof dsa);
    dsa.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
    dsa.descriptorPool = vk.dpool;
    dsa.descriptorSetCount = 1;
    dsa.pSetLayouts = &vk.set_layout;
    VKCHECK(vkAllocateDescriptorSets_(vk.dev, &dsa, &vk.set), "vkAllocateDescriptorSets");
    dii.sampler = vk.sampler;
    dii.imageView = vk.tex_view;
    dii.imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    memset(&w, 0, sizeof w);
    w.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    w.dstSet = vk.set;
    w.dstBinding = 0;
    w.descriptorCount = 1;
    w.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    w.pImageInfo = &dii;
    vkUpdateDescriptorSets_(vk.dev, 1, &w, 0, NULL);
    return 1;
}

/* ------------------------------------------------------------------ one frame */

/* Records the frame into vk.cmd: the cube into swapchain image `i`, and with `read` two pixels of it into vk.readbuf. */
static int record(uint32_t i, float t, int read)
{
    VkCommandBufferBeginInfo bi;
    VkClearValue clear[2];
    VkRenderPassBeginInfo rp;
    VkViewport viewport;
    VkRect2D scissor;
    VkDeviceSize offset = 0;
    dxt_mat proj = dxt_proj(), mvp;

    proj.m[5] = -proj.m[5];   /* Vulkan's clip space has y down; Direct3D's depth range (0 to 1) is Vulkan's already */
    mvp = dxt_mul(dxt_mul(dxt_world(t), dxt_view()), proj);
    VKCHECK(vkResetCommandBuffer_(vk.cmd, 0), "vkResetCommandBuffer");
    memset(&bi, 0, sizeof bi);
    bi.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VKCHECK(vkBeginCommandBuffer_(vk.cmd, &bi), "vkBeginCommandBuffer");
    memset(clear, 0, sizeof clear);
    clear[0].color.float32[0] = 15 / 255.0f;   /* the DirectX tests' clear colour, 0f121a */
    clear[0].color.float32[1] = 18 / 255.0f;
    clear[0].color.float32[2] = 26 / 255.0f;
    clear[0].color.float32[3] = 1.0f;
    clear[1].depthStencil.depth = 1.0f;
    memset(&rp, 0, sizeof rp);
    rp.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO;
    rp.renderPass = vk.pass;
    rp.framebuffer = vk.framebuffers[i];
    rp.renderArea.extent = vk.extent;
    rp.clearValueCount = 2;
    rp.pClearValues = clear;
    vkCmdBeginRenderPass_(vk.cmd, &rp, VK_SUBPASS_CONTENTS_INLINE);
    vkCmdBindPipeline_(vk.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, vk.pipeline);
    viewport.x = 0;
    viewport.y = 0;
    viewport.width = (float)vk.extent.width;
    viewport.height = (float)vk.extent.height;
    viewport.minDepth = 0.0f;
    viewport.maxDepth = 1.0f;
    vkCmdSetViewport_(vk.cmd, 0, 1, &viewport);
    scissor.offset.x = scissor.offset.y = 0;
    scissor.extent = vk.extent;
    vkCmdSetScissor_(vk.cmd, 0, 1, &scissor);
    vkCmdBindVertexBuffers_(vk.cmd, 0, 1, &vk.vbuf, &offset);
    vkCmdBindIndexBuffer_(vk.cmd, vk.ibuf, 0, VK_INDEX_TYPE_UINT16);
    vkCmdBindDescriptorSets_(vk.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, vk.layout, 0, 1, &vk.set, 0, NULL);
    vkCmdPushConstants_(vk.cmd, vk.layout, VK_SHADER_STAGE_VERTEX_BIT, 0, sizeof mvp, &mvp);
    vkCmdDrawIndexed_(vk.cmd, 36, 1, 0, 0, 0);
    vkCmdEndRenderPass_(vk.cmd);
    if (read) {
        VkBufferImageCopy regions[2];
        memset(regions, 0, sizeof regions);
        regions[0].imageSubresource.aspectMask = regions[1].imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        regions[0].imageSubresource.layerCount = regions[1].imageSubresource.layerCount = 1;
        regions[0].imageOffset.x = (int32_t)(vk.extent.width / 2);   /* the centre: the cube */
        regions[0].imageOffset.y = (int32_t)(vk.extent.height / 2);
        regions[1].bufferOffset = 4;
        regions[1].imageOffset.x = 4;                                /* a corner: the clear colour */
        regions[1].imageOffset.y = 4;
        regions[0].imageExtent.width = regions[1].imageExtent.width = 1;
        regions[0].imageExtent.height = regions[1].imageExtent.height = 1;
        regions[0].imageExtent.depth = regions[1].imageExtent.depth = 1;
        barrier(vk.cmd, vk.images[i], VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        vkCmdCopyImageToBuffer_(vk.cmd, vk.images[i], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, vk.readbuf, 2, regions);
        {   /* the copy's writes made visible to the host, which reads them once the fence has signalled */
            VkMemoryBarrier mb;
            memset(&mb, 0, sizeof mb);
            mb.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
            mb.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            mb.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
            vkCmdPipelineBarrier_(vk.cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
        }
        barrier(vk.cmd, vk.images[i], VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
                VK_ACCESS_TRANSFER_READ_BIT, 0, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    }
    VKCHECK(vkEndCommandBuffer_(vk.cmd), "vkEndCommandBuffer");
    return 1;
}

/* After a frame with the read-back has finished: the two pixels, as RRGGBB, and whether the cube is there. */
static void log_readback(void)
{
    unsigned char *p;
    unsigned px[2];
    int k;

    if (vkMapMemory_(vk.dev, vk.readmem, 0, 8, 0, (void **)&p) != VK_SUCCESS) { dxt_log(API ": read-back: vkMapMemory failed"); return; }
    for (k = 0; k < 2; k++) {
        const unsigned char *q = p + 4 * k;
        int bgra = vk.format.format == VK_FORMAT_B8G8R8A8_UNORM || vk.format.format == VK_FORMAT_B8G8R8A8_SRGB;
        unsigned r = bgra ? q[2] : q[0], g = q[1], b = bgra ? q[0] : q[2];
        px[k] = (r << 16) | (g << 8) | b;
    }
    vkUnmapMemory_(vk.dev, vk.readmem);
    dxt_log(API ": read-back: centre %06x, corner %06x (clear colour 0f121a): %s", px[0], px[1],
            px[0] != px[1] ? "the cube is in the picture" : "the centre is the clear colour (nothing drawn, or the read-back returns nothing)");
}

/* ------------------------------------------------------------------ the program */

static void cleanup(void)
{
    if (vk.dev) {
        vkDeviceWaitIdle_(vk.dev);
        destroy_swapchain();
        if (vk.pipeline) vkDestroyPipeline_(vk.dev, vk.pipeline, NULL);
        if (vk.layout) vkDestroyPipelineLayout_(vk.dev, vk.layout, NULL);
        if (vk.set_layout) vkDestroyDescriptorSetLayout_(vk.dev, vk.set_layout, NULL);
        if (vk.dpool) vkDestroyDescriptorPool_(vk.dev, vk.dpool, NULL);
        if (vk.sampler) vkDestroySampler_(vk.dev, vk.sampler, NULL);
        if (vk.tex_view) vkDestroyImageView_(vk.dev, vk.tex_view, NULL);
        if (vk.tex) vkDestroyImage_(vk.dev, vk.tex, NULL);
        if (vk.tex_mem) vkFreeMemory_(vk.dev, vk.tex_mem, NULL);
        if (vk.vbuf) vkDestroyBuffer_(vk.dev, vk.vbuf, NULL);
        if (vk.vmem) vkFreeMemory_(vk.dev, vk.vmem, NULL);
        if (vk.ibuf) vkDestroyBuffer_(vk.dev, vk.ibuf, NULL);
        if (vk.imem) vkFreeMemory_(vk.dev, vk.imem, NULL);
        if (vk.readbuf) vkDestroyBuffer_(vk.dev, vk.readbuf, NULL);
        if (vk.readmem) vkFreeMemory_(vk.dev, vk.readmem, NULL);
        if (vk.pass) vkDestroyRenderPass_(vk.dev, vk.pass, NULL);
        if (vk.acquired) vkDestroySemaphore_(vk.dev, vk.acquired, NULL);
        if (vk.done) vkDestroyFence_(vk.dev, vk.done, NULL);
        if (vk.pool) vkDestroyCommandPool_(vk.dev, vk.pool, NULL);
        vkDestroyDevice_(vk.dev, NULL);
    }
    if (vk.surface) vkDestroySurfaceKHR_(vk.instance, vk.surface, NULL);
    if (vk.instance) vkDestroyInstance_(vk.instance, NULL);
}

/* Everything up to the first frame; 1 when the cube can be drawn, 0 when the program has said why not. */
static int start(HWND hwnd, int vulkan_on)
{
    VkApplicationInfo app;
    VkInstanceCreateInfo ici;
    VkExtensionProperties ext[256];
    uint32_t n_ext = 256, n_gpu = 0, n_fam = 0, i;
    const char *inst_ext[3] = { "VK_KHR_surface", "VK_KHR_win32_surface", NULL };
    uint32_t n_inst_ext = 2;
    VkPhysicalDevice gpus[16];
    VkQueueFamilyProperties fams[32];
    VkWin32SurfaceCreateInfoKHR wsi;
    VkDeviceQueueCreateInfo qi;
    VkDeviceCreateInfo dci;
    const char *dev_ext[2] = { "VK_KHR_swapchain", NULL };
    uint32_t n_dev_ext = 1;
    float priority = 1.0f;
    VkCommandPoolCreateInfo cpi;
    VkCommandBufferAllocateInfo cba;
    VkSemaphoreCreateInfo sem;
    VkFenceCreateInfo fci;
    VkResult r;
    int made;
    static const VkFormat depths[] = { VK_FORMAT_D32_SFLOAT, VK_FORMAT_D24_UNORM_S8_UINT, VK_FORMAT_D32_SFLOAT_S8_UINT, VK_FORMAT_D16_UNORM };

    r = vkEnumerateInstanceExtensionProperties_(NULL, &n_ext, ext);
    if (r != VK_SUCCESS && r != VK_INCOMPLETE) n_ext = 0;
    if (!has_extension(ext, n_ext, "VK_KHR_win32_surface")) {
        if (!vulkan_on) { vulkan_off("vulkan-1.dll lists no VK_KHR_win32_surface"); return 0; }
        dxt_log(API ": vulkan-1.dll lists no VK_KHR_win32_surface (%u instance extensions); trying anyway", n_ext);
    }
    memset(&app, 0, sizeof app);
    app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    app.pApplicationName = "BCD Vulkan test";
    app.pEngineName = "BCD";
    app.apiVersion = VK_API_VERSION_1_0;
    memset(&ici, 0, sizeof ici);
    ici.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    ici.pApplicationInfo = &app;
    ici.enabledExtensionCount = n_inst_ext;
    ici.ppEnabledExtensionNames = inst_ext;
    r = vkCreateInstance_(&ici, NULL, &vk.instance);
    if (r == VK_ERROR_INCOMPATIBLE_DRIVER && has_extension(ext, n_ext, "VK_KHR_portability_enumeration")) {
        /* a driver that is only a portability implementation is listed only when the instance asks for it */
        inst_ext[n_inst_ext++] = "VK_KHR_portability_enumeration";
        ici.enabledExtensionCount = n_inst_ext;
        ici.flags = VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR;
        dxt_log(API ": vkCreateInstance returned VK_ERROR_INCOMPATIBLE_DRIVER; trying again with VK_KHR_portability_enumeration");
        r = vkCreateInstance_(&ici, NULL, &vk.instance);
    }
    if (r != VK_SUCCESS) {
        char m[200];
        vk.instance = VK_NULL_HANDLE;
        if (!vulkan_on && (r == VK_ERROR_INCOMPATIBLE_DRIVER || r == VK_ERROR_INITIALIZATION_FAILED || r == VK_ERROR_EXTENSION_NOT_PRESENT)) {
            snprintf(m, sizeof m, "vkCreateInstance returned %s", vk_result(r));
            vulkan_off(m);
            return 0;
        }
        snprintf(m, sizeof m, "vkCreateInstance failed (%s, %d)", vk_result(r), (int)r);
        dxt_fail(API, m);
        return 0;
    }
#define VK_LOAD(name) name##_ = (PFN_##name)(void (*)(void))vkGetInstanceProcAddr_(vk.instance, #name);
    VK_INSTANCE_FUNCS(VK_LOAD)
#undef VK_LOAD
#define VK_HAVE(name) if (!name##_) { dxt_fail(API, "vkGetInstanceProcAddr gave no " #name); return 0; }
    VK_INSTANCE_FUNCS(VK_HAVE)
#undef VK_HAVE

    n_gpu = 16;
    r = vkEnumeratePhysicalDevices_(vk.instance, &n_gpu, gpus);
    if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || !n_gpu) {
        if (!vulkan_on) { vulkan_off("the instance lists no GPU"); return 0; }
        dxt_fail(API, "vkEnumeratePhysicalDevices lists no GPU");
        return 0;
    }
    vk.gpu = gpus[0];
    vkGetPhysicalDeviceProperties_(vk.gpu, &vk.props);
    vkGetPhysicalDeviceMemoryProperties_(vk.gpu, &vk.mem);
    dxt_log(API ": GPU \"%s\" (%u of %u), Vulkan %u.%u.%u, driver version %#x, vendor %#x, device %#x, type %d", vk.props.deviceName, 1, n_gpu,
            VK_API_VERSION_MAJOR(vk.props.apiVersion), VK_API_VERSION_MINOR(vk.props.apiVersion), VK_API_VERSION_PATCH(vk.props.apiVersion),
            vk.props.driverVersion, vk.props.vendorID, vk.props.deviceID, (int)vk.props.deviceType);

    memset(&wsi, 0, sizeof wsi);
    wsi.sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR;
    wsi.hinstance = GetModuleHandleA(NULL);
    wsi.hwnd = hwnd;
    VKCHECK(vkCreateWin32SurfaceKHR_(vk.instance, &wsi, NULL, &vk.surface), "vkCreateWin32SurfaceKHR");

    n_fam = 32;
    vkGetPhysicalDeviceQueueFamilyProperties_(vk.gpu, &n_fam, fams);
    vk.family = UINT32_MAX;
    for (i = 0; i < n_fam && vk.family == UINT32_MAX; i++) {
        VkBool32 present = VK_FALSE;
        if (!(fams[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) continue;
        if (vkGetPhysicalDeviceSurfaceSupportKHR_(vk.gpu, i, vk.surface, &present) == VK_SUCCESS && present) vk.family = i;
    }
    if (vk.family == UINT32_MAX) { dxt_fail(API, "no queue family both draws and presents to the window"); return 0; }

    n_ext = 256;
    r = vkEnumerateDeviceExtensionProperties_(vk.gpu, NULL, &n_ext, ext);
    if (r != VK_SUCCESS && r != VK_INCOMPLETE) n_ext = 0;
    if (!has_extension(ext, n_ext, "VK_KHR_swapchain")) { dxt_fail(API, "the GPU has no VK_KHR_swapchain"); return 0; }
    if (has_extension(ext, n_ext, "VK_KHR_portability_subset")) dev_ext[n_dev_ext++] = "VK_KHR_portability_subset";   /* the spec asks for it when listed */
    memset(&qi, 0, sizeof qi);
    qi.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    qi.queueFamilyIndex = vk.family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &priority;
    memset(&dci, 0, sizeof dci);
    dci.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qi;
    dci.enabledExtensionCount = n_dev_ext;
    dci.ppEnabledExtensionNames = dev_ext;
    VKCHECK(vkCreateDevice_(vk.gpu, &dci, NULL, &vk.dev), "vkCreateDevice");
    vkGetDeviceQueue_(vk.dev, vk.family, 0, &vk.queue);
    dxt_log(API ": device made (queue family %u, %u device extensions listed%s)", vk.family, n_ext,
            n_dev_ext > 1 ? ", VK_KHR_portability_subset on" : "");

    vk.depth_format = VK_FORMAT_UNDEFINED;
    for (i = 0; i < sizeof depths / sizeof depths[0]; i++) {
        VkFormatProperties fp;
        vkGetPhysicalDeviceFormatProperties_(vk.gpu, depths[i], &fp);
        if (fp.optimalTilingFeatures & VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT) { vk.depth_format = depths[i]; break; }
    }
    if (vk.depth_format == VK_FORMAT_UNDEFINED) { dxt_fail(API, "no depth format the GPU can draw into"); return 0; }

    memset(&cpi, 0, sizeof cpi);
    cpi.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    cpi.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    cpi.queueFamilyIndex = vk.family;
    VKCHECK(vkCreateCommandPool_(vk.dev, &cpi, NULL, &vk.pool), "vkCreateCommandPool");
    memset(&cba, 0, sizeof cba);
    cba.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    cba.commandPool = vk.pool;
    cba.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cba.commandBufferCount = 1;
    VKCHECK(vkAllocateCommandBuffers_(vk.dev, &cba, &vk.cmd), "vkAllocateCommandBuffers");
    memset(&sem, 0, sizeof sem);
    sem.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
    VKCHECK(vkCreateSemaphore_(vk.dev, &sem, NULL, &vk.acquired), "vkCreateSemaphore");
    memset(&fci, 0, sizeof fci);
    fci.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    fci.flags = VK_FENCE_CREATE_SIGNALED_BIT;
    VKCHECK(vkCreateFence_(vk.dev, &fci, NULL, &vk.done), "vkCreateFence");

    /* the swapchain's format decides the render pass's; the pass is needed for the framebuffers: format first, then pass, then the rest */
    {
        VkSurfaceFormatKHR formats[64];
        uint32_t n = 64;
        r = vkGetPhysicalDeviceSurfaceFormatsKHR_(vk.gpu, vk.surface, &n, formats);
        if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || !n) { dxt_fail(API, "vkGetPhysicalDeviceSurfaceFormatsKHR gave no format"); return 0; }
        vk.format = formats[0];
        if (n == 1 && formats[0].format == VK_FORMAT_UNDEFINED) vk.format.format = VK_FORMAT_B8G8R8A8_UNORM;
        for (i = 0; i < n; i++)
            if (formats[i].format == VK_FORMAT_B8G8R8A8_UNORM || formats[i].format == VK_FORMAT_R8G8B8A8_UNORM) { vk.format = formats[i]; break; }
    }
    if (!make_render_pass()) return 0;
    made = make_swapchain(1);
    if (made == 2) { dxt_fail(API, "the window has no size"); return 0; }
    if (!made) return 0;
    if (!make_pipeline()) return 0;
    if (!make_resources()) return 0;
    return 1;
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    HMODULE dll;
    HWND hwnd;
    dxt_clock clock;
    unsigned failures = 0, presented = 0, suboptimal = 0;
    int reported = 0, reading = 0, vulkan_on;
    char title[96], verdict[16], lib[MAX_PATH];
    VkResult r;

    (void)inst; (void)prev; (void)cmd; (void)show;
    dxt_crash_log();
    dxt_log(API " (%d-bit): start, %dx%d", DXT_BITS, DXT_W, DXT_H);
    lib[0] = 0;
    GetEnvironmentVariableA("MADEIRA_VULKAN_LIBRARY", lib, sizeof lib);
    vulkan_on = lib[0] != 0;
    {
        const char *base = strrchr(lib, '/');
        dxt_log(API ": the session's Vulkan driver: %s", lib[0] ? (base ? base + 1 : lib) : "none (Vulkan is not on in this session)");
    }
    snprintf(title, sizeof title, "BCD Vulkan test (%d-bit)", DXT_BITS);
    dll = LoadLibraryA("vulkan-1.dll");
    vkGetInstanceProcAddr_ = dll ? (PFN_vkGetInstanceProcAddr)(void (*)(void))GetProcAddress(dll, "vkGetInstanceProcAddr") : NULL;
    if (!vkGetInstanceProcAddr_) {
        if (!vulkan_on) vulkan_off("there is no vulkan-1.dll");
        else dxt_fail(API, "vulkan-1.dll (vkGetInstanceProcAddr) is not available");
        return 1;
    }
#define VK_LOAD(name) name##_ = (PFN_##name)(void (*)(void))vkGetInstanceProcAddr_(NULL, #name);
    VK_GLOBAL_FUNCS(VK_LOAD)
#undef VK_LOAD
    if (!vkCreateInstance_ || !vkEnumerateInstanceExtensionProperties_) {
        if (!vulkan_on) vulkan_off("vulkan-1.dll gives no vkCreateInstance");
        else dxt_fail(API, "vulkan-1.dll gives no vkCreateInstance");
        return 1;
    }
    hwnd = dxt_window(title);
    if (!hwnd) { dxt_fail(API, "the window could not be created"); return 1; }
    if (!start(hwnd, vulkan_on)) { cleanup(); return 1; }

    dxt_clock_start(&clock, hwnd, title);
    dxt_log(API ": drawing");
    while (dxt_pump()) {
        uint32_t i = 0;
        VkPipelineStageFlags wait = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
        VkSubmitInfo submit;
        VkPresentInfoKHR present;
        int read;

        r = vkWaitForFences_(vk.dev, 1, &vk.done, VK_TRUE, UINT64_MAX);
        if (r != VK_SUCCESS) { if (failures++ < 3) dxt_log(API ": vkWaitForFences returned %s", vk_result(r)); Sleep(16); continue; }
        if (reading) {   /* the frame before had the read-back: it has finished */
            log_readback();
            reading = 0;
        }
        r = vkAcquireNextImageKHR_(vk.dev, vk.swapchain, UINT64_MAX, vk.acquired, VK_NULL_HANDLE, &i);
        if (r == VK_ERROR_OUT_OF_DATE_KHR) {
            if (!rebuild("vkAcquireNextImageKHR: out of date")) break;
            continue;
        }
        if (r != VK_SUCCESS && r != VK_SUBOPTIMAL_KHR) {
            if (failures++ < 3) dxt_log(API ": vkAcquireNextImageKHR returned %s", vk_result(r));
            Sleep(16);
            dxt_clock_frame(&clock);
            continue;
        }
        read = reported == 1 && vk.can_read;   /* the frame right after the verdict */
        if (!record(i, dxt_seconds(&clock), read)) break;
        vkResetFences_(vk.dev, 1, &vk.done);
        memset(&submit, 0, sizeof submit);
        submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        submit.waitSemaphoreCount = 1;
        submit.pWaitSemaphores = &vk.acquired;
        submit.pWaitDstStageMask = &wait;
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &vk.cmd;
        submit.signalSemaphoreCount = 1;
        submit.pSignalSemaphores = &vk.rendered[i];
        r = vkQueueSubmit_(vk.queue, 1, &submit, vk.done);
        if (r != VK_SUCCESS) {
            dxt_log(API ": vkQueueSubmit returned %s", vk_result(r));
            failures++;
            break;   /* the fence stays unsignalled, and a lost device does not come back */
        }
        if (read) reading = 1;
        if (reported == 1) {
            if (!vk.can_read) dxt_log(API ": read-back: the swapchain's images cannot be copied (no TRANSFER_SRC); not checked");
            reported = 2;
        }
        memset(&present, 0, sizeof present);
        present.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;
        present.waitSemaphoreCount = 1;
        present.pWaitSemaphores = &vk.rendered[i];
        present.swapchainCount = 1;
        present.pSwapchains = &vk.swapchain;
        present.pImageIndices = &i;
        r = vkQueuePresentKHR_(vk.queue, &present);
        if (r == VK_SUCCESS || r == VK_SUBOPTIMAL_KHR) {
            presented++;
        } else {
            if (failures++ < 3) dxt_log(API ": vkQueuePresentKHR returned %s", vk_result(r));
        }
        /* out of date: a new swapchain; suboptimal (it still presents): a new one the first three times, then the old one is kept, since
         * a driver may say so of every frame (a surface whose size or rotation it would rather have otherwise) */
        if (r == VK_ERROR_OUT_OF_DATE_KHR || (r == VK_SUBOPTIMAL_KHR && suboptimal++ < 3)) {
            vkDeviceWaitIdle_(vk.dev);
            if (reading) { log_readback(); reading = 0; }
            if (!rebuild(r == VK_SUBOPTIMAL_KHR ? "vkQueuePresentKHR: suboptimal" : "vkQueuePresentKHR: out of date")) break;
        }
        dxt_clock_frame(&clock);
        if (!reported && dxt_seconds(&clock) >= 2.0f) {   /* the verdict, once; the title keeps it */
            reported = 1;
            dxt_verdict(API, presented, failures, dxt_seconds(&clock), vk.props.deviceName, verdict, sizeof verdict);
            snprintf(clock.base, sizeof clock.base, "%s - %s", title, verdict);
        }
    }
    if (reported < 1) dxt_log(API ": the window was closed (or drawing stopped) before the verdict, after %u frames", clock.total);
    else dxt_log(API ": the window was closed after %u frames", clock.total);
    cleanup();
    return 0;
}
