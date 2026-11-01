/*
 * BCD (madeira-bcd, round 626): a raw video converter as a wg_transform, behind env.MADEIRA_WG_VPROC = 1.
 *
 * SPDX-License-Identifier: LGPL-2.1-or-later
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * #included by winegstreamer_unixlib_ios.c directly after winegstreamer_wmv_ios.c, whose format, layout and conversion helpers it
 * uses (wmv_fmt_from_guid, wmv_layout_for, wmv_convert, wmv_video_format). No libav header.
 *
 * Why (round 626, agent V; Hollow Knight: Silksong DF0537, Hollow Knight H3SBFV, Power Drill Massacre 641PEM and 34 more logs of
 * Unity games): Unity's video player opens a clip through a Media Foundation source reader with video processing on and asks for an
 * RGB picture. The media source hands out NV12 (BCD's parser decodes H.264 on VideoToolbox), so the reader inserts Wine's Video
 * Processor MFT (msvproc), whose wg_transform for raw video to raw video BCD refused: "WindowsVideoMedia error 0xc00d5212"
 * (MF_E_TOPO_CODEC_NOT_FOUND) and a black screen at every cutscene. This transform does that one job: a picture of NV12, YV12 or
 * I420 to NV12, YV12, I420, YUY2, UYVY, YVYU, RGB32, ARGB32, ABGR32, RGB24, RGB565 or RGB555 of the same size (no scaling: a size
 * change is refused). BT.709 at 720 lines and more, else BT.601, limited range, as the parser itself assumes for untagged video.
 *
 * Keyed: without MADEIRA_WG_VPROC=1 vproc_wanted() is false and every create reaches the code it reached before (the WMV transform,
 * which refuses raw video, or the WMA transform); the dispatch branches below only ever match a handle this file made ('VPRC').
 * Log lines: "[vproc] created|refused|first picture|stats" (capped).
 */

#define MADEIRA_VPROC_MAGIC 0x56505243  /* 'VPRC' */
#define VPROC_MAX_LOGS 60

static unsigned int vproc_log_count;

static void vproc_log( const char *fmt, ... ) __attribute__((format(printf, 1, 2)));
static void vproc_log( const char *fmt, ... )
{
    char buf[640];
    va_list args;
    int n;

    if (__atomic_fetch_add( &vproc_log_count, 1, __ATOMIC_RELAXED ) >= VPROC_MAX_LOGS) return;
    va_start( args, fmt );
    n = vsnprintf( buf, sizeof(buf), fmt, args );
    va_end( args );
    if (n <= 0) return;
    dprintf( 2, "[vproc] %s%s", buf, buf[strlen(buf) - 1] == '\n' ? "" : "\n" );
}

struct vproc_transform
{
    UINT32 magic;               /* first member, as wmv_transform's and wma_transform's: the entries tell them apart */
    pthread_mutex_t lock;

    enum wmv_fmt in_fmt, out_fmt;
    BOOL out_swap_rb;           /* ABGR32: RGB32 with red and blue swapped */
    UINT32 width, height;
    BOOL in_bottom_up, out_bottom_up;
    int bt709;
    BYTE *out_format;           /* the output MFVIDEOFORMAT as set, handed back by get_output_type */
    UINT32 out_format_size;

    BYTE *picture;              /* one input picture, copied at push */
    size_t picture_cap, picture_size;
    UINT32 in_pitch;            /* the first plane's row length of the stored picture */
    BYTE *scratch;              /* NV12's chroma split into U and V for wmv_convert */
    size_t scratch_cap;
    BOOL pending;
    UINT32 pending_flags;
    INT64 pending_pts;
    UINT64 pending_duration;
    BOOL discontinuity;

    BOOL first_logged;
    UINT64 pushed, frames, refused_size;
    double convert_ms, convert_ms_max;
};

static BOOL vproc_key_on( void )
{
    static int on = -1;
    if (on < 0)
    {
        const char *v = getenv( "MADEIRA_WG_VPROC" );
        on = v && v[0] == '1' && !v[1];
    }
    return on;
}

static struct vproc_transform *vproc_get( wg_transform_t handle )
{
    struct vproc_transform *transform = (struct vproc_transform *)(UINT_PTR)handle;

    if (!transform || transform->magic != MADEIRA_VPROC_MAGIC) return NULL;
    return transform;
}

