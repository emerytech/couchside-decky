"""
Couchside Decky Loader plugin backend.

Runs as ROOT (see plugin.json "flags": ["root"]) so it can install the systemd
unit, the scoped sudoers rule, and the /dev/uinput udev rule without a terminal.
The Couchside *agent* itself still runs as the desktop user (deck / bazzite);
this backend just puts the files in place, chowns them, and enables the service.

This is a faithful port of the box installer (install.sh) MINUS the pieces a
Game-Mode plugin doesn't need: the Steam "Pair Phone" shortcut / shortcuts.vdf
hack and the terminal QR (the plugin panel shows the QR itself).

Frontend-callable methods:
    install()            -> {ok, error?}          full install / upgrade (idempotent)
    status()             -> {installed, running, port, agent_version?}
    get_pairing()        -> {ok, host, port, token, pair_url}
    restart_agent()      -> {ok, error?}
    regenerate_token()   -> {ok, token, error?}
    uninstall(purge)     -> {ok, error?}          purge=True also drops token+sudoers
    check_update()       -> {ok, current, latest?, update_available, error?}
    self_update()        -> {ok, updated?, version?, error?}  verified plugin self-update
"""

import hashlib
import json
import os
import pwd
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import urllib.request

try:
    import decky  # provided by Decky Loader at runtime
    log = decky.logger
except Exception:  # pragma: no cover - lets the file import outside Decky
    import logging
    log = logging.getLogger("couchside")

# ---- paths (mirror install.sh) -------------------------------------------
PORT_DEFAULT = 8787
ETC_DIR = "/etc/couchside"
TOKEN_FILE = f"{ETC_DIR}/token"
CONFIG_FILE = f"{ETC_DIR}/config.json"
# Fixed-arg, root-owned journal wrapper the sudoers rule grants (no wildcards);
# it validates its inputs so --file/--directory can't be injected. Mirrors
# install.sh. Lives in the root-owned ETC_DIR (user can execute, not modify).
JOURNAL_WRAPPER = f"{ETC_DIR}/couchside-journal"
SUDOERS_FILE = "/etc/sudoers.d/couchside"
UNIT_DST = "/etc/systemd/system/couchside.service"
UINPUT_UDEV = "/etc/udev/rules.d/99-couchside-uinput.rules"
UINPUT_MODLOAD = "/etc/modules-load.d/couchside-uinput.conf"
RTC_UDEV = "/etc/udev/rules.d/99-couchside-rtc.rules"
WOL_LINK = "/etc/systemd/network/50-couchside-wol.link"

# Pre-rename installs to retire: "etc_dir|unit|sudoers".
OLD_INSTALLS = [
    ("/etc/rescue-agent", "rescue-agent.service", "/etc/sudoers.d/rescue-agent"),
    ("/etc/couchpilot", "couchpilot.service", "/etc/sudoers.d/couchpilot"),
]

