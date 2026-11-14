#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Porthole authors. The file this script writes (d3d11_lsfg_bridge.cpp) and the code it puts into
# d3d11_fence.cpp, d3d11_fence.hpp and d3d11.def were taken from https://github.com/tylerkaska112/Porthole (commit 99ec315:
# patches/0191-lossless-scaling-d3d12-bridge-dxmt.patch) under GPL-3.0-or-later with the Madeira Converter Exception. Modified by
# BCD (thebcd69), November 2026: made a patch script for d3d11-src.dll's copy of the sources. See THIRD-PARTY-NOTICES.md.
r"""Lossless Scaling frame generation for Direct3D 12 games: the bridge inside d3d11-src.dll (opt-in).

tools/patch-dxmt-lsfg.py puts Lossless Scaling's frame generation passes into DXMT's Direct3D 11 swap chain. A Direct3D 12 game
never draws through that swap chain: madeira_d3d12.dll presents with Metal itself. The passes are Direct3D 11 compute shaders, so
for such a game they run here, on a Direct3D 11 device of their own inside d3d11.dll:

  d3d11_lsfg_bridge.cpp (new)   DXMTLsfgBegin (does this frame get extra frames? hands out the texture the game's picture is copied
                                into and a shared event), DXMTLsfgRun (waits for that event, runs the passes, returns the Metal
                                textures of the generated pictures and the event that says they are done), DXMTLsfgAbort.
  d3d11_fence.cpp / .hpp        CreateFenceExposingEvent: a Direct3D 11 fence whose MTLSharedEvent the caller gets as well.
  d3d11.def                     the three functions are exported.

madeira_d3d12.c (mad_lsfg_before, mad_lsfg_show) looks for the three functions in the d3d11.dll of the session, and only when the
app set MADEIRA_LSFG_CTL. They exist in d3d11-src.dll only: this script is applied by tools/build-d3d11-dll.sh to its copy of the
sources (after tools/patch-dxmt-lsfg.py, whose d3d11_lsfg.hpp the bridge includes), never to the dxmt submodule and never for the
32-bit d3d11.dll (Direct3D 12 games are 64-bit), which is why it is not one of the workflow's tools/patch-dxmt-*.py steps.
Upstream's d3d11.dll has no such functions, so without env.MADEIRA_D3D11_SRC = 1 (which the app sets with the game's
`lossless-scaling` line) a Direct3D 12 game gets no extra frames and nothing else changes.

Usage: patch-d3d11-src-lsfg-bridge.py <the d3d11 source directory of the copy>. Idempotent; fails by name if an anchor moved.
"""
import pathlib
import sys

MARKER = "madeira-bcd: lsfg bridge"

