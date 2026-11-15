/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */
/* BCD host test (tests/host/check-dxcompiler-optin.py; never shipped): D3DCompile from d3dcompiler_47.dll, the way wgpu 30.0.1's FXC path calls it
 * (D3DCOMPILE_ENABLE_STRICTNESS, no defines, no include handler). Usage: fxc-probe.exe <file.hlsl> <entry> <target> */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { void **vt; } Blob;
typedef HRESULT (WINAPI *D3DCompile_t)(const void *, SIZE_T, const char *, const void *, void *, const char *, const char *, UINT, UINT,
                                      Blob **, Blob **);
int main(int argc, char **argv)
{
    if (argc < 4) return 2;
    HMODULE lib = LoadLibraryA("d3dcompiler_47.dll");
    D3DCompile_t c = lib ? (D3DCompile_t)GetProcAddress(lib, "D3DCompile") : NULL;
    if (!c) { printf("fxc-probe: no D3DCompile\n"); return 3; }
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *src = malloc(n + 1); fread(src, 1, n, f); fclose(f); src[n] = 0;
    Blob *code = NULL, *err = NULL;
    HRESULT hr = c(src, n, argv[1], NULL, NULL, argv[2], argv[3], 1 << 11 /* D3DCOMPILE_ENABLE_STRICTNESS */, 0, &code, &err);
    printf("fxc-probe: D3DCompile(%s, %s) hr=%#lx\n", argv[2], argv[3], hr);
    if (err) printf("fxc-probe: messages:\n%.*s\n", (int)((SIZE_T (WINAPI *)(Blob *))err->vt[4])(err),
                    (const char *)((void *(WINAPI *)(Blob *))err->vt[3])(err));
    return FAILED(hr) ? 1 : 0;
}
