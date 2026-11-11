// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// A 64-bit InterlockedMax nobody reads (a visibility buffer's shape): the converter takes it, BCD's rewrite leaves it alone.
// dxc -T ps_6_6 -E main -Fo a64_unread_ps.dxil a64_unread_ps.hlsl
RWTexture2D<uint64_t> Vis : register(u0);
cbuffer Params : register(b0) { uint id; };
void main(float4 pos : SV_Position)
{
    InterlockedMax(Vis[uint2(pos.xy)], ((uint64_t)asuint(pos.z) << 32) | (uint64_t)id);
}
