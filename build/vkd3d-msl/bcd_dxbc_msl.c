// SPDX-License-Identifier: LGPL-2.1-or-later
// Copyright 2026 thebcd69 (BCD)
//
// BCD's front door to Wine's vkd3d-shader: one DXBC compute shader (cs_5_0) in, Metal Shading Language and the thread group out.
// Linked into libbcd-dxbc-msl.dylib with vkd3d-shader (build/vkd3d-msl/build.sh); this file and the dylib's two exported symbols
// are all BCD adds to it. Same bindings as host/xlate.c: constant buffers b0-15 -> descriptors[0-15], SRVs t0-31 -> [16-47],
// UAVs u0-31 -> [48-79], samplers s0-15 -> [80-95]; `descriptors` is an argument buffer of 8-byte entries at [[buffer(0)]].

#include <pthread.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <vkd3d_shader.h>

#define BCD_EXPORT __attribute__((visibility("default")))

#define BCD_TAG(a, b, c, d) ((uint32_t)(a) | ((uint32_t)(b) << 8) | ((uint32_t)(c) << 16) | ((uint32_t)(d) << 24))
#define BCD_OP_CUSTOMDATA 0x35u
#define BCD_OP_DCL_THREAD_GROUP 0x9bu

/* vkd3d prints its notes ("Generating a MSL shader. This is unsupported...", FIXMEs) to stderr by default; a log callback that
 * drops them keeps the app's log clean. What went wrong with a shader comes back in `*log` instead. */
static void bcd_drop_log(const char *format, va_list args)
{
    (void)format;
    (void)args;
}

static pthread_once_t bcd_init_once = PTHREAD_ONCE_INIT;

static void bcd_init(void)
{
    vkd3d_shader_set_log_callback(bcd_drop_log);
}

static uint32_t bcd_read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* The thread group of a shader chunk (SHDR / SHEX): version token, length token, then the opcode tokens; the first
 * dcl_thread_group (0x9b, three dwords after its opcode token) has it. Every read stays inside the chunk and inside the length
 * the chunk declares. Returns 1 when found. */
static int bcd_find_thread_group(const uint8_t *chunk, size_t chunk_size, unsigned tg[3])
{
    size_t dwords = chunk_size / 4, end, pos;

    if (dwords < 2)
        return 0;
    end = bcd_read_u32(chunk + 4);   /* the length token: the whole program, version and length tokens included */
    if (end > dwords)
        end = dwords;
    for (pos = 2; pos < end;)
    {
        uint32_t token = bcd_read_u32(chunk + pos * 4), opcode = token & 0x7ffu, length;

        if (opcode == BCD_OP_CUSTOMDATA)
        {
            if (pos + 1 >= end)
                return 0;
            length = bcd_read_u32(chunk + (pos + 1) * 4);   /* customdata: its length is the next dword */
        }
        else
            length = (token >> 24) & 0x7fu;
        if (!length || length > end - pos)   /* zero would never advance; past the end is a broken shader */
            return 0;
        if (opcode == BCD_OP_DCL_THREAD_GROUP)
        {
            if (length < 4)
                return 0;
            tg[0] = bcd_read_u32(chunk + (pos + 1) * 4);
            tg[1] = bcd_read_u32(chunk + (pos + 2) * 4);
            tg[2] = bcd_read_u32(chunk + (pos + 3) * 4);
            return 1;
        }
        pos += length;
    }
    return 0;
}

/* The thread group of a DXBC container. Returns 1 when found. */
static int bcd_dxbc_thread_group(const void *dxbc, size_t size, unsigned tg[3])
{
    struct vkd3d_shader_code code = {.code = dxbc, .size = size};
    struct vkd3d_shader_dxbc_desc desc;
    int found = 0;
    size_t i;

    /* the checksum was verified by the compile that came first */
    if (vkd3d_shader_parse_dxbc(&code, VKD3D_SHADER_PARSE_DXBC_IGNORE_CHECKSUM, &desc, NULL) < 0)
        return 0;
    for (i = 0; i < desc.section_count && !found; ++i)
    {
        const struct vkd3d_shader_dxbc_section_desc *section = &desc.sections[i];

        if (section->tag != BCD_TAG('S', 'H', 'E', 'X') && section->tag != BCD_TAG('S', 'H', 'D', 'R'))
            continue;
        found = bcd_find_thread_group(section->data.code, section->data.size, tg);
    }
    vkd3d_shader_free_dxbc(&desc);
    return found;
}

/* Direct3D 11's limits on a thread group: at least 1 and at most 1024, 1024 and 64 a side, at most 1024 threads together (Metal's
 * limit on Apple GPUs too). A caller dividing by these, or dispatching with them, can rely on it. */
