#!/bin/bash
# Restart the messaging job safely. `launchctl unload` sends SIGTERM to the
# supervisor and returns before the supervisor's own graceful shutdown
# actually finishes (telegram_bot gets up to 45s grace before SIGKILL, see
# messaging_supervisor.py) — an unload immediately followed by load can start
# a second telegram_bot.py polling the same bot token while the old one is
# still draining (confirmed live 2026-07-14). Poll until every managed child
# process is actually gone before reloading.
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.clixen.messaging.plist"

launchctl unload "$PLIST"

echo "waiting for messaging processes to fully drain..."
for i in $(seq 1 60); do
    if ! pgrep -f "telegram_bot\.py|whatsapp_bot\.py|whatsapp_bridge\.ts|messaging_supervisor\.py" > /dev/null 2>&1; then
        echo "drained after ${i}s"
        break
    fi
    sleep 1
done

if pgrep -f "telegram_bot\.py|whatsapp_bot\.py|whatsapp_bridge\.ts|messaging_supervisor\.py" > /dev/null 2>&1; then
    echo "WARNING: messaging processes still alive after 60s, loading anyway (may briefly double-poll)" >&2
fi

launchctl load "$PLIST"
echo "messaging job reloaded"
