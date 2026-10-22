#!/usr/bin/env bash
# restore.sh — restore a tracker backup tarball into place.
#
# Usage: bash restore.sh /path/to/pi-agent-<stamp>.tar.gz
# WARNING: overwrites current /root/pi-agent state (DB, lancedb, out, scripts, .env).
# Recommend: stop cron first (crontab -l > /tmp/cron.save && crontab -r)

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <backup-tarball>"
  exit 1
fi
TARBALL="$1"
if [ ! -f "$TARBALL" ]; then
  echo "error: tarball not found: $TARBALL"
  exit 1
fi

ROOT="/root"
STAMP="$(date +%Y-%m-%d-%H%M%S)"

# safety: snapshot the current state before overwrite
mkdir -p "${ROOT}/pi-agent/_pre_restore"
echo "snapshotting current state to ${ROOT}/pi-agent/_pre_restore/ ..."
tar -czf "${ROOT}/pi-agent/_pre_restore/pre-restore-${STAMP}.tar.gz" \
  -C /root pi-agent || echo "warn: snapshot failed (non-fatal)"

echo "restoring $TARBALL ..."
tar -xzf "$TARBALL" -C /root

echo "restore complete. Current state:"
ls -la "${ROOT}/pi-agent" | head -20
echo
echo "NOTE: if you stopped cron, reinstall with:"
echo "  crontab /tmp/cron.save"