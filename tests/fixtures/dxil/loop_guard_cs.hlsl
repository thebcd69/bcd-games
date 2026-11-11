// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The shape of a k-d tree walk with a stack (written for BCD's tests, no game code; The Last of Us Part I's
// CS_ComputeVolProbeBufferWriteCache walks its light probes this way): a node's left child is the next node, its right child is
// stored in it (0x1FFFFFFF for none). A sound tree ends; a node whose right child points back never does. Three loops, all of
// them ending on a conditional back edge in DXC's output (the last one is written with a break): dxil-loop-guard caps all three.
// Compiled with DXC v1.9.2609: dxc -T cs_6_0 -E main -Fo loop_guard_cs.dxil loop_guard_cs.hlsl
struct Node { float split; uint packed; };
StructuredBuffer<Node> Tree : register(t1);
RWStructuredBuffer<uint> Out : register(u0);
cbuffer Params : register(b0) { uint volumes; uint roots[4]; float3 pos; uint steps; };

[numthreads(64, 1, 1)]
void main(uint3 t : SV_DispatchThreadID)
{
    uint found = 0;
    for (uint v = 0; v < volumes; v++) {
        uint stack[32];
        uint count = 1;
        stack[0] = roots[v & 3];
        do {
            uint node = stack[--count];
            Node n = Tree[node];
            uint axis = n.packed & 3;
            if (axis == 3) { found++; continue; }
            uint right = n.packed >> 3;
            float d = pos[axis] - n.split;
            if (right != 0x1FFFFFFF && count < 31) stack[count++] = right;
            if (count < 31) stack[count++] = node + 1;
            if (d > 100.0f) found++;
        } while (count > 0);
    }
    uint i = 0;
    while (true) {
        Out[t.x * 8 + i] = found + i;
        i++;
        if (i >= steps) break;
    }
}
