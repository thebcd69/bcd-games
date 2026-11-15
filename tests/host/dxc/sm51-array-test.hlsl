// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD minimal test shader (round 663), written for this test (NOT taken from any game): a Shader Model 5.1+ resource array indexed at run time,
// the pattern of naga's Direct3D 12 sampler heap (wgpu 30) that skate2-pc's shaders stop on (Send Log JQ0202, E5017).
SamplerState heap[2048] : register(s0, space0);
StructuredBuffer<uint> heapIndex : register(t0, space255);
Texture2D<float4> tex[4] : register(t1, space0);
float4 main(float4 pos : SV_Position, float2 uv : TEXCOORD0, nointerpolation uint i : BLENDINDICES0) : SV_Target
{
    return tex[i % 4].Sample(heap[heapIndex[0]], uv);
}
