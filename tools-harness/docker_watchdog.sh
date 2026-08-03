#!/bin/bash
# Keep Docker running. launchd KeepAlive restarts this on exit.
# macOS: `open -a Docker`. Linux: systemctl start docker (if permitted).
while true; do
    if ! docker info >/dev/null 2>&1; then
        if [[ "$(uname)" == "Darwin" ]]; then
            open -a Docker
        else
            systemctl start docker >/dev/null 2>&1 || true
        fi
        for i in $(seq 1 60); do
            docker info >/dev/null 2>&1 && break
            sleep 5
        done
    fi
    sleep 30
done
