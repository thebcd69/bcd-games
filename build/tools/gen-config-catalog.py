#!/usr/bin/env python3
"""Generate app/Madeira/ConfigCatalog.generated.swift: every option Madeira reads.

Two kinds of option live in Documents/bcd.cfg:
  - plain keys ("vram-mb = 4352"), read through build/madeira_cfg.h
    (madeira_cfg_get/int/bool), the D3D12 runtime (mad_cfg_int_pe/str_pe) or
    the app (MadeiraConfig.get/bool/flag);
  - environment switches ("env.NAME = value"), which the app exports before
    Wine starts; any MADEIRA_*/DXMT_*/MYTHIC_* name the code reads with getenv,
    GetEnvironmentVariable, env::getEnvVar, madeiraSwitch, ios_srv_switch or a flag helper.

The scan covers the tracked sources of this repository and of the wine, DXMT
and FEX submodules. Each option gets its type and default from the reading
call, a subsystem from the file it is read in, and the comment next to the
first read. OVERLAY adds titles and fixed choices for the options that have a
dedicated place in Settings.

    build/tools/gen-config-catalog.py           rewrite the Swift catalog
    build/tools/gen-config-catalog.py --check   exit 1 if it is out of date
"""
import collections, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bcd_brand import display  # the app's name in the notes people read is BCD

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = os.path.join(ROOT, "app", "Madeira", "ConfigCatalog.generated.swift")
REPOS = {
    ".": ["build", "app", "madeira-d3d12/src", "madeira-dock/src"],
    "dxmt": ["src"],
    "wine": ["dlls", "server"],
    "FEX": ["Source", "FEXCore/Source"],
}
EXT = (".c", ".h", ".m", ".mm", ".cpp", ".hpp", ".cc", ".swift")
SKIP = ("/host-tests/", "/ffmpeg/", "ConfigCatalog.generated.swift")

CFG = re.compile(r'\b(madeira_cfg_(?:get|int|bool)|mad_cfg_(?:int|str)_pe|'
                 r'MadeiraConfig\.(?:get|bool|flag))\(\s*"([A-Za-z0-9._-]+)"\s*(?:,\s*([^,)]+))?')
ENV = re.compile(r'\b(getenv|GetEnvironmentVariable[AW]?|env::getEnvVar|madeiraSwitch|'
                 r'madeira_switch_for_caller|mad_env_is_zero|flag|envFlag|ios_srv_switch|sessionValue)\(\s*L?"((?:MADEIRA|DXMT|MYTHIC)_[A-Z0-9_]+)"'
                 r'\s*(?:,\s*(?:fallback:|default:)?\s*([^,)]+))?')
BOOL_READERS = {"madeira_cfg_bool", "MadeiraConfig.bool", "MadeiraConfig.flag", "flag", "envFlag",
                "madeiraSwitch", "madeira_switch_for_caller", "mad_env_is_zero"}
INT_READERS = {"madeira_cfg_int", "mad_cfg_int_pe"}

