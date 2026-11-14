#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Porthole authors. The file this script writes (d3d11_lsfg.hpp) and the code it puts into
# d3d11_swapchain.cpp were taken from https://github.com/tylerkaska112/Porthole (commit 99ec315: patches 0189, 0191 and 0197)
# under GPL-3.0-or-later with the Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: made a patch script,
# BCD's folder in the prefix, the log marker, and the present state of a frame without generated frames is taken where it always
# was. The pass layout in d3d11_lsfg.hpp follows lsfg-vk 1.0.0 (MIT, Copyright (c) 2025 lsfg-vk; LICENSES/MIT-lsfg-vk.txt).
# See THIRD-PARTY-NOTICES.md.
r"""Lossless Scaling frame generation in DXMT's Direct3D 11 swap chain (opt-in; nothing of Lossless Scaling is in BCD).

A person who owns Lossless Scaling on Steam (app 993090) can let its frame generation make one to three pictures between
every two frames of a game. BCD checks the purchase with Steam, downloads the program with the person's own account, keeps
its Lossless.dll sealed on the device and, while a game with the switch on runs, writes the compiled compute shaders of the
frame generation (read out of that DLL in memory) into the game's prefix: C:\ProgramData\BCD\lsfg.bin. The file is removed
when the session ends. BCD ships none of it, converts none of it ahead of time and keeps none of it outside the device.

This script adds the engine side to DXMT (dxmt/src/d3d11):

  d3d11_lsfg.hpp (new)     lsfg::Control (the control file the app rewrites when the in-game menu changes something, read
                           twice a second: enabled, generated, flow, force, performance, measure, hz; a status file for
                           the menu), lsfg::ShaderStore (the shader file), lsfg::FrameGen (the passes: mipmaps, alpha x7,
                           beta, gamma x7, delta x3, generate, recorded on a deferred context and run on the game's own
                           with its state put back), lsfg::processAllowed (not for launchers and helper programs).
  d3d11_swapchain.cpp      Present1 feeds the back buffer to the passes before it takes the device lock, presents each
                           generated picture from a chunk of its own (committed before the next: a layer has three
                           drawables), then the game's frame. With DXMT's MetalFX swap chain each generated picture is
                           enlarged first, with a scaler of its own when its format differs (an sRGB back buffer).

Nothing runs unless the app set MADEIRA_LSFG_CTL (the control file) for the session: LsfgBeforePresent returns at its
first line, no file is opened, and the frame's present state is taken exactly where it was before (only a frame that has
generated pictures takes it earlier, for them). Environment: MADEIRA_LSFG_CTL, MADEIRA_LSFG_SHADERS (the shader file;
default C:\ProgramData\BCD\lsfg.bin), MADEIRA_LSFG=1 (on from the start, without a control file), MADEIRA_LSFG_GENERATED,
MADEIRA_LSFG_FLOW, MADEIRA_FRAMEGEN_HZ, MADEIRA_LSFG_ANY_PROCESS=1.

The 32-bit d3d11.dll (the i386 farm) is built from the patched tree; a 64-bit game gets the code through d3d11-src.dll
(tools/build-d3d11-dll.sh, BCD_PATCHES; the app sets MADEIRA_D3D11_SRC=1 with the switch). Log lines start with "[lsfg]".

NOT taken from Porthole, on purpose: the LS1 / LS1 performance upscaler (its patches 0200 to 0202).

Idempotent; fails by name if an anchor moved. Run from the repository root (or a directory whose "dxmt" is the tree).
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src/d3d11")
MARKER = "madeira-bcd: lsfg"

HEADER = r'''// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. This file was taken from https://github.com/tylerkaska112/Porthole (commit 99ec315:
// patches/0189-lossless-scaling-frame-generation.patch with the changes of 0191-lossless-scaling-d3d12-bridge-dxmt.patch) under
// GPL-3.0-or-later with the Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: the place of the files in the
// prefix, the log marker, and only the frame generation shaders are expected in the shader file. See THIRD-PARTY-NOTICES.md.
// Written into dxmt/src/d3d11/ by tools/patch-dxmt-lsfg.py at build time.
//
// Lossless Scaling frame generation, run through the ordinary D3D11 interface.
//
// The shaders are NOT part of this repository or of the app: the owner's own copy of Lossless Scaling's Lossless.dll is read on the device
// by the app (only when Steam says the account owns the program), the compiled compute shaders of its frame generation are written to a
// file in the game's prefix for the length of the session, and this code reads that file. Nothing is generated without it.
//
// How the passes are chained (which shader, what it reads and writes, how many thread groups, the constant buffer) follows lsfg-vk 1.0.0
// (https://github.com/PancakeTAS/lsfg-vk, tag v1.0.0, framegen/v3.1_src and v3.1p_src), which is under the MIT licence:
//
//   Copyright (c) 2025 lsfg-vk
//
//   Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files
//   (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge,
//   publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do
//   so, subject to the following conditions:
//
//   The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
//
//   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
//   MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
//   FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
//   CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
// (LICENSES/MIT-lsfg-vk.txt in BCD's repository.) Nothing of lsfg-vk later than 1.0.0 is used. That layout was checked by Porthole against
// the shaders in Lossless.dll 3.2.1 themselves (resource counts, thread-group sizes and the constant buffer's fields are all read out of
// each shader's own reflection data).
#pragma once

#include "d3d11_device.hpp"
#include "log/log.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <format>
#include <map>
#include <memory>
#include <string>
#include <tuple>
#include <vector>

namespace dxmt::lsfg {

/// A COM pointer that does only what is needed here.
template <class T> class Ptr {
public:
  Ptr() = default;
  Ptr(const Ptr &o) : p_(o.p_) { if (p_) p_->AddRef(); }
  Ptr(Ptr &&o) noexcept : p_(o.p_) { o.p_ = nullptr; }
  Ptr &operator=(Ptr o) noexcept { std::swap(p_, o.p_); return *this; }
  ~Ptr() { if (p_) p_->Release(); }
  T **put() { reset(); return &p_; }
  void reset() { if (p_) { p_->Release(); p_ = nullptr; } }
  T *get() const { return p_; }
  T *operator->() const { return p_; }
  explicit operator bool() const { return p_ != nullptr; }
private:
  T *p_ = nullptr;
};

/// What the app asks for, now. At start from the environment (MADEIRA_LSFG_CTL = a control file, MADEIRA_LSFG_SHADERS, and the older MADEIRA_LSFG=1 switch), afterwards from the
/// control file, which the app rewrites whenever the person changes something in the game's menu: lines "enabled=1", "generated=2", "flow=1.5", "force=1", "hz=120".
/// It is looked at twice a second at most. A status file next to it ("<control file>.status") says what the passes are doing, for the menu.
struct Control {
  std::string shaderFile, ctlPath, statusPath;
  bool available = false;       // the app set something up at all
  bool enabled = false;
  bool measure = false;         // run the passes but do not show what they make (tells the cost of the passes from the cost of showing more frames)
  bool performance = false;     // the lighter pass layout (fewer, smaller intermediate pictures)
  bool force = false;           // generate even when the game is fast enough to need no help (for testing)
  uint32_t generated = 1;       // frames made between two real ones (1 = double the frame rate)
  float flowDivisor = 1.0f;     // the motion search runs at the picture's size divided by this
  double screenHz = 0;          // 0 = ask the display
  uint32_t version = 0;         // raised when the passes have to be rebuilt or started again
  uint64_t lastWrite_ = 0;
  std::chrono::steady_clock::time_point lastPoll_{};
  bool seenFile_ = false, envOn_ = false;

  Control() {
    auto env = getenvStr("MADEIRA_LSFG");
    envOn_ = env == "1";
    ctlPath = getenvStr("MADEIRA_LSFG_CTL");
    shaderFile = getenvStr("MADEIRA_LSFG_SHADERS");
    if (shaderFile.empty()) shaderFile = "C:\\ProgramData\\BCD\\lsfg.bin";   /* madeira-bcd: BCD's folder in the prefix */
    if (!ctlPath.empty()) statusPath = ctlPath + ".status";
    available = envOn_ || !ctlPath.empty();
    enabled = envOn_;
    auto g = getenvStr("MADEIRA_LSFG_GENERATED");
    if (!g.empty()) generated = (uint32_t)std::clamp(atoi(g.c_str()), 1, 3);
    auto f = getenvStr("MADEIRA_LSFG_FLOW");
    if (!f.empty()) flowDivisor = std::clamp((float)atof(f.c_str()), 1.0f, 4.0f);
    auto h = getenvStr("MADEIRA_FRAMEGEN_HZ");
    if (!h.empty()) screenHz = atof(h.c_str());
  }

  static std::string getenvStr(const char *name) {
    char buf[1024];
    DWORD n = GetEnvironmentVariableA(name, buf, sizeof buf);
    return (n > 0 && n < sizeof buf) ? std::string(buf, n) : std::string();
  }

  void poll(std::chrono::steady_clock::time_point now) {
    if (ctlPath.empty() || now - lastPoll_ < std::chrono::milliseconds(500)) return;
    lastPoll_ = now;
    WIN32_FILE_ATTRIBUTE_DATA info{};
    if (!GetFileAttributesExA(ctlPath.c_str(), GetFileExInfoStandard, &info)) {
      if (seenFile_) { seenFile_ = false; lastWrite_ = 0; apply(envOn_, generated, flowDivisor, force, screenHz, performance); }
      return;
    }
    uint64_t w = ((uint64_t)info.ftLastWriteTime.dwHighDateTime << 32) | info.ftLastWriteTime.dwLowDateTime;
    if (w == lastWrite_ && seenFile_) return;
    HANDLE f = CreateFileA(ctlPath.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    char buf[2048];
    DWORD got = 0;
    BOOL ok = ReadFile(f, buf, sizeof buf - 1, &got, nullptr);
    CloseHandle(f);
    if (!ok) return;
    buf[got] = 0;
    seenFile_ = true;
    lastWrite_ = w;
    bool en = enabled, fc = force;
    uint32_t gen = generated;
    float fl = flowDivisor;
    double hz = screenHz;
    bool pf = performance;
    bool ms = measure;
    for (char *line = strtok(buf, "\r\n"); line; line = strtok(nullptr, "\r\n")) {
      char *eq = strchr(line, '=');
      if (!eq) continue;
      *eq = 0;
      const char *k = line, *v = eq + 1;
      if (!strcmp(k, "enabled")) en = atoi(v) != 0;
      else if (!strcmp(k, "force")) fc = atoi(v) != 0;
      else if (!strcmp(k, "generated")) gen = (uint32_t)std::clamp(atoi(v), 1, 3);
      else if (!strcmp(k, "flow")) fl = std::clamp((float)atof(v), 1.0f, 4.0f);
      else if (!strcmp(k, "hz")) hz = atof(v);
      else if (!strcmp(k, "performance")) pf = atoi(v) != 0;
      else if (!strcmp(k, "measure")) ms = atoi(v) != 0;
    }
    measure = ms;
    apply(en, gen, fl, fc, hz, pf);
  }

  void apply(bool en, uint32_t gen, float fl, bool fc, double hz, bool pf) {
    bool rebuild = en != enabled || gen != generated || fl != flowDivisor || pf != performance;
    enabled = en; generated = gen; flowDivisor = fl; force = fc; screenHz = hz; performance = pf;
    if (rebuild) version++;
  }

  /// For the menu: "state=...", then free text lines. Written at most every couple of seconds.
  void writeStatus(const std::string &text) {
    if (statusPath.empty()) return;
    HANDLE f = CreateFileA(statusPath.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD put = 0;
    WriteFile(f, text.data(), (DWORD)text.size(), &put, nullptr);
    CloseHandle(f);
  }
};

inline Control &control() {
  static Control c;
  return c;
}

/// Frame generation is for the game, not for the programs that start it: Rockstar's Launcher and Social Club helper, Steam, the Epic and EA clients and every browser helper also present
/// through Direct3D, and the control file is the same for all of them (they share the prefix), so each ran the passes on its own window (a 1280x600 and a 700x800 window at 2-8 FPS)
/// and the game's own start-up suffered. Decided once per process from its program name; MADEIRA_LSFG_ANY_PROCESS=1 lifts it.
inline bool processAllowed() {
  static int allowed = -1;
  if (allowed >= 0) return allowed == 1;
  char path[MAX_PATH] = {};
  DWORD n = GetModuleFileNameA(nullptr, path, sizeof path);
  std::string name(path, n < sizeof path ? n : 0);
  size_t slash = name.find_last_of("\\/");
  if (slash != std::string::npos) name = name.substr(slash + 1);
  for (auto &c : name) c = (char)tolower((unsigned char)c);
  static const char *deny[] = {"helper", "launcher", "service", "webview", "crashhandler", "crashreport", "reporter", "setup", "install", "unins", "steam.exe", "upc.exe", "uplay",
                               "eadesktop", "eabackground", "ealink", "origin.exe", "galaxyclient", "cefsubprocess", "dockhost", "wineboot", "explorer.exe", "playgtav.exe"};
  bool ok = true;
  for (auto *d : deny) if (name.find(d) != std::string::npos) ok = false;
  if (Control::getenvStr("MADEIRA_LSFG_ANY_PROCESS") == "1") ok = true;
  allowed = ok ? 1 : 0;
  if (control().available) Logger::info(std::format("[lsfg] madeira-bcd {} {} get frame generation", name, ok ? "may" : "may not (a launcher or helper program)"));
  return ok;
}

/// The compiled shaders, by the resource id they have in Lossless.dll. File: "LSFG", u32 version 1, u32 count, then (u32 id, u32 size, bytes) each.
class ShaderStore {
public:
  static ShaderStore &get() { static ShaderStore s; return s; }
  /// Reads the file once it exists; asked again later (the app writes it when the person switches the feature on), it tries again while nothing has been loaded.
  bool load() {
    if (!blobs_.empty()) return true;
    HANDLE f = CreateFileA(control().shaderFile.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) {
      error_ = "the shader file is not there (" + control().shaderFile + ")";
      WARN("[lsfg] ", error_);
      return false;
    }
    LARGE_INTEGER sz{};
    GetFileSizeEx(f, &sz);
    std::vector<uint8_t> data((size_t)sz.QuadPart);
    DWORD got = 0;
    bool ok = sz.QuadPart > 12 && sz.QuadPart < (256ll << 20) && ReadFile(f, data.data(), (DWORD)data.size(), &got, nullptr) && got == data.size();
    CloseHandle(f);
    if (!ok || std::memcmp(data.data(), "LSFG", 4) != 0) {
      error_ = "the shader file cannot be read";
      WARN("[lsfg] ", error_);
      return false;
    }
    auto u32 = [&](size_t o) { uint32_t v; std::memcpy(&v, data.data() + o, 4); return v; };
    if (u32(4) != 1) { error_ = "the shader file has an unknown version"; WARN("[lsfg] ", error_); return false; }
    uint32_t count = u32(8);
    size_t o = 12;
    for (uint32_t i = 0; i < count; i++) {
      if (o + 8 > data.size()) break;
      uint32_t id = u32(o), n = u32(o + 4);
      o += 8;
      if (o + n > data.size()) break;
      blobs_[id].assign(data.begin() + o, data.begin() + o + n);
      o += n;
    }
    Logger::info(std::format("[lsfg] {} shaders loaded", blobs_.size()));
    return !blobs_.empty();
  }
  const std::vector<uint8_t> *find(uint32_t id) const {
    auto it = blobs_.find(id);
    return it == blobs_.end() ? nullptr : &it->second;
  }
private:
public:
  std::string error_;
private:
  std::map<uint32_t, std::vector<uint8_t>> blobs_;
};

/// Resource ids in Lossless.dll: the 3.1 "quality" layout and the lighter "performance" one (fewer, smaller intermediate pictures; the same chain of passes).
struct Ids {
  uint32_t mip, gen;
  uint32_t alpha[4], beta[5], gamma[5], delta[10];
};
inline constexpr Ids kQuality{255, 256, {267, 268, 269, 270}, {275, 276, 277, 278, 279}, {257, 259, 260, 261, 262}, {257, 263, 264, 265, 266, 258, 271, 272, 273, 274}};
inline constexpr Ids kPerformance{255, 256, {290, 291, 292, 293}, {298, 299, 300, 301, 302}, {280, 282, 283, 284, 285}, {280, 286, 287, 288, 289, 281, 294, 295, 296, 297}};

struct ConstantBuffer { // read back out of the shaders' own reflection: InputOffset, FirstIter, FirstIterS, AdvancedColorKind, HdrSupport, ResolutionInvScale, Timestamp, UiThreshold
  uint32_t inputOffset[2];
  uint32_t firstIter, firstIterS, advancedColorKind, hdrSupport;
  float resolutionInvScale, timestamp, uiThreshold;
  uint32_t pad[3];
};
static_assert(sizeof(ConstantBuffer) == 48);

/// The shader objects, kept for the life of the device: building the passes again (a setting changed) must not make the engine convert and compile all of them once more.
inline std::map<ID3D11Device *, std::map<uint32_t, Ptr<ID3D11ComputeShader>>> &shaderCache() {
  static std::map<ID3D11Device *, std::map<uint32_t, Ptr<ID3D11ComputeShader>>> c;
  return c;
}

struct Img {
  Ptr<ID3D11Texture2D> tex;
  Ptr<ID3D11ShaderResourceView> srv;
  Ptr<ID3D11UnorderedAccessView> uav;
  UINT w = 0, h = 0;
};
using Imgs = std::vector<Img>;

class FrameGen {
public:
  FrameGen(ID3D11Device *dev, ID3D11DeviceContext4 *imm, UINT width, UINT height, DXGI_FORMAT format, uint32_t generated, float flowDivisor, bool performance)
      : dev_(dev), imm_(imm), w_(width), h_(height), fmt_(format), n_(std::max(1u, generated)), flowDiv_(flowDivisor), perf_(performance), id_(performance ? kPerformance : kQuality) {
    ok_ = build();
    if (!ok_) {
      if (error_.empty()) error_ = "a picture or sampler could not be created (" + stage_ + ")";
      Logger::info("[lsfg] could not be set up: " + error_);
    }
  }
  bool ok() const { return ok_; }
  const std::string &error() const { return error_; }
  float flowDivisor() const { return flowDiv_; }
  UINT width() const { return w_; }
  UINT height() const { return h_; }
  DXGI_FORMAT format() const { return fmt_; }
  /// The format of the pictures made (the game's own, except for an sRGB back buffer: 16-bit floats).
  DXGI_FORMAT generatedFormat() const { return genFmt_; }
  uint32_t generated() const { return n_; }

  /// Feeds the game's frame in. When `generate` and enough history is there, the in-between frames are made as well; returns how many (their textures are in `out`).
  /// Everything is recorded on a deferred context and replayed on the game's own with its state put back.
  size_t run(ID3D11Resource *backbuffer, bool generate, std::vector<ID3D11Texture2D *> &out) {
    out.clear();
    if (!ok_) return 0;
    uint64_t f = frame_;
    ID3D11DeviceContext *c = def_.get();
    prevSrv_ = prevUav_ = 0;
    c->CopyResource(in_[f % 2].tex.get(), backbuffer);
    mipmaps(c, f);
    for (int i = 6; i >= 0; i--) alpha(c, i, f);
    beta(c, f);
    bool made = false;
    if (generate && history_ >= 2) {
      for (uint32_t p = 0; p < n_; p++) {
        for (int i = 0; i < 7; i++) {
          gamma(c, i, f, p);
          if (i >= 4) delta(c, i - 4, f, p);
        }
        generateFrame(c, f, p);
        out.push_back(gen_[p].tex.get());
      }
      made = true;
    }
    Ptr<ID3D11CommandList> cl;
    if (FAILED(c->FinishCommandList(FALSE, cl.put()))) {
      Logger::info("[lsfg] FinishCommandList failed");
      ok_ = false;
      error_ = "the engine could not finish the command list";
      out.clear();
      return 0;
    }
    imm_->ExecuteCommandList(cl.get(), TRUE);
    frame_++;
    if (history_ < 3) history_++;
    return made ? out.size() : 0;
  }

  /// A frame was skipped (the game ran too fast to show two frames): the motion history is no longer one frame apart.
  void breakHistory() { history_ = 0; }

private:
  // MARK: building

  bool img(Img &i, UINT w, UINT h, DXGI_FORMAT f, UINT bind) {
    w = std::max(1u, w); h = std::max(1u, h);
    D3D11_TEXTURE2D_DESC d{};
    d.Width = w; d.Height = h; d.MipLevels = 1; d.ArraySize = 1; d.Format = f; d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT; d.BindFlags = bind;
    if (FAILED(dev_->CreateTexture2D(&d, nullptr, i.tex.put()))) return false;
    if (bind & D3D11_BIND_SHADER_RESOURCE)
      if (FAILED(dev_->CreateShaderResourceView(i.tex.get(), nullptr, i.srv.put()))) return false;
    if (bind & D3D11_BIND_UNORDERED_ACCESS)
      if (FAILED(dev_->CreateUnorderedAccessView(i.tex.get(), nullptr, i.uav.put()))) return false;
    i.w = w; i.h = h;
    return true;
  }
  bool work(Img &i, UINT w, UINT h, DXGI_FORMAT f = DXGI_FORMAT_R8G8B8A8_UNORM) {
    return img(i, w, h, f, D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS);
  }
  bool works(Imgs &v, size_t n, UINT w, UINT h, DXGI_FORMAT f = DXGI_FORMAT_R8G8B8A8_UNORM) {
    v.assign(n, Img());
    for (auto &i : v) if (!work(i, w, h, f)) return false;
    return true;
  }

  ID3D11SamplerState *sampler(int kind) { return samplers_[kind].get(); }
  bool makeSampler(int kind, D3D11_TEXTURE_ADDRESS_MODE mode, float border) {
    D3D11_SAMPLER_DESC d{};
    d.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    d.AddressU = d.AddressV = d.AddressW = mode;
    d.ComparisonFunc = D3D11_COMPARISON_NEVER;
    d.MaxLOD = D3D11_FLOAT32_MAX;
    d.MaxAnisotropy = 1;
    d.BorderColor[0] = d.BorderColor[1] = d.BorderColor[2] = d.BorderColor[3] = border;
    return SUCCEEDED(dev_->CreateSamplerState(&d, samplers_[kind].put()));
  }

  ID3D11Buffer *cbuf(float ts, bool firstIter = false, bool firstIterS = false) {
    auto key = std::make_tuple(ts, firstIter, firstIterS);
    auto it = cbufs_.find(key);
    if (it != cbufs_.end()) return it->second.get();
    ConstantBuffer cb{};
    cb.firstIter = firstIter; cb.firstIterS = firstIterS;
    cb.advancedColorKind = hdr_ ? 2u : 0u; cb.hdrSupport = hdr_ ? 1u : 0u;
    cb.resolutionInvScale = flowDiv_; cb.timestamp = ts; cb.uiThreshold = 0.5f;
    D3D11_BUFFER_DESC d{};
    d.ByteWidth = sizeof cb; d.Usage = D3D11_USAGE_IMMUTABLE; d.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    D3D11_SUBRESOURCE_DATA sd{&cb, 0, 0};
    Ptr<ID3D11Buffer> b;
    if (FAILED(dev_->CreateBuffer(&d, &sd, b.put()))) return nullptr;
    return (cbufs_[key] = b).get();
  }

  ID3D11ComputeShader *shader(uint32_t id) {
    auto &cache = shaderCache()[dev_];
    auto it = cache.find(id);
    if (it != cache.end()) return it->second.get();
    auto *blob = ShaderStore::get().find(id);
    if (!blob) { error_ = std::format("shader {} is not in the shader file", id); return nullptr; }
    Ptr<ID3D11ComputeShader> s;
    if (FAILED(dev_->CreateComputeShader(blob->data(), blob->size(), nullptr, s.put()))) {
      error_ = std::format("the engine refused shader {}", id);
      return nullptr;
    }
    return (cache[id] = s).get();
  }

  static UINT half(UINT v) { return std::max(1u, (v + 1) >> 1); }

  bool build() {
    auto t0 = std::chrono::steady_clock::now();
    stage_ = "shader file";
    if (!ShaderStore::get().load()) { error_ = ShaderStore::get().error_; return false; }
    DXGI_FORMAT f = fmt_;
    // An sRGB back buffer (Unity games, mostly) is read through its sRGB view, which hands the passes linear light, and the pictures they make are kept in 16-bit floats (an 8-bit
    // linear picture would band in the shadows). The presenter treats a source texture as sRGB-encoded only by the swap chain's format and encodes what it samples, so linear
    // floats come out right through the same pipeline as the game's own frame.
    srgb_ = f == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB || f == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    if (f != DXGI_FORMAT_R8G8B8A8_UNORM && f != DXGI_FORMAT_B8G8R8A8_UNORM && f != DXGI_FORMAT_R10G10B10A2_UNORM && f != DXGI_FORMAT_R16G16B16A16_FLOAT && !srgb_) {
      error_ = std::format("the game's back buffer format ({}) is not supported", (int)f);
      return false;
    }
    genFmt_ = srgb_ ? DXGI_FORMAT_R16G16B16A16_FLOAT : f;
    hdr_ = f == DXGI_FORMAT_R16G16B16A16_FLOAT;
    // how many intermediate pictures each pass works with (the performance layout has fewer)
    const size_t a1 = perf_ ? 1 : 2, a3 = perf_ ? 2 : 4;   // alpha: first two stages / last two stages
    n1_ = perf_ ? 3 : 4; n2_ = perf_ ? 2 : 4; nm_ = perf_ ? 1 : 2;   // gamma and delta: first temporaries / second temporaries and the middle stages / delta's last stages
    stage_ = "deferred context";
    Ptr<ID3D11DeviceContext> d;
    if (FAILED(dev_->CreateDeferredContext(0, d.put()))) return false;
    def_ = d;
    stage_ = "samplers";
    if (!makeSampler(0, D3D11_TEXTURE_ADDRESS_BORDER, 0.0f) || !makeSampler(1, D3D11_TEXTURE_ADDRESS_BORDER, 1.0f) || !makeSampler(2, D3D11_TEXTURE_ADDRESS_CLAMP, 0.0f))
      return false;
    stage_ = "history pictures";
    for (auto &i : in_) if (!img(i, w_, h_, f, D3D11_BIND_SHADER_RESOURCE)) return false;
    stage_ = std::format("output pictures (format {} as an unordered-access view)", (int)f);
    for (uint32_t p = 0; p < n_; p++) {
      gen_.emplace_back();
      if (!img(gen_.back(), w_, h_, genFmt_, D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS)) return false;
    }
    UINT fw = std::max(1u, (UINT)(w_ / flowDiv_)), fh = std::max(1u, (UINT)(h_ / flowDiv_));
    flowW_ = fw; flowH_ = fh;
    stage_ = "motion pictures";
    for (int i = 0; i < 7; i++)
      if (!work(mip_[i], fw >> i, fh >> i, DXGI_FORMAT_R8_UNORM)) return false;
    for (int i = 0; i < 7; i++) {
      Alpha &a = alpha_[i];
      UINT ew = std::max(1u, fw >> i), eh = std::max(1u, fh >> i);
      UINT hw = half(ew), hh = half(eh), qw = half(hw), qh = half(hh);
      a.half = {hw, hh}; a.quarter = {qw, qh};
      if (!works(a.t1, a1, hw, hh) || !works(a.t2, a1, hw, hh) || !works(a.t3, a3, qw, qh)) return false;
      for (int s = 0; s < 3; s++) if (!works(a.out[s], a3, qw, qh)) return false;
    }
    Beta &b = beta_;
    UINT bw = alpha_[0].quarter.first, bh = alpha_[0].quarter.second;
    b.ext = {bw, bh};
    if (!works(b.t1, 2, bw, bh) || !works(b.t2, 2, bw, bh)) return false;
    b.out.assign(6, Img());
    for (int k = 0; k < 6; k++) if (!work(b.out[k], bw >> k, bh >> k, DXGI_FORMAT_R8_UNORM)) return false;
    for (int i = 0; i < 7; i++) {
      Gamma &g = gamma_[i];
      g.ext = alpha_[6 - i].quarter;
      if (!works(g.t1, n1_, g.ext.first, g.ext.second) || !works(g.t2, n2_, g.ext.first, g.ext.second)) return false;
      if (!work(g.out, g.ext.first, g.ext.second, DXGI_FORMAT_R16G16B16A16_FLOAT)) return false;
    }
    for (int k = 0; k < 3; k++) {
      Delta &d2 = delta_[k];
      d2.ext = alpha_[6 - (k + 4)].quarter;
      if (!works(d2.t1, n1_, d2.ext.first, d2.ext.second) || !works(d2.t2, n2_, d2.ext.first, d2.ext.second)) return false;
      if (!work(d2.out1, d2.ext.first, d2.ext.second, DXGI_FORMAT_R16G16B16A16_FLOAT) || !work(d2.out2, d2.ext.first, d2.ext.second, DXGI_FORMAT_R16G16B16A16_FLOAT)) return false;
    }
    // every shader up front, so a missing or refused one is known before the first frame
    stage_ = "shaders";
    for (uint32_t id : {id_.mip, id_.gen})
      if (!shader(id)) return false;
    for (uint32_t id : id_.alpha) if (!shader(id)) return false;
    for (uint32_t id : id_.beta) if (!shader(id)) return false;
    for (uint32_t id : id_.gamma) if (!shader(id)) return false;
    for (uint32_t id : id_.delta) if (!shader(id)) return false;
    Logger::info(std::format("[lsfg] ready in {:.0f} ms: {}x{} format {}, {} layout, motion search {}x{}, {} generated per frame", std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count(), w_, h_, (int)f, perf_ ? "performance" : "quality", fw, fh, n_));
    return true;
  }

  // MARK: recording

  using Srvs = std::vector<ID3D11ShaderResourceView *>;
  using Uavs = std::vector<ID3D11UnorderedAccessView *>;

  static void add(Srvs &v, const Img &i) { v.push_back(i.srv.get()); }
  static void add(Srvs &v, const Imgs &a, size_t n = ~size_t(0)) { for (size_t i = 0; i < a.size() && i < n; i++) v.push_back(a[i].srv.get()); }
  static void add(Uavs &v, const Img &i) { v.push_back(i.uav.get()); }
  static void add(Uavs &v, const Imgs &a, size_t n = ~size_t(0)) { for (size_t i = 0; i < a.size() && i < n; i++) v.push_back(a[i].uav.get()); }

  void dispatch(ID3D11DeviceContext *c, uint32_t id, ID3D11Buffer *cb, std::initializer_list<int> smp, const Srvs &srv, const Uavs &uav, UINT gx, UINT gy) {
    ID3D11ShaderResourceView *s[16] = {};
    ID3D11UnorderedAccessView *u[8] = {};
    ID3D11SamplerState *ss[2] = {};
    UINT counts[8];
    for (auto &x : counts) x = ~0u;
    for (size_t i = 0; i < srv.size() && i < 16; i++) s[i] = srv[i];
    for (size_t i = 0; i < uav.size() && i < 8; i++) u[i] = uav[i];
    int k = 0;
    for (int kind : smp) ss[k++] = sampler(kind);
    // only as many slots as are needed now or were used by the dispatch before (those have to be cleared): every slot set costs the engine work
    size_t ns = std::max(std::min<size_t>(srv.size(), 16), prevSrv_), nu = std::max(std::min<size_t>(uav.size(), 8), prevUav_);
    prevSrv_ = std::min<size_t>(srv.size(), 16); prevUav_ = std::min<size_t>(uav.size(), 8);
    c->CSSetShader(shader(id), nullptr, 0);
    c->CSSetConstantBuffers(0, 1, &cb);
    c->CSSetSamplers(0, (UINT)k, ss);
    if (ns) c->CSSetShaderResources(0, (UINT)ns, s);
    if (nu) c->CSSetUnorderedAccessViews(0, (UINT)nu, u, counts);
    c->Dispatch(gx, gy, 1);
  }

  static UINT groups(UINT v, int shift) { return (v + (1u << shift) - 1) >> shift; }

  void mipmaps(ID3D11DeviceContext *c, uint64_t f) {
    Srvs s; add(s, in_[f % 2]);
    Uavs u; for (auto &m : mip_) add(u, m);
    dispatch(c, id_.mip, cbuf(0.0f), {0}, s, u, groups(flowW_, 6), groups(flowH_, 6));
  }

  void alpha(ID3D11DeviceContext *c, int lvl, uint64_t f) {
    Alpha &a = alpha_[lvl];
    UINT hx = groups(a.half.first, 3), hy = groups(a.half.second, 3), qx = groups(a.quarter.first, 3), qy = groups(a.quarter.second, 3);
    { Srvs s; add(s, mip_[lvl]); Uavs u; add(u, a.t1); dispatch(c, id_.alpha[0], nullptr, {0}, s, u, hx, hy); }
    { Srvs s; add(s, a.t1); Uavs u; add(u, a.t2); dispatch(c, id_.alpha[1], nullptr, {0}, s, u, hx, hy); }
    { Srvs s; add(s, a.t2); Uavs u; add(u, a.t3); dispatch(c, id_.alpha[2], nullptr, {0}, s, u, qx, qy); }
    { Srvs s; add(s, a.t3); Uavs u; add(u, a.out[f % 3]); dispatch(c, id_.alpha[3], nullptr, {0}, s, u, qx, qy); }
  }

  void beta(ID3D11DeviceContext *c, uint64_t f) {
    Beta &b = beta_;
    auto &hist = alpha_[0].out;
    UINT i = (UINT)(f % 3);
    UINT x = groups(b.ext.first, 3), y = groups(b.ext.second, 3);
    { Srvs s; add(s, hist[(i + 1) % 3]); add(s, hist[(i + 2) % 3]); add(s, hist[i % 3]); Uavs u; add(u, b.t1); dispatch(c, id_.beta[0], nullptr, {1}, s, u, x, y); }
    { Srvs s; add(s, b.t1); Uavs u; add(u, b.t2); dispatch(c, id_.beta[1], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, b.t2); Uavs u; add(u, b.t1); dispatch(c, id_.beta[2], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, b.t1); Uavs u; add(u, b.t2); dispatch(c, id_.beta[3], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, b.t2); Uavs u; add(u, b.out); dispatch(c, id_.beta[4], cbuf(0.5f), {0}, s, u, groups(b.ext.first, 5), groups(b.ext.second, 5)); }
  }

  float stamp(uint32_t p) const { return (float)(p + 1) / (float)(n_ + 1); }

  void gamma(ID3D11DeviceContext *c, int i, uint64_t f, uint32_t p) {
    Gamma &g = gamma_[i];
    auto &hist = alpha_[6 - i].out;
    UINT fi = (UINT)(f % 3);
    UINT x = groups(g.ext.first, 3), y = groups(g.ext.second, 3);
    const Img *opt = i == 0 ? nullptr : &gamma_[i - 1].out;
    const Img &in2 = beta_.out[std::min(6 - i, 5)];
    ID3D11Buffer *cb = cbuf(stamp(p), i == 0);
    { Srvs s; add(s, hist[(fi + 2) % 3]); add(s, hist[fi % 3]); if (opt) add(s, *opt);
      Uavs u; add(u, g.t1, 3); dispatch(c, id_.gamma[0], cb, {1, 2}, s, u, x, y); }
    { Srvs s; add(s, g.t1, 3); Uavs u; add(u, g.t2); dispatch(c, id_.gamma[1], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, g.t2); Uavs u; add(u, g.t1, n2_); dispatch(c, id_.gamma[2], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, g.t1, n2_); Uavs u; add(u, g.t2); dispatch(c, id_.gamma[3], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, g.t2); if (opt) add(s, *opt); else s.push_back(nullptr); add(s, in2);
      Uavs u; add(u, g.out); dispatch(c, id_.gamma[4], cb, {0, 2}, s, u, x, y); }
  }

  void delta(ID3D11DeviceContext *c, int k, uint64_t f, uint32_t p) {
    Delta &d = delta_[k];
    int i = k + 4;
    auto &hist = alpha_[6 - i].out;
    UINT fi = (UINT)(f % 3);
    UINT x = groups(d.ext.first, 3), y = groups(d.ext.second, 3);
    const Img *opt1 = k == 0 ? nullptr : &gamma_[i - 1].out;
    const Img *opt2 = k == 0 ? nullptr : &delta_[k - 1].out1;
    const Img *opt3 = k == 0 ? nullptr : &delta_[k - 1].out2;
    const Img &in2 = beta_.out[6 - i];
    ID3D11Buffer *cb = cbuf(stamp(p), false, k == 0);
    auto optSrv = [&](Srvs &s, const Img *o) { s.push_back(o ? o->srv.get() : nullptr); };
    { Srvs s; add(s, hist[(fi + 2) % 3]); add(s, hist[fi % 3]); optSrv(s, opt1);
      Uavs u; add(u, d.t1, 3); dispatch(c, id_.delta[0], cb, {1, 2}, s, u, x, y); }
    { Srvs s; add(s, d.t1, 3); Uavs u; add(u, d.t2); dispatch(c, id_.delta[1], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, d.t2); Uavs u; add(u, d.t1, n2_); dispatch(c, id_.delta[2], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, d.t1, n2_); Uavs u; add(u, d.t2); dispatch(c, id_.delta[3], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, d.t2); optSrv(s, opt1); add(s, in2);
      Uavs u; add(u, d.out1); dispatch(c, id_.delta[4], cb, {0, 2}, s, u, x, y); }
    { Srvs s; add(s, hist[(fi + 2) % 3]); add(s, hist[fi % 3]); optSrv(s, opt1); optSrv(s, opt2);
      Uavs u; add(u, d.t2, nm_); dispatch(c, id_.delta[5], cb, {1, 2}, s, u, x, y); }
    { Srvs s; add(s, d.t2, nm_); Uavs u; add(u, d.t1, nm_); dispatch(c, id_.delta[6], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, d.t1, nm_); Uavs u; add(u, d.t2, nm_); dispatch(c, id_.delta[7], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, d.t2, nm_); Uavs u; add(u, d.t1, nm_); dispatch(c, id_.delta[8], nullptr, {0}, s, u, x, y); }
    { Srvs s; add(s, d.t1, nm_); optSrv(s, opt3);
      Uavs u; add(u, d.out2); dispatch(c, id_.delta[9], cb, {0, 2}, s, u, x, y); }
  }

  void generateFrame(ID3D11DeviceContext *c, uint64_t f, uint32_t p) {
    const Img &img0 = in_[0], &img1 = in_[1];
    bool even = f % 2 == 0;
    Srvs s;
    add(s, even ? img1 : img0); add(s, even ? img0 : img1);
    add(s, gamma_[6].out); add(s, delta_[2].out1); add(s, delta_[2].out2);
    Uavs u; add(u, gen_[p]);
    dispatch(c, id_.gen, cbuf(stamp(p)), {0, 2}, s, u, groups(w_, 4), groups(h_, 4));
  }

  struct Alpha {
    Imgs t1, t2, t3;
    std::array<Imgs, 3> out;
    std::pair<UINT, UINT> half, quarter;
  };
  struct Beta {
    Imgs t1, t2;
    Imgs out;
    std::pair<UINT, UINT> ext;
  };
  struct Gamma {
    Imgs t1, t2;
    Img out;
    std::pair<UINT, UINT> ext;
  };
  struct Delta {
    Imgs t1, t2;
    Img out1, out2;
    std::pair<UINT, UINT> ext;
  };

  ID3D11Device *dev_;
  ID3D11DeviceContext4 *imm_;
  Ptr<ID3D11DeviceContext> def_;
  UINT w_, h_;
  DXGI_FORMAT fmt_;
  uint32_t n_;
  float flowDiv_;
  bool perf_;
  const Ids &id_;
  size_t n1_ = 4, n2_ = 4, nm_ = 2, prevSrv_ = 0, prevUav_ = 0;
  UINT flowW_ = 0, flowH_ = 0;
  bool hdr_ = false, ok_ = false, srgb_ = false;
  DXGI_FORMAT genFmt_ = DXGI_FORMAT_UNKNOWN;
  std::string error_, stage_;
  uint64_t frame_ = 0;
  int history_ = 0;
  std::array<Img, 2> in_;
  std::vector<Img> gen_;
  std::array<Img, 7> mip_;
  std::array<Alpha, 7> alpha_;
  Beta beta_;
  std::array<Gamma, 7> gamma_;
  std::array<Delta, 3> delta_;
  Ptr<ID3D11SamplerState> samplers_[3];
  std::map<std::tuple<float, bool, bool>, Ptr<ID3D11Buffer>> cbufs_;
};

} // namespace dxmt::lsfg
'''

A_INCLUDE = '#include "d3d11_device.hpp"\n#include "util_cpu_fence.hpp"\n'
INCLUDE = ('#include "d3d11_device.hpp"\n#include "d3d11_lsfg.hpp"   /* ' + MARKER + ' (tools/patch-dxmt-lsfg.py) */\n'
           '#include <optional>\n#include "util_cpu_fence.hpp"\n')

A_BACKBUFFER = """    // CreateDeviceTexture2D returns public reference, change to private one here
    backbuffer_->AddRefPrivate();
    backbuffer_->Release();
