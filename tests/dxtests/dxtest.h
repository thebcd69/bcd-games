// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// dxtest.h: what BCD's Direct3D 9, 10 and 11 test programs share (d3d9_cube.c, d3d10_cube.c,
// d3d11_cube.c; the Direct3D 12 one is madeira-d3d12/tests/windows/cube_window.c), and the OpenGL
// and Vulkan tests too (gl_test.c, vulkan_test.c).
//
// Each test is a plain C program for x86-64 Windows that opens a window, draws a spinning
// textured cube through its API every frame, shows the frame rate in the window title, writes a
// few lines to the log (stderr, one write each: "[dx-test] ..."), and ends when the window is
// closed (the close button, Alt+F4 or Escape). It is built with llvm-mingw
// (tools/build-dx-tests.sh) and is started from the Wine desktop's Start menu (BCDStartMenu.c).
//
// The matrices are row-major with the row-vector convention of Direct3D (v * M), so the same
// bytes go to Direct3D 9's SetTransform and to a shader's `row_major float4x4`.

#ifndef BCD_DXTEST_H
#define BCD_DXTEST_H

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define DXT_W 640   /* the client area */
#define DXT_H 480
#define DXT_BITS ((int)(sizeof(void *) * 8))   /* 64 or 32: the 32-bit and 64-bit Direct3D 9 tests are one source */

/* ------------------------------------------------------------------ log */

/* One line to stderr in one WriteFile, so lines from other threads do not interleave. From the Start menu a program has no standard
 * error (shell32 starts it with CREATE_NEW_CONSOLE, kernelbase then passes no standard handles on and a GUI program gets no console), so
 * the same line goes to <temp>\bcd-test.log too: the session log's [guest-log] mirror copies the lines of a *.log file that say error,
 * fail, exception, gpu, driver, adapter, display and a few more words (tools/patch-wine-guest-log.py), and the verdict lines below say
 * "failed" and name the adapter for that reason. See bcdtest.h. */
static HANDLE dxt_logfile;

static void dxt_log(const char *fmt, ...)
{
    char line[512];
    int n;
    DWORD written = 0;
    va_list ap;

    n = snprintf(line, sizeof line, "[dx-test] ");
    va_start(ap, fmt);
    n += vsnprintf(line + n, sizeof line - (size_t)n - 2, fmt, ap);
    va_end(ap);
    if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
    line[n++] = '\n';
    WriteFile(GetStdHandle(STD_ERROR_HANDLE), line, (DWORD)n, &written, NULL);
    if (!dxt_logfile) {   /* the first line opens <temp>\bcd-test.log (a run replaces the last one's) */
        char path[MAX_PATH + 16];
        DWORD len = GetTempPathA(MAX_PATH, path);
        dxt_logfile = INVALID_HANDLE_VALUE;
        if (len > 0 && len < MAX_PATH) {
            strcat(path, "bcd-test.log");
            dxt_logfile = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        }
    }
    if (dxt_logfile != INVALID_HANDLE_VALUE) WriteFile(dxt_logfile, line, (DWORD)n, &written, NULL);
}

/* A crash is a log line before Wine's own handling goes on (round 602: the Direct3D 8 and 9 tests install it; the exception is not
 * handled here, so nothing about what happens to the process changes). */
static inline LONG WINAPI dxt_crash_filter(EXCEPTION_POINTERS *ep)
{
    char where[160] = "";
    HMODULE m = NULL;
    const void *addr = ep->ExceptionRecord->ExceptionAddress;

    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCSTR)addr, &m) && m) {
        char path[MAX_PATH];
        if (GetModuleFileNameA(m, path, sizeof path)) {
            const char *base = strrchr(path, '\\');
            snprintf(where, sizeof where, " in %s+0x%lx", base ? base + 1 : path, (unsigned long)((const char *)addr - (const char *)m));
        }
    }
    dxt_log("CRASH: exception %#lx at %p%s", (unsigned long)ep->ExceptionRecord->ExceptionCode, addr, where);
    return EXCEPTION_CONTINUE_SEARCH;
}

static inline void dxt_crash_log(void) { SetUnhandledExceptionFilter(dxt_crash_filter); }

/* The message boxes' title. The OpenGL and Vulkan tests (gl_test.c, vulkan_test.c) name their own before including this header. */
#ifndef DXT_BOX_TITLE
#define DXT_BOX_TITLE "BCD DirectX test"
#endif

/* A failure the person can read: logged, and shown in a message box (the program ends right after). */
static void dxt_fail(const char *api, const char *what)
{
    char text[400];
    dxt_log("%s: FAILED: %s", api, what);
    snprintf(text, sizeof text, "%s\n\nThe %s test could not start.", what, api);
    MessageBoxA(NULL, text, DXT_BOX_TITLE, MB_OK | MB_ICONERROR);
}

/* ------------------------------------------------------------------ window */

