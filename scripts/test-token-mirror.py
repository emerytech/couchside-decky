#!/usr/bin/env python3
"""Tests for the token mirror + root file I/O in the user-owned state dir.

Run: python3 scripts/test-token-mirror.py

Why this exists: agent 2.9.114 (emerytech/couchside #557, KI-088) keeps
/etc/couchside/token CANONICAL and a 0600 MIRROR at /var/lib/couchside/token,
because SteamOS updates have dropped /etc/couchside wholesale while
/var/lib/couchside survived. Before this, the plugin's install minted a NEW
token whenever /etc was empty (silently breaking every paired phone), its
pairing screen read /etc only (dead QR exactly when needed), and regenerate
wrote /etc only.

It also pins the SECURITY properties of doing that as ROOT inside a directory
the desktop user owns (the same account every game and the LAN-exposed agent
run as): a planted symlink must never be followed for a read, a write or a
chown, and the mirror's contents are never trusted into /etc unless they look
like a token. Each symlink case plants a victim file and asserts it is untouched.

Both directions everywhere (present/absent), no pytest, no deps -- same style
as test-config-migration.py so CI can just run it.
"""
import asyncio
import getpass
import importlib.util
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("main", os.environ.get("MAIN_PY", os.path.join(ROOT, "main.py")))
m = importlib.util.module_from_spec(_spec)
sys.modules["main"] = m
_spec.loader.exec_module(m)

UID, GID = os.getuid(), os.getgid()
FAILURES = []
_REAL = {k: getattr(m, k) for k in ("STATE_DIR", "CONFIG_FILE", "ETC_DIR", "TOKEN_FILE", "OLD_INSTALLS",
                                    "_target_user", "_run")}
TOK_A = "a" * 48
TOK_B = "b" * 48
TOK_OLD = "c" * 48


def check(name, got, want):
    if got == want:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s (got %r, want %r)" % (name, got, want))
        FAILURES.append(name)


def _mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


def _put(p, text, mode=0o600):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    os.chmod(p, mode)


def _read(p):
    with open(p) as f:
        return f.read().strip()


def sandbox():
    """Repoint every path global at a throwaway tree; returns (canonical, mirror, root)."""
    root = tempfile.mkdtemp()
    m.ETC_DIR = os.path.join(root, "etc", "couchside")
    m.TOKEN_FILE = os.path.join(m.ETC_DIR, "token")
    m.STATE_DIR = os.path.join(root, "var", "lib", "couchside")
    m.CONFIG_FILE = os.path.join(m.STATE_DIR, "config.json")
    m.OLD_INSTALLS = [(os.path.join(root, "etc", "couchpilot"), "x", "y")]
    return m.TOKEN_FILE, os.path.join(m.STATE_DIR, "token"), root


def restore():
    for k, v in _REAL.items():
        setattr(m, k, v)


def case(fn):
    print(fn.__name__)
    try:
        fn(*sandbox())
    except Exception as e:  # a crash is a failure, not a test-runner abort
        print("  FAIL  %s raised %r" % (fn.__name__, e))
        FAILURES.append(fn.__name__)
    finally:
        restore()


# ---- _ensure_token: the install-time step ---------------------------------

def test_canonical_present_mirror_created(canon, mirror, root):
    _put(canon, TOK_A + "\n")
    got = m._ensure_token(UID, GID)
    check("canonical token kept", got, TOK_A)
    check("mirror created = canonical", _read(mirror), TOK_A)
    check("mirror 0600", _mode(mirror), 0o600)
    check("state dir 0700", _mode(m.STATE_DIR), 0o700)
    check("canonical 0600", _mode(canon), 0o600)


def test_canonical_wins_over_stale_mirror(canon, mirror, root):
    """Rotation already happened in /etc; the mirror must follow, not win."""
    _put(canon, TOK_B + "\n")
    _put(mirror, TOK_A + "\n")
    got = m._ensure_token(UID, GID)
    check("canonical (rotated) token kept", got, TOK_B)
    check("stale mirror re-synced", _read(mirror), TOK_B)


