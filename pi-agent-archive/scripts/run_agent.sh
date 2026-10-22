#!/usr/bin/env bash
# run_agent.sh — drive pi (PI Agent) to research + write a tracker briefing.
# Called from run.sh (cron). Works in agent_workspace, produces a dated .md in out/.
#
# pi reads OPENROUTER_API_KEY from env. Uses --print for non-interactive (one-shot).
# Env: PI_MODEL (OpenRouter model id) optional.
#
# Memory/context management:
#   - builds a folded context block (prior briefings + memory + recent articles)
#     via recall.py --context and injects it into the agent system prompt
#   - after the run, folds a compact run summary into memory via memory.py fold

set -euo pipefail

ROOT="/root/pi-agent"
WORK="${ROOT}/agent_workspace"
OUT="${ROOT}/out"
TODAY="$(date +%Y-%m-%d)"
STAMP="$(date +%Y-%m-%d-%H%M)"
PI_MODEL="${PI_MODEL:-openrouter/deepseek/deepseek-v4-flash-latest}"
PY="${ROOT}/.venv/bin/python"

# Load API keys (Exa/Tavily/Brave/Firecrawl/OpenRouter) so pi and tools have them
if [ -f "${ROOT}/.env" ]; then
  set -a
  . "${ROOT}/.env"
  set +a
fi

mkdir -p "$WORK" "$OUT"

# Folded context: what the agent should know from prior runs (semantic recall)
# over today's topics, plus durable memory facts. Keeps each run lean.
RECALL_TOPICS="${RECALL_TOPICS:-AI science technology model release breakthrough research discovery}"
RECALL_CTX="$("$PY" "$ROOT/scripts/recall.py" --context "$RECALL_TOPICS" --k 6 2>/dev/null || echo "(recall unavailable)")"
MEMORY_TOP="$(set -a; . "$ROOT/.env" 2>/dev/null; set +a; "$PY" "$ROOT/scripts/memory.py" recall --limit 6 2>/dev/null || echo "(memory unavailable)")"

# Agent task: read from tracker DB + prior context, produce a dated briefing markdown.
TASK_FILE="${WORK}/task-${STAMP}.md"
cat > "$TASK_FILE" <<TASKEOF
You are an autonomous AI / science / technology news tracker on an Intel N100.

Write a focused daily briefing covering the most notable recent developments.
Use the tools available to you (read, bash, edit, write) to:
1. Inspect the tracker knowledge base for what has been collected.
2. Optionally do a fresh web search for today's top AI / science / tech news
   (only for genuinely new items — avoid re-reporting what prior recall already covered).
3. Produce a clean, factual markdown briefing with sections for AI, Science, and Tech,
   each listing 2-4 concrete items with: headline, 1-2 sentence summary, and source URL.
4. Write the briefing to the path given in OUT_PATH.
5. Prefer concrete verifiable facts over speculation. Cite sources. Keep total under 900 words.

## Prior knowledge recall (semantic) — from the tracker's own past briefings + memory
Avoid duplicating these. New briefing should extend, update, or skip already-covered items.
$RECALL_CTX

## Durable memory facts
$MEMORY_TOP

Knowledge base (SQLite with FTS5 + LanceDB vector store):
- SQLite DB: /root/pi-agent/data/tracker.db  (table articles)
- Semantic recall: /root/pi-agent/.venv/bin/python /root/pi-agent/scripts/recall.py "<topic>" --k 5
- Fresh searches: /root/pi-agent/.venv/bin/python /root/pi-agent/scripts/search_store.py --query "<topic>" --limit 8
TASKEOF

OUT_PATH="${OUT}/briefing-${STAMP}.md"
echo "OUT_PATH=${OUT_PATH}" >> "$TASK_FILE"

export PATH="$PATH:$(npm config get prefix)/bin"

# Run pi one-shot with --approve for non-interactive tool use.
cd "$WORK" || exit 1
pi --print \
   --approve \
   --provider openrouter \
   --model "$PI_MODEL" \
   --append-system-prompt "$(cat "$TASK_FILE")" \
   "Produce today's briefing now. Write the file to ${OUT_PATH}." \
   > "${WORK}/pi-run-${STAMP}.log" 2>&1 || {
       echo "[agent] pi run failed (exit $?), see ${WORK}/pi-run-${STAMP}.log" >&2
       exit 1
   }

if [ -f "$OUT_PATH" ]; then
    echo "[agent] wrote $OUT_PATH ($(wc -w < "$OUT_PATH") words)"

    # LLM verification gate: last-pass check before the briefing is accepted.
    # Fail -> alert + non-zero exit (run.sh then alerts and skips delivery).
    if VERIFY_OUT="$("$PY" "$ROOT/scripts/verify.py" "$OUT_PATH" 2>&1)"; then
        echo "[verify] $VERIFY_OUT"
    else
        echo "[verify] FAILED: $VERIFY_OUT" >&2
        "$PY" "$ROOT/scripts/notify.py" alert \
            "Briefing ${STAMP} rejected by verifier: ${VERIFY_OUT}" \
            >/dev/null 2>&1 || true
        exit 1
    fi

    # Fold a compact run summary into durable memory for future context.
    SUMMARY_HEAD="$(head -3 "$OUT_PATH" 2>/dev/null | tr '\n' ' ' | cut -c1-200)"
    "$PY" "$ROOT/scripts/memory.py" fold "Ran briefing $TODAY: $SUMMARY_HEAD" --topic run 2>/dev/null || true

    # Record last-run state.
    set -a; . "$ROOT/.env"; set +a
    "$PY" "$ROOT/scripts/memory.py" state --set last_run "$TODAY" 2>/dev/null || true
    "$PY" "$ROOT/scripts/memory.py" state --set last_briefing "$OUT_PATH" 2>/dev/null || true

    exit 0
else
    echo "[agent] pi completed but no output file at $OUT_PATH; check log" >&2
    exit 1
fi