#!/usr/bin/env python3
"""DXMT's geometry/tessellation draw-argument marshal loops advance to the next task; a zero-size draw dispatches nothing.

madeira-bcd (build 623), the backport upstream Madeira took as willfaust/dxmt d811847f2 (3Shain/dxmt aadf0fb "add
missing increment in loop" and the zero-count guard of the same functions on 3Shain/dxmt main, issue #200).
gs_draw_arguments_marshal and ts_draw_arguments_marshal (src/dxmt/dxmt_command.metal) never incremented `index`. The host
sets end_of_command only on the last task, so a batch of two or more geometry or tessellation draws spun on tasks[0] until
the GPU timed out and the later tasks' dispatch arguments were never written (Dark Souls III froze in combat upstream). A
draw with zero vertices or instances underflowed (x - 1) and could ask for about 4G threadgroups. A batch of one draw, the
only kind that finishes today, takes the same path as before (end_of_command breaks before the new increment).

Reaches what CI compiles from DXMT's source: the 32-bit d3d10/d3d11 (the i386 farm's meson build) and the native library.
The 64-bit games' d3d11.dll is upstream's committed binary and keeps its own copy. Idempotent; fails by name if an anchor
moved. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/dxmt/dxmt_command.metal")
MARKER = "madeira-bcd: GS/TS marshal advances"

GUARD_OLD = "    if (x * task.draw_arguments.y > task.max_object_threadgroups) {\n"
GUARD_NEW = ("    if ((task.draw_arguments.x * task.draw_arguments.y) == 0 ||   /* madeira-bcd: a zero-size draw dispatches nothing */\n"
             "        x * task.draw_arguments.y > task.max_object_threadgroups) {\n")
END_OLD = "    if (task.end_of_command)\n      break;\n  };\n}\n"
END_NEW = ("    if (task.end_of_command)\n      break;\n"
           "    index++;   /* madeira-bcd: GS/TS marshal advances to the next task (a batch of two or more draws spun on tasks[0]) */\n"
           "  };\n}\n")
# The same change as upstream wrote it (willfaust/dxmt d811847f).
UP_GUARD = ("    if ((task.draw_arguments.x * task.draw_arguments.y) == 0 ||\n"
            "        x * task.draw_arguments.y > task.max_object_threadgroups) {\n")
UP_END = "    if (task.end_of_command)\n      break;\n    index++;\n  };\n}\n"


def main():
    s = PATH.read_text()
    if MARKER in s:
        print(f"{PATH}: already patched")
        return
    ts = s.index("[[vertex]] void ts_draw_arguments_marshal(")
    gs = s.index("[[vertex]] void gs_draw_arguments_marshal(")
    end = s.index("struct depth_stencil_out", gs)
    region = s[ts:end]
    # Since the dxmt pin of the 2026-11-14 upstream merge the fork carries willfaust/dxmt d811847f itself (the same two lines in
    # both functions, without this script's comments): nothing is left to do, on the pristine pin and on a patched tree alike.
    if (region.count(UP_GUARD) == 2 and region.count(UP_END) == 2
            and region.count(GUARD_OLD) == 0 and region.count(END_OLD) == 0):
        print(f"{PATH}: the GS/TS marshal fix is upstream (willfaust/dxmt d811847f); nothing to do")
        return
    if region.count(GUARD_OLD) != 2 or region.count(END_OLD) != 2:
        sys.exit(f"patch-dxmt-gsts-marshal: anchors not found twice in the two marshal functions of {PATH}")
    region = region.replace(GUARD_OLD, GUARD_NEW).replace(END_OLD, END_NEW)
    s = s[:ts] + region + s[end:]
    if s.count(MARKER) != 2:
        sys.exit("patch-dxmt-gsts-marshal: expected the marker in both functions")
    PATH.write_text(s)
    print(f"{PATH}: patched")


main()
