# USB-C SSD games

Settings → Storage → External SSD → **Choose or reconnect an SSD folder**
opens the iOS Files folder picker. Select a games folder on the attached SSD.
BCD exposes its contents as **C:\External** without copying them to the phone.
Use **Add a game → External** to select a game's executable, or **Add every
game on drive C:** to scan the folder with the rest of the library.

Existing Windows files and C:\users saves stay on the phone. Games that save
beside their own executable write those saves to the SSD. Existing game folders
are not automatically moved: copy them to the SSD with Files and add their
external executables. Check the external copy before removing a local copy.
BCD's store downloads continue to install on the phone in this version.

Use a drive that appears under Locations in Files. Apple supports APFS and
exFAT, among other formats; some drives need a powered USB-C hub. See
[Apple's external storage instructions](https://support.apple.com/guide/iphone/iph95baac91f/ios).
Keep the SSD connected throughout play. Reconnect it and select the folder
again if iOS cannot restore access after a restart. Replacing the chosen folder
changes what C:\External refers to, including previously added external games.

## Implementation and validation

`ExternalGameStorage` keeps the picked URL's security scope open for native
Wine/FEX I/O and saves its bookmark in app preferences. An owned symlink in
the local Windows drive supplies a stable guest path. BCD refuses to replace
an existing user folder or an unowned symlink, checks write access before
changing the mapping, and rejects links escaping the permitted roots. It
does not change C: itself or relocate the Wine prefix.

Run `python3 tests/host/check-external-storage.py` on macOS for Foundation
checks covering data preservation, scope balance, containment, failed
reconnections and bookmark restoration. Windows skips these checks because
Apple's bookmark APIs and the iOS SDK are unavailable there.

Before release, build the iOS app and test a physical SSD: select a folder,
add and launch an x64 game and a batch file, relaunch BCD with the drive
attached, launch with the drive absent, reconnect it, and verify both normal
C:\users saves and games that save beside their executable. Also check a
read-only drive and an existing C:\External folder. Host checks cannot verify
iOS sandbox permission propagation to the native runtime or a drive unplugged
during play.
