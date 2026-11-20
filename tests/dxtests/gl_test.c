// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// BCD's OpenGL test: the spinning textured cube of the DirectX tests (dxtest.h), drawn with OpenGL through opengl32.dll the way a
// Windows game does: a pixel format with a depth buffer (ChoosePixelFormat), a context (wglCreateContext), the OpenGL 2.0 calls a
// game takes from wglGetProcAddress (shaders, vertex buffers), a texture, and SwapBuffers. The shaders are GLSL 1.10 on desktop
// OpenGL (Mesa's Zink, gl-backend = zink) and GLSL ES 1.00 on OpenGL ES (Apple's, gl-backend = gles); the program asks
// GL_VERSION which one it has. One source, two programs: gl-test-x64.exe (Start menu: "OpenGL test") and gl-test-x86.exe
// ("OpenGL test (32-bit)"). After two seconds of drawing it logs "PASS" or "FAIL" with the frame count and rate (dxt_verdict) and
// puts the word into the window title; on the next frame it reads two pixels back (glReadPixels: where the cube is and a corner)
// and logs whether the cube is really in the picture.
//
// A session has OpenGL only when the game's own file says `gl-backend = zink` or `gles` (the Windows desktop's Options >
// Direct3D > OpenGL); without it there is no pixel format or context, and the test says how to turn OpenGL on instead of only
// failing. Built with llvm-mingw (tools/build-dx-tests.sh) and no import library: opengl32.dll is loaded at run time.

#define DXT_BOX_TITLE "BCD OpenGL test"
#include "dxtest.h"
#include <GL/gl.h>

#define API "OpenGL"

/* OpenGL 2.0 names <GL/gl.h> (OpenGL 1.1) does not have. */
#define GL_ARRAY_BUFFER          0x8892
#define GL_ELEMENT_ARRAY_BUFFER  0x8893
#define GL_STATIC_DRAW           0x88E4
#define GL_FRAGMENT_SHADER       0x8B30
#define GL_VERTEX_SHADER         0x8B31
#define GL_COMPILE_STATUS        0x8B81
#define GL_LINK_STATUS           0x8B82
#define GL_INFO_LOG_LENGTH       0x8B84
#define GL_SHADING_LANGUAGE_VERSION 0x8B8C
#define GL_TEXTURE0              0x84C0
#define GL_CLAMP_TO_EDGE_        0x812F
typedef char GLchar_;
typedef ptrdiff_t GLsizeiptr_;

/* The calls, taken at run time: OpenGL 1.1 and WGL from opengl32.dll (GetProcAddress), OpenGL 2.0 from the context
 * (wglGetProcAddress), as a game does. */
#define GL11_FUNCS(X) \
    X(const GLubyte *, glGetString, (GLenum)) X(void, glViewport, (GLint, GLint, GLsizei, GLsizei)) \
    X(void, glClearColor, (GLfloat, GLfloat, GLfloat, GLfloat)) X(void, glClear, (GLbitfield)) X(void, glEnable, (GLenum)) \
    X(void, glDisable, (GLenum)) X(void, glDepthFunc, (GLenum)) X(void, glGenTextures, (GLsizei, GLuint *)) \
    X(void, glBindTexture, (GLenum, GLuint)) X(void, glTexImage2D, (GLenum, GLint, GLint, GLsizei, GLsizei, GLint, GLenum, GLenum, const void *)) \
    X(void, glTexParameteri, (GLenum, GLenum, GLint)) X(void, glDrawElements, (GLenum, GLsizei, GLenum, const void *)) \
    X(void, glReadPixels, (GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void *)) X(GLenum, glGetError, (void)) \
    X(void, glDeleteTextures, (GLsizei, const GLuint *)) X(void, glPixelStorei, (GLenum, GLint)) \
    X(HGLRC, wglCreateContext, (HDC)) X(BOOL, wglDeleteContext, (HGLRC)) X(BOOL, wglMakeCurrent, (HDC, HGLRC)) \
    X(PROC, wglGetProcAddress, (LPCSTR))
