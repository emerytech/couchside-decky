#!/usr/bin/env python3
"""Tests for the config-path migration.

Run: python3 scripts/test-config-migration.py

Why this exists: config.json moved out of the root-owned /etc/couchside into the
user-owned /var/lib/couchside, because the agent runs as the desktop user and
could not write the old location — every TV pairing and settings save returned
500 "could not persist config: Permission denied".

A migration that loses the file loses the user's TV pairings, and a migration
that overwrites the LIVE config with a stale legacy copy is worse: on a box that
also ran install.sh, /etc/couchside/config.json is a leftover while
/var/lib/couchside/config.json is the real one. Both directions are covered here.

No pytest, no deps — same style as check-invariants.sh so CI can just run it.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("main", os.path.join(ROOT, "main.py"))
m = importlib.util.module_from_spec(_spec)
sys.modules["main"] = m
_spec.loader.exec_module(m)

UID, GID = os.getuid(), os.getgid()
FAILURES = []

# sandbox() repoints these module globals at a temp tree. Snapshot the real
# values so each test can restore them — otherwise a later test (e.g. the unit
# render, which reads m.CONFIG_FILE) sees a leaked sandbox path and fails for the
# wrong reason.
_REAL_PATHS = {k: getattr(m, k) for k in ("STATE_DIR", "CONFIG_FILE", "LEGACY_CONFIG")}


def _restore_paths():
    for k, v in _REAL_PATHS.items():
        setattr(m, k, v)


def check(name, got, want):
    if got == want:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s (got %r, want %r)" % (name, got, want))
        FAILURES.append(name)


def sandbox(legacy=None, new=None):
    """Point the module's paths at a throwaway tree."""
    root = tempfile.mkdtemp()
    m.STATE_DIR = os.path.join(root, "var", "lib", "couchside")
    m.CONFIG_FILE = os.path.join(m.STATE_DIR, "config.json")
    etc = os.path.join(root, "etc", "couchside")
    os.makedirs(etc)
    m.LEGACY_CONFIG = os.path.join(etc, "config.json")
    if legacy is not None:
        with open(m.LEGACY_CONFIG, "w") as f:
            json.dump(legacy, f)
    if new is not None:
        os.makedirs(m.STATE_DIR, exist_ok=True)
        with open(m.CONFIG_FILE, "w") as f:
            json.dump(new, f)
    return root


def test_legacy_only():
    print("legacy config only (a box installed by the pre-fix plugin)")
    root = sandbox(legacy={"vidaa": {"host": "10.1.1.98"}, "units": [1, 2]})
    try:
        m._migrate_legacy_config(UID, GID)
        check("migrated into the state dir", os.path.exists(m.CONFIG_FILE), True)
        with open(m.CONFIG_FILE) as f:
            got = json.load(f)
        check("TV pairing preserved", got.get("vidaa"), {"host": "10.1.1.98"})
        check("legacy copy removed", os.path.exists(m.LEGACY_CONFIG), False)
        check("config is 0600", oct(os.stat(m.CONFIG_FILE).st_mode & 0o777), "0o600")
        # The agent writes a temp file into this DIRECTORY and os.replace()s it,
        # so the directory itself must be user-writable.
        check("state dir is 0700", oct(os.stat(m.STATE_DIR).st_mode & 0o777), "0o700")
    finally:
        shutil.rmtree(root)
        _restore_paths()


def test_both_present():
    print("both present (box that ran install.sh AND has the plugin)")
    root = sandbox(legacy={"who": "stale"},
                   new={"who": "live", "panel": {"dev": "/dev/ttyS0"}})
    try:
        m._migrate_legacy_config(UID, GID)
        with open(m.CONFIG_FILE) as f:
            got = json.load(f)
        # The single most destructive possible bug here.
        check("LIVE config not clobbered by the stale legacy copy",
              got.get("who"), "live")
        check("panel (RS-232 TV backend) intact",
              got.get("panel"), {"dev": "/dev/ttyS0"})
    finally:
        shutil.rmtree(root)
        _restore_paths()


def test_neither():
    print("fresh box (no config anywhere)")
    root = sandbox()
    try:
        m._migrate_legacy_config(UID, GID)
        check("no config invented", os.path.exists(m.CONFIG_FILE), False)
        check("state dir still prepared", os.path.isdir(m.STATE_DIR), True)
    finally:
        shutil.rmtree(root)
        _restore_paths()


def test_ownership_repair():
    print("ownership repair + idempotence")
    root = sandbox(new={"a": 1})
    try:
        os.chmod(m.CONFIG_FILE, 0o644)   # what the old plugin left behind
        changed = m._migrate_legacy_config(UID, GID)
        check("root-ish 0644 repaired to 0600",
              oct(os.stat(m.CONFIG_FILE).st_mode & 0o777), "0o600")
        check("repair reports a change", changed, True)
        check("second run is a no-op", m._migrate_legacy_config(UID, GID), False)
    finally:
        shutil.rmtree(root)
        _restore_paths()


