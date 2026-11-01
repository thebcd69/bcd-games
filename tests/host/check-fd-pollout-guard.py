#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_SRV_POLLOUT_GUARD (opt-in, round 626, agent R S1; the Rockstar launcher M443E0, PN6QF2; a class of 172 archive logs).

The wineserver's poll loop (build/wineserver/fd_ios.c) synthesizes POLLOUT for every fd it classes as not INET, and its class cache
kept the class of a (user, fd) pair after the slot was recycled: a new TCP socket on an old pipe's slot and fd number got a connect
completion at once, the program sent and the kernel answered ENOTCONN. With the key on, a recycled slot forgets its class and a
POLLOUT for a TCP stream socket without a peer is withheld (synthesized or real; POLLERR/POLLHUP untouched). Off, nothing changes.

Checks: the switch is read with the server's other switches (bcd.cfg / the game's lines, then the environment), exactly "1"; every new
call is behind it; the real dispatch never touches POLLERR/POLLHUP; the cache, ios_fd_class_forget and ios_tcp_unconnected, cut from
the source and run on the host with real sockets: a fresh TCP socket is "unconnected", a loopback-connected one is not (and is cached),
an AF_UNIX pair, a UDP socket and a pipe are not (cached as not a TCP stream); a pipe's class on a (user, fd) pair stays stale after
dup2 of a TCP socket onto the fd number (the old bug) until ios_fd_class_forget, which makes it INET; auto-tune's rule, the gta4-steam
row and its fixture; the catalog; CI.
"""
from pathlib import Path
import json, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "build/wineserver/fd_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


check("the switch is read with the server's others, exactly \"1\"",
      'const char *pg = ios_srv_switch( "MADEIRA_SRV_POLLOUT_GUARD", pgb, sizeof(pgb) );' in SRC
      and "ios_pollout_guard = pg && pg[0] == '1' && !pg[1];" in SRC)
check("slot recycling forgets the class only with the switch",
      SRC.count("if (ios_pollout_guard) ios_fd_class_forget( ret );") == 1 and SRC.count("if (ios_pollout_guard) ios_fd_class_forget( user );") == 1)
check("both real dispatch loops withhold POLLOUT only with the switch, never with POLLERR/POLLHUP",
      SRC.count("if (ios_pollout_guard && (rp[k].revents & POLLOUT) && !(rp[k].revents & (POLLERR | POLLHUP)) &&") == 2)
check("the synthesized POLLOUT is withheld only with the switch, and the class forgotten",
      "if (ios_pollout_guard && ios_tcp_unconnected( i, pollfd[i].fd ))" in SRC and "ios_fd_class_forget( i );   /* the next pass" in SRC)
cache = SRC[SRC.index("static int *cache_fd;"):SRC.index("/* pollout-guard-test:begin")]
region = SRC[SRC.index("/* pollout-guard-test:begin"):SRC.index("/* pollout-guard-test:end */")]
HARNESS = r'''
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
''' + cache + region + r'''
int main( void )
{
    int fails = 0, sp[2], pp[2], lst, cli, srv, tcp, udp;
    struct sockaddr_in a; socklen_t al = sizeof(a);
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
    tcp = socket( AF_INET, SOCK_STREAM, 0 );
    T( ios_fd_is_inet( 3, tcp ) == 1 );
    T( ios_tcp_unconnected( 3, tcp ) == 1 );                                   /* no peer: a completion would be false */
    lst = socket( AF_INET, SOCK_STREAM, 0 ); memset( &a, 0, sizeof(a) ); a.sin_family = AF_INET; a.sin_addr.s_addr = htonl( INADDR_LOOPBACK );
    T( !bind( lst, (struct sockaddr *)&a, sizeof(a) ) && !listen( lst, 1 ) && !getsockname( lst, (struct sockaddr *)&a, &al ) );
    cli = socket( AF_INET, SOCK_STREAM, 0 ); T( !connect( cli, (struct sockaddr *)&a, sizeof(a) ) ); srv = accept( lst, NULL, NULL );
    T( ios_fd_is_inet( 4, cli ) == 1 );
    T( ios_tcp_unconnected( 4, cli ) == 0 && cache_peer[4] == 1 );              /* connected, and cached */
    T( !socketpair( AF_UNIX, SOCK_STREAM, 0, sp ) );
    T( ios_fd_is_inet( 5, sp[0] ) == 0 && ios_tcp_unconnected( 5, sp[0] ) == 0 && cache_peer[5] == 2 );   /* AF_UNIX: not TCP */
    udp = socket( AF_INET, SOCK_DGRAM, 0 );
    T( ios_fd_is_inet( 6, udp ) == 1 && ios_tcp_unconnected( 6, udp ) == 0 && cache_peer[6] == 2 );        /* UDP: not a stream */
    T( !pipe( pp ) );
    T( ios_fd_is_inet( 7, pp[0] ) == 0 && ios_tcp_unconnected( 7, pp[0] ) == 0 && cache_peer[7] == 2 );   /* a pipe */
    /* the old bug: the pipe's slot and fd number get a TCP socket; the cache keeps "not INET" ... */
    T( dup2( tcp, pp[0] ) == pp[0] );
    T( ios_fd_is_inet( 7, pp[0] ) == 0 );                                       /* stale: what the old loop synthesized POLLOUT for */
    ios_fd_class_forget( 7 );                                                   /* ... until the recycled slot forgets it */
    T( ios_fd_is_inet( 7, pp[0] ) == 1 && cache_peer[7] == 0 && ios_tcp_unconnected( 7, pp[0] ) == 1 );
    puts( fails ? "FAILED" : "ok: no peer = withhold; connected, AF_UNIX, UDP, pipes untouched; a forgotten slot is classed again" );
    close( srv ); close( cli ); close( lst ); close( tcp ); close( udp ); close( sp[0] ); close( sp[1] ); close( pp[0] ); close( pp[1] );
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the cache and the guard compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("with real sockets: only an unconnected TCP stream is withheld; a recycled slot is classed again", run.returncode == 0, run.stdout)
else:
    print("note: no host C compiler; the guard was not run")

cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents it, off by default", re.search(r'key: "env\.MADEIRA_SRV_POLLOUT_GUARD"[^\n]*defaultValue: "0"', cat) is not None)
recs = json.loads((root / "recommended/recommendations.json").read_text())
carriers = sorted(r["id"] for r in recs["rules"] if "MADEIRA_SRV_POLLOUT_GUARD" in json.dumps(r["config"]))
check("only the GTA IV Steam row sets it, from build 626", carriers == ["gta4-steam"]
      and {"line": "env.MADEIRA_SRV_POLLOUT_GUARD = 1", "minBuild": 626} in next(r for r in recs["rules"] if r["id"] == "gta4-steam")["config"], carriers)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 1", '"MADEIRA_SRV_POLLOUT_GUARD",' in pol and 'case "MADEIRA_SRV_POLLOUT_GUARD":' in pol)
rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "fabricated-connect-completion"'):] if '"id": "fabricated-connect-completion"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune adds the line after a fabricated connect completion", '"lines": ["env.MADEIRA_SRV_POLLOUT_GUARD = 1"]' in r
      and "peer=NO path=SYNTHESIZED-or-still-connecting" in r and "errno=57" in r, r[:400])
exp = root / "tests/fixtures/autotune/rockstar-fabricated-connect-2026-10-23.expect"
check("the fixture is there and expects the line", exp.is_file() and json.loads(exp.read_text()).get("next") == ["env.MADEIRA_SRV_POLLOUT_GUARD = 1"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fd-pollout-guard.py" in wf)
print("PASS: POLLOUT guard" if ok else "FAILED: POLLOUT guard")
sys.exit(0 if ok else 1)
