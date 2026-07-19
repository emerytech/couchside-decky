"""
Couchside Decky Loader plugin backend.

Runs as ROOT (see plugin.json "flags": ["root"]) so it can install the systemd
unit, the scoped sudoers rule, and the /dev/uinput udev rule without a terminal.
The Couchside *service* itself still runs as the desktop user (deck / bazzite);
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
import re
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
# config.json lives in a USER-OWNED state dir, NOT the root-owned ETC_DIR.
# The agent runs as the desktop user and rewrites this file whenever the phone
# saves a setting — TV pairings, the guide-hold trigger. Kept in ETC_DIR it was
# unwritable, so every such save failed with a 500 "could not persist config:
# Permission denied" that the user could do nothing about. install.sh hit this
# exact bug and moved to /var/lib (see its own note at the migration step); this
# plugin never got the same fix, so Decky-only installs stayed broken.
#
# Root ownership was not buying protection here: nothing the agent exposes over
# HTTP writes `actions`, `units`, or `allow_app_*`. The only config writers are
# the TV-pairing endpoints and the guide setting. So it blocked legitimate saves
# while stopping nothing. The privileged surface is the SUDOERS file and the
# fixed-arg journal wrapper, which stay root-owned in ETC_DIR.
STATE_DIR = "/var/lib/couchside"
CONFIG_FILE = f"{STATE_DIR}/config.json"
# Where pre-fix Decky installs kept it; migrated on install and on plugin load.
LEGACY_CONFIG = f"{ETC_DIR}/config.json"
# Fixed-arg, root-owned journal wrapper the sudoers rule grants (no wildcards);
# it validates its inputs so --file/--directory can't be injected. Mirrors
# install.sh. Lives in the root-owned ETC_DIR (user can execute, not modify).
JOURNAL_WRAPPER = f"{ETC_DIR}/couchside-journal"
# zz- prefix is LOAD-BEARING: sudoers.d applies lexically and sudoers is
# last-match-wins — a "wheel" file ("(ALL) ALL", password) sorting after a
# plain "couchside" file silently shadowed every NOPASSWD grant on a real box.
# zz- makes our fixed-argument rules the winning (last) match.
SUDOERS_FILE = "/etc/sudoers.d/zz-couchside"
SUDOERS_FILE_LEGACY = "/etc/sudoers.d/couchside"
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
    """The desktop user the service runs as — the one we grant passwordless sudo and
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


def _render_unit(user: str, uid: int, exec_path: str) -> str:
    """Render defaults/couchside.service, which is SYNCED VERBATIM from
    couchside's agent/couchside.service — the same template install.sh uses.

    Keeping one template is the point: this plugin used to carry its own copy,
    and when install.sh moved config.json to a user-owned path (so the non-root
    agent could actually write it) the plugin's copy never learned about it.
    Decky-only boxes were left unable to save a single setting. Syncing the file
    means an install-layout change reaches Decky automatically instead of via
    someone noticing.

    Four placeholders, all of which MUST be substituted:
      __USER__   desktop user the service runs as
      __UID__    that user's uid (XDG_RUNTIME_DIR)
      __EXEC__   resolved daemon path. NOT /home/<user>/... — the home can live
                 at /var/home (Bazzite/ostree), be systemd-homed, or come from
                 LDAP, so the real path is injected rather than assumed.
      __CONFIG__ user-owned config path (see CONFIG_FILE)
    """
    pdir = _plugin_dir()
    with open(os.path.join(pdir, "defaults", "couchside.service")) as f:
        unit = f.read()
    unit = (unit.replace("__USER__", user)
                .replace("__UID__", str(uid))
                .replace("__EXEC__", exec_path)
                .replace("__CONFIG__", CONFIG_FILE))
    # Catch ANY remaining __PLACEHOLDER__ token, not just the four we know. The
    # template is synced from couchside main; if upstream adds a fifth field this
    # substitution does not fill, we must fail loudly here rather than write a
    # unit with a literal __NEW__ in its ExecStart and restart the box into it.
    leftover = re.findall(r"__[A-Z][A-Z0-9_]*__", unit)
    if leftover:
        raise RuntimeError("unit template has unsubstituted placeholders: %s"
                           % ", ".join(sorted(set(leftover))))
    return unit


def _execstart_has_config(unit_text: str) -> bool:
    """True when the unit's ExecStart actually passes --config.

    Deliberately inspects the ExecStart LINE, not the whole file: the template
    explains --config in a comment, so a naive `"--config" in text` would report
    True for a unit whose ExecStart lacks it and skip the repair.
    """
    for line in unit_text.splitlines():
        line = line.strip()
        if line.startswith("ExecStart") and "--config" in line:
            return True
    return False