#define GL20_FUNCS(X) \
    X(GLuint, glCreateShader, (GLenum)) X(void, glShaderSource, (GLuint, GLsizei, const GLchar_ *const *, const GLint *)) \
    X(void, glCompileShader, (GLuint)) X(void, glGetShaderiv, (GLuint, GLenum, GLint *)) \
    X(void, glGetShaderInfoLog, (GLuint, GLsizei, GLsizei *, GLchar_ *)) X(GLuint, glCreateProgram, (void)) \
    X(void, glAttachShader, (GLuint, GLuint)) X(void, glBindAttribLocation, (GLuint, GLuint, const GLchar_ *)) \
    X(void, glLinkProgram, (GLuint)) X(void, glGetProgramiv, (GLuint, GLenum, GLint *)) \
    X(void, glGetProgramInfoLog, (GLuint, GLsizei, GLsizei *, GLchar_ *)) X(void, glUseProgram, (GLuint)) \
    X(GLint, glGetUniformLocation, (GLuint, const GLchar_ *)) X(void, glUniformMatrix4fv, (GLint, GLsizei, GLboolean, const GLfloat *)) \
    X(void, glUniform1i, (GLint, GLint)) X(void, glGenBuffers, (GLsizei, GLuint *)) X(void, glBindBuffer, (GLenum, GLuint)) \
    X(void, glBufferData, (GLenum, GLsizeiptr_, const void *, GLenum)) \
    X(void, glVertexAttribPointer, (GLuint, GLint, GLenum, GLboolean, GLsizei, const void *)) \
    X(void, glEnableVertexAttribArray, (GLuint)) X(void, glDeleteBuffers, (GLsizei, const GLuint *)) \
    X(void, glDeleteProgram, (GLuint)) X(void, glDeleteShader, (GLuint)) X(void, glActiveTexture, (GLenum))

#define BCD_GL_DECLARE(ret, name, args) typedef ret (WINAPI *name##_fn) args; static name##_fn p_##name;
GL11_FUNCS(BCD_GL_DECLARE)
GL20_FUNCS(BCD_GL_DECLARE)
#undef BCD_GL_DECLARE

/* The shaders: one text, two first lines. GLSL 1.10 runs on any desktop OpenGL 2.0 or later (a compatibility context, which is
 * what wglCreateContext makes); GLSL ES 1.00 runs on OpenGL ES 2.0 and 3.0. The matrix goes in as the C code's rows (dxtest.h:
 * v * M) with transpose GL_FALSE, so GLSL reads them as columns and mvp * v is the same product. */
static const char vs_body[] =
    "uniform mat4 mvp;\n"
    "attribute vec3 pos;\n"
    "attribute vec2 uv;\n"
    "attribute vec4 col;\n"
    "varying vec2 v_uv;\n"
    "varying vec4 v_col;\n"
    "void main() { v_uv = uv; v_col = col; gl_Position = mvp * vec4(pos, 1.0); }\n";
static const char fs_body[] =
    "uniform sampler2D tex;\n"
    "varying vec2 v_uv;\n"
    "varying vec4 v_col;\n"
    "void main() { gl_FragColor = texture2D(tex, v_uv) * v_col; }\n";

/* OpenGL is off in this session (no pixel format, no context): say how to turn it on. */
static void opengl_off(const char *why)
{
    dxt_log(API " (%d-bit): OpenGL is not on in this session (%s); the Windows desktop's Options > Direct3D > OpenGL turns it on", DXT_BITS, why);
    MessageBoxA(NULL,
                "OpenGL is not on in this session.\n\n"
                "Turn it on in the Windows desktop's Options (Home > Windows desktop > the sliders button) > Direct3D > "
                "OpenGL > Desktop OpenGL. Then stop the Windows desktop and start it again, and run this test once more.\n\n"
                "A game gets OpenGL the same way, from its own Options.",
                DXT_BOX_TITLE, MB_OK | MB_ICONINFORMATION);
}

static GLuint compile(GLenum type, const char *first, const char *body, const char *what)
{
    const GLchar_ *text[2] = { first, body };
    GLint status = 0;
    GLuint s = p_glCreateShader(type);
    p_glShaderSource(s, 2, text, NULL);
    p_glCompileShader(s);
    p_glGetShaderiv(s, GL_COMPILE_STATUS, &status);
    if (!status) {
        char info[400], m[480];
        GLsizei n = 0;
        info[0] = 0;
        p_glGetShaderInfoLog(s, sizeof info, &n, info);
        snprintf(m, sizeof m, "the %s shader does not compile: %s", what, info[0] ? info : "(no message)");
        dxt_fail(API, m);
        p_glDeleteShader(s);
        return 0;
    }
    return s;
}

