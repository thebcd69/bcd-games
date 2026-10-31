#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Wine server survives a refused master-socket bind, and a dead server ends the launch with its reason (round 624, agent D2 S1).

RM4C0R, AQZ3E2, 3DX90K and a BioShock 2 player on Discord (62 minutes at "Loading"), all LiveContainer installs: "[Wine] FATAL: bind:
Operation not permitted" ended the server thread, wineserver_is_running() kept answering 1, and the session waited on a black screen.

Checks (text; request_ios.c and the bridge are iOS-only):
  - acquire_lock(): on a bind failure other than EEXIST/EADDRINUSE (iOS only) it logs the facts, goes back to the server directory and
    tries once more when the working directory was elsewhere, and otherwise carries on without the master socket (closes the socket, returns);
    the EEXIST/EADDRINUSE branch and the non-iOS fatal_error stay; a success after the retry joins the normal path (listen, the object);
  - nothing else needs the master socket: BCD's clients connect through socketpairs, close_master_socket() is NULL-safe;
  - the bridge's fatal_error() marks the server as not running and keeps its message; a new start clears it;
  - the launch: after the ready wait, a server that is neither ready nor running fails the launch with that message and does not start Wine;
  - the library launch line says lc=0/1 (inside LiveContainer), appended after the fields that were there.
"""
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


req = (root / 'build/wineserver/request_ios.c').read_text()
lock = req[req.index('static void acquire_lock(void)\n{'):]
lock = lock[:lock.index('\n}\n') + 3]
fail = lock[lock.index('    if (bind( fd, (struct sockaddr *)&addr, slen ) == -1)'):lock.index('    atexit( socket_cleanup );')]
check('the EEXIST/EADDRINUSE branch is unchanged and still fatal',
      'if ((bind_errno == EEXIST) || (bind_errno == EADDRINUSE))' in fail
      and 'fatal_error( "couldn\'t bind to the socket even though we hold the lock\\n" );' in fail
      and 'fatal_error( "bind: address already in use (another wineserver?)\\n" );' in fail)
ios = fail[fail.index('#ifdef WINE_IOS\n        /* madeira-bcd (round 624, agent D2 S1'):fail.index('        fatal_error( "bind: %s\\n", strerror( bind_errno ));')]
check('iOS: the failure is logged with the working directory, the server directory, whether they are the same and writable',
      '"[wineserver] bind(%s) failed errno=%d (%s): cwd=%s server_dir=%s same-dir=%d access-w=%d got_lock=%d rev=r624"' in ios)
check('... one retry, only when the working directory was elsewhere and this server holds the lock, after going back to the server directory',
      'if (!same && got_lock && server_dir_fd != -1 && fchdir( server_dir_fd ) == 0)' in ios and ios.count('bind( fd, (struct sockaddr *)&addr, slen )') == 1
      and 'goto ios_bound;' in ios)
check('... otherwise the socket is closed and the server carries on without the master socket',
      ios.rstrip().endswith('close( fd );\n        return;\n#endif') and 'continuing without the master socket' in ios)
check('... a success after the retry joins the normal path (atexit, listen, the master-socket object)',
      '#ifdef WINE_IOS\nios_bound:\n#endif\n    atexit( socket_cleanup );' in lock and lock.index('ios_bound:') < lock.index('if (listen( fd, 5 ) == -1)'))
check('outside iOS the error is fatal as in Wine', '        fatal_error( "bind: %s\\n", strerror( bind_errno ));' in fail)
close = req[req.index('void close_master_socket( timeout_t timeout )'):]
close = close[:close.index('\n}\n')]
check('close_master_socket() does nothing without a master socket', '    if (master_socket)' in close)
check('BCD connects its clients through socketpairs (the master socket\'s accept is not how a client arrives)',
      'socketpair(AF_UNIX, SOCK_STREAM, 0, pair)' in (root / 'app/Madeira/WineProcessBridge.m').read_text())

br = (root / 'app/Madeira/WineServerBridge.m').read_text()
fe = br[br.index('void fatal_error( const char *err, ... ) {'):]
fe = fe[:fe.index('\n}\n')]
check('the bridge\'s fatal_error marks the server as not running and keeps its message before the thread ends',
      'g_wineserver_running = 0;' in fe and 'snprintf(g_wineserver_fatal, sizeof(g_wineserver_fatal), "%s", buf);' in fe
      and fe.index('g_wineserver_running = 0;') < fe.index('pthread_exit(NULL);'))
check('... a new start clears the message; the header exports it',
      'g_wineserver_fatal[0] = 0;' in br and 'const char *wineserver_fatal_message(void);' in (root / 'app/Madeira/WineServerBridge.h').read_text())

cv = (root / 'app/Madeira/ContentView.swift').read_text()
seq = cv[cv.index('// Step 2: Start wineserver'):cv.index('self.startWineProcess()', cv.index('// Step 2: Start wineserver'))]
check('the launch: a server that is neither ready nor running after the wait fails the launch with its message, before Wine starts',
      'if wineserver_is_ready() == 0, wineserver_is_running() == 0 {' in seq and 'String(cString: wineserver_fatal_message())' in seq
      and 'LibraryModel.shared.launchFailed("The Wine server could not start (' in seq and seq.rstrip().endswith('winiosPhase("wine-start")'))

lb = (root / 'app/Madeira/LibraryBCD.swift').read_text()
check('the library launch line ends with lc=0/1 (LiveContainer), after the fields that were there',
      '+ " lc=\\(getenv("LC_HOME_PATH") != nil ? 1 : 0)")' in lb and lb.index('metalfx=') < lb.index(' lc='))

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-wineserver-bind-fallback.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