static int bcd_thread_group_valid(const unsigned tg[3])
{
    return tg[0] >= 1 && tg[1] >= 1 && tg[2] >= 1 && tg[0] <= 1024 && tg[1] <= 1024 && tg[2] <= 64
            && (uint64_t)tg[0] * tg[1] * tg[2] <= 1024;
}

/* A malloc'd copy of text, for *log. */
static char *bcd_dup(const char *text)
{
    size_t n = strlen(text) + 1;
    char *copy = malloc(n);

    if (copy)
        memcpy(copy, text, n);
    return copy;
}

BCD_EXPORT int bcd_dxbc_to_msl(const void *dxbc, size_t size, char **msl, unsigned tg[3], char **log)
{
    static const enum vkd3d_shader_descriptor_type types[4] = {VKD3D_SHADER_DESCRIPTOR_TYPE_CBV, VKD3D_SHADER_DESCRIPTOR_TYPE_SRV,
            VKD3D_SHADER_DESCRIPTOR_TYPE_UAV, VKD3D_SHADER_DESCRIPTOR_TYPE_SAMPLER};
    static const unsigned base[4] = {0, 16, 48, 80}, count[4] = {16, 32, 32, 16};
    struct vkd3d_shader_resource_binding bindings[4];
    struct vkd3d_shader_interface_info interface_info;
    struct vkd3d_shader_compile_info info;
    struct vkd3d_shader_code out = {0};
    unsigned group[3] = {0, 0, 0};
    char *messages = NULL, *text;
    int result, i;

    if (msl)
        *msl = NULL;
    if (log)
        *log = NULL;
    if (tg)
        tg[0] = tg[1] = tg[2] = 0;
    if (!dxbc || !size)
    {
        if (log)
            *log = bcd_dup("no DXBC given");
        return VKD3D_ERROR_INVALID_ARGUMENT;
    }
    pthread_once(&bcd_init_once, bcd_init);

    memset(bindings, 0, sizeof(bindings));
    for (i = 0; i < 4; ++i)
    {
        bindings[i].type = types[i];
        bindings[i].register_space = 0;
        bindings[i].register_index = 0;
        bindings[i].shader_visibility = VKD3D_SHADER_VISIBILITY_ALL;
        bindings[i].flags = VKD3D_SHADER_BINDING_FLAG_BUFFER | VKD3D_SHADER_BINDING_FLAG_IMAGE;
        bindings[i].binding.set = 0;
        bindings[i].binding.binding = base[i];
        bindings[i].binding.count = count[i];
    }
    memset(&interface_info, 0, sizeof(interface_info));
    interface_info.type = VKD3D_SHADER_STRUCTURE_TYPE_INTERFACE_INFO;
    interface_info.bindings = bindings;
    interface_info.binding_count = 4;
    memset(&info, 0, sizeof(info));
    info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
    info.next = &interface_info;
    info.source.code = dxbc;
    info.source.size = size;
    info.source_type = VKD3D_SHADER_SOURCE_DXBC_TPF;
    info.target_type = VKD3D_SHADER_TARGET_MSL;
    info.log_level = VKD3D_SHADER_LOG_WARNING;

    result = vkd3d_shader_compile(&info, &out, &messages);
    if (messages && *messages && log)
        *log = bcd_dup(messages);
    if (messages)
        vkd3d_shader_free_messages(messages);
    if (result != VKD3D_OK)
        return result < 0 ? result : VKD3D_ERROR;
    if (!out.code || !out.size)
    {
        vkd3d_shader_free_shader_code(&out);
        return VKD3D_ERROR;
    }

    if (tg)
    {
        int found = bcd_dxbc_thread_group(dxbc, size, group);

        if (!found || !bcd_thread_group_valid(group))
        {
            char note[160];

            if (found)
                snprintf(note, sizeof(note), "the shader's thread group (%u, %u, %u) is outside Direct3D 11's limits",
                        group[0], group[1], group[2]);
            else
                snprintf(note, sizeof(note), "the shader declares no thread group (dcl_thread_group): not a compute shader");
            vkd3d_shader_free_shader_code(&out);
            if (log)
            {
                free(*log);
                *log = bcd_dup(note);
            }
            return VKD3D_ERROR_INVALID_SHADER;
        }
    }
    if (!(text = malloc(out.size + 1)))
    {
        vkd3d_shader_free_shader_code(&out);
        return VKD3D_ERROR_OUT_OF_MEMORY;
    }
    memcpy(text, out.code, out.size);
    text[out.size] = '\0';
    vkd3d_shader_free_shader_code(&out);

    if (msl)
        *msl = text;
    else
        free(text);
    if (tg)
    {
        tg[0] = group[0];
        tg[1] = group[1];
        tg[2] = group[2];
    }
    return VKD3D_OK;
}

BCD_EXPORT void bcd_dxbc_free(void *p)
{
    free(p);
}