def test_lost_canonical_restored_from_mirror(canon, mirror, root):
    """THE field case: SteamOS dropped /etc/couchside, the mirror survived."""
    _put(mirror, TOK_A + "\n")
    got = m._ensure_token(UID, GID)
    check("mirror token restored (phones stay paired)", got, TOK_A)
    check("canonical recreated from the mirror", _read(canon), TOK_A)
    check("canonical 0600", _mode(canon), 0o600)


def test_mirror_beats_old_product_token(canon, mirror, root):
    _put(mirror, TOK_A + "\n")
    _put(os.path.join(m.OLD_INSTALLS[0][0], "token"), TOK_OLD + "\n")
    check("live mirror wins over a leftover old-product token", m._ensure_token(UID, GID), TOK_A)


def test_old_product_token_migrated(canon, mirror, root):
    _put(os.path.join(m.OLD_INSTALLS[0][0], "token"), TOK_OLD + "\n")
    got = m._ensure_token(UID, GID)
    check("old-product token migrated", got, TOK_OLD)
    check("…into canonical and mirror", (_read(canon), _read(mirror)), (TOK_OLD, TOK_OLD))


def test_nothing_anywhere_mints(canon, mirror, root):
    got = m._ensure_token(UID, GID)
    check("minted 48-hex token", len(got) == 48 and all(c in "0123456789abcdef" for c in got), True)
    check("canonical and mirror hold it", (_read(canon), _read(mirror)), (got, got))


# ---- security: root in a user-owned dir -----------------------------------

def test_symlinked_mirror_is_not_read(canon, mirror, root):
    """Even a TOKEN-SHAPED file must not be read through a planted symlink."""
    victim = os.path.join(root, "root-only-secret")
    _put(victim, TOK_B + "\n", 0o644)
    os.makedirs(m.STATE_DIR, exist_ok=True)
    os.symlink(victim, mirror)
    got = m._ensure_token(UID, GID)
    check("symlink target not adopted as the token", got != TOK_B, True)
    check("victim content untouched", _read(victim), TOK_B)
    check("victim mode untouched", _mode(victim), 0o644)
    check("symlink replaced by a regular file", os.path.islink(mirror), False)


def test_junk_mirror_is_not_laundered_into_etc(canon, mirror, root):
    _put(mirror, "root:$6$abcdefgh$xyz:19000:0:99999:7:::\n")
    got = m._ensure_token(UID, GID)
    check("non-token mirror content not copied into canonical", "root:" in _read(canon), False)
    check("a fresh token was minted instead", len(got), 48)


def test_symlinked_mirror_not_followed_on_write(canon, mirror, root):
    victim = os.path.join(root, "victim-file")
    _put(victim, "original\n", 0o644)
    _put(canon, TOK_A + "\n")
    os.makedirs(m.STATE_DIR, exist_ok=True)
    os.symlink(victim, mirror)
    m._ensure_token(UID, GID)
    check("victim content untouched by the mirror write", _read(victim), "original")
    check("victim mode untouched by fchmod", _mode(victim), 0o644)
    check("mirror is now a real file with the token", (os.path.islink(mirror), _read(mirror)), (False, TOK_A))


def test_dangling_symlink_write_does_not_create_target(canon, mirror, root):
    """os.path.exists() is False for a dangling link; root must not create its target."""
    victim = os.path.join(root, "etc-profile.d", "zz.sh")
    os.makedirs(os.path.dirname(victim))
    os.makedirs(m.STATE_DIR, exist_ok=True)
    os.symlink(victim, m.CONFIG_FILE)
    m._write_private(m.CONFIG_FILE, "{}", UID, GID)
    check("dangling target NOT created", os.path.exists(victim), False)
    check("config is a regular 0600 file", (os.path.islink(m.CONFIG_FILE), _mode(m.CONFIG_FILE)), (False, 0o600))


