# Couchside: Decky Loader plugin

Install and manage the [Couchside](https://couchside.tv) service on your Steam Deck
or SteamOS / Bazzite box without leaving Game Mode. The plugin also shows the
phone pairing QR right in the Quick Access Menu.

Couchside turns your phone into your box's dashboard, remote console, and game
controller ([App Store](https://apps.apple.com/app/id6786884115) ·
[Google Play](https://play.google.com/store/apps/details?id=com.ets3d.rescueremote)).
The box side is a tiny, dependency-free Python service; this plugin installs and
supervises it for you.

## What it does

- One-tap install / update of the service. It ships bundled with the plugin, so
  nothing is downloaded. The install writes it to `~/.local/opt/couchside`,
  enables `couchside.service`, and installs the scoped, `visudo`-validated
  sudoers rule plus the `/dev/uinput` udev rule the virtual gamepad needs.
  It is a faithful port of the box installer (`install.sh`), minus the terminal.
- Pairing QR in Game Mode: scan it with your phone. Host, port, and token are
  shown alongside it.
- Status: running state, service version, port.
- Manage: restart the service, regenerate the token, uninstall.

The plugin backend runs as root (`"flags": ["root"]` in `plugin.json`) so it can
place the systemd unit and sudoers rule. The service itself runs as your desktop
user (`deck` / `bazzite`).

## Status

Released and in active use. The plugin ships signed releases (`SHA256SUMS.sig`,
verified against the maintainer's offline Ed25519 key) and can **update itself**
from GitHub, panel-side. The bundled Couchside service lives in `defaults/` and
is refreshed on load, so a Decky box tracks the same service version as an
`install.sh` box — when the plugin is present, `install.sh` stands down and lets
the plugin own the install rather than fighting it.

## Build

Requires Node ≥ 16.14 and pnpm v9.

```sh
pnpm install
pnpm run build     # -> dist/index.js
```

Copy the plugin folder (or symlink) into `~/homebrew/plugins/Couchside` on the
device to test, or use Decky's developer mode.

## Repo layout

```
plugin.json        Decky manifest (name, root flag, store metadata)
main.py            Python backend: install / status / pairing / restart / uninstall
src/index.tsx      Quick Access panel (React, @decky/ui)
defaults/          bundled service: couchsided.py + couchside.service
package.json        build config (@decky/rollup)
```

## Submitting to the Decky store

PR this repo as a submodule to
[`decky-plugin-database`](https://github.com/SteamDeckHomebrew/decky-plugin-database),
bumping `version` in `package.json`. The `LICENSE` (MIT) is included as required.

## License

MIT © 2026 Taylor Emery (ETS3D LLC). The bundled service is MIT; see the main
[couchside](https://github.com/emerytech/couchside) repo.