BRIDGE = r'''// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. This file was taken from https://github.com/tylerkaska112/Porthole (commit 99ec315:
// patches/0191-lossless-scaling-d3d12-bridge-dxmt.patch) under GPL-3.0-or-later with the Madeira Converter Exception. Modified by BCD
// (thebcd69), November 2026: the header only (nothing of the code). See THIRD-PARTY-NOTICES.md.
// Written into the d3d11 sources of d3d11-src.dll by tools/patch-d3d11-src-lsfg-bridge.py at build time.
//
// Lossless Scaling frame generation for Direct3D 12 games.
//
// Direct3D 12 games do not draw through this DLL: their swap chain belongs to madeira_d3d12.dll, which presents the finished picture with Metal directly. The frame generation
// passes (d3d11_lsfg.hpp) are Direct3D 11 compute shaders, so they run here, on a Direct3D 11 device of their own that nobody else uses:
//
//   madeira_d3d12.dll                                 this DLL (bridge device)
//   -----------------                                 ------------------------
//   DXMTLsfgBegin()  ---------------------------->    decides (settings, speed of the game), makes the pictures; returns the Metal texture the
//                                                     game's finished picture is to be copied into, and the shared events used below
//   copy the picture into it, signal input_event
//   DXMTLsfgRun()    ---------------------------->    waits for input_event, runs the passes, signals out_event; returns the Metal textures of the
//                                                     generated pictures
//   for each generated picture: wait out_event, copy it to a drawable, present
//   signal done_event once the last copy is queued ->  the next run waits for done_event before it writes the generated pictures again
//
// Both sides use the same Metal device, so the textures and the shared events are the ones Metal hands out; no copy through the CPU happens. Everything that can go wrong ends
// in "returns 0": the game then simply gets no extra frames (and the status file says why).
#include "d3d11_lsfg.hpp"
#include "d3d11_fence.hpp"
#include "d3d11_resource.hpp"
#include <mutex>

extern "C" {
// The layout is shared with madeira_d3d12.dll (madeira-d3d12/src/pe/madeira_d3d12.c, struct lsfg_bridge_frame): both change together.
struct DXMTLsfgFrame {
  uint32_t size;                    // sizeof (this), so an older caller is refused
  uint32_t width, height;           // the game's picture
  uint32_t dxgi_format;             // its DXGI format
  uint64_t input_texture;           // Metal texture (obj_handle_t) to copy the picture into
  uint64_t input_event, input_value;   // shared event: the caller signals it with input_value once the copy is done
  uint32_t count;                   // pictures generated (out of Run)
  uint32_t reserved;
  uint64_t out_texture[3];          // Metal textures of the generated pictures
  uint64_t out_event, out_value;    // shared event: the passes are done when it reaches out_value
  uint64_t done_event, done_value;  // the caller signals it with done_value once it has copied the generated pictures out
};
}

namespace {

using namespace dxmt;
using namespace dxmt::lsfg;

struct Bridge {
  std::mutex lock;
  Ptr<ID3D11Device> dev;
  Ptr<ID3D11DeviceContext> imm;
  Ptr<ID3D11DeviceContext4> imm4;
  Ptr<ID3D11Fence> inFence, outFence, doneFence;
  obj_handle_t inEvent = 0, outEvent = 0, doneEvent = 0;
  uint64_t inValue = 0, outValue = 0, doneValue = 0, doneWait = 0;
  Ptr<ID3D11Texture2D> input;
  std::unique_ptr<FrameGen> fg;
  UINT w = 0, h = 0;
  DXGI_FORMAT fmt = DXGI_FORMAT_UNKNOWN;
  uint32_t version = ~0u;
  bool dead = false;
  std::string error;
  bool running = false;     // Begin said yes and Run has not been called yet
  // pacing and statistics, as in the Direct3D 11 swap chain
  std::chrono::steady_clock::time_point last{}, statT{};
  double dt = 0;
  uint64_t statFrames = 0, statMade = 0, statFast = 0;
  double statMs = 0;
  bool measure = false;
  uint64_t cBegin = 0, cYes = 0, cFast = 0, cBusy = 0;   // heartbeat counters
  std::chrono::steady_clock::time_point busySince{};
  std::chrono::steady_clock::time_point beat{};
};

Bridge &bridge() {
  static Bridge b;
  return b;
}

void fail(Bridge &b, Control &ctl, const std::string &why) {
  b.error = why;
  b.dead = true;
  b.fg.reset();
  b.statT = std::chrono::steady_clock::now();
  Logger::info("[lsfg] (Direct3D 12 bridge) " + why);
  ctl.writeStatus("state=error\nmessage=" + why + "\n");
}

bool ensureDevice(Bridge &b, Control &ctl) {
  if (b.dev) return true;
  D3D_FEATURE_LEVEL level{};
  HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION, b.dev.put(), &level, b.imm.put());
  if (FAILED(hr) || !b.dev || !b.imm) { fail(b, ctl, std::format("the Direct3D 11 device for the passes could not be created (0x{:x})", (unsigned)hr)); b.dev.reset(); return false; }
  if (FAILED(b.imm->QueryInterface(__uuidof(ID3D11DeviceContext4), reinterpret_cast<void **>(b.imm4.put())))) { fail(b, ctl, "the Direct3D 11 device has no fences"); b.dev.reset(); return false; }
  auto *d = static_cast<MTLD3D11Device *>(b.dev.get());
  if (FAILED(CreateFenceExposingEvent(d, 0, &b.inEvent, b.inFence.put())) || FAILED(CreateFenceExposingEvent(d, 0, &b.outEvent, b.outFence.put())) ||
      FAILED(CreateFenceExposingEvent(d, 0, &b.doneEvent, b.doneFence.put()))) {
    fail(b, ctl, "the events that join the two graphics paths could not be created");
    b.dev.reset();
    return false;
  }
  b.inValue = b.outValue = b.doneValue = b.doneWait = 0;
  Logger::info("[lsfg] (Direct3D 12 bridge) Direct3D 11 device for the passes created");
  return true;
}

obj_handle_t metalTexture(ID3D11Resource *r) {
  auto t = GetTexture(r);
  if (!t) return 0;
  auto *a = t->current();
  return a ? a->texture().handle : 0;
}

void rebuild(Bridge &b) {
  b.fg.reset();
  b.input.reset();
  b.running = false;
  b.dt = 0;
  b.last = {};
  b.statFrames = b.statMade = b.statFast = 0;
  b.statMs = 0;
}

} // namespace

extern "C" {

/// Asks whether this frame gets extra frames. Returns 1 when the caller has to copy its finished picture into `input_texture`, signal `input_event` with `input_value`, and then
/// call DXMTLsfgRun. Returns 0 when nothing is to be done now (switched off, not needed, or not possible: the status file says which).
__declspec(dllexport) int DXMTLsfgBegin(DXMTLsfgFrame *f) {
  if (!f || f->size < sizeof(DXMTLsfgFrame)) return 0;
  Bridge &b = bridge();
  std::lock_guard<std::mutex> g(b.lock);
  auto &ctl = control();
  if (!ctl.available || !processAllowed() || f->width < 640 || f->height < 360) return 0;
  auto now = std::chrono::steady_clock::now();
  ctl.poll(now);
  if (ctl.version != b.version) {
    b.version = ctl.version;
    rebuild(b);
    b.dead = false;
    b.error.clear();
    b.statT = now;
    if (!ctl.enabled) ctl.writeStatus("state=off\n");
  }
  b.measure = ctl.measure;
  if (!ctl.enabled) return 0;
  if (b.dead) {
    if (now - b.statT >= std::chrono::seconds(2)) { b.statT = now; ctl.writeStatus("state=error\nmessage=" + b.error + "\n"); }
    return 0;
  }
  double dt = 0;
  if (b.last.time_since_epoch().count() != 0) dt = std::chrono::duration<double>(now - b.last).count();
  b.last = now;
  if (dt > 0) b.dt = b.dt > 0 ? b.dt * 0.8 + std::min(dt, 0.16) * 0.2 : std::min(dt, 0.16);
  if (b.fg && (b.w != f->width || b.h != f->height || b.fmt != (DXGI_FORMAT)f->dxgi_format)) rebuild(b);   // the game changed its resolution
  if (!b.fg) {
    if (f->width < 64 || f->height < 64) return 0;
    ctl.writeStatus("state=starting\n");
    if (!ensureDevice(b, ctl)) return 0;
    b.w = f->width; b.h = f->height; b.fmt = (DXGI_FORMAT)f->dxgi_format;
    D3D11_TEXTURE2D_DESC d{};
    d.Width = b.w; d.Height = b.h; d.MipLevels = 1; d.ArraySize = 1; d.Format = b.fmt; d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT; d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(b.dev->CreateTexture2D(&d, nullptr, b.input.put())) || !metalTexture(b.input.get())) { fail(b, ctl, "the picture the game's frame is copied into could not be created"); return 0; }
    auto fg = std::make_unique<FrameGen>(b.dev.get(), b.imm4.get(), b.w, b.h, b.fmt, ctl.generated, ctl.flowDivisor, ctl.performance);
    if (!fg->ok()) { fail(b, ctl, fg->error()); return 0; }
    if (fg->generatedFormat() != b.fmt) { fail(b, ctl, std::format("the game's back buffer format ({}) is not supported in Direct3D 12 games", (int)b.fmt)); return 0; }
    b.fg = std::move(fg);
    b.statT = now;
    ctl.writeStatus("state=running\nmessage=waiting for the game to run slower than the screen\n");
  }
  double hz = ctl.screenHz > 0 ? ctl.screenHz : 60.0;
  b.statFrames++;
  b.cBegin++;
  if (b.beat.time_since_epoch().count() == 0) b.beat = now;
  if (now - b.beat >= std::chrono::seconds(5)) {   // what the bridge decided, even when it never gets to run (the log has no other trace of it)
    Logger::info(std::format("[lsfg] (Direct3D 12 bridge) last {:.0f} s: {} presents seen, {} asked for the passes, {} skipped (game faster than {:.0f} Hz allows), {} while the last passes were still running, game frame {:.1f} ms",
                             std::chrono::duration<double>(now - b.beat).count(), b.cBegin, b.cYes, b.cFast, hz, b.cBusy, b.dt * 1000.0));
    b.beat = now; b.cBegin = b.cYes = b.cFast = b.cBusy = 0;
  }
  // The previous frame's passes are still running on the GPU (the first run of each shader also has to be compiled, which takes seconds): start no more, so no work piles up behind them
  // and the game is never made to wait for them. A pass that is still not done after 20 s is taken for stuck.
  if (b.outValue > 0 && MTLSharedEvent_signaledValue(b.outEvent) < b.outValue) {
    if (b.busySince.time_since_epoch().count() == 0) b.busySince = now;
    if (now - b.busySince > std::chrono::seconds(20)) { fail(b, ctl, "the passes did not finish in 20 seconds, so frame generation switched itself off for this game (switch it off and on in the menu to try again)"); return 0; }
    b.fg->breakHistory();
    b.cBusy++;
    return 0;
  }
  b.busySince = {};
  bool can = b.dt > 0 && dt <= 0.16 && (ctl.force || b.dt >= ((double)b.fg->generated() + 0.9) / hz);
  if (!can) {
    b.fg->breakHistory();
    b.statFast++;
    b.cFast++;
    return 0;
  }
  f->input_texture = metalTexture(b.input.get());
  f->input_event = b.inEvent;
  f->input_value = ++b.inValue;
  b.running = true;
  b.cYes++;
  return 1;
}

/// The caller has copied its picture and committed the signal. Runs the passes and returns how many pictures were generated (their Metal textures are in out_texture).
__declspec(dllexport) int DXMTLsfgRun(DXMTLsfgFrame *f) {
  if (!f || f->size < sizeof(DXMTLsfgFrame)) return 0;
  Bridge &b = bridge();
  std::lock_guard<std::mutex> g(b.lock);
  auto &ctl = control();
  f->count = 0;
  if (!b.running || !b.fg) return 0;
  b.running = false;
  auto t0 = std::chrono::steady_clock::now();
  b.imm4->Wait(b.inFence.get(), f->input_value);          // the game's picture is in the input texture
  b.imm4->Wait(b.doneFence.get(), b.doneWait);              // the caller has copied last time's generated pictures out
  std::vector<ID3D11Texture2D *> out;
  b.fg->run(b.input.get(), true, out);
  if (!b.fg->ok()) { fail(b, ctl, b.fg->error()); return 0; }
  b.imm4->Signal(b.outFence.get(), ++b.outValue);
  b.imm->Flush();
  b.statMs += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
  b.statMade += out.size();
  f->out_event = b.outEvent;
  f->out_value = b.outValue;
  f->done_event = b.doneEvent;
  f->done_value = ++b.doneValue;
  b.doneWait = f->done_value;                               // the next run waits for this one
  uint32_t n = 0;
  if (!b.measure) {   // "measure only": the passes ran, nothing extra is shown
    for (auto *o : out) {
      if (n >= 3) break;
      f->out_texture[n] = metalTexture(o);
      if (!f->out_texture[n]) { n = 0; break; }
      n++;
    }
  }
  if (b.measure || n == 0) {   // nothing will be copied out: let the next run go on
    MTLSharedEvent_signalValue(b.doneEvent, f->done_value);
  }
  f->count = n;
  auto now = std::chrono::steady_clock::now();
  double secs = std::chrono::duration<double>(now - b.statT).count();
  if (secs >= 2.0) {
    double hz = ctl.screenHz > 0 ? ctl.screenHz : 60.0;
    uint64_t ran = b.statFrames > b.statFast ? b.statFrames - b.statFast : 0;
    double passes = ran ? b.statMs / (double)ran : 0.0;
    Logger::info(std::format(
        "[lsfg] (Direct3D 12) {:.1f} s: game {:.1f} fps (frame {:.1f} ms), generated {:.1f}/s, frames without ({}: screen {:.0f} Hz cannot show more), passes cost {:.2f} ms on the present thread per frame",
        secs, b.statFrames / secs, b.dt * 1000.0, b.statMade / secs, b.statFast, hz, passes));
    ctl.writeStatus(std::format(
        "state={}\ngame={:.1f}\ngenerated={:.1f}\nwithout={}\nframes={}\nscreen={:.0f}\ncost={:.2f}\nsize={}x{}\nflow={}x{}\n",
        b.statMade ? "generating" : "running", b.statFrames / secs, b.statMade / secs, b.statFast, b.statFrames, hz, passes, b.fg->width(), b.fg->height(),
        (UINT)(b.fg->width() / b.fg->flowDivisor()), (UINT)(b.fg->height() / b.fg->flowDivisor())));
    b.statT = now; b.statFrames = b.statMade = b.statFast = 0; b.statMs = 0;
  }
  return (int)n;
}

/// The caller could not copy its picture after DXMTLsfgBegin said yes: nothing waits for it.
__declspec(dllexport) void DXMTLsfgAbort(DXMTLsfgFrame *f) {
  if (!f) return;
  Bridge &b = bridge();
  std::lock_guard<std::mutex> g(b.lock);
  b.running = false;
  if (b.fg) b.fg->breakHistory();
  if (b.inEvent) MTLSharedEvent_signalValue(b.inEvent, f->input_value);
  if (f->reserved == 1) fail(b, control(), "the passes did not finish in time, so frame generation switched itself off for this game (switch it off and on in the menu to try again)");
}
}
'''

