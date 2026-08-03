#!/bin/bash
# Keep Docker Desktop running. launchd KeepAlive restarts this on exit.
while true; do
    if ! docker info >/dev/null 2>&1; then
        open -a Docker
        for i in $(seq 1 60); do
            docker info >/dev/null 2>&1 && break
            sleep 5
        done
    fi
    sleep 30
done
