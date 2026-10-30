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
SteamOS device unreachable; Bazzite host pending from owner. Native focus and TV
screen-capture acceptance remain open. No push, release, device install or merge.
