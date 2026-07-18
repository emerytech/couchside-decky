#!/usr/bin/env bash
# check-invariants.sh — fail the build when a rename has quietly broken a string
# that must stay EXACT.
#
# Why this exists: a blanket "agent" -> "service" rename across main.py on
# 2026-07-18 silently rewrote the HTTP User-Agent header and the legacy
# /etc/rescue-agent cleanup paths. Both still parsed, still passed review at a
# glance, and would have broken real behaviour in the field — the self-update
# request and the cleanup of pre-rename installs. They were caught by reading
# the diff, which is luck, not a control. This is the control.
#
# Each entry below is a string whose exact bytes matter. If you are deliberately
# changing one, update this file in the same commit and say why.
set -euo pipefail

cd "$(dirname "$0")/.."
fail=0

check() { # <file> <literal> <why it must not change>
  if ! grep -qF -- "$2" "$1"; then
    printf 'FAIL  %s\n      missing: %s\n      why:     %s\n' "$1" "$2" "$3" >&2
    fail=1
  fi
}

# The self-update request to GitHub. Renaming the header name breaks the
# request; renaming only the value is harmless but pointless.
check main.py '"User-Agent"' 'GitHub self-update request must send a real User-Agent header'

# Legacy on-disk paths from installs that predate the rename. These must keep
# matching what is ACTUALLY on older boxes or the cleanup silently finds nothing.
check main.py '/etc/rescue-agent'    'legacy cleanup must match the old config dir on disk'
check main.py 'rescue-agent.service' 'legacy cleanup must match the old systemd unit name'

# Wire contract with the phone app / panel.
check main.py 'agent_version' 'wire field the panel reads; renaming breaks the status display'
check main.py 'couchsided.py'  'filename of the bundled Couchside service'

if [ "$fail" -eq 0 ]; then
  echo "invariants OK"
else
  echo "" >&2
  echo "One or more rename-sensitive strings changed. See the notes above." >&2
  exit 1
fi
