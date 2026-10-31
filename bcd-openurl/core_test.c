// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The host test of bcd-openurl/core.h (round 625): the command lines Wine's registry, Unreal Engine and ShellExecute give, the
// addresses that pass and the ones that do not, the host for the log, the file name and the switch. Compiled by
// tests/host/check-open-url.py with the host's clang and the sanitizers.
#include "core.h"

static int failures, checks;
#define CHECK(cond, what) do { checks++; if (!(cond)) { failures++; printf("FAIL %s\n", what); } else printf("ok   %s\n", what); } while (0)

static int arg_is(const char *line, const char *want)
{
    char out[OU_MAX_URL + 1];
    if (!ou_argument(line, out, sizeof out)) return want == NULL;
    return want && !strcmp(out, want);
}

int main(void)
{
    char big[OU_MAX_URL + 64], line[OU_MAX_URL + 128], host[64], name[64];

    /* the command lines */
    CHECK(arg_is("\"C:\\windows\\system32\\winebrowser.exe\" \"https://store.steampowered.com/app/1/\"", "https://store.steampowered.com/app/1/"),
          "Wine's registry command: the quoted address");
    CHECK(arg_is("C:\\windows\\system32\\winebrowser.exe  \"https://www.epicgames.com/activate?userCode=ABCD1234\"",
                 "https://www.epicgames.com/activate?userCode=ABCD1234"),
          "Unreal's LaunchURL (SAZFJQ): an unquoted program, two spaces, the quoted address");
    CHECK(arg_is("winebrowser.exe -nohome http://example.com/x", "http://example.com/x"), "Wine's -nohome is passed over");
    CHECK(arg_is("\"winebrowser.exe\" -nohome \"https://a.b/c d\"", "https://a.b/c d"), "a quoted argument keeps its spaces (the page test refuses them)");
    CHECK(arg_is("\"C:\\Program Files\\x\\winebrowser.exe\"", NULL), "no argument: nothing");
    CHECK(arg_is("winebrowser.exe   ", NULL), "only spaces: nothing");
    CHECK(arg_is("\"winebrowser.exe\" \"\" https://a.b/", "https://a.b/"), "an empty quoted argument is passed over");
    memset(big, 'a', sizeof big - 1); big[sizeof big - 1] = 0;
    memcpy(big, "https://", 8);
    snprintf(line, sizeof line, "winebrowser.exe %s", big);
    CHECK(arg_is(line, NULL), "an argument longer than 2048 characters: nothing");
    CHECK(!ou_argument(NULL, line, sizeof line) && !ou_argument("x y", NULL, 10), "no line or no room: nothing");

    /* the page test */
    CHECK(ou_is_page("https://www.epicgames.com/activate?userCode=ABCD1234"), "an https page");
    CHECK(ou_is_page("http://go.beamng.com/unusualProccessExit/1"), "an http page (BeamNG's crash page)");
    CHECK(ou_is_page("HTTPS://Example.com"), "the scheme in capitals");
    CHECK(ou_is_page("https://example.com:8443/x#y"), "a port and a fragment");
    CHECK(!ou_is_page("file:///C:/readme.html") && !ou_is_page("C:\\Games\\readme.html"), "a file is not a page");
    CHECK(!ou_is_page("mailto:someone@example.com") && !ou_is_page("ftp://example.com/x"), "mail and ftp are not pages");
    CHECK(!ou_is_page("javascript:alert(1)") && !ou_is_page("steam://run/1"), "javascript: and other schemes are not");
    CHECK(!ou_is_page("https://") && !ou_is_page("https:///path") && !ou_is_page("https://?q"), "no host");
    CHECK(!ou_is_page("https://user:pass@example.com/") && !ou_is_page("https://user@example.com"), "a user name or password before the host");
    CHECK(ou_is_page("https://example.com/a@b"), "an @ after the host is part of the path");
    CHECK(!ou_is_page("https://exa mple.com") && !ou_is_page("https://example.com/\x01") && !ou_is_page("https://example.com/\xc3\xa9"),
          "a space, a control character or a byte above 0x7e");
    CHECK(!ou_is_page("https://:443/") && !ou_is_page("https://./"), "a host that starts with ':' or '.'");
    big[OU_MAX_URL] = 0;
    CHECK(ou_is_page(big), "2048 characters pass");
    big[OU_MAX_URL] = 'a'; big[OU_MAX_URL + 1] = 0;
    CHECK(!ou_is_page(big), "2049 do not");
    CHECK(!ou_is_page("") && !ou_is_page(NULL), "nothing");

    /* the host for the log, never the rest (a sign-in code) */
    ou_host("https://www.epicgames.com/activate?userCode=ABCD1234", host, sizeof host);
    CHECK(!strcmp(host, "www.epicgames.com"), "the host of Epic's page");
    ou_host("http://example.com:8080/x", host, sizeof host);
    CHECK(!strcmp(host, "example.com"), "a port is not part of the host");
    ou_host("https://example.com?x", host, sizeof host);
    CHECK(!strcmp(host, "example.com"), "a query right after the host");
    ou_host("https://abcdefghijklmnopqrstuvwxyz.example.com/", host, 8);
    CHECK(!strcmp(host, "abcdefg"), "cut to the room given");

    /* the file name: oldest first by name */
    CHECK(ou_file_name(name, sizeof name, 1234, 56) && !strcmp(name, "00000000000000001234-56.url"), "<ticks, 20 digits>-<pid>.url");
    {
        char a[64], b[64];
        ou_file_name(a, sizeof a, 999, 1);
        ou_file_name(b, sizeof b, 1000, 1);
        CHECK(strcmp(a, b) < 0, "an earlier request sorts first");
    }
    CHECK(!ou_file_name(name, 8, 1, 1), "no room: no name");

    /* the switch */
    CHECK(ou_off("0") && !ou_off(NULL) && !ou_off("1") && !ou_off("") && !ou_off("00"), "only MADEIRA_OPEN_URL=0 turns it off");

    printf("%d checks, %d failures\n", checks, failures);
    return failures != 0;
}
