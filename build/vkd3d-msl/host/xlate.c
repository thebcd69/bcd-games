/* Host spike: DXBC (TPF) -> MSL with Wine's vkd3d-shader 1.19 and BCD's additions. Usage: xlate <in.dxbc> <out.metal>
 * (XL_ASM=1: D3D assembly instead). Bindings: CBV b0-15 -> descriptors[0-15], SRV t0-31 -> [16-47], UAV u0-31 -> [48-79],
 * sampler s0-15 -> [80-95]. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vkd3d_shader.h>
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *buf = malloc(n); fread(buf, 1, n, f); fclose(f);
    struct vkd3d_shader_resource_binding b[4];
    static const enum vkd3d_shader_descriptor_type types[4] = { VKD3D_SHADER_DESCRIPTOR_TYPE_CBV, VKD3D_SHADER_DESCRIPTOR_TYPE_SRV,
                                                                 VKD3D_SHADER_DESCRIPTOR_TYPE_UAV, VKD3D_SHADER_DESCRIPTOR_TYPE_SAMPLER };
    static const unsigned base[4] = { 0, 16, 48, 80 }, count[4] = { 16, 32, 32, 16 };
    for (int i = 0; i < 4; i++) {
        memset(&b[i], 0, sizeof b[i]);
        b[i].type = types[i]; b[i].register_space = 0; b[i].register_index = 0;
        b[i].shader_visibility = VKD3D_SHADER_VISIBILITY_ALL;
        b[i].flags = VKD3D_SHADER_BINDING_FLAG_BUFFER | VKD3D_SHADER_BINDING_FLAG_IMAGE;
        b[i].binding.set = 0; b[i].binding.binding = base[i]; b[i].binding.count = count[i];
    }
    struct vkd3d_shader_interface_info iface = { .type = VKD3D_SHADER_STRUCTURE_TYPE_INTERFACE_INFO, .bindings = b, .binding_count = 4 };
    struct vkd3d_shader_compile_info info = { .type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO, .next = &iface };
    struct vkd3d_shader_code out = {0}; char *msgs = NULL;
    info.source.code = buf; info.source.size = n;
    info.source_type = VKD3D_SHADER_SOURCE_DXBC_TPF;
    info.target_type = getenv("XL_ASM") ? VKD3D_SHADER_TARGET_D3D_ASM : VKD3D_SHADER_TARGET_MSL;
    info.log_level = VKD3D_SHADER_LOG_WARNING;
    int r = vkd3d_shader_compile(&info, &out, &msgs);
    if (msgs && *msgs) fprintf(stderr, "%s", msgs);
    if (r < 0) { fprintf(stderr, "vkd3d_shader_compile: %d\n", r); return 1; }
    f = fopen(argv[2], "wb"); fwrite(out.code, 1, out.size, f); fclose(f);
    return 0;
}
