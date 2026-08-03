#!/bin/bash

MODEL="gemma4:e2b"
PROMPT="Explain quantum entanglement in one paragraph."
# Pre-escape the prompt for JSON
JSON_DATA="{\"model\": \"$MODEL\", \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT\"}], \"stream\": false}"

run_calls() {
    CONCURRENCY=$1
    echo "--- Testing Concurrency: $CONCURRENCY ---"
    
    # Use a subshell to run curls in parallel and print status + time
    for i in $(seq 1 $CONCURRENCY); do
        curl -s -o /dev/null -w "Call $i: HTTP %{http_code} | Time: %{time_total}s\n" \
             -X POST http://127.0.0.1:11434/api/chat \
             -H "Content-Type: application/json" \
             -d "$JSON_DATA" &
    done
    
    wait
    echo ""
}

echo "Starting Concurrency Benchmark for $MODEL"
echo "Target: http://127.0.0.1:11434"
echo ""

# Warmup to ensure model is loaded
echo "Warming up (loading model)..."
curl -s -o /dev/null -w "Warmup: HTTP %{http_code} | Time: %{time_total}s\n" \
     -X POST http://127.0.0.1:11434/api/chat \
     -H "Content-Type: application/json" \
     -d "$JSON_DATA"

run_calls 1
run_calls 2
run_calls 4
run_calls 6