A_FENCE_CPP = """HRESULT
OpenSharedFence(MTLD3D11Device *pDevice, HANDLE hResource,
                REFIID riid, void **ppFence) {
"""
FENCE_CPP = """/* """ + MARKER + """ (tools/patch-d3d11-src-lsfg-bridge.py; from Porthole's patch 0191): a fence whose Metal shared event the caller
 * gets as well (the Lossless Scaling bridge for Direct3D 12 games waits on it from another Metal queue). */
HRESULT
CreateFenceExposingEvent(MTLD3D11Device *pDevice, UINT64 InitialValue, obj_handle_t *pEvent, ID3D11Fence **ppFence) {
  auto event = pDevice->GetMTLDevice().newSharedEvent();
  if (!event)
    return E_FAIL;
  event.signalValue(InitialValue);
  *pEvent = event.handle;
  auto fence = new MTLD3D11FenceImpl(pDevice, std::move(event), 0);
  return fence->QueryInterface(__uuidof(ID3D11Fence), reinterpret_cast<void **>(ppFence));
}

""" + A_FENCE_CPP

A_FENCE_HPP = """HRESULT
OpenSharedFence(MTLD3D11Device *pDevice, HANDLE hResource,
                REFIID riid, void **ppFence);
"""
FENCE_HPP = A_FENCE_HPP + """
/* """ + MARKER + """: a fence whose Metal shared event the caller also wants (d3d11_lsfg_bridge.cpp). */
HRESULT
CreateFenceExposingEvent(MTLD3D11Device *pDevice, UINT64 InitialValue, obj_handle_t *pEvent, ID3D11Fence **ppFence);
"""

