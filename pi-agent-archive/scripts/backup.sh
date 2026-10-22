#!/usr/bin/env bash
# backup.sh — snapshot the tracker state (DB + LanceDB + out + scripts + .env).
# Run from cron daily. Prunes backups older than KEEP_DAYS.
#
# Usage: bash backup.sh [keep_days]

set -euo pipefail

ROOT="/root/pi-agent"
BK_DIR="${ROOT}/backups"
KEEP_DAYS="${1:-14}"

mkdir -p "$BK_DIR"
STAMP="$(date +%Y-%m-%d-%H%M%S)"
TARBALL="${BK_DIR}/pi-agent-${STAMP}.tar.gz"

# Exclude backups dir itself + venv (rebuildable) + logs
tar -czf "$TARBALL" \
  -C /root \
  --exclude='pi-agent/backups' \
  --exclude='pi-agent/.venv' \
  --exclude='pi-agent/data/*.log' \
  --exclude='pi-agent/data*/cron.log' \
  --exclude='pi-agent/.venv/*' \
  pi-agent

echo "backup: $TARBALL ($(du -h "$TARBALL" | cut -f1))"

# prune old backups
find "$BK_DIR" -name 'pi-agent-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "pruned backups older than ${KEEP_DAYS}d"
ls -1t "$BK_DIR"/pi-agent-*.tar.gz 2>/dev/null | head -1