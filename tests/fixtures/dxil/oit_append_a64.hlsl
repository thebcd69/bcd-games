// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// A k-buffer insert of four layers, the shape of The Last of Us Part I's MainPsOitAppend (written for BCD's tests, no game
// code): each InterlockedMax on a RWTexture2D<uint64_t> hands the value it replaced to the next layer, so three of the four
// atomics have their result read. Compiled with DXC v1.9.2609: dxc -T ps_6_6 -E main -Fo oit_append_a64.dxil oit_append_a64.hlsl
RWTexture2D<uint64_t> L0 : register(u2, space4);
RWTexture2D<uint64_t> L1 : register(u3, space4);
RWTexture2D<uint64_t> L2 : register(u4, space4);
RWTexture2D<uint64_t> L3 : register(u5, space4);
cbuffer Params : register(b0) { uint layers; uint id; };

void main(float4 pos : SV_Position, float2 uv : TEXCOORD0)
{
    if (uv.x < 0.0f) discard;
    uint2 p = uint2(pos.xy);
    uint64_t v = ((uint64_t)asuint(pos.z) << 32) | (uint64_t)id;
    uint64_t old;
    if (layers > 0) { InterlockedMax(L0[p], v, old); v = min(old, v); if (v == 0) return; }
    if (layers > 1) { InterlockedMax(L1[p], v, old); v = min(old, v); if (v == 0) return; }
    if (layers > 2) { InterlockedMax(L2[p], v, old); v = min(old, v); if (v == 0) return; }
    if (layers > 3) InterlockedMax(L3[p], v);
}