def test_symlinked_config_not_chowned_on_load(canon, mirror, root):
    """The every-boot path: _arm_on_load -> _migrate_legacy_config ownership repair."""
    victim = os.path.join(root, "shadow")
    _put(victim, "root:x:1:::\n", 0o644)
    os.makedirs(m.STATE_DIR, exist_ok=True)
    os.symlink(victim, m.CONFIG_FILE)
    m._migrate_legacy_config(UID, GID)
    check("victim mode untouched (ownership repair did not follow the link)", _mode(victim), 0o644)
    check("victim content untouched", _read(victim), "root:x:1:::")


# ---- get_pairing -----------------------------------------------------------

def _pairing():
    return asyncio.run(m.Plugin().get_pairing())


def test_pairing_canonical(canon, mirror, root):
    _put(canon, TOK_A + "\n")
    _put(mirror, TOK_B + "\n")
    r = _pairing()
    check("pairing uses canonical when present", (r["ok"], r.get("token")), (True, TOK_A))


def test_pairing_falls_back_to_mirror(canon, mirror, root):
    _put(mirror, TOK_A + "\n")
    r = _pairing()
    check("pairing falls back to the mirror", (r["ok"], r.get("token")), (True, TOK_A))
    check("pair URL carries it in the #fragment", ("token=" + TOK_A) in r.get("pair_url", ""), True)


def test_pairing_nothing(canon, mirror, root):
    check("no token anywhere -> not installed", _pairing(), {"ok": False, "error": "not installed yet"})


def test_pairing_ignores_junk_mirror(canon, mirror, root):
    _put(mirror, "not a token at all\n")
    check("junk mirror -> not installed", _pairing().get("ok"), False)


# ---- regenerate_token ------------------------------------------------------

def test_regenerate_writes_both(canon, mirror, root):
    calls = []
    m._target_user = lambda: getpass.getuser()
    m._run = lambda cmd, check=False: calls.append(cmd)
    _put(canon, TOK_A + "\n")
    _put(mirror, TOK_A + "\n")
    r = asyncio.run(m.Plugin().regenerate_token())
    new = r.get("token", "")
    check("regenerate ok with a new 48-hex token", (r.get("ok"), len(new), new != TOK_A), (True, 48, True))
    check("canonical holds the new token", _read(canon), new)
    check("mirror holds the new token too", _read(mirror), new)
    check("both 0600", (_mode(canon), _mode(mirror)), (0o600, 0o600))
    check("service restarted once", calls, [["systemctl", "restart", "couchside.service"]])


def test_regenerate_creates_missing_dirs(canon, mirror, root):
    m._target_user = lambda: getpass.getuser()
    m._run = lambda cmd, check=False: None
    r = asyncio.run(m.Plugin().regenerate_token())
    check("regenerate ok with nothing on disk", r.get("ok"), True)
    check("canonical + mirror written, state dir 0700",
          (_read(canon), _read(mirror), _mode(m.STATE_DIR)), (r.get("token"), r.get("token"), 0o700))


if __name__ == "__main__":
    for fn in (test_canonical_present_mirror_created, test_canonical_wins_over_stale_mirror,
               test_lost_canonical_restored_from_mirror, test_mirror_beats_old_product_token,
               test_old_product_token_migrated, test_nothing_anywhere_mints,
               test_symlinked_mirror_is_not_read, test_junk_mirror_is_not_laundered_into_etc,
               test_symlinked_mirror_not_followed_on_write, test_dangling_symlink_write_does_not_create_target,
               test_symlinked_config_not_chowned_on_load,
               test_pairing_canonical, test_pairing_falls_back_to_mirror, test_pairing_nothing,
               test_pairing_ignores_junk_mirror,
               test_regenerate_writes_both, test_regenerate_creates_missing_dirs):
        case(fn)
    if FAILURES:
        print("\n%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("\nall token-mirror tests passed")