# ---- self-update ----------------------------------------------------------
# The plugin updates ITSELF from GitHub releases. Trust model matches
# install.sh: a release is only accepted if its SHA256SUMS is signed by the
# maintainer's OFFLINE Ed25519 key (below — same key install.sh embeds).
# Unsigned or tampered releases are rejected outright; there is deliberately
# NO checksum-only fallback here, because this code runs as root.
UPDATE_REPO = "emerytech/couchside-decky"
RELEASE_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
RELEASE_PUBKEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA+9aBnheHC7N3J9JNfkP2PoBf89SCkBxmqlZ/2lrcwGA=
-----END PUBLIC KEY-----
"""
# Tarball is ~2 MB; anything past this is wrong and gets cut off mid-stream.
UPDATE_MAX_BYTES = 30 * 1024 * 1024
UPDATE_TIMEOUT_S = 30

# Written to JOURNAL_WRAPPER (root:root 0755) at install. Kept byte-identical to
# install.sh's copy: a fixed-arg wrapper so the sudoers grant on THIS script
# (not journalctl) can't be used to read arbitrary files as root.
_JOURNAL_WRAPPER_SRC = """#!/usr/bin/env bash
# couchside-journal <unit> <lines>: read ONE system unit's journal, safely.
# The Couchside sudoers rule grants ONLY this script. It validates its inputs
# and calls journalctl with a fixed option set, so --file/--directory can never
# be injected (arbitrary-file read as root) the way a wildcard rule on
# journalctl itself would allow.
set -euo pipefail
unit="${1:-}"
lines="${2:-200}"
# Unit: a strict systemd unit name — no leading dash, slash, space, or option.
case "$unit" in
    ''|-*|*/*|*[[:space:]]*) echo "couchside-journal: invalid unit" >&2; exit 2 ;;
esac
case "$unit" in
    *.service|*.socket|*.target|*.timer|*.mount|*.scope|*.slice|*.path|*.device|*.swap|*.automount) : ;;
    *) echo "couchside-journal: invalid unit" >&2; exit 2 ;;
esac
# Lines: positive integer, clamped to 1..2000.
case "$lines" in ''|*[!0-9]*) lines=200 ;; esac
if [ "$lines" -lt 1 ]; then lines=1; fi
if [ "$lines" -gt 2000 ]; then lines=2000; fi
exec journalctl -u "$unit" -n "$lines" --no-pager -o short-iso
"""


def _plugin_dir() -> str:
    return os.environ.get("DECKY_PLUGIN_DIR", os.path.dirname(os.path.abspath(__file__)))


def _daemon_version(path: str):
    """Parse the top-level `VERSION = "x.y.z"` string from a couchsided.py file.
    Returns the version string, or None if the file is missing/unreadable or has
    no recognizable VERSION line. Used for the no-downgrade check at install."""
    import re
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r'''\s*VERSION\s*=\s*["']([^"']+)["']''', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def _ver_tuple(v: str):
    """A comparable tuple from a dotted version like "2.8.1". Non-numeric trailers
    (e.g. "2.8.1-rc1") are dropped so the numeric core still compares sanely."""
    parts = []
    for chunk in str(v).split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _ver_gt(a: str, b: str) -> bool:
    """True if version a is strictly newer than version b."""
    return _ver_tuple(a) > _ver_tuple(b)


def _seat_owner() -> str:
    """The user who owns the active graphical seat, per loginctl. Empty string if
    it can't be determined unambiguously. This is the box's real desktop user and
    the only trustworthy signal on a multi-user box — we grant that user
    passwordless sudo + the input group, so we must not guess who it is."""
    try:
        p = _run(["loginctl", "list-sessions", "--no-legend"])
        if p.returncode != 0:
            return ""
        # Prefer an active graphical seat session; fall back to any session on a
        # seat. Collect the owning users so we can refuse to guess if ambiguous.
        active_seat, seat_users = "", set()
        for line in p.stdout.splitlines():
            sid = line.split()[0] if line.split() else ""
            if not sid:
                continue
            d = {}
            s = _run(["loginctl", "show-session", sid,
                      "-p", "Name", "-p", "Active", "-p", "Seat", "-p", "Type"])
            if s.returncode != 0:
                continue
            for kv in s.stdout.splitlines():
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    d[k] = v
            name, seat = d.get("Name", ""), d.get("Seat", "")
            if not name or not seat:  # no seat => not a graphical local session
                continue
            seat_users.add(name)
            if d.get("Active") == "yes" and d.get("Type") in ("x11", "wayland", "mir"):
                active_seat = name
        if active_seat:
            return active_seat
        # No clearly-active graphical session: only trust it if exactly one user
        # owns a seat; otherwise stay silent so the caller aborts rather than guess.
        if len(seat_users) == 1:
            return next(iter(seat_users))
        return ""
    except Exception:
        return ""


def _target_user() -> str:
    """The desktop user the agent runs as — the one we grant passwordless sudo and
    the input group. Resolve it from an authoritative source, never a guess:
      1. DECKY_USER if set and a real account.
      2. The owner of the active graphical seat (loginctl).
    The deck/bazzite fast-path is used ONLY when it agrees with one of those; on a
    multi-user box, picking an arbitrary /home user would grant privesc to the
    wrong account, so if the source is ambiguous we raise instead of guessing."""
    def _valid(name: str) -> bool:
        if not name:
            return False
        try:
            pwd.getpwnam(name)
            return True
        except KeyError:
            return False

    u = os.environ.get("DECKY_USER")
    if _valid(u):
        return u

    seat = _seat_owner()
    if _valid(seat):
        # deck/bazzite fast-path only if it matches the real seat owner.
        return seat

    raise RuntimeError(
        "couchside: cannot determine the desktop user (DECKY_USER unset and no "
        "unambiguous active graphical seat from loginctl). Refusing to guess — "
        "set DECKY_USER to the intended account and retry, so passwordless sudo "
        "and the input group are never granted to the wrong user.")


def _run(cmd, check=False):
    """Run cmd and return the CompletedProcess. check=True raises RuntimeError
    on nonzero exit; otherwise never raises, so callers can probe .returncode
    directly."""
    log.info("couchside: run %s", " ".join(cmd))
    # Decky's plugin loader is a PyInstaller bundle: it points LD_LIBRARY_PATH at
    # its own bundled libs (an older libcrypto), and we inherit that. A system
    # binary like systemctl then links the wrong libcrypto and dies before it
    # does anything ("OPENSSL_3.4.0 not found"). Hand the child the pre-bundle
    # library path (LD_LIBRARY_PATH_ORIG if PyInstaller saved one, else drop the
    # var) so it loads the OS libraries instead.
    env = dict(os.environ)
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}): {p.stderr.strip()}")
    return p


def _chown(path, uid, gid, recursive=False):
    os.chown(path, uid, gid)
    if recursive and os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                # follow_symlinks=False so a dangling link chowns the link
                # itself instead of chasing a missing target; skip anything
                # that vanished between the walk and the chown.
                try:
                    os.chown(os.path.join(root, name), uid, gid,
                             follow_symlinks=False)
                except FileNotFoundError:
                    pass


def _uinput_ready() -> bool:
    """Whether /dev/uinput exists and is writable by the TARGET user. This
    backend runs as root (os.access would always say yes), so decide from the
    node's owner/group/mode against the user's uid + group membership."""
    try:
        import grp, stat
        st = os.stat("/dev/uinput")
        user = _target_user()
        pw = pwd.getpwnam(user)
        # gather the user's group ids (primary + supplementary)
        gids = {pw.pw_gid}
        for g in grp.getgrall():
            if user in g.gr_mem:
                gids.add(g.gr_gid)
        mode = st.st_mode
        if st.st_uid == pw.pw_uid and (mode & stat.S_IWUSR):
            return True
        if st.st_gid in gids and (mode & stat.S_IWGRP):
            return True
        if mode & stat.S_IWOTH:
            return True
        return False
    except Exception:
        return False


def _read_port() -> int:
    try:
        with open(CONFIG_FILE) as f:
            return int(json.load(f).get("port") or PORT_DEFAULT)
    except Exception:
        return PORT_DEFAULT


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # TEST-NET-1 (RFC 5737): unroutable on purpose, so this only picks the
        # outbound interface; no packet is actually sent.
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        s.close()
        return "" if ip.startswith("127.") else ip
    except OSError:
        return ""


def _hostname_short() -> str:
    for p in ("/proc/sys/kernel/hostname", "/etc/hostname"):
        try:
            with open(p) as f:
                return f.read().strip().split(".")[0]
        except Exception:
            continue
    return "localhost"


def _gen_config(have_sddm: bool, have_kodi: bool) -> dict:
    units = []
    if have_sddm:
        units.append({"name": "sddm.service", "scope": "system"})
    units.append({"name": "couchside.service", "scope": "system"})
    actions, order = {}, []
    if have_sddm:
        actions["restart-session"] = {
            "label": "Restart Session",
            "description": "Restart the display session (sddm) to fix a wedged or black screen",
            "danger": "high",
            "cmd": ["sudo", "systemctl", "restart", "sddm"],
            "user_env": False, "detached": False,
        }
        order.append("restart-session")
    if have_kodi:
        actions["stop-kodi"] = {
            "label": "Stop Kodi",
            "description": "Stop the Kodi flatpak; relaunch it from Game Mode",
            "danger": "medium",
            "cmd": ["flatpak", "kill", "tv.kodi.Kodi"],
            "user_env": True, "detached": False,
        }
        order.append("stop-kodi")
    actions["reboot"] = {"label": "Reboot", "description": "Reboot the box", "danger": "high",
                         "cmd": ["sudo", "systemctl", "reboot"], "user_env": False, "detached": True}
    actions["poweroff"] = {"label": "Power Off", "description": "Power off the box", "danger": "high",
                           "cmd": ["sudo", "systemctl", "poweroff"], "user_env": False, "detached": True}
    order += ["reboot", "poweroff"]
    return {"units": units, "actions": actions, "action_order": order}


def _plugin_version() -> str:
    """This plugin's own version, from its package.json. "0" when unreadable
    (compares older than any real tag, so an unreadable state can update)."""
    try:
        with open(os.path.join(_plugin_dir(), "package.json")) as f:
            return str(json.load(f).get("version") or "0")
    except Exception:
        return "0"


def _http_get(url, out_path=None):
    """GET url with a UA (GitHub API requires one) and a hard timeout.
    With out_path: stream to that file, enforcing UPDATE_MAX_BYTES, return the
    byte count. Without: return the decoded JSON body."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "couchside-decky-selfupdate",
        "Accept": "application/octet-stream" if out_path else "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT_S) as r:
        if out_path is None:
            return json.loads(r.read(UPDATE_MAX_BYTES).decode())
        total = 0
        with open(out_path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    return total
                total += len(chunk)
                if total > UPDATE_MAX_BYTES:
                    raise ValueError(f"download exceeds {UPDATE_MAX_BYTES} bytes: {url}")
                f.write(chunk)


def _fetch_latest_release():
    """Latest release descriptor: (version, {asset_name: download_url}).
    Raises on network/shape errors."""
    rel = _http_get(RELEASE_API)
    tag = str(rel.get("tag_name") or "")
    version = tag.lstrip("v")
    if not version:
        raise ValueError("release has no tag_name")
    assets = {}
    for a in rel.get("assets") or []:
        name, url = a.get("name"), a.get("browser_download_url")
        if name and url:
            assets[name] = url
    return version, assets


def _verify_release(staging, tarball, sums, sig):
    """Reject the download unless (1) SHA256SUMS' Ed25519 signature checks out
    against the embedded maintainer pubkey and (2) the tarball's sha256 matches
    its SHA256SUMS entry. Raises with a precise reason on any failure."""
    # (1) signature FIRST: an attacker who controls the download can write any
    # SHA256SUMS they like; only the offline-key signature makes it meaningful.
    pub = os.path.join(staging, "release-pub.pem")
    with open(pub, "w") as f:
        f.write(RELEASE_PUBKEY_PEM)
    p = _run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", pub,
              "-rawin", "-in", sums, "-sigfile", sig])
    if p.returncode != 0:
        raise ValueError("release signature verification FAILED: %s"
                         % (p.stderr.strip() or p.stdout.strip() or "openssl error"))
    # (2) tarball hash against the now-trusted SHA256SUMS
    h = hashlib.sha256()
    with open(tarball, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    digest = h.hexdigest()
    want = None
    with open(sums) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == "Couchside.tar.gz":
                want = parts[0].lower()
    if not want:
        raise ValueError("SHA256SUMS has no entry for Couchside.tar.gz")
    if digest != want:
        raise ValueError(f"tarball sha256 mismatch: got {digest}, signed {want}")


def _extract_tarball(tarball, dest):
    """Extract to dest with the stdlib 'data' filter (blocks abs paths, ..,
    links out of tree, setuid — everything a hostile tar could try). Falls back
    to a manual member check on Pythons without extraction filters."""
    with tarfile.open(tarball) as t:
        try:
            t.extractall(dest, filter="data")
        except TypeError:  # < 3.12: no filter kwarg
            base = os.path.realpath(dest)
            for m in t.getmembers():
                p = os.path.realpath(os.path.join(dest, m.name))
                if not p.startswith(base + os.sep):
                    raise ValueError(f"tar member escapes destination: {m.name}")
                if m.islnk() or m.issym():
                    raise ValueError(f"tar member is a link: {m.name}")
            t.extractall(dest)


class Plugin:
    # ---- lifecycle -------------------------------------------------------
    async def _main(self):
        # _target_user() now RAISES rather than guess on an ambiguous box, so keep
        # it out of the load path — a log line must never fail the plugin load.
        try:
            who = _target_user()
        except Exception as e:
            who = f"<unresolved: {e}>"
        log.info("Couchside plugin loaded (target user: %s)", who)

    async def _unload(self):
        log.info("Couchside plugin unloaded")

    # ---- install / upgrade ----------------------------------------------
    async def install(self):
        try:
            self._do_install()
            return {"ok": True}
        except Exception as e:
            log.exception("Couchside install failed")
            return {"ok": False, "error": str(e)}

    def _do_install(self):
        user = _target_user()
        pw = pwd.getpwnam(user)
        uid, gid, home = pw.pw_uid, pw.pw_gid, pw.pw_dir
        pdir = _plugin_dir()
        src_daemon = os.path.join(pdir, "defaults", "couchsided.py")
        src_unit = os.path.join(pdir, "defaults", "couchside.service")
        for f in (src_daemon, src_unit):
            if not os.path.isfile(f):
                raise FileNotFoundError(f"bundled agent file missing: {f}")

        # sanity: the bundled daemon compiles
        _run(["python3", "-m", "py_compile", src_daemon], check=True)

        # (c) daemon -> ~/.local/opt/couchside
        # Install the VENDORED, release-reviewed defaults/couchsided.py that ships
        # in this plugin tarball. We deliberately do NOT fetch the agent live from
        # GitHub main at install time: this backend runs as ROOT, and a live fetch
        # would have root execute whatever happens to be on main at that moment —
        # unreviewed, unpinned code. The release automation refreshes this vendored
        # copy on every plugin release, so it is always the current reviewed agent
        # and a live fetch is both unnecessary and unsafe.
        #
        # Preserve the original no-downgrade intent by version, not by network:
        # compare the vendored VERSION against any already-installed agent and
        # install the vendored one only when it is newer-or-equal. Never downgrade
        # a box whose installed agent is somehow newer than the vendored copy.
        install_dir = os.path.join(home, ".local", "opt", "couchside")
        os.makedirs(install_dir, exist_ok=True)
        dst_daemon = os.path.join(install_dir, "couchsided.py")
        vendored_ver = _daemon_version(src_daemon)
        installed_ver = _daemon_version(dst_daemon) if os.path.exists(dst_daemon) else None
        # Install the vendored copy unless a strictly-newer agent is already there.
        # Unknown installed version (can't parse) => treat as older and (re)install.
        if installed_ver is not None and _ver_gt(installed_ver, vendored_ver):
            log.info("couchside: keeping installed agent %s (newer than vendored %s)",
                     installed_ver, vendored_ver)
        else:
            shutil.copyfile(src_daemon, dst_daemon)
        os.chmod(dst_daemon, 0o755)
        # Aerial-screensaver player (optional: only bundled in plugin >= 0.2.10).
        # The agent's /api/screensaver launches it through a Steam shortcut.
        src_saver = os.path.join(pdir, "defaults", "couchside-screensaver.sh")
        if os.path.isfile(src_saver):
            dst_saver = os.path.join(install_dir, "couchside-screensaver.sh")
            shutil.copyfile(src_saver, dst_saver)
            os.chmod(dst_saver, 0o755)
        # Only fix ownership of what we just created. Chowning all of ~/.local
        # would recurse into the user's Steam library (tens of GB) and blow up
        # on the broken symlinks in old steam-runtime trees. makedirs may have
        # created ~/.local and ~/.local/opt as root, so touch those two nodes
        # too, but never their subtrees.
        _chown(install_dir, uid, gid, recursive=True)
        for parent in (os.path.join(home, ".local", "opt"),
                       os.path.join(home, ".local")):
            try:
                os.chown(parent, uid, gid)
            except OSError:
                pass

        # (d) token
        os.makedirs(ETC_DIR, exist_ok=True)
        token = None
        if os.path.exists(TOKEN_FILE) and os.path.getsize(TOKEN_FILE) > 0:
            with open(TOKEN_FILE) as f:
                token = f.read().strip()
        else:
            for old_etc, _, _ in OLD_INSTALLS:  # migrate an old token if present
                op = os.path.join(old_etc, "token")
                if os.path.exists(op) and os.path.getsize(op) > 0:
                    with open(op) as f:
                        token = f.read().strip()
            if not token:
                token = secrets.token_hex(24)
            with open(TOKEN_FILE, "w") as f:
                f.write(token + "\n")
        os.chmod(TOKEN_FILE, 0o600)
        os.chown(TOKEN_FILE, uid, gid)

        # (e) config.json (only if absent)
        if not (os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 0):
            have_sddm = _run(["systemctl", "cat", "sddm.service"]).returncode == 0
            # Per-user flatpaks are invisible to root, so probe as the DESKTOP USER
            # (matches install.sh, which runs `flatpak info` as the invoking user).
            have_kodi = (shutil.which("flatpak") is not None
                         and _run(["sudo", "-u", user, "flatpak", "info", "tv.kodi.Kodi"]).returncode == 0)
            with open(CONFIG_FILE, "w") as f:
                json.dump(_gen_config(have_sddm, have_kodi), f, indent=2)
            os.chmod(CONFIG_FILE, 0o644)
            # root-owned so the user-run agent can read its allowed actions but
            # not rewrite which privileged commands it is permitted to run.
            os.chown(CONFIG_FILE, 0, 0)

        # (f0) Fixed-arg journal wrapper the sudoers rule grants. Root-owned
        # (0755) in the root-owned ETC_DIR so the desktop user can execute but
        # never modify it (a modifiable target would be root-code injection).
        with open(JOURNAL_WRAPPER, "w") as f:
            f.write(_JOURNAL_WRAPPER_SRC)
        os.chmod(JOURNAL_WRAPPER, 0o755)
        os.chown(JOURNAL_WRAPPER, 0, 0)

        # (f) sudoers rule, validated with visudo before install
        sudoers = (
            f"# couchside: passwordless sudo for EXACTLY the agent's privileged commands.\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart sddm\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl reboot\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl suspend\n"
            # Grant the wrapper, never journalctl itself — the only way to block
            # --file/--directory injection a wildcard journalctl rule would allow.
            f"{user} ALL=(root) NOPASSWD: {JOURNAL_WRAPPER}\n"
        )
        tmp_sudoers = os.path.join(pdir, ".couchside-sudoers.tmp")
        with open(tmp_sudoers, "w") as f:
            f.write(sudoers)
        _run(["visudo", "-cf", tmp_sudoers], check=True)
        shutil.copyfile(tmp_sudoers, SUDOERS_FILE)
        os.chmod(SUDOERS_FILE, 0o440)
        os.chown(SUDOERS_FILE, 0, 0)
        os.remove(tmp_sudoers)

        # (f2) /dev/uinput access for the virtual gamepad
        os.makedirs("/etc/udev/rules.d", exist_ok=True)
        with open(UINPUT_UDEV, "w") as f:
            f.write('KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", '
                    'OPTIONS+="static_node=uinput"\n')
        with open(UINPUT_MODLOAD, "w") as f:
            f.write("uinput\n")
        _run(["modprobe", "uinput"])
        _run(["usermod", "-aG", "input", user])
        _run(["udevadm", "control", "--reload-rules"])
        _run(["udevadm", "trigger", "--name-match=uinput"])

        # (f3) /dev/rtc0 access for scheduled wake (RTC alarm). The agent is already
        # in group 'input' (added above), so this grant needs no sudoers change.
        with open(RTC_UDEV, "w") as f:
            f.write('KERNEL=="rtc0", SUBSYSTEM=="rtc", GROUP="input", MODE="0660"\n')
        _run(["udevadm", "control", "--reload-rules"])
        _run(["udevadm", "trigger", "--subsystem-match=rtc", "--action=change"])

        # (g) systemd unit (render __USER__/__UID__)
        with open(src_unit) as f:
            unit = f.read().replace("__USER__", user).replace("__UID__", str(uid))
        with open(UNIT_DST, "w") as f:
            f.write(unit)
        os.chmod(UNIT_DST, 0o644)
        os.chown(UNIT_DST, 0, 0)
        _run(["systemctl", "daemon-reload"], check=True)
        _run(["systemctl", "enable", "couchside.service"], check=True)
        _run(["systemctl", "restart", "couchside.service"], check=True)

        # (h) firewall (Bazzite/Fedora has firewalld; SteamOS usually none)
        if shutil.which("firewall-cmd") and _run(["firewall-cmd", "--state"]).returncode == 0:
            port = _read_port()
            _run(["firewall-cmd", f"--add-port={port}/tcp", "--permanent"])
            _run(["firewall-cmd", "--reload"])

        # (i) retire pre-rename installs so they don't fight for the port/uinput
        for old_etc, old_unit, old_sudoers in OLD_INSTALLS:
            unit_path = f"/etc/systemd/system/{old_unit}"
            if os.path.exists(unit_path):
                _run(["systemctl", "disable", "--now", old_unit])
                try:
                    os.remove(unit_path)
                except OSError:
                    pass
                _run(["systemctl", "daemon-reload"])
            if os.path.exists(old_sudoers):
                try:
                    os.remove(old_sudoers)
                except OSError:
                    pass

    # ---- status ----------------------------------------------------------
    async def status(self):
        installed = os.path.exists(UNIT_DST)
        running = _run(["systemctl", "is-active", "--quiet", "couchside.service"]).returncode == 0
        port = _read_port()
        version = None
        if running:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
                    version = json.loads(r.read().decode()).get("version")
            except Exception:
                pass
        return {"installed": installed, "running": running, "port": port,
                "agent_version": version, "uinput_ready": _uinput_ready()}

    # ---- pairing (for the QR) -------------------------------------------
    async def get_pairing(self):
        if not (os.path.exists(TOKEN_FILE) and os.path.getsize(TOKEN_FILE) > 0):
            return {"ok": False, "error": "not installed yet"}
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        port = _read_port()
        host = f"{_hostname_short()}.local"
        # HTTPS relaunch link; token rides the #fragment so it never hits the web server.
        pair_url = f"https://couchside.tv/pair#host={host}&port={port}&token={token}"
        # Ship the raw LAN IP too: the .local host is mDNS and may not resolve
        # where mDNS is blocked, so the phone can fall back to &ip=. Keep both.
        ip = _lan_ip()
        if ip:
            pair_url += f"&ip={ip}"
        return {"ok": True, "host": host, "port": port, "token": token, "pair_url": pair_url}

    # ---- actions ---------------------------------------------------------
    async def restart_agent(self):
        p = _run(["systemctl", "restart", "couchside.service"])
        return {"ok": p.returncode == 0, "error": p.stderr.strip() or None}

    async def regenerate_token(self):
        try:
            user = _target_user()
            pw = pwd.getpwnam(user)
            token = secrets.token_hex(24)
            os.makedirs(ETC_DIR, exist_ok=True)
            with open(TOKEN_FILE, "w") as f:
                f.write(token + "\n")
            os.chmod(TOKEN_FILE, 0o600)
            os.chown(TOKEN_FILE, pw.pw_uid, pw.pw_gid)
            _run(["systemctl", "restart", "couchside.service"])
            return {"ok": True, "token": token}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def uninstall(self, purge=False):
        try:
            user = _target_user()
            home = pwd.getpwnam(user).pw_dir
            _run(["systemctl", "disable", "--now", "couchside.service"])
            for p in (UNIT_DST, UINPUT_UDEV, UINPUT_MODLOAD, RTC_UDEV, JOURNAL_WRAPPER):
                if os.path.exists(p):
                    os.remove(p)
            _run(["systemctl", "daemon-reload"])
            shutil.rmtree(os.path.join(home, ".local", "opt", "couchside"), ignore_errors=True)

            # Mirror the install's system-level side effects so an uninstall
            # leaves nothing behind. All best-effort / idempotent: a missing
            # file or a tool that isn't present must never abort the uninstall.
            # (a) firewalld port we opened
            if shutil.which("firewall-cmd") and _run(["firewall-cmd", "--state"]).returncode == 0:
                port = _read_port()
                _run(["firewall-cmd", f"--remove-port={port}/tcp", "--permanent"])
                _run(["firewall-cmd", "--reload"])
            # (b) Wake-on-LAN .link, if the box installer armed one
            if os.path.exists(WOL_LINK):
                try:
                    os.remove(WOL_LINK)
                except OSError:
                    pass
            # (c) drop the agent user from the 'input' group we added it to
            if shutil.which("gpasswd"):
                _run(["gpasswd", "-d", user, "input"])
            elif shutil.which("usermod"):
                # rebuild the supplementary list without 'input'
                try:
                    import grp
                    keep = [g.gr_name for g in grp.getgrall()
                            if user in g.gr_mem and g.gr_name != "input"]
                    _run(["usermod", "-G", ",".join(keep), user])
                except Exception:
                    pass

            # Retire any pre-rename installs (rescue-agent / couchpilot) too, so a
            # purge is a clean slate. Units always; sudoers only on purge, matching
            # the current-install sudoers being purge-gated below.
            for old_etc, old_unit, old_sudoers in OLD_INSTALLS:
                unit_path = f"/etc/systemd/system/{old_unit}"
                if os.path.exists(unit_path):
                    _run(["systemctl", "disable", "--now", old_unit])
                    try:
                        os.remove(unit_path)
                    except OSError:
                        pass
                    _run(["systemctl", "daemon-reload"])
                if purge:
                    shutil.rmtree(old_etc, ignore_errors=True)
                    if os.path.exists(old_sudoers):
                        try:
                            os.remove(old_sudoers)
                        except OSError:
                            pass

            if purge:
                shutil.rmtree(ETC_DIR, ignore_errors=True)
                if os.path.exists(SUDOERS_FILE):
                    os.remove(SUDOERS_FILE)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- self-update ------------------------------------------------------
    async def check_update(self):
        """Compare this plugin's version against the latest GitHub release.
        Read-only: no downloads beyond the release metadata."""
        current = _plugin_version()
        try:
            latest, _assets = _fetch_latest_release()
            return {"ok": True, "current": current, "latest": latest,
                    "update_available": _ver_gt(latest, current)}
        except Exception as e:
            log.warning("couchside: update check failed: %s", e)
            return {"ok": False, "current": current, "update_available": False,
                    "error": str(e)}

    async def self_update(self):
        """Replace this plugin (and its vendored agent) with the latest GitHub
        release, IF it is strictly newer and its signature verifies. On success
        a detached `systemctl restart plugin_loader` fires ~2s later so the
        reply reaches the UI before Decky reloads."""
        try:
            current = _plugin_version()
            latest, assets = _fetch_latest_release()
            if not _ver_gt(latest, current):
                return {"ok": True, "updated": False, "version": current}

            need = ("Couchside.tar.gz", "SHA256SUMS", "SHA256SUMS.sig")
            missing = [n for n in need if n not in assets]
            if missing:
                # No sig => no update. Root code refuses unsigned releases.
                raise ValueError("release v%s is missing asset(s): %s"
                                 % (latest, ", ".join(missing)))

            staging = tempfile.mkdtemp(prefix="couchside-update-")
            try:
                paths = {}
                for n in need:
                    paths[n] = os.path.join(staging, n)
                    _http_get(assets[n], out_path=paths[n])
                _verify_release(staging, paths["Couchside.tar.gz"],
                                paths["SHA256SUMS"], paths["SHA256SUMS.sig"])

                newroot = os.path.join(staging, "x")
                _extract_tarball(paths["Couchside.tar.gz"], newroot)
                newdir = os.path.join(newroot, "Couchside")
                # Sanity: shipped tree is what the tag says it is, and compiles.
                with open(os.path.join(newdir, "package.json")) as f:
                    shipped = str(json.load(f).get("version") or "")
                if shipped != latest:
                    raise ValueError(f"tarball version {shipped!r} != release v{latest}")
                _run(["python3", "-m", "py_compile",
                      os.path.join(newdir, "main.py"),
                      os.path.join(newdir, "defaults", "couchsided.py")], check=True)

                # Swap: current dir -> backup (kept for manual rollback until
                # the next update), verified new tree in, root-owned throughout —
                # Decky silently skips plugins that are not root-owned. The
                # backup must live OUTSIDE the plugins dir: Decky loads every
                # plugin.json-bearing folder in there, and a Couchside.bak
                # would come up as a duplicate plugin.
                pdir = _plugin_dir()
                bak = os.path.join(os.path.dirname(os.path.dirname(pdir)),
                                   "couchside-plugin.bak")
                shutil.rmtree(bak, ignore_errors=True)
                os.rename(pdir, bak)
                try:
                    shutil.move(newdir, pdir)
                    _chown(pdir, 0, 0, recursive=True)
                except Exception:
                    shutil.rmtree(pdir, ignore_errors=True)
                    os.rename(bak, pdir)  # roll back to the working install
                    raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)

            log.info("couchside: self-updated %s -> %s; restarting plugin_loader",
                     current, latest)
            # Detached + delayed so this reply lands before the loader (and this
            # process) restarts. Same LD_LIBRARY_PATH fix as _run: systemctl
            # must load the OS libcrypto, not Decky's bundled one.
            env = dict(os.environ)
            orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
            if orig is not None:
                env["LD_LIBRARY_PATH"] = orig
            else:
                env.pop("LD_LIBRARY_PATH", None)
            subprocess.Popen(["/bin/sh", "-c",
                              "sleep 2; systemctl restart plugin_loader"],
                             env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "updated": True, "version": latest}
        except Exception as e:
            log.exception("Couchside self-update failed")
            return {"ok": False, "error": str(e)}