static BOOL vproc_is_handle( wg_transform_t handle )
{
    return vproc_get( handle ) != NULL;
}

/* A raw video format this transform reads or writes; *swap_rb is set for ABGR32 (D3DFMT_A8B8G8R8, 32). */
static enum wmv_fmt vproc_fmt( const MFVIDEOFORMAT *format, BOOL *swap_rb )
{
    *swap_rb = FALSE;
    if (wmv_guid_is_mf( &format->guidFormat ) && format->guidFormat.Data1 == 32)
    {
        *swap_rb = TRUE;
        return WMV_FMT_RGB32;
    }
    return wmv_fmt_from_guid( &format->guidFormat );
}

static BOOL vproc_is_input_fmt( enum wmv_fmt fmt )
{
    return fmt == WMV_FMT_NV12 || fmt == WMV_FMT_YV12 || fmt == WMV_FMT_I420;
}

/* The key is on, both types are video, the input is planar 4:2:0 and the output a raw format: raw video to raw video, which no
 * other transform here makes. */
static BOOL vproc_wanted( const struct wg_transform_create_params *params )
{
    const MFVIDEOFORMAT *in, *out;
    BOOL swap;

    if (!vproc_key_on()) return FALSE;
    if (!(in = wmv_video_format( &params->input_type )) || !(out = wmv_video_format( &params->output_type ))) return FALSE;
    return vproc_is_input_fmt( vproc_fmt( in, &swap ) ) && vproc_fmt( out, &swap ) != WMV_FMT_NONE;
}

static const char *vproc_fmt_label( enum wmv_fmt fmt, BOOL swap_rb )
{
    return swap_rb ? "ABGR32" : wmv_fmt_name( fmt );
}

static BOOL vproc_set_output( struct vproc_transform *transform, const MFVIDEOFORMAT *format, const char **why )
{
    enum wmv_fmt fmt;
    BOOL swap;
    UINT32 w, h;
    BYTE *copy;

    if (!(fmt = vproc_fmt( format, &swap ))) { *why = "an output format it does not write"; return FALSE; }
    w = format->videoInfo.dwWidth ? format->videoInfo.dwWidth : transform->width;
    h = format->videoInfo.dwHeight ? format->videoInfo.dwHeight : transform->height;
    if (w != transform->width || h != transform->height) { *why = "a size change (no scaler here)"; return FALSE; }
    if (!(copy = malloc( format->dwSize ))) { *why = "out of memory"; return FALSE; }
    memcpy( copy, format, format->dwSize );
    free( transform->out_format );
    transform->out_format = copy;
    transform->out_format_size = format->dwSize;
    transform->out_fmt = fmt;
    transform->out_swap_rb = swap;
    transform->out_bottom_up = !!(format->videoInfo.VideoFlags & MFVideoFlag_BottomUpLinearRep);
    return TRUE;
}

