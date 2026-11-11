#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wineserver: name the object a failed open asked for (night round 2026-11-11, A1 S4; Horizon Zero Dawn in BCD Dock).

Horizon Zero Dawn (Steam 1151640, the only Dock game that fails in every session of ~950: ASEXZ5, PGE6RE, VJM1NS, 6VESF6, WGQXKV, 5YEVTP) ends with
"[S_API FAIL] SteamAPI_Init() failed; create pipe failed.": the game's own steamclient64.dll loads, cannot reach the Steam client that is running
and logged on, and stops. Which object the library tries to open (an event, a section, a mutex, an IPC name) is named by no log; Valve's code
is closed and the `[steam-api]` block only guesses names. Every such open is a request the wineserver answers, and a failed open leaves no trace.

With env.MADEIRA_OBJ_TRACE = 1 (off by default; the app's rule table adds the line for exactly this failure) open_named_object() logs, for every open that
FAILED, one line (200 a session at most):
  [obj-open-fail] pid=0050 tid=0054 type=Event parent=\\BaseNamedObjects name=Global\\SteamClient_X status=c0000034 attrs=0x80 #1 rev=r652
(pid/tid of the opening thread, the object type the caller asked for or `-`, the directory the name is looked up from or `-`, the name as ASCII, the
status the server answers, the name attributes, a counter). The key is read at every failed open (a failed open is rare, and the app exports its env.
lines after the server thread started, so a value cached at the first call could miss it). WINE_IOS only; the open's result is unchanged.
Named patch-wineserver-*, not patch-wine-*: the i386 farm builds no server and its cache is keyed on tools/patch-wine-*.py.

Usage: patch-wineserver-object-open-trace.py <wine source dir>. Idempotent (marker "obj-open-trace-v1"); fails by name if an anchor moved, the
file unchanged.
"""
import pathlib
import sys

MARKER = "obj-open-trace-v1"

ANCHOR_COMMENT = "/* open a object by name under the specified parent */\n"
FUNCTION_OLD = """void *open_named_object( struct object *parent, const struct object_ops *ops,
                         const struct unicode_str *name, unsigned int attributes )
{
    struct unicode_str name_left;
    struct object *obj;

    if ((obj = lookup_named_object( parent, name, attributes, &name_left )))
    {
        if (name_left.len) /* not fully parsed */
            set_error( STATUS_OBJECT_NAME_NOT_FOUND );
        else if (ops && obj->ops != ops)
            set_error( STATUS_OBJECT_TYPE_MISMATCH );
        else
            return obj;

        release_object( obj );
    }
    return NULL;
}
"""
FUNCTION_NEW = FUNCTION_OLD.replace("""        release_object( obj );
    }
    return NULL;
}
""", """        release_object( obj );
    }
#ifdef WINE_IOS
    madeira_obj_open_fail( parent, ops, name, attributes );  /* madeira-bcd obj-open-trace-v1 */
#endif
    return NULL;
}
""")
HELPER = r'''#ifdef WINE_IOS
/* madeira-bcd obj-open-trace-v1 (night round 2026-11-11, Horizon Zero Dawn ASEXZ5, tools/patch-wineserver-object-open-trace.py): with
 * env.MADEIRA_OBJ_TRACE = 1 every open_named_object() that failed says what it asked for: the opening thread, the object type, the directory the
 * name is looked up from, the name and the status. 200 lines a session; the open itself is unchanged. The key is read at every failed open (the
 * app exports its env. lines after this thread started). */
static void madeira_obj_ascii( char *dst, size_t cap, const WCHAR *str, data_size_t len )
{
    size_t i, n = len / sizeof(WCHAR);

    if (n > cap - 1) n = cap - 1;
    for (i = 0; i < n; i++) dst[i] = (str[i] >= 32 && str[i] < 127) ? (char)str[i] : '?';
    dst[n] = 0;
}

static void madeira_obj_path( char *dst, size_t cap, struct object *obj )
{
    char leaf[96];
    size_t used;

    if (!obj || !obj->name) return;
    madeira_obj_path( dst, cap, obj->name->parent );
    used = strlen( dst );
    madeira_obj_ascii( leaf, sizeof(leaf), obj->name->name, obj->name->len );
    if (used + 2 < cap) snprintf( dst + used, cap - used, "\\%s", leaf );
}

static void madeira_obj_open_fail( struct object *parent, const struct object_ops *ops, const struct unicode_str *name,
                                   unsigned int attributes )
{
    static unsigned int shown;
    const char *key = getenv( "MADEIRA_OBJ_TRACE" );
    char type[48] = "-", path[192] = "", leaf[160];

    if (!key || key[0] != '1' || shown >= 200) return;
    shown++;
    if (ops && ops->type) madeira_obj_ascii( type, sizeof(type), ops->type->name.str, ops->type->name.len );
    madeira_obj_path( path, sizeof(path), parent );
    madeira_obj_ascii( leaf, sizeof(leaf), name ? name->str : NULL, name ? name->len : 0 );
    fprintf( stderr, "[obj-open-fail] pid=%04x tid=%04x type=%s parent=%s name=%s status=%08x attrs=%#x #%u rev=r652\n",
             current ? current->process->id : 0, current ? current->id : 0, type, path[0] ? path : "-", leaf, get_error(), attributes, shown );
}
#endif

'''


def patch(src):
    if MARKER in src:
        if src.count(FUNCTION_NEW) != 1 or src.count(HELPER) != 1:
            raise ValueError("partial or changed object open trace patch")
        return src
    if src.count(ANCHOR_COMMENT) != 1:
        raise ValueError("the comment of open_named_object in server/object.c not found once (Wine changed)")
    if src.count(FUNCTION_OLD) != 1:
        raise ValueError("open_named_object in server/object.c changed (Wine changed)")
    return src.replace(ANCHOR_COMMENT, HELPER + ANCHOR_COMMENT, 1).replace(FUNCTION_OLD, FUNCTION_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = pathlib.Path(sys.argv[1]) / "server/object.c"
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        print("::error::patch-wineserver-object-open-trace: " + str(error))
        return 1
    if result == source:
        print("patch-wineserver-object-open-trace: already patched")
    else:
        path.write_text(result)
        print("patch-wineserver-object-open-trace: [obj-open-fail] line added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