"""
BACKBUFFER = A_BACKBUFFER + """    lsfg_.reset();      /* """ + MARKER + """: the passes are sized for the old back buffer */
    upscaled_generated_.clear();
    lsfg_dead_ = false;
"""

A_BEFORE_LOCK = """    if (should_exit_fs)
      SetFullscreenState(FALSE, nullptr);

    std::unique_lock<d3d11_device_mutex> lock(device_->mutex);
"""
BEFORE_LOCK = """    if (should_exit_fs)
      SetFullscreenState(FALSE, nullptr);

    /* """ + MARKER + """: the pictures to show before this frame (none unless the app set MADEIRA_LSFG_CTL) */
    std::vector<Rc<Texture>> lsfg_frames;
    LsfgBeforePresent(lsfg_frames);
    std::vector<Rc<Texture>> lsfg_upscaled;   /* with the MetalFX scaler on: where each generated frame is enlarged to */
    if constexpr (EnableMetalFX) {
      for (size_t i = 0; i < lsfg_frames.size(); i++) {
        if (!EnsureUpscaledGenerated(i, lsfg_frames[i])) { lsfg_frames.clear(); lsfg_upscaled.clear(); break; }
        lsfg_upscaled.push_back(upscaled_generated_[i]->texture());
      }
    }

    std::unique_lock<d3d11_device_mutex> lock(device_->mutex);
