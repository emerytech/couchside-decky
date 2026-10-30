# Optional TV appearance packs (unreleased)

The companion advertises `appearance: 1` in themes-capability.json. The matching
agent adds `appearance_ready`, active `settings.appearance`, and at most eight
`settings.appearance_packs`. No pack means the existing render tree and styling.
Phone Apply stores a validated pack; this companion reads the same fixed settings
file. The TV picker previews saved packs, cancels drafts and restores layout colors.
Steam default continues to disable the custom home without deleting saved packs.

Version 1 packs contain a name, six hex colors (background, surface, text, muted,
accent, focus) and optional home/library/settings gradients (integer 0–360 degree
angle, 2–4 hex stops). Root backend and renderer independently validate the data.
No supplied CSS/JS, URLs, file paths or external assets are executed or loaded.
Home gradient covers the launcher; library gradient covers its game rows, not
Steam's native Library route; settings gradient covers the Couchside theme picker.

A new companion with an old agent sends no appearance patch until packs exist.
Default installations retain original styling. Once packs are stored, downgrading
to an older strict-schema companion can fall back to Steam; deploy the matching
agent and companion together for pack users. No bundled agent release was changed.

Validated locally: build, eight backend tests, renderer/hub/navigation/layout
regressions, pack rendering test. Browser harness exercised all seven layouts,
phone import and Apply, TV Cancel/default restore, and persistence across the local
agent process restart. Browser uses sample games and Decky shims, not real Steam.
Hardware checked on 2026-10-30: SteamOS 3.8.16 with Decky 3.3.0 and Bazzite 44
with a temporary official Decky 3.3.1 loader. Native captures verified Forest and
Harbor on both machines, all seven layouts on Bazzite, native picker preview,
Cancel, Apply, original-color restoration, and return to Steam. Agent restarts
retained both packs. D-pad/A inputs were sent through the real authenticated
Couchside gamepad connection; this is not a physical-phone usability sign-off.

Two native picker issues were fixed: toggling layout defaults no longer remounts
the content (retaining scroll/focus), and vertical pack choices now declare column
navigation so Down reaches the next visible pack. Both fixes were rechecked on
both machines. Installations with no saved packs retain the original tree.

Bazzite's pre-existing Decky 3.2.7-pre1 crashed before plugins loaded on its current
Steam client. The temporary test used the official 3.3.1 binary, verified against
its published SHA-256, and the SELinux bin_t label required by Bazzite's own Decky
recipe. The original loader was restored afterward. A compatible loader is a
prerequisite for a future installation; appearance flags alone do not prove its
frontend is healthy. Same-version test JS replacement also required a frontend
reload, and preview assets must be included alongside dist/index.js.

Both machines were backed up and restored after testing; no game was launched.
No push, merge, release, or permanent device installation. Native phone import /
Apply and release packaging remain separate acceptance work.
