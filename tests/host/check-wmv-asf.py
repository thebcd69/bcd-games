#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Windows Media Video through Media Foundation's ASF source: the unix side, on the host; no Wine runs.

Resident Evil 2 (Steam 883710, build 579, tester's log re2-ipadm4-579) plays WMV cutscenes through a Media
Foundation source reader and ends at the first one: the ASF byte stream handler's DLL, mfasfsrcsnk.dll, is
not in the prefix (c0000135), the game does not check, and reads NULL (re2.exe+0x25c9861).  The fix is
keyed (docs/MEDIA.md, "ASF and WMV"):

  - mfasfsrcsnk-optin.dll, wmvdecod-optin.dll and wmadmod-optin.dll are opt-in DLLs, winedmo.dll a plain
    one nothing imports; the game's file lists env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod and
    env.MADEIRA_WG_64BIT = 1;
  - winedmo's unix side (build/ntdll-unix/winedmo_unixlib_ios.c) is the FFmpeg demuxer, bound by name in
    virtual_ios.c for 64-bit callers;
  - winegstreamer's unix side (winegstreamer_unixlib_ios.c + winegstreamer_wmv_ios.c) decodes WMV in
    wg_transform: wmv1, wmv2, wmv3, vc1 on libavcodec, converted to the formats video_decoder.c lists.

This test (Linux or macOS, needs a configured Wine tree for the generated headers, like check-wma-decoder.py):

  1. Source checks that the change is keyed: the FFmpeg configuration is LGPL and has exactly the new
     components; the WMV transform is reached only through the 64-bit table's create entry; the wow64 table
     and every other module's binding are as they were; winedmo is bound for 64-bit callers only;
     the build scripts compile and archive both files, ship the DLLs and apply the patch.
  2. The pixel conversions (wmv_convert) against an independent reference, every format, odd sizes, a 2D
     buffer's pitch, bottom-up rows, padding and cropping.
  3. The transform, through the 64-bit unix call entries the way video_decoder.c / wg_sample.c drive them:
     a WMV2 stream encoded here (FFmpeg's encoder, test only) and muxed into ASF, and Wine's own WMV1 ASF
     test files, demuxed, pushed and read back in all eleven formats; time stamps; the lazy open the
     support check needs; refusals; drain, flush, a buffer that is too small, MF_E_NOTACCEPTING.
  4. The demuxer (winedmo's unix side): the same files read through its unix call table with the PE
     callbacks emulated, stream types, packets, BUFFER_TOO_SMALL, seek, EOF.

Environment: MADEIRA_FFMPEG_TARBALL (default: build/ffmpeg/src/ffmpeg-7.1.5.tar.xz), MADEIRA_HOST_FFMPEG (prefix
of an existing host build with the components below), MADEIRA_WINE_SRC (default: wine/; the submodule's test
media), MADEIRA_WINE_BUILD (a configured Wine build tree whose include/ holds config.h and the widl-generated
headers; default: wine/build-macos, the tree build/ntdll-unix/build.sh uses).
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ntdll_unix = root / "build/ntdll-unix"
wine = Path(os.environ.get("MADEIRA_WINE_SRC") or root / "wine")
wine_build = Path(os.environ.get("MADEIRA_WINE_BUILD") or wine / "build-macos")
for need in [wine_build / "include/config.h", wine_build / "include/mfobjects.h", wine / "dlls/winegstreamer/unixlib.h",
             wine / "dlls/winedmo/unixlib.h"]:
    if not need.exists():
        sys.exit("SKIP-FAIL: %s is missing; point MADEIRA_WINE_BUILD / MADEIRA_WINE_SRC at a configured Wine tree" % need)
media = [wine / "dlls/qasf/tests/test.wmv", wine / "dlls/mfplat/tests/test-wmv1.wmv"]
for m in media:
    if not m.exists():
        sys.exit("SKIP-FAIL: %s is missing (Wine's test media)" % m)

ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def code(path):
    """A source file without comments, so a check does not read what a comment says."""
    text = (root / path).read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


# ------------------------------------------------------------------ 1. source checks (keyed)
ff = (root / "build/ffmpeg/build.sh").read_text()
args = ff[ff.index("CONFIG_ARGS=("):]
args = args[:args.index("\n)\n")]
for flag in ["--disable-gpl", "--disable-nonfree", "--disable-version3", "--disable-everything", "--disable-swscale"]:
    check("FFmpeg stays LGPL-only and minimal (%s)" % flag, flag in args)
check("FFmpeg adds the WMV decoders", "--enable-decoder=wmv1,wmv2,wmv3,vc1" in args)
check("FFmpeg adds the asf demuxer next to mp3, wav and mov", "--enable-demuxer=mp3,wav,mov,asf" in args)
check("no H.264, HEVC, AAC, GPL component or encoder/muxer is enabled",
      not re.search(r"--enable-(decoder|parser|encoder|muxer|protocol)=[^\n]*\b(h264|hevc|aac|x264|x265)\b", args)
      and "--enable-encoder" not in args and "--enable-muxer" not in args and "--enable-protocol" not in args)

glue = code("build/ntdll-unix/winegstreamer_unixlib_ios.c")
wmv = code("build/ntdll-unix/winegstreamer_wmv_ios.c")
dmo = code("build/ntdll-unix/winedmo_unixlib_ios.c")
virtual = code("build/ntdll-unix/virtual_ios.c")
build_sh = (ntdll_unix / "build.sh").read_text()

check("the glue includes the WMV file once, after the WMA transform and before the entries",
      glue.count('#include "winegstreamer_wmv_ios.c"') == 1 and
      glue.index("static NTSTATUS transform_set_output_type(") < glue.index('#include "winegstreamer_wmv_ios.c"') <
      glue.index("static NTSTATUS wma_init("))
create64 = glue[glue.index("static NTSTATUS wma_transform_create( void *args )"):]
create64 = create64[:create64.index("\n}\n")]
check("the 64-bit create entry sends a video input to the WMV transform and everything else to transform_create",
      "madeira_MFMediaType_Video" in create64 and "return wmv_transform_create( params );" in create64 and
      create64.rstrip().endswith("return transform_create( params );"))
wow = glue[glue.index("static NTSTATUS wow64_wma_transform_create( void *args )"):]
wow = wow[:wow.index("\n}\n")]
check("the wow64 (32-bit) create thunk never reaches the WMV transform", "wmv_" not in wow and "transform_create( &params )" in wow)
wow_table = glue[glue.index("const unixlib_entry_t __wine_unix_call_wow64_funcs[]"):]
check("the wow64 table lists the unchanged WMA thunks (no wmv_ entry)", "wmv_" not in wow_table[:wow_table.index("};")]
      and "wow64_wma_transform_create," in wow_table)
check("the stub-table rule for 64-bit callers is untouched (only MADEIRA_WG_64BIT=1 binds winegstreamer)",
      '} else if (match && strstr(match, "winegstreamer") && (wow || ios_wg_64bit_opted_in())) {' in virtual)
check("winedmo is bound for 64-bit callers only, by name, in front of the generic fallback",
      '} else if (match && strstr(match, "winedmo") && !wow) {' in virtual and
      virtual.index('strstr(match, "winedmo")') < virtual.index("using stub table") and
      "funcs64 = (const void *)winedmo_unix_call_funcs;" in virtual and
      virtual.count("winedmo_unix_call_funcs") == 2)
check("winedmo_unixlib_ios.c is compiled with its own unixlib.h and archived",
      'compile_unixlib "$BUILD_DIR/winedmo_unixlib_ios.c" "winedmo_unixlib" "winedmo"' in build_sh and
      '-I"$WINE_SRC/dlls/winedmo"' in build_sh and '"$OBJ_DIR/winedmo_unixlib.o"' in build_sh)
check("the winedmo unix side defines static GUIDs only (no initguid.h: a global GUID would clash in the archive)",
      "initguid.h" not in dmo and "DEFINE_GUID" not in dmo)

extra = (root / "tools/build-wine-extra-dlls.sh").read_text()
optin = re.search(r'OPTIN_WANT="([^"]*)"', extra).group(1).split()
want = extra[extra.index('WANT="'):]
want = want[:want.index('"\n', 6)].split()
check("OPTIN_WANT ships mfasfsrcsnk, wmvdecod and wmadmod as opt-in files", {"mfasfsrcsnk", "wmvdecod", "wmadmod"} <= set(optin))
check("winedmo is built as a plain DLL (nothing shipped imports it) and is not opt-in", "winedmo" in want and "winedmo" not in optin)
check("none of the opt-in names is in the plain list (shipped under its own name)", not ({"mfasfsrcsnk", "wmvdecod", "wmadmod"} & set(want)))
check("the build applies and restores the mfasfsrcsnk patch and verifies the DLL before it ships",
      'python3 "$R/tools/patch-wine-mfasfsrcsnk-winedmo.py" "$R/wine"' in extra and
      'git -C "$R/wine" checkout -- dlls/mfsrcsnk/media_source.c' in extra and
      'patch-wine-mfasfsrcsnk-winedmo.py" --verify "$f"' in extra)
check("the keyed bridge needs no change: it links every shipped *-optin.dll a game lists",
      'hasSuffix:@"-optin.dll"' in (root / "app/Madeira/WineProcessBridge.m").read_text())

# the patch script applies and is idempotent on Wine's source, and does what it says
patch_py = root / "tools/patch-wine-mfasfsrcsnk-winedmo.py"
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp) / "dlls/mfsrcsnk"
    d.mkdir(parents=True)
    orig = (wine / "dlls/mfsrcsnk/media_source.c").read_text()
    (d / "media_source.c").write_text(orig)
    r1 = subprocess.run([sys.executable, str(patch_py), tmp], capture_output=True, text=True)
    patched = (d / "media_source.c").read_text()
    r2 = subprocess.run([sys.executable, str(patch_py), tmp], capture_output=True, text=True)
    check("the patch applies to Wine's media_source.c and a second run changes nothing",
          r1.returncode == 0 and "patched" in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout and
          (d / "media_source.c").read_text() == patched, )
    fn = patched[patched.index("static BOOL use_gst_byte_stream_handler(void)"):]
    fn = fn[:fn.index("\n}\n")]
    check("without the registry key the demuxer is used (return FALSE), the key still decides when it is set",
          fn.rstrip().endswith("return FALSE;") and "return !result;" in fn)
    check("the handler says which handler it chose and why, as ERR lines the app's WINEDEBUG shows",
          patched.count('ERR("[mfasfsrcsnk] madeira-bcd:') == 2 and "mfasfsrcsnk-winedmo-v1 madeira-bcd" in patched)
    changed = [l for l in orig.splitlines() if l not in patched.splitlines()]
    check("only the three intended lines of Wine's file change", len(changed) == 3)

# ------------------------------------------------------------------ 2. host FFmpeg
tarball = os.environ.get("MADEIRA_FFMPEG_TARBALL") or str(root / "build/ffmpeg/src/ffmpeg-7.1.5.tar.xz")
FLAGS = [
    "--disable-everything", "--disable-autodetect", "--disable-gpl", "--disable-nonfree", "--disable-version3",
    "--enable-decoder=wmav1,wmav2,wmapro,wmalossless,xma1,xma2",
    "--enable-decoder=mp1,mp2,mp3", "--enable-decoder=pcm_u8,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le",
    "--enable-decoder=wmv1,wmv2,wmv3,vc1",
    "--enable-demuxer=mp3,wav,mov,asf", "--enable-parser=mpegaudio",
    # TEST ONLY: the encoders and the muxer that make the input streams
    "--enable-encoder=wmv1,wmv2,wmav2", "--enable-muxer=asf",
    "--disable-programs", "--disable-doc", "--disable-network", "--disable-avdevice", "--disable-swscale",
    "--disable-avfilter", "--disable-postproc", "--enable-avformat", "--enable-avcodec", "--enable-swresample",
    "--disable-asm", "--disable-shared", "--enable-static", "--enable-pic", "--disable-debug",
]
prefix = os.environ.get("MADEIRA_HOST_FFMPEG")
if not prefix:
    key = hashlib.sha256(" ".join(FLAGS).encode()).hexdigest()[:12]
    prefix = str(Path.home() / ".cache/madeira-host-ffmpeg" / ("7.1.5-" + key))
    if not (Path(prefix) / "lib/libavcodec.a").exists():
        assert Path(tarball).exists(), "no FFmpeg tarball at %s" % tarball
        src = Path(prefix + "-src")
        src.mkdir(parents=True, exist_ok=True)
        if not (src / "ffmpeg-7.1.5/configure").exists():
            subprocess.run(["tar", "-xJf", tarball, "-C", str(src)], check=True)
        bld = Path(prefix + "-build")
        bld.mkdir(parents=True, exist_ok=True)
        print("building host FFmpeg into", prefix, flush=True)
        log = open(str(bld) + ".log", "w")
        subprocess.run([str(src / "ffmpeg-7.1.5/configure"), "--prefix=" + prefix, "--cc=cc"] + FLAGS,
                       cwd=bld, check=True, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(["make", "-j%d" % (os.cpu_count() or 4)], cwd=bld, check=True, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(["make", "install"], cwd=bld, check=True, stdout=log, stderr=subprocess.STDOUT)
inc, lib = Path(prefix) / "include", Path(prefix) / "lib"
libs = [str(lib / "libavformat.a"), str(lib / "libavcodec.a"), str(lib / "libswresample.a"), str(lib / "libavutil.a"), "-lm", "-lpthread"]

# ------------------------------------------------------------------ 3. the transform harness
HARNESS_WMV = r"""
#include "winegstreamer_unixlib_ios.c"
#include <math.h>

/* What the device build gets from wg_parser_apple_ios.c. */
const struct mav_video_backend mav_apple_video_backend;
const struct mav_audio_backend mav_apple_audio_backend;

static int failures;
static const char *cur = "?";
#define CHECK(c, ...) do { if (!(c)) { printf( "FAIL %s: ", cur ); printf( __VA_ARGS__ ); printf( "\n" ); failures++; return 0; } } while (0)

struct mem { uint8_t *d; size_t n, pos; };
static int mem_read( void *o, uint8_t *b, int s ) { struct mem *m = o; size_t k; if (m->pos >= m->n) return AVERROR_EOF; k = m->n - m->pos; if (k > (size_t)s) k = s; memcpy( b, m->d + m->pos, k ); m->pos += k; return (int)k; }
static int64_t mem_seek( void *o, int64_t off, int wh ) { struct mem *m = o; if (wh == AVSEEK_SIZE) return m->n; if (wh == SEEK_CUR) off += m->pos; else if (wh == SEEK_END) off += m->n; if (off < 0 || (size_t)off > m->n) return -1; m->pos = off; return off; }

static int load_file( const char *path, struct mem *m )
{
    FILE *f = fopen( path, "rb" );
    memset( m, 0, sizeof(*m) );
    if (!f) return -1;
    fseek( f, 0, SEEK_END ); m->n = ftell( f ); fseek( f, 0, SEEK_SET );
    m->d = malloc( m->n ); if (fread( m->d, 1, m->n, f ) != m->n) return -1;
    fclose( f );
    return 0;
}

static AVFormatContext *open_mem( struct mem *m )
{
    AVFormatContext *ctx = avformat_alloc_context();
    ctx->pb = avio_alloc_context( av_malloc( 65536 ), 65536, 0, m, mem_read, NULL, mem_seek );
    if (avformat_open_input( &ctx, NULL, NULL, NULL ) < 0) return NULL;
    if (avformat_find_stream_info( ctx, NULL ) < 0) return NULL;
    return ctx;
}

/* ---- the MF types the PE side hands over: an MFVIDEOFORMAT, private data after it ---- */
static GUID subtype( UINT32 data1 )
{
    GUID g = {0x00000000, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
    g.Data1 = data1;
    return g;
}
#define FOURCC( a, b, c, d ) ((UINT32)(a) | ((UINT32)(b) << 8) | ((UINT32)(c) << 16) | ((UINT32)(d) << 24))

static MFVIDEOFORMAT *make_format( UINT32 data1, int w, int h, int fps_n, int fps_d, const void *extra, int extra_size,
                                   int bottom_up )
{
    MFVIDEOFORMAT *f = calloc( 1, sizeof(*f) + extra_size );
    f->dwSize = sizeof(*f) + extra_size;
    f->guidFormat = subtype( data1 );
    f->videoInfo.dwWidth = w; f->videoInfo.dwHeight = h;
    f->videoInfo.FramesPerSecond.Numerator = fps_n; f->videoInfo.FramesPerSecond.Denominator = fps_d;
    if (bottom_up) f->videoInfo.VideoFlags |= MFVideoFlag_BottomUpLinearRep;
    if (extra_size) memcpy( f + 1, extra, extra_size );
    return f;
}

static const struct { const char *name; UINT32 d1; enum wmv_fmt fmt; } fmts[] =
{
    { "NV12", FOURCC( 'N', 'V', '1', '2' ), WMV_FMT_NV12 }, { "YV12", FOURCC( 'Y', 'V', '1', '2' ), WMV_FMT_YV12 },
    { "IYUV", FOURCC( 'I', 'Y', 'U', 'V' ), WMV_FMT_I420 }, { "I420", FOURCC( 'I', '4', '2', '0' ), WMV_FMT_I420 },
    { "YUY2", FOURCC( 'Y', 'U', 'Y', '2' ), WMV_FMT_YUY2 }, { "UYVY", FOURCC( 'U', 'Y', 'V', 'Y' ), WMV_FMT_UYVY },
    { "YVYU", FOURCC( 'Y', 'V', 'Y', 'U' ), WMV_FMT_YVYU }, { "RGB32", 22, WMV_FMT_RGB32 },
    { "RGB24", 20, WMV_FMT_RGB24 }, { "RGB565", 23, WMV_FMT_RGB565 }, { "RGB555", 24, WMV_FMT_RGB555 },
};
#define NFMTS ((int)(sizeof(fmts) / sizeof(fmts[0])))

/* ---- 2. conversions against an independent reference ---- */
static uint8_t clampd( double v ) { v = floor( v + 0.5 ); return v < 0 ? 0 : v > 255 ? 255 : (uint8_t)v; }

static void ref_rgb( int y, int u, int v, int full, int bt709, uint8_t *r, uint8_t *g, uint8_t *b )
{
    /* unscaled constants; limited range scales luma by 255/219 and chroma by 255/224 */
    double yy = full ? y : 1.164383 * (y - 16), cb = u - 128, cr = v - 128, sc = full ? 1.0 : 1.138393;
    double rv = bt709 ? 1.5748 : 1.402, gu = bt709 ? 0.187324 : 0.344136, gv = bt709 ? 0.468124 : 0.714136, bu = bt709 ? 1.8556 : 1.772;
    *r = clampd( yy + rv * sc * cr );
    *g = clampd( yy - gu * sc * cb - gv * sc * cr );
    *b = clampd( yy + bu * sc * cb );
}

static int test_convert( void )
{
    static const int sizes[][2] = { {34, 18}, {33, 17}, {16, 16}, {2, 2}, {1, 1} };
    int si, fi, full, bt709, flip;
    cur = "convert";
    for (si = 0; si < 5; si++)
    {
        int w = sizes[si][0], h = sizes[si][1], cw = (w + 1) / 2, chh = (h + 1) / 2, x, y;
        uint8_t *py = calloc( (size_t)(w + 3) * h, 1 ), *pu = calloc( (size_t)(cw + 3) * chh, 1 ), *pv = calloc( (size_t)(cw + 3) * chh, 1 );
        struct wmv_picture pic = { .p = { py, pu, pv }, .stride = { w + 3, cw + 3, cw + 3 }, .width = w, .height = h };
        for (y = 0; y < h; y++) for (x = 0; x < w; x++) py[y * (w + 3) + x] = (uint8_t)(16 + ((x * 7 + y * 13) % 220));
        for (y = 0; y < chh; y++) for (x = 0; x < cw; x++) { pu[y * (cw + 3) + x] = (uint8_t)(16 + ((x * 11 + y * 5) % 224)); pv[y * (cw + 3) + x] = (uint8_t)(16 + ((x * 3 + y * 17) % 224)); }
        for (fi = 0; fi < NFMTS; fi++)
        {
            if (fmts[fi].d1 == FOURCC( 'I', 'Y', 'U', 'V' )) continue;
            for (full = 0; full < 2; full++) for (bt709 = 0; bt709 < 2; bt709++) for (flip = 0; flip < 2; flip++)
            {
                struct wmv_layout l;
                size_t size = wmv_layout_for( fmts[fi].fmt, w, h, 0, &l );
                uint8_t *dst = malloc( size + 16 ), *chk;
                enum wmv_fmt f = fmts[fi].fmt;
                memset( dst, 0xa5, size + 16 );
                pic.full_range = full; pic.bt709 = bt709;
                wmv_convert( &pic, dst, f, &l, w, h, flip );
                CHECK( dst[size] == 0xa5 && dst[size + 15] == 0xa5, "%s %dx%d wrote past its layout", fmts[fi].name, w, h );
                chk = dst;
#define ROWP( plane, row, rows ) (chk + l.offset[plane] + (size_t)l.stride[plane] * (flip ? (rows) - 1 - (row) : (row)))
                for (y = 0; y < h; y++) for (x = 0; x < w; x++)
                {
                    int yy = py[y * (w + 3) + x], uu = pu[(y / 2) * (cw + 3) + x / 2], vv = pv[(y / 2) * (cw + 3) + x / 2];
                    if (f == WMV_FMT_NV12) {
                        CHECK( ROWP( 0, y, h )[x] == yy, "NV12 Y %dx%d at %d,%d", w, h, x, y );
                        CHECK( ROWP( 1, y / 2, l.rows[1] )[(x & ~1) ] == uu && ROWP( 1, y / 2, l.rows[1] )[(x & ~1) + 1] == vv, "NV12 UV %dx%d at %d,%d", w, h, x, y );
                    } else if (f == WMV_FMT_I420 || f == WMV_FMT_YV12) {
                        int up = f == WMV_FMT_I420 ? 1 : 2, vp = 3 - up;
                        CHECK( ROWP( 0, y, h )[x] == yy, "I420 Y" );
                        CHECK( ROWP( up, y / 2, l.rows[up] )[x / 2] == uu && ROWP( vp, y / 2, l.rows[vp] )[x / 2] == vv, "%s U/V %dx%d at %d,%d", fmts[fi].name, w, h, x, y );
                    } else if (f == WMV_FMT_YUY2 || f == WMV_FMT_UYVY || f == WMV_FMT_YVYU) {
                        uint8_t *o = ROWP( 0, y, h ) + (x & ~1) * 2;
                        int yo = f == WMV_FMT_UYVY ? 1 : 0, uo = f == WMV_FMT_YUY2 ? 1 : f == WMV_FMT_UYVY ? 0 : 3, vo = f == WMV_FMT_YUY2 ? 3 : f == WMV_FMT_UYVY ? 2 : 1;
                        CHECK( o[yo + (x & 1) * 2] == yy, "%s Y %dx%d at %d,%d", fmts[fi].name, w, h, x, y );
                        CHECK( o[uo] == uu && o[vo] == vv, "%s chroma %dx%d at %d,%d", fmts[fi].name, w, h, x, y );
                    } else {
                        uint8_t r, g, b, *o = ROWP( 0, y, h );
                        int er = 0, eg = 0, eb = 0, got_r, got_g, got_b;
                        ref_rgb( yy, uu, vv, full, bt709, &r, &g, &b );
                        if (f == WMV_FMT_RGB32) { got_b = o[4*x]; got_g = o[4*x+1]; got_r = o[4*x+2]; CHECK( o[4*x+3] == 0xff, "RGB32 alpha" ); }
                        else if (f == WMV_FMT_RGB24) { got_b = o[3*x]; got_g = o[3*x+1]; got_r = o[3*x+2]; }
                        else {
                            unsigned px = o[2*x] | (o[2*x+1] << 8);
                            if (f == WMV_FMT_RGB565) { got_r = ((px >> 11) & 31) << 3; got_g = ((px >> 5) & 63) << 2; got_b = (px & 31) << 3; er = 8; eg = 4; eb = 8; }
                            else { got_r = ((px >> 10) & 31) << 3; got_g = ((px >> 5) & 31) << 3; got_b = (px & 31) << 3; er = eg = eb = 8; }
                        }
                        CHECK( abs( got_r - r ) <= 2 + er && abs( got_g - g ) <= 2 + eg && abs( got_b - b ) <= 2 + eb,
                               "%s full=%d 709=%d y=%d u=%d v=%d: got %d,%d,%d want %d,%d,%d", fmts[fi].name, full, bt709, yy, uu, vv, got_r, got_g, got_b, r, g, b );
                    }
                }
                free( dst );
            }
        }
        free( py ); free( pu ); free( pv );
    }
    printf( "PASS convert: 5 sizes x 10 formats x range x matrix x flip against the reference\n" );
    return 1;
}

static int test_layout_pad_crop( void )
{
    /* MFCalculateImageSize / default strides, a 2D pitch, padding and cropping */
    struct wmv_layout l;
    cur = "layout";
    CHECK( wmv_layout_for( WMV_FMT_NV12, 96, 96, 0, &l ) == 96 * 96 * 3 / 2 && l.stride[0] == 96, "NV12 96x96" );
    CHECK( wmv_layout_for( WMV_FMT_NV12, 97, 96, 0, &l ) == 98 * 96 * 3 / 2 && l.stride[0] == 98, "NV12 97x96 rounds the width up to 2" );
    CHECK( wmv_layout_for( WMV_FMT_I420, 96, 96, 0, &l ) == 96 * 96 * 3 / 2 && l.stride[1] == 48, "I420 96x96" );
    CHECK( wmv_layout_for( WMV_FMT_YUY2, 96, 96, 0, &l ) == 96 * 96 * 2 && l.stride[0] == 192, "YUY2 96x96" );
    CHECK( wmv_layout_for( WMV_FMT_RGB32, 96, 96, 0, &l ) == 96 * 96 * 4, "RGB32 96x96" );
    CHECK( wmv_layout_for( WMV_FMT_RGB24, 97, 96, 0, &l ) == 292 * 96 && l.stride[0] == 292, "RGB24 97x96 rows end on 4 bytes" );
    CHECK( wmv_layout_for( WMV_FMT_RGB565, 97, 96, 0, &l ) == 196 * 96, "RGB565 97x96" );
    CHECK( wmv_layout_for( WMV_FMT_NV12, 96, 96, 128, &l ) == 128 * 96 * 3 / 2 && l.stride[0] == 128 && l.stride[1] == 128, "NV12 in a pitch-128 2D buffer" );
    CHECK( wmv_layout_for( WMV_FMT_I420, 96, 96, 128, &l ) == 128 * 96 + 2 * 64 * 48 && l.stride[1] == 64, "I420 in a pitch-128 2D buffer" );
    CHECK( wmv_min_pitch( WMV_FMT_RGB32, 96 ) == 384 && wmv_min_pitch( WMV_FMT_NV12, 96 ) == 96, "minimum pitches" );
    {
        /* a 4x4 picture into 8x6: padded with black */
        uint8_t py[16], pu[4] = { 90, 91, 92, 93 }, pv[4] = { 200, 201, 202, 203 }, dst[8 * 6 * 4];
        struct wmv_picture pic = { .p = { py, pu, pv }, .stride = { 4, 2, 2 }, .width = 4, .height = 4 };
        int x, y;
        memset( py, 120, sizeof(py) );
        wmv_layout_for( WMV_FMT_NV12, 8, 6, 0, &l );
        wmv_convert( &pic, dst, WMV_FMT_NV12, &l, 8, 6, 0 );
        CHECK( dst[0] == 120 && dst[3] == 120 && dst[4] == 16 && dst[5 * 8] == 16 && dst[3 * 8 + 3] == 120, "NV12 padding: Y of the picture, 16 around it" );
        CHECK( dst[l.offset[1]] == 90 && dst[l.offset[1] + 1] == 200 && dst[l.offset[1] + 4] == 128, "NV12 padding: chroma of the picture, 128 around it" );
        wmv_layout_for( WMV_FMT_RGB32, 8, 6, 0, &l );
        wmv_convert( &pic, dst, WMV_FMT_RGB32, &l, 8, 6, 0 );
        CHECK( dst[4 * 4 + 3] == 0xff && dst[4 * 4] == 0 && dst[4 * 4 + 1] == 0 && dst[4 * 4 + 2] == 0, "RGB32 padding: opaque black" );
        /* 8x6 into 4x2: cropped */
        {
            uint8_t big[48], bu[12], bv[12], out[4 * 2 * 3 / 2 + 8];
            struct wmv_picture p2 = { .p = { big, bu, bv }, .stride = { 8, 4, 4 }, .width = 8, .height = 6 };
            for (x = 0; x < 48; x++) big[x] = x;
            memset( bu, 77, 12 ); memset( bv, 99, 12 );
            wmv_layout_for( WMV_FMT_NV12, 4, 2, 0, &l );
            wmv_convert( &p2, out, WMV_FMT_NV12, &l, 4, 2, 0 );
            for (y = 0; y < 2; y++) for (x = 0; x < 4; x++) CHECK( out[y * 4 + x] == big[y * 8 + x], "crop Y %d,%d", x, y );
            CHECK( out[8] == 77 && out[9] == 99, "crop chroma" );
        }
    }
    printf( "PASS layout: default strides, a 2D pitch, padding and cropping\n" );
    return 1;
}

/* ---- the times of a stream with B frames ---- */
static int test_ts( void )
{
    struct wmv_ts ts = {0};
    static const INT64 in[] = { 0, 3, 1, 2, 7, 5, 6, 4 };   /* two groups of I0 P3 B1 B2 in decode order */
    INT64 t, last = -1;
    unsigned int i;
    cur = "time stamps";
    CHECK( !wmv_ts_take( &ts, &t ), "nothing to take from an empty list" );
    for (i = 0; i < 4; i++) wmv_ts_insert( &ts, in[i] * 833333 );
    for (i = 0; i < 4; i++) { CHECK( wmv_ts_take( &ts, &t ) && t == (INT64)i * 833333, "take %u: %lld", i, (long long)t ); }
    for (i = 4; i < 8; i++) wmv_ts_insert( &ts, in[i] * 833333 );
    for (i = 4; i < 8; i++) { CHECK( wmv_ts_take( &ts, &t ) && t > last && t == (INT64)i * 833333, "take %u: %lld", i, (long long)t ); last = t; }
    for (i = 0; i < WMV_TS_MAX + 10; i++) wmv_ts_insert( &ts, 1000 + i );
    CHECK( ts.n == WMV_TS_MAX, "the list is bounded (%u)", ts.n );
    CHECK( wmv_ts_take( &ts, &t ) && t == 1010, "the oldest are forgotten first (%lld)", (long long)t );
    printf( "PASS time stamps: a reordering stream is stamped in ascending order, the list is bounded\n" );
    return 1;
}

/* ---- 3. the transform, through the 64-bit entries ---- */
struct pkt { uint8_t *d; int size; int64_t pts, dur; int key; };
struct vstream { struct pkt *pk; int n; int w, h; UINT32 tag; uint8_t extra[64]; int extra_size; int fps_n, fps_d; };

static int collect( AVFormatContext *ctx, int index, struct vstream *vs )
{
    AVCodecParameters *par = ctx->streams[index]->codecpar;
    AVPacket *pkt = av_packet_alloc();
    memset( vs, 0, sizeof(*vs) );
    vs->w = par->width; vs->h = par->height; vs->tag = par->codec_tag; vs->extra_size = par->extradata_size;
    if (par->extradata_size) memcpy( vs->extra, par->extradata, par->extradata_size );
    vs->fps_n = ctx->streams[index]->avg_frame_rate.num; vs->fps_d = ctx->streams[index]->avg_frame_rate.den;
    while (av_read_frame( ctx, pkt ) >= 0)
    {
        if (pkt->stream_index == index)
        {
            vs->pk = realloc( vs->pk, (vs->n + 1) * sizeof(*vs->pk) );
            vs->pk[vs->n].d = malloc( pkt->size ); memcpy( vs->pk[vs->n].d, pkt->data, pkt->size );
            vs->pk[vs->n].size = pkt->size;
            vs->pk[vs->n].pts = av_rescale_q( pkt->pts, ctx->streams[index]->time_base, (AVRational){1, 10000000} );
            vs->pk[vs->n].dur = av_rescale_q( pkt->duration, ctx->streams[index]->time_base, (AVRational){1, 10000000} );
            vs->pk[vs->n].key = !!(pkt->flags & AV_PKT_FLAG_KEY);
            vs->n++;
        }
        av_packet_unref( pkt );
    }
    av_packet_free( &pkt );
    return vs->n;
}

static wg_transform_t create_t( const struct vstream *vs, UINT32 out_d1, int out_w, int out_h, int bottom_up, NTSTATUS *status, UINT32 in_d1 )
{
    struct wg_transform_create_params p = {0};
    MFVIDEOFORMAT *in = make_format( in_d1 ? in_d1 : vs->tag, vs->w, vs->h, vs->fps_n, vs->fps_d, vs->extra, vs->extra_size, 0 );
    MFVIDEOFORMAT *out = make_format( out_d1, out_w, out_h, 0, 0, NULL, 0, bottom_up );
    p.input_type.major = madeira_MFMediaType_Video; p.input_type.format_size = in->dwSize; p.input_type.u.video = in;
    p.output_type.major = madeira_MFMediaType_Video; p.output_type.format_size = out->dwSize; p.output_type.u.video = out;
    p.attrs.input_queue_length = 15; p.attrs.preserve_timestamps = TRUE;   /* video_decoder.c's */
    *status = __wine_unix_call_funcs[unix_wg_transform_create]( &p );
    free( in ); free( out );
    return p.transform;
}

static HRESULT push_t( wg_transform_t t, const struct pkt *pk, int with_flags )
{
    struct wg_transform_push_data_params pp = {0};
    struct wg_sample s = {0};
    NTSTATUS st;
    s.size = s.max_size = pk->size; s.data = (UINT64)(UINT_PTR)pk->d;
    s.pts = pk->pts; s.duration = pk->dur;
    s.flags = WG_SAMPLE_FLAG_HAS_PTS | (pk->dur ? WG_SAMPLE_FLAG_HAS_DURATION : 0) | ((pk->key && with_flags) ? WG_SAMPLE_FLAG_SYNC_POINT : 0);
    pp.transform = t; pp.sample = &s;
    st = __wine_unix_call_funcs[unix_wg_transform_push_data]( &pp );
    return st ? HRESULT_FROM_NT( st ) : pp.result;
}

static HRESULT read_t( wg_transform_t t, void *buf, UINT32 max, UINT32 stride, struct wg_sample *out )
{
    struct wg_transform_read_data_params rp = {0};
    NTSTATUS st;
    memset( out, 0, sizeof(*out) );
    out->max_size = max; out->data = (UINT64)(UINT_PTR)buf; out->stride = stride;
    rp.transform = t; rp.sample = out;
    st = __wine_unix_call_funcs[unix_wg_transform_read_data]( &rp );
    return st ? HRESULT_FROM_NT( st ) : rp.result;
}

static void destroy_t( wg_transform_t t ) { __wine_unix_call_funcs[unix_wg_transform_destroy]( &t ); }

/* Decodes the whole stream to `fmt`; returns the pictures (each layout.size bytes) in *out, the count, and the time stamps. */
static int run_stream( const struct vstream *vs, int fi, int bottom_up, UINT32 pitch, uint8_t **out, int64_t *pts, int *flags_ok )
{
    NTSTATUS st;
    wg_transform_t t = create_t( vs, fmts[fi].d1, vs->w, vs->h, bottom_up, &st, 0 );
    struct wmv_layout l;
    size_t size = wmv_layout_for( fmts[fi].fmt, vs->w, vs->h, pitch, &l );
    uint8_t *buf = malloc( size );
    int i = 0, got = 0;
    struct wg_sample s;
    HRESULT hr;
    *flags_ok = 1;
    if (st || !t) { printf( "FAIL %s: create %s status %#x\n", cur, fmts[fi].name, (unsigned)st ); failures++; return -1; }
    for (;;)
    {
        hr = read_t( t, buf, size, pitch, &s );
        if (hr == S_OK)
        {
            if (s.size != size) { printf( "FAIL %s: picture size %u, layout %zu\n", cur, (unsigned)s.size, size ); failures++; return -1; }
            if (!(s.flags & WG_SAMPLE_FLAG_PRESERVE_TIMESTAMPS) || !(s.flags & WG_SAMPLE_FLAG_HAS_PTS)) *flags_ok = 0;
            if (got < 4096) { out[got] = malloc( size ); memcpy( out[got], buf, size ); pts[got] = s.pts; }
            got++;
            continue;
        }
        if (hr != MF_E_TRANSFORM_NEED_MORE_INPUT) { printf( "FAIL %s: read hr %#x\n", cur, (unsigned)hr ); failures++; return -1; }
        if (i >= vs->n) break;
        hr = push_t( t, &vs->pk[i], 1 );
        if (hr == MF_E_NOTACCEPTING) continue;   /* read first, push again */
        if (hr != S_OK) { printf( "FAIL %s: push hr %#x\n", cur, (unsigned)hr ); failures++; return -1; }
        i++;
    }
    /* drain */
    { wg_transform_t h = t; __wine_unix_call_funcs[unix_wg_transform_drain]( &h ); }
    for (;;)
    {
        hr = read_t( t, buf, size, pitch, &s );
        if (hr != S_OK) break;
        if (got < 4096) { out[got] = malloc( size ); memcpy( out[got], buf, size ); pts[got] = s.pts; }
        got++;
    }
    free( buf );
    destroy_t( t );
    return got;
}

static double mean_y( const uint8_t *pic, const struct wmv_layout *l, int w, int h )
{
    double sum = 0; int x, y;
    for (y = 0; y < h; y++) for (x = 0; x < w; x++) sum += pic[l->offset[0] + (size_t)y * l->stride[0] + x];
    return sum / (w * h);
}

static int test_stream( const char *name, const struct vstream *vs, int min_pictures, double min_psnr_db, const uint8_t *ref_y /* w*h per picture, may be NULL */ )
{
    uint8_t *pics[NFMTS][4096];
    int64_t pts[NFMTS][4096];
    int counts[NFMTS], fi, i, x, y, flags_ok;
    struct wmv_layout l_nv12, l_i420;
    char buf[160];
    snprintf( buf, sizeof(buf), "stream %s", name ); cur = buf;
    for (fi = 0; fi < NFMTS; fi++)
    {
        counts[fi] = run_stream( vs, fi, 0, 0, pics[fi], pts[fi], &flags_ok );
        CHECK( counts[fi] >= min_pictures, "%s: %d pictures of %d packets, want at least %d", fmts[fi].name, counts[fi], vs->n, min_pictures );
        CHECK( counts[fi] == counts[0], "%s: %d pictures, NV12 gave %d", fmts[fi].name, counts[fi], counts[0] );
        CHECK( flags_ok, "%s: pictures without a time stamp or without WG_SAMPLE_FLAG_PRESERVE_TIMESTAMPS", fmts[fi].name );
    }
    for (i = 1; i < counts[0]; i++) CHECK( pts[0][i] > pts[0][i - 1], "time stamps not increasing: %lld after %lld", (long long)pts[0][i], (long long)pts[0][i - 1] );
    if (vs->n == counts[0]) for (i = 0; i < counts[0]; i++) CHECK( pts[0][i] == vs->pk[i].pts, "picture %d has time %lld, its packet %lld", i, (long long)pts[0][i], (long long)vs->pk[i].pts );
    wmv_layout_for( WMV_FMT_NV12, vs->w, vs->h, 0, &l_nv12 );
    wmv_layout_for( WMV_FMT_I420, vs->w, vs->h, 0, &l_i420 );
    for (i = 0; i < counts[0]; i++)
    {
        /* every format is the same picture: NV12 against I420 plane by plane, and YUY2/RGB against the luma */
        for (y = 0; y < vs->h; y++) for (x = 0; x < vs->w; x++)
        {
            uint8_t yv = pics[0][i][l_nv12.offset[0] + (size_t)y * l_nv12.stride[0] + x];
            CHECK( pics[3][i][l_i420.offset[0] + (size_t)y * l_i420.stride[0] + x] == yv, "I420 Y differs from NV12's at %d,%d of picture %d", x, y, i );
            CHECK( pics[1][i][l_i420.offset[0] + (size_t)y * l_i420.stride[0] + x] == yv, "YV12 Y differs at %d,%d of picture %d", x, y, i );
            CHECK( pics[4][i][(size_t)y * vs->w * 2 + x * 2] == yv, "YUY2 Y differs at %d,%d of picture %d", x, y, i );
            CHECK( pics[5][i][(size_t)y * vs->w * 2 + x * 2 + 1] == yv, "UYVY Y differs at %d,%d of picture %d", x, y, i );
            if (x % 2 == 0 && y % 2 == 0)
            {
                uint8_t u = pics[0][i][l_nv12.offset[1] + (size_t)(y / 2) * l_nv12.stride[1] + x], v = pics[0][i][l_nv12.offset[1] + (size_t)(y / 2) * l_nv12.stride[1] + x + 1];
                CHECK( pics[3][i][l_i420.offset[1] + (size_t)(y / 2) * l_i420.stride[1] + x / 2] == u, "I420 U differs at %d,%d of picture %d", x, y, i );
                CHECK( pics[3][i][l_i420.offset[2] + (size_t)(y / 2) * l_i420.stride[2] + x / 2] == v, "I420 V differs at %d,%d of picture %d", x, y, i );
                CHECK( pics[1][i][l_i420.offset[1] + (size_t)(y / 2) * l_i420.stride[1] + x / 2] == v, "YV12 holds V first" );
                CHECK( pics[4][i][(size_t)y * vs->w * 2 + x * 2 + 1] == u && pics[4][i][(size_t)y * vs->w * 2 + x * 2 + 3] == v, "YUY2 chroma differs at %d,%d of picture %d", x, y, i );
            }
        }
    }
    {
        /* RGB32: a pixel's luma recovered from it matches the picture's mean luma, and the picture is not flat */
        double sum = 0, sumsq = 0; int n = vs->w * vs->h;
        const uint8_t *p = pics[7][0];
        for (y = 0; y < vs->h; y++) for (x = 0; x < vs->w; x++)
        {
            double lum = 16 + 0.257 * p[(size_t)y * vs->w * 4 + x * 4 + 2] + 0.504 * p[(size_t)y * vs->w * 4 + x * 4 + 1] + 0.098 * p[(size_t)y * vs->w * 4 + x * 4];
            sum += lum; sumsq += lum * lum;
        }
        {
            double my = mean_y( pics[0][0], &l_nv12, vs->w, vs->h ), mr = sum / n, var = sumsq / n - mr * mr;
            CHECK( fabs( my - mr ) < 6.0, "RGB32 mean luma %.1f, the Y plane's %.1f", mr, my );
            CHECK( var > 4.0, "the first picture is flat (variance %.2f)", var );
        }
    }
    /* bottom-up: the same picture, rows reversed */
    {
        uint8_t *bpics[4096]; int64_t bpts[4096]; int bn;
        bn = run_stream( vs, 7, 1, 0, bpics, bpts, &flags_ok );
        CHECK( bn == counts[7], "bottom-up RGB32 gave %d pictures, top-down %d", bn, counts[7] );
        for (y = 0; y < vs->h; y++) CHECK( !memcmp( bpics[0] + (size_t)y * vs->w * 4, pics[7][0] + (size_t)(vs->h - 1 - y) * vs->w * 4, vs->w * 4 ), "bottom-up row %d", y );
    }
    {
        /* a 2D buffer: NV12 with a pitch of 128 holds the same rows */
        uint8_t *ppics[4096]; int64_t ppts[4096]; int pn; struct wmv_layout lp;
        wmv_layout_for( WMV_FMT_NV12, vs->w, vs->h, (vs->w + 63) & ~63, &lp );
        pn = run_stream( vs, 0, 0, (vs->w + 63) & ~63, ppics, ppts, &flags_ok );
        CHECK( pn == counts[0], "pitched NV12 gave %d pictures, packed %d", pn, counts[0] );
        for (y = 0; y < vs->h; y++) CHECK( !memcmp( ppics[0] + (size_t)y * lp.stride[0], pics[0][0] + (size_t)y * l_nv12.stride[0], vs->w ), "pitched row %d", y );
    }
    if (ref_y)
    {
        double worst = 1e9;
        for (i = 0; i < counts[0] && i < 64; i++)
        {
            double mse = 0;
            /* the picture's time says which source frame it is (12 frames a second): this also checks the time stamps */
            int k = (int)((pts[0][i] * 12 + 5000000) / 10000000);
            CHECK( k >= 0 && k < 24, "picture %d has time %lld: no such source frame", i, (long long)pts[0][i] );
            for (y = 0; y < vs->h; y++) for (x = 0; x < vs->w; x++)
            {
                double d = (double)pics[0][i][l_nv12.offset[0] + (size_t)y * l_nv12.stride[0] + x] - ref_y[(size_t)k * vs->w * vs->h + (size_t)y * vs->w + x];
                mse += d * d;
            }
            mse /= vs->w * vs->h;
            { double psnr = mse < 1e-9 ? 99.0 : 10 * log10( 255.0 * 255.0 / mse ); if (psnr < worst) worst = psnr; }
        }
        CHECK( worst >= min_psnr_db, "worst PSNR against the source %.1f dB, want %.1f", worst, min_psnr_db );
        printf( "     worst PSNR against the encoder's input: %.1f dB\n", worst );
    }
    printf( "PASS %s: %d packets -> %d pictures in all %d formats, same picture in each, times, bottom-up, pitch\n", name, vs->n, counts[0], NFMTS );
    return 1;
}

/* ---- a WMV2 + WMAv2 ASF made with FFmpeg's own encoders (test only) ---- */
/* The encoder's own packets, in 100 ns, for a decode that does not depend on the muxer's timing. */
static void keep_raw( struct vstream *raw, const AVPacket *pkt, const AVCodecContext *vx )
{
    raw->pk = realloc( raw->pk, (raw->n + 1) * sizeof(*raw->pk) );
    raw->pk[raw->n].d = malloc( pkt->size ); memcpy( raw->pk[raw->n].d, pkt->data, pkt->size );
    raw->pk[raw->n].size = pkt->size;
    raw->pk[raw->n].pts = av_rescale_q( pkt->pts, vx->time_base, (AVRational){1, 10000000} );
    raw->pk[raw->n].dur = av_rescale_q( 1, vx->time_base, (AVRational){1, 10000000} );
    raw->pk[raw->n].key = !!(pkt->flags & AV_PKT_FLAG_KEY);
    raw->n++;
}

static uint8_t *make_asf( size_t *size, uint8_t **ref_y, struct vstream *raw, int w, int h, int frames )
{
    const AVCodec *vc = avcodec_find_encoder( AV_CODEC_ID_WMV2 ), *ac = avcodec_find_encoder( AV_CODEC_ID_WMAV2 );
    AVCodecContext *vx = avcodec_alloc_context3( vc ), *ax = avcodec_alloc_context3( ac );
    AVFormatContext *oc = NULL;
    AVFrame *vf = av_frame_alloc(), *af = av_frame_alloc();
    AVPacket *pkt = av_packet_alloc();
    AVStream *vs, *as;
    uint8_t *out = NULL;
    int f, x, y, ch;
    int64_t apts = 0;

    vx->width = w; vx->height = h; vx->pix_fmt = AV_PIX_FMT_YUV420P; vx->time_base = (AVRational){1, 12}; vx->framerate = (AVRational){12, 1};
    vx->bit_rate = 400000; vx->gop_size = 6; vx->max_b_frames = 0;
    if (avcodec_open2( vx, vc, NULL ) < 0) return NULL;
    raw->extra_size = vx->extradata_size; memcpy( raw->extra, vx->extradata, vx->extradata_size > 64 ? 64 : vx->extradata_size );
    ax->sample_rate = 44100; ax->bit_rate = 64000; ax->sample_fmt = AV_SAMPLE_FMT_FLTP; av_channel_layout_default( &ax->ch_layout, 1 );
    if (avcodec_open2( ax, ac, NULL ) < 0) return NULL;
    avformat_alloc_output_context2( &oc, NULL, "asf", NULL );
    if (!oc || avio_open_dyn_buf( &oc->pb ) < 0) return NULL;
    vs = avformat_new_stream( oc, NULL ); avcodec_parameters_from_context( vs->codecpar, vx ); vs->time_base = vx->time_base;
    as = avformat_new_stream( oc, NULL ); avcodec_parameters_from_context( as->codecpar, ax ); as->time_base = (AVRational){1, 44100};
    if (avformat_write_header( oc, NULL ) < 0) return NULL;

    *ref_y = malloc( (size_t)frames * w * h );
    vf->format = AV_PIX_FMT_YUV420P; vf->width = w; vf->height = h;
    av_frame_get_buffer( vf, 0 );
    for (f = 0; f < frames; f++)
    {
        av_frame_make_writable( vf );
        /* a smooth moving pattern: a codec can follow it */
        for (y = 0; y < h; y++) for (x = 0; x < w; x++)
            vf->data[0][y * vf->linesize[0] + x] = (uint8_t)(40 + 90 * (0.5 + 0.5 * sin( (x + 5 * f) * 0.12 )) + 70 * (0.5 + 0.5 * cos( y * 0.1 )));
        for (y = 0; y < h / 2; y++) for (x = 0; x < w / 2; x++) { vf->data[1][y * vf->linesize[1] + x] = (uint8_t)(110 + x); vf->data[2][y * vf->linesize[2] + x] = (uint8_t)(150 - y); }
        for (y = 0; y < h; y++) memcpy( *ref_y + (size_t)f * w * h + (size_t)y * w, vf->data[0] + y * vf->linesize[0], w );
        vf->pts = f;
        avcodec_send_frame( vx, vf );
        while (avcodec_receive_packet( vx, pkt ) >= 0)
        {
            keep_raw( raw, pkt, vx );
            av_packet_rescale_ts( pkt, vx->time_base, vs->time_base ); pkt->stream_index = vs->index;
            av_interleaved_write_frame( oc, pkt );
        }
    }
    avcodec_send_frame( vx, NULL );
    while (avcodec_receive_packet( vx, pkt ) >= 0) { keep_raw( raw, pkt, vx ); av_packet_rescale_ts( pkt, vx->time_base, vs->time_base ); pkt->stream_index = vs->index; av_interleaved_write_frame( oc, pkt ); }
    /* one second of a 440 Hz tone */
    af->format = AV_SAMPLE_FMT_FLTP; af->nb_samples = ax->frame_size; av_channel_layout_copy( &af->ch_layout, &ax->ch_layout ); av_frame_get_buffer( af, 0 );
    for (f = 0; f < 44100 / ax->frame_size; f++)
    {
        av_frame_make_writable( af );
        for (x = 0; x < af->nb_samples; x++) for (ch = 0; ch < 1; ch++) ((float *)af->extended_data[ch])[x] = 0.4f * (float)sin( 2 * M_PI * 440.0 * (apts + x) / 44100 );
        af->pts = apts; apts += af->nb_samples;
        avcodec_send_frame( ax, af );
        while (avcodec_receive_packet( ax, pkt ) >= 0) { pkt->stream_index = as->index; av_interleaved_write_frame( oc, pkt ); }
    }
    avcodec_send_frame( ax, NULL );
    while (avcodec_receive_packet( ax, pkt ) >= 0) { pkt->stream_index = as->index; av_interleaved_write_frame( oc, pkt ); }
    av_write_trailer( oc );
    { uint8_t *buf; int n = avio_close_dyn_buf( oc->pb, &buf ); out = malloc( n ); memcpy( out, buf, n ); *size = n; av_free( buf ); }
    return out;
}

static int test_refusals_and_lifetimes( const struct vstream *wmv1 )
{
    NTSTATUS st;
    wg_transform_t t;
    struct vstream fake = *wmv1;
    /* STRUCT_C: main profile, no loop filter, no B frames (libavcodec rejects an all-zero one: simple profile needs FASTUVMC) */
    static const uint8_t struct_c[4] = { 0x40, 0x01, 0x00, 0x00 };
    cur = "refusals";

    /* the support check wmv_decoder_create makes: WMV3 1920x1080 -> I420, no private data */
    memset( &fake, 0, sizeof(fake) ); fake.tag = FOURCC( 'W', 'M', 'V', '3' ); fake.w = 1920; fake.h = 1080;
    t = create_t( &fake, FOURCC( 'I', '4', '2', '0' ), 1920, 1080, 0, &st, 0 );
    CHECK( st == STATUS_SUCCESS && t, "the support check (WMV3 1920x1080 -> I420, no private data) must succeed: %#x", (unsigned)st );
    { struct wmv_transform *w = wmv_get( t ); CHECK( w && !w->avctx, "no private data: nothing is opened until the first sample" ); }
    { struct wg_sample s; uint8_t b[16]; HRESULT hr = read_t( t, b, sizeof(b), 0, &s ); CHECK( hr == MF_E_TRANSFORM_NEED_MORE_INPUT, "an unopened decoder has nothing to read: %#x", (unsigned)hr ); }
    { struct pkt pk = { (uint8_t[]){ 1, 2, 3, 4 }, 4, 0, 0, 1 }; HRESULT hr = push_t( t, &pk, 1 ); CHECK( hr == E_FAIL, "a sample for a WMV3 decoder with no private data is refused (E_FAIL), not decoded: %#x", (unsigned)hr ); }
    destroy_t( t );

    /* private data: it opens at create */
    fake.extra_size = 4; memcpy( fake.extra, struct_c, 4 );
    t = create_t( &fake, FOURCC( 'N', 'V', '1', '2' ), 1920, 1080, 0, &st, 0 );
    CHECK( st == STATUS_SUCCESS && t && wmv_get( t )->avctx, "WMV3 with private data opens at create: %#x", (unsigned)st );
    destroy_t( t );
    fake.tag = FOURCC( 'W', 'V', 'C', '1' ); fake.extra_size = 0;
    t = create_t( &fake, FOURCC( 'N', 'V', '1', '2' ), 1920, 1080, 0, &st, 0 );
    CHECK( st == STATUS_SUCCESS && t, "WVC1 support check succeeds: %#x", (unsigned)st );
    destroy_t( t );
    fake.tag = FOURCC( 'W', 'M', 'V', 'A' );
    t = create_t( &fake, FOURCC( 'Y', 'U', 'Y', '2' ), 1920, 1080, 0, &st, 0 );
    CHECK( st == STATUS_SUCCESS && t, "WMVA is VC-1: %#x", (unsigned)st );
    destroy_t( t );

    /* refusals: the handle stays 0, the status is the one every video type got before */
    t = 77; st = 0;
    fake.tag = FOURCC( 'H', '2', '6', '4' ); fake.w = 640; fake.h = 480;
    t = create_t( &fake, FOURCC( 'N', 'V', '1', '2' ), 640, 480, 0, &st, 0 );
    CHECK( st == STATUS_NOT_SUPPORTED && t == 0, "H.264 is refused as before: %#x handle %llu", (unsigned)st, (unsigned long long)t );
    fake.tag = FOURCC( 'W', 'M', 'V', 'P' );
    t = create_t( &fake, FOURCC( 'N', 'V', '1', '2' ), 640, 480, 0, &st, 0 );
    CHECK( st == STATUS_NOT_SUPPORTED && t == 0, "WMVP (the image variants) is refused: %#x", (unsigned)st );
    fake.tag = FOURCC( 'W', 'M', 'V', '2' );
    t = create_t( &fake, FOURCC( 'N', 'V', '1', '1' ), 640, 480, 0, &st, 0 );
    CHECK( st == STATUS_NOT_SUPPORTED && t == 0, "NV11 output is refused: %#x", (unsigned)st );
    t = create_t( &fake, 41 /* RGB8 */, 640, 480, 0, &st, 0 );
    CHECK( st == STATUS_NOT_SUPPORTED && t == 0, "RGB8 output is refused: %#x", (unsigned)st );
    t = create_t( &fake, FOURCC( 'N', 'V', '1', '2' ), 0, 0, 0, &st, 0 );
    CHECK( st == STATUS_SUCCESS && t && wmv_get( t )->out_width == 640, "an output type without a size takes the input's: %#x", (unsigned)st );
    destroy_t( t );
    {
        /* a video input with an audio output type (and the reverse) is refused like any mismatch */
        struct wg_transform_create_params p = {0};
        MFVIDEOFORMAT *in = make_format( FOURCC( 'W', 'M', 'V', '2' ), 64, 48, 0, 0, NULL, 0, 0 );
        WAVEFORMATEX wfx = { .wFormatTag = WAVE_FORMAT_PCM, .nChannels = 1, .nSamplesPerSec = 44100, .wBitsPerSample = 16, .nBlockAlign = 2, .nAvgBytesPerSec = 88200 };
        p.input_type.major = madeira_MFMediaType_Video; p.input_type.format_size = in->dwSize; p.input_type.u.video = in;
        p.output_type.major = madeira_MFMediaType_Audio; p.output_type.format_size = sizeof(wfx); p.output_type.u.audio = &wfx;
        st = __wine_unix_call_funcs[unix_wg_transform_create]( &p );
        CHECK( st == STATUS_NOT_SUPPORTED && !p.transform, "video in, audio out: %#x", (unsigned)st );
        free( in );
    }
    {
        /* the WMA path through the same table is untouched: create, ask the state, destroy */
        struct wg_transform_create_params p = {0};
        struct { WAVEFORMATEX w; uint8_t extra[10]; } in = { { .wFormatTag = WAVE_FORMAT_WMAUDIO2, .nChannels = 1, .nSamplesPerSec = 44100, .nBlockAlign = 743, .nAvgBytesPerSec = 16000, .wBitsPerSample = 16, .cbSize = 10 }, { 0, 0, 0, 0, 1, 0, 0, 0, 0, 0 } };
        WAVEFORMATEX out = { .wFormatTag = WAVE_FORMAT_PCM, .nChannels = 1, .nSamplesPerSec = 44100, .wBitsPerSample = 16, .nBlockAlign = 2, .nAvgBytesPerSec = 88200 };
        struct wg_transform_get_status_params gs = {0};
        p.input_type.major = madeira_MFMediaType_Audio; p.input_type.format_size = sizeof(in); p.input_type.u.audio = &in.w;
        p.output_type.major = madeira_MFMediaType_Audio; p.output_type.format_size = sizeof(out); p.output_type.u.audio = &out;
        st = __wine_unix_call_funcs[unix_wg_transform_create]( &p );
        CHECK( st == STATUS_SUCCESS && p.transform && !wmv_is_handle( p.transform ), "a WMA transform is still a WMA transform: %#x", (unsigned)st );
        gs.transform = p.transform;
        CHECK( __wine_unix_call_funcs[unix_wg_transform_get_status]( &gs ) == STATUS_SUCCESS && gs.accepts_input, "WMA get_status through the shared entry" );
        { wg_transform_t h = p.transform; CHECK( __wine_unix_call_funcs[unix_wg_transform_drain]( &h ) == STATUS_SUCCESS, "WMA drain" );
          CHECK( __wine_unix_call_funcs[unix_wg_transform_flush]( &h ) == STATUS_SUCCESS, "WMA flush" ); }
        destroy_t( p.transform );
    }
    CHECK( __wine_unix_call_funcs[unix_wg_transform_destroy]( &(wg_transform_t){ 0 } ) == STATUS_INVALID_HANDLE, "destroying handle 0 is an invalid handle, as before" );
    printf( "PASS refusals: the support check opens nothing, private data opens at create, non-WMV and unsupported outputs are refused, WMA untouched\n" );
    return 1;
}

static int test_behaviours( const struct vstream *vs )
{
    NTSTATUS st;
    wg_transform_t t;
    struct wmv_layout l;
    size_t size;
    uint8_t *buf;
    struct wg_sample s;
    HRESULT hr;
    int i, pictures = 0, notacc = 0;
    cur = "behaviours";

    t = create_t( vs, FOURCC( 'N', 'V', '1', '2' ), vs->w, vs->h, 0, &st, 0 );
    CHECK( !st && t, "create" );
    size = wmv_layout_for( WMV_FMT_NV12, vs->w, vs->h, 0, &l );
    buf = malloc( size );
    {
        struct wg_transform_get_status_params gs = { .transform = t };
        CHECK( !__wine_unix_call_funcs[unix_wg_transform_get_status]( &gs ) && gs.accepts_input, "a fresh decoder accepts input" );
    }
    /* push without reading until the decoder says no */
    for (i = 0; i < vs->n; i++)
    {
        hr = push_t( t, &vs->pk[i], 1 );
        if (hr == MF_E_NOTACCEPTING) { notacc++; break; }
        CHECK( hr == S_OK, "push %d: %#x", i, (unsigned)hr );
    }
    CHECK( notacc == 1 || i == vs->n, "MF_E_NOTACCEPTING when pictures wait" );
    {
        struct wg_transform_get_status_params gs = { .transform = t };
        __wine_unix_call_funcs[unix_wg_transform_get_status]( &gs );
        CHECK( notacc ? !gs.accepts_input : 1, "get_status says it does not accept input while a picture waits" );
    }
    /* a buffer that is too small keeps the picture */
    hr = read_t( t, buf, (UINT32)size - 1, 0, &s );
    CHECK( hr == MF_E_BUFFERTOOSMALL && s.size == size, "a too-small buffer: %#x, size %u", (unsigned)hr, (unsigned)s.size );
    hr = read_t( t, buf, (UINT32)size, 0, &s );
    CHECK( hr == S_OK && s.size == size, "the same picture is there for a large enough buffer: %#x", (unsigned)hr );
    pictures++;
    /* flush: nothing is left; the next picture is a discontinuity */
    { wg_transform_t h = t; CHECK( !__wine_unix_call_funcs[unix_wg_transform_flush]( &h ), "flush" ); }
    hr = read_t( t, buf, (UINT32)size, 0, &s );
    CHECK( hr == MF_E_TRANSFORM_NEED_MORE_INPUT, "nothing after a flush: %#x", (unsigned)hr );
    for (i = 0; i < vs->n; i++)
    {
        hr = push_t( t, &vs->pk[i], 1 );
        if (hr == MF_E_NOTACCEPTING) { read_t( t, buf, (UINT32)size, 0, &s ); i--; continue; }
        hr = read_t( t, buf, (UINT32)size, 0, &s );
        if (hr == S_OK) break;
    }
    CHECK( hr == S_OK && (s.flags & WG_SAMPLE_FLAG_DISCONTINUITY), "the first picture after a flush is a discontinuity (flags %#x)", (unsigned)s.flags );
    hr = read_t( t, buf, (UINT32)size, 0, &s );
    CHECK( hr != S_OK || !(s.flags & WG_SAMPLE_FLAG_DISCONTINUITY), "and the next one is not" );
    /* an empty sample is accepted and ignored (an empty packet would end the stream) */
    { struct pkt pk = { (uint8_t *)"", 0, 0, 0, 0 }; hr = push_t( t, &pk, 0 ); CHECK( hr == S_OK, "an empty sample: %#x", (unsigned)hr ); }
    /* drain, then new input starts over */
    { wg_transform_t h = t; CHECK( !__wine_unix_call_funcs[unix_wg_transform_drain]( &h ), "drain" ); }
    while (read_t( t, buf, (UINT32)size, 0, &s ) == S_OK) pictures++;
    hr = push_t( t, &vs->pk[0], 1 );
    CHECK( hr == S_OK, "a sample after a drain starts a new stream: %#x", (unsigned)hr );
    hr = read_t( t, buf, (UINT32)size, 0, &s );
    CHECK( hr == S_OK || hr == MF_E_TRANSFORM_NEED_MORE_INPUT, "and reads normally: %#x", (unsigned)hr );
    /* set_output_type to RGB32 bottom-up changes the format of what is read next */
    {
        struct wg_transform_set_output_type_params sp = {0};
        struct wg_transform_get_output_type_params gp = {0};
        MFVIDEOFORMAT *out = make_format( 22, vs->w, vs->h, 0, 0, NULL, 0, 1 );
        sp.transform = t; sp.media_type.major = madeira_MFMediaType_Video; sp.media_type.format_size = out->dwSize; sp.media_type.u.video = out;
        CHECK( !__wine_unix_call_funcs[unix_wg_transform_set_output_type]( &sp ), "set_output_type to RGB32" );
        gp.transform = t;
        CHECK( __wine_unix_call_funcs[unix_wg_transform_get_output_type]( &gp ) == STATUS_BUFFER_TOO_SMALL && gp.media_type.format_size == out->dwSize, "get_output_type is two-phase" );
        gp.media_type.u.video = calloc( 1, gp.media_type.format_size );
        CHECK( !__wine_unix_call_funcs[unix_wg_transform_get_output_type]( &gp ) && !memcmp( gp.media_type.u.video, out, out->dwSize ), "get_output_type returns the type that was set" );
        free( gp.media_type.u.video );
        sp.media_type.u.video = make_format( FOURCC( 'N', 'V', '1', '1' ), vs->w, vs->h, 0, 0, NULL, 0, 0 );
        CHECK( __wine_unix_call_funcs[unix_wg_transform_set_output_type]( &sp ) == STATUS_NOT_SUPPORTED, "set_output_type to NV11 is refused" );
        free( sp.media_type.u.video );
        free( out );
    }
    { wg_transform_t h = t; __wine_unix_call_funcs[unix_wg_transform_flush]( &h ); }
    free( buf );
    destroy_t( t );
    printf( "PASS behaviours: NOTACCEPTING, a too-small buffer, flush, drain, empty sample, set/get_output_type, get_status\n" );
    return pictures > 0;
}

int main( int argc, char **argv )
{
    struct mem m;
    AVFormatContext *ctx;
    struct vstream vs;
    int i, w = 160, h = 96, frames = 24;
    uint8_t *asf, *ref_y;
    size_t asf_size;
    struct vstream raw;
    (void)argc;

    if (!test_convert()) return 1;
    if (!test_layout_pad_crop()) return 1;
    if (!test_ts()) return 1;

    /* Wine's own WMV1 files */
    for (i = 1; i < argc; i++)
    {
        int vi;
        if (load_file( argv[i], &m )) { printf( "FAIL cannot read %s\n", argv[i] ); return 1; }
        if (!(ctx = open_mem( &m ))) { printf( "FAIL cannot open %s\n", argv[i] ); return 1; }
        for (vi = 0; vi < (int)ctx->nb_streams && ctx->streams[vi]->codecpar->codec_type != AVMEDIA_TYPE_VIDEO; vi++) ;
        if (vi == (int)ctx->nb_streams) { printf( "FAIL no video in %s\n", argv[i] ); return 1; }
        collect( ctx, vi, &vs );
        if (!test_stream( strrchr( argv[i], '/' ) ? strrchr( argv[i], '/' ) + 1 : argv[i], &vs, vs.n > 4 ? vs.n - 2 : 1, 0, NULL )) return 1;
        if (i == 1 && !test_refusals_and_lifetimes( &vs )) return 1;
        if (i == 1 && !test_behaviours( &vs )) return 1;
    }

    /* WMV2, encoded here */
    memset( &raw, 0, sizeof(raw) );
    raw.w = w; raw.h = h; raw.tag = FOURCC( 'W', 'M', 'V', '2' ); raw.fps_n = 12; raw.fps_d = 1;
    if (!(asf = make_asf( &asf_size, &ref_y, &raw, w, h, frames ))) { printf( "FAIL the test encoders did not build an ASF file\n" ); return 1; }
    m.d = asf; m.n = asf_size; m.pos = 0;
    if (!(ctx = open_mem( &m ))) { printf( "FAIL cannot demux the generated ASF\n" ); return 1; }
    printf( "generated ASF: %zu bytes, %u streams, video %s %dx%d tag %.4s\n", asf_size, ctx->nb_streams, avcodec_get_name( ctx->streams[0]->codecpar->codec_id ),
            ctx->streams[0]->codecpar->width, ctx->streams[0]->codecpar->height, (const char *)&ctx->streams[0]->codecpar->codec_tag );
    { const char *o = getenv( "MADEIRA_ASF_OUT" ); if (o) { FILE *f = fopen( o, "wb" ); fwrite( asf, 1, asf_size, f ); fclose( f ); } }
    collect( ctx, 0, &vs );
    /* the encoder's packets: every frame comes back, at the right time, close to the source */
    if (raw.n != frames) { printf( "FAIL the encoder made %d packets for %d frames\n", raw.n, frames ); return 1; }
    if (!test_stream( "generated wmv2 (encoder packets)", &raw, frames, 28.0, ref_y )) return 1;
    /* the same stream through FFmpeg's ASF muxer and demuxer: it decodes (the muxer's timing is its own) */
    if (!test_stream( "generated wmv2 (asf)", &vs, vs.n > 4 ? vs.n - 2 : 1, 0, NULL )) return 1;
    printf( "%s: %d failure(s)\n", failures ? "FAIL" : "PASS", failures );
    return failures ? 1 : 0;
}
"""

# ------------------------------------------------------------------ 4. the demuxer harness
HARNESS_DMO = r"""
#include "winedmo_unixlib_ios.c"

static int failures;
static const char *cur = "?";
#define CHECK(c, ...) do { if (!(c)) { printf( "FAIL %s: ", cur ); printf( __VA_ARGS__ ); printf( "\n" ); failures++; return 0; } } while (0)

/* The PE side's callbacks, as winedmo.dll's main.c has them, with NtCallbackReturn replaced by a global. */
static uint8_t *file_data; static size_t file_size; static size_t file_pos;
static ULONG g_ret_ulong; static UINT64 g_ret_u64; static int g_seen_dispatch;
static void *g_ret_ptr; static ULONG g_ret_len;

static NTSTATUS WINAPI pe_seek_callback( void *args, ULONG size )
{
    struct seek_callback_params *params = args;
    file_pos = params->offset > (INT64)file_size ? file_size : params->offset;
    g_ret_u64 = file_pos; g_ret_ptr = &g_ret_u64; g_ret_len = sizeof(g_ret_u64);
    return STATUS_SUCCESS;
}

static NTSTATUS WINAPI pe_read_callback( void *args, ULONG size )
{
    struct read_callback_params *params = args;
    struct stream_context *context = (struct stream_context *)(UINT_PTR)params->context;
    size_t n = file_size - file_pos;
    if (n > (size_t)params->size) n = params->size;
    memcpy( context->buffer, file_data + file_pos, n );
    file_pos += n;
    g_ret_ulong = n; g_ret_ptr = &g_ret_ulong; g_ret_len = sizeof(g_ret_ulong);
    return STATUS_SUCCESS;
}

/* What ntdll's KeUserModeCallback does for winedmo.dll: run the callback the params name and hand its answer back. */
NTSTATUS KeUserModeCallback( ULONG id, const void *args, ULONG len, void **ret_ptr, ULONG *ret_len )
{
    const struct dispatch_callback_params *p = args;
    NTSTATUS (WINAPI *fn)( void *, ULONG ) = (void *)(UINT_PTR)p->callback;
    NTSTATUS st = fn( (void *)args, len );
    g_seen_dispatch++;
    *ret_ptr = g_ret_ptr; *ret_len = g_ret_len;
    return st;
}

static struct stream_context *new_context( void )
{
    struct stream_context *c = malloc( sizeof(*c) + 0x40000 );
    c->stream = 0; c->length = file_size; c->position = 0; c->capacity = 0x40000; c->size = 0;
    return c;
}

static int load_file( const char *path )
{
    FILE *f = fopen( path, "rb" );
    if (!f) return -1;
    fseek( f, 0, SEEK_END ); file_size = ftell( f ); fseek( f, 0, SEEK_SET );
    free( file_data ); file_data = malloc( file_size );
    if (fread( file_data, 1, file_size, f ) != file_size) return -1;
    fclose( f ); file_pos = 0;
    return 0;
}

static NTSTATUS call( int code, void *args ) { return __wine_unix_call_funcs[code]( args ); }

static int test_file( const char *path, int want_streams, int want_video_w, int want_video_h, UINT32 want_tag, int want_audio_tag, int clean_eof )
{
    struct process_attach_params attach = { (UINT_PTR)pe_seek_callback, (UINT_PTR)pe_read_callback };
    struct demuxer_create_params cp = {0};
    struct demuxer_destroy_params dp = {0};
    struct stream_context *ctx;
    char name[200];
    int i, packets[4] = {0}, first_stream = -1, big = 0, small_retries = 0;
    NTSTATUS st;

    snprintf( name, sizeof(name), "winedmo %s", strrchr( path, '/' ) ? strrchr( path, '/' ) + 1 : path ); cur = name;
    CHECK( !load_file( path ), "cannot read %s", path );
    CHECK( call( unix_process_attach, &attach ) == STATUS_SUCCESS, "process_attach" );
    { struct demuxer_check_params c = {0}; strcpy( c.mime_type, "video/x-ms-asf" ); CHECK( call( unix_demuxer_check, &c ) == STATUS_SUCCESS, "asf is supported" );
      strcpy( c.mime_type, "video/x-ms-wmv" ); CHECK( call( unix_demuxer_check, &c ) == STATUS_SUCCESS, "wmv is supported" );
      strcpy( c.mime_type, "audio/x-ms-wma" ); CHECK( call( unix_demuxer_check, &c ) == STATUS_SUCCESS, "wma is supported" );
      strcpy( c.mime_type, "video/avi" ); CHECK( call( unix_demuxer_check, &c ) == STATUS_NOT_SUPPORTED, "avi is not built" );
      strcpy( c.mime_type, "video/mpeg" ); CHECK( call( unix_demuxer_check, &c ) == STATUS_NOT_SUPPORTED, "mpeg is not built" );
      strcpy( c.mime_type, "text/plain" ); CHECK( call( unix_demuxer_check, &c ) == STATUS_NOT_SUPPORTED, "an unknown type" ); }

    ctx = new_context();
    cp.url = "movie/intro.wmv"; cp.context = ctx;
    st = call( unix_demuxer_create, &cp );
    CHECK( st == STATUS_SUCCESS && cp.demuxer.handle, "demuxer_create %#x", (unsigned)st );
    CHECK( cp.stream_count == (UINT32)want_streams, "%u streams, want %d", (unsigned)cp.stream_count, want_streams );
    CHECK( !strcmp( cp.mime_type, "video/x-ms-wmv" ), "mime %s (an asf file named .wmv)", cp.mime_type );
    CHECK( g_seen_dispatch > 0, "the demuxer read through the PE callbacks" );

    for (i = 0; i < (int)cp.stream_count; i++)
    {
        struct demuxer_stream_type_params tp = { .demuxer = cp.demuxer, .stream = i };
        st = call( unix_demuxer_stream_type, &tp );
        CHECK( st == STATUS_BUFFER_TOO_SMALL && tp.media_type.format_size >= sizeof(WAVEFORMATEX), "stream %d type, first call: %#x size %u", i, (unsigned)st, (unsigned)tp.media_type.format_size );
        tp.media_type.format = calloc( 1, tp.media_type.format_size );
        st = call( unix_demuxer_stream_type, &tp );
        CHECK( st == STATUS_SUCCESS, "stream %d type, second call: %#x", i, (unsigned)st );
        if (IsEqualGUID( &tp.media_type.major, &dmo_MFMediaType_Video ))
        {
            MFVIDEOFORMAT *v = tp.media_type.video;
            CHECK( v->dwSize == tp.media_type.format_size && v->videoInfo.dwWidth == (DWORD)want_video_w && v->videoInfo.dwHeight == (DWORD)want_video_h && v->guidFormat.Data1 == want_tag,
                   "video type %ux%u tag %08x, want %dx%d %08x", (unsigned)v->videoInfo.dwWidth, (unsigned)v->videoInfo.dwHeight, (unsigned)v->guidFormat.Data1, want_video_w, want_video_h, (unsigned)want_tag );
            CHECK( v->videoInfo.PixelAspectRatio.Numerator == 1 && v->videoInfo.PixelAspectRatio.Denominator == 1, "aspect ratio defaults to 1:1" );
        }
        else
        {
            WAVEFORMATEX *a = tp.media_type.audio;
            CHECK( IsEqualGUID( &tp.media_type.major, &dmo_MFMediaType_Audio ) && a->wFormatTag == want_audio_tag && a->nSamplesPerSec == 44100 && a->nChannels == 1 && a->cbSize + sizeof(*a) == tp.media_type.format_size,
                   "audio type tag %#x %u Hz %u ch cbSize %u of %u", a->wFormatTag, (unsigned)a->nSamplesPerSec, a->nChannels, a->cbSize, (unsigned)tp.media_type.format_size );
        }
        free( tp.media_type.format );
        { struct demuxer_stream_lang_params lp = { .demuxer = cp.demuxer, .stream = i }; CHECK( call( unix_demuxer_stream_lang, &lp ) == STATUS_NOT_FOUND, "no language tag" ); }
        { struct demuxer_stream_name_params np = { .demuxer = cp.demuxer, .stream = i }; CHECK( call( unix_demuxer_stream_name, &np ) == STATUS_NOT_FOUND, "no title tag" ); }
    }
    { struct demuxer_stream_type_params tp = { .demuxer = cp.demuxer, .stream = 99 }; CHECK( call( unix_demuxer_stream_type, &tp ) == STATUS_NOT_FOUND, "a stream out of range" ); }

    /* read every packet through a deliberately small buffer first: BUFFER_TOO_SMALL keeps the packet */
    {
        uint8_t *buf = malloc( 1 << 20 );
        int64_t first_pts = INT64_MIN; int total = 0;
        for (;;)
        {
            struct demuxer_read_params rp = { .demuxer = cp.demuxer };
            rp.sample.data = (UINT64)(UINT_PTR)buf; rp.sample.size = 8;
            st = call( unix_demuxer_read, &rp );
            if (st == STATUS_BUFFER_TOO_SMALL)
            {
                UINT64 need = rp.sample.size;
                small_retries++;
                CHECK( need > 8, "BUFFER_TOO_SMALL reports the size (%llu)", (unsigned long long)need );
                memset( &rp.sample, 0, sizeof(rp.sample) ); rp.sample.data = (UINT64)(UINT_PTR)buf; rp.sample.size = 1 << 20;
                st = call( unix_demuxer_read, &rp );
                CHECK( st == STATUS_SUCCESS && rp.sample.size == need, "the retry returns the same packet: %#x size %llu want %llu", (unsigned)st, (unsigned long long)rp.sample.size, (unsigned long long)need );
            }
            else if (st == STATUS_SUCCESS) big++;
            else break;
            CHECK( rp.stream < 4, "stream index" );
            packets[rp.stream]++; total++;
            if (first_stream < 0) { first_stream = rp.stream; first_pts = rp.sample.pts; }
        }
        CHECK( st == STATUS_END_OF_FILE || (!clean_eof && st == STATUS_UNSUCCESSFUL), "the end of the file is STATUS_END_OF_FILE, got %#x", (unsigned)st );
        CHECK( total > 3 && small_retries > 0, "%d packets, %d through BUFFER_TOO_SMALL", total, small_retries );
        /* seek to the start and read the first packet again */
        { struct demuxer_seek_params sp = { .demuxer = cp.demuxer, .timestamp = 0 }; CHECK( call( unix_demuxer_seek, &sp ) == STATUS_SUCCESS, "seek to 0" ); }
        {
            struct demuxer_read_params rp = { .demuxer = cp.demuxer };
            rp.sample.data = (UINT64)(UINT_PTR)buf; rp.sample.size = 1 << 20;
            st = call( unix_demuxer_read, &rp );
            CHECK( st == STATUS_SUCCESS && (int)rp.stream == first_stream && rp.sample.pts == first_pts, "after the seek the first packet is back: stream %u pts %lld (was %lld)", (unsigned)rp.stream, (long long)rp.sample.pts, (long long)first_pts );
        }
        free( buf );
    }
    dp.demuxer = cp.demuxer;
    CHECK( call( unix_demuxer_destroy, &dp ) == STATUS_SUCCESS && dp.context == ctx, "destroy hands the stream context back" );
    free( ctx );
    printf( "PASS %s: %d streams, types, %d video + %d audio packets, %d BUFFER_TOO_SMALL retries, seek, EOF\n", name, want_streams, packets[0], packets[1], small_retries );
    return 1;
}

static int test_garbage( void )
{
    struct demuxer_create_params cp = {0};
    struct stream_context *ctx;
    int i;
    cur = "winedmo garbage";
    file_size = 5000; free( file_data ); file_data = malloc( file_size ); file_pos = 0;
    for (i = 0; i < 5000; i++) file_data[i] = (uint8_t)(i * 31 + 7);
    ctx = new_context(); cp.context = ctx; cp.url = "x.wmv";
    CHECK( call( unix_demuxer_create, &cp ) == STATUS_UNSUCCESSFUL && !cp.demuxer.handle, "a file that is not ASF is refused, nothing is left behind" );
    free( ctx );
    printf( "PASS winedmo garbage: refused cleanly\n" );
    return 1;
}

int main( int argc, char **argv )
{
    int i;
    (void)argc;
    /* argv[1]: Wine's qasf test.wmv (wmv1 64x48 + wmav1, 2 streams); argv[2]: the generated asf file; argv[3]: the generated asf's size line */
    if (!test_file( argv[1], 2, 64, 48, 0x31564d57 /* WMV1 */, 0x160, 0 /* Wine's file is cut short */ )) return 1;
    if (argc > 2 && !test_file( argv[2], 2, 160, 96, 0x32564d57 /* WMV2 */, 0x161, 1 )) return 1;
    if (!test_garbage()) return 1;
    for (i = 0; i < 3; i++) if (!test_garbage()) return 1;
    printf( "%s: %d failure(s)\n", failures ? "FAIL" : "PASS", failures );
    return failures ? 1 : 0;
}
"""


def compile_run(name, harness, includes, defines, extra_args, runner_args, env_extra=None):
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / (name + ".c")
        h.write_text(harness)
        exe = Path(tmp) / name
        cc = os.environ.get("CC", "cc")
        cmd = [cc, "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
               "-fno-strict-aliasing", "-Wno-int-conversion", "-Wno-implicit-function-declaration",
               "-include", str(wine_build / "include/config.h")]
        cmd += ["-I" + str(ntdll_unix)] + ["-I" + str(i) for i in includes]
        cmd += ["-I" + str(wine_build / "include"), "-I" + str(wine / "include"), "-I" + str(inc),
                "-D__WINESRC__", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", "-DWINE_UNIX_LIB"] + defines
        cmd += [str(h), "-o", str(exe)] + libs
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            print(r.stdout[-6000:], r.stderr[-6000:])
            print("FAIL %s: harness did not compile" % name)
            return False
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:symbolize=0", **(env_extra or {}))
        r = subprocess.run([str(exe)] + runner_args, env=env, capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            tail = [l for l in r.stderr.splitlines() if not l.startswith(("[wmv]", "[winedmo]", "[wma]"))][-30:]
            print("\n".join(tail))
            print("log lines (first 30):")
            print("\n".join([l for l in r.stderr.splitlines() if l.startswith(("[wmv]", "[winedmo]"))][:30]))
            print("FAIL %s: exit %d" % (name, r.returncode))
            return False
        logs = [l for l in r.stderr.splitlines() if l.startswith(("[wmv]", "[winedmo]"))]
        return logs


# The generated ASF is made inside the first harness, which writes it to the path in MADEIRA_ASF_OUT for the second.

with tempfile.TemporaryDirectory() as tmp:
    asf_path = str(Path(tmp) / "generated.asf")
    logs = compile_run("wmv_host", HARNESS_WMV, [wine / "dlls/winegstreamer"], [], [], [str(m) for m in media],
                       {"MADEIRA_ASF_OUT": asf_path})
    check("the WMV transform host test (conversions, transform, refusals, behaviours)", logs is not False)
    if logs:
        for needle in ("[wmv] decoder created fmt=wmv1", "[wmv] decoder opened: wmv1", "[wmv] first sample accepted",
                       "[wmv] first picture:", "[wmv] stats (transform", "[wmv] decoder created fmt=wmv2"):
            check("the log says how far it got: '%s'" % needle, any(needle in l for l in logs))
    dmo_logs = compile_run("dmo_host", HARNESS_DMO, [wine / "dlls/winedmo"],
                           ["-D__wine_unix_call_funcs=winedmo_unix_call_funcs", "-D__wine_unix_call_wow64_funcs=winedmo_unix_call_wow64_funcs"],
                           [], [str(media[0]), asf_path])
    check("the winedmo demuxer host test (types, packets, BUFFER_TOO_SMALL, seek, EOF)", dmo_logs is not False)
    if dmo_logs:
        for needle in ("[winedmo] process attached", "[winedmo] demuxer created: format=asf", "stream 0: video wmv1",
                       "stream 1: audio wmav", "[winedmo] first packet:", "[winedmo] demuxer destroyed: packets read per stream:"):
            check("the log says how far it got: '%s'" % needle, any(needle in l for l in dmo_logs))

print("PASS: ASF and WMV" if ok else "FAILED: ASF and WMV")
sys.exit(0 if ok else 1)
