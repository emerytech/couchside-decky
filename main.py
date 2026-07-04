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
"""

import json
import os
import pwd
import secrets
import shutil
import socket
import subprocess
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
SUDOERS_FILE = "/etc/sudoers.d/couchside"
UNIT_DST = "/etc/systemd/system/couchside.service"
UINPUT_UDEV = "/etc/udev/rules.d/99-couchside-uinput.rules"
UINPUT_MODLOAD = "/etc/modules-load.d/couchside-uinput.conf"

# Pre-rename installs to retire: "etc_dir|unit|sudoers".
OLD_INSTALLS = [
    ("/etc/rescue-agent", "rescue-agent.service", "/etc/sudoers.d/rescue-agent"),
    ("/etc/couchpilot", "couchpilot.service", "/etc/sudoers.d/couchpilot"),
]


def _plugin_dir() -> str:
    return os.environ.get("DECKY_PLUGIN_DIR", os.path.dirname(os.path.abspath(__file__)))


def _target_user() -> str:
    """The desktop user the agent runs as. Decky exposes it; fall back to a guess."""
    u = os.environ.get("DECKY_USER")
    if u:
        return u
    for cand in ("deck", "bazzite"):
        try:
            pwd.getpwnam(cand)
            return cand
        except KeyError:
            continue
    # last resort: the first regular login user
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 65000 and p.pw_dir.startswith("/home/"):
            return p.pw_name
    return "deck"


def _run(cmd, check=False):
    log.info("couchside: run %s", " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
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


def _read_port() -> int:
    try:
        with open(CONFIG_FILE) as f:
            return int(json.load(f).get("port") or PORT_DEFAULT)
    except Exception:
        return PORT_DEFAULT


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # no packet is actually sent
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


class Plugin:
    # ---- lifecycle -------------------------------------------------------
    async def _main(self):
        log.info("Couchside plugin loaded (target user: %s)", _target_user())

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
        install_dir = os.path.join(home, ".local", "opt", "couchside")
        os.makedirs(install_dir, exist_ok=True)
        dst_daemon = os.path.join(install_dir, "couchsided.py")
        shutil.copyfile(src_daemon, dst_daemon)
        os.chmod(dst_daemon, 0o755)
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
            have_kodi = (shutil.which("flatpak") is not None
                         and _run(["flatpak", "info", "tv.kodi.Kodi"]).returncode == 0)
            with open(CONFIG_FILE, "w") as f:
                json.dump(_gen_config(have_sddm, have_kodi), f, indent=2)
            os.chmod(CONFIG_FILE, 0o644)
            os.chown(CONFIG_FILE, 0, 0)

        # (f) sudoers rule, validated with visudo before install
        sudoers = (
            f"# couchside: passwordless sudo for EXACTLY the agent's privileged commands.\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart sddm\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl reboot\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n"
            f"{user} ALL=(root) NOPASSWD: /usr/bin/journalctl *\n"
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
        return {"installed": installed, "running": running, "port": port, "agent_version": version}

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
            for p in (UNIT_DST, UINPUT_UDEV, UINPUT_MODLOAD):
                if os.path.exists(p):
                    os.remove(p)
            _run(["systemctl", "daemon-reload"])
            shutil.rmtree(os.path.join(home, ".local", "opt", "couchside"), ignore_errors=True)
            if purge:
                shutil.rmtree(ETC_DIR, ignore_errors=True)
                if os.path.exists(SUDOERS_FILE):
                    os.remove(SUDOERS_FILE)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