def test_empty_legacy():
    print("empty legacy file")
    root = sandbox()
    try:
        open(m.LEGACY_CONFIG, "w").close()   # zero bytes
        m._migrate_legacy_config(UID, GID)
        check("empty legacy file is not migrated",
              os.path.exists(m.CONFIG_FILE), False)
    finally:
        shutil.rmtree(root)
        _restore_paths()


def test_unit_passes_config():
    print("bundled unit template")
    with open(os.path.join(ROOT, "defaults", "couchside.service")) as f:
        unit = f.read()
    # The template is install.sh's, synced verbatim, so the config path is the
    # __CONFIG__ placeholder here, not a literal — the literal path is asserted
    # in test_render_unit after substitution. Without --config the agent falls
    # back to its built-in /etc/couchside/config.json, the unwritable path this
    # change exists to escape.
    check("ExecStart passes --config", m._execstart_has_config(unit), True)
    check("...via the __CONFIG__ placeholder", "__CONFIG__" in unit, True)
    check("resolved daemon path via __EXEC__ (never a hardcoded /home)",
          "__EXEC__" in unit and "/home/" not in unit, True)
    check("still grants the input group (evdev + uinput)",
          "SupplementaryGroups=input" in unit, True)
    # This file must stay byte-identical to couchside's agent/couchside.service.
    # The CI sync re-fetches it, but if someone hand-edits the vendored copy the
    # template drift this whole change fights would silently return.
    check("CONFIG_FILE constant points at the user-owned state dir",
          _REAL_PATHS["CONFIG_FILE"], "/var/lib/couchside/config.json")


def test_unit_repair_guard():
    """The guard that decides whether on-load repair rewrites the unit.

    It must fire on a pre-fix Decky unit and NEVER on install.sh's unit — that
    is what makes the two installers safe side by side on one box.
    """
    print("unit-repair guard")
    install_sh_unit = (
        "[Service]\nUser=deck\n"
        "ExecStart=/usr/bin/python3 /home/deck/.local/opt/couchside/couchsided.py"
        " --config /var/lib/couchside/config.json\n")
    check("install.sh's unit is LEFT ALONE",
          m._execstart_has_config(install_sh_unit), True)

    pre_fix_decky_unit = (
        "[Service]\nUser=deck\n"
        "ExecStart=/usr/bin/python3 /home/deck/.local/opt/couchside/couchsided.py\n")
    check("pre-fix Decky unit IS repaired",
          m._execstart_has_config(pre_fix_decky_unit), False)

    # The trap this helper exists for: --config named only in a comment must not
    # count as the flag being passed, or the repair silently no-ops.
    comment_only = (
        "; --config is explained here but not actually passed\n"
        "[Service]\n"
        "ExecStart=/usr/bin/python3 /opt/couchsided.py\n")
    check("--config in a COMMENT does not count",
          m._execstart_has_config(comment_only), False)


def test_render_unit():
    """_render_unit fills every placeholder in the synced template.

    The template is install.sh's, carried verbatim, so the plugin must
    substitute all four fields — including __EXEC__ with the RESOLVED daemon
    path (never a hardcoded /home, which breaks on Bazzite's /var/home) and
    __CONFIG__ with the user-owned state path.
    """
    print("unit rendering")
    m._plugin_dir = lambda: ROOT
    exec_path = "/var/home/deck/.local/opt/couchside/couchsided.py"
    u = m._render_unit("deck", 1000, exec_path)
    for ph in ("__USER__", "__UID__", "__EXEC__", "__CONFIG__"):
        check("placeholder %s substituted" % ph, ph in u, False)
    check("runs as the desktop user", "\nUser=deck\n" in u, True)
    check("XDG_RUNTIME_DIR uses the uid", "/run/user/1000" in u, True)
    check("ExecStart uses the resolved path verbatim",
          ("ExecStart=/usr/bin/python3 " + exec_path) in u, True)
    check("ExecStart passes --config to the state dir",
          "--config /var/lib/couchside/config.json" in u, True)
    check("input group preserved", "SupplementaryGroups=input" in u, True)

    # If upstream adds a placeholder this plugin does not know to substitute, the
    # render must FAIL rather than write a unit with a literal __NEW__ in its
    # ExecStart and restart the box into it.
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "defaults"))
        with open(os.path.join(tmp, "defaults", "couchside.service"), "w") as f:
            f.write("[Service]\nUser=__USER__\n"
                    "ExecStart=/usr/bin/python3 __EXEC__ --config __CONFIG__\n"
                    "Environment=XDG_RUNTIME_DIR=/run/user/__UID__\n"
                    "Environment=NEW=__FUTURE_FIELD__\n")   # unknown to render
        m._plugin_dir = lambda: tmp
        raised = False
        try:
            m._render_unit("deck", 1000, "/x/couchsided.py")
        except RuntimeError:
            raised = True
        check("an unknown leftover placeholder raises", raised, True)
    finally:
        m._plugin_dir = lambda: ROOT
        shutil.rmtree(tmp)


if __name__ == "__main__":
    for fn in (test_legacy_only, test_both_present, test_neither,
               test_ownership_repair, test_empty_legacy, test_unit_passes_config,
               test_unit_repair_guard, test_render_unit):
        fn()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        sys.exit(1)
    print("all config-migration tests passed")