static NTSTATUS vproc_transform_create( struct wg_transform_create_params *params )
{
    const MFVIDEOFORMAT *in = wmv_video_format( &params->input_type ), *out = wmv_video_format( &params->output_type );
    struct vproc_transform *transform;
    const char *why = NULL;
    BOOL swap;

    params->transform = 0;
    if (!in || !out) return STATUS_NOT_SUPPORTED;
    if (!(transform = calloc( 1, sizeof(*transform) ))) return STATUS_NO_MEMORY;
    transform->magic = MADEIRA_VPROC_MAGIC;
    pthread_mutex_init( &transform->lock, NULL );
    transform->in_fmt = vproc_fmt( in, &swap );
    transform->width = in->videoInfo.dwWidth;
    transform->height = in->videoInfo.dwHeight;
    transform->in_bottom_up = !!(in->videoInfo.VideoFlags & MFVideoFlag_BottomUpLinearRep);
    if (!vproc_is_input_fmt( transform->in_fmt ) || swap)
        why = "an input format it does not read";
    else if (!transform->width || !transform->height || transform->width > WMV_MAX_DIMENSION || transform->height > WMV_MAX_DIMENSION)
        why = "no picture size";
    else
        vproc_set_output( transform, out, &why );
    if (why)
    {
        vproc_log( "refused: in=%s %ux%u out=%s %ux%u (%s)\n", wmv_fmt_name( vproc_fmt( in, &swap ) ), (unsigned)in->videoInfo.dwWidth,
                   (unsigned)in->videoInfo.dwHeight, vproc_fmt_label( vproc_fmt( out, &swap ), swap ),
                   (unsigned)out->videoInfo.dwWidth, (unsigned)out->videoInfo.dwHeight, why );
        free( transform->out_format );
        pthread_mutex_destroy( &transform->lock );
        free( transform );
        return STATUS_NOT_SUPPORTED;
    }
    /* What the parser itself assumes for untagged video (wg_parser_av_ios.c): BT.709 from 720 lines up, limited range. */
    transform->bt709 = in->videoInfo.TransferMatrix == MFVideoTransferMatrix_BT709
                       || (in->videoInfo.TransferMatrix != MFVideoTransferMatrix_BT601 && transform->height >= 720);
    vproc_log( "created %s %ux%u%s -> %s %ux%u%s matrix=%s range=limited (transform %p)\n", wmv_fmt_name( transform->in_fmt ),
               (unsigned)transform->width, (unsigned)transform->height, transform->in_bottom_up ? " bottom-up" : "",
               vproc_fmt_label( transform->out_fmt, transform->out_swap_rb ), (unsigned)transform->width,
               (unsigned)transform->height, transform->out_bottom_up ? " bottom-up" : "", transform->bt709 ? "709" : "601", transform );
    params->transform = (wg_transform_t)(UINT_PTR)transform;
    return STATUS_SUCCESS;
}

static NTSTATUS vproc_destroy( wg_transform_t handle )
{
    struct vproc_transform *transform = vproc_get( handle );

    if (!transform) return STATUS_INVALID_HANDLE;
    vproc_log( "stats: %llu pushed, %llu pictures, convert avg %.2f ms max %.2f ms%s (transform %p)\n",
               (unsigned long long)transform->pushed, (unsigned long long)transform->frames,
               transform->frames ? transform->convert_ms / transform->frames : 0.0, transform->convert_ms_max,
               transform->refused_size ? ", some input of the wrong size" : "", transform );
    transform->magic = 0;
    free( transform->picture );
    free( transform->scratch );
    free( transform->out_format );
    pthread_mutex_destroy( &transform->lock );
    free( transform );
    return STATUS_SUCCESS;
}

/* The input picture's first-plane pitch from its size: Media Foundation's default stride, GStreamer's 4-byte aligned rows (what BCD's
 * media source hands out for NV12), or the 2D buffer's stride when the sample names one. 0: none fits. */
static UINT32 vproc_input_pitch( const struct vproc_transform *transform, UINT32 size, UINT32 stride )
{
    struct wmv_layout l;
    UINT32 w = transform->width, cand[3], i;

    cand[0] = stride;
    cand[1] = wmv_min_pitch( transform->in_fmt, w );
    cand[2] = (w + 3) & ~3u;
    for (i = 0; i < 3; i++)
    {
        if (!cand[i] || cand[i] < cand[1]) continue;
        if (wmv_layout_for( transform->in_fmt, w, transform->height, cand[i], &l ) && l.size == size) return cand[i];
    }
    /* A buffer larger than a picture with the default stride: read the picture from its start. */
    if (wmv_layout_for( transform->in_fmt, w, transform->height, cand[1], &l ) && size >= l.size) return cand[1];
    return 0;
}

static NTSTATUS vproc_push_data( wg_transform_t handle, struct wg_sample *sample, const void *data, HRESULT *result )
{
    struct vproc_transform *transform = vproc_get( handle );
    UINT32 pitch;

    if (!transform || !sample) return STATUS_INVALID_HANDLE;
    if (sample->size && !data) return STATUS_INVALID_PARAMETER;
    pthread_mutex_lock( &transform->lock );
    if (transform->pending)
    {
        /* One picture at a time: the reader reads it, then pushes again. */
        pthread_mutex_unlock( &transform->lock );
        *result = MF_E_NOTACCEPTING;
        return STATUS_SUCCESS;
    }
    if (!(pitch = vproc_input_pitch( transform, sample->size, sample->stride )))
    {
        if (!transform->refused_size++)
            vproc_log( "an input of %u bytes is not a %s %ux%u picture; dropped (transform %p)\n", (unsigned)sample->size,
                       wmv_fmt_name( transform->in_fmt ), (unsigned)transform->width, (unsigned)transform->height, transform );
        pthread_mutex_unlock( &transform->lock );
        *result = S_OK;
        return STATUS_SUCCESS;
    }
    if (transform->picture_cap < sample->size)
    {
        BYTE *p = realloc( transform->picture, sample->size );
        if (!p)
        {
            pthread_mutex_unlock( &transform->lock );
            *result = E_OUTOFMEMORY;
            return STATUS_SUCCESS;
        }
        transform->picture = p;
        transform->picture_cap = sample->size;
    }
    memcpy( transform->picture, data, sample->size );
    transform->picture_size = sample->size;
    transform->in_pitch = pitch;
    transform->pending = TRUE;
    transform->pending_flags = sample->flags;
    transform->pending_pts = sample->pts;
    transform->pending_duration = sample->duration;
    transform->pushed++;
    pthread_mutex_unlock( &transform->lock );
    *result = S_OK;
    return STATUS_SUCCESS;
}

