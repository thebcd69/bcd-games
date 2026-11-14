#!/usr/bin/env python3
"""Check opt-in graphics wrappers and the linked ARM64EC entry-point ABI.

Without arguments, exercise the production switch and argument forwarding on
the host. --factory/--d3d12 also inspect the actual linked DLLs: x64 entries,
native targets, code-map classification and the factory's COM vtable slots.
"""
from pathlib import Path
import argparse
import importlib.util
import os
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
D3D12_METHODS = ('ExecuteCommandLists', 'GetTimestampFrequency', 'GetClockCalibration',
                 'GetDesc', 'Signal', 'Wait', 'Present', 'Present1', 'ResizeBuffers', 'ResizeBuffers1')
FACTORY_METHODS = {8: 'MakeWindowAssociation', 10: 'CreateSwapChain',
                   15: 'CreateSwapChainForHwnd', 16: 'CreateSwapChainForCoreWindow',
                   24: 'CreateSwapChainForComposition'}
SWAPCHAIN_METHODS = {8: 'Present', 13: 'ResizeBuffers', 22: 'Present1', 39: 'ResizeBuffers1'}


class PE:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        nt = self.u32(0x3c)
        assert self.data[nt:nt + 4] == b'PE\0\0', 'not a PE image'
        opt = nt + 24
        assert self.u16(opt) == 0x20b, 'not PE32+'
        self.base = self.u64(opt + 24)
        self.sections = []
        start = opt + self.u16(nt + 20)
        for i in range(self.u16(nt + 6)):
            p = start + 40 * i
            name = self.data[p:p + 8].split(b'\0')[0].decode()
            size, rva, rawsize, raw = struct.unpack_from('<IIII', self.data, p + 8)
            self.sections.append((name, rva, size, raw, rawsize, self.u32(p + 36)))
        self.symbols = {}
        sym, count = self.u32(nt + 12), self.u32(nt + 16)
        strings = sym + 18 * count
        i = 0
        while i < count:
            p = sym + 18 * i
            name = self.data[p:p + 8]
            if name[:4] == b'\0' * 4:
                q = strings + self.u32(p + 4)
                name = self.data[q:self.data.index(b'\0', q)]
            else:
                name = name.split(b'\0')[0]
            section = struct.unpack_from('<h', self.data, p + 12)[0]
            if 0 < section <= len(self.sections):
                self.symbols[name.decode()] = self.sections[section - 1][1] + self.u32(p + 8)
            i += 1 + self.data[p + 17]
        config = self.u32(opt + 112 + 10 * 8)
        meta = self.u64(self.offset(config) + 0xc8) - self.base
        m = struct.unpack_from('<20I', self.data, self.offset(meta))
        self.ranges = []
        for i in range(m[2]):
            lo, size = struct.unpack_from('<II', self.data, self.offset(m[1]) + 8 * i)
            self.ranges.append((lo & ~3, size, lo & 3))
        self.redirects = dict(struct.unpack_from('<II', self.data, self.offset(m[4]) + 8 * i)
                              for i in range(m[13]))

    def u16(self, p):
        return struct.unpack_from('<H', self.data, p)[0]

    def u32(self, p):
        return struct.unpack_from('<I', self.data, p)[0]

    def u64(self, p):
        return struct.unpack_from('<Q', self.data, p)[0]

    def offset(self, rva, length=1):
        for _, lo, _, raw, size, _ in self.sections:
            if lo <= rva and rva - lo + length <= size:
                return raw + rva - lo
        raise AssertionError(f'RVA {rva:#x}+{length:#x} has no file backing')

    def kind(self, rva):
        return next((kind for lo, size, kind in self.ranges if lo <= rva < lo + size), None)

    def entry(self, method, factory=False, owner=None):
        owner = owner or ('MTLDXGIHookFactory' if factory else None)
        names = [n for n in self.symbols if n.startswith('EXP+#') and
                 ((owner in n and str(len(method)) + method in n)
                  if owner else n == 'EXP+#mad_x64_' + method)]
        assert len(names) == 1, f'{method}: one generated x64 entry required, found {len(names)}'
        name = names[0]
        rva = self.symbols[name]
        section = next(s for s in self.sections if s[1] <= rva < s[1] + s[2])
        assert section[0] == '.hexpthk' and section[5] & 0x20000000, f'{method}: not executable .hexpthk'
        assert self.kind(rva) == 2, f'{method}: entry must be classified x64, not native'
        p = self.offset(rva, 14)
        assert self.data[p:p + 10] == bytes.fromhex('488bc448895820555de9'), f'{method}: invalid fast-forward entry'
        target = rva + 14 + struct.unpack_from('<i', self.data, p + 10)[0]
        native = self.symbols.get(name.removeprefix('EXP+') + '$hp_target')
        assert target == native and self.kind(target) == 1, f'{method}: wrong native ABI target'
        assert self.redirects.get(rva) == target, f'{method}: missing ARM64EC redirection metadata'
        print(f'PASS {method}: x64 entry {rva:#x}, native ABI target {target:#x}')
        return rva

    def factory_slots(self, entries):
        for cls, patchable in [('MTLDXGIHookFactory', True), ('MTLDXGIFactory', False)]:
            names = [n for n in self.symbols if n.startswith('_ZTV') and n.endswith(cls + 'E')]
            assert len(names) == 1, f'{cls}: vtable missing or ambiguous'
            table = self.symbols[names[0]] + 16  # Itanium ABI offset and RTTI header
            for slot, entry in entries.items():
                rva = self.u64(self.offset(table + slot * 8, 8)) - self.base
                if patchable:
                    assert rva == entry, f'{cls}: wrong entry in COM slot {slot}'
                else:
                    assert self.kind(rva) == 1, f'{cls}: default slot {slot} must remain native'
            # The instance clone also needs Factory7's last two COM methods and
            # both C++ destructor slots; truncating at Factory6 breaks Release.
            for slot, method in [(30, 'RegisterAdaptersChangedEvent'), (31, 'UnregisterAdaptersChangedEvent')]:
                rva = self.u64(self.offset(table + slot * 8, 8)) - self.base
                assert any(v == rva and str(len(method)) + method in n for n, v in self.symbols.items())
            for slot, destructor in [(32, 'D2Ev'), (33, 'D0Ev')]:
                rva = self.u64(self.offset(table + slot * 8, 8)) - self.base
                assert self.kind(rva) == 1 and any(v == rva and cls + destructor in n for n, v in self.symbols.items())
            assert self.u64(self.offset(table - 16, 8)) == 0, 'single-interface vtable required'
            print(f'PASS {cls}: COM slots {", ".join(map(str, entries))} preserve the selected ABI')

    def swapchain_slots(self):
        for flavor in (0, 1):
            owner = f'MTLD3D11HookSwapChainILb{flavor}EE'
            entries = {slot: self.entry(method, owner=owner) for slot, method in SWAPCHAIN_METHODS.items()}
            tables = {}
            for cls, patchable in [('MTLD3D11HookSwapChain', True), ('MTLD3D11SwapChain', False)]:
                ident = f'{cls}ILb{flavor}EE'
                names = [n for n in self.symbols if n == f'_ZTVN4dxmt{len(cls)}{ident}E']
                assert len(names) == 1, f'{ident}: vtable missing or ambiguous'
                table = self.symbols[names[0]] + 16
                assert self.u64(self.offset(table - 16, 8)) == 0, 'single-interface vtable required'
                values = [self.u64(self.offset(table + slot * 8, 8)) - self.base for slot in range(43)]
                for slot, rva in enumerate(values):
                    if patchable and slot in entries:
                        assert rva == entries[slot], f'{ident}: wrong entry in COM slot {slot}'
                    else:
                        assert self.kind(rva) == 1, f'{ident}: slot {slot} must remain native'
                for slot, destructor in [(41, 'D2Ev'), (42, 'D0Ev')]:
                    assert any(v == values[slot] and ident + destructor in n for n, v in self.symbols.items()), \
                        f'{ident}: destructor slot {slot} lost'
                tables[patchable] = values
            for slot in range(41):
                if slot not in entries:
                    assert tables[True][slot] == tables[False][slot], f'{owner}: unrelated COM slot {slot} changed'
            print(f'PASS D3D11 swapchain MetalFX={flavor}: 41 COM methods, two destructors, four typed x64 entries')