"""

A_COMMIT = """    if (commit_before_present_ && cmd_queue.CurrentChunk()->hasCommands())
      device_context_->Commit();
    auto chunk = cmd_queue.CurrentChunk();
    chunk->signal_frame_latency_fence_ = cmd_queue.CurrentFrameSeq();
"""
COMMIT = """    if (commit_before_present_ && cmd_queue.CurrentChunk()->hasCommands())
      device_context_->Commit();
    /* """ + MARKER + """: every generated frame is presented from a command buffer of its own, committed before the next is built. The encode thread takes a drawable while it
     * builds a command buffer and the drawable only goes to the screen when that buffer is committed; the layer has three, one of them on the screen. All the frame's
     * presents in one buffer therefore needed one drawable more than there are as soon as two frames were added, and the next one waited for the layer's timeout
     * (the game ran at 3 FPS); with one added, the real frame waited for the screen to give the first one back.
     * BCD: the present state is taken here only for a frame that has generated pictures (they and the frame share it); a frame without takes it below, where it always did. */
    std::optional<Presenter::PresentState> lsfg_state;
    if (!lsfg_frames.empty()) {
      if (target_)
        presenter->changeGammaRamp(static_cast<MTLDXGIOutput *>(target_.ptr())->GetGammaRamp());   /* as below: before the state is taken */
      lsfg_state.emplace(presenter->synchronizeLayerProperties());
      for (size_t i = 0; i < lsfg_frames.size(); i++) {
        auto generated_chunk = cmd_queue.CurrentChunk();
        if constexpr (EnableMetalFX) {
          if (i >= lsfg_upscaled.size()) break;
          generated_chunk->emitcc([
            this, vsync_duration, meta = lsfg_state->metadata, scaler = (lsfg_gen_own_scaler_ ? this->metalfx_scaler_gen : this->metalfx_scaler), src = lsfg_frames[i], dst = lsfg_upscaled[i]
          ](ArgumentEncodingContext &ctx) mutable {
            ctx.upscale(src, dst, scaler);
            ctx.present(dst, presenter, vsync_duration, meta);
          });
        } else {
          generated_chunk->emitcc([this, vsync_duration, meta = lsfg_state->metadata, src = lsfg_frames[i]](ArgumentEncodingContext &ctx) mutable {
            ctx.present(src, presenter, vsync_duration, meta);
          });
        }
        device_context_->Commit();
      }
    }
    auto chunk = cmd_queue.CurrentChunk();
    chunk->signal_frame_latency_fence_ = cmd_queue.CurrentFrameSeq();
