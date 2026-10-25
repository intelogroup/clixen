#!/usr/bin/env bash
# Dev convenience: give the unpacked extension its localhost token so it can poll.
# The generated file is gitignored and only read by the dev build; a shipped build
# has no such file and the user pastes the token in Options once.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
token=$(security find-generic-password -s clixen.localhost.token -w 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])')
printf '{"core":"http://127.0.0.1:9234","token":"%s"}\n' "$token" > "$here/extension/token.local.json"
chmod 600 "$here/extension/token.local.json"
echo "wrote extension/token.local.json (mode 600)"