def _migrate_legacy_config(uid: int, gid: int) -> bool:
    """Ensure a user-owned config.json at CONFIG_FILE, migrating the legacy
    root-owned one if that is all the box has. Idempotent; safe to call on every
    install and every plugin load.

    Three cases, in order:
      1. CONFIG_FILE already exists -> only repair ownership. NEVER clobber it
         with the legacy copy; on a box that also ran install.sh this is the
         live config and the legacy file is a stale leftover.
      2. Only the legacy file exists -> move it across, preserving pairings.
      3. Neither -> do nothing; the caller writes a fresh default.

    Returns True when something changed.
    """
    changed = False
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        # 0700 + user-owned, matching install.sh. The agent writes a temp file
        # into this DIRECTORY and os.replace()s it, so the directory itself must
        # be user-writable — a writable file in a root-owned dir is not enough.
        os.chmod(STATE_DIR, 0o700)
        os.chown(STATE_DIR, uid, gid)
    except OSError:
        log.exception("couchside: could not prepare %s", STATE_DIR)
        return False

    have_new = os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 0
    have_old = os.path.exists(LEGACY_CONFIG) and os.path.getsize(LEGACY_CONFIG) > 0
    if not have_new and have_old:
        try:
            shutil.move(LEGACY_CONFIG, CONFIG_FILE)
            log.info("couchside: migrated config %s -> %s (pairings preserved)",
                     LEGACY_CONFIG, CONFIG_FILE)
            changed = True
            have_new = True
        except OSError:
            log.exception("couchside: config migration failed")
    if have_new:
        # Repair ownership even when we did not move anything: a box installed by
        # an older plugin has a root-owned file the agent still cannot write.
        try:
            st = os.stat(CONFIG_FILE)
            if st.st_uid != uid or st.st_gid != gid:
                os.chown(CONFIG_FILE, uid, gid)
                changed = True
            if st.st_mode & 0o777 != 0o600:
                os.chmod(CONFIG_FILE, 0o600)
                changed = True
        except OSError:
            log.exception("couchside: could not fix config ownership")
    return changed


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
        # Reconcile the running service with this (possibly just-updated) plugin
        # bundle, and take over install.sh's dormant unit. Best-effort: never let
        # a reconcile error fail the plugin load.
        try:
            self._arm_on_load()
        except Exception:
            log.exception("Couchside on-load reconcile failed")

    async def _unload(self):
        log.info("Couchside plugin unloaded")

    def _arm_on_load(self):
        """Load-time reconciliation so the plugin OWNS the service with no manual
        click. Two idempotent, best-effort jobs:

          1. Propagate a bundle refresh. Decky replaces this plugin dir on update
             (self_update or the store) and reloads us, but never calls install().
             If the vendored service is newer than the copy on disk, install it +
             restart, so a plugin update becomes a service update automatically —
             no Re-install click, no box stuck on an old service.
          2. Take over install.sh's dormant unit. When Decky is present the box
             installer leaves couchside.service installed-but-disabled and hands
             it to us; enable + start it here so the handoff needs no user action.

        Only touches an ALREADY-installed unit (UNIT_DST present): a first-time
        install still goes through the explicit Install button / _do_install so
        the heavy one-time setup (sudoers, udev, firewall) never runs on a plain
        load. Same no-downgrade rule as _do_install — never replace a strictly
        newer installed service with an older vendored one.
        """
        if not os.path.exists(UNIT_DST):
            return  # nothing installed yet; leave first install to the button
        pdir = _plugin_dir()
        src_daemon = os.path.join(pdir, "defaults", "couchsided.py")
        if not os.path.isfile(src_daemon):
            return
        changed = False
        # (1) refresh the on-disk service when the vendored copy is strictly newer
        try:
            pw = pwd.getpwnam(_target_user())
            install_dir = os.path.join(pw.pw_dir, ".local", "opt", "couchside")
            dst_daemon = os.path.join(install_dir, "couchsided.py")
            vendored = _daemon_version(src_daemon)
            installed = _daemon_version(dst_daemon) if os.path.exists(dst_daemon) else None
            if vendored and (installed is None or _ver_gt(vendored, installed)):
                _run(["python3", "-m", "py_compile", src_daemon], check=True)
                os.makedirs(install_dir, exist_ok=True)
                shutil.copyfile(src_daemon, dst_daemon)
                os.chmod(dst_daemon, 0o755)
                _chown(install_dir, pw.pw_uid, pw.pw_gid, recursive=True)
                log.info("couchside: on-load service refresh %s -> %s", installed, vendored)
                changed = True
        except Exception:
            log.exception("couchside: on-load service refresh skipped")
        # (1b) repair the config path on boxes installed by a pre-fix plugin.
        # Those got config.json root-owned in ETC_DIR and a unit with no
        # --config, so the agent silently could not save anything (every TV
        # pairing 500'd). A plugin update alone must fix them: users do not know
        # to click Re-install, and the symptom looks like a broken app, not a
        # broken install.
        #
        # The unit is rewritten ONLY when it lacks --config. That is what makes
        # this safe next to install.sh: install.sh's unit already passes
        # --config, so it is left completely alone on a dual-install box.
        try:
            pw = pwd.getpwnam(_target_user())
            # Recomputed rather than reused from step (1): that block is wrapped
            # in its own try/except, so an early failure there would leave the
            # name unbound and silently skip this repair.
            daemon_path = os.path.join(pw.pw_dir, ".local", "opt", "couchside",
                                       "couchsided.py")
            if _migrate_legacy_config(pw.pw_uid, pw.pw_gid):
                changed = True
            with open(UNIT_DST) as f:
                live_unit = f.read()
            if not _execstart_has_config(live_unit):
                if os.path.isfile(os.path.join(pdir, "defaults", "couchside.service")):
                    unit = _render_unit(pw.pw_name, pw.pw_uid, daemon_path)
                    with open(UNIT_DST, "w") as f:
                        f.write(unit)
                    os.chmod(UNIT_DST, 0o644)
                    os.chown(UNIT_DST, 0, 0)
                    _run(["systemctl", "daemon-reload"], check=True)
                    log.info("couchside: repaired unit to pass --config %s",
                             CONFIG_FILE)
                    changed = True
        except Exception:
            log.exception("couchside: on-load config-path repair skipped")
        # (1b2) migrate the sudoers file to the zz- name. sudoers.d is lexical
        # and last-match-wins: a "wheel" file ("(ALL) ALL", password) sorting
        # after plain "couchside" shadowed EVERY grant on a real box while
        # `sudo -l` displayed them all. This plugin runs as root, so Decky
        # boxes heal on plugin update with no password. Never merge into an
        # existing zz- file here — if both exist, install.sh already wrote the
        # canonical zz- copy and the legacy file is just retired.
        try:
            if os.path.exists(SUDOERS_FILE_LEGACY):
                if os.path.exists(SUDOERS_FILE):
                    os.remove(SUDOERS_FILE_LEGACY)
                    log.info("couchside: removed shadowed legacy sudoers file")
                else:
                    os.replace(SUDOERS_FILE_LEGACY, SUDOERS_FILE)
                    log.info("couchside: sudoers migrated to zz-couchside "
                             "(ordering fix)")
                changed = True
        except Exception:
            log.exception("couchside: sudoers zz-migration skipped")
        # (1c) ensure the "Restart Decky" sudoers grant exists. Older installs
        # (either installer) predate it, and the recovery action only appears
        # when the grant is present — so a plugin update alone must add it.
        # Append-only with a contains-guard, and the RESULT is visudo-validated
        # before replacing the file: on a dual-install box this file may be
        # install.sh's, which must keep working verbatim.
        try:
            grant = ("%s ALL=(root) NOPASSWD: "
                     "/usr/bin/systemctl restart plugin_loader\n" % pw.pw_name)
            if os.path.exists(SUDOERS_FILE):
                with open(SUDOERS_FILE) as f:
                    cur = f.read()
                if "systemctl restart plugin_loader" not in cur:
                    tmp = SUDOERS_FILE + ".couchside-tmp"
                    with open(tmp, "w") as f:
                        f.write(cur.rstrip("\n") + "\n" + grant)
                    if _run(["visudo", "-cf", tmp]).returncode == 0:
                        os.chmod(tmp, 0o440)
                        os.chown(tmp, 0, 0)
                        os.replace(tmp, SUDOERS_FILE)
                        log.info("couchside: added Restart Decky sudoers grant")
                        changed = True
                    else:
                        os.remove(tmp)
                        log.error("couchside: grant append failed visudo check;"
                                  " leaving sudoers untouched")
        except Exception:
            log.exception("couchside: on-load sudoers grant repair skipped")
        # (2) arm the unit: enable if install.sh left it dormant, (re)start if we
        # swapped the binary or it isn't running.
        try:
            enabled = _run(["systemctl", "is-enabled", "--quiet", "couchside.service"]).returncode == 0
            active = _run(["systemctl", "is-active", "--quiet", "couchside.service"]).returncode == 0
            if not enabled:
                _run(["systemctl", "enable", "couchside.service"])
            if changed or not active:
                _run(["systemctl", "restart", "couchside.service"])
        except Exception:
            log.exception("couchside: on-load service arm skipped")

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
                raise FileNotFoundError(f"bundled service file missing: {f}")

        # sanity: the bundled daemon compiles
        _run(["python3", "-m", "py_compile", src_daemon], check=True)

        # (c) daemon -> ~/.local/opt/couchside
        # Install the VENDORED, release-reviewed defaults/couchsided.py that ships
        # in this plugin tarball. We deliberately do NOT fetch the service live from
        # GitHub main at install time: this backend runs as ROOT, and a live fetch
        # would have root execute whatever happens to be on main at that moment —
        # unreviewed, unpinned code. The release automation refreshes this vendored
        # copy on every plugin release, so it is always the current reviewed service
        # and a live fetch is both unnecessary and unsafe.
        #
        # Preserve the original no-downgrade intent by version, not by network:
        # compare the vendored VERSION against any already-installed service and
        # install the vendored one only when it is newer-or-equal. Never downgrade
        # a box whose installed service is somehow newer than the vendored copy.
        install_dir = os.path.join(home, ".local", "opt", "couchside")
        os.makedirs(install_dir, exist_ok=True)
        dst_daemon = os.path.join(install_dir, "couchsided.py")
        vendored_ver = _daemon_version(src_daemon)
        installed_ver = _daemon_version(dst_daemon) if os.path.exists(dst_daemon) else None
        # Install the vendored copy unless a strictly-newer service is already there.
        # Unknown installed version (can't parse) => treat as older and (re)install.
        if installed_ver is not None and _ver_gt(installed_ver, vendored_ver):
            log.info("couchside: keeping installed service %s (newer than vendored %s)",
                     installed_ver, vendored_ver)
        else:
            shutil.copyfile(src_daemon, dst_daemon)
        os.chmod(dst_daemon, 0o755)
        # Aerial-screensaver player (optional: only bundled in plugin >= 0.2.10).
        # The service's /api/screensaver launches it through a Steam shortcut.
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

        # (e) config.json in the user-owned state dir, migrating any legacy copy.
        # Migration must run BEFORE the "only if absent" check, or a box that
        # already has TV pairings in the legacy path would get a fresh default
        # config and appear to have lost them.
        _migrate_legacy_config(uid, gid)
        if not (os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 0):
            have_sddm = _run(["systemctl", "cat", "sddm.service"]).returncode == 0
            # Per-user flatpaks are invisible to root, so probe as the DESKTOP USER
            # (matches install.sh, which runs `flatpak info` as the invoking user).
            have_kodi = (shutil.which("flatpak") is not None
                         and _run(["sudo", "-u", user, "flatpak", "info", "tv.kodi.Kodi"]).returncode == 0)
            with open(CONFIG_FILE, "w") as f:
                json.dump(_gen_config(have_sddm, have_kodi), f, indent=2)
            # User-owned 0600, matching install.sh: the agent runs as this user
            # and MUST be able to rewrite the file when a pairing is saved.
            os.chmod(CONFIG_FILE, 0o600)
            os.chown(CONFIG_FILE, uid, gid)

        # (f0) Fixed-arg journal wrapper the sudoers rule grants. Root-owned
        # (0755) in the root-owned ETC_DIR so the desktop user can execute but
        # never modify it (a modifiable target would be root-code injection).
        with open(JOURNAL_WRAPPER, "w") as f:
            f.write(_JOURNAL_WRAPPER_SRC)
        os.chmod(JOURNAL_WRAPPER, 0o755)
        os.chown(JOURNAL_WRAPPER, 0, 0)

        # (f) sudoers rule, validated with visudo before install
        sudoers = (
            f"# couchside: passwordless sudo for EXACTLY the service's privileged commands.\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart sddm\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl reboot\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl suspend\n"
            # Restart Decky Loader itself: plugin_loader.service dies (cleanly,
            # so systemd never revives it) whenever Steam's CEF restarts, and
            # the Decky panel silently vanishes until reboot. This fixed-arg
            # grant powers the app's "Restart Decky" recovery action — and on a
            # Decky box the service that runs THIS plugin is exactly the one
            # being made recoverable.
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart plugin_loader\n"
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

        # (f3) /dev/rtc0 access for scheduled wake (RTC alarm). The service is already
        # in group 'input' (added above), so this grant needs no sudoers change.
        with open(RTC_UDEV, "w") as f:
            f.write('KERNEL=="rtc0", SUBSYSTEM=="rtc", GROUP="input", MODE="0660"\n')
        _run(["udevadm", "control", "--reload-rules"])
        _run(["udevadm", "trigger", "--subsystem-match=rtc", "--action=change"])

        # (g) systemd unit, rendered from the template install.sh owns
        unit = _render_unit(user, uid, dst_daemon)
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
            # (c) drop the service user from the 'input' group we added it to
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
                # config.json moved here, so a purge that skipped it would leave
                # the box's pairings behind and a reinstall would silently adopt
                # them — surprising for someone who asked to purge.
                shutil.rmtree(STATE_DIR, ignore_errors=True)
                for f in (SUDOERS_FILE, SUDOERS_FILE_LEGACY):
                    if os.path.exists(f):
                        os.remove(f)
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
        """Replace this plugin (and its vendored service) with the latest GitHub
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
