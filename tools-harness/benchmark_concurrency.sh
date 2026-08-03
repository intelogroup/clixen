#!/bin/bash

MODEL="gemma4:e2b"
PROMPT="Explain quantum entanglement in one paragraph."
JSON_DATA=$(cat <<EOF
{
  "model": "$MODEL",
  "messages": [{"role": "user", "content": "$PROMPT"}],
  "stream": false
}
EOF
)

run_calls() {
    CONCURRENCY=$1
    echo "--- Testing Concurrency: $CONCURRENCY ---"
    
    START=$(python3 -c 'import time; print(time.time())')
    
    # Run concurrent curls
    PIDS=()
    for i in $(seq 1 $CONCURRENCY); do
        curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:11434/api/chat -d "$JSON_DATA" &
        PIDS+=($!)
    done
    
    # Wait for all to finish
    for pid in "${PIDS[@]}"; do
        wait $pid
    done
    
    END=$(python3 -c 'import time; print(time.time())')
    ELAPSED=$(python3 -c "print($END - $START)")
    
    echo "Total Time: ${ELAPSED}s"
    echo ""
}

# Warmup
echo "Warming up..."
curl -s -X POST http://127.0.0.1:11434/api/chat -d "$JSON_DATA" > /dev/null

run_calls 1
run_calls 2
run_calls 4
run_calls 6
