# Couchside: Decky Loader plugin

Install and manage the [Couchside](https://couchside.tv) agent on your Steam Deck
or SteamOS / Bazzite box without leaving Game Mode. The plugin also shows the
phone pairing QR right in the Quick Access Menu.

Couchside turns your phone into your box's monitor, remote console, and game
controller ([App Store](https://apps.apple.com/app/id6786884115) ·
[Google Play](https://play.google.com/store/apps/details?id=com.ets3d.rescueremote)).
The box side is a tiny, dependency-free Python agent; this plugin installs and
supervises it for you.

## What it does

- One-tap install / update of the agent. It ships bundled with the plugin, so
  nothing is downloaded. The install writes it to `~/.local/opt/couchside`,
  enables `couchside.service`, and installs the scoped, `visudo`-validated
  sudoers rule plus the `/dev/uinput` udev rule the virtual gamepad needs.
  It is a faithful port of the box installer (`install.sh`), minus the terminal.
- Pairing QR in Game Mode: scan it with your phone. Host, port, and token are
  shown alongside it.
- Status: running state, agent version, port.
- Manage: restart the agent, regenerate the token, uninstall.

The plugin backend runs as root (`"flags": ["root"]` in `plugin.json`) so it can
place the systemd unit and sudoers rule. The agent itself runs as your desktop
user (`deck` / `bazzite`).

## Status

Tested on device and ready to submit. The structure, backend (`main.py`), and
panel (`src/index.tsx`) are in place; the bundled agent lives in `defaults/`.
On a Bazzite box running Decky Loader v3.2.6-pre1 the plugin loaded cleanly in
Game Mode: the panel detected the running agent, reported its status, and
rendered the pairing QR, which paired with both an Android phone and an iPhone.
Next step is submission to the Decky plugin store.

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
defaults/          bundled agent: couchsided.py + couchside.service
package.json        build config (@decky/rollup)
```

## Submitting to the Decky store

PR this repo as a submodule to
[`decky-plugin-database`](https://github.com/SteamDeckHomebrew/decky-plugin-database),
bumping `version` in `package.json`. The `LICENSE` (MIT) is included as required.

## License

MIT © 2026 Taylor Emery (ETS3D LLC). The bundled agent is MIT; see the main
[couchside](https://github.com/emerytech/couchside) repo.
