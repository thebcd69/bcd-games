/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */
/* What the stand-in Vulkan library of tests/host/check-vulkan-wow64.py saw (mock_icd.c), read by the harness. */
#ifndef MOCK_ICD_H
#define MOCK_ICD_H

#include <stdint.h>

#define MOCKVK_HOST   0   /* MoltenVK's set: VK_EXT_external_memory_host, no VK_EXT_map_memory_placed */
#define MOCKVK_PLACED 1   /* plus VK_EXT_map_memory_placed */
#define MOCKVK_NONE   2   /* neither: a 32-bit program's mapping cannot be placed */

#define MOCKVK_NAME 64
#define MOCKVK_EXTS 32
#define MOCKVK_NAMED 16
#define MOCKVK_DEVICE_NAME "Mock GPU (MoltenVK's memory model)"

struct mockvk_state
{
    /* instance */
    uint32_t instance_creates, instance_destroys;
    char app_name[MOCKVK_NAME], engine_name[MOCKVK_NAME];
    uint32_t instance_ext_count;
    char instance_exts[MOCKVK_EXTS][MOCKVK_NAME];
    /* device */
    uint32_t device_creates, device_destroys;
    uint32_t device_ext_count;
    char device_exts[MOCKVK_EXTS][MOCKVK_NAME];
    uint32_t device_queue_count, device_anisotropy, device_placed_feature;
    float device_queue_priority;
    uint64_t last_device;
    /* memory */
    uint32_t alloc_calls, free_calls, live_memories, imported_allocs, import_misaligned, host_pointer_queries;
    uint32_t last_alloc_type, last_alloc_imported, last_alloc_flags_seen;
    uint64_t last_alloc_size, last_import_pointer;
    uint32_t fail_next_alloc;      /* set by the harness: the next vkAllocateMemory fails */
    uint32_t map_calls, map2_calls, unmap_calls, placed_maps, reserve_unmaps;
    uint64_t last_map_result;
    /* buffers and commands */
    uint64_t last_buffer_size;
    uint32_t last_buffer_external;
    uint32_t pool_flags, pool_destroys, cmdbuf_frees, begin_flags, submits, copies_executed, wait_idles;
    uint64_t last_pool, cmdbuf_pool_seen, last_cmdbuf;
    /* images, fences, mapped ranges (the device probe's calls) */
    uint32_t image_creates, image_destroys, last_image_format, last_image_usage, last_image_external, image_memory_type;
    uint32_t image_barriers, buffer_barriers, last_barrier_new_layout;
    uint32_t fence_signals, fence_waits, fence_destroys, flushes, invalidates;
    uint64_t fence_timeout, range_size;
    /* surface */
    uint32_t surface_creates, surface_destroys;
    uint64_t surface_layer;
    /* debug */
    uint32_t named_count;
    uint32_t named_type[MOCKVK_NAMED];
    uint64_t named_object[MOCKVK_NAMED];
    char named_name[MOCKVK_NAMED][MOCKVK_NAME];
    uint64_t report_object;
    char report_message[MOCKVK_NAME];
    uint32_t utils_returns;
};

#endif
