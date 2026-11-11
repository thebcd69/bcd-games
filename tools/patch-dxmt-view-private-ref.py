#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A view holds its resource with a PRIVATE reference, as Windows' (and DXVK's) views do (opt-in; night round 2026-11-11, G5 S15).

Hatred (Unreal Engine 4.10.2, Steam 341940; Send Log PKJ7ZW, build 630): the engine's viewport resize checks the reference count of
its back buffer before it lets DXGI resize the swap chain, and stops itself with
  D3D11Viewport.cpp:(198): BackBuffer->GetResource() has 3 refs, expected 1
DXMT's views (RTV, SRV, UAV, DSV) keep `Com<RESOURCE_IMPL> resource;`, a PUBLIC reference, so every view made on a texture raises the
number AddRef/Release report for it (the engine's own GetBuffer reference + a render target view + a shader resource view = 3). On
Windows a view's reference to its resource is internal and not part of that number; DXVK, from which Com<> comes, keeps it as
Com<T, false> for that reason (util/com/com_object.hpp: "not to break games which steal internal references if the reference count of
an object is greater than they expect"). The swap chain code already does the same for its own back buffer (d3d11_swapchain.cpp:
CreateDeviceTexture2D returns a public reference, AddRefPrivate(); Release()).

With env.MADEIRA_VIEW_PRIVATE_REF = 1 (default off: nothing changes for a game without the line) the view constructor turns the
public reference it just took into a private one (AddRefPrivate(); Release()) and the destructor turns it back before the Com<>
member lets go (AddRef(); ReleasePrivate()), so the resource lives as long as one of its views, as before, and the public count a
game reads no longer includes the views. The first six views made this way are logged: "[view-ref] madeira-bcd #N kind=RTV
resource=... public=3->2 private=P" (public count before -> after, private count). Only for resource types that have the private
reference calls (every buffer and texture class; a view on a plain ID3D11Resource is left alone). Whether the engine's count then
reads 1 depends on nothing else of DXMT's holding public references (the first suspect would be the DXGI resource wrapper): the log
line shows the counts. A 64-bit game gets it with env.MADEIRA_D3D11_SRC = 1 (tools/build-d3d11-dll.sh builds d3d11-src.dll with this
patch; the committed 64-bit d3d11.dll is upstream's); a 32-bit game from the farm's d3d11.dll.

Idempotent; fails by name if an anchor moved. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: views hold their resource privately"
REL = "d3d11/d3d11_resource.hpp"

OLD_INC = '#include "log/log.hpp"\n'
NEW_INC = '#include "log/log.hpp"\n#include "util_env.hpp"   /* ' + MARKER + ' */\n#include <atomic>\n'

OLD_HEAD = """template <typename tag, typename... Base>
class TResourceViewBase
    : public MTLD3D11DeviceChild<typename tag::COM_IMPL, Base...> {
public:
"""
NEW_HEAD = """/* """ + MARKER + """ (night round 2026-11-11, Hatred PKJ7ZW): env.MADEIRA_VIEW_PRIVATE_REF = 1; off by default.
 * Unreal Engine 4.10 checks that its back buffer has exactly one reference before a resize; DXMT's views counted as public ones. */
inline bool MadeiraViewPrivateRefOn() {
  static const bool on = [] {
    const std::string env_v = env::getEnvVar("MADEIRA_VIEW_PRIVATE_REF");
    return !env_v.empty() && env_v != "0";
  }();
  return on;
}

inline unsigned MadeiraViewRefLogCount() {
  static std::atomic<unsigned> logged{0};
  return ++logged;
}

template <typename tag, typename... Base>
class TResourceViewBase
    : public MTLD3D11DeviceChild<typename tag::COM_IMPL, Base...> {
public:
"""

OLD_CTOR = """        d3d10(static_cast<typename tag::COM_IMPL *>(this)), resource(pResource) {
    if (pDesc) {
      desc = *pDesc;
    }
  }
"""
NEW_CTOR = """        d3d10(static_cast<typename tag::COM_IMPL *>(this)), resource(pResource) {
    if (pDesc) {
      desc = *pDesc;
    }
    /* """ + MARKER + """: the public reference `resource` just took becomes a private one (see MadeiraViewPrivateRefOn) */
    if constexpr (requires(typename tag::RESOURCE_IMPL *r) { r->AddRefPrivate(); r->ReleasePrivate(); r->GetPrivateRefCount(); }) {
      if (MadeiraViewPrivateRefOn() && resource) {
        resource->AddRefPrivate();
        ULONG public_after = resource->Release();
        priv_ = true;
        unsigned n = MadeiraViewRefLogCount();
        if (n <= 6) {
          const char *kind = std::is_same_v<typename tag::COM, ID3D11ShaderResourceView> ? "SRV"
                             : std::is_same_v<typename tag::COM, ID3D11UnorderedAccessView> ? "UAV"
                             : std::is_same_v<typename tag::COM, ID3D11RenderTargetView> ? "RTV" : "DSV";
          ERR("[view-ref] madeira-bcd #", n, " kind=", kind, " resource=", (const void *)resource.ptr(), " public=", public_after + 1, "->",
              public_after, " private=", resource->GetPrivateRefCount());
        }
      }
    }
  }

  ~TResourceViewBase() {
    /* """ + MARKER + """: give the public reference back before the Com<> member releases it */
    if constexpr (requires(typename tag::RESOURCE_IMPL *r) { r->AddRefPrivate(); r->ReleasePrivate(); r->GetPrivateRefCount(); }) {
      if (priv_) {
        resource->AddRef();
        resource->ReleasePrivate();
      }
    }
  }
"""

OLD_MEMBER = """  /**
  strong ref to resource
  */
  Com<typename tag::RESOURCE_IMPL> resource;
};
"""
NEW_MEMBER = """  /**
  strong ref to resource
  */
  Com<typename tag::RESOURCE_IMPL> resource;
  bool priv_ = false;   /* """ + MARKER + """: `resource` holds a private reference, not a public one */
};
"""
PAIRS = ((OLD_INC, NEW_INC), (OLD_HEAD, NEW_HEAD), (OLD_CTOR, NEW_CTOR), (OLD_MEMBER, NEW_MEMBER))


def main():
    path = ROOT / REL
    if not path.exists():
        sys.exit(f"patch-dxmt-view-private-ref: {path} not found (run from the repository root)")
    s = path.read_text()
    if MARKER in s:
        print(f"{REL}: already patched")
        return
    for old, _ in PAIRS:
        if s.count(old) != 1:
            sys.exit("patch-dxmt-view-private-ref: an anchor was not found exactly once in " + REL + " (DXMT changed): " + old.strip().splitlines()[0])
    for old, new in PAIRS:
        s = s.replace(old, new, 1)
    path.write_text(s)
    print(f"{REL}: patched")


main()
