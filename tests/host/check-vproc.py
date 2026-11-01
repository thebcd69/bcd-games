#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's raw video converter wg_transform, behind env.MADEIRA_WG_VPROC = 1 (round 626, agent V; Unity cutscenes: Hollow Knight:
Silksong DF0537, Hollow Knight H3SBFV, Power Drill Massacre 641PEM).

Compiles the production build/ntdll-unix/winegstreamer_unixlib_ios.c (which #includes winegstreamer_vproc_ios.c) under ASan/UBSan
against Wine's generated headers and the host FFmpeg check-wmv-asf.py builds (the same cache), and drives the unix entries:
  - key off: a raw-video to raw-video create is refused (STATUS_NOT_SUPPORTED, handle 0), as before;
  - key on: NV12 -> RGB32 white/black halves, ARGB32 alpha 0xff, ABGR32 = RGB32 with red and blue swapped on a red picture,
    NV12 -> I420 copy, a bottom-up output flips the rows, a size change is refused, a second push before a read is
    MF_E_NOTACCEPTING, a read with nothing pushed is MF_E_TRANSFORM_NEED_MORE_INPUT, a buffer too small is MF_E_BUFFERTOOSMALL and
    keeps the picture, the time stamp and flags are copied, get_status follows the pending picture, destroy logs its stats;
  - the wiring of both tables (source check).
SKIP when no configured Wine tree is found (set MADEIRA_WINE_SRC / MADEIRA_WINE_BUILD as for check-wmv-asf.py).
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


ntdll_unix = root / "build/ntdll-unix"
lib_c = (ntdll_unix / "winegstreamer_unixlib_ios.c").read_text()
vp_c = (ntdll_unix / "winegstreamer_vproc_ios.c").read_text()
check("the converter is included right after the WMV transform", '#include "winegstreamer_wmv_ios.c"' in lib_c
      and lib_c.index('#include "winegstreamer_wmv_ios.c"') < lib_c.index('#include "winegstreamer_vproc_ios.c"'))
check("the 64-bit create asks vproc_wanted() before the WMV transform",
      "if (vproc_wanted( params )) return vproc_transform_create( params );" in lib_c)
check("the wow64 create does too", "status = vproc_wanted( &params ) ? vproc_transform_create( &params ) : transform_create( &params );" in lib_c)
for entry in ("vproc_destroy", "vproc_get_output_type", "vproc_set_output_type", "vproc_push_data", "vproc_read_data",
              "vproc_get_status", "vproc_drain", "vproc_flush"):
    check("the shared entries reach %s for a converter handle" % entry, lib_c.count(entry + "(") >= 1)
check("the wow64 push, read, get and set thunks tell a converter handle apart",
      lib_c.count("vproc_is_handle( params32->transform )") == 4)
check("the key is exactly '1' (MADEIRA_WG_VPROC), read once", 'getenv( "MADEIRA_WG_VPROC" )' in vp_c and "v[0] == '1' && !v[1]" in vp_c)

wine = Path(os.environ.get("MADEIRA_WINE_SRC") or (root / "wine"))
wine_build = Path(os.environ.get("MADEIRA_WINE_BUILD") or (wine / "build-macos"))
if not (wine_build / "include/config.h").exists():
    print("SKIP the harness: %s is missing (point MADEIRA_WINE_BUILD / MADEIRA_WINE_SRC at a configured Wine tree)"
          % (wine_build / "include/config.h"))
    print("PASS: raw video converter (source checks)" if ok else "FAILED: raw video converter")
    sys.exit(0 if ok else 1)

# The host FFmpeg of check-wmv-asf.py (same flags, same cache key).
FLAGS = [
    "--disable-everything", "--disable-autodetect", "--disable-gpl", "--disable-nonfree", "--disable-version3",
    "--enable-decoder=wmav1,wmav2,wmapro,wmalossless,xma1,xma2",
    "--enable-decoder=mp1,mp2,mp3", "--enable-decoder=pcm_u8,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le",
    "--enable-decoder=wmv1,wmv2,wmv3,vc1",
    "--enable-demuxer=mp3,wav,mov,asf", "--enable-parser=mpegaudio",
    "--enable-encoder=wmv1,wmv2,wmav2", "--enable-muxer=asf",
    "--disable-programs", "--disable-doc", "--disable-network", "--disable-avdevice", "--disable-swscale",
    "--disable-avfilter", "--disable-postproc", "--enable-avformat", "--enable-avcodec", "--enable-swresample",
    "--disable-asm", "--disable-shared", "--enable-static", "--enable-pic", "--disable-debug",
]
prefix = os.environ.get("MADEIRA_HOST_FFMPEG") or str(
    Path.home() / ".cache/madeira-host-ffmpeg" / ("7.1.1-" + hashlib.sha256(" ".join(FLAGS).encode()).hexdigest()[:12]))
if not (Path(prefix) / "lib/libavcodec.a").exists():
    print("SKIP the harness: no host FFmpeg at %s (run tests/host/check-wmv-asf.py once, it builds it)" % prefix)
    print("PASS: raw video converter (source checks)" if ok else "FAILED: raw video converter")
    sys.exit(0 if ok else 1)
inc, lib = Path(prefix) / "include", Path(prefix) / "lib"
libs = [str(lib / "libavformat.a"), str(lib / "libavcodec.a"), str(lib / "libswresample.a"), str(lib / "libavutil.a"), "-lm", "-lpthread"]

HARNESS = r"""
#include "winegstreamer_unixlib_ios.c"

const struct mav_video_backend mav_apple_video_backend;
const struct mav_audio_backend mav_apple_audio_backend;

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf( "FAIL " ); printf( __VA_ARGS__ ); printf( "\n" ); failures++; } else { printf( "ok   " ); printf( __VA_ARGS__ ); printf( "\n" ); } } while (0)

static GUID st( UINT32 d1 ) { GUID g = {0, 0, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}}; g.Data1 = d1; return g; }
#define FCC( a, b, c, d ) ((UINT32)(a) | ((UINT32)(b) << 8) | ((UINT32)(c) << 16) | ((UINT32)(d) << 24))

static MFVIDEOFORMAT *fmt( UINT32 d1, int w, int h, int bottom_up )
{
    MFVIDEOFORMAT *f = calloc( 1, sizeof(*f) );
    f->dwSize = sizeof(*f); f->guidFormat = st( d1 ); f->videoInfo.dwWidth = w; f->videoInfo.dwHeight = h;
    if (bottom_up) f->videoInfo.VideoFlags |= MFVideoFlag_BottomUpLinearRep;
    return f;
}

static NTSTATUS create( UINT32 in, UINT32 out, int w, int h, int ow, int oh, int bottom_up, wg_transform_t *t )
{
    struct wg_transform_create_params p;
    NTSTATUS s;
    memset( &p, 0, sizeof(p) );
    p.input_type.major = madeira_MFMediaType_Video; p.input_type.u.video = fmt( in, w, h, 0 ); p.input_type.format_size = sizeof(MFVIDEOFORMAT);
    p.output_type.major = madeira_MFMediaType_Video; p.output_type.u.video = fmt( out, ow, oh, bottom_up ); p.output_type.format_size = sizeof(MFVIDEOFORMAT);
    s = wma_transform_create( &p );
    *t = p.transform;
    return s;
}

static HRESULT push( wg_transform_t t, BYTE *d, UINT32 n, INT64 pts )
{
    struct wg_sample s; struct wg_transform_push_data_params p;
    memset( &s, 0, sizeof(s) ); memset( &p, 0, sizeof(p) );
    s.data = (UINT_PTR)d; s.size = n; s.max_size = n; s.pts = pts; s.duration = 333333;
    s.flags = WG_SAMPLE_FLAG_HAS_PTS | WG_SAMPLE_FLAG_HAS_DURATION | WG_SAMPLE_FLAG_SYNC_POINT;
    p.transform = t; p.sample = &s;
    wma_transform_push_data( &p );
    return p.result;
}

static HRESULT readp( wg_transform_t t, BYTE *d, UINT32 max, struct wg_sample *s )
{
    struct wg_transform_read_data_params p;
    memset( s, 0, sizeof(*s) ); memset( &p, 0, sizeof(p) );
    s->data = (UINT_PTR)d; s->max_size = max;
    p.transform = t; p.sample = s;
    wma_transform_read_data( &p );
    return p.result;
}

static UINT32 accepts( wg_transform_t t )
{
    struct wg_transform_get_status_params p;
    memset( &p, 0, sizeof(p) ); p.transform = t;
    wma_transform_get_status( &p );
    return p.accepts_input;
}

int main( int argc, char **argv )
{
    enum { W = 64, H = 48 };
    static BYTE nv12[W * H * 3 / 2], out[W * H * 4];
    wg_transform_t t = 0;
    struct wg_sample s;
    NTSTATUS st_;
    int x, y, okrow;

    if (argc > 1 && !strcmp( argv[1], "off" ))
    {
        st_ = create( FCC('N','V','1','2'), 22, W, H, W, H, 0, &t );
        CHECK( st_ == STATUS_NOT_SUPPORTED && !t, "key off: NV12 -> RGB32 is refused as before (status %#x)", (unsigned)st_ );
        return failures ? 1 : 0;
    }

    /* Y: left half 235 (white), right half 16 (black); chroma 128 (no colour). */
    for (y = 0; y < H; y++) for (x = 0; x < W; x++) nv12[y * W + x] = x < W / 2 ? 235 : 16;
    memset( nv12 + W * H, 128, W * H / 2 );

    st_ = create( FCC('N','V','1','2'), 22, W, H, W, H, 0, &t );
    CHECK( st_ == STATUS_SUCCESS && vproc_is_handle( t ), "key on: NV12 -> RGB32 %dx%d is created as a converter", W, H );
    CHECK( readp( t, out, sizeof(out), &s ) == MF_E_TRANSFORM_NEED_MORE_INPUT, "a read with nothing pushed wants more input" );
    CHECK( accepts( t ), "it accepts input when nothing waits" );
    CHECK( push( t, nv12, sizeof(nv12), 1234 ) == S_OK, "the NV12 picture is taken" );
    CHECK( !accepts( t ), "get_status says no while a picture waits" );
    CHECK( push( t, nv12, sizeof(nv12), 5678 ) == MF_E_NOTACCEPTING, "a second push before the read is MF_E_NOTACCEPTING" );
    CHECK( readp( t, out, 100, &s ) == MF_E_BUFFERTOOSMALL && s.size == W * H * 4, "a buffer too small: MF_E_BUFFERTOOSMALL with the size it needs" );
    CHECK( readp( t, out, sizeof(out), &s ) == S_OK && s.size == W * H * 4, "then the picture is read whole (%u bytes)", (unsigned)s.size );
    CHECK( s.pts == 1234 && s.duration == 333333 && (s.flags & WG_SAMPLE_FLAG_HAS_PTS) && (s.flags & WG_SAMPLE_FLAG_SYNC_POINT),
           "its time, duration and flags are the pushed sample's" );
    CHECK( out[0] == 255 && out[1] == 255 && out[2] == 255 && out[3] == 255, "the left half is white (%u %u %u %u)", out[0], out[1], out[2], out[3] );
    CHECK( out[4 * (W - 1)] == 0 && out[4 * (W - 1) + 1] == 0 && out[4 * (W - 1) + 2] == 0 && out[4 * (W - 1) + 3] == 255, "the right half is black, alpha 0xff" );
    wma_transform_destroy( &t );

    /* A red picture (BT.601 limited: Y 81, Cb 90, Cr 240): RGB32 is B,G,R,A and ABGR32 R,G,B,A. */
    memset( nv12, 81, W * H );
    for (x = 0; x < W * H / 2; x += 2) { nv12[W * H + x] = 90; nv12[W * H + x + 1] = 240; }
    st_ = create( FCC('N','V','1','2'), 21, W, H, W, H, 0, &t );
    CHECK( st_ == STATUS_SUCCESS, "NV12 -> ARGB32 is created" );
    push( t, nv12, sizeof(nv12), 0 ); readp( t, out, sizeof(out), &s );
    CHECK( out[0] < 16 && out[1] < 16 && out[2] > 235 && out[3] == 255, "ARGB32 of red: B %u G %u R %u A %u", out[0], out[1], out[2], out[3] );
    wma_transform_destroy( &t );
    st_ = create( FCC('N','V','1','2'), 32, W, H, W, H, 0, &t );
    CHECK( st_ == STATUS_SUCCESS, "NV12 -> ABGR32 is created" );
    push( t, nv12, sizeof(nv12), 0 ); readp( t, out, sizeof(out), &s );
    CHECK( out[0] > 235 && out[1] < 16 && out[2] < 16 && out[3] == 255, "ABGR32 of red has red first: %u %u %u %u", out[0], out[1], out[2], out[3] );
    wma_transform_destroy( &t );

    /* Rows: a top row of 235 and the rest 16; a bottom-up RGB32 output puts the white row last. */
    memset( nv12, 16, W * H ); memset( nv12, 235, W ); memset( nv12 + W * H, 128, W * H / 2 );
    st_ = create( FCC('N','V','1','2'), 22, W, H, W, H, 1, &t );
    push( t, nv12, sizeof(nv12), 0 ); readp( t, out, sizeof(out), &s );
    CHECK( st_ == STATUS_SUCCESS && out[0] == 0 && out[(H - 1) * W * 4] == 255, "a bottom-up output flips the rows" );
    wma_transform_destroy( &t );

    /* NV12 -> I420: the planes come out separate, the same values. */
    for (x = 0; x < W * H / 2; x += 2) { nv12[W * H + x] = 90; nv12[W * H + x + 1] = 240; }
    st_ = create( FCC('N','V','1','2'), FCC('I','4','2','0'), W, H, W, H, 0, &t );
    push( t, nv12, sizeof(nv12), 0 ); readp( t, out, sizeof(out), &s );
    okrow = st_ == STATUS_SUCCESS && s.size == W * H * 3 / 2 && out[0] == 235 && out[W * H] == 90 && out[W * H + W * H / 4] == 240;
    CHECK( okrow, "NV12 -> I420 keeps the values in separate planes" );
    wma_transform_destroy( &t );

    st_ = create( FCC('N','V','1','2'), 22, W, H, W / 2, H / 2, 0, &t );
    CHECK( st_ == STATUS_NOT_SUPPORTED && !t, "a size change is refused (no scaler)" );
    st_ = create( FCC('W','M','V','3'), 22, W, H, W, H, 0, &t );
    CHECK( !vproc_is_handle( t ), "a compressed input is not the converter's" );
    if (t) wma_transform_destroy( &t );
    return failures ? 1 : 0;
}
"""

with tempfile.TemporaryDirectory() as tmp:
    h = Path(tmp) / "vproc_host.c"
    h.write_text(HARNESS)
    exe = Path(tmp) / "vproc_host"
    cmd = [os.environ.get("CC", "cc"), "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
           "-fno-strict-aliasing", "-Wno-int-conversion", "-Wno-implicit-function-declaration",
           "-include", str(wine_build / "include/config.h"), "-I" + str(ntdll_unix), "-I" + str(wine / "dlls/winegstreamer"),
           "-I" + str(wine_build / "include"), "-I" + str(wine / "include"), "-I" + str(inc),
           "-D__WINESRC__", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", "-DWINE_UNIX_LIB", str(h), "-o", str(exe)] + libs
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("the harness compiles with the production file", r.returncode == 0)
    if r.returncode:
        print(r.stderr[-4000:])
    else:
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:symbolize=0")
        env.pop("MADEIRA_WG_VPROC", None)
        r0 = subprocess.run([str(exe), "off"], env=env, capture_output=True, text=True)
        sys.stdout.write(r0.stdout)
        check("key off: the old refusal", r0.returncode == 0)
        r1 = subprocess.run([str(exe)], env=dict(env, MADEIRA_WG_VPROC="1"), capture_output=True, text=True)
        sys.stdout.write(r1.stdout)
        check("key on: the converter's cases", r1.returncode == 0)
        logs = [l for l in r1.stderr.splitlines() if l.startswith("[vproc]")]
        for needle in ("[vproc] created NV12 64x48 -> RGB32 64x48", "[vproc] first picture: NV12 64x48", "[vproc] stats:",
                       "[vproc] refused: in=NV12 64x48 out=RGB32 32x24 (a size change"):
            check("the log says '%s'" % needle, any(needle in l for l in logs))
        if r1.returncode:
            print("\n".join(r1.stderr.splitlines()[-20:]))

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-vproc.py" in wf)
print("PASS: raw video converter" if ok else "FAILED: raw video converter")
sys.exit(0 if ok else 1)
