#!/usr/bin/env bash
# A throwaway Chrome with a debugging port, for testing the orb — never for shipping.
#
# Why this exists: auditing console/network/DOM needs CDP, and the shipped DMG must
# never ask a user to relaunch their browser with a debug port open. So the two are
# separated: this script is developer-only, runs a *separate* profile under /tmp,
# and leaves the user's own Chrome untouched. Point the harness at it with:
#
#     eval "$(scripts/dev_cdp_chrome.sh --print-env)"   # exports CLIXEN_BROWSER_CDP_URL
#     python -c 'from tools.registry import execute_tool; print(execute_tool("browser_open", {}))'
#
#   scripts/dev_cdp_chrome.sh                 # launch (port 9333, fresh profile)
#   scripts/dev_cdp_chrome.sh --url https://www.theguardian.com/us
#   scripts/dev_cdp_chrome.sh --print-env     # just the exports
#   scripts/dev_cdp_chrome.sh --kill          # stop it
set -euo pipefail

PORT="${CLIXEN_DEV_CDP_PORT:-9333}"
PROFILE="${CLIXEN_DEV_CDP_PROFILE:-/tmp/clixen-cdp-profile}"
CHROME="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
URL=""

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --print-env) echo "export CLIXEN_BROWSER_CDP_URL=http://127.0.0.1:${PORT}"; exit 0 ;;
    --kill)
      # Match the profile path, not DevToolsActivePort: that file is written into
      # the profile only sometimes, which made --kill report "none" while the
      # browser was still running (caught on the first use).
      if pgrep -f "user-data-dir=${PROFILE}" >/dev/null 2>&1; then
        pkill -f "user-data-dir=${PROFILE}" 2>/dev/null || true
        echo "stopped the dev CDP Chrome (${PROFILE})"
      else
        echo "no dev CDP Chrome for ${PROFILE}"
      fi
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ ! -x "$CHROME" ]; then
  echo "Chrome not found at: $CHROME (set CHROME_BIN)" >&2
  exit 1
fi

mkdir -p "$PROFILE"
# A separate --user-data-dir is the whole point: the user's profile is never opened
# with a debugging port. --no-first-run keeps it quiet.
"$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port="$PORT" \
  --no-first-run --no-default-browser-check \
  --disable-background-networking \
  ${URL:+"$URL"} >/dev/null 2>&1 &

for _ in $(seq 1 40); do
  if curl -fsS -m 1 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "dev CDP Chrome ready on http://127.0.0.1:${PORT} (profile: ${PROFILE})"
    echo "export CLIXEN_BROWSER_CDP_URL=http://127.0.0.1:${PORT}"
    exit 0
  fi
  sleep 0.25
done
echo "Chrome did not open a debugging port on ${PORT}" >&2
exit 1