static NTSTATUS vproc_read_data( wg_transform_t handle, struct wg_sample *sample, void *data, HRESULT *result )
{
    struct vproc_transform *transform = vproc_get( handle );
    struct wmv_layout in_l, out_l;
    struct wmv_picture pic;
    UINT32 pitch = 0, w, h, cw, ch, x, y;
    double t0;

    if (!transform || !sample) return STATUS_INVALID_HANDLE;
    if (sample->max_size && !data) return STATUS_INVALID_PARAMETER;
    pthread_mutex_lock( &transform->lock );
    sample->size = 0;
    if (!transform->pending)
    {
        pthread_mutex_unlock( &transform->lock );
        *result = MF_E_TRANSFORM_NEED_MORE_INPUT;
        return STATUS_SUCCESS;
    }
    w = transform->width;
    h = transform->height;
    if (sample->stride && sample->stride >= wmv_min_pitch( transform->out_fmt, w )) pitch = sample->stride;
    wmv_layout_for( transform->out_fmt, w, h, pitch, &out_l );
    if (sample->max_size < out_l.size)
    {
        /* Keep the picture: the caller comes back with a larger buffer. */
        sample->size = out_l.size;
        pthread_mutex_unlock( &transform->lock );
        *result = MF_E_BUFFERTOOSMALL;
        return STATUS_SUCCESS;
    }

    wmv_layout_for( transform->in_fmt, w, h, transform->in_pitch, &in_l );
    memset( &pic, 0, sizeof(pic) );
    pic.width = w;
    pic.height = h;
    pic.bt709 = transform->bt709;
    pic.full_range = 0;
    pic.p[0] = transform->picture;
    pic.stride[0] = in_l.stride[0];
    cw = (w + 1) / 2;
    ch = (h + 1) / 2;
    if (transform->in_fmt == WMV_FMT_NV12)
    {
        size_t need = (size_t)cw * ch * 2;
        if (transform->scratch_cap < need)
        {
            BYTE *p = realloc( transform->scratch, need );
            if (!p)
            {
                pthread_mutex_unlock( &transform->lock );
                *result = E_OUTOFMEMORY;
                return STATUS_SUCCESS;
            }
            transform->scratch = p;
            transform->scratch_cap = need;
        }
        for (y = 0; y < ch; y++)
        {
            const BYTE *uv = transform->picture + in_l.offset[1] + (size_t)y * in_l.stride[1];
            BYTE *u = transform->scratch + (size_t)y * cw, *v = transform->scratch + (size_t)cw * ch + (size_t)y * cw;
            for (x = 0; x < cw; x++)
            {
                u[x] = uv[2 * x];
                v[x] = uv[2 * x + 1];
            }
        }
        pic.p[1] = transform->scratch;
        pic.p[2] = transform->scratch + (size_t)cw * ch;
        pic.stride[1] = pic.stride[2] = cw;
    }
    else
    {
        /* I420: U then V; YV12: V then U. */
        int up = transform->in_fmt == WMV_FMT_I420 ? 1 : 2, vp = 3 - up;
        pic.p[1] = transform->picture + in_l.offset[up];
        pic.p[2] = transform->picture + in_l.offset[vp];
        pic.stride[1] = in_l.stride[up];
        pic.stride[2] = in_l.stride[vp];
    }

    t0 = wmv_now_ms();
    wmv_convert( &pic, data, transform->out_fmt, &out_l, w, h, transform->in_bottom_up != transform->out_bottom_up );
    if (transform->out_swap_rb)
        for (y = 0; y < out_l.rows[0]; y++)
        {
            BYTE *row = (BYTE *)data + (size_t)y * out_l.stride[0];
            for (x = 0; x < w; x++)
            {
                BYTE t = row[4 * x];
                row[4 * x] = row[4 * x + 2];
                row[4 * x + 2] = t;
            }
        }
    t0 = wmv_now_ms() - t0;
    transform->convert_ms += t0;
    if (t0 > transform->convert_ms_max) transform->convert_ms_max = t0;
    if (!transform->first_logged)
    {
        transform->first_logged = TRUE;
        vproc_log( "first picture: %s %ux%u (pitch %u) -> %s (pitch %u, %zu bytes)%s in %.2f ms (transform %p)\n",
                   wmv_fmt_name( transform->in_fmt ), (unsigned)w, (unsigned)h, (unsigned)transform->in_pitch,
                   vproc_fmt_label( transform->out_fmt, transform->out_swap_rb ), (unsigned)out_l.stride[0], out_l.size,
                   transform->in_bottom_up != transform->out_bottom_up ? " flipped" : "", t0, transform );
    }

    sample->size = out_l.size;
    sample->flags &= ~(WG_SAMPLE_FLAG_INCOMPLETE | WG_SAMPLE_FLAG_HAS_PTS | WG_SAMPLE_FLAG_HAS_DURATION |
                       WG_SAMPLE_FLAG_SYNC_POINT | WG_SAMPLE_FLAG_DISCONTINUITY);
    sample->flags |= transform->pending_flags & (WG_SAMPLE_FLAG_HAS_PTS | WG_SAMPLE_FLAG_HAS_DURATION | WG_SAMPLE_FLAG_SYNC_POINT |
                                                 WG_SAMPLE_FLAG_DISCONTINUITY);
    if (transform->discontinuity)
    {
        sample->flags |= WG_SAMPLE_FLAG_DISCONTINUITY;
        transform->discontinuity = FALSE;
    }
    sample->pts = transform->pending_pts;
    sample->duration = transform->pending_duration;
    transform->pending = FALSE;
    transform->frames++;
    pthread_mutex_unlock( &transform->lock );
    *result = S_OK;
    return STATUS_SUCCESS;
}

