// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// A compute shader that reads a 64-bit texture atomic's result: no rasterizer order outside a pixel shader, so BCD's rewrite
// leaves it alone (the converter refuses it as before). dxc -T cs_6_6 -E main -Fo a64_read_cs.dxil a64_read_cs.hlsl
RWTexture2D<uint64_t> L0 : register(u0);
RWStructuredBuffer<uint64_t> Out : register(u1);
[numthreads(8, 8, 1)]
void main(uint3 t : SV_DispatchThreadID)
{
    uint64_t old;
    InterlockedMax(L0[t.xy], (uint64_t)t.x << 32 | t.y, old);
    Out[t.y * 8 + t.x] = old;
}