A_DEF = "    D3D11On12CreateDevice @24"
DEF = A_DEF + "\n    DXMTLsfgBegin\n    DXMTLsfgRun\n    DXMTLsfgAbort\n"
EXPORTS = ("DXMTLsfgBegin", "DXMTLsfgRun", "DXMTLsfgAbort")

FILES = (
    ("d3d11_fence.cpp", "fence implementation", A_FENCE_CPP, FENCE_CPP, MARKER),
    ("d3d11_fence.hpp", "fence declaration", A_FENCE_HPP, FENCE_HPP, MARKER),
    ("d3d11.def", "exports", A_DEF, DEF, "DXMTLsfgBegin"),
)


def main():
    if len(sys.argv) != 2:
        print("usage: patch-d3d11-src-lsfg-bridge.py <d3d11 source directory>")
        return 2
    root = pathlib.Path(sys.argv[1])
    if not (root / "d3d11_lsfg.hpp").is_file():
        print(f"::error::{root}/d3d11_lsfg.hpp is not there: tools/patch-dxmt-lsfg.py has to run on this tree first")
        return 1
    texts = {}
    for name, what, anchor, _, mark in FILES:
        path = root / name
        if not path.is_file():
            print(f"::error::{path} not found")
            return 1
        s = path.read_text()
        if mark not in s and s.count(anchor) != 1:
            print(f"::error::{path}: {what} anchor found {s.count(anchor)} times, not once -- dxmt moved, review tools/patch-d3d11-src-lsfg-bridge.py")
            return 1
        texts[name] = s
    changed = 0
    for name, _, anchor, new, mark in FILES:
        if mark not in texts[name]:
            (root / name).write_text(texts[name].replace(anchor, new, 1))
            changed += 1
    bridge = root / "d3d11_lsfg_bridge.cpp"
    if not bridge.is_file() or bridge.read_text() != BRIDGE:
        bridge.write_text(BRIDGE)
        changed += 1
    print("d3d11: lsfg bridge patched" if changed else "d3d11: lsfg bridge already patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
