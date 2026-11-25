#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# Portions copyright 2026 wenze-allen: the change to dlls/winegstreamer/wm_reader.c, taken from https://github.com/willfaust/wine (pull request #78,
# commit b0d33cbd85) under LGPL-2.1-or-later and modified for BCD in November 2026 (written as a build-time script). See THIRD-PARTY-NOTICES.md.
# Copyright 2026 thebcd69 (BCD): the script and its check; BCD's section 7 terms (LICENSE-BCD.md) apply to those, not to the Wine lines it writes.
"""The Windows Media Format sync reader reads compressed samples per stream, and keeps its streams when a re-initialization fails
(Wine pull request #78 by wenze-allen).

IWMSyncReader::SetReadStreamSamples is per stream, but wm_reader.c's reinit_stream() used the flag of the LAST call for every stream. When
the parser then refused to connect (BCD's wg_parser refuses a compressed-output ASF, and a compressed-output MP3), reinit_stream() went to
its error path, whose free_streams() freed the stream array but kept stream_count, and SetReadStreamSamples() returned S_OK over a reader
without a parser. KiriKiri 2's movie plugin (krmovie.dll) asks for compressed samples: it then wrote through the NULL array and crashed
(the pull request's case: Sabbat of the Witch stayed on a white screen). BCD has the same code in its fork.

With this: reinit_stream() takes each stream's own flag (the parser is created for compressed output when any stream asks for it, and a
stream is enabled with its own format, the compressed one or the decoded one); a failed re-initialization keeps the streams (they belong
to the open file: the application has their selection, output format and allocators) and only forgets the handles into the destroyed
parser; a parser that exposes a different number of streams fails the call instead of hitting an assert; SetReadStreamSamples() goes back
to the previous mode and parser and returns the failure instead of S_OK. free_streams() resets stream_count with the array.
A call that succeeds does exactly what it did (same parser, same stream formats).

WHERE THIS REACHES: winegstreamer.dll of the 32-bit set (the i386 farm builds every module) and of the 64-bit one the app builds from this
tree (tools/build-wine-extra-dlls.sh builds winegstreamer for arm64ec). Whether the wg_parser accepts a compressed ASF is a separate
matter (it does not in BCD today); this only stops a refusal from crashing the reader.
Idempotent (marker "madeira-bcd: wmreader-compressed-streams-v1"); every anchor must be found exactly once or nothing is written.
Usage: patch-wine-wmreader-compressed-streams.py WINE-DIR | path/to/wine/dlls/winegstreamer/wm_reader.c
"""
from pathlib import Path
import sys

FILE = "dlls/winegstreamer/wm_reader.c"
MARKER = "madeira-bcd: wmreader-compressed-streams-v1"

E1_OLD = """    free(reader->streams);
    reader->streams = NULL;
}
"""
E1_NEW = """    free(reader->streams);
    reader->streams = NULL;
    reader->stream_count = 0;   /* """ + MARKER + """ */
}
"""

E2_OLD = """static HRESULT reinit_stream(struct wm_reader *reader, bool read_compressed)
{
    wg_parser_t wg_parser;
    HRESULT hr;
    WORD i;

    ReleaseSemaphore(reader->read_sem, 1, NULL);

    free_stream_buffers(reader);

    wg_parser_disconnect(reader->wg_parser);

    EnterCriticalSection(&reader->shutdown_cs);
    reader->read_thread_shutdown = true;
    LeaveCriticalSection(&reader->shutdown_cs);
    WaitForSingleObject(reader->read_thread, INFINITE);
    CloseHandle(reader->read_thread);
    CloseHandle(reader->read_sem);
    reader->read_thread = NULL;
    reader->read_sem = NULL;

    wg_parser_destroy(reader->wg_parser);
    reader->wg_parser = 0;
"""
E2_NEW = """/* """ + MARKER + """ (tools/patch-wine-wmreader-compressed-streams.py; Wine pull request #78
 * by wenze-allen). A stream reads compressed samples or decoded ones on its own (SetReadStreamSamples is per stream): the parser is
 * created for compressed output when any stream asks for it, and each stream is enabled with its own format, the compressed one or
 * the decoded one. A parser that cannot do both fails to connect, and the reader keeps its streams (see the end). */
static bool wm_reader_reads_compressed(struct wm_reader *reader)
{
    WORD i;

    for (i = 0; i < reader->stream_count; ++i)
        if (reader->streams[i].read_compressed)
            return true;
    return false;
}

static HRESULT reinit_stream(struct wm_reader *reader)
{
    bool read_compressed = wm_reader_reads_compressed(reader);
    wg_parser_t wg_parser;
    HRESULT hr;
    WORD i;

    /* After a failed reinit (below) there is no parser, read thread or semaphore to tear down. */
    if (reader->read_sem)
        ReleaseSemaphore(reader->read_sem, 1, NULL);

    free_stream_buffers(reader);

    if (reader->wg_parser)
        wg_parser_disconnect(reader->wg_parser);

    if (reader->read_thread)
    {
        EnterCriticalSection(&reader->shutdown_cs);
        reader->read_thread_shutdown = true;
        LeaveCriticalSection(&reader->shutdown_cs);
        WaitForSingleObject(reader->read_thread, INFINITE);
        CloseHandle(reader->read_thread);
    }
    if (reader->read_sem)
        CloseHandle(reader->read_sem);
    reader->read_thread = NULL;
    reader->read_sem = NULL;

    if (reader->wg_parser)
        wg_parser_destroy(reader->wg_parser);
    reader->wg_parser = 0;
"""