static LRESULT CALLBACK dxt_wndproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    switch (m) {
    case WM_KEYDOWN:
        if (w == VK_ESCAPE) PostMessageA(h, WM_CLOSE, 0, 0);
        return 0;
    case WM_CLOSE:
        DestroyWindow(h);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    case WM_ERASEBKGND:
        return 1;   /* the API draws every pixel */
    }
    return DefWindowProcA(h, m, w, l);
}

/* A window with a DXT_W x DXT_H client area that cannot be resized, shown. */
static HWND dxt_window(const char *title)
{
    WNDCLASSA wc;
    RECT r = { 0, 0, DXT_W, DXT_H };
    const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    HWND hwnd;

    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = dxt_wndproc;
    wc.hInstance = GetModuleHandleA(NULL);
    wc.hCursor = LoadCursorA(NULL, (LPCSTR)IDC_ARROW);
    wc.lpszClassName = "BCDDirectXTest";
    if (!RegisterClassA(&wc)) return NULL;
    AdjustWindowRect(&r, style, FALSE);
    hwnd = CreateWindowExA(0, wc.lpszClassName, title, style, CW_USEDEFAULT, CW_USEDEFAULT,
                           r.right - r.left, r.bottom - r.top, NULL, NULL, wc.hInstance, NULL);
    if (!hwnd) return NULL;
    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);
    return hwnd;
}

/* Handles the window's messages; 0 once the window has been closed. */
static int dxt_pump(void)
{
    MSG msg;
    while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
        if (msg.message == WM_QUIT) return 0;
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
    return 1;
}

/* ------------------------------------------------------------------ time and the title's frame rate */

static double dxt_now(void)
{
    LARGE_INTEGER f, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)f.QuadPart;
}

typedef struct {
    HWND hwnd;
    char base[96];
    double start, last;
    unsigned frames, total;
} dxt_clock;

static void dxt_clock_start(dxt_clock *c, HWND hwnd, const char *base)
{
    c->hwnd = hwnd;
    snprintf(c->base, sizeof c->base, "%s", base);
    c->start = c->last = dxt_now();
    c->frames = c->total = 0;
}

static float dxt_seconds(const dxt_clock *c) { return (float)(dxt_now() - c->start); }

/* Counts a frame; once a second the title shows the rate (and the log has it for the first three). */
static void dxt_clock_frame(dxt_clock *c)
{
    double t = dxt_now();
    c->frames++;
    c->total++;
    if (t - c->last >= 1.0) {
        char title[160];
        double fps = c->frames / (t - c->last);
        snprintf(title, sizeof title, "%s - %.0f fps", c->base, fps);
        SetWindowTextA(c->hwnd, title);
        if (t - c->start < 3.5) dxt_log("%s", title);
        c->frames = 0;
        c->last = t;
    }
}

/* The verdict of a cube test (round 602), after its first seconds of drawing: PASS when at least 5 frames were presented and no more
 * than a tenth of the Present calls failed (it works; how fast is a number in the line, with a note under 20 fps). One log line with
 * the numbers, the adapter's name and "failed" (so that the [guest-log] mirror takes it); `title` becomes "PASS" or "FAIL" for the
 * window title's prefix. Returns 1 for PASS. (Unused in a program that does not call it, and then not in its binary.) */
static inline int dxt_verdict(const char *api, unsigned frames, unsigned failed, double seconds, const char *adapter, char *title, size_t cap)
{
    double fps = seconds > 0 ? frames / seconds : 0;
    int ok = frames >= 5 && failed * 10 <= frames;
    dxt_log("%s (%d-bit): %s drawing: %u frames in %.1f s (%.0f fps), %u Present calls failed, adapter \"%s\"%s", api, DXT_BITS, ok ? "PASS" : "FAIL", frames,
            seconds, fps, failed, adapter && adapter[0] ? adapter : "?", ok && fps < 20.0 ? " (slow: under 20 fps)" : "");
    snprintf(title, cap, "%s", ok ? "PASS" : "FAIL");
    return ok;
}

/* ------------------------------------------------------------------ matrices */

typedef struct { float m[16]; } dxt_mat;   /* m[row * 4 + column] */

static dxt_mat dxt_mul(dxt_mat a, dxt_mat b)
{
    dxt_mat r;
    int i, j, k;
    for (i = 0; i < 4; i++)
        for (j = 0; j < 4; j++) {
            float s = 0;
            for (k = 0; k < 4; k++) s += a.m[i * 4 + k] * b.m[k * 4 + j];
            r.m[i * 4 + j] = s;
        }
    return r;
}

static dxt_mat dxt_identity(void)
{
    dxt_mat r;
    memset(&r, 0, sizeof r);
    r.m[0] = r.m[5] = r.m[10] = r.m[15] = 1.0f;
    return r;
}

static dxt_mat dxt_rot_x(float a)
{
    dxt_mat r = dxt_identity();
    r.m[5] = cosf(a);  r.m[6] = sinf(a);
    r.m[9] = -sinf(a); r.m[10] = cosf(a);
    return r;
}

static dxt_mat dxt_rot_y(float a)
{
    dxt_mat r = dxt_identity();
    r.m[0] = cosf(a);  r.m[2] = -sinf(a);
    r.m[8] = sinf(a);  r.m[10] = cosf(a);
    return r;
}