def host_checks():
    spec = importlib.util.spec_from_file_location('factory_patch', ROOT / 'tools/patch-dxgi-x64-entry.py')
    patcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patcher)
    original = (ROOT / 'dxmt/src/dxgi/dxgi_factory.cpp').read_text()
    patched = patcher.patch(original)
    assert patcher.patch(patched) == patched, 'factory patch not idempotent'
    if patched == original:
        # dxmt 128b297 (the 2026-10-15 upstream merge) carries the x64 entries
        # itself (willfaust/dxmt#29): the patch leaves the factory as it is.
        print('PASS factory patch: the dxmt pin carries the x64 entries; left as it is')
    else:
        try:
            patcher.patch(original.replace('new MTLDXGIFactory(Flags)', 'new OtherFactory(Flags)'))
        except ValueError:
            pass
        else:
            raise AssertionError('changed factory allocation was silently accepted')
        print('PASS factory patch: idempotent and rejects an incompatible source')

    native = (ROOT / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
    wrappers = []
    for method in D3D12_METHODS:
        at = native.index('mad_x64_' + method + '(')
        begin = native.rfind('MAD_X64_GRAPHICS_ENTRY', 0, at)
        end = native.index('\n}', at) + 2
        wrappers.append(native[begin:end])
    begin = native.index('    g_queue_vtbl.ExecuteCommandLists    = queue_ExecuteCommandLists;')
    end = native.index('\n\n    madeira_fill_ID3D12CommandAllocator', begin)
    queue_select = native[begin:end]
    code = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
typedef unsigned DWORD; typedef unsigned UINT; typedef int32_t HRESULT;
typedef uint64_t UINT64;
typedef int DXGI_FORMAT;
#define STDMETHODCALLTYPE
#define interface struct
#define EXTERN_C extern
#define IMAGE_DOS_SIGNATURE 0x5a4d
#define IMAGE_NT_SIGNATURE 0x4550
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS 4
#define GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT 2
typedef struct { unsigned short e_magic; int e_lfanew; unsigned char pad[4096]; } IMAGE_DOS_HEADER;
typedef struct { unsigned Signature; struct { unsigned Magic, SizeOfImage; } OptionalHeader; } IMAGE_NT_HEADERS;
typedef void *HMODULE; typedef const char *LPCSTR;
IMAGE_DOS_HEADER __ImageBase;
static unsigned module_lookups;
static int module_failure, module_null;
static uintptr_t module_base = UINT64_C(0x73abbc0000);
typedef struct obj { int unused; } IDXGISwapChain4, ID3D12CommandQueue, ID3D12CommandList, ID3D12Fence, IUnknown;
typedef struct params { int unused; } DXGI_PRESENT_PARAMETERS;
typedef struct { int Type, Priority; unsigned Flags, NodeMask; } D3D12_COMMAND_QUEUE_DESC;
static DWORD last_error = 123;
static const char *setting;
static DWORD GetLastError(void) { return last_error; }
static void SetLastError(DWORD value) { last_error = value; }
static DWORD GetEnvironmentVariableA(const char *name, char *out, DWORD cap) {
    assert(!strcmp(name, "MADEIRA_X64_GRAPHICS_ENTRY")); last_error = 999;
    if (!setting) return 0;
    DWORD n = strlen(setting); if (n >= cap) return n + 1;
    memcpy(out, setting, n + 1); return n;
}
static int GetModuleHandleExA(DWORD flags, LPCSTR address, HMODULE *out) {
    assert(flags == (GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT));
    assert(address == (LPCSTR)&__ImageBase); module_lookups++; last_error = 456;
    *out = module_null ? 0 : (HMODULE)module_base;
    return !module_failure;
}
#include "madeira_graphics_entry.h"
static IDXGISwapChain4 swap;
static ID3D12CommandQueue queue;
static ID3D12Fence fence;
static ID3D12CommandList *lists[2];
static DXGI_PRESENT_PARAMETERS params;
static UINT masks[2]; static IUnknown *queues[2];
static unsigned calls;
static void queue_ExecuteCommandLists(ID3D12CommandQueue *p, UINT n, ID3D12CommandList *const *l) {
    assert(p == &queue && n == 2 && l == lists); calls++;
}
static HRESULT queue_GetTimestampFrequency(ID3D12CommandQueue *p, UINT64 *f) {
    assert(p == &queue); calls++;
    if (!f) return (HRESULT)0x80070057;
    *f = UINT64_C(1000000000); return (HRESULT)0x887a0003;
}
static HRESULT queue_GetClockCalibration(ID3D12CommandQueue *p, UINT64 *g, UINT64 *c) {
    assert(p == &queue); calls++;
    if (g) *g = UINT64_C(0x1122334455667788);
    if (c) *c = UINT64_C(0xfedcba9876543210);
    return (HRESULT)0x887a0004;
}
static D3D12_COMMAND_QUEUE_DESC *queue_GetDesc(ID3D12CommandQueue *p, D3D12_COMMAND_QUEUE_DESC *out) {
    assert(p == &queue); calls++;
    *out = (D3D12_COMMAND_QUEUE_DESC){7, -1, 4, 32}; return out;
}
static HRESULT queue_Signal(ID3D12CommandQueue *p, ID3D12Fence *f, UINT64 v) {
    assert(p == &queue && v == UINT64_C(0xfedcba9876543210)); calls++;
    if (!f) return (HRESULT)0x80070057;
    assert(f == &fence); return (HRESULT)0x887a0020;
}
static HRESULT queue_Wait(ID3D12CommandQueue *p, ID3D12Fence *f, UINT64 v) {
    assert(p == &queue && v == UINT64_C(0x123456789abcdef0)); calls++;
    if (!f) return (HRESULT)0x80070057;
    assert(f == &fence); return (HRESULT)0x887a0021;
}
static void queue_UpdateTileMappings(void) {}
static void queue_CopyTileMappings(void) {}
static struct {
    void (*ExecuteCommandLists)(ID3D12CommandQueue *, UINT, ID3D12CommandList *const *);
    HRESULT (*GetTimestampFrequency)(ID3D12CommandQueue *, UINT64 *);
    HRESULT (*GetClockCalibration)(ID3D12CommandQueue *, UINT64 *, UINT64 *);
    D3D12_COMMAND_QUEUE_DESC *(*GetDesc)(ID3D12CommandQueue *, D3D12_COMMAND_QUEUE_DESC *);
    HRESULT (*Signal)(ID3D12CommandQueue *, ID3D12Fence *, UINT64);
    HRESULT (*Wait)(ID3D12CommandQueue *, ID3D12Fence *, UINT64);
    void (*UpdateTileMappings)(void), (*CopyTileMappings)(void);
} g_queue_vtbl;
static HRESULT swap_Present(IDXGISwapChain4 *p, UINT sync, UINT flags) {
    assert(p == &swap && sync == 3 && flags == 8); calls++; return (HRESULT)0x887a0001;
}
static HRESULT swap_Present1(IDXGISwapChain4 *p, UINT sync, UINT flags, const DXGI_PRESENT_PARAMETERS *q) {
    assert(q == &params); return swap_Present(p, sync, flags);
}
static HRESULT swap_ResizeBuffers(IDXGISwapChain4 *p, UINT n, UINT w, UINT h, DXGI_FORMAT f, UINT flags) {
    assert(p == &swap && n == 3 && w == 1920 && h == 1080 && f == 87 && flags == 0x800);
    calls++; return (HRESULT)0x887a0002;
}
static HRESULT swap_ResizeBuffers1(IDXGISwapChain4 *p, UINT n, UINT w, UINT h, DXGI_FORMAT f, UINT flags,
                                  const UINT *m, IUnknown *const *q) {
    assert(m == masks && q == queues); return swap_ResizeBuffers(p, n, w, h, f, flags);
}
''' + '\n'.join(wrappers) + '\nstatic void select_queue(void) {\n' + queue_select + '\n}\n' + r'''
int main(void) {
    UINT64 freq = 0, gpu = 0, cpu = 0;
    const char *values[] = {0, "", "0", "true", "on", "yes", "10", "1 ", "1"};
    for (unsigned i = 0; i < sizeof(values) / sizeof(values[0]); i++) {
        setting = values[i]; last_error = 123;
        assert(mad_x64_graphics_entry_enabled() == (i == 8)); assert(last_error == 123);
        select_queue(); assert(last_error == 123);
        assert(g_queue_vtbl.ExecuteCommandLists == (i == 8 ? mad_x64_ExecuteCommandLists : queue_ExecuteCommandLists));
        assert(g_queue_vtbl.GetTimestampFrequency == (i == 8 ? mad_x64_GetTimestampFrequency : queue_GetTimestampFrequency));
        assert(g_queue_vtbl.GetClockCalibration == (i == 8 ? mad_x64_GetClockCalibration : queue_GetClockCalibration));
        assert(g_queue_vtbl.GetDesc == (i == 8 ? mad_x64_GetDesc : queue_GetDesc));
        assert(g_queue_vtbl.Signal == (i == 8 ? mad_x64_Signal : queue_Signal));
        assert(g_queue_vtbl.Wait == (i == 8 ? mad_x64_Wait : queue_Wait));
    }
    mad_x64_ExecuteCommandLists(&queue, 2, lists);
    assert(mad_x64_Present(&swap, 3, 8) == (HRESULT)0x887a0001);
    assert(mad_x64_Present1(&swap, 3, 8, &params) == (HRESULT)0x887a0001);
    assert(mad_x64_ResizeBuffers(&swap, 3, 1920, 1080, 87, 0x800) == (HRESULT)0x887a0002);
    assert(mad_x64_ResizeBuffers1(&swap, 3, 1920, 1080, 87, 0x800, masks, queues) == (HRESULT)0x887a0002);
    assert(calls == 5);
    assert(g_queue_vtbl.GetTimestampFrequency(&queue, &freq) == (HRESULT)0x887a0003);
    assert(freq == UINT64_C(1000000000));
    assert(g_queue_vtbl.GetTimestampFrequency(&queue, 0) == (HRESULT)0x80070057);
    assert(g_queue_vtbl.GetClockCalibration(&queue, &gpu, &cpu) == (HRESULT)0x887a0004);
    assert(gpu == UINT64_C(0x1122334455667788) && cpu == UINT64_C(0xfedcba9876543210));
    gpu = cpu = 0;
    assert(g_queue_vtbl.GetClockCalibration(&queue, &gpu, 0) == (HRESULT)0x887a0004);
    assert(gpu == UINT64_C(0x1122334455667788) && cpu == 0);
    assert(g_queue_vtbl.GetClockCalibration(&queue, 0, &cpu) == (HRESULT)0x887a0004);
    assert(cpu == UINT64_C(0xfedcba9876543210));
    assert(g_queue_vtbl.GetClockCalibration(&queue, 0, 0) == (HRESULT)0x887a0004);
    assert(calls == 11);
    D3D12_COMMAND_QUEUE_DESC desc = {0};
    assert(g_queue_vtbl.GetDesc(&queue, &desc) == &desc);
    assert(desc.Type == 7 && desc.Priority == -1 && desc.Flags == 4 && desc.NodeMask == 32);
    assert(calls == 12);
    assert(g_queue_vtbl.Signal(&queue, &fence, UINT64_C(0xfedcba9876543210)) == (HRESULT)0x887a0020);
    assert(g_queue_vtbl.Wait(&queue, &fence, UINT64_C(0x123456789abcdef0)) == (HRESULT)0x887a0021);
    assert(g_queue_vtbl.Signal(&queue, 0, UINT64_C(0xfedcba9876543210)) == (HRESULT)0x80070057);
    assert(g_queue_vtbl.Wait(&queue, 0, UINT64_C(0x123456789abcdef0)) == (HRESULT)0x80070057);
    assert(calls == 16);

    /* One module-relative offset, two views: the native pool entry becomes a
     * PE entry without taking a module reference or losing LastError. */
    __ImageBase.e_magic = IMAGE_DOS_SIGNATURE; __ImageBase.e_lfanew = 256;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((char *)&__ImageBase + 256);
    nt->Signature = IMAGE_NT_SIGNATURE; nt->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    nt->OptionalHeader.SizeOfImage = sizeof __ImageBase;
    uintptr_t base = (uintptr_t)&__ImageBase;
    void *entry = (void *)(base + 0x300);
    last_error = 123;
    assert(mad_x64_graphics_entry_pe(entry, &__ImageBase) == (void *)(module_base + 0x300));
    assert(last_error == 123 && module_lookups == 1);
    module_failure = 1;
    assert(mad_x64_graphics_entry_pe(entry, &__ImageBase) == entry && last_error == 123);
    module_failure = 0; module_null = 1;
    assert(mad_x64_graphics_entry_pe(entry, &__ImageBase) == entry && last_error == 123);
    module_null = 0;
    assert(mad_x64_graphics_entry_pe((void *)(module_base + 0x300), &__ImageBase) == (void *)(module_base + 0x300));
    assert(mad_x64_graphics_entry_pe((void *)(base + sizeof __ImageBase), &__ImageBase) == (void *)(base + sizeof __ImageBase));
    assert(mad_x64_graphics_entry_pe((void *)(base - 1), &__ImageBase) == (void *)(base - 1));
    assert(module_lookups == 3 && last_error == 123);
    nt->Signature = 0;
    assert(mad_x64_graphics_entry_pe(entry, &__ImageBase) == entry && module_lookups == 3);
    nt->Signature = IMAGE_NT_SIGNATURE;

    /* Preserve RTTI, 32 COM slots and BOTH destructor pointers per instance.
     * Only the five selected x64 entries are made PE-relative. */
    void *original[36], *copy[36], *other[36];
    const unsigned selected[] = {8, 10, 15, 16, 24};
    for (unsigned i = 0; i < 36; i++) original[i] = (void *)(base + 0x300 + 8*i);
    mad_x64_graphics_factory_table(original + 2, copy, &__ImageBase);
    mad_x64_graphics_factory_table(original + 2, other, &__ImageBase);
    for (unsigned i = 0; i < 36; i++) {
        int mapped = 0;
        for (unsigned k = 0; k < 5; k++) if (i == 2 + selected[k]) mapped = 1;
        assert(copy[i] == (mapped ? (void *)(module_base + 0x300 + 8*i) : original[i]));
        assert(other[i] == copy[i]);
        assert(original[i] == (void *)(base + 0x300 + 8*i));
    }
    copy[10] = 0;
    assert(other[10] == (void *)(module_base + 0x300 + 80));  /* independent table storage */
    assert(last_error == 123);

    /* Reproduce the observed out-of-range E9 math; PE entry and trampoline
     * are in range, while replacing the entry with its pool alias is not. */
    int64_t pe = INT64_C(0x73abb44010), pool = INT64_C(0x163ad8010), trampoline = INT64_C(0x73a81d0fd5);
    int64_t near_delta = trampoline - (pe + 5), bad_delta = trampoline - (pool + 5);
    assert(near_delta >= INT32_MIN && near_delta <= INT32_MAX);
    assert(bad_delta < INT32_MIN || bad_delta > INT32_MAX);
    assert(pe + 5 + (int32_t)near_delta == trampoline);
    assert(pool + 5 + (int32_t)bad_delta == INT64_C(0x1a81d0fd5));
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = Path(tmp) / 'h.c', Path(tmp) / 'h'
        src.write_text(code)
        subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Werror', '-std=c11',
                        '-I' + str(ROOT / 'madeira-d3d12/src/pe'), str(src), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        # Exercise the actual C++ initializer and forwarding overrides with a
        # single-interface Itanium ABI fixture. Virtual Release must still
        # destroy each object through the copied destructor entries; RTTI and
        # the second instance must survive the first instance's replacement.
        cpp = code[:code.index('static IDXGISwapChain4 swap;')]
        cpp += r'''
#include <typeinfo>
typedef void *HWND;
struct IDXGISwapChain {}; struct IDXGISwapChain1 {}; struct IDXGIOutput {};
struct DXGI_SWAP_CHAIN_DESC {}; struct DXGI_SWAP_CHAIN_DESC1 {};
struct DXGI_SWAP_CHAIN_FULLSCREEN_DESC {};
static unsigned destroyed;
class IDXGIFactory7 {
public:
'''
        decls = re.findall(r'MAD_X64_GRAPHICS_ENTRY\s+(HRESULT STDMETHODCALLTYPE\s+(\w+)\([^;]*?\)) override;',
                           patcher.FACTORY)
        by_method = {name: decl for decl, name in decls}
        for slot in range(32):
            if slot == 2:
                cpp += 'virtual unsigned Release() { delete this; return 0; }\n'
            elif slot in FACTORY_METHODS:
                cpp += 'virtual ' + by_method[FACTORY_METHODS[slot]] + ' { return ' + str(1000 + slot) + '; }\n'
            else:
                cpp += f'virtual HRESULT Slot{slot}() {{ return {slot}; }}\n'
        cpp += r'''
virtual ~IDXGIFactory7() { destroyed++; }
};
class MTLDXGIFactory : public IDXGIFactory7 {
public:
  explicit MTLDXGIFactory(UINT) {}
};
''' + patcher.FACTORY + r'''
static void **table(IDXGIFactory7 *p) {
    void **vptr; __builtin_memcpy(&vptr, (void *)p, sizeof(vptr)); return vptr;
}
int main() {
    auto *one = new MTLDXGIHookFactory(0), *two = new MTLDXGIHookFactory(0);
    void *snapshot[36]; void **original = table(one);
    __builtin_memcpy(snapshot, original - 2, sizeof(snapshot));
    assert(table(two) == original);
    one->InitializeEntries(); two->InitializeEntries();
    IDXGIFactory7 *a = one, *b = two;
    assert(table(a) != original && table(b) != original && table(a) != table(b));
    assert(!memcmp(table(a) - 2, snapshot, sizeof(snapshot)));
    assert(!memcmp(table(b) - 2, snapshot, sizeof(snapshot)));
    assert(typeid(*a) == typeid(MTLDXGIHookFactory));
    assert(dynamic_cast<MTLDXGIHookFactory *>(a) == one);
    assert(a->MakeWindowAssociation(0, 0) == 1008);
    assert(a->CreateSwapChain(0, 0, 0) == 1010);
    assert(a->CreateSwapChainForHwnd(0, 0, 0, 0, 0, 0) == 1015);
    assert(a->CreateSwapChainForCoreWindow(0, 0, 0, 0, 0) == 1016);
    assert(a->CreateSwapChainForComposition(0, 0, 0, 0) == 1024);
    a->Release(); assert(destroyed == 1);
    assert(b->CreateSwapChain(0, 0, 0) == 1010);
    b->Release(); assert(destroyed == 2);
}
'''
        cxx_source, cxx_exe = Path(tmp) / 'factory.cpp', Path(tmp) / 'factory'
        cxx_source.write_text(cpp)
        subprocess.run([os.environ.get('CXX', 'c++'), '-O2', '-Wall', '-Werror',
                        '-Wno-unused-function', '-std=c++17',
                        '-I' + str(ROOT / 'madeira-d3d12/src/pe'), str(cxx_source), '-o', str(cxx_exe)], check=True)
        subprocess.run([str(cxx_exe)], check=True)
    print('PASS production switch: default off, exact 1, LastError preserved')
    print('PASS production queue vtable: native when off, all six typed entries when on')
    print('PASS production wrappers: all arguments and error HRESULTs preserved')
    print('PASS queue clock wrappers: 64-bit outputs and nullable arguments forwarded')
    print('PASS queue descriptor wrapper: explicit COM output buffer and returned pointer preserved')
    print('PASS queue fence wrappers: 64-bit Signal/Wait values, null fences and error HRESULTs preserved')
    print('PASS PE entry addresses: correct offset, unchanged on failed lookup, LastError/refcount preserved')
    print('PASS factory instance table: all native/RTTI/destructor slots preserved, five PE entries selected')
    print('PASS production C++ factory: typed virtual calls, RTTI and Release/destruction for two independent tables')
    print('PASS observed rel32 regression: PE/trampoline addresses fit; pool/trampoline addresses truncate')


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--factory')
    args.add_argument('--d3d12')
    args.add_argument('--d3d11')
    ns = args.parse_args()
    host_checks()
    if ns.factory:
        pe = PE(ns.factory)
        pe.factory_slots({slot: pe.entry(method, True) for slot, method in FACTORY_METHODS.items()})
    if ns.d3d12:
        pe = PE(ns.d3d12)
        for name in D3D12_METHODS:
            pe.entry(name)
    if ns.d3d11:
        PE(ns.d3d11).swapchain_slots()
