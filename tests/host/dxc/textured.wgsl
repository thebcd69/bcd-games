// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD minimal test shader (round 663), written for this test (NOT taken from any game): a textured quad with a uniform tint,
// the most ordinary thing a wgpu program draws. naga 30.0.1 (wgpu 30's shader translator) turns it into the HLSL that
// wgpu hands to D3DCompile (FXC, Shader Model 5.1) or to DXC (Shader Model 6.x).
struct Globals { tint: vec4<f32> };
@group(0) @binding(0) var<uniform> globals: Globals;
@group(1) @binding(0) var diffuse_tex: texture_2d<f32>;
@group(1) @binding(1) var diffuse_smp: sampler;

struct VsOut { @builtin(position) pos: vec4<f32>, @location(0) uv: vec2<f32> };

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> VsOut {
    var p = array<vec2<f32>, 3>(vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
    var o: VsOut;
    o.pos = vec4<f32>(p[i], 0.0, 1.0);
    o.uv = p[i] * 0.5 + vec2(0.5);
    return o;
}

@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    return textureSample(diffuse_tex, diffuse_smp, v.uv) * globals.tint;
}
