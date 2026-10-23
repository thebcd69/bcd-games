# Battle.net / World of Warcraft

This branch implements experimental account sign-in, library sync and native WoW downloads.
It targets official Retail, Classic and Classic Era. Device compatibility and successful
in-game authentication have not been verified. Keep the integration in draft until tested.

## Account and library

- Settings > Accounts opens Blizzard's own sign-in page in an ephemeral native WebKit sheet,
  including Blizzard's password and Authenticator flow. BCD does not store the password.
- The account portal session is validated against its games API before secure cookies are
  saved in the existing device-only Keychain (`bcd.battlenet.session`). Credentials and API
  replies are never logged. The last account name and client list are cached with the session
  in Keychain, so the library can appear while the next refresh is pending.
- A WoW licence exposes Retail, Classic and Classic Era as client choices. These rows do not
  assert an active subscription, expansion ownership, server availability or compatibility.
- Refresh runs on connection and when the library returns to the foreground, with a six-hour
  freshness interval. Network errors retain the last list. Expired sessions require sign-in.
- API requests are bounded and never follow redirects with a Cookie header. Sign-out clears
  the saved account, cancels refresh and pauses downloads. Installed clients remain available.
- Imported `_retail_/Wow.exe`, `_classic_/WowClassic.exe` and `_classic_era_/WowClassic.exe`
  retain their existing library IDs, settings and history. Managed downloads get Battle.net IDs.

The account UI uses `BCDBnetAccount`, separately from Steam and GOG. Public CDN downloads
never receive account cookies. WoW uses its own in-game login; portal sign-in is not game login.

## Native downloads

The Install action selects the region's current build through Blizzard's HTTPS v2 version
service, checks build/CDN configuration hashes, and reads encoding, install and download
manifests. The first implementation selects Windows x64 and enUS. It searches CDN archive
indexes for selected payloads, then fetches bounded archive ranges or loose files. Planning
can take time and transfer substantial index metadata before payload progress starts.

Downloads use the shared content queue, including disk checks, pause/resume, cancellation,
retry and a persistent completion journal. A saved build snapshot pins resumed downloads to
the same version. Every downloaded BLTE payload is checked against its encoding key and
frame hashes while streaming and again during assembly. Invalid formats fail explicitly.

Finishing writes CASC data archives and V7 local indexes, build/configuration files and the
selected client programs. Extracted programs are also checked against their content keys.
The finished folder is committed with an atomic move and registered in the library. A build
receipt allows retrying library registration if the app stops after that move. Uninstall
removes the managed client through the existing removal mechanism.

Payloads remain staged during assembly to allow cancellation and resume. The disk check
reserves roughly twice the encoded download size, plus extracted programs and a margin.
Temporary payloads are removed after registration. Client files are under
`C:\Battle.net Games\<product>`. Unrelated folders are never replaced to finish an install.

## Remaining work

- Keep running Swift protocol/engine tests and iOS type checks on Apple's toolchain after changes.
  These checks passed in GitHub Actions; Windows source checks alone cannot verify compilation.
- Test real sign-in, Authenticator/passkeys, session expiry and library refresh on a device.
- Test complete official downloads, launching and in-game login with a subscription account
  through the Wine/FEX/rendering path. Synthetic fixtures do not demonstrate WoW compatibility.
- Implement update/repair and selectable locales. Extracted files currently support plain and
  zlib BLTE frames; encrypted or other codecs fail explicitly. CASC payloads remain encoded.
- Support other Battle.net products and account regions including China. Cloud saves,
  friends/chat and achievements are not implemented.

## Checks and protocol references

Run `python3 tests/host/check-battlenet.py --require-swift` on macOS. Synthetic tests cover
cookie isolation, credential redaction, library caching and deduplication, manifest tag
selection, BLTE corruption/bounds, archive ranges, disk refusal, journal reuse, CASC assembly
and interrupted-registration recovery. By default they never contact Blizzard or execute a game binary.
The optional `--live-metadata` check also fetches and validates current public Classic Era
encoding/install/download manifests. It does not use an account or download game payloads;
its CI step is non-blocking because external service availability is outside the unit tests.
The Battle.net workflow also runs shared content/GOG regression tests, iOS adapter type checks
and shared interface/preview compilation. Full app builds and live device checks remain separate.

- [Blizzard OAuth samples](https://github.com/Blizzard/oauth-client-sample): public profile
  authentication is separate from installing and signing in to games.
- [Galaxy Battle.net backend](https://github.com/FriendsOfGalaxy/galaxy-integration-battlenet/blob/master/src/backend.py)
  and [library mapping](https://github.com/FriendsOfGalaxy/galaxy-integration-battlenet/blob/master/src/plugin.py):
  references for undocumented account portal endpoints and WoW title ID 5730135.
- [TACTSharp](https://github.com/wowdev/TACTSharp) and
  [cascette-py](https://github.com/wowemulation-dev/cascette-py): protocol references for
  public NGDP manifests, BLTE and local CASC layouts. The adapter is independently written.

The live Swift metadata check has validated current Classic Era encoding/install/download
manifests, their hashes, selected Windows client files and root content-key mappings. A separate
bounded probe confirmed the current CDN archive index page/footer layout. These checks have
not verified a complete installed game.
