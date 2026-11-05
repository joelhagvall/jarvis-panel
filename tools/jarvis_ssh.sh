#!/bin/sh
# Use the same noninteractive, keychain-backed connection for deploys, native staging and rsync.
set -eu
# Usage: tools/jarvis_ssh.sh user@jarvis.local 'COMMAND'. The host comes first; a command in its place
# made ssh fail with "hostname contains invalid characters" (2026-10-29).
case "${1:-}" in
  ''|*' '*) echo "usage: $0 HOST [COMMAND]  (e.g. user@jarvis.local)" >&2; exit 2 ;;
esac
exec ssh -o BatchMode=yes -o ConnectTimeout=8 -o IgnoreUnknown=UseKeychain \
  -o UseKeychain=yes -o ControlMaster=auto -o "ControlPath=$HOME/.ssh/cm-%C" \
  -o ControlPersist=10m "$@"