"""

A_STATE_FX = "        scaler = this->metalfx_scaler, state = presenter->synchronizeLayerProperties()\n"
STATE_FX = ("        scaler = this->metalfx_scaler,\n"
            "        state = lsfg_state ? std::move(*lsfg_state) : presenter->synchronizeLayerProperties()   /* " + MARKER + ": the state its generated frames were shown with */\n")

A_STATE_PLAIN = "        this, vsync_duration, state = presenter->synchronizeLayerProperties(),\n"
STATE_PLAIN = ("        this, vsync_duration,\n"
               "        state = lsfg_state ? std::move(*lsfg_state) : presenter->synchronizeLayerProperties(),   /* " + MARKER + ": the state its generated frames were shown with */\n")

A_METHODS = "  void UpdateStatistics(const FrameStatisticsContainer& statistics, uint64_t frame_id) {\n"
METHODS = r'''  /* madeira-bcd: lsfg (tools/patch-dxmt-lsfg.py; from Porthole's patches 0189, 0191 and 0197). */
  /* The enlarged picture for generated frame `i` (MetalFX variant), made the way upscaled_backbuffer_ is. */
  bool EnsureUpscaledGenerated(size_t i, Rc<Texture> &generated) {
    /* With an sRGB back buffer the generated pictures are 16-bit floats (see lsfg::FrameGen), not the back buffer's format; the scaler of the game's own picture was
     * made for that format and gave flickering garbage for every generated frame (2026-11-08, Schedule I with upscaling and frame generation). They get a scaler and enlarged
     * pictures of their own in their own format; the presenter samples either the same way. */
    auto gen_pf = generated->pixelFormat();
    DXGI_FORMAT gen_fmt = lsfg_ ? lsfg_->generatedFormat() : backbuffer_desc_.Format;
    bool own = gen_pf != backbuffer_->texture()->pixelFormat();
    if (own && (!metalfx_scaler_gen || lsfg_gen_pf_ != gen_pf)) {
      WMTFXSpatialScalerInfo info;
      info.input_height = desc_.Height;
      info.input_width = desc_.Width;
      info.output_height = desc_.Height * scale_factor;
      info.output_width = desc_.Width * scale_factor;
      info.color_format = gen_pf;
      info.output_format = gen_pf;
      metalfx_scaler_gen = device_->GetMTLDevice().newSpatialScaler(info);
      if (!metalfx_scaler_gen) return false;
      lsfg_gen_pf_ = gen_pf;
      upscaled_generated_.clear();
    }
    lsfg_gen_own_scaler_ = own;
    while (upscaled_generated_.size() <= i) {
      D3D11_TEXTURE2D_DESC1 d = backbuffer_desc_;
      if (own) d.Format = gen_fmt;
      d.Height *= scale_factor;
      d.Width *= scale_factor;
      Com<D3D11ResourceCommon> tex;
      if (FAILED(dxmt::CreateDeviceTexture2D(device_, &d, nullptr, reinterpret_cast<ID3D11Texture2D1 **>(&tex))))
        return false;
      upscaled_generated_.push_back(std::move(tex));
    }
    return true;
  }

  /* Feeds this frame to the Lossless Scaling passes and returns the frames to show before it. Called before the device lock is taken
   * (the passes go through the ordinary D3D11 interface, which takes it). Returns at once unless the app set MADEIRA_LSFG_CTL. */
  void LsfgBeforePresent(std::vector<Rc<Texture>> &frames) {
    auto &ctl = lsfg::control();
    if (!ctl.available || !lsfg::processAllowed() || desc_.Width < 640 || desc_.Height < 360)
      return;
    auto now = std::chrono::steady_clock::now();
    ctl.poll(now);
    if (ctl.version != lsfg_version_) {   /* switched on or off, or a setting that needs the passes built again */
      lsfg_version_ = ctl.version;
      lsfg_.reset();
      lsfg_dead_ = false;
      lsfg_error_.clear();
      lsfg_stat_t_ = now;
      lsfg_stat_frames_ = lsfg_stat_made_ = lsfg_stat_fast_ = 0; lsfg_stat_cpu_ms_ = 0;
      lsfg_dt_ = 0; lsfg_last_ = {};
      if (!ctl.enabled) ctl.writeStatus("state=off\n");
    }
    if (!ctl.enabled)
      return;
    if (lsfg_dead_) {
      if (now - lsfg_stat_t_ >= std::chrono::seconds(2)) { lsfg_stat_t_ = now; ctl.writeStatus("state=error\nmessage=" + lsfg_error_ + "\n"); }
      return;
    }
    double dt = 0;
    if (lsfg_last_.time_since_epoch().count() != 0)
      dt = std::chrono::duration<double>(now - lsfg_last_).count();
    lsfg_last_ = now;
    if (dt > 0)
      lsfg_dt_ = lsfg_dt_ > 0 ? lsfg_dt_ * 0.8 + std::min(dt, 0.16) * 0.2 : std::min(dt, 0.16);
    if (!lsfg_) {
      if (desc_.SampleDesc.Count > 1) {
        lsfg_error_ = "this game uses a multisampled swap chain, which is not supported";
        Logger::info("[lsfg] " + lsfg_error_);
        lsfg_dead_ = true;
        ctl.writeStatus("state=error\nmessage=" + lsfg_error_ + "\n");
        return;
      }
      ctl.writeStatus("state=starting\n");
      auto fg = std::make_unique<lsfg::FrameGen>(
          device_, device_context_, backbuffer_desc_.Width, backbuffer_desc_.Height, backbuffer_desc_.Format, ctl.generated, ctl.flowDivisor, ctl.performance
      );
      if (!fg->ok()) {
        lsfg_error_ = fg->error();
        lsfg_dead_ = true;
        lsfg_stat_t_ = now;
        ctl.writeStatus("state=error\nmessage=" + lsfg_error_ + "\n");
        return;
      }
      lsfg_ = std::move(fg);
      lsfg_stat_t_ = now;
      ctl.writeStatus("state=running\nmessage=waiting for the game to run slower than the screen\n");
    }
    double hz = ctl.screenHz > 0 ? ctl.screenHz : (init_refresh_rate_ < DBL_MAX ? init_refresh_rate_ : 60.0);
    lsfg_stat_frames_++;
    /* the screen has to be able to show the extra frames: a game already running near the screen's rate gets none (and no history is kept), unless asked for anyway */
    bool can = lsfg_dt_ > 0 && dt <= 0.16 && (ctl.force || lsfg_dt_ >= ((double)lsfg_->generated() + 0.9) / hz);
    if (!can) {
      lsfg_->breakHistory();
      lsfg_stat_fast_++;
    } else {
      std::vector<ID3D11Texture2D *> out;
      auto t0 = std::chrono::steady_clock::now();
      lsfg_->run(backbuffer_.ptr(), true, out);
      lsfg_stat_cpu_ms_ += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
      if (!ctl.measure)   /* "measure only": the passes ran, nothing extra is shown */
        for (auto *o : out)
          frames.push_back(GetTexture(static_cast<ID3D11Resource *>(o)));
      lsfg_stat_made_ += out.size();
    }
    double secs = std::chrono::duration<double>(now - lsfg_stat_t_).count();
    if (secs >= 2.0) {
      double passes = lsfg_stat_frames_ > lsfg_stat_fast_ ? lsfg_stat_cpu_ms_ / (double)(lsfg_stat_frames_ - lsfg_stat_fast_) : 0.0;
      Logger::info(std::format(
          "[lsfg] {:.1f} s: game {:.1f} fps (frame {:.1f} ms), generated {:.1f}/s, frames without ({}: screen {:.0f} Hz cannot show more), passes cost {:.2f} ms on the game thread per frame",
          secs, lsfg_stat_frames_ / secs, lsfg_dt_ * 1000.0, lsfg_stat_made_ / secs, lsfg_stat_fast_, hz, passes
      ));
      ctl.writeStatus(std::format(
          "state={}\ngame={:.1f}\ngenerated={:.1f}\nwithout={}\nframes={}\nscreen={:.0f}\ncost={:.2f}\nsize={}x{}\nflow={}x{}\n",
          lsfg_stat_made_ ? "generating" : "running", lsfg_stat_frames_ / secs, lsfg_stat_made_ / secs, lsfg_stat_fast_, lsfg_stat_frames_, hz, passes,
          lsfg_->width(), lsfg_->height(), (UINT)(lsfg_->width() / lsfg_->flowDivisor()), (UINT)(lsfg_->height() / lsfg_->flowDivisor())
      ));
      lsfg_stat_t_ = now; lsfg_stat_frames_ = lsfg_stat_made_ = lsfg_stat_fast_ = 0; lsfg_stat_cpu_ms_ = 0;
    }
  }

''' + A_METHODS

A_MEMBERS = "  std::conditional<EnableMetalFX, WMT::Reference<WMT::FXSpatialScaler>, std::monostate>::type metalfx_scaler;\n"
MEMBERS = r'''  /* madeira-bcd: lsfg (tools/patch-dxmt-lsfg.py): Lossless Scaling frame generation (d3d11_lsfg.hpp). Off unless the app set MADEIRA_LSFG_CTL for this game. */
  std::unique_ptr<lsfg::FrameGen> lsfg_;
  std::vector<Com<D3D11ResourceCommon>> upscaled_generated_;   /* MetalFX variant: one enlarged picture per generated frame */
  bool lsfg_dead_ = false;
  uint32_t lsfg_version_ = ~0u;
  std::string lsfg_error_;
  std::chrono::steady_clock::time_point lsfg_last_{};
  double lsfg_dt_ = 0;
  std::chrono::steady_clock::time_point lsfg_stat_t_{};
  uint64_t lsfg_stat_frames_ = 0, lsfg_stat_made_ = 0, lsfg_stat_fast_ = 0;
  double lsfg_stat_cpu_ms_ = 0;

''' + A_MEMBERS + r'''  WMT::Reference<WMT::FXSpatialScaler> metalfx_scaler_gen;   /* for generated pictures whose format is not the back buffer's */
  WMTPixelFormat lsfg_gen_pf_ = WMTPixelFormatInvalid;
  bool lsfg_gen_own_scaler_ = false;
'''

PAIRS = (
    ("include", A_INCLUDE, INCLUDE),
    ("back buffer", A_BACKBUFFER, BACKBUFFER),
    ("before the device lock", A_BEFORE_LOCK, BEFORE_LOCK),
    ("commit before present", A_COMMIT, COMMIT),
    ("present state (MetalFX swap chain)", A_STATE_FX, STATE_FX),
    ("present state", A_STATE_PLAIN, STATE_PLAIN),
    ("methods", A_METHODS, METHODS),
    ("members", A_MEMBERS, MEMBERS),
)


def main():
    swap = ROOT / "d3d11_swapchain.cpp"
    header = ROOT / "d3d11_lsfg.hpp"
    if not swap.exists():
        print(f"::error::{swap} not found (run from the repository root)")
        return 1
    s = swap.read_text()
    if MARKER in s:
        if not header.exists() or header.read_text() != HEADER:
            header.write_text(HEADER)
            print("d3d11/d3d11_lsfg.hpp: written again (the swap chain was already patched)")
        else:
            print("d3d11/d3d11_swapchain.cpp: lsfg already patched")
        return 0
    for name, anchor, _ in PAIRS:
        if s.count(anchor) != 1:
            print(f"::error::{swap}: {name} anchor found {s.count(anchor)} times, not once -- dxmt moved, review tools/patch-dxmt-lsfg.py")
            return 1
    for _, anchor, new in PAIRS:
        s = s.replace(anchor, new, 1)
    header.write_text(HEADER)
    swap.write_text(s)
    print("d3d11/d3d11_swapchain.cpp, d3d11/d3d11_lsfg.hpp: lsfg patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
