#!/bin/bash
# Supervisor: runs telegram_bot, whatsapp_bot, and whatsapp_bridge independently.
# Each is restarted on its own if it exits — one crashing no longer takes down
# the other two (previously any single exit killed the whole group).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Environment
export PYTHONUNBUFFERED=1
export WHATSAPP_BRIDGE_URL=http://localhost:9235
export WHATSAPP_BOT_PORT=9236
export WHATSAPP_BRIDGE_PORT=9235
export WHATSAPP_BOT_URL=http://localhost:9236
export NODE_ENV=production
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

start_telegram() {
    "$SCRIPT_DIR/../.venv/bin/python" "$SCRIPT_DIR/telegram_bot.py" &
    PID_TELEGRAM=$!
}
start_whatsapp_bot() {
    "$SCRIPT_DIR/../.venv/bin/python" "$SCRIPT_DIR/whatsapp_bot.py" &
    PID_WHATSAPP_BOT=$!
}
start_whatsapp_bridge() {
    /opt/homebrew/bin/node "$SCRIPT_DIR/node_modules/tsx/dist/cli.mjs" "$SCRIPT_DIR/whatsapp_bridge.ts" &
    PID_WHATSAPP_BRIDGE=$!
}

start_telegram
start_whatsapp_bot
start_whatsapp_bridge

CLEANED=0

# per-process restart tracking (backoff: double each crash, reset after 60s uptime)
# bash 3.2 (macOS default) doesn't support declare -A, use flat vars instead
_BW_telegram=2
_BW_whatsapp_bot=2
_BW_whatsapp_bridge=2
_RT_telegram=0
_RT_whatsapp_bot=0
_RT_whatsapp_bridge=0

_backoff() {
    local k=$1
    local rt_var="_RT_$k"
    local bw_var="_BW_$k"
    local now; now=$(date +%s)
    eval "local last=\${$rt_var:-0}"
    local age=$((now - last))
    eval "local w=\${$bw_var:-2}"
    if [ $age -gt 60 ]; then
        eval "${bw_var}=2"
        return
    fi
    sleep "$w"
    local next=$((w * 2))
    [ $next -gt 30 ] && next=30
    eval "${bw_var}=$next"
    eval "${rt_var}=$now"
}

cleanup() {
    [ "$CLEANED" = "1" ] && return
    CLEANED=1
    echo "Stopping all messaging processes..."
    kill $PID_TELEGRAM $PID_WHATSAPP_BOT $PID_WHATSAPP_BRIDGE 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' SIGTERM SIGINT SIGHUP

# Poll each child independently; restart only the one that died.
while true; do
    if ! kill -0 $PID_TELEGRAM 2>/dev/null; then
        echo "telegram_bot exited, restarting it (only)..."
        _backoff telegram
        start_telegram
    fi
    if ! kill -0 $PID_WHATSAPP_BOT 2>/dev/null; then
        echo "whatsapp_bot exited, restarting it (only)..."
        _backoff whatsapp_bot
        start_whatsapp_bot
    fi
    if ! kill -0 $PID_WHATSAPP_BRIDGE 2>/dev/null; then
        echo "whatsapp_bridge exited, restarting it (only)..."
        _backoff whatsapp_bridge
        start_whatsapp_bridge
    fi
    sleep 2
done
