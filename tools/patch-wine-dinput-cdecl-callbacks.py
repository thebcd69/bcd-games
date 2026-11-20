#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DirectInput's callbacks into the program restore the stack themselves (round 669, agent D S4; The Wolf Among Us 4VHH72, build 665).

A callback a program hands to DirectInput is declared CALLBACK (stdcall: the callee pops its arguments). Telltale's 32-bit games pass a cdecl
one to IDirectInput8A::EnumDevices; Wine's ANSI shim (enum_devices_wtoa_callback, dlls/dinput/ansi.c) called it directly and then cleaned its
own frame relative to ESP, so `ret 8` returned into the DIDEVICEINSTANCEW on the stack and the game jumped there (the log's "[entry-fail] ...
NoExec block"). Windows' DirectInput tolerates it, and Wine already has the cure for its W path: enum_callback_wrapper (dlls/dinput/dinput.c),
which calls through a frame (`leave`) and so restores ESP whatever the callee popped. This script routes every call of a program's callback in
dlls/dinput through that wrapper (and a five-argument twin for EnumDevicesBySemantics): ansi.c (4), device.c (12), dinput.c (1),
joystick_hid.c (1). With a stdcall callback the result is identical (the wrapper's `leave` restores the ESP `ret N` already restored); only a
cdecl callback, which crashes today, changes. Not on i386 the wrapper is a macro for the same direct call, so other builds compile the same code.
Keyless: a fix on a path that fails. Applied for the i386 farm (build-ipa.yml) and put back after it with the other farm-only files.
Idempotent (marker dinput-cdecl-callbacks-v1); fails by name when an anchor moved.
Usage: patch-wine-dinput-cdecl-callbacks.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: dinput-cdecl-callbacks-v1"

HEADER_ANCHOR = '#include "wine/list.h"\n'
HEADER_ADD = HEADER_ANCHOR + '''
/* madeira-bcd: dinput-cdecl-callbacks-v1 (tools/patch-wine-dinput-cdecl-callbacks.py, round 669): a program's callback is called through a
 * frame, so a cdecl callback cannot unbalance the stack (enum_callback_wrapper is defined in dinput.c). */
#if defined __i386__ && (defined __GNUC__ || defined _MSC_VER)
extern BOOL enum_callback_wrapper( void *callback, const void *instance, void *ref );
extern BOOL enum_callback_wrapper5( void *callback, const void *a, const void *b, DWORD c, DWORD d, void *ref );
#else
#define enum_callback_wrapper(callback, instance, ref) (callback)((instance), (ref))
#define enum_callback_wrapper5(callback, a, b, c, d, ref) (callback)((a), (b), (c), (d), (ref))
#endif
'''

WRAP_ANCHOR = '''#else
#define enum_callback_wrapper(callback, instance, ref) (callback)((instance), (ref))
#endif
'''
WRAP_ADD = WRAP_ANCHOR + '''
/* madeira-bcd: dinput-cdecl-callbacks-v1: the five-argument twin of enum_callback_wrapper, for EnumDevicesBySemantics' callback */
#if defined __i386__ && defined _MSC_VER
__declspec(naked) BOOL enum_callback_wrapper5(void *callback, const void *a, const void *b, DWORD c, DWORD d, void *ref)
{
    __asm
    {
        push ebp
        mov ebp, esp
        push [ebp+28]
        push [ebp+24]
        push [ebp+20]
        push [ebp+16]
        push [ebp+12]
        call [ebp+8]
        leave
        ret
    }
}
#elif defined __i386__ && defined __GNUC__
__ASM_GLOBAL_FUNC( enum_callback_wrapper5,
    "pushl %ebp\\n\\t"
    __ASM_CFI(".cfi_adjust_cfa_offset 4\\n\\t")
    __ASM_CFI(".cfi_rel_offset %ebp,0\\n\\t")
    "movl %esp,%ebp\\n\\t"
    __ASM_CFI(".cfi_def_cfa_register %ebp\\n\\t")
    "pushl 28(%ebp)\\n\\t"
    "pushl 24(%ebp)\\n\\t"
    "pushl 20(%ebp)\\n\\t"
    "pushl 16(%ebp)\\n\\t"
    "pushl 12(%ebp)\\n\\t"
    "call *8(%ebp)\\n\\t"
    "leave\\n\\t"
    __ASM_CFI(".cfi_def_cfa %esp,4\\n\\t")
    __ASM_CFI(".cfi_same_value %ebp\\n\\t")
    "ret" )
#endif
'''

# (file, old, new, count)
SITES = [
    ("dlls/dinput/ansi.c", "    return params->callback( &instance_a, params->ref );\n",
     "    return enum_callback_wrapper( params->callback, &instance_a, params->ref );   /* madeira-bcd: dinput-cdecl-callbacks-v1 */\n", 2),
    ("dlls/dinput/ansi.c", "    return params->callback( &info_a, params->ref );\n",
     "    return enum_callback_wrapper( params->callback, &info_a, params->ref );   /* madeira-bcd: dinput-cdecl-callbacks-v1 */\n", 1),
    ("dlls/dinput/ansi.c", "    return params->callback( &instance_a, iface_a, flags, remaining, params->ref );\n",
     "    return enum_callback_wrapper5( params->callback, &instance_a, iface_a, flags, remaining, params->ref );   /* madeira-bcd: dinput-cdecl-callbacks-v1 */\n", 1),
    ("dlls/dinput/device.c", "    return params->callback( instance, params->context ) ? DIENUM_CONTINUE : DIENUM_STOP;\n",
     "    return enum_callback_wrapper( params->callback, instance, params->context ) ? DIENUM_CONTINUE : DIENUM_STOP;   /* madeira-bcd: dinput-cdecl-callbacks-v1 */\n", 1),
    ("dlls/dinput/device.c", "if (hr == DI_OK && callback( &info, context ) == DIENUM_STOP) return DI_OK;",
     "if (hr == DI_OK && enum_callback_wrapper( callback, &info, context ) == DIENUM_STOP) return DI_OK;", 11),
    ("dlls/dinput/dinput.c", "            ret = callback( &object_params.instance, device, object_params.flags, params.device_count, context );\n",
     "            ret = enum_callback_wrapper5( callback, &object_params.instance, device, object_params.flags, params.device_count, context );   /* madeira-bcd: dinput-cdecl-callbacks-v1 */\n", 1),
    ("dlls/dinput/joystick_hid.c", "        if (callback( &effect->IDirectInputEffect_iface, context ) != DIENUM_CONTINUE) break;\n",
     "        if (enum_callback_wrapper( callback, &effect->IDirectInputEffect_iface, context ) != DIENUM_CONTINUE) break;   /* madeira-bcd: dinput-cdecl-callbacks-v1 */\n", 1),
]


def main():
    wine = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    header = wine / "dlls/dinput/dinput_private.h"
    if MARKER in header.read_text():
        print("dinput-cdecl-callbacks: already applied")
        return
    texts = {}
    for rel in ("dlls/dinput/dinput_private.h", "dlls/dinput/dinput.c", "dlls/dinput/ansi.c", "dlls/dinput/device.c", "dlls/dinput/joystick_hid.c"):
        texts[rel] = (wine / rel).read_text()
    checks = [("dlls/dinput/dinput_private.h", HEADER_ANCHOR, 1), ("dlls/dinput/dinput.c", WRAP_ANCHOR, 1)] + [(f, o, n) for f, o, _new, n in SITES]
    for rel, old, n in checks:
        if texts[rel].count(old) != n:
            sys.exit(f"patch-wine-dinput-cdecl-callbacks: an anchor in {rel} is not there {n} time(s): {old.strip()[:70]}")
    texts["dlls/dinput/dinput_private.h"] = texts["dlls/dinput/dinput_private.h"].replace(HEADER_ANCHOR, HEADER_ADD)
    texts["dlls/dinput/dinput.c"] = texts["dlls/dinput/dinput.c"].replace(WRAP_ANCHOR, WRAP_ADD)
    for rel, old, new, _n in SITES:
        texts[rel] = texts[rel].replace(old, new)
    for rel, text in texts.items():
        (wine / rel).write_text(text)
    print("dinput-cdecl-callbacks: applied (18 calls of a program's callback go through enum_callback_wrapper/5)")


if __name__ == "__main__":
    main()