static NTSTATUS vproc_get_output_type( wg_transform_t handle, struct wg_media_type *type )
{
    struct vproc_transform *transform = vproc_get( handle );
    UINT32 size;

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    size = transform->out_format_size;
    type->major = madeira_MFMediaType_Video;
    /* Two-phase, as main.c's wg_transform_get_output_type() expects. */
    if (!type->u.format || type->format_size < size)
    {
        type->format_size = size;
        pthread_mutex_unlock( &transform->lock );
        return STATUS_BUFFER_TOO_SMALL;
    }
    memcpy( type->u.format, transform->out_format, size );
    type->format_size = size;
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}

static NTSTATUS vproc_set_output_type( wg_transform_t handle, const struct wg_media_type *type )
{
    struct vproc_transform *transform = vproc_get( handle );
    const MFVIDEOFORMAT *format = wmv_video_format( type );
    const char *why = "not a video type";
    BOOL ok = FALSE;

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    if (format) ok = vproc_set_output( transform, format, &why );
    if (!ok) vproc_log( "output type refused (%s) (transform %p)\n", why, transform );
    pthread_mutex_unlock( &transform->lock );
    return ok ? STATUS_SUCCESS : STATUS_NOT_SUPPORTED;
}

static NTSTATUS vproc_get_status( wg_transform_t handle, UINT32 *accepts_input )
{
    struct vproc_transform *transform = vproc_get( handle );

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    *accepts_input = !transform->pending;
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}

/* Nothing is held back: a pending picture stays readable after a drain. */
static NTSTATUS vproc_drain( wg_transform_t handle )
{
    return vproc_get( handle ) ? STATUS_SUCCESS : STATUS_INVALID_HANDLE;
}

static NTSTATUS vproc_flush( wg_transform_t handle )
{
    struct vproc_transform *transform = vproc_get( handle );

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    transform->pending = FALSE;
    transform->discontinuity = TRUE;
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}
