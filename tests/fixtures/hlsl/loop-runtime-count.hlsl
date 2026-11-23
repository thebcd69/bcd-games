// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// A Direct3D 9 pixel shader (ps_3_0, entry main), written for BCD's tests (no game code), for the shape that Wine's HLSL compiler cannot
// write at Shader Model 3: a loop whose trip count is computed at run time from a texture sample (it is at most 39 here), with four
// shadow-map taps (tex2Dlod, one texldl each) in its body. tests/host/check-vkd3d-loop-fallback.py compiles it.

uniform float4x4 m_shadow;
uniform float4 density;

sampler2D s_smap : register(s0);
sampler2D s_position : register(s1);

float tap(float4 tc, float2 shift)
{
    float4 t = tc / tc.w;
    return tex2Dlod(s_smap, float4(t.xy + shift * 0.0003, t.z, 0)).x;
}

float4 main(float2 tc : TEXCOORD0) : COLOR
{
    float3 P = tex2D(s_position, tc).xyz;
    float3 direction = P / 40;
    float deltaDepth = direction.z;
    float4 current = mul(m_shadow, float4(P, 1));
    float4 delta = mul(m_shadow, float4(direction, 0));
    float res = 0;

    int n = (int)((P.z - 0.3) / deltaDepth);
    for (int i = 0; i < n; ++i)
    {
        res += density.x * (tap(current, float2(-1, -1)) + tap(current, float2(1, -1))
                + tap(current, float2(-1, 1)) + tap(current, float2(1, 1))) * 0.25;
        current -= delta;
    }
    return res * density;
}
