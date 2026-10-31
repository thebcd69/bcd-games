#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Start Wine process initialization on a background thread.
// Must be called AFTER wineserver is running.
// prefix_path: path to the Wine prefix directory
// Returns 0 on success, -1 on error.
int wine_process_start(const char *prefix_path);

/* Guest stdin. -1 until the pipe is enabled via Documents/bcd-stdin.txt. */
extern int madeira_stdin_master;
int  madeira_stdin_is_open(void);
long madeira_stdin_write(const void *buf, unsigned long len);
void madeira_stdin_close(void);

/* Guest stdout. Closed until Documents/bcd-stdout.txt enables the
 * pipe; with it, the guest's stdout is separated from the runtime's log.
 *
 * Deliberately a function rather than the extern global the stdin half also
 * avoids: Swift 6 rejects a C global `var` as shared mutable state, so the
 * global stays internal to the .m and callers ask through here. Read returns
 * bytes read, 0 at EOF, or -1 with errno EAGAIN when nothing is pending. */
int  madeira_stdout_is_open(void);
long madeira_stdout_read(void *buf, unsigned long len);

// Check if Wine process is running
int wine_process_is_running(void);

/* How the last guest program ended. Only meaningful once
 * wine_process_is_running() returns 0. */
int wine_process_exit_code(void);

// Session exit report (the library front end). ntdll calls
// wine_launched_process_did_exit() when the program the app launched (the
// session's initial process) exits; other processes are not reported.
// Returns 1 and fills *status when that program ended with an NTSTATUS error
// (0xC...) since the last reset.
void wine_launched_process_did_exit(int status);
int wine_crash_exit_status(uint32_t *status);
// Forget the recorded status; called when a session begins.
void wine_exit_status_reset(void);

// Session end (madeira-bcd, round 577; WineProcessBridge.m "Session end"). The session ends when __wine_main
// returns on the boot thread. A program that ends on another thread never gets it back there.
// wine_launched_exit_stuck: 1 when the program (the session's initial process) ended on a thread other than
// the boot thread and __wine_main has not returned; *code is its exit status, *seconds how long ago.
// wine_session_abandon: the end-of-session work the boot thread would have done (g_wine_running = 0,
// the wineserver stopped). Blocks until the wineserver thread is joined: call it off the main queue.
int wine_launched_exit_stuck(int32_t *code, double *seconds);
void wine_session_abandon(void);
// Processes the program started that still run, not counting Wine's own (explorer, services, winedevice,
// conhost, plugplay, rpcss, svchost, the Dock host) or crash reporters and helpers (process_ios.c). Their
// file names go to names. A child that could not be tracked counts as running.
int madeira_live_user_processes(char *names, int len);

// BCD Dock's game-end watch (madeira-bcd, round 579; process_ios.c, WineProcessBridge.m "Session end", DockGameEnd.swift).
// madeira_child_report: a text snapshot of the process table, one tab-separated line per process (running ones, then the
// last ones that ended with the status each ended with), the image path last; returns the number of process lines.
// wine_session_dock_game_ended: a Dock session whose game's last process ended with an error status and left nothing:
// records the status as the session's exit status and does the end work. Blocks until the wineserver thread is
// joined: call it off the main queue. 1 when it ended the session.
int madeira_child_report(char *buf, int len);
int wine_session_dock_game_ended(int32_t status);
// wine_session_dock_game_quit (round 624): the same end for a game that ended with a status that is not a crash after the
// player asked to close; the status is not made a crash status. Off the main queue. 1 when it ended the session.
int wine_session_dock_game_quit(int32_t status);
// wine_session_dock_game_left (round 625, N-S18): the same end for a game whose last program closed by itself with a status that is
// not a crash before it drew a picture (the library's watch decides); not a crash status, not the player's quit.
int wine_session_dock_game_left(int32_t status);

// madeira-bcd (round 624, agent D2 S3 and S7; process_ios.c, virtual_ios.c): the Windows desktop's exit watch
// (LibraryModel.watchDesktopExits, DesktopExitRules). madeira_child_ends_since: the processes that ended after the first `after`
// ends, one tab-separated line each (<number> <status hex> <seconds lived> <gl> <helper> <path>), oldest first; returns the
// `after` for the next call ((unsigned)-1 asks for the count alone, buf may be NULL). madeira_jit_pool_refusals: image copies
// the JIT pool refused so far.
unsigned int madeira_child_ends_since(unsigned int after, char *buf, int len);
unsigned long madeira_jit_pool_refusals(void);

// Steam S0 net-test VPN gate: write C:\madeira-continue.flag into the
// prefix's drive_c so the paused winhttp-test.exe resumes to the Steam
// stage. Called by the "Continue Net Test" UI button after the user has
// detached the JIT debugger and switched VPNs. Returns 0 on success.
int madeira_write_continue_flag(void);

// Extract the bundled prefix template if the prefix is new (idempotent).
// Madeira Dock's component setup calls it before writing Steam's registry keys.
void madeira_seed_prefix_if_needed(const char *prefix_path);

#ifdef __cplusplus
}
#endif
