#!/usr/bin/env bash
# =============================================================================
# Clixen launchd installer — generates plists with THIS machine's paths and
# installs them into ~/Library/LaunchAgents.
#
#   ./install-launchd.sh            # install all services
#   ./install-launchd.sh --dry-run  # print what would be written, change nothing
#   ./install-launchd.sh --unload   # unload (not remove) installed services
#   ./install-launchd.sh --remove   # unload + delete installed plists
#
# Plist sources use __CLIXEN_ROOT__ / __PYTHON_BIN__ / __HARNESS_DIR__ tokens.
# Static services (task_worker, email summary, health writer, autopilot mail)
# are always installed. The per-sender email watchers install one plist per
# sender in WATCHED_SENDERS (comma-separated).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HARNESS_DIR="$REPO_ROOT/tools-harness"
AGENTS_DIR="$HOME/Library/LaunchAgents"

# Defaults (override via env):
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || echo /usr/bin/python3)}"
fi
# autopilot-mail node binary (override via AUTOPILOT_BIN). Optional — service
# is skipped when not set.
AUTOPILOT_BIN="${AUTOPILOT_BIN:-}"
# Senders watched by email_watch_* services (comma-separated). Override via
# WATCHED_SENDERS. Empty means "skip per-sender email watchers".
WATCHED_SENDERS="${WATCHED_SENDERS:-}"

ACTION=install
for arg in "$@"; do
  case "$arg" in
    --dry-run) ACTION=dry-run ;;
    --unload) ACTION=unload ;;
    --remove) ACTION=remove ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

mkdir -p "$AGENTS_DIR"

STATIC_PLISTS="com.clixen.task_worker com.clixen.daily_email_summary com.clixen.health_writer com.clixen.autopilot-mail"

_sub() {
  # $1 = template file; remaining args are extra sed expressions.
  local tpl="$1"
  shift
  sed -e "s|__CLIXEN_ROOT__|$REPO_ROOT|g" \
      -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
      -e "s|__HARNESS_DIR__|$HARNESS_DIR|g" \
      -e "s|__AUTOPILOT_BIN__|$AUTOPILOT_BIN|g" \
      "$@" "$tpl"
}

# Sanitize an email sender into a launchd-safe identifier (no @ . /).
_sender_id() {
  echo "$1" | sed 's/[^A-Za-z0-9_-]/_/g'
}

if [ "$ACTION" = "unload" ] || [ "$ACTION" = "remove" ]; then
  for base in $STATIC_PLISTS; do
    launchctl unload "$AGENTS_DIR/$base.plist" 2>/dev/null || true
  done
  for sender in $(echo "$WATCHED_SENDERS" | tr ',' ' '); do
    [ -z "$sender" ] && continue
    sid="$(_sender_id "$sender")"
    launchctl unload "$AGENTS_DIR/com.clixen.email_watch_${sid}.plist" 2>/dev/null || true
  done
  if [ "$ACTION" = "remove" ]; then
    rm -f "$AGENTS_DIR"/com.clixen.*.plist
    echo "Removed installed clixen plists from $AGENTS_DIR"
  else
    echo "Unloaded clixen services (plists remain in $AGENTS_DIR)"
  fi
  exit 0
fi

for base in $STATIC_PLISTS; do
  tpl="$SCRIPT_DIR/$base.plist.tpl"
  dst="$AGENTS_DIR/$base.plist"
  if [ ! -f "$tpl" ]; then
    echo "SKIP $base: no template $tpl"
    continue
  fi
  if [ "$ACTION" = "dry-run" ]; then
    echo "would write: $dst (from $tpl)"
    continue
  fi
  _sub "$tpl" > "$dst"
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "loaded: $dst"
done

for sender in $(echo "$WATCHED_SENDERS" | tr ',' ' '); do
  [ -z "$sender" ] && continue
  sid="$(_sender_id "$sender")"
  tpl="$SCRIPT_DIR/com.clixen.email_watch.plist.tpl"
  dst="$AGENTS_DIR/com.clixen.email_watch_${sid}.plist"
  if [ "$ACTION" = "dry-run" ]; then
    echo "would write: $dst (sender=$sender)"
    continue
  fi
  _sub "$tpl" -e "s|__SENDER__|$sender|g" -e "s|__SENDER_ID__|$sid|g" > "$dst"
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "loaded: $dst"
done

if [ "$ACTION" = "dry-run" ]; then
  echo ""
  echo "Dry run only — nothing changed. Install with ./install-launchd.sh"
fi