# Titles, kinds and fixed choices for options with a dedicated Settings row.
# "choices" are (value, label); the empty value means "remove the key".
OVERLAY = {
    "env.MADEIRA_PIN_GRAPHICS_DLLS": {"category": "Memory & JIT pool", "title": "Keep graphics DLLs loaded",
                "kind": "bool", "default": "0",
                "note": "Default off. On a D3D12 device/probe call, pins already loaded D3D12, DXGI and Wine Metal "
                        "DLLs until normal process shutdown, preventing repeated unload/reload copies. Keeps DLL "
                        "data live; does not recycle executable ranges or change GPU capabilities. Restart the "
                        "session after changing it.",
                "sources": ["madeira-d3d12/src/pe/madeira_d3d12.c"]},
    "env.MADEIRA_GDI_SHARED_SECTION": {"category": "Windows, display & input",
                "title": "GDI table 32-bit programs can reach", "kind": "text", "default": "",
                "note": "Empty: the GDI handle table is a section every 32-bit program can map in its own memory "
                        "window when the session is armed for 32-bit code as it starts (a 32-bit game), and always "
                        "in BCD Dock and in the library's Desktop (build 580). 1 forces it: for a 64-bit launcher "
                        "or a game started in a Wine desktop that later starts a 32-bit program, whose gdi32 "
                        "otherwise faults on its first GDI handle (logged as [gdi-shared] 32-bit program in a "
                        "session whose GDI table is private). 0 keeps the private table, in the Dock and the "
                        "Desktop too. Read once, when the session's first program starts.",
                "sources": ["wine/dlls/win32u/gdiobj.c", "app/Madeira/ContentView.swift",
                            "build/ntdll-unix/virtual_ios.c"]},
    "env.LC_ALL": {"category": "Wine core (ntdll)", "title": "Wine's locale for this game",
                "kind": "text", "default": "",
                "note": "Empty: Wine takes the user locale from the phone's language (Turkish phone: tr-TR, code pages "
                        "1254/857). en_US.UTF-8 gives the game an English system and user locale. Ratchet & Clank: "
                        "Rift Apart threw an unhandled C++ exception right after loading the Turkish OEM code page "
                        "(builds 550 and 553, R6025) and runs with it. Read at session start (init_locale).",
                "sources": ["build/ntdll-unix/env_ios.c"]},
    "env.MADEIRA_NLS_EXTRA": {"category": "Wine core (ntdll)", "title": "Extra code page tables",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a code page table BCD does not load for every game (Wine's own: 932 Japanese, 936 Simplified "
                        "Chinese, 949 Korean, 950 Traditional Chinese, 874 Thai, 1250, 1251 and 1253 to 1258) is read from the app's "
                        "nls/extra folder when this game asks for it; without the line such a request fails, as it always did (GetCPInfo "
                        "and MultiByteToWideChar return an error for that code page). For a game that converts text in one of those "
                        "pages: Sword and Fairy 3 asked for 936, read the missing table and ended 0.8 s after the start (build 580). "
                        "Programs that ask for these pages and run without them (Steam's own client asks for 932 in every session of "
                        "BCD Dock) are not changed unless the game's file has the line. "
                        "Read at every request, so it ends with the session. Logged as [nls-extra]; [nls-missing] names a code "
                        "page whose table was not found. Auto-tune adds it after a crash in a game that asked for 932, 936, 949, 950 or 874.",
                "sources": ["build/ntdll-unix/env_ios.c"]},
    "env.MADEIRA_APP_DLL_SHADOW": {"category": "Wine core (ntdll)", "title": "Ignore these DLLs in the game's folder",
                "kind": "text", "default": "",
                "note": "Default empty. DLL names separated by ';' (e.g. vcruntime140.dll): a plain x64 copy of one of "
                        "them outside C:\\windows (the game's own folder) is reported as not loadable, so the loader "
                        "binds the system's, as it already does for ucrtbase.dll. For a game that ships Microsoft's "
                        "x64 vcruntime140.dll but not msvcp140.dll: Wine's msvcp140 cannot call that x64 copy "
                        "(Crysis 3 Remastered: \"MSVCP140.dll failed to initialize\", c000001d). Logged as "
                        "[app-dll-shadow]. Read at every DLL load.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_ALIAS_RELEASE": {"category": "Memory & JIT pool", "title": "Drop a released arena's code copies",
                "kind": "bool", "default": "0",
                "note": "1: anonymous memory the game made executable (copied into the JIT pool) follows the game's "
                        "MEM_RELEASE and MEM_DECOMMIT. A released view drops its copies' records; a decommit zeroes "
                        "each copy in its own pool slot and maps fresh pages over the rest. For a game that releases "
                        "a memory arena and reserves it again at the same base (Ratchet & Clank: Rift Apart froze "
                        "loading a save on build 553: [alias-tomb] then [store-noalias]). Logged as "
                        "[alias-release]. Read once per session.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_JUMBO_HALF": {"category": "Memory & JIT pool", "title": "Give a too-large probe reservation half its size",
                "kind": "bool", "default": "0",
                "note": "1: a reserve-only request of 4 GB or more (a power of two) that finds no hole gets half its size at "
                        "a half-aligned free slot below the emulator's arena. For games that find each aligned arena with "
                        "a double-size probe and keep only half (Ratchet & Clank: Rift Apart's 8 GB probes for 4 GB "
                        "arenas). A game that really uses the whole reservation would fault, so set it per game. Logged "
                        "as [jumbo-half]. Read at every such request.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_FEX_BAND_KEEP_MB": {"category": "Memory & JIT pool", "title": "Keep the lowest MB of the emulator's address range for the emulator",
                "kind": "int", "default": "0",
                "note": "Default 0 (off). N MB: reservations of 32 MB to 1 GB that BCD places above the game's own range "
                        "because that range is tight may not take the lowest N MB of the emulator's 16 GB range, so "
                        "the emulator keeps room for its own memory; what no longer fits goes to the range below it, "
                        "as every such reservation already does once the 16 GB are full. For a game that reserves "
                        "many 511 MB ranges and ends with \"FEXAlloc could not allocate\" (No Rest for the Wicked "
                        "reserved 12 GB of it in 24 steps; the emulator held 4 GB). 6144 gives the emulator "
                        "6 GB. Only applies when the emulator's range is the 16 GB one at 0x7c00000000. Logged as "
                        "[fex-keep]. Read once per session.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_PRELOAD_DLLS": {"category": "Memory & JIT pool", "title": "Load these DLLs with the D3D12 device",
                "kind": "text", "default": "",
                "note": "Default empty. DLL names separated by ';' (e.g. xinput1_4.dll): each program loads and pins "
                        "them at its first D3D12 device or probe call, while the JIT pool still has room for their "
                        "copies. RDR2 loads xinput1_4.dll only at its first controller read, and on build 476 the "
                        "pool was full by then: no controller and no on-screen controls. Restart the session after "
                        "changing it.",
                "sources": ["madeira-d3d12/src/pe/madeira_d3d12.c"]},
    "env.MADEIRA_POOL_RECYCLE_IMAGES": {"category": "Memory & JIT pool", "title": "Images in retired code-buffer space",
                "kind": "bool", "default": "0",
                "note": "Default off. If other image allocations fail, retired region-C code buffers up to 64 MB "
                        "may enter the image freelist after a 3-second grace, executable-page and thread-PC checks. "
                        "Requires pool-low; 64-bit processes only. Live buffers and the pool-low-margin stay unchanged."},
    "env.MADEIRA_POOL_CODE_EXIT_RECLAIM": {"category": "Memory & JIT pool", "title": "Return an exited program's code buffers",
                "kind": "bool", "default": "1",
                "note": "On by default. When a program of the session exits (a launcher restarting itself, "
                        "RockstarService.exe, a setup step), the emulator's code buffers it held go back to the JIT "
                        "pool: reusable 3 seconds later, once no thread runs in them. Before, they stayed in use "
                        "until Madeira closed, and a game started after many such exits got 16 MB code buffers and "
                        "ran slowly. 0 keeps them in use, as before."},
    "env.MADEIRA_DOCK_GAME_SCM": {"category": "Steam & Dock", "title": "Wine's service manager in a Steam game's session",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: the Dock starts Wine's service manager (services.exe) in the game's session before "
                        "Steam launches the game, and leaves it running until the session ends. For a game whose launcher "
                        "drives a Windows service of its own: Rockstar's Launcher runs RockstarService.exe, which without "
                        "a manager logs \"Unable to open SC manager! (1722)\" and exits 1 (GTA San Andreas - The "
                        "Definitive Edition, Grand Theft Auto IV: The Complete Edition; the GTA V and Red Dead "
                        "Redemption 2 lists carry the line). The Dock report says game-scm=2 (started), 1 (already "
                        "there), 0 (off) or -1 (failed), or game-scm-timeout=1. Set it in the game's own file; read when "
                        "the session starts.",
                "sources": ["tools/patch-dock-game-scm.py"]},
    "env.MADEIRA_X64_IMAGE_NOCOPY": {"category": "Memory & JIT pool", "title": "Pure-x64 images without a JIT-pool copy",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: an x64-only image (not ARM64EC hybrid, not a Wine builtin) in a 64-bit "
                        "process is not copied into the JIT pool; the emulator runs its code from the loaded "
                        "image, as 32-bit programs already do, and its executable protections are applied without "
                        "EXEC. Frees the pool for hybrid DLL copies and code buffers. Hybrid images keep their "
                        "copy. Set it in the game's own file; restart the session after changing it."},
    "env.MADEIRA_WOW_CPU_POOL_ONLY": {"category": "Memory & JIT pool", "title": "32-bit emulator module runs from its JIT-pool copy only",
                "kind": "bool", "default": "1",
                "note": "On by default. Where the kernel lets BCD make an image executable in place (iOS 18; iOS 26 "
                        "without TXM), the 32-bit emulator module (xtajit.dll) kept an executable PE view next to its "
                        "JIT-pool copy: its start-up code and C++ constructors ran in the view while the emulator "
                        "was called in the copy, whose globals were still zero (every 32-bit program stopped before "
                        "its first instruction, builds 573 and 574). 1: EXEC is taken back from that view when its "
                        "pool copy exists, so both use the copy, as on iOS 27. 0 restores the previous behaviour. "
                        "Logged as [wow-cpu-pool] and [exec-split]. Read once per session.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_FIXED_BASE_GUARD": {"category": "Memory & JIT pool", "title": "Keep 0x140000000 for a fixed-base game",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a resource-only image view (a version or resource query, e.g. the Rockstar "
                        "Games Launcher reading Social-Club-Setup.exe) never takes the reserved executable window at "
                        "0x140000000; it is mapped elsewhere. The window then stays free for a game exe without "
                        "relocations that can only run there (Red Dead Redemption 2). It moves other programs' "
                        "images (licensed GTA V Enhanced), so set it only in such a game's own file; read at "
                        "session start."},
    "env.MADEIRA_RDR2_VA_HOLD": {"category": "Memory & JIT pool", "title": "Keep 8960 MB free for Red Dead Redemption 2",
                "kind": "bool", "default": "0",
                "note": "Default off; only with env.MADEIRA_SC_PA_POOLS = 2. 1: Social Club's layout 2 keeps libcef.dll's "
                        "PartitionAlloc pools at 256 MB and its metadata at 1 GB, chrome_elf.dll's metadata at 6.25 GB, "
                        "gives the rest of the app's 4 GB hold to Wine's furniture (3.75 GB more room for threads and "
                        "allocations), keeps Oilpan's cage at 1 GB so the FEX arena grows to 15 GB, and holds "
                        "[0x75d0000000, 0x7800000000) from boot for one 8960 MB reserve, the one "
                        "Red Dead Redemption 2 makes as it starts. Set it only in that game's own file; GTA V keeps "
                        "layout 2 as it is. Read at session start."},
    "env.MADEIRA_SWAP_RESERVE_MAX_MB": {"category": "Memory & JIT pool", "title": "Swap tier: largest reservation backed (MB)",
                "kind": "int", "default": "", "sources": ["build/ntdll-unix/virtual_ios.c"],
                "note": "The largest new reservation the swap tier backs whole: 256 MB in wide (at most 4 GB), 8 GB in "
                        "broad (at most 16 GB). 9216 with broad coverage (swap-mode = 2) also backs Red Dead "
                        "Redemption 2's 8960 MB heap with the file. Read at session start."},
    "env.MADEIRA_THREAD_STACK_SPILL": {"category": "Memory & JIT pool", "title": "New threads may use the kernel's pick when memory space is full",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a new thread's 1 MB kernel stack (and its emulator stack) is looked for from "
                        "the start of Wine's furniture band instead of from 4 GB (each new thread no longer tries "
                        "about 300 refused addresses first), and when that band is full the kernel places it "
                        "anywhere still free instead of the thread failing. Red Dead Redemption 2 froze four minutes "
                        "into the story because every new thread failed this way while 1.8 GB was free elsewhere. "
                        "Read at session start."},
    "env.MADEIRA_SMALL_STACK_EXES": {"category": "Memory & JIT pool", "title": "Programs whose threads keep a 2 MB stack",
                "kind": "text", "default": "",
                "note": "Empty by default: every thread stack is at least 8 MB (Chromium needs that). Exe names, "
                        "separated by commas (e.g. RDR2.exe): threads of these programs get the stack they ask for, "
                        "at least 2 MB, so their stacks take less of the shared memory space. Never list Chromium "
                        "programs (SocialClubHelper.exe, Launcher.exe, steamwebhelper.exe). Read when a thread starts."},
    "quiet-log": {"category": "Direct3D 12", "title": "Cheaper Direct3D 12 logging (no census frames)",
                "kind": "bool", "default": "0",
                "note": "1: the Direct3D 12 runtime's log lines skip OutputDebugString (an exception and a server "
                        "request each, ~0.5 ms a line on the device) and reach the log through Wine's own debug "
                        "output and stderr only; the census frames between presents 1500 and 3000 and the list "
                        "12000-12400 draw dumps are off (each froze the game for 1-2 s); the per-shader "
                        "\"converted at runtime\" line is kept for the first 64 shaders and then every 1000th. "
                        "A manual frame capture still works. Default off: the logging as before."},
    "replay-split": {"category": "Direct3D 12", "title": "Log where Direct3D 12 submission time goes",
                "kind": "bool", "default": "0",
                "note": "Default off, logging only. 1: every 5 seconds a [perf] replay split line divides the time "
                        "ExecuteCommandLists takes per frame into waiting for the queue, preparation, opening a "
                        "command buffer, each kind of command (draws, dispatches, copies, clears, barriers, render "
                        "targets, queries, state) and the end of each list. For finding why a game's frame is slow "
                        "when the GPU is not busy."},
    "env.MADEIRA_MFC": {"category": "Wine libraries", "title": "Microsoft MFC (mfc140.dll, mfc140u.dll)",
                "kind": "bool", "default": "1",
                "note": "On by default. MFC comes from the same Microsoft VC++ package as the C++ runtime and is "
                        "linked into the game's system32; games that import it (Horizon Zero Dawn) stop at once "
                        "without it. Wine has no MFC of its own. 0 leaves it out. Read at session start."},
    "env.MADEIRA_MFC_DOCK": {"category": "Wine libraries", "title": "Microsoft MFC in a BCD Dock game",
                "kind": "bool", "default": "0",
                "note": "Default off. A game BCD Dock starts (an x64 program in an aarch64 session) looks for MFC in "
                        "C:\\windows\\sysx64, where none is: Horizon Zero Dawn and Age of Empires II HD stop at once with "
                        "c0000135 (\"import_dll Library mfc140.dll ... not found\"). 1: mfc140.dll and mfc140u.dll "
                        "from the downloaded VC++ runtime are linked there, and nothing else of it. Does nothing in a "
                        "direct start (ARM64EC), which has MFC already; MADEIRA_MFC=0 keeps it out here too. Logged "
                        "as MADEIRA_MFC_DOCK. Read at session start, from the game's own lines."},
    "env.MADEIRA_DOCK_SCRIPT_REGISTRY": {"category": "Steam & Dock", "title": "Apply the install script's registry values",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: before a BCD Dock start, the \"Registry\" values of the game's Steam install "
                        "script (installscript.vdf; the install folder above all, %INSTALLDIR% filled in) are written "
                        "into BCD's Windows registry where the key or value is missing, as Steam on Windows does. "
                        "Fallout: New Vegas' launcher says \"does not appear to be installed\" without its \"Installed "
                        "Path\". Values already there are never changed; logged as [dock-script-registry], one line "
                        "each. Read at a Dock start, from the game's own lines."},
    "fullscreen-window": {"category": "Direct3D 12", "title": "Full screen moves the game's window over the whole screen",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: when a Direct3D 12 game switches to full screen, its window is moved to the "
                        "screen's top-left corner and over the whole screen first, as Windows does; the game then "
                        "sizes it to the chosen mode. Without it a game that only resizes its window (Red Dead "
                        "Redemption 2) keeps it where it was, beside the Wine desktop. Read once per session."},
    "encode-batch": {"category": "Direct3D 12", "title": "One Metal call per encoder, not per draw",
                "kind": "bool", "default": "1",
                "note": "Default on. The commands a replay hands to one Metal encoder (every draw's state and draw, the "
                        "encoder's fence wait and update) are queued and handed over in one call when the encoder ends, "
                        "instead of one call into the Metal bridge per draw: the same commands in the same order, with "
                        "~1,500 fewer crossings a frame in Horizon Zero Dawn. 0: one call per draw, as before (for "
                        "telling a regression apart on the device). Read once per game start."},
    "draw-dedup": {"category": "Direct3D 12", "title": "Leave out render state the encoder already has",
                "kind": "bool", "default": "1",
                "note": "Default on. A draw no longer sends Metal the pipeline, viewport, scissor, depth-stencil state, "
                        "blend colour, rasterizer state or buffer bindings the render encoder already holds from the "
                        "draw before it; a buffer bound again with only a new offset is sent as an offset. Every draw "
                        "still runs with exactly the state it set (about half of a Horizon Zero Dawn draw's Metal "
                        "calls were such repeats). 0: every draw sends all of it, as before. Read once per game start."},
    "addr-scan": {"category": "Direct3D 12", "title": "Quicker GPU-address lookups",
                "kind": "bool", "default": "1",
                "note": "Default on. Finding the buffer behind a GPU address (every vertex buffer, index buffer and root "
                        "descriptor a game records) walks back only over entries that can still reach the address, "
                        "instead of over every buffer within the largest buffer's size of it (124 MB in Horizon Zero "
                        "Dawn). The same buffer is found. 0: the old walk. Read once per game start."},
    # madeira-bcd: tools/patch-winemetal-rp-lean.py (winemetal reads it).
    "rp-lean": {"category": "Direct3D 12", "title": "Render passes fill only the targets they use",
                "kind": "bool", "default": "1", "sources": ["tools/patch-winemetal-rp-lean.py"],
                "note": "Default on. A Metal render pass is described with only the colour targets it uses; the bridge "
                        "filled all eight slots for every pass (~240 Objective-C calls and 8 allocations a pass, a few "
                        "hundred passes a frame). Metal ignores a slot without a texture, so the pass is the same. "
                        "Also used by Direct3D 9/10/11 games through DXMT. 0: all eight slots, as before. Read once "
                        "per game start."},
    "indirect-fast": {"category": "Direct3D 12", "title": "Encode ExecuteIndirect records without per-record setup",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: after the first record of an ExecuteIndirect, the other records (which share "
                        "all state but their offset) are encoded 64 at a time with only their offset and the "
                        "indirect draw or dispatch, instead of the whole per-draw setup each. For games that issue "
                        "thousands of indirect records a frame (Red Dead Redemption 2), where that setup costs more "
                        "CPU time than the GPU spends. DXIL and DXBC pipelines (DXBC from build 470); pipelines "
                        "with tessellation or geometry stages, diagnostics and captures keep the per-record path. "
                        "Read once per session."},
    "dxbc-register-spaces": {"category": "Direct3D 12", "title": "Shader model 5.1 resources in register spaces other than 0",
                "kind": "bool", "default": "0",
                "note": "Default off: a DXBC shader (shader model 5.1) that declares a resource in a register space "
                        "other than 0 is refused, and its pipeline is not created. 1: such shaders are converted, and "
                        "each resource is bound from the root-signature entry with the same space and register. "
                        "Horizon Zero Dawn needs it (its constant buffers sit in spaces 6 and 8). Shaders converted "
                        "with it on are cached apart from the others. Read once per session.",
                "sources": ["madeira-d3d12/src/unix/madeira_ir_unix.mm", "madeira-d3d12/src/pe/madeira_d3d12.c"]},
    "env.MADEIRA_GAME_DIALOG_CURSOR": {"category": "Windows, display & input", "title": "Cursor over a game's launcher or message box",
                "kind": "bool", "default": "1",
                "note": "Default on. A game session draws no cursor: the finger is the pointer. While the game shows a "
                        "launcher or message box, the arrow is drawn where the finger last touched (where the click "
                        "lands) and goes away with the box. Tap the button itself to press it. 0: no cursor, as "
                        "before. Desktop (Dock) sessions keep their own cursor."},
    # madeira-bcd: Horizon Zero Dawn on build 471 got no input at all.
    "env.MADEIRA_INPUT_FOREGROUND": {"category": "Windows, display & input",
                "title": "Bring the game to the front when no window is",
                "kind": "bool", "default": "0",
                "note": "Default off. iOS has no window manager: a game that shows its window without activating "
                        "it and reads no XInput stays in the background, and Windows sends raw keyboard and mouse "
                        "input only to the program in front (Horizon Zero Dawn ignored every key and tap). 1: while "
                        "no window at all is in front, the game's main window is brought to the front when it polls "
                        "its messages or a key or tap arrives. Never while another window is in front."},
    "env.MADEIRA_WGI_HOST_PADS": {"category": "Controllers", "title": "Controllers in Windows.Gaming.Input",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: each connected XInput controller (a paired pad or the touch controller) "
                        "is also a Windows.Gaming.Input gamepad, an Xbox pad to the game, with rumble. For games "
                        "that read their controller only that way (Horizon Zero Dawn); a game that also reads "
                        "XInput could list it twice. Read when the game first asks for a gamepad.",
                "sources": ["tools/patch-wine-wgi-host-pads.py"]},
    # madeira-bcd: Red Dead Redemption 2 on build 470, 1.5 GB of argument-ring chunks.
    "ind-probe": {"category": "Direct3D 12", "title": "ExecuteIndirect probe (diagnostic)",
                "kind": "bool", "default": "1",
                "note": "Default on. Every 3 s per pipeline, the first argument record of an ExecuteIndirect is read "
                        "back by a small compute pass and logged as a [probe] line, 1500 lines at most. Each probe "
                        "ends the open render pass. 0: no probes (Red Dead Redemption 2's list, build 476)."},
    # madeira-bcd: Horizon Zero Dawn's refused shaders (build 475, 2026-10-09 16:20).
    "msc-unbounded-retry": {"category": "Direct3D 12", "title": "Retry refused shaders with sized unbounded ranges",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a DXIL shader the converter refuses with code 4 (a resource not in the root "
                        "signature) is converted once more with the root signature's unbounded descriptor ranges "
                        "given a size (2048 samplers, 1000000 others). Horizon Zero Dawn's ranges start at register 4 "
                        "and ~4,900 of its shaders were refused. Once that works for a root signature, its later "
                        "shaders take the sized ranges first."},
    "pso-placeholder": {"category": "Direct3D 12", "title": "Placeholder for pipelines whose shaders did not convert",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a graphics pipeline whose vertex or pixel shader did not convert is returned "
                        "to the game as a placeholder whose draws are skipped, instead of a failure. Horizon Zero "
                        "Dawn stopped with an \"Error\" box when its settings menu got such a failure."},
    # madeira-bcd: Horizon Zero Dawn's 23,045 libraries (build 480, 2026-10-09 20:28).
    "pso-lazy-libs": {"category": "Direct3D 12", "title": "Shader libraries only for pipelines in use",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a pipeline built at its first draw (or dispatch) lets its converted Metal "
                        "libraries go once the game has created it, and reads them back from the shader cache at "
                        "that first draw. Horizon Zero Dawn creates ~52,000 pipelines in its menu and draws a few "
                        "thousand; their libraries held hundreds of MB until iOS closed the game. Needs the shader "
                        "cache (on by default); with pso-warm, a library stays until its warm-up compile is done."},
    # madeira-bcd: Horizon Zero Dawn's corrupted first gameplay frames (build 480, 2026-10-09 20:37).
    "clear-rects": {"category": "Direct3D 12", "title": "Clear only the rectangles a game asks for",
                "kind": "bool", "default": "0",
                "note": "Default off: a render-target or depth-stencil clear with rectangles clears the whole view, as "
                        "before. 1: only the rectangles are cleared, in a small pass of their own (as DXMT's D3D11 "
                        "ClearView does); a rectangle that covers the view still clears it the fast way. Horizon Zero "
                        "Dawn passes rectangles from its first gameplay frames."},
    # madeira-bcd: render-pass memory traffic (Horizon Zero Dawn ~1.9 GB a frame through attachments, build 493).
    "clear-defer": {"category": "Direct3D 12", "title": "Clears wait for the pass that draws to their target",
                "kind": "bool", "default": "1",
                "note": "On by default. A whole render-target or depth-stencil clear becomes the clear the next "
                        "render pass that draws to that target starts with: a clear no longer ends the pass in "
                        "progress unless it is one of that pass's own targets, and needs no pass of its own (which "
                        "wrote the whole target to memory and read it back later). A barrier or copy that names the "
                        "cleared texture, or a pass or clear that uses it through another view, applies the clear "
                        "first, so nothing sees it uncleared. 0: every clear ends "
                        "the open pass and goes before the next pass on its own, as before. Read once per game start."},
    "rp-carry": {"category": "Direct3D 12", "title": "Experiment: render passes go on into the next command list",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: when a command list ends inside a render pass and the next list of the same "
                        "submission draws to the same targets first, with only state between the two draws, the pass "
                        "stays open: its targets are not written to memory and read back. Horizon Zero Dawn records "
                        "its depth and G-buffer passes in ~20 lists (0.3-0.6 GB a frame of such writes and reads), "
                        "GTA V Enhanced its G-buffer in ~12. Opt-in: the second list's draws no longer wait for the "
                        "whole first pass, an ordering Direct3D 12 does not promise but a game might rely on. Read "
                        "once per game start; [perf] gpu-traffic lines show what it saves."},
    "rp-discard": {"category": "Direct3D 12", "title": "Experiment: no load after, no store before DiscardResource",
                "kind": "bool", "default": "0", "sources": ["madeira-d3d12/src/pe/madeira_d3d12.c", "tools/patch-winemetal-rp-store.py"],
                "note": "Default off. 1: after a game discards a whole render target or depth buffer "
                        "(DiscardResource), the next render pass that draws to it does not read its old contents "
                        "from memory, and a pass whose target is discarded (or cleared whole) next, before any other "
                        "use in the same submission, does not write it back -- as tile-based GPUs do on Windows. "
                        "Opt-in: a game that still reads what it discarded (desktop GPUs ignore discards) would show "
                        "garbage there. Read once per game start; [perf] gpu-traffic lines show the bytes."},
    "f6-rar": {"category": "Direct3D 12", "title": "Experiment: fence-chain 6 ignores read-to-read barriers",
                "kind": "bool", "default": "0",
                "note": "Default off; only with fence-chain = 6. 1: a ResourceBarrier whose every transition goes "
                        "from read states to read states (or is the first half of a split barrier) no longer makes "
                        "the next GPU pass wait for all earlier ones, and no longer ends an overlapping render pass "
                        "(which writes its targets to memory and reads them back). Direct3D 12 orders nothing at "
                        "such a barrier; a game that relied on it anyway would show flicker. Read once per game "
                        "start; [perf] f6-rar lines count them."},
    "skin-check": {"category": "Direct3D 12", "title": "Diagnostic: check meshes skinned on the GPU",
                "kind": "int", "default": "0",
                "note": "Default 0 (off). N: windows of N frames (1 = 8) log every draw that reads a vertex buffer a "
                        "compute shader wrote, the dispatches that wrote it with every binding, a summary of up to "
                        "32 KB of each such stream copied just before the draw, and the shaders' bytecode. For "
                        "characters drawn with missing faces or spikes (Horizon Zero Dawn); costs time in those frames."},
    "skin-check-every": {"category": "Direct3D 12", "title": "Diagnostic: presents between skin-check windows",
                "kind": "int", "default": "300",
                "note": "With skin-check on: a window starts this many presents after the previous one began "
                        "(at least 30); the first starts 60 presents after the first such draw."},
    "skin-check-capture": {"category": "Direct3D 12", "title": "Diagnostic: skin-check copies the streams",
                "kind": "bool", "default": "1",
                "note": "On by default with skin-check. 0: log only, with no GPU copies, which add a copy between "
                        "the skinning dispatches and the draw (to compare what the frames look like)."},
    "view-census": {"category": "Direct3D 12", "title": "Diagnostic: report buffer views that fall back",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: typed buffer views that cannot become a Metal texture buffer (the shader then "
                        "sees no data), views cut short at the end of their resource, and views or constant buffer "
                        "views past their resource are counted and named in the log, with Metal's minimum alignment "
                        "for each pixel format typed views use. Changes nothing else."},
    "skin-check-trace": {"category": "Direct3D 12", "title": "Diagnostic: skin-check traces whole frames",
                "kind": "bool", "default": "0",
                "note": "Default off. 1, with skin-check on: the first frame of the first two windows logs every draw "
                        "in one line (shaders, counts, streams, input layout); a pipeline with integer vertex inputs "
                        "(a mesh skinned in its vertex shader) also gets its vertex-stage bindings, copies of its "
                        "CPU-written inputs and first vertices, and its vertex shader's bytecode (32 such draws)."},
    "skin-dump": {"category": "Direct3D 12", "title": "Diagnostic: skin-check dumps the skinning data",
                "kind": "bool", "default": "0",
                "note": "Default off. 1, with skin-check on: in the first window's first two frames the compute "
                        "shaders that skin a mesh have their inputs and the stream they wrote copied whole (up to 256 KB "
                        "each) and logged as base64 (4 MB at most), so the skinning can be recomputed on a PC. Uses a "
                        "24 MB capture buffer."},
    "typed-view-align": {"category": "Direct3D 12", "title": "Typed buffer view alignment",
                "kind": "int", "default": "0",
                "note": "Default 0 (64 bytes, as before). 16 or 256: a typed buffer view's texture starts at that "
                        "boundary, never below Metal's minimum for its format (logged once per format); 16 is what "
                        "Apple's converter documents, so a float4 view has no padding elements. 4: every view's texture "
                        "starts exactly at its first element, for the element sizes a GPU probe at the first view shows "
                        "read right (logged); the others start at 16. A view Metal refuses there is made at 16, then 64. "
                        "Read once per game start."},
    "typed-view-shadow": {"category": "Direct3D 12", "title": "Aligned copies of offset typed views",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a typed shader-resource view of UPLOAD memory whose first element is not on a "
                        "16-byte boundary reads a 16-byte-aligned copy of its bytes, made when the view is made and "
                        "refreshed at every ExecuteCommandLists (the GPU rounds a texture buffer's start down to 16 bytes "
                        "and the shader converter ignores the element offset). view-census counts them. Read once per "
                        "game start."},
    "vis-trace": {"category": "Direct3D 12", "title": "Diagnostic: per-frame draws and culling readback",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: one log line per present (the HUD's frame number): draws, depth-only and "
                        "3+-target draws, indices, dispatches, depth-pyramid readback copies, new offset typed views, and "
                        "Queue::Wait calls (how many passed before the awaited batch was committed); and every value of the "
                        "22x10, 11x5 and 5x2 mips of the pyramid a game culls with, as the GPU wrote them. The GPU also "
                        "copies each of those mips into Madeira's own memory, and a mip whose readback memory holds "
                        "something else is flagged (an earlier frame's values: STALE). Read once per game start."},
    "readback-far": {"category": "Direct3D 12", "title": "Experiment: empty culling readback",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a readback of a depth pyramid (an R32_FLOAT mip of 1 or more of a texture with 4+ "
                        "mips, up to 64x64) is not copied; the CPU fills it with 4096, so a game that culls on the CPU "
                        "against it culls nothing (everything is drawn: slower). A test, not a fix. Read once per game "
                        "start."},
    "static-cbv": {"category": "Direct3D 12", "title": "Constant buffers as the draw was recorded",
                "kind": "int", "default": "0",
                "note": "Default off: the GPU reads every descriptor of a table when it runs. 1: a descriptor table's "
                        "single constant-buffer ranges are read when each draw or dispatch is recorded, as NVIDIA's "
                        "driver and vkd3d-proton do; a game that rewrites one after recording the draw no longer makes "
                        "that draw use another object's constants. Ranges marked VOLATILE stay as before. 2: VOLATILE "
                        "ones too (vkd3d-proton's force_static_cbv). 3: only count the rewrites ([static-cbv] lines). "
                        "Read once per game start."},
    "readback-swap": {"category": "Direct3D 12", "title": "D3D12 readback buffers on file-backed memory",
                "kind": "bool", "default": "1",
                "note": "Default on: like upload buffers (upload-swap), READBACK buffers of 8 MB or more live on "
                        "file-backed storage, off the memory footprint. 0: READBACK buffers get Metal's own memory "
                        "(Horizon Zero Dawn: two 32 MB buffers), so what the GPU writes into them reaches the CPU "
                        "without the file tier between; upload buffers stay where upload-swap puts them. Read once per "
                        "game start."},
    # madeira-bcd: A/B switches for the Sony ports' corrupted meshes (Horizon Zero Dawn, Ghost of Tsushima).
    "d3d12-wave-ops": {"category": "Direct3D 12", "title": "Report wave operations",
                "kind": "bool", "default": "1",
                "note": "On by default. 0: OPTIONS1 reports WaveOps FALSE, so a game with a fallback picks shaders "
                        "without wave intrinsics (an A/B for the shader converter's wave code; Horizon Zero Dawn reads "
                        "it). Read once per game start."},
    "d3d12-binding-tier": {"category": "Direct3D 12", "title": "Reported resource binding tier",
                "kind": "int", "default": "2",
                "note": "Default 2. 3: OPTIONS reports resource binding tier 3, as Apple's D3DMetal and vkd3d-proton do; "
                        "a game can take another binding path for it. Anything else keeps tier 2. Read once per game "
                        "start."},
    "d3d12-pipeline-library": {"category": "Direct3D 12", "title": "Pipeline library for the game",
                "kind": "bool", "default": "0",
                "note": "Default off: CreatePipelineLibrary fails (E_NOTIMPL), as before. 1: it makes a library that "
                        "keeps no pipeline (loads answer \"not in the library\", stores are accepted and forgotten, "
                        "the saved form is 16 bytes). For an Unreal title that ends in a fatal error when the call "
                        "fails (Returnal); GTA V and Red Dead Redemption 2 call it and carry on without. Read once "
                        "per game start."},
    "d3d12-raw-typed-views": {"category": "Direct3D 12", "title": "Raw buffer views readable as typed",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: raw and structured buffer views also carry a texture-buffer view (R32, or "
                        "RG32/RGBA32 for a structured SRV whose stride allows it), as native drivers and vkd3d-proton "
                        "do, so a shader that reads one as Buffer<T> gets the data instead of zeros (vertices at the "
                        "origin). Costs one Metal texture object per such view."},
    "ignore-volatile-metadata": {"category": "Memory & JIT pool", "title": "FEX: x86 ordering for the whole game",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: an x64 image's PE volatile metadata (MSVC /volatileMetadata) is cleared when "
                        "it is mapped, so FEX orders every instruction of it by its TSO settings instead of only the "
                        "ones the metadata lists (Ghost of Tsushima's exe carries it; Horizon Zero Dawn's concrt140.dll "
                        "too). Slower; for testing memory-ordering bugs. Each cleared image is logged as [fex-vmeta]. "
                        "Read at session start."},
    "msc-fail-memo": {"category": "Direct3D 12", "title": "Remember refused shaders for the session",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a shader the converter refused is not converted again for the next pipeline "
                        "that uses it (same bytecode, root signature and options) until the game exits. Horizon Zero "
                        "Dawn converted each refused shader ~6 times, ~30,000 conversions in its first minutes."},
    "ring-share": {"category": "Direct3D 12", "title": "Share argument buffers between command lists",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: a command list gives the argument-buffer chunks its replay wrote back as "
                        "soon as the GPU is done with them, instead of keeping them until it is recorded again; "
                        "all lists then share one pool. Red Dead Redemption 2 kept 1.5 GB of them (half of its "
                        "Metal memory) with the switch off."},
    # madeira-bcd: tools/patch-winemetal-heap-untracked.py (winemetal reads it).
    "heap-untracked": {"category": "Direct3D 12", "title": "Placement heaps without Metal hazard tracking",
                "kind": "bool", "default": "0", "sources": ["tools/patch-winemetal-heap-untracked.py"],
                "note": "Default off. 1: the Metal placement heaps madeira-d3d12 makes for a game's DEFAULT heaps and "
                        "for its own texture heaps are created untracked. A tracked heap makes later work on every "
                        "resource in it wait for a write to any one of them, and every encoder pays the driver's "
                        "bookkeeping; with fence-chain 1 (the default) the runtime's own encoder fences already "
                        "order everything. With a looser fence-chain mode the game's own barriers are then all that "
                        "orders heap resources: try it with fence-chain 1 first. Read once per game start."},
    # madeira-bcd: tools/patch-winemetal-null-fragment.py (winemetal reads it).
    "gs-null-fragment": {"category": "Direct3D 12", "title": "Empty fragment function for pipelines without a pixel shader",
                "kind": "bool", "default": "1", "sources": ["tools/patch-winemetal-null-fragment.py"],
                "note": "Default on. Metal aborts on a mesh pipeline (geometry shader or tessellation emulation) "
                        "with rasterization on and no fragment function, while D3D allows a pipeline with no pixel "
                        "shader. On: such a pipeline gets an empty fragment function and writes no render target, "
                        "as in D3D; depth and stencil work as before. Horizon Zero Dawn's pipeline thread died "
                        "without it and the game stopped after its first frame. 0: such a pipeline is refused "
                        "instead (the geometry shader is dropped). Pipelines with a pixel shader are not touched. "
                        "Read once per session."},
    "env.MADEIRA_BAND_CENSUS": {"category": "Debugging / logs", "title": "Log the 64 GB address band by piece",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: about once a minute and when a large reserve fails, [band] lines say what "
                        "each piece of [0x7000000000, 0x8000000000) uses (Social Club's pools and metadata, the "
                        "furniture, the V8 and Oilpan cages, the FEX arena), the largest free gap, and whether Red "
                        "Dead Redemption 2's 8960 MB reserve fits; 64 censuses at most. Logging only, changes no "
                        "placement; read at session start."},
    "env.MADEIRA_RECOVERY_LOG_SAMPLE": {"category": "Debugging / logs",
                "title": "Sample routine instruction recovery logs", "kind": "bool", "default": "0",
                "note": "Default off: recovery diagnostics are unchanged. 1 keeps the first 16 and every 1024th "
                        "recognised instruction-recovery debug record, and the first 8 and every 1024th known "
                        "shared-HLT diagnostic bundle. Other exceptions and unknown records remain visible. "
                        "Only log output changes; existing RTCS limits remain independent. Read before guest "
                        "threads start. Restart BCD after changing it."},
    "env.MADEIRA_EC_HOOK_TRACE": {"category": "Debugging / logs", "title": "Log code patches the emulated copy misses",
                "kind": "bool", "default": "1",
                "note": "Default on, logging only. Compare executable PE sections and their pool copies before "
                        "sync, including x64 entry thunks. Separate budgets preserve later graphics-jump records "
                        "after native startup rewrites. A difference alone does not prove an inline hook. 0 disables it."},
    "env.MADEIRA_X64_GRAPHICS_ENTRY": {"category": "Direct3D 12 (Madeira)", "title": "Patchable x64 graphics method entries",
                "kind": "bool", "default": "0",
                "note": "Default off. 1 exposes typed x64 ARM64EC entries for swapchain Present/Present1, "
                        "ResizeBuffers/ResizeBuffers1 and queue ExecuteCommandLists/GetTimestampFrequency/GetClockCalibration/GetDesc/Signal/Wait. "
                        "With MADEIRA_DXGI_SRC=1, "
                        "also selects the factory's patchable MakeWindowAssociation and swapchain-creation entries. "
                        "The x64 entries use the loader's PE addresses so code patches and nearby allocations agree. "
                        "Existing native implementations remain behind the entries. Restart the game session."},
    "env.MADEIRA_POOL_LOW_IMAGES": {"category": "Memory & JIT pool", "title": "Small images in spare code-buffer space",
                "kind": "bool", "default": "0",
                "note": "Default off. If the normal JIT image allocation fails, copies up to 16 MB may use "
                        "unallocated region C, preserving 4 MB for code buffers. Requires pool-low; 64-bit "
                        "processes only. Does not shrink live buffers or the pool-low-margin."},
    "env.MADEIRA_IMAGE_PATCH_TRACE": {"category": "Debugging / logs", "title": "DLL code patch trace", "kind": "bool", "default": "0",
                "note": "1 logs up to 64 successful 1–16 byte executable-image protection requests and their PE/pool bytes. Read-only, owner-aware diagnostics; does not alter hooks or code."},
    "env.MADEIRA_DLL_LOCAL": {"category": "Wine core (ntdll)", "title": "App-local DLL reads", "kind": "text", "default": "",
                "note": "Optional semicolon-separated DLL filenames, including .dll. A source.dll=proxy.dll entry "
                        "redirects requests outside the executable's directory to that local proxy, preserving initial "
                        "loads from the executable's own directory. Bare filenames prefer the same local DLL. "
                        "Only read-only opens and attribute queries are redirected. Missing local files, "
                        "writes, create/delete operations, relative paths and non-file devices keep the normal path. "
                        "Off by default; set only in the game's own file."},
    "swap-mb": { "note": "Moves game data to a file on this device's storage when memory runs short, up to this size. Off by default; read at launch.", "category": "Memory & JIT pool","title": "Swap tier size", "kind": "choice",
                "choices": [("", "Off"), ("1024", "1 GB"), ("2048", "2 GB"), ("3072", "3 GB"), ("4096", "4 GB")]},
    "env.MADEIRA_SWAP_COVERAGE": {"category": "Memory & JIT pool", "note": "Which allocations the swap tier backs with its file (only when the tier is on). Large allocations (classic, the default): single 8 MB+ commits in the guest band. All allocations of 1 MB+ (blocks). 1 MB+ and overflow (wide): blocks plus allocations outside the band and fresh reservations. Whole reservations 4 MB+ (broad, ml1257): every new reservation of at least swap-min-mb (4 MB) below FEX's band backed whole when made, holes punched on decommit, swap-mb caps the disk it uses (a soft cap, checked when a block is backed). Unset: broad if swap-mode = 2, else classic.", "title": "Swap tier coverage", "kind": "choice",
                "choices": [("", "Large allocations (8 MB+)"), ("blocks", "All allocations of 1 MB+"), ("wide", "1 MB+ and overflow"), ("broad", "Whole reservations 4 MB+ (broad)")]},
    "swap-mode": {"category": "Memory & JIT pool", "title": "Swap tier mode (2 = broad)",
                "note": "2 selects broad swap coverage (ml1257) when env.MADEIRA_SWAP_COVERAGE is unset; any other value, classic. The coverage key wins when set."},
    "swap-min-mb": {"category": "Memory & JIT pool", "title": "Swap tier floor (MB)",
                "note": "The smallest allocation the swap tier backs (ml1257): 8 MB in classic, 1 MB in blocks and wide, 4 MB in broad unless set. MADEIRA_SWAP_MIN_KB overrides it for blocks, wide and broad."},
    "inproc-sync": { "category": "Synchronisation","title": "Madsync (in-process sync)", "default": "0",
                "note": "1 selects madsync (Settings > Sync engine > Madsync). Unset: fastsync, the default engine; 0 without env.MADEIRA_FASTSYNC: Wine standard sync."},
    "env.MADEIRA_FASTSYNC": {"category": "Synchronisation", "title": "Fastsync (in-process sync, default)", "kind": "choice",
                "note": "Fastsync is the default engine: with neither this nor inproc-sync set, the app exports auto. Settings > Sync engine > Fastsync removes both keys. Never runs while madsync is on. auto arms the fast wake path on heavy event traffic, 1 from the start, cells only answers polls, 0 is off.",
                "choices": [("", "Default (auto)"), ("auto", "Auto"), ("1", "On"), ("cells", "Poll answers only"), ("0", "Off")]},
    # Read by winemetal (the DXGI budget) and, for the opt-in D3DKMT adapter, by
    # build/win32u-unix/d3dkmt_ios.c; keep the DXMT category and note.
    "vram-mb": {"title": "Video memory budget (MB)", "category": "Direct3D 9/10/11 (DXMT)",
                "note": "ml1095: bcd.cfg vram-mb = N"},
    "pool": { "category": "Memory & JIT pool","title": "JIT pool size (MB)"},
    "totalphys": { "category": "Memory & JIT pool","title": "Reported physical memory (MB)"},
    "eco": { "note": 'Runs guest threads at a lower iOS QoS class so the system favours efficiency and saves power; can cost speed. Class chosen by eco-qos.',"title": "Eco scheduling"},
    "eco-qos": {"title": "Eco QoS class", "kind": "choice", "note": "The QoS class guest threads run at while eco is on.",
                "choices": [("", "Utility (default)"), ("background", "Background"), ("initiated", "User initiated")]},
    "env.MADEIRA_WG_VIDEO": {"title": "Media: MP4 video (32-bit programs)",
                "note": "0 limits the media parser to MP3/WAV; by default MP4 with H.264/HEVC video decodes through VideoToolbox."},
    "env.MADEIRA_WOW_RWX_PLAIN": {"category": "Memory & JIT pool", "note": "Unset: a 32-bit window's anonymous RWX memory is plain read/write to the host once Wine Mono's libmono-2.0-x86.dll is mapped there (ml1279/ml1282). 1: in every 32-bit window. 0: never (stores go through the JIT pool's alias, and FEX's Mono bridge stays off)."},
    "env.MADEIRA_WINEMONO_BRIDGE": {"note": "FEX's Mono backpatcher bridge (ml712). On by itself for Wine Mono, 32- and 64-bit (ml1282/ml1286); for the 32-bit runtime in a guest window it also turns SMC detection off once the backpatcher is found (ml1280). 0 keeps it off."},
    "env.MADEIRA_MONO_DEFAULTS": {"category": "Wine libraries", "note": "Wine Mono, 32- and 64-bit: mscoree sets MONO_THREADS_SUSPEND=coop and adds keep-delegates to MONO_DEBUG before Mono loads, and puts the previous values back once Mono has read them (ml1282); values already set win. 0 sets nothing."},
    "cpu-count": {"title": "Reported CPU count (0 = device)"},
    "desktop-size": {"title": "Virtual desktop size (WxH)",
                     "note": "The developer interface's Wine desktop size (ml1127); its Resolution menu writes it (ml1157). Madeira Dock sessions without a game's Resolution use it too. Unset: this screen's shape at 1280x720's pixel count (ml1172: 1408x648 on a 19.5:9 iPhone, 1152x800 on an 11-inch iPad). Applies at the next app start."},
    "d3d9": {"title": "Direct3D 9 frontend (32-bit)", "kind": "choice",
             "choices": [("", "Default (emulated)"), ("native", "Native ARM64 frontend")]},
    "fence-chain": {"title": "D3D12 fence chain mode"},
    "async-submit": {"title": "D3D12 asynchronous submission"},
    "upload-swap": {"title": "D3D12 upload buffers on file-backed memory"},
    "d3d12-typed-uav-load": {"title": "D3D12 typed UAV loads (report support)"},
    # madeira-bcd: the packed shader cache (madeira-d3d12/src/unix/madeira_sc_pack.c).
    "d3d12-shader-pack": {"category": "Direct3D 12", "title": "D3D12 shader cache in two pack files",
                "kind": "bool", "default": "0",
                "note": "Off by default: one file per converted shader in Documents/shadercache, as before. 1: the "
                        "converter keeps its DXBC and DXIL cache entries in two append-only files "
                        "(shadercache/pack/dxbc.mdpk, dxil.mdpk); loose entries of earlier runs move in as games use "
                        "them. Off again, the packs are not read: what moved into them converts again. Bounds: DXIL as "
                        "env.MADEIRA_D3D12_DXIL_CACHE_MB (512 MB), DXBC 2 GB; a full pack starts over. Read once per "
                        "app start; the game's own file wins."},
    # madeira-bcd: madeira-d3d12's shader cache without Wine's file API (tools/patch-winemetal-sc-native.py).
    "shader-cache-native": {"category": "Direct3D 12", "title": "D3D12 shader cache files read natively",
                "kind": "bool", "default": "1",
                "note": "On by default. The Direct3D 12 runtime's shader cache (AppData\\Local\\BCD\\ShaderCache) "
                        "is read and written by Madeira itself instead of through Wine's file calls, which asked "
                        "Wine's server three times per entry (~3.5 ms each in Horizon Zero Dawn's menu, ~1,000 "
                        "entries a second, with the server on a performance core). The same files either way. "
                        "0: Wine's file calls, as before. Read when the game creates its first pipeline.",
                "sources": ["madeira-d3d12/src/pe/madeira_d3d12.c", "tools/patch-winemetal-sc-native.py"]},
    # Read by DXMT's DXGI and by win32u's display adapter (sysparams_ios.c); a
    # library entry's "Report an NVIDIA GPU" sets it.
    "env.DXMT_ENABLE_NVEXT": {"category": "Direct3D 9/10/11 (DXMT)", "title": "Report an NVIDIA GPU (all games)",
                "note": "1: DXGI names NVIDIA as the vendor, DXMT's NVAPI answers and win32u registers the display "
                        "adapter as a GeForce RTX 3060 (driver 581.57). Per game: Game details > Report an NVIDIA GPU, "
                        "which also sets the matching DXGI device id."},
    "ags-rewrite": {"title": "D3D12 AMD AGS 64-bit atomics rewrite"},
    "env.MADEIRA_EXE": {"title": "Program to start at launch (Windows path or name)"},
    "env.MADEIRA_ONBOARDING": {"title": "First-run Steam setup"},
    "env.MADEIRA_XINPUT": {"title": "Physical controllers (XInput)"},
    "env.MADEIRA_TOUCH_XINPUT": {"title": "Touch controller as XInput player 1"},
    # 578: app/Madeira/TouchControlPresets.swift (ControlPresetsModel.applyGameDefault).
    "env.MADEIRA_TOUCH_GAME_DEFAULT": {"category": "Controllers", "title": "Built-in touch layout for a game with none",
                "kind": "bool", "default": "1",
                "note": "On by default. A library game whose touch controls are on with nothing on screen (no controls and "
                        "no layout, at its start or when you turn them on in its menu) gets a built-in layout for its kind: "
                        "the controller layout for a Steam game, the PC keyboard layout for any other. Controls or a "
                        "layout you have are never replaced. 0: the overlay stays empty until you pick a layout.",
                "sources": ["app/Madeira/TouchControlPresets.swift"]},
    "env.MADEIRA_DINPUT_PAD": {"title": "DirectInput joystick from the host gamepad"},
    # ml2100: the HID controller (build/wineserver/hidpad_ios.c, docs/CONTROLLERS.md).
    "env.MADEIRA_PAD_MODE": {"category": "Controllers", "title": "Controller API (player 1)", "kind": "choice",
                "note": "XInput (default): every controller is an Xbox pad. hid: player 1 becomes a HID game controller, "
                        "a DualSense (054C:0CE6) when it is a PlayStation pad, else a generic HID gamepad, and leaves "
                        "XInput. dualsense/generic force the identity. Read at session start; the game's own file wins.",
                "choices": [("", "XInput (default)"), ("hid", "DirectInput / HID"), ("dualsense", "HID, always a DualSense"),
                            ("generic", "HID, always a generic gamepad")],
                "sources": ["app/Madeira/GamepadInput.swift"]},
    # madeira-bcd (2026-10-18): hardware mouse and keyboard (app/Madeira/HardwareInput.swift,
    # docs/KEYBOARD_MOUSE.md); read through HardwareInput.sessionValue, so a game's own lines set them.
    "env.MADEIRA_POINTER_FOLLOW": {"category": "Windows, display & input", "kind": "choice",
                "title": "Mouse: a game's own cursor follows the iPad pointer",
                "note": "iPad with a mouse or trackpad, for a game that hides the Windows cursor and draws its own "
                        "where it is (Warcraft II, most strategy and adventure games; BCD tells it from mouse-look, "
                        "which recentres, confines or reads raw motion). Automatic (default): its cursor follows the "
                        "pointer's position while iPadOS does not lock the pointer (BCD not full screen, or a mouse "
                        "that reaches it through UIKit), where relative moves drifted away from the pointer; with the "
                        "lock, relative moves as before. Always (a game's own line): its hidden cursor always follows "
                        "the pointer, without the classification and instead of the automatic lock. Never: relative, "
                        "as before. The game's own file wins.",
                "choices": [("", "Automatic (default)"), ("1", "Always"), ("0", "Never")],
                "sources": ["app/Madeira/HardwareInput.swift"]},
    "env.MADEIRA_KEY_REPEAT": {"category": "Windows, display & input", "kind": "bool", "default": "0",
                "title": "Keyboard: repeat a held key",
                "note": "Off (default): a held key of a hardware keyboard goes down once (GameController never "
                        "repeats). 1: as on Windows, the key pressed last repeats after 0.5 s, 30 times a second, "
                        "until it is released (a held Backspace in a game's chat). The game's own file wins.",
                "sources": ["app/Madeira/HardwareInput.swift"]},
    "env.MADEIRA_HIDPAD": {"category": "Controllers",
                "note": "Set by the app at session start from env.MADEIRA_PAD_MODE (dualsense or generic) for the "
                        "wineserver and ntdll; not meant to be set by hand."},
    "env.MADEIRA_HIDPAD_NAME": {"category": "Controllers",
                "note": "Set by the app: the product string a generic HID gamepad reports (the physical pad's name)."},
    "env.MADEIRA_HIDPAD_XINPUT": {"category": "Controllers", "title": "HID mode: keep player 1 on XInput too", "kind": "bool",
                "default": "0",
                "note": "1: with the HID controller on, player 1 also stays an XInput pad. Off by default, so a game "
                        "that reads both APIs does not see the same pad twice.",
                "sources": ["app/Madeira/GamepadInput.swift"]},
    # ml2106: game output to the physical pad (app/Madeira/PadOutput.m).
    "env.MADEIRA_PAD_OUTPUT": {"category": "Controllers", "title": "Rumble, adaptive triggers and lightbar to the pad",
                "kind": "choice",
                "note": "On (default): XInput rumble plays on the controller (CoreHaptics), and in DualSense HID mode the "
                        "game's output reports drive rumble, adaptive triggers (closest GameController mode), lightbar "
                        "and player LEDs. hid: only the DualSense's; xinput: only XInput rumble; 0: none. Read at "
                        "session start; the game's own file wins.",
                "choices": [("", "On (default)"), ("hid", "DualSense output only"), ("xinput", "XInput rumble only"),
                            ("0", "Off")],
                "sources": ["app/Madeira/GamepadInput.swift", "app/Madeira/PadOutput.m"]},
    # madeira-bcd (2026-10-18): the iOS 18 gameplay audit's fixes
    # (docs/research/ios18-2026-10-18/gameplay.md). The switches are off by default.
    "env.MADEIRA_AUDIO_RESUME": {"category": "Audio", "title": "Bring game audio back after an interruption",
                "kind": "bool", "default": "0",
                "note": "1: after a phone call, Siri, an alarm, another app's sound or the app being suspended, BCD "
                        "activates the audio session again and restarts the output; while iOS holds the output "
                        "stopped, the game's audio clock keeps running, so a game timed by its audio does not stall. "
                        "Off (default): the sound stays off until the game is restarted, as before. Logged as "
                        "[audio] interruption began / ended, resumed=1. Read when the session starts.",
                "sources": ["build/ntdll-unix/audio_null_ios.c", "app/Madeira/WineProcessBridge.m"]},
    "env.MADEIRA_LUM_PROBE": {"category": "Direct3D 9/10/11 (DXMT)",
                "title": "Log GPU readback values", "kind": "bool", "default": "1",
                "note": "1 (default): the log names what the game reads back from the GPU ([rb-tex], [rb-buf], "
                        "[lum-readback]) with the values of small readbacks. 0: none of these lines. Builds before 575 "
                        "read a freed readback buffer there and ended the game (Batman: Arkham Asylum, Brutal Legend, "
                        "Dishonored); 0 avoids that on them. Read once per session.",
                "sources": ["tools/patch-winemetal-lum-probe.py", "tools/patch-winemetal-readback-census.py",
                            "tools/patch-winemetal-probe-safe-read.py"]},
    "env.MADEIRA_PAUSE_IN_BACKGROUND": {"category": "Direct3D 9/10/11 (DXMT)",
                "title": "Pause GPU work while BCD is not active", "kind": "bool", "default": "0",
                "note": "1: Metal command buffers and presents wait from the moment BCD stops being active until it is "
                        "active again, so iOS never refuses the game's GPU work from the background (\"Device error\", "
                        "\"refused while the app was in the background\") and textures streamed meanwhile are not lost. "
                        "The game halts at its next frame and goes on when BCD comes back. Direct3D 9/10/11 and 12 "
                        "alike. Off (default): as before. Logged as [gpu-pause].",
                "sources": ["app/Madeira/WineProcessBridge.m", "tools/patch-winemetal-pause-inactive.py"]},
    "env.MADEIRA_D3D12_CAPS_BY_FAMILY": {"category": "Direct3D 12",
                "title": "Report 64-bit atomics and wave operations by GPU", "kind": "bool", "default": "0",
                "note": "1: D3D12's answers follow the GPU: 64-bit atomics (OPTIONS9, OPTIONS11) only on Apple9 GPUs "
                        "(A17 Pro, A18, M3 and later), wave operations (OPTIONS1) only from Apple7 (A14, M1). For an "
                        "engine that picks shader paths the GPU cannot run (64-bit atomics on A14-A16 and M1/M2). Off "
                        "(default): both claimed on every GPU, as before; on Apple9 and later the answers are the same "
                        "either way.",
                "sources": ["madeira-d3d12/src/pe/madeira_d3d12.c"]},
    "env.MADEIRA_DEVICE_GATE": {"category": "App & front end", "title": "Refuse games this device cannot run",
                "kind": "bool", "default": "1",
                "note": "On (default): on a GPU with Tier 1 argument buffers (A12 and older: iPhone XS, XS Max, XR, "
                        "the A12 iPads, iPad 7th generation) a game is refused with a message, and so is a 64-bit "
                        "program on a CPU without LSE atomics or LDAPR (A10); the Desktop still starts. A13 and later "
                        "pass, A13 with a one-time notice (no geometry shaders or tessellation). 0: start them "
                        "anyway; Direct3D then crashes or draws garbage on such a GPU.",
                "sources": ["app/Madeira/DeviceCapability.swift"]},
    "env.MADEIRA_UPDATE_CHECK": {"category": "App & front end", "title": "Say when a newer BCD is out",
                "kind": "bool", "default": "1",
                "note": "On (default): at most once a day, a few seconds after the library first appears, BCD reads its "
                        "AltStore/SideStore source (thebcd69.github.io/bcd-games/source.json, about 7 KB) and, when the "
                        "newest release is a later build than this one, shows a small alert with OK over the library. "
                        "Nothing is blocked, downloaded or installed, and no game session is interrupted. 0: never ask.",
                "sources": ["app/Madeira/BCDUpdateCheck.swift", "app/Madeira/BCDUpdateNotice.swift"]},
    "env.MADEIRA_DOCK_GAME_END": {"category": "Steam & Dock", "title": "End a Dock session when the game crashed",
                "kind": "bool", "default": "1",
                "note": "On (default): a BCD Dock session whose game's last program ended with a Windows error (an "
                        "access violation, a stack overrun, an illegal instruction, a missing DLL, a breakpoint exit "
                        "or a negative exit code) ends: when Valve's client has counted the game over, the Dock host "
                        "has finished, nothing of the game runs and the grace time passed, BCD returns to the library "
                        "and says which Windows error the game closed with. It acts only after the game is already "
                        "dead; a game that quit with exit code 0 keeps the session as before. 0: the session stays "
                        "open on the dead desktop, as before.",
                "sources": ["app/Madeira/Library.swift", "app/Madeira/DockGameEnd.swift"]},
    "env.MADEIRA_DOCK_GAME_END_GRACE": {"category": "Steam & Dock", "title": "Seconds to wait before ending a Dock session after the game crashed",
                "kind": "int", "default": "10",
                "note": "Default 10 (3 to 300). How long the game's last program must have been gone, with nothing "
                        "of the game running, before a crashed game ends its BCD Dock session (env.MADEIRA_DOCK_GAME_END). "
                        "A launcher that starts the game again comes back within a second in the logs; a longer wait "
                        "only helps one that takes its time.",
                "sources": ["app/Madeira/Library.swift", "app/Madeira/DockGameEnd.swift"]},
    "env.MADEIRA_PAD_SYSTEM_GESTURES": {"category": "Controllers", "title": "Home and Create buttons straight to the game",
                "kind": "bool", "default": "0",
                "note": "1: for the session, a controller's Home button and its Create/Share (Options) button go to "
                        "the game at once; iOS otherwise binds them to its own gestures (the Games overlay, "
                        "screenshots, recordings) and passes them on late. iOS gets them back when the session ends. "
                        "Off (default): as before. The game's own file wins.",
                "sources": ["app/Madeira/SystemGestures.swift"]},
    "env.MADEIRA_DEFER_EDGES": {"category": "Controllers", "title": "Edge swipes reach the game first",
                "kind": "bool", "default": "0",
                "note": "1: while the on-screen controls are shown, a swipe from a screen edge goes to the game first, "
                        "and only a second swipe opens Notification Center or Control Center or goes Home, so a "
                        "thumb on a corner button stays in the game. Off (default): as before. Read once per app run.",
                "sources": ["app/Madeira/SystemGestures.swift"]},
    "env.MADEIRA_PROMOTE": {"title": "Hold the display at its maximum rate"},
    # madeira-bcd: GTA V Enhanced's Social Club (StikJITHelper.swift, process_ios.c, virtual_ios.c).
    "pool-split": {"category": "Memory & JIT pool", "title": "Split JIT pool around the main thread's stack",
                "kind": "bool", "default": "0",
                "note": "1: when the pool would shrink below its size (pool, 896 MB by default) because the main "
                        "thread's stack splits the only executable band, the free run above the stack becomes a "
                        "second debugger region and both form one pool (about 880 MB instead of 560-630 MB). Costs "
                        "the second region's size in memory. Off by default; read at launch, the game's own file "
                        "wins."},
    "pool-page-fit": {"category": "Memory & JIT pool", "title": "Fit split pool regions to memory pages",
                "kind": "bool", "default": "0",
                "note": "With pool-split on: fit regions A/B to 16 KB pages instead of 16 MB steps, retaining small "
                        "remainders in free runs without increasing the requested pool budget. With pool-low on, "
                        "also fit region C to pages while preserving pool-low-margin. Costs the additional pages in memory. Off by default; read at "
                        "launch, the game's own file wins. Restart Madeira and enable JIT again after changing it."},
    "pool-pair": {"category": "Memory & JIT pool", "title": "Split JIT pool: prefer two runs above the window",
                "kind": "bool", "default": "1",
                "note": "With pool-split on: when the largest free run lies below the 0x140000000 executable window "
                        "(where the pool cannot split), the pool takes two runs above the window instead if together "
                        "they are larger (GTA V: 368 + 320 MB instead of 464 MB). Falls back to the single run if the "
                        "placement misses. On by default; 0 turns it off. Read at launch, the game's own file wins."},
    "pool-mid": {"category": "Memory & JIT pool", "title": "Split JIT pool: use the free run between the regions",
                "kind": "bool", "default": "0",
                "note": "With pool-split on: when another mapping sits between the pool's two regions and leaves a "
                        "free run of 64 MB or more there, that run becomes a third debugger region in the same pool "
                        "(RDR2 build 470: 604 MB pool, a 71 MB mapping and 213 MB free between the regions; the "
                        "DLL copies ran out and the game failed with ERR_GFX_INIT). Only from what the two regions "
                        "left of the pool size; costs that region's size in memory. Off by default (RDR2's list turns "
                        "it on); read at launch, the game's own file wins.",
                "sources": ["app/Madeira/StikJITHelper.swift", "build/ntdll-unix/virtual_ios.c"]},
    "pool-low": {"category": "Memory & JIT pool", "title": "JIT code buffers below the executable window",
                "kind": "bool", "default": "0",
                "note": "1: a free run below the 0x140000000 executable window (less pool-low-margin) "
                        "becomes a third debugger region for the emulator's code buffers, so the whole JIT pool is "
                        "left to DLL copies (GTA V: about 300 MB more). Needs the pool above the window; costs the "
                        "region's size in memory. Off by default; read at launch, the game's own file wins."},
    "env.MADEIRA_TAIL_CARVE_EXEC_CHECK": {"category": "Memory & JIT pool",
                "title": "Refuse a code buffer that cannot execute", "kind": "bool", "default": "1",
                "note": "1 (default): a new emulator code buffer whose first 16 MB hold a JIT-pool page that lost "
                        "execute permission for good is refused, and the emulator asks for half the size elsewhere "
                        "(Dead Space, Star Wars: The Force Unleashed ended there). A buffer that can execute is "
                        "granted as before. 0: every buffer is granted, as in build 575 and earlier. Read once per "
                        "session.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_POOL_CLAMP_SECTIONS": {"category": "Memory & JIT pool",
                "title": "Keep a writable section inside its DLL copy", "kind": "bool", "default": "0",
                "note": "Default off. 1: the mprotect that makes a copied program's writable section (.data) "
                        "writable stops at the end of the copy. A 32-bit program whose section table claims 256 MB "
                        "(GTA Vice City's) otherwise made every JIT-pool page after the copy lose execute permission "
                        "for good, and the next DLL copy placed there could not run. Does nothing for a normal "
                        "image. Off by default because LEGO Batman 2 runs with the dead run in place. Read once "
                        "per session.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "exe-window-mb": {"category": "Memory & JIT pool", "title": "Room for a fixed-address program (MB)",
                "kind": "int", "default": "128",
                "note": "A program whose relocations are stripped runs only at 0x140000000; BCD keeps 128 MB there. "
                        "A larger one (Hogwarts Legacy 445 MB, Star Wars Galactic Racer 480 MB) ran into the JIT pool "
                        "and failed to start (\"failed to create main module ... c0000018\"). Up to 1024: the room "
                        "grows to this size before the pool is placed, so the pool may be smaller. 128 (default): as "
                        "before. Read when the JIT pool is made (the first game after BCD starts). Auto-tune adds 512 "
                        "after that failure.",
                "sources": ["app/Madeira/StikJITHelper.swift"]},
    "pool-low-margin": {"category": "Memory & JIT pool", "title": "Code-buffer region: MB left free below the window",
                "kind": "int", "default": "128",
                "note": "With pool-low on: how much of the free run below the executable window stays free for "
                        "programs that load there (child processes' main executables). A run with a free run at "
                        "least this large below it keeps none. 128 by default."},
    "env.MADEIRA_SC_CEF": {"category": "Wine core (ntdll)", "title": "Social Club's Chromium in one process",
                "kind": "bool", "default": "1",
                "note": "On by default; 0 turns it off. SocialClubHelper.exe runs --single-process with "
                        "PartitionAllocBackupRefPtr disabled and V8 --jitless (MADEIRA_JITLESS = 0 keeps V8's JIT), its "
                        "--type= children are refused, and a process with socialclub.dll mapped that is not the "
                        "helper (the game) gets no JIT-pool copy of libcef.dll: its load fails, as it did when the "
                        "pool was full. No effect on programs without Social Club."},
    "env.MADEIRA_SC_CEF_FLAGS": {"category": "Wine core (ntdll)", "title": "Extra SocialClubHelper.exe switches",
                "note": "Appended verbatim to SocialClubHelper.exe's command line while env.MADEIRA_SC_CEF is on, "
                        "e.g. --disable-gpu or --enable-logging=file --v=1. A --js-flags= in it gets --jitless as its "
                        "first flag (Chromium keeps only the last --js-flags=), unless MADEIRA_JITLESS = 0."},
    "env.MADEIRA_SC_PA_POOLS": {"category": "Memory & JIT pool", "title": "Room for Social Club's PartitionAlloc pools",
                "kind": "choice", "default": "",
                "choices": [("", "Off"), ("1", "Layout 1 (chrome_elf.dll only)"), ("2", "Layout 2 (chrome_elf, libcef, Oilpan)")],
                "note": "1: Wine boots with the emulator's arena at 0x7d00000000 (12 GB instead of 16 GB) and keeps "
                        "0x7c00000000 +4 GB free, so SocialClubHelper.exe's 32 GB PartitionAlloc reservation, which "
                        "must start on a 32 GB boundary, gets 0x7800000000 (20 GB of it really reserved). 2: also "
                        "libcef.dll's (0x7000000000) and Oilpan's; the JIT pool's RW alias moves to 0x7900000000. "
                        "Off by default; set it in the game's own file; read at session start."},
    # madeira-bcd: the opt-in source build of DXMT's 64-bit dxgi.dll (tools/build-dxgi-dll.sh).
    "env.MADEIRA_DXGI_SRC": {"category": "Direct3D 9/10/11 (DXMT)", "title": "DXGI built from DXMT source (IDXGIFactory7)",
                "kind": "bool", "default": "0",
                "note": "1: the game runs the 64-bit dxgi.dll the CI builds from the dxmt submodule (dxgi-src.dll): "
                        "upstream's DXMT dxgi plus IDXGIFactory7 and EnumAdapterByLuid (GTA V Enhanced stops with "
                        "ERR_GFX_D3D_NOD3D12 without Factory7). Off (default): upstream's committed dxgi.dll. Set it in "
                        "the game's own file, not for every game; read at session start."},
    "avail-phys": {"category": "Memory & JIT pool", "title": "Report the memory left before the app's limit as available",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: Windows programs' available physical memory (GlobalMemoryStatusEx) is what "
                        "remains before Madeira's memory limit instead of the phone's free memory, so Chromium "
                        "(Social Club, Steam) frees caches below 1000 MB and 400 MB left, and a game can see memory "
                        "running out. Set it in the game's own file; read at session start."},
    # madeira-bcd: the memory before the game (2026-10-09, virtual_ios.c ios_avail_phys / ios_swap_image_flush).
    "avail-phys-cef-mb": {"category": "Memory & JIT pool", "title": "Available memory Chromium sees (MB)",
                "kind": "int", "default": "0",
                "note": "0 (default): off. N: Social Club's SocialClubHelper.exe and Steam's steamwebhelper.exe see at "
                        "most N MB of available physical memory, nobody else (the game and the launcher keep the real "
                        "figure). Below 400 (e.g. 300) Chromium reports critical memory pressure every 5 s and purges "
                        "caches and garbage; 400-999 (e.g. 900), moderate every 10 s. Independent of avail-phys. Set it "
                        "in the game's own file; read at session start."},
    "swap-images": {"category": "Memory & JIT pool", "title": "Swap tier: pure-x64 DLLs and programs in the file",
                "kind": "bool", "default": "0",
                "note": "Default off. 1: the sections of pure-x64 images (with env.MADEIRA_X64_IMAGE_NOCOPY = 1: "
                        "libcef.dll, steamclient64.dll, launchers, the game exe) move to the swap tier's file once they "
                        "are read, so they stop counting against the memory limit (iOS writes them back and may drop "
                        "them). Needs swap-mb. Page edges shared with a neighbouring section stay in memory. Set it in "
                        "the game's own file; read at session start."},
    # madeira-bcd: budget-change events in dxgi-src.dll (tools/patch-dxgi-budget-events.py).
    "env.MADEIRA_DXGI_BUDGET_EVENTS": {"category": "Direct3D 9/10/11 (DXMT)",
                "title": "Tell the game when its video memory budget changes",
                "kind": "bool", "default": "0", "sources": ["tools/patch-dxgi-budget-events.py"],
                "note": "Needs env.MADEIRA_DXGI_SRC = 1. The video memory budget DXGI reports shrinks as Madeira "
                        "nears its memory limit (vram-trim-mb below it). 1: a game's budget-change event is "
                        "registered for real, signalled at once and again whenever the budget moves by 32 MB, so a "
                        "game that waits for it (Red Dead Redemption 2) reads the smaller budget and frees textures. "
                        "Off (default): the registration is refused, as before. Set it in the game's own file; read "
                        "at session start."},
    # madeira-bcd: the opt-in source build of DXMT's 64-bit d3d11.dll (tools/build-d3d11-dll.sh).
    "env.MADEIRA_D3D11_SRC": {"category": "Direct3D 9/10/11 (DXMT)", "title": "D3D11 built from DXMT source (context state swap)",
                "kind": "bool", "default": "0",
                "note": "1: the game runs the 64-bit d3d11.dll the CI builds from the dxmt submodule (d3d11-src.dll): "
                        "upstream's DXMT d3d11 plus SwapDeviceContextState, which Wine's Direct2D (d2d1) calls and "
                        "upstream's aborts in (Rockstar Games Launcher exited with code 3). Off (default): upstream's "
                        "committed d3d11.dll. Set it in the game's own file, not for every game; read at session start. "
                        "Since build 543 (the 2026-10-15 upstream merge) upstream's d3d11.dll has SwapDeviceContextState "
                        "itself, no d3d11-src.dll is built and this switch changes nothing."},
    # madeira-bcd: the wineserver names an opened file with F_GETPATH (build/wineserver/fd_ios.c).
    "env.MADEIRA_SRV_FGETPATH": {"category": "Wine core (ntdll)", "title": "Name opened files with one call (server)",
                "kind": "bool", "default": "0",
                "sources": ["app/Madeira/WineProcessBridge.m", "build/wineserver/fd_ios.c"],
                "note": "1: the Wine server takes the path of a file it has just opened from its descriptor "
                        "(fcntl F_GETPATH) instead of realpath, which checks the path one folder at a time "
                        "(~0.5 ms a file on the device). Same resolved path; realpath stays the fallback. For "
                        "games that open many files a second (Horizon Zero Dawn's shader cache in its menu). "
                        "Off (default): realpath as before. Read at each launch; a later game of the same app run does not "
                        "inherit it."},
    "env.MADEIRA_SRV_DIRECT": {"category": "Wine server", "title": "Run simple server requests on the calling thread",
                "kind": "text", "default": "0",
                "choices": [("", "Off (default)"), ("1", "Files, handles, registry, events, semaphores"),
                            ("2", "Also waits, timers and the cursor")],
                "sources": ["build/wineserver/request_ios.c", "build/wineserver/fd_ios.c", "build/ntdll-unix/server_ios.c"],
                "note": "Default off. 1: the Wine server's simplest requests (opening files and their descriptors, "
                        "closing handles, mappings looked up, registry keys and values, events, semaphores, mutexes, "
                        "completion ports) run on the thread that asks, under the server's lock, instead of a round "
                        "trip through a pipe to the server thread: no thread switches, and the work runs on the asking "
                        "thread's core. 2: waits (select), timers and set_cursor too. Every other request goes through "
                        "the pipe as before. Horizon Zero Dawn's menu sends ~7000 requests a second. Read at each launch."},
    "env.MADEIRA_SRV_ECO_FOLLOW": {"category": "Wine server", "title": "Server thread follows ECO",
                "kind": "bool", "default": "0",
                "sources": ["build/wineserver/fd_ios.c", "app/Madeira/WineProcessBridge.m"],
                "note": "Default off. 1: while ECO is on, the Wine server thread runs at utility QoS (ECO's default "
                        "class) instead of user-interactive, so it moves to the efficiency cores with the game's "
                        "threads, and back when ECO goes off. In Horizon Zero Dawn's menu with "
                        "eco = 1 the server was the only thread left on a performance core (0.54 cores at ~3.9 GHz). "
                        "Server requests may take longer. Read at each launch."},
    "env.MADEIRA_SRV_IDLE_US": {"category": "Wine server", "title": "Server loop's longest sleep (us)",
                "kind": "int", "default": "1000",
                "sources": ["build/wineserver/fd_ios.c", "app/Madeira/WineProcessBridge.m"],
                "note": "Default 1000 (1 ms, as before); 100 to 50000. Requests and timers wake the Wine server at "
                        "once; only its socket polls and fd scans wait for this tick, so a longer one means fewer "
                        "server wake-ups while idle (1000 a second at the default) and slower readiness of network "
                        "sockets and pipes. Mostly useful with env.MADEIRA_SRV_DIRECT. Read at each launch."},
    "env.MADEIRA_SRV_FD_SOCKET": {"category": "Wine server", "title": "File descriptors through the process socket",
                "kind": "bool", "default": "0",
                "sources": ["build/ntdll-unix/server_ios.c", "build/wineserver/fd_ios.c"],
                "note": "Kill switch, default 0: a file's descriptor comes back in the server's get_handle_fd reply "
                        "(a copy the server makes; it shares this process's descriptor table), the same descriptor "
                        "without a sendmsg, a recvmsg, an fcntl and two server log lines per file. 1: through the "
                        "process socket, as before. Read once per app run."},
    "env.MADEIRA_FILE_INFO_FULL": {"category": "Wine core (ntdll)", "title": "File size queries read attributes too",
                "kind": "bool", "default": "0", "sources": ["tools/patch-wine-file-info-fstat.py"],
                "note": "Kill switch, default 0: a file's size, index or end-of-file query (GetFileSizeEx and the "
                        "like) is answered from one fstat; the two extended-attribute reads that came after it fed "
                        "only file attributes, which these queries do not return. 1: as before. Read once per app run."},
    "env.MADEIRA_SRV_LOG_ALL": {"category": "Wine server", "title": "Every per-descriptor server log line",
                "kind": "bool", "default": "0",
                "sources": ["build/wineserver/fd_ios.c", "build/wineserver/request_ios.c"],
                "note": "Kill switch, default 0: the Wine server's add_poll_user and send_client_fd log lines (one per "
                        "opened file or descriptor sent, each a formatted write to the log file on the server thread) "
                        "are written for their first 64 and then every 1000th; a failed send is always written. 1: "
                        "every line, as before. Read at each launch."},
    # madeira-bcd: WM_TIMER not starved by WM_PAINT (build/wineserver/queue_ios.c).
    "env.MADEIRA_TIMER_FAIRNESS": {"category": "Wine server", "title": "Timers run while a window keeps repainting",
                "kind": "bool", "default": "1", "sources": ["build/wineserver/queue_ios.c"],
                "note": "Default on. A thread gets its paint messages before its timers, so a window that "
                        "invalidates itself while it paints starved every timer of that thread for good: the "
                        "Rockstar Games Launcher installer, whose status text and finish page run on a timer, "
                        "stayed at \"Preparing to Install...\" after its work was done. A timer that has waited "
                        "100 ms now goes before such a paint. 0: the strict order, as before. Read once per app run."},
    # madeira-bcd: the yield primitive (tools/patch-wine-yield-mode.py, wine/dlls/ntdll/unix/sync.c).
    "yield-mode": {"category": "Synchronisation", "title": "Yield primitive (SwitchToThread, Sleep(0))",
                "kind": "choice", "default": "", "sources": ["tools/patch-wine-yield-mode.py"],
                "choices": [("", "sched_yield (default)"), ("switch", "Switch, no priority drop"),
                            ("depress", "Bounded priority drop")],
                "note": "What a game thread's yield does. Default: sched_yield, which on iOS drops the thread to the "
                        "lowest priority until it runs again or 10 ms pass. switch hands the core to another "
                        "runnable thread and keeps the yielder's priority; depress keeps the drop but ends it after "
                        "yield-depress-ms. An experiment for games whose threads wait in yield loops (Horizon Zero "
                        "Dawn: 1-2.6 thousand yields a second, 14-18 % of runnable-thread samples in the yield "
                        "trap under the power clamp). The adaptive sleep (yield-streak, yield-sleep-us) still "
                        "comes first. Read once per app run."},
    "yield-depress-ms": {"category": "Synchronisation", "title": "Yield priority drop (ms)",
                "kind": "int", "default": "1", "sources": ["tools/patch-wine-yield-mode.py"],
                "note": "yield-mode = depress only: how long a yielding thread stays at the lowest priority at "
                        "most, 1-10 ms (default 1; sched_yield's drop lasts up to 10 ms)."},
    # madeira-bcd: ntdll-sync.dll (tools/patch-wine-ntdll-sync.py), mapped by build/ntdll-unix/loader_ios.c.
    "env.MADEIRA_NTDLL_SYNC": {"category": "Wine core (ntdll)", "title": "Cheaper locks (ntdll-sync.dll)",
                "kind": "choice", "default": "",
                "sources": ["build/ntdll-unix/loader_ios.c", "app/Madeira/WineProcessBridge.m",
                            "tools/patch-wine-ntdll-sync.py"],
                "choices": [("", "Off (default)"), ("1", "On"), ("native", "On, native lock code only")],
                "note": "Off by default since build 499: on build 498, where it was on, every 64-bit game died at "
                        "start (ARM64EC code reached the x64 lock bodies through kernel32's forwarders, and their "
                        "exit thunk had no dispatcher yet). Kept for that fix; do not turn it on for a game yet. "
                        "1: 64-bit sessions run ntdll-sync.dll, Wine's ntdll from the same source as the shipped "
                        "ntdll.dll (the build checks that it rebuilds it byte for byte) with cheaper SRW locks and "
                        "critical sections: same lock word and rules, but a release wakes only a waiter the word "
                        "records, no shared bookkeeping on every lock call, and a 64-bit game's uncontended lock "
                        "calls stay in the emulator (Horizon Zero Dawn: about a million SRW and 0.13 million "
                        "critical-section calls a second). native keeps the 64-bit callers on the native lock code "
                        "(one switch out of the emulator per call, as before). Unset or 0: the shipped ntdll.dll. "
                        "Read at session start; a later game of the same app run does not inherit it."},
    # madeira-bcd: ucrtbase-x64math.dll (tools/patch-wine-ucrtbase-x64-math.py, linked by WineProcessBridge.m).
    "env.MADEIRA_UCRT_X64_MATH": {"category": "Wine core (ntdll)", "title": "floor and sqrt as x64 code (ucrtbase)",
                "kind": "bool", "default": "0",
                "sources": ["app/Madeira/WineProcessBridge.m", "tools/patch-wine-ucrtbase-x64-math.py"],
                "note": "1: the game runs ucrtbase-x64math.dll as ucrtbase.dll, Wine's ucrtbase whose floor and sqrt "
                        "exports are x64 code, so a 64-bit game's calls to them stay in the emulator instead of "
                        "crossing to the native code and back (Horizon Zero Dawn: 2-4 million such calls a second). "
                        "Same results; a negative or NaN sqrt still goes to Wine's native sqrt. Off (default): the "
                        "shipped ucrtbase.dll. Set it in the game's own file; read at session start."},
    # madeira-bcd: ios_vcrt_arm64_key_publish (build/ntdll-unix/server_ios.c).
    "env.MADEIRA_VCRT_ARM64_KEY": {"category": "Wine core (ntdll)", "title": "ARM64 Visual C++ runtime marker (Unreal bootstrap)",
                "kind": "text", "default": "",
                "sources": ["build/ntdll-unix/server_ios.c"],
                "note": "1: the session has the ARM64 Visual C++ 2015-2022 redistributable's \"Installed\" marker, "
                        "HKLM\\SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\arm64 (also under Wow6432Node), "
                        "with version 14.44.35211.0, or the VC++ runtime's own version when that is newer; a version such as "
                        "14.50.35717 writes that one. The keys are "
                        "volatile (not saved with the prefix) and a key the prefix already has is left alone. For Unreal "
                        "Engine's bootstrap program (Denshattack_Proto.exe, FFRS.exe), an x64 program that sees an ARM64 "
                        "machine and stops with \"Microsoft Visual C++ 2015-2022 Redistributable (arm64)\" (exit "
                        "0x2329). Off (default): nothing written. Set it in the game's own file; read at session start. "
                        "Not tried on a device yet."},
    # madeira-bcd: kernelbase-fibertrace.dll (tools/patch-wine-kernelbase-fibertrace.py, linked by WineProcessBridge.m).
    "env.MADEIRA_DXMT_FAULT_INFO": {"category": "Debugging / logs", "title": "Name the DXMT encoder of a GPU fault or hang",
                "kind": "bool", "default": "0",
                "sources": ["tools/patch-dxmt-d3d11-fault-info.py"],
                "note": "1: DXMT's command buffers are created with encoder execution status and every render, compute "
                        "and blit encoder gets a label (rp#<seq> <w>x<h> cmds=<n> gs=<0|1> ts=<0|1> ...); after "
                        "\"Device error at frame N\" a [fault-enc] line names the faulted, affected and unknown encoders. "
                        "For D3D9/10/11 games that hang or fault the GPU (GTA V Legacy at Story Mode, build 546). A "
                        "diagnostic with some GPU cost; off (default): unchanged. Set it in the game's own file."},
    "env.MADEIRA_WG_64BIT": {"category": "Media", "title": "BCD's media decoders for a 64-bit game",
                "kind": "bool", "default": "0",
                "sources": ["build/ntdll-unix/virtual_ios.c", "build/ntdll-unix/winegstreamer_unixlib_ios.c",
                            "build/ntdll-unix/winegstreamer_wmv_ios.c", "build/ntdll-unix/winedmo_unixlib_ios.c"],
                "note": "Default off. 1: winegstreamer.dll in this (64-bit) game runs on BCD's own unix side, which "
                        "decodes WMA and, with the DLLs of env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod, "
                        "Windows Media Video (wmv1, wmv2, wmv3, VC-1) from an ASF file: Resident Evil 2's cutscenes, "
                        "which ended the game at its first one (build 579). 32-bit games have it already; without "
                        "the line a 64-bit game keeps the stub table (every media call answers STATUS_NOT_SUPPORTED). "
                        "Set it in the game's own file; read at session start. Logged as [wmv] and [winedmo] lines."},
    "env.MADEIRA_OPTIN_DLLS": {"category": "Wine libraries", "title": "Give the game more of Wine's DLLs",
                "kind": "text", "default": "",
                "sources": ["app/Madeira/WineProcessBridge.m", "tools/build-wine-extra-dlls.sh",
                            "tools/patch-wine-setupapi-devinst-lock.py", "tools/patch-wine-mfasfsrcsnk-winedmo.py"],
                "note": "A comma-separated list (dxva2, evr, xpsprint, sfc, sfc_os, tbs, twinapi.appcore, setupapi): each named DLL, which BCD ships as "
                        "<name>-optin.dll and never for every game, runs as <name>.dll for this game. For a program that "
                        "imports one and exits at once with c0000135 (\"Library dxva2.dll ... not found\"; Fort Solis, "
                        "DRAGON BALL Sparking! ZERO; sfc,sfc_os,tbs for Diablo II: Resurrected, sfc.dll forwards to sfc_os.dll; twinapi.appcore for Senua's Saga: Hellblade II, whose XCurl.dll imports api-ms-win-core-psm-appnotify-l1-1-0.dll, which Wine maps to it). Programs that load these DLLs optionally keep running without them. "
                        "setupapi is the one name BCD already ships for every game: setupapi-optin.dll is the same DLL "
                        "with a lock round its list of device nodes, for a game whose controller library and main thread "
                        "list devices at the same moment (Dying Light crashed in setupapi.dll on build 576); without the "
                        "line the shipped setupapi.dll runs. mfasfsrcsnk, wmvdecod and wmadmod are Wine's ASF byte stream handler "
                        "and the WMV and WMA decoders behind it, for a program that plays Windows Media Video through "
                        "Media Foundation (Resident Evil 2's cutscenes: c0000135 for mfasfsrcsnk.dll); they need "
                        "env.MADEIRA_WG_64BIT = 1 as well, which gives a 64-bit game BCD's decoders. "
                        "oledlg, fwpuclnt and sensapi are three small system DLLs the 64-bit set lacked (the 32-bit set has them): a program "
                        "that imports oledlg.dll (Digimon Story Cyber Sleuth), fwpuclnt.dll (FINAL FANTASY XV) or SensApi.dll (Borderlands 3's voice "
                        "library, whose missing import raised UE4's delay-load exception) ended at once with c0000135. An earlier session's links are removed when a name is gone. "
                        "Set it in the game's own file; read at session start. Auto-tune adds dxva2, xpsprint, oledlg, fwpuclnt, sensapi, "
                        "setupapi and (with env.MADEIRA_WG_64BIT = 1) mfasfsrcsnk,wmvdecod,wmadmod after their failures."},
    "env.MADEIRA_WINTYPES": {"category": "Wine libraries", "title": "Give the game Wine's wintypes.dll",
                "kind": "bool", "default": "0",
                "sources": ["app/Madeira/WineProcessBridge.m", "tools/build-wine-extra-dlls.sh"],
                "note": "1: the game runs wintypes-optin.dll as wintypes.dll, Wine's own Windows Runtime types "
                        "library, for a program that imports it (or api-ms-win-core-winrt-robuffer-l1-1-0.dll, which "
                        "maps to it) and exits at once with c0000135: Unity IL2CPP games such as Megabonk. BCD ships "
                        "no 64-bit wintypes.dll for every game: Steam's web helper loads it optionally and works "
                        "without it. An earlier session's link is removed when the line is gone. Set it in the "
                        "game's own file; read at session start. Auto-tune adds it after that failure."},
    "env.MADEIRA_X64_MACHINE": {"category": "Wine core (ntdll)", "title": "Tell 32-bit programs the machine is x64",
                "kind": "bool", "default": "0", "sources": ["tools/patch-wine-x64-machine.py"],
                "note": "1: a 32-bit program that asks which machine is native (IsWow64Process2, the PROCESSOR_ARCHITEW6432 "
                        "variable) hears AMD64 instead of ARM64. For BeamNG.drive's launcher, which on an ARM64 answer starts "
                        "its native ARM64 build (BinArm64) and ends with c0000135 (D3DCOMPILER_47.dll, which BCD's native "
                        "ARM64 set does not have); on an AMD64 answer it starts the x64 build. Programs that never ask see "
                        "no change, and what WoW64 itself reads is unchanged. Set it in the game's own file; read at "
                        "session start. Auto-tune adds it after that failure. [x64-machine] in the log names each "
                        "answer given."},
    "env.MADEIRA_TINY_WINDOW_FIX": {"category": "Windows, display & input", "title": "Size a tiny game window to the screen",
                "kind": "bool", "default": "0", "sources": ["build/win32u-unix/driver_ios.c"],
                "note": "1: a window a Direct3D swapchain presents into, whose client area stays under 160x120 for 3 seconds while "
                        "the swapchain draws 320x200 or more into it, is sized to the virtual screen (at most twice per window). "
                        "American Truck Simulator opens as a 132x38 window that way (its picture is 1024x768) and nothing ever "
                        "sizes it; on Windows a fullscreen swapchain does, and from build 578 so does DXMT's fullscreen path "
                        "(a client area under 160x120, no switch); this line covers a swapchain that never asks for "
                        "fullscreen. Needs a desktop (Dock) session. Without it the log says [tiny-win] once and nothing "
                        "changes. [win-create] in the log shows what size each program asked its window for. Set it in the "
                        "game's own file; read at session start."},
    "env.MADEIRA_WER": {"category": "Wine libraries", "title": "Give the game Wine's wer.dll",
                "kind": "bool", "default": "0",
                "sources": ["app/Madeira/WineProcessBridge.m", "tools/build-wine-extra-dlls.sh"],
                "note": "1: the game runs wer-optin.dll as wer.dll, Wine's own Windows Error Reporting library, for "
                        "a program that imports it and exits at once with c0000135 (\"Library wer.dll ... not found\"; "
                        "Hello Neighbor, an Unreal Engine 4 game). BCD ships no wer.dll for every game: Wine's has "
                        "68 unimplemented exports, so only a game with this line gets it, and an earlier session's "
                        "link is removed when the line is gone. Set it in the game's own file; read at session start. "
                        "Auto-tune adds it after that failure."},
    "env.DOTNET_EnableWriteXorExecute": {"category": "Memory & JIT pool", "title": "Write-xor-execute memory for .NET 7+ games",
                "kind": "bool", "default": "1",
                "sources": ["build/tools/autotune_rules.py"],
                "note": "Read by the game's own .NET runtime (self-contained .NET 7 and later: hostfxr.dll, coreclr.dll), "
                        "not by BCD. On (the runtime's default): the runtime keeps its code in two views of one memory "
                        "section, one it writes and one it runs. BCD gives the executable view a separate copy, so the "
                        "two differ and the runtime stops with \"CLR: Assert failure ... m_pRegionToGenTableImmediate\" "
                        "(exit c0000602; Quaver). 0: the runtime uses plain executable memory, which BCD serves as it "
                        "serves Wine Mono's JIT. Set it in the game's own file; read when the game starts. Auto-tune "
                        "adds it after that failure."},
    "env.DOTNET_GCRegionRange": {"category": "Memory & JIT pool", "title": "Address range of a .NET game's garbage collector heap",
                "kind": "text", "default": "",
                "sources": ["build/tools/autotune_rules.py"],
                "note": "Read by the game's own .NET runtime (.NET 7 and later, 64-bit), not by BCD. The size, in bytes and "
                        "in hexadecimal, of the virtual address range its garbage collector reserves up front for the heap "
                        "(reserve only, nothing is committed); empty: the runtime's default of 256 GB. No 256 GB range fits "
                        "BCD's address map, whose largest free gap is 32 GB, so the runtime stops at start with \"GC heap "
                        "initialization failed with error 0x8007000E\" (Unrailed! 2: Back on Track). 400000000 is 16 GB: "
                        "Microsoft's documented advice for this failure is two to five times the heap's committed size, or "
                        "two times the physical memory the process has when that is unknown (BCD reports 8 GB at most), "
                        "and the range fits the 32 GB gap. A range too small ends in an out-of-memory error once the heap "
                        "outgrows it. Set it in the game's own file; read when the game starts. Auto-tune adds it after "
                        "that failure."},
    "env._JAVA_OPTIONS": {"category": "Memory & JIT pool", "title": "Options of a Java game's runtime (JVM)",
                "kind": "text", "default": "",
                "sources": ["build/tools/autotune_rules.py"],
                "note": "Read by the game's own Java runtime, not by BCD (after the command line, so it wins). -XX:-UseZGC turns "
                        "ZGC off: that collector reserves its heap at a fixed base of 4 TiB and up, which BCD's 512 GB address "
                        "map does not have, so Project Zomboid's JVM exits at start after 12,230 refused reservations; the "
                        "default collector reserves its heap low. Set it in the game's own file; read when the game starts. "
                        "Auto-tune adds it after that failure."},
    "env.MADEIRA_FIBER_TRACE": {"category": "Debugging / logs", "title": "Log the fiber calls (kernelbase)",
                "kind": "bool", "default": "0",
                "sources": ["app/Madeira/WineProcessBridge.m", "tools/patch-wine-kernelbase-fibertrace.py"],
                "note": "1: the game runs kernelbase-fibertrace.dll as kernelbase.dll, Wine's kernelbase that logs "
                        "[fiber-trace] lines: each fiber created, converted or deleted, a summary every 2 s (fibers "
                        "running, parked, never run; switches; overlapped reads and completion-port entries), each "
                        "fiber parked for 3 s or more with the x64 calls on its stack, wrong switches, and every "
                        "fiber at an unhandled exception. For games that stop with a fiber error (Microsoft Flight "
                        "Simulator 2024: \"FiberError\"). Also, fibers or not, an unhandled C++ exception's thrown "
                        "types, what() for a std::exception, the object's vftable and the x64 calls that threw it "
                        "(a game whose own crash handler says \"Unknown exception\"). A diagnostic; off (default): the shipped kernelbase.dll. "
                        "Set it in the game's own file; read at session start."},
    # madeira-bcd: lean periodic diagnostics (build/ntdll-unix/server_ios.c, virtual_ios.c).
    "env.MADEIRA_LEAN_DIAG": {"category": "Debugging / logs", "title": "Lean diagnostics (no thread-suspending samplers)",
                "kind": "bool", "default": "0",
                "sources": ["build/ntdll-unix/server_ios.c", "build/ntdll-unix/virtual_ios.c"],
                "note": "1: the samplers that suspend threads stop ([thread-sample] bursts every 20 s, the guest RIP "
                        "profile behind [rip-profile] and [cpu-split], [wprof]); the warmer's address-space walks "
                        "([phys-map], slot and window inventory) run every ~10 min instead of every minute, its zone "
                        "check and allocation probe every minute instead of every 2 s; mprotect_exec's per-call ERR "
                        "line is kept for the first 200 calls, then every 1000th. [xp], [xp-t], [xp-api] and "
                        "[sync-census] keep their rates. Off (default): every diagnostic as before. Read at session start."},
    # madeira-bcd: the runtime's native helper threads at utility QoS (build/ntdll-unix/thread_ios.c).
    "env.MADEIRA_ECO_NATIVE": {"category": "Wine core (ntdll)", "title": "Runtime helper threads on efficiency cores",
                "kind": "bool", "default": "0",
                "sources": ["build/ntdll-unix/thread_ios.c", "build/ntdll-unix/virtual_ios.c",
                            "build/ntdll-unix/signal_arm64_ios.c"],
                "note": "1: the JIT-pool warmer (page warming and the periodic memory walks) and the stale-pointer "
                        "healer run at utility QoS, on the efficiency cores, ECO on or off; neither does work a frame "
                        "waits for. The wineserver thread, the exception thread, audio and the interface keep their "
                        "QoS. Off (default): they keep the QoS they were created with. Read at session start."},
    # madeira-bcd: the power probe (build/ntdll-unix/server_ios.c ios_power_probe_tick).
    "env.MADEIRA_POWER_PROBE": {"category": "Debugging / logs", "title": "Log GPU and device power every 5 s",
                "kind": "bool", "default": "0",
                "sources": ["build/ntdll-unix/server_ios.c"],
                "note": "1: every 5 s a [power] line next to [xp]'s CPU energy: the GPU energy and time the driver "
                        "charged to the app's coalition, the SoC's Energy Model counters (IOReport) and the battery's "
                        "voltage and current (the whole device while unplugged), each source named unavailable once "
                        "when the system gives nothing; and [power-t], the threads that used the most energy. Off "
                        "(default): nothing is read. Needs the [xp] sampler. Read at session start."},
    # madeira-bcd: the frame-cap power governor (build/ntdll-unix/server_ios.c ios_fps_gov_step,
    # madeira-d3d12 Present, tools/patch-winemetal-fps-cap.py).
    "env.MADEIRA_FPS_CAP": {"category": "Windows, display & input", "title": "Fixed frame cap (fps)",
                "kind": "int", "default": "",
                "sources": ["build/ntdll-unix/server_ios.c", "madeira-d3d12/src/pe/madeira_d3d12.c",
                            "tools/patch-winemetal-fps-cap.py"],
                "note": "Unset (default): no cap beyond the FPS limit. 10-120: frames start at most N times a second for "
                        "the whole session: a Direct3D 12 game's thread waits after each Present, a DXMT game's encode "
                        "thread before each present (a timed kernel wait, not a spin). Any value, e.g. 20 or 24, unlike "
                        "the FPS limit's 30/40/60; the lower of the two applies. While it is set, env.MADEIRA_FPS_GOV_MW "
                        "is ignored. Needs the [xp] sampler (MADEIRA_PROBES not 0). Read at session start."},
    "env.MADEIRA_FPS_GOV_MW": {"category": "Windows, display & input", "title": "Hold CPU power with a frame cap (mW)",
                "kind": "int", "default": "",
                "sources": ["build/ntdll-unix/server_ios.c", "madeira-d3d12/src/pe/madeira_d3d12.c",
                            "tools/patch-winemetal-fps-cap.py"],
                "note": "Unset (default): off. N: while the game presents, the process's CPU power ([xp]'s energy) is "
                        "held at N mW on average by moving a frame cap between 60, 40, 30, 24 and 20 fps (at most one "
                        "step per 3 s; held while nothing presents). Unlike env.MADEIRA_POWER_GOV_MW it moves no thread "
                        "to the efficiency cores. Menus and loading at 3-4 W fill the budget after which iOS clamps the "
                        "CPU; e.g. 1500 keeps a 60 fps menu near 30-40 fps. [fps-gov] lines every 10 s. "
                        "env.MADEIRA_FPS_GOV_MIN / _MAX (20 / 60) bound the cap, _BAND_MJ (1500) is the energy the "
                        "average may drift before it moves. Needs the [xp] sampler. Read at session start."},
    "env.MADEIRA_FPS_GOV_MAX": {"category": "Windows, display & input", "title": "Frame-cap governor: highest cap (fps)",
                "kind": "int", "default": "60", "sources": ["build/ntdll-unix/server_ios.c"],
                "note": "Only with env.MADEIRA_FPS_GOV_MW set: the highest frame cap the governor uses, the largest of "
                        "60, 40, 30, 24 and 20 at or below this value (20 when lower). Unset: 60. Read at session start."},
    "env.MADEIRA_FPS_GOV_MIN": {"category": "Windows, display & input", "title": "Frame-cap governor: lowest cap (fps)",
                "kind": "int", "default": "20", "sources": ["build/ntdll-unix/server_ios.c"],
                "note": "Only with env.MADEIRA_FPS_GOV_MW set: the lowest frame cap the governor goes down to, the "
                        "smallest of 60, 40, 30, 24 and 20 at or above this value, never above the highest cap. "
                        "Unset: 20. Read at session start."},
    "env.MADEIRA_FPS_GOV_BAND_MJ": {"category": "Windows, display & input", "title": "Frame-cap governor: energy band (mJ)",
                "kind": "int", "default": "1500", "sources": ["build/ntdll-unix/server_ios.c"],
                "note": "Only with env.MADEIRA_FPS_GOV_MW set: how much energy the running balance may drift above or "
                        "below the target before the cap moves one step (100-100000; unset or out of range: 1500). "
                        "Smaller moves the cap sooner and more often. Read at session start."},
    # madeira-bcd: small staging rings in d3d11-src.dll (tools/patch-d3d11-src-small-rings.py).
    "env.MADEIRA_LOCK_ORPHAN": {"category": "Wine core (ntdll)", "title": "Orphan-lock reaper mode",
                "kind": "choice", "default": "", "sources": ["tools/patch-wine-lock-orphan.py"],
                "choices": [("", "As before"), ("stamped", "Only stamped locks"), ("0", "Off")],
                "note": "The monitor releases an SRW lock it believes a dead thread left held. Unset (default): as "
                        "before, which counts one strike per parked waiter, so a busy lock with 3 waiters is released "
                        "at once even while its owner lives (Horizon Zero Dawn stopped with \"Error\" right after such "
                        "a release, build 479). stamped: one strike per monitor cycle, three in a row, and only locks a "
                        "live thread has stamped (FEX's code-buffer locks) can be released. 0: no releases. The "
                        "dead-thread reaper is unchanged. Restart the session after changing it."},
    "env.DXMT_SMALL_RINGS": {"category": "Direct3D 9/10/11 (DXMT)", "title": "Small DXMT staging rings for these programs",
                "kind": "text", "default": "", "sources": ["tools/patch-d3d11-src-small-rings.py"],
                "note": "Needs env.MADEIRA_D3D11_SRC = 1 before build 543; since then upstream's d3d11.dll has it. Program file names separated by ';' (e.g. "
                        "Launcher.exe;SocialClubHelper.exe), or 1 for every program: in those programs DXMT's staging "
                        "ring, copy-temp ring and upload heap use 4 MB Metal blocks instead of 32 MB. A ring keeps its "
                        "blocks until 300 later submissions and an idle program keeps them for good (Rockstar Games "
                        "Launcher 132 MB, Social Club 68 MB through a whole RDR2 session). Larger uploads still get a "
                        "block of their own; each named program logs one [small-rings] line. Unset or 0 (default): "
                        "32 MB as upstream. Set it in the game's own file; read when the program starts."},
    # madeira-bcd: the D3D12/DXGI GPU as a D3DKMT adapter (build/win32u-unix/d3dkmt_ios.c).
    "env.MADEIRA_KMT_ADAPTER": {"category": "Windows, display & input", "title": "D3DKMT adapter for the GPU (WDDM 3.1)",
                "kind": "bool", "default": "0",
                "note": "1: D3DKMTEnumAdapters2 lists the GPU DXGI and D3D12 report (same LUID) and "
                        "D3DKMTQueryAdapterInfo answers like a WDDM 3.1 driver (driver version, caps, device ids, "
                        "memory, performance data); with env.MADEIRA_DXGI_SRC = 1 DXGI's CheckInterfaceSupport gives "
                        "the same driver version. Off (default): no adapter is listed, as before. Set it in the game's "
                        "own file; read at session start."},
    # madeira-bcd (round 579): a last resort for a guest-window placement the system refused on every candidate (virtual_ios.c).
    "env.MADEIRA_RESERVED_REMAP": {"category": "Memory & JIT pool", "title": "Last resort for a refused window placement",
                "kind": "bool", "default": "1", "sources": ["build/ntdll-unix/virtual_ios.c"],
                "note": "Default on, and only on the path that already fails: a 32-bit program on a device with a smaller address "
                        "map asked for memory inside its 4 GB window, the system refused the placement on every candidate "
                        "(ENOMEM; Shade: Wrath of Angels, 8 MB for its Direct3D 9 buffer, iPhone13,4 iOS 18.6.2) and the request "
                        "would end in STATUS_NO_MEMORY. BCD then unmaps the first refused range, which Wine's own tables call "
                        "free, and maps it again without overwriting; if that fails the reservation is put back and the request "
                        "fails as before. 0: no last resort. Read when a program starts."},
    # madeira-bcd (round 579): QueryDisplayConfig lists the virtual monitor's path (build/win32u-unix/sysparams_ios.c).
    "env.MADEIRA_VDCFG_PATH": {"category": "Windows, display & input", "title": "QueryDisplayConfig lists the monitor's path",
                "kind": "bool", "default": "0", "sources": ["build/win32u-unix/sysparams_ios.c"],
                "note": "1: QueryDisplayConfig returns the one path of BCD's virtual monitor (its source and target mode, "
                        "the current desktop size) as GetDisplayConfigBufferSizes already says it has, and "
                        "DisplayConfigGetDeviceInfo names its source (\\\\.\\DISPLAY1), target and adapter. Off (default): "
                        "success with no path, as before. For a game that builds its fullscreen window from the paths "
                        "(Trails in the Sky 1st Chapter: a one-pixel window at 32767, then a NULL swapchain). Set it in "
                        "the game's own file; read when the program asks for the display configuration."},
    # madeira-bcd: the ARM64EC JIT's boundary with native code (tools/patch-fex-ios-*.py, all on by default).
    "env.MADEIRA_FEX_CALLRET_ELIDE": {"category": "x86 emulation (FEX)", "title": "64-bit JIT without the call-return shadow stack",
                "kind": "bool", "default": "1", "sources": ["tools/patch-fex-ios-callret-elide.py"],
                "note": "Default on. The 64-bit (ARM64EC) JIT no longer keeps FEX's call-return shadow stack, which "
                        "nothing reads on iOS: 9 instructions less per guest CALL and per RET, and linked calls become "
                        "`bl`, so the CPU's return predictor follows guest calls. 0: the stack as before. Read when a "
                        "program starts."},
    "env.MADEIRA_FEX_EC_RET_DIRECT": {"category": "x86 emulation (FEX)", "title": "Returns from native code straight into the JIT",
                "kind": "bool", "default": "1", "sources": ["tools/patch-fex-ios-ec-ret-direct.py"],
                "note": "Default on. When a native (ARM64EC) function returns to the game's x64 code, the return enters "
                        "the 64-bit JIT at once instead of testing the native-code bitmap first; the JIT's dispatcher "
                        "already tests every address it has no translation for. 11 instructions less per return from a "
                        "system DLL. 0: the test as before. Read when a program starts."},
    "env.MADEIRA_FEX_EC_RET": {"category": "x86 emulation (FEX)", "title": "Returns from native code through the return predictor",
                "kind": "bool", "default": "1", "sources": ["tools/patch-fex-ios-ec-ret.py"],
                "note": "Default on. A guest call into native (ARM64EC) code leaves the JIT with BLR and its return "
                        "comes back with RET after an L1 lookup (10 instructions instead of 22), so the CPU's "
                        "return-address predictor stays in step with the guest's calls. 0: BR and the dispatcher loop "
                        "as before. Read when a program starts."},
    "env.MADEIRA_FEX_EC_BITMAP_CACHE": {"category": "x86 emulation (FEX)", "title": "Native-code check with the thread's bitmap pointer",
                "kind": "bool", "default": "1", "sources": ["tools/patch-fex-ios-ec-bitmap-cache.py"],
                "note": "Default on. The 64-bit JIT's dispatcher tests the native-code bitmap through a pointer kept in "
                        "the thread's state instead of walking TPIDRRO_EL0, the TEB and the PEB: 4 instructions less on "
                        "every call into a system DLL through its import table. 0: the walk as before. Read when a "
                        "program starts."},
    "env.MADEIRA_FEX_EC_EXIT_LEAN": {"category": "x86 emulation (FEX)", "title": "Lean exit from the JIT into native code",
                "kind": "bool", "default": "1", "sources": ["tools/patch-fex-ios-ec-exit-lean.py"],
                "note": "Default on while AFP is off (the default). Calls from the 64-bit JIT into native (ARM64EC) code "
                        "use a shorter stub: no FPCR read, the CPU area and suspend doorbell from the thread's state, the "
                        "call counted per thread instead of with an atomic, and the alias lookup starting at the thread's "
                        "own last hit; 31 instructions instead of 44. [xp-api] x64->EC rates lag by under 64 calls per "
                        "thread. 0: ExitFunctionEC as before. Read when a program starts."},
    # ajan6-arkham (2026-10-21): FEX's own switch, read by the emulator (FEX WOW64 Module.cpp, ml950), not by BCD's code, so the overlay
    # carries the whole entry. Auto-tune's rule x87-int64-roundtrip adds "0"; WineProcessBridge.m already treats the name as a
    # per-launch key (a game's own line wins over bcd.cfg's).
    "env.FEX_X87REDUCEDPRECISION": {"category": "x86 emulation (FEX)", "title": "Fast 64-bit x87 math (FEX_X87REDUCEDPRECISION)",
                "kind": "bool", "default": "1", "sources": ["app/Madeira/WineProcessBridge.m"],
                "note": "FEX's reduced-precision x87 does floating point in 64 bits instead of x87's 80, which is faster. A 32-bit "
                        "game runs with it ON unless this line says 0 (BCD's default for 32-bit games, set when the 32-bit emulator "
                        "starts); a 64-bit game runs with it OFF unless it says 1 (the game's own \"Faster x87 math\" switch sets it). "
                        "0 turns exact x87 on: slower, but an 8-byte integer read through FILD/FISTP (how MSVC reads an __int64 "
                        "atomically; Unreal Engine 3's lock-free lists) is no longer rounded to 53 bits. Auto-tune adds 0 when a "
                        "game's last fault follows that conversion. Read when a program starts."},
    # ajan7-arkham (2026-10-21): read by virtual_clear_tls_index through build/ntdll-unix/ios_tls_clear.h. An ATTEMPT (not proven) for
    # Batman: Arkham City GOTY's launcher (a .NET program run by Wine Mono, started by BatmanAC.exe), whose Mono thread info slot read
    # NULL while the parent was tearing down; auto-tune's rule mono-child-tls-null adds it.
    "env.MADEIRA_TLS_CLEAR_OWN_PROCESS": {"category": "Wine core (ntdll)", "title": "TlsFree only touches the calling program's threads",
                "kind": "bool", "default": "0",
                "note": "Default off. TlsFree clears the freed thread-local slot in every thread of the process; here every program of a "
                        "session (the Steam client, the game, a launcher the game starts) lives in one address space and the "
                        "list holds all their threads, so by default a slot freed by one program is also cleared in the others, "
                        "including one they are using. 1: only the calling program's threads are cleared, as on Windows. For a "
                        "program that starts a second one while it shuts down (Batman: Arkham City GOTY's launcher, run by Wine "
                        "Mono, died on an empty thread slot; not proven to be this). Every slot cleared in another program "
                        "while it held a value is logged as [tls-cross-clear], with or without this line. Read once per session.",
                "sources": ["build/ntdll-unix/ios_tls_clear.h", "build/ntdll-unix/virtual_ios.c"]},
    # ajan10/motor (build 591): read in build/ntdll-unix (signal_arm64_ios.c, virtual_ios.c); auto-tune's rules fault-guard-rate-redeliv,
    # fault-guard-rate-segv, user-va-cap-reserve-256gb and anon-cas-flush add them.
    "env.MADEIRA_FAULT_GUARD_RATE": {"category": "Exceptions & threads", "title": "Fault-storm guards end only a real storm",
                "kind": "bool", "default": "0",
                "note": "Default off. BCD ends a game that faults at one instruction 2000 times in a row (and skips an instruction that "
                        "faults 5 times in a row, handing the game a made-up value). A game that throws managed exceptions through "
                        "null-check traps (Unity Mono: SpiderHeck, ROUNDS, PlateUp!) reaches that count slowly while it keeps drawing "
                        "frames and was ended alive. 1: a run of 2000 counts only when it is dense (inside half a second) or the "
                        "presenter makes no progress, and the 5-repeat guard counts per thread and only when the repeats lie within "
                        "5 ms; a fast, real storm ends exactly as before. Logged as [redeliv] MADEIRA_FAULT_GUARD_RATE and "
                        "[segv-loop]. Read once per session.",
                "sources": ["build/ntdll-unix/signal_arm64_ios.c"]},
    "env.MADEIRA_USER_VA_GB": {"category": "Memory & JIT pool", "title": "Address space the game is told it has (GiB)",
                "kind": "int", "default": "0",
                "note": "Default 0 (off): a 64-bit game is told its user address space is the 512 GB this device maps "
                        "(GlobalMemoryStatusEx ullTotalVirtual 524,287 MB). N: it is told N GiB (4 to 4096; never more than the real "
                        "figure). Only the figure the game reads changes, not where memory is placed. For a game that sizes one "
                        "reservation from it and quits when it cannot get it (FINAL FANTASY TACTICS asks for 256 GiB + 64 MiB: 32 "
                        "is the line to try). Not for the Steam client, browser engines or 32-bit programs. Logged as [user-va]. "
                        "Read once per session.",
                "sources": ["build/ntdll-unix/virtual_ios.c"]},
    "env.MADEIRA_ANON_CAS_FLUSH": {"category": "Exceptions & threads", "title": "Atomic code patches go through the emulator",
                "kind": "bool", "default": "0",
                "note": "Default off: a compare-and-swap the game does on its own executable memory (a .NET runtime patching a stub it "
                        "has already run) is completed by BCD itself. 1: it is handed to the emulator instead, which discards its "
                        "translation of that page and does the compare-and-swap, as before build 580. For Jetboard Joust (MonoGame on "
                        "CoreCLR): one such CAS, then the main thread calls one routine 11 million times a second and never presents. "
                        "Slower (two exceptions per CAS). Logged as [anon-cas] build591. Read once per session.",
                "sources": ["build/ntdll-unix/signal_arm64_ios.c"]},
    "dxmt": {"title": "DXMT options (a=b;c=d)",
             "note": "Exported as DXMT_CONFIG with the options joined by ';', a library game's own dxmt options after these: "
                     "e.g. d3d11.mipClampBC=1;d3d11.preferredMaxFrameRate=30. DXMT reads at most 259 characters of it, "
                     "and nothing at all from a longer value (ml1255)."},
    "metalfx-upscale": {"title": "MetalFX upscaling factor", "kind": "choice",
             "note": "Scales the presented picture with Apple's MetalFX spatial scaler (Direct3D 11 and 12). Usually set per game in Game details > Display.",
             "choices": [("", "Off"), ("1.5", "1.5x"), ("2", "2x")]},
}


def category(path):
    p = path.replace("\\", "/")
    rules = [
        ("madeira-d3d12", "Direct3D 12"), ("dxmt", "Direct3D 9/10/11 (DXMT)"),
        ("madeira-dock", "Steam & Dock"), ("FEX/", "x86 emulation (FEX)"),
        ("winegstreamer", "Media"), ("audio", "Audio"), ("madsync", "Synchronisation"),
        ("/sync", "Synchronisation"), ("virtual_ios", "Memory & JIT pool"), ("JITAllocator", "Memory & JIT pool"),
        ("StikJIT", "Memory & JIT pool"), ("signal_", "Exceptions & threads"), ("thread", "Exceptions & threads"),
        ("wine/server", "Wine server"), ("wineserver", "Wine server"), ("win32u", "Windows, display & input"),
        ("Winios", "Windows, display & input"), ("Input", "Windows, display & input"), ("Gamepad", "Controllers"),
        ("Touch", "Controllers"), ("dinput", "Controllers"), ("xinput", "Controllers"),
        ("Steam", "Steam & Dock"), ("Dock", "Steam & Dock"), ("Onboarding", "Steam & Dock"),
        ("Library", "App & front end"), ("FPSOverlay", "App & front end"), ("app/Madeira", "App & front end"),
        ("ntdll", "Wine core (ntdll)"), ("wine/dlls", "Wine libraries"), ("build/", "Wine core (ntdll)"),
    ]
    for needle, name in rules:
        if needle in p:
            return name
    return "Other"


def comment_near(lines, i):
    """The comment on line i, else the nearest comment block within 8 lines above."""
    def trailing(line):
        for m in re.finditer(r'//+|/\*+', line):
            if line[:m.start()].count('"') % 2 == 0:   # not inside a string literal
                return line[m.end():]
        return None
    parts = []
    t = trailing(lines[i])
    if t and t.strip(" */"):
        parts.append(t)
    else:
        j = i - 1
        while j >= 0 and j >= i - 8 and not lines[j].strip().startswith(("//", "/*", "*")) \
                and not lines[j].rstrip().endswith("*/"):
            j -= 1
        block = []
        while j >= 0 and j >= i - 14:
            t = lines[j].strip()
            if t.startswith(("//", "/*", "*")) or t.endswith("*/"):
                if not t.startswith(("//", "/*", "*")):
                    t = trailing(t) or t
                block.insert(0, t)
                j -= 1
            else:
                break
        parts += block
    text = " ".join(parts)
    text = re.sub(r'/\*+|\*+/|//+', ' ', text)
    text = re.sub(r'(^|\s)\*(\s|$)', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(" *-")
    return (text[:237] + "...") if len(text) > 240 else text


def scan():
    opts = {}
    for repo, dirs in REPOS.items():
        base = os.path.join(ROOT, repo)
        if not os.path.isdir(base):
            continue
        out = subprocess.run(["git", "-C", base, "ls-files", "--"] + dirs,
                             capture_output=True, text=True).stdout.split()
        for rel in out:
            path = os.path.normpath(os.path.join(repo, rel))
            if not path.endswith(EXT) or any(s in path for s in SKIP):
                continue
            try:
                txt = open(os.path.join(ROOT, path), errors="ignore").read()
            except OSError:
                continue
            lines = txt.split("\n")
            for pat, is_env in ((CFG, False), (ENV, True)):
                for m in pat.finditer(txt):
                    reader, name, arg = m.group(1), m.group(2), (m.group(3) or "").strip()
                    if is_env or reader == "MadeiraConfig.flag":
                        key = "env." + name
                    else:
                        key = name
                    ln = txt.count("\n", 0, m.start())
                    kind = "bool" if reader in BOOL_READERS else "int" if reader in INT_READERS else "text"
                    dflt = ""
                    if reader in ("madeira_cfg_int", "madeira_cfg_bool", "mad_cfg_int_pe"):
                        dflt = arg if re.fullmatch(r'-?\d+|0x[0-9a-fA-F]+', arg or "") else ""
                    elif reader in ("MadeiraConfig.flag", "flag", "envFlag"):
                        dflt = "0" if arg.endswith("false") else "1" if (not arg or arg.endswith("true")) else ""
                    elif reader in ("madeiraSwitch", "madeira_switch_for_caller"):
                        dflt = "32-bit only"
                    elif reader == "MadeiraConfig.bool":
                        dflt = "1" if arg.endswith("true") else "0"
                    o = opts.setdefault(key, {"key": key, "kind": kind, "default": dflt, "sites": []})
                    if o["kind"] == "text" and kind != "text":
                        o["kind"] = kind
                    if not o["default"] and dflt:
                        o["default"] = dflt
                    o["sites"].append((path, ln + 1, comment_near(lines, ln)))
    # The engine code that acts on an option describes it better than the
    # Settings UI that merely reads it back: prefer sites outside app/.
    for o in opts.values():
        sites = sorted(o.pop("sites"), key=lambda s: (s[0].startswith("app/"), s[0], s[1]))
        o["category"] = category(sites[0][0])
        o["note"] = next((n for _, _, n in sites if n), "")
        files = []
        for p, _, _ in sites:                 # file names only: line numbers would make
            if p not in files:                # the catalog stale on every unrelated edit
                files.append(p)
        o["sources"] = files[:3]
    for key, extra in OVERLAY.items():
        o = opts.setdefault(key, {"key": key, "kind": "text", "default": "", "category": "App & front end",
                                  "note": "", "sources": []})
        o.update({k: v for k, v in extra.items()})
    return [opts[k] for k in sorted(opts, key=lambda k: (opts[k]["category"], k.lower()))]


def swift_str(s):
    return json.dumps(s, ensure_ascii=False)


def render(opts):
    out = ["// Generated by build/tools/gen-config-catalog.py from the sources that read each option.",
           "// Do not edit by hand: run the script (tests/host/check-config-catalog.py fails when stale).",
           "", "extension ConfigCatalog {", "    static let generated: [ConfigOption] = ["]
    for o in opts:
        choices = ", ".join(f"({swift_str(v)}, {swift_str(l)})" for v, l in o.get("choices", []))
        sources = ", ".join(swift_str(s) for s in o["sources"])
        out.append(f'        ConfigOption(key: {swift_str(o["key"])}, title: {swift_str(display(o.get("title", "")).replace("iOS-Madeira", "BCD on iOS"))}, '
                   f'kind: .{o["kind"]}, defaultValue: {swift_str(o["default"])}, '
                   f'category: {swift_str(display(o["category"]))}, note: {swift_str(display(o["note"].replace("iOS-Madeira", "BCD on iOS")))}, '
                   f'choices: [{choices}], sources: [{sources}]),')
    out += ["    ]", "}", ""]
    return "\n".join(out)


def main():
    text = render(scan())
    if "--check" in sys.argv:
        cur = open(OUT).read() if os.path.exists(OUT) else ""
        if cur != text:
            print("ConfigCatalog.generated.swift is out of date: run build/tools/gen-config-catalog.py")
            return 1
        print("ConfigCatalog.generated.swift is current")
        return 0
    open(OUT, "w").write(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
