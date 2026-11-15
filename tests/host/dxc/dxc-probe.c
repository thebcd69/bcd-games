/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */
/* BCD host test (tests/host/check-dxcompiler-optin.py; never shipped): load dxcompiler.dll exactly the way wgpu 30.0.1's DX12 back end does
 * (wgpu-hal/src/dx12/shader_compilation.rs: LoadLibrary "dxcompiler.dll", DxcCreateInstance(CLSID_DxcCompiler, IID_IDxcCompiler3),
 * QueryInterface IDxcVersionInfo, IDxcCompiler3::Compile with "-E ep -T stage -HV 2018 -no-warnings -Ges", GetOutput(DXC_OUT_ERRORS,
 * IDxcBlobUtf8), GetOutput(DXC_OUT_OBJECT, IDxcBlob)) through raw vtable slots, as windows-rs does, and write the object.
 * Usage: dxc-probe.exe <dll> <file.hlsl> <entry> <target> <out.dxil> [extra args...] */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef HRESULT (WINAPI *DxcCreateInstanceProc)(REFCLSID, REFIID, void **);
static const GUID CLSID_DxcCompiler_ = {0x73e22d93, 0xe6ce, 0x47f3, {0xb5, 0xbf, 0xf0, 0x66, 0x4f, 0x39, 0xc1, 0xb0}};
static const GUID IID_IDxcCompiler3_ = {0x228b4687, 0x5a6a, 0x4730, {0x90, 0x0c, 0x97, 0x02, 0xb2, 0x20, 0x3f, 0x54}};
static const GUID IID_IDxcVersionInfo_ = {0xb04f5b50, 0x2059, 0x4f12, {0xa8, 0xff, 0xa1, 0xe0, 0xcd, 0xe1, 0xcc, 0x7e}};
static const GUID IID_IDxcResult_ = {0x58346cda, 0xdde7, 0x4497, {0x94, 0x61, 0x6f, 0x87, 0xaf, 0x5e, 0x06, 0x59}};
static const GUID IID_IDxcBlob_ = {0x8ba5fb08, 0x5195, 0x40e2, {0xac, 0x58, 0x0d, 0x98, 0x9c, 0x3a, 0x01, 0x02}};
static const GUID IID_IDxcBlobUtf8_ = {0x3da636c9, 0xba71, 0x4024, {0xa3, 0x01, 0x30, 0xcb, 0xf1, 0x25, 0x30, 0x5b}};

typedef struct { const void *Ptr; SIZE_T Size; UINT Encoding; } DxcBuffer_;
typedef void **VT;   /* an object is a pointer to its vtable */
#define SLOT(obj, n) (((void **)*(VT)(obj))[n])
typedef HRESULT (WINAPI *QI_t)(void *, REFIID, void **);
typedef ULONG (WINAPI *Rel_t)(void *);
typedef HRESULT (WINAPI *Compile_t)(void *, const DxcBuffer_ *, LPCWSTR *, UINT32, void *, REFIID, void **);
typedef HRESULT (WINAPI *GetVersion_t)(void *, UINT32 *, UINT32 *);
typedef HRESULT (WINAPI *GetStatus_t)(void *, HRESULT *);
typedef HRESULT (WINAPI *GetOutput_t)(void *, int, REFIID, void **, void **);
typedef void *(WINAPI *GetBufferPointer_t)(void *);
typedef SIZE_T (WINAPI *GetBufferSize_t)(void *);
typedef const char *(WINAPI *GetStringPointer_t)(void *);
typedef SIZE_T (WINAPI *GetStringLength_t)(void *);

static WCHAR *widen(const char *s)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    WCHAR *w = malloc(n * sizeof(WCHAR));
    MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}

