# Color profiles

Open **Settings → Appearance** to switch between BCD Violet, Ocean, Forest,
Ember, Rose and Graphite. BCD Violet preserves the original palette. Changes
apply immediately to the shared UI, including settings, the library, dialogs and
in-game panels, and survive restarting the app. Game artwork, the BCD logo,
controller face-button colors and explicit touch-control overrides keep their
own colors. These are dark palettes; Appearance does not alter game rendering
or HDR calibration.

**Main color** creates a matching working palette named **My colors**. BCD
adjusts action fills for contrast while keeping the selected color as the seed.
**Save as a new profile** keeps a named copy so several combinations can be
switched from the Profile menu. Editing a built-in or saved profile creates a
working copy; it does not overwrite the source. Delete removes only the selected
saved profile or working copy. **Use BCD Violet** restores the original look.

**Export this profile** writes a JSON file through Files. **Import a profile**
validates and adds a separate copy before selecting it. A bad file leaves the
current colors untouched. Importing an existing ID never overwrites a preset.
Profiles and the selection are stored locally in `bcd.appearance.v1` in the
app's preferences; nothing is written to a game's config or synced to an account.

## JSON format

The six bundled profiles live in
[`app/Madeira/BCDThemes.json`](../app/Madeira/BCDThemes.json), an array of
profiles. An exported or imported file contains **one profile object**, not the
catalog array. Export a profile to start editing. For example:

```json
{
  "version": 1,
  "id": "my-violet",
  "name": "My Violet",
  "colors": {
    "ground": "#0B0816",
    "groundTop": "#1A0F33",
    "surface": "#1A1430",
    "surfaceRaised": "#231B3F",
    "stroke": "#2F2350",
    "text": "#F4F1FF",
    "textSoft": "#B7AECF",
    "accent": "#7C3AED",
    "accentSecondary": "#D946EF",
    "accentSoft": "#C9B7FF",
    "success": "#34D399",
    "warning": "#FFC24A",
    "danger": "#FF7588",
    "panel": "#100C1F",
    "onAccent": "#FFFFFF"
  }
}
```

All listed roles are required, with opaque `#RRGGBB` colors. The optional `seed`
stores the original main color of a generated palette. Version 1 files are
limited to 64 KB, with names of 1–60 characters. IDs are 1–80 letters, numbers,
hyphens, underscores or braces. Text, secondary text and soft accents must reach a
4.5:1 contrast ratio against the five main surfaces. Action text must reach 3:1
against both gradient endpoints (matching the original BCD palette); generated
action fills target at least 4.5:1. Import reports the offending role when a
color or contrast check fails.

`BCDStyle` retains the existing color names as computed aliases into the
observable store. Views automatically track the selected palette without
changing their identity or rebuilding the game session. New UI should use
these shared roles instead of retaining colors in static constants.

## Validation

```sh
python3 tests/host/check-bcd-themes.py
python3 tests/host/check-bcd-settings.py --no-preview
python3 tests/host/check-ui-preview-cache.py
python3 tools/ui-preview.py --screens settings.appearance,home,library --devices 'iPhone 17 Pro Max'
```

The preview app also accepts `-BCDTheme ocean` (or another profile ID). The
profile selector uses Settings' standard D-pad navigation. The color picker,
name entry and file chooser use iOS controls.
