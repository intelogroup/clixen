#!/usr/bin/env bash
# Run Scrapling's MCP server from an ISOLATED environment.
#
# Why a script, and why a second venv: the MCP server is not something Clixen needs —
# Clixen *is* the agent, and its own tools already wrap scrapling. This exists for the
# other direction: letting an external agent (Claude Desktop, Cursor, another
# harness) use scrapling through the browser stack already installed here.
#
# It cannot live in the main venv. Clixen pins mcp<2.0.0 (fastmcp-slim needs it),
# scrapling[ai] requires mcp>=2.0.0. Installing both into one environment breaks the
# other MCP tooling, so the server gets its own venv — the same pattern the repo
# already uses for Surya's daemon.
#
#   scripts/scrapling_mcp.sh                       # stdio (what MCP clients expect)
#   scripts/scrapling_mcp.sh --http --port 8811    # streamable HTTP for a remote client
#
# The server can drive and screenshot pages, so treat its port like any other local
# capability: bind 127.0.0.1, never expose it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-mcp"

if [ ! -x "$VENV/bin/scrapling" ]; then
  echo "== creating $VENV (mcp>=2 lives here, not in the app venv)" >&2
  uv venv "$VENV" --python 3.12 >&2
  uv pip install --python "$VENV/bin/python" "scrapling[ai]>=0.5" >&2
fi

# Keep browser profiles and the adaptive DB beside the app's own data, never inside
# the package directory (read-only in a signed .app).
: "${CLIXEN_DATA_DIR:=$HOME/Library/Application Support/Clixen}"
mkdir -p "$CLIXEN_DATA_DIR/scrapling"
export SCRAPLING_STORAGE_DIR="${SCRAPLING_STORAGE_DIR:-$CLIXEN_DATA_DIR/scrapling}"

# Over HTTP the server refuses to start without a bearer token, because anyone who
# can reach the port can drive every tool. Generate one, keep it in the Keychain
# beside the app's own token, and pass it through the environment so it never shows
# up in `ps`.
if printf '%s\n' "$*" | grep -q -- '--http'; then
  if [ -z "${SCRAPLING_MCP_AUTH_TOKEN:-}" ]; then
    SCRAPLING_MCP_AUTH_TOKEN="$(security find-generic-password -s clixen.scrapling.mcp -w 2>/dev/null || true)"
    if [ -z "$SCRAPLING_MCP_AUTH_TOKEN" ]; then
      SCRAPLING_MCP_AUTH_TOKEN="$(openssl rand -hex 24)"
      security add-generic-password -a "$USER" -s clixen.scrapling.mcp \
        -w "$SCRAPLING_MCP_AUTH_TOKEN" -U >/dev/null 2>&1 || true
      echo "generated an MCP bearer token and stored it in the Keychain" >&2
    fi
  fi
  export SCRAPLING_MCP_AUTH_TOKEN
fi

exec "$VENV/bin/scrapling" mcp "$@"
