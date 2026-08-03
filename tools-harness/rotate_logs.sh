#!/bin/bash
# Rotates launchd-piped raw stdout/stderr logs that Python's RotatingFileHandler
# doesn't touch (core_stdout/stderr, messaging_stdout/stderr). Python-side logs
# (chat_ui.log, task_worker.log, telegram_bot.log) already rotate via log_config.py.
set -euo pipefail

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_BYTES=$((10 * 1024 * 1024))  # 10MB, same threshold as log_config.py
KEEP=5

for f in core_stdout.log core_stderr.log messaging_stdout.log messaging_stderr.log; do
    path="$LOG_DIR/$f"
    [ -f "$path" ] || continue
    size=$(stat -f%z "$path" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_BYTES" ]; then
        ts=$(date +%Y%m%d_%H%M%S)
        mv "$path" "$path.$ts"
        gzip "$path.$ts"
        : > "$path"
        # keep only the newest $KEEP rotated copies
        ls -t "$path".*.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do rm -f -- "$old"; done
    fi
done