static dxt_mat dxt_translate(float x, float y, float z)
{
    dxt_mat r = dxt_identity();
    r.m[12] = x; r.m[13] = y; r.m[14] = z;
    return r;
}

/* D3DXMatrixPerspectiveFovLH. */
static dxt_mat dxt_perspective(float fovy, float aspect, float zn, float zf)
{
    dxt_mat r;
    float f = 1.0f / tanf(fovy * 0.5f);
    memset(&r, 0, sizeof r);
    r.m[0] = f / aspect;
    r.m[5] = f;
    r.m[10] = zf / (zf - zn);
    r.m[11] = 1.0f;
    r.m[14] = -zn * zf / (zf - zn);
    return r;
}

/* The cube turns about two axes at rates that do not repeat soon. */
static dxt_mat dxt_world(float t) { return dxt_mul(dxt_rot_x(t * 0.6f), dxt_rot_y(t * 0.9f)); }
/* The camera sits at (0, 0, -4) looking along +z. */
static dxt_mat dxt_view(void) { return dxt_translate(0.0f, 0.0f, 4.0f); }
static dxt_mat dxt_proj(void) { return dxt_perspective(1.0472f, (float)DXT_W / (float)DXT_H, 0.5f, 20.0f); }

/* ------------------------------------------------------------------ the cube */

typedef struct { float x, y, z, u, v; unsigned char r, g, b, a; } dxt_vertex;   /* 24 bytes */

/* 24 vertices (four a face, each face a tint and the whole texture) and 36 indices. */
static void dxt_cube(dxt_vertex v[24], unsigned short idx[36])
{
    static const float faces[6][4][3] = {
        { {  .5f, -.5f, -.5f }, {  .5f,  .5f, -.5f }, {  .5f,  .5f,  .5f }, {  .5f, -.5f,  .5f } },   /* +x */
        { { -.5f, -.5f,  .5f }, { -.5f,  .5f,  .5f }, { -.5f,  .5f, -.5f }, { -.5f, -.5f, -.5f } },   /* -x */
        { { -.5f,  .5f, -.5f }, { -.5f,  .5f,  .5f }, {  .5f,  .5f,  .5f }, {  .5f,  .5f, -.5f } },   /* +y */
        { { -.5f, -.5f,  .5f }, { -.5f, -.5f, -.5f }, {  .5f, -.5f, -.5f }, {  .5f, -.5f,  .5f } },   /* -y */
        { {  .5f, -.5f,  .5f }, {  .5f,  .5f,  .5f }, { -.5f,  .5f,  .5f }, { -.5f, -.5f,  .5f } },   /* +z */
        { { -.5f, -.5f, -.5f }, { -.5f,  .5f, -.5f }, {  .5f,  .5f, -.5f }, {  .5f, -.5f, -.5f } },   /* -z */
    };
    static const unsigned char tint[6][3] = {
        { 255, 150, 150 }, { 150, 255, 255 }, { 150, 255, 150 }, { 255, 150, 255 }, { 150, 150, 255 }, { 255, 255, 150 },
    };
    static const float uv[4][2] = { { 0, 1 }, { 0, 0 }, { 1, 0 }, { 1, 1 } };
    int f, k;
    for (f = 0; f < 6; f++) {
        for (k = 0; k < 4; k++) {
            dxt_vertex *p = &v[f * 4 + k];
            p->x = faces[f][k][0]; p->y = faces[f][k][1]; p->z = faces[f][k][2];
            p->u = uv[k][0]; p->v = uv[k][1];
            p->r = tint[f][0]; p->g = tint[f][1]; p->b = tint[f][2]; p->a = 255;
        }
        idx[f * 6 + 0] = (unsigned short)(f * 4 + 0); idx[f * 6 + 1] = (unsigned short)(f * 4 + 1); idx[f * 6 + 2] = (unsigned short)(f * 4 + 2);
        idx[f * 6 + 3] = (unsigned short)(f * 4 + 0); idx[f * 6 + 4] = (unsigned short)(f * 4 + 2); idx[f * 6 + 5] = (unsigned short)(f * 4 + 3);
    }
}

#define DXT_TEX 64

/* A white and light-grey checkerboard of 8 x 8 texel cells with a darker rim, 32 bits a texel. The texels are
 * grey, so Direct3D 9's A8R8G8B8 and the R8G8B8A8 of the other APIs are the same bytes. */
static void dxt_checker(unsigned *texels)
{
    int x, y;
    for (y = 0; y < DXT_TEX; y++)
        for (x = 0; x < DXT_TEX; x++) {
            unsigned g = ((x / 8 + y / 8) & 1) ? 255 : 176;
            if (x == 0 || y == 0 || x == DXT_TEX - 1 || y == DXT_TEX - 1) g = 80;
            texels[y * DXT_TEX + x] = 0xFF000000u | (g << 16) | (g << 8) | g;
        }
}

#endif /* BCD_DXTEST_H */
