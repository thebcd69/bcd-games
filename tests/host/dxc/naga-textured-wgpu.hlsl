struct Globals {
    float4 tint;
};

struct VsOut {
    float4 pos : SV_Position;
    float2 uv : LOC0;
};

cbuffer globals : register(b0) { Globals globals; }
Texture2D<float4> diffuse_tex : register(t0, space1);
SamplerState nagaSamplerHeap[2048]: register(s0, space0);
SamplerComparisonState nagaComparisonSamplerHeap[2048]: register(s2048, space0);
StructuredBuffer<uint> nagaGroup1SamplerIndexArray : register(t1, space255);
static const SamplerState diffuse_smp = nagaSamplerHeap[nagaGroup1SamplerIndexArray[1]];

struct VertexOutput_vs_main {
    float2 uv : LOC0;
    float4 pos : SV_Position;
};

struct FragmentInput_fs_main {
    float2 uv_1 : LOC0;
    float4 pos_1 : SV_Position;
};

typedef float2 ret_Constructarray3_float2_[3];
ret_Constructarray3_float2_ Constructarray3_float2_(float2 arg0, float2 arg1, float2 arg2) {
    float2 ret[3] = { arg0, arg1, arg2 };
    return ret;
}

VertexOutput_vs_main vs_main(uint i : SV_VertexID)
{
    float2 p[3] = Constructarray3_float2_(float2(-1.0, -1.0), float2(3.0, -1.0), float2(-1.0, 3.0));
    VsOut o = (VsOut)0;

    float2 _e15 = p[min(uint(i), 2u)];
    o.pos = float4(_e15, 0.0, 1.0);
    float2 _e21 = p[min(uint(i), 2u)];
    o.uv = ((_e21 * 0.5) + (0.5).xx);
    VsOut _e27 = o;
    const VsOut vsout = _e27;
    const VertexOutput_vs_main vsout_1 = { vsout.uv, vsout.pos };
    return vsout_1;
}

float4 fs_main(FragmentInput_fs_main fragmentinput_fs_main) : SV_Target0
{
    VsOut v = { fragmentinput_fs_main.pos_1, fragmentinput_fs_main.uv_1 };
    float4 _e4 = diffuse_tex.Sample(diffuse_smp, v.uv);
    float4 _e7 = globals.tint;
    return (_e4 * _e7);
}