int main(int argc, char **argv)
{
    if (argc < 6) { fprintf(stderr, "usage: dxc-probe <dll> <file.hlsl> <entry> <target> <out.dxil> [args...]\n"); return 2; }
    HMODULE lib = LoadLibraryA(argv[1]);
    if (!lib) { printf("probe: LoadLibrary(%s) failed %lu\n", argv[1], GetLastError()); return 3; }
    DxcCreateInstanceProc create = (DxcCreateInstanceProc)GetProcAddress(lib, "DxcCreateInstance");
    printf("probe: loaded %s, DxcCreateInstance=%p DxcCreateInstance2=%p\n", argv[1], (void *)create,
           (void *)GetProcAddress(lib, "DxcCreateInstance2"));
    if (!create) return 4;
    void *compiler = NULL;
    HRESULT hr = create(&CLSID_DxcCompiler_, &IID_IDxcCompiler3_, &compiler);
    printf("probe: DxcCreateInstance(CLSID_DxcCompiler, IID_IDxcCompiler3) hr=%#lx obj=%p\n", hr, compiler);
    if (FAILED(hr) || !compiler) return 5;
    void *vinfo = NULL;
    hr = ((QI_t)SLOT(compiler, 0))(compiler, &IID_IDxcVersionInfo_, &vinfo);
    if (SUCCEEDED(hr) && vinfo) {
        UINT32 major = 0, minor = 0;
        hr = ((GetVersion_t)SLOT(vinfo, 3))(vinfo, &major, &minor);
        printf("probe: IDxcVersionInfo::GetVersion hr=%#lx -> %u.%u\n", hr, major, minor);
        ((Rel_t)SLOT(vinfo, 2))(vinfo);
    } else printf("probe: no IDxcVersionInfo (hr=%#lx)\n", hr);

    FILE *f = fopen(argv[2], "rb");
    if (!f) { printf("probe: cannot open %s\n", argv[2]); return 6; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *src = malloc(n + 1); fread(src, 1, n, f); fclose(f); src[n] = 0;

    LPCWSTR args[32]; UINT32 na = 0;
    args[na++] = widen(argv[2]);            /* wgpu passes the source name first when it has one */
    args[na++] = L"-E"; args[na++] = widen(argv[3]);
    args[na++] = L"-T"; args[na++] = widen(argv[4]);
    args[na++] = L"-HV"; args[na++] = L"2018";
    args[na++] = L"-no-warnings";
    args[na++] = L"-Ges";                   /* DXC_ARG_ENABLE_STRICTNESS */
    for (int i = 6; i < argc && na < 32; i++) args[na++] = widen(argv[i]);
    DxcBuffer_ buf = { src, (SIZE_T)n, 65001 /* DXC_CP_UTF8 */ };
    void *result = NULL;
    DWORD t0 = GetTickCount();
    hr = ((Compile_t)SLOT(compiler, 3))(compiler, &buf, args, na, NULL, &IID_IDxcResult_, &result);
    printf("probe: IDxcCompiler3::Compile hr=%#lx result=%p (%lu ms)\n", hr, result, GetTickCount() - t0);
    if (FAILED(hr) || !result) return 7;
    HRESULT status = 0;
    ((GetStatus_t)SLOT(result, 3))(result, &status);
    void *errs = NULL;
    hr = ((GetOutput_t)SLOT(result, 7))(result, 2 /* DXC_OUT_ERRORS */, &IID_IDxcBlobUtf8_, &errs, NULL);
    SIZE_T elen = errs ? ((GetStringLength_t)SLOT(errs, 7))(errs) : 0;
    printf("probe: status=%#lx errors hr=%#lx length=%Iu\n", status, hr, elen);
    if (elen) { printf("probe: errors:\n%.*s\n", (int)elen, ((GetStringPointer_t)SLOT(errs, 6))(errs)); }
    void *obj = NULL;
    hr = ((GetOutput_t)SLOT(result, 7))(result, 1 /* DXC_OUT_OBJECT */, &IID_IDxcBlob_, &obj, NULL);
    if (FAILED(hr) || !obj) { printf("probe: no object (hr=%#lx)\n", hr); return elen ? 8 : 9; }
    const unsigned char *p = ((GetBufferPointer_t)SLOT(obj, 3))(obj);
    SIZE_T size = ((GetBufferSize_t)SLOT(obj, 4))(obj);
    int zero = 1; for (int i = 4; i < 20 && size >= 20; i++) if (p[i]) zero = 0;
    printf("probe: object %Iu bytes, magic %.4s, digest %s\n", size, (const char *)p, zero ? "ZERO (unsigned)" : "set (hashed)");
    FILE *o = fopen(argv[5], "wb"); fwrite(p, 1, size, o); fclose(o);
    ((Rel_t)SLOT(obj, 2))(obj);
    if (errs) ((Rel_t)SLOT(errs, 2))(errs);
    ((Rel_t)SLOT(result, 2))(result);
    ((Rel_t)SLOT(compiler, 2))(compiler);
    printf("probe: done\n");
    return 0;
}