/* Once, on the frame after the verdict, before SwapBuffers: the back buffer's pixel at the centre (the cube) and at a corner. */
static void readback(void)
{
    unsigned char c[4] = { 0, 0, 0, 0 }, k[4] = { 0, 0, 0, 0 };
    unsigned centre, corner;
    GLenum err;

    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glReadPixels(DXT_W / 2, DXT_H / 2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, c);
    p_glReadPixels(4, DXT_H - 5, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, k);
    err = p_glGetError();
    centre = ((unsigned)c[0] << 16) | ((unsigned)c[1] << 8) | c[2];
    corner = ((unsigned)k[0] << 16) | ((unsigned)k[1] << 8) | k[2];
    if (err != GL_NO_ERROR) dxt_log(API ": read-back: glReadPixels gave error %#x", (unsigned)err);
    else
        dxt_log(API ": read-back: centre %06x, corner %06x (clear colour 0f121a): %s", centre, corner,
                centre != corner ? "the cube is in the back buffer" : "the centre is the clear colour (nothing drawn, or the read-back returns nothing)");
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    HMODULE dll;
    HWND hwnd;
    HDC dc;
    HGLRC rc;
    PIXELFORMATDESCRIPTOR pfd;
    int format, gl_on, es, reported = 0;
    char title[96], verdict[16], backend[32], adapter[160];
    const char *version, *renderer, *vendor, *glsl;
    GLuint vs, fs, prog, tex = 0, buf[2];
    GLint status = 0, u_mvp, u_tex;
    dxt_vertex verts[24];
    unsigned short idx[36];
    unsigned texels[DXT_TEX * DXT_TEX];
    dxt_clock clock;
    unsigned failures = 0, presented = 0;
    GLenum err;

    (void)inst; (void)prev; (void)cmd; (void)show;
    dxt_crash_log();
    dxt_log(API " (%d-bit): start, %dx%d", DXT_BITS, DXT_W, DXT_H);
    backend[0] = 0;
    GetEnvironmentVariableA("MADEIRA_GL_BACKEND", backend, sizeof backend);
    gl_on = backend[0] != 0;
    dxt_log(API ": the session's OpenGL: %s", gl_on ? backend : "none (OpenGL is not on in this session)");
    snprintf(title, sizeof title, "BCD OpenGL test (%d-bit)", DXT_BITS);
    dll = LoadLibraryA("opengl32.dll");
    if (!dll) {
        if (!gl_on) opengl_off("there is no opengl32.dll");
        else dxt_fail(API, "opengl32.dll is not available");
        return 1;
    }
#define BCD_GL_LOAD(ret, name, args) p_##name = (name##_fn)(void (*)(void))GetProcAddress(dll, #name);
    GL11_FUNCS(BCD_GL_LOAD)
#undef BCD_GL_LOAD
#define BCD_GL_HAVE(ret, name, args) if (!p_##name) { dxt_fail(API, "opengl32.dll has no " #name); return 1; }
    GL11_FUNCS(BCD_GL_HAVE)
#undef BCD_GL_HAVE

    hwnd = dxt_window(title);
    if (!hwnd) { dxt_fail(API, "the window could not be created"); return 1; }
    dc = GetDC(hwnd);
    memset(&pfd, 0, sizeof pfd);
    pfd.nSize = sizeof pfd;
    pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    pfd.iPixelType = PFD_TYPE_RGBA;
    pfd.cColorBits = 32;
    pfd.cAlphaBits = 8;
    pfd.cDepthBits = 24;
    pfd.cStencilBits = 8;
    pfd.iLayerType = PFD_MAIN_PLANE;
    format = ChoosePixelFormat(dc, &pfd);
    if (!format) {
        if (!gl_on) opengl_off("ChoosePixelFormat found no OpenGL pixel format");
        else dxt_fail(API, "ChoosePixelFormat found no OpenGL pixel format");
        return 1;
    }
    DescribePixelFormat(dc, format, sizeof pfd, &pfd);
    dxt_log(API ": pixel format %d: %d colour bits, %d depth, %d stencil, %s", format, pfd.cColorBits, pfd.cDepthBits, pfd.cStencilBits,
            (pfd.dwFlags & PFD_DOUBLEBUFFER) ? "double-buffered" : "single-buffered");
    if (!SetPixelFormat(dc, format, &pfd)) { dxt_fail(API, "SetPixelFormat failed"); return 1; }
    rc = p_wglCreateContext(dc);
    if (!rc) {
        if (!gl_on) opengl_off("wglCreateContext made no context");
        else dxt_fail(API, "wglCreateContext made no context");
        return 1;
    }
    if (!p_wglMakeCurrent(dc, rc)) { dxt_fail(API, "wglMakeCurrent failed"); return 1; }
    version = (const char *)p_glGetString(GL_VERSION);
    renderer = (const char *)p_glGetString(GL_RENDERER);
    vendor = (const char *)p_glGetString(GL_VENDOR);
    glsl = (const char *)p_glGetString(GL_SHADING_LANGUAGE_VERSION);
    if (!version) { dxt_fail(API, "glGetString(GL_VERSION) gave nothing: the context does not work"); return 1; }
    es = !strncmp(version, "OpenGL ES", 9);
    dxt_log(API ": GL_VERSION \"%s\", GL_RENDERER \"%s\", GL_VENDOR \"%s\", GLSL \"%s\"", version, renderer ? renderer : "?",
            vendor ? vendor : "?", glsl ? glsl : "?");
    snprintf(adapter, sizeof adapter, "%s", renderer ? renderer : "?");

#define BCD_GL_LOAD(ret, name, args) p_##name = (name##_fn)(void (*)(void))p_wglGetProcAddress(#name);
    GL20_FUNCS(BCD_GL_LOAD)
#undef BCD_GL_LOAD
    /* a driver may answer 1, 2, 3 or -1 instead of NULL for a name it does not have (the WGL documentation warns of it) */
#define BCD_GL_HAVE(ret, name, args) if ((INT_PTR)p_##name >= -1 && (INT_PTR)p_##name <= 3) { \
        dxt_fail(API, "wglGetProcAddress gave no " #name " (OpenGL 2.0 is needed)"); return 1; }
    GL20_FUNCS(BCD_GL_HAVE)
#undef BCD_GL_HAVE

    /* the program: GLSL 1.10, or GLSL ES 1.00 with a default float precision for the fragment shader */
    vs = compile(GL_VERTEX_SHADER, es ? "#version 100\n" : "#version 110\n", vs_body, "vertex");
    if (!vs) return 1;
    fs = compile(GL_FRAGMENT_SHADER, es ? "#version 100\nprecision mediump float;\n" : "#version 110\n", fs_body, "fragment");
    if (!fs) return 1;
    prog = p_glCreateProgram();
    p_glAttachShader(prog, vs);
    p_glAttachShader(prog, fs);
    p_glBindAttribLocation(prog, 0, "pos");
    p_glBindAttribLocation(prog, 1, "uv");
    p_glBindAttribLocation(prog, 2, "col");
    p_glLinkProgram(prog);
    p_glGetProgramiv(prog, GL_LINK_STATUS, &status);
    if (!status) {
        char info[400], m[480];
        GLsizei n = 0;
        info[0] = 0;
        p_glGetProgramInfoLog(prog, sizeof info, &n, info);
        snprintf(m, sizeof m, "the shaders do not link: %s", info[0] ? info : "(no message)");
        dxt_fail(API, m);
        return 1;
    }
    p_glUseProgram(prog);
    u_mvp = p_glGetUniformLocation(prog, "mvp");
    u_tex = p_glGetUniformLocation(prog, "tex");
    p_glUniform1i(u_tex, 0);
    dxt_log(API ": %s shaders compiled and linked", es ? "GLSL ES 1.00" : "GLSL 1.10");

    /* the cube in two buffers; the attributes at the dxt_vertex offsets */
    dxt_cube(verts, idx);
    p_glGenBuffers(2, buf);
    p_glBindBuffer(GL_ARRAY_BUFFER, buf[0]);
    p_glBufferData(GL_ARRAY_BUFFER, sizeof verts, verts, GL_STATIC_DRAW);
    p_glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, buf[1]);
    p_glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof idx, idx, GL_STATIC_DRAW);
    p_glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(dxt_vertex), (const void *)0);
    p_glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(dxt_vertex), (const void *)12);
    p_glVertexAttribPointer(2, 4, GL_UNSIGNED_BYTE, GL_TRUE, sizeof(dxt_vertex), (const void *)20);
    p_glEnableVertexAttribArray(0);
    p_glEnableVertexAttribArray(1);
    p_glEnableVertexAttribArray(2);

    /* the texture (grey, so RGBA and BGRA are the same bytes), linear, no mipmaps */
    dxt_checker(texels);
    p_glActiveTexture(GL_TEXTURE0);
    p_glGenTextures(1, &tex);
    p_glBindTexture(GL_TEXTURE_2D, tex);
    p_glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    p_glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, DXT_TEX, DXT_TEX, 0, GL_RGBA, GL_UNSIGNED_BYTE, texels);
    p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE_);
    p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE_);

    {   /* one frame a display refresh, as the DirectX tests present (WGL_EXT_swap_control, where the driver has it) */
        typedef BOOL (WINAPI *swap_interval_fn)(int);
        swap_interval_fn swap_interval = (swap_interval_fn)(void (*)(void))p_wglGetProcAddress("wglSwapIntervalEXT");
        if ((INT_PTR)swap_interval < -1 || (INT_PTR)swap_interval > 3)
            dxt_log(API ": wglSwapIntervalEXT(1) %s", swap_interval(1) ? "on" : "refused");
        else
            dxt_log(API ": no wglSwapIntervalEXT: frames as fast as SwapBuffers takes them");
    }
    p_glViewport(0, 0, DXT_W, DXT_H);
    p_glEnable(GL_DEPTH_TEST);
    p_glDepthFunc(GL_LESS);
    p_glDisable(GL_CULL_FACE);
    p_glClearColor(15 / 255.0f, 18 / 255.0f, 26 / 255.0f, 1.0f);   /* the DirectX tests' clear colour, 0f121a */
    err = p_glGetError();
    if (err != GL_NO_ERROR) dxt_log(API ": set-up left error %#x", (unsigned)err);

    dxt_clock_start(&clock, hwnd, title);
    dxt_log(API ": drawing");
    while (dxt_pump()) {
        dxt_mat mvp = dxt_mul(dxt_mul(dxt_world(dxt_seconds(&clock)), dxt_view()), dxt_proj());
        p_glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        p_glUniformMatrix4fv(u_mvp, 1, GL_FALSE, mvp.m);
        p_glDrawElements(GL_TRIANGLES, 36, GL_UNSIGNED_SHORT, (const void *)0);
        if (reported == 1) {   /* the frame after the verdict */
            readback();
            reported = 2;
        }
        if (SwapBuffers(dc)) {
            presented++;
        } else {
            if (failures++ < 3) dxt_log(API ": SwapBuffers failed (%lu)", (unsigned long)GetLastError());
            Sleep(16);
        }
        if (presented == 1) {
            err = p_glGetError();
            if (err != GL_NO_ERROR) dxt_log(API ": the first frame left error %#x", (unsigned)err);
        }
        dxt_clock_frame(&clock);
        if (!reported && dxt_seconds(&clock) >= 2.0f) {   /* the verdict, once; the title keeps it */
            reported = 1;
            dxt_verdict(API, presented, failures, dxt_seconds(&clock), adapter, verdict, sizeof verdict);
            snprintf(clock.base, sizeof clock.base, "%s - %s", title, verdict);
        }
    }
    dxt_log(API ": the window was closed after %u frames", clock.total);

    p_glDeleteTextures(1, &tex);
    p_glDeleteBuffers(2, buf);
    p_glDeleteProgram(prog);
    p_glDeleteShader(vs);
    p_glDeleteShader(fs);
    p_wglMakeCurrent(NULL, NULL);
    p_wglDeleteContext(rc);
    ReleaseDC(hwnd, dc);
    return 0;
}