E3_OLD = """    assert(reader->stream_count == wg_parser_get_stream_count(reader->wg_parser));
"""
E3_NEW = """    /* A parser that exposes the file's streams differently with compressed output fails here,
     * and the caller goes back to the previous parser. */
    if (wg_parser_get_stream_count(reader->wg_parser) != reader->stream_count)
    {
        ERR("Parser has %u streams, expected %u.\\n",
                wg_parser_get_stream_count(reader->wg_parser), reader->stream_count);
        wg_parser_disconnect(reader->wg_parser);
        hr = E_FAIL;
        goto out_shutdown_thread;
    }
"""

E4_OLD = """            wg_parser_stream_enable(stream->wg_stream, read_compressed ? &format : &stream->format);
"""
E4_NEW = """            wg_parser_stream_enable(stream->wg_stream, stream->read_compressed ? &format : &stream->format);
"""

E5_OLD = """        ERR("Failed to wait for read semaphore.\\n");
        goto out_shutdown_thread;
"""
E5_NEW = """        ERR("Failed to wait for read semaphore.\\n");
        hr = E_FAIL;
        goto out_shutdown_thread;
"""

E6_OLD = """    free_stream_buffers(reader);
    free_streams(reader);
    wg_parser_destroy(reader->wg_parser);
    reader->wg_parser = 0;

    return hr;
}
"""
E6_NEW = """    free_stream_buffers(reader);
    /* The streams belong to the open file, not to this parser: the application has their
     * selection, output format and allocators. Freeing them here left stream_count streams in a
     * NULL array, and the next call into the reader wrote through it (a KiriKiri movie plugin
     * that asks for compressed samples). Only the handles into the destroyed parser go. */
    for (i = 0; i < reader->stream_count; ++i)
        reader->streams[i].wg_stream = 0;
    wg_parser_destroy(reader->wg_parser);
    reader->wg_parser = 0;

    return hr;
}
"""

E7_OLD = """    struct wm_stream *stream;

    TRACE("reader %p, stream_index %u, compressed %d.\\n", reader, stream_number, compressed);
"""
E7_NEW = """    struct wm_stream *stream;
    bool old_compressed;
    HRESULT hr;

    TRACE("reader %p, stream_index %u, compressed %d.\\n", reader, stream_number, compressed);
"""

E8_OLD = """    stream->read_compressed = compressed;
    reinit_stream(reader, compressed);

    LeaveCriticalSection(&reader->cs);
    return S_OK;
}
"""
E8_NEW = """    old_compressed = stream->read_compressed;
    stream->read_compressed = compressed;
    if (FAILED(hr = reinit_stream(reader)))
    {
        /* """ + MARKER + """: the parser could not serve the new mix of compressed and decoded streams.
         * Go back to the one that worked, so the reader stays usable, and say so instead of returning S_OK
         * over a reader without a parser. */
        ERR("Stream %u: compressed %d is not available, hr %#lx; keeping the previous mode.\\n",
                stream_number, compressed, hr);
        stream->read_compressed = old_compressed;
        if (FAILED(reinit_stream(reader)))
            ERR("Failed to restore the previous parser.\\n");
    }

    LeaveCriticalSection(&reader->cs);
    return hr;
}
"""

EDITS = [(E1_OLD, E1_NEW), (E2_OLD, E2_NEW), (E3_OLD, E3_NEW), (E4_OLD, E4_NEW), (E5_OLD, E5_NEW),
         (E6_OLD, E6_NEW), (E7_OLD, E7_NEW), (E8_OLD, E8_NEW)]


def main():
    arg = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = arg if arg.suffix == ".c" else arg / FILE
    text = path.read_text()
    if MARKER in text:
        print("wm_reader compressed streams: already patched")
        return
    for old, _ in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-wmreader-compressed-streams: an anchor was not found exactly once in %s: %r; nothing written"
                     % (path, old.strip().splitlines()[0]))
    if text.count("reinit_stream(") != 2:
        sys.exit("patch-wine-wmreader-compressed-streams: reinit_stream has another caller than SetReadStreamSamples in %s; nothing written" % path)
    for old, new in EDITS:
        text = text.replace(old, new)
    path.write_text(text)
    print("wm_reader compressed streams: patched")


if __name__ == "__main__":
    main()
