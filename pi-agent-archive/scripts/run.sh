#!/usr/bin/env bash
# run.sh — full tracker loop entry point. Called by cron.
# 1. seed fresh search results into SQLite
# 2. embed new content into LanceDB
# 3. index briefings+memory into vectors (recall grid)
# 4. drive pi agent briefing (folded memory + vector recall context)
# 5. deliver briefing via Telegram + alert on failures
#
# All output to a per-run log; never let a failure kill the daemon.

set -uo pipefail

ROOT="/root/pi-agent"
LOG="${ROOT}/data/run.log"
STAMP="$(date +%Y-%m-%d-%H%M%S)"
PY="${ROOT}/.venv/bin/python"
FAILED=""

step_failed() {
  FAILED="${FAILED} $1"
  echo "[!] step failed: $1" >&2
}

{
  echo "=== [${STAMP}] tracker run start ==="

  echo "[1/5] seeding search results..."
  "$PY" "$ROOT/scripts/search_store.py" --seed-all || step_failed "seed"

  echo "[2/5] indexing article embeddings..."
  "$PY" "$ROOT/scripts/embed_index.py" || step_failed "embed"

  echo "[3/5] indexing recall grid (briefings + memory)..."
  "$PY" "$ROOT/scripts/recall.py" --index || step_failed "recall-index"

  echo "[4/5] running pi agent..."
  bash "$ROOT/scripts/run_agent.sh" || step_failed "agent"

  echo "=== [${STAMP}] tracker run end ==="
} >> "$LOG" 2>&1

# Alert on any failure (quiet ok: no alert when all steps passed)
if [ -n "$FAILED" ]; then
  "$PY" "$ROOT/scripts/notify.py" alert "Tracker run ${STAMP} had failures:${FAILED}" \
      >> "$LOG" 2>&1 || true
  exit 1
fi

# Deliver latest briefing to Telegram after a successful run
LATEST="$(ls -1t "$ROOT"/out/briefing-*.md 2>/dev/null | head -1)"
if [ -n "$LATEST" ]; then
  "$PY" "$ROOT/scripts/notify.py" briefing "$LATEST" >> "$LOG" 2>&1 || true
fi

exit 0