# Model Configuration

## Cloud-First Routing (2026-07 revamp)

**The main agent now defaults to a cloud model, not gemma4.** gemma4:12b-mlx was proving
unreliable past 2-3 chained tool calls (CSS-selector probing loops, hallucinated content,
empty `content=''` responses — known failure class for small local models under long tool-calling chains).
Both the web-UI/Telegram/WhatsApp path (`harness.py`) and the LangGraph local-agent
(`agents/local_agent_graph.py`) now default to OpenRouter's DeepSeek, with GPT-4o-mini
as an automatic fallback.

- **`clients/cloud_client.py`** — OpenRouter-backed chat client, same `chat(**kwargs)` shape
  as `ollama_client.chat()`. `DEFAULT_CLOUD_MODEL = "deepseek/deepseek-v4-flash"` (DeepSeek's own
  direct API — OpenRouter blocks DeepSeek entirely for this account, see below),
  `CLOUD_FALLBACK_MODEL = "openrouter/anthropic/claude-haiku-4.5"`. No vision/image support.
  Two providers supported via prefix: `"openrouter/<provider>/<model>"` and
  `"deepseek/<model>"` (direct) — see `_PROVIDERS` in `cloud_client.py`.
- **Dispatch by model-string prefix**, at exactly two call sites:
  `harness.py`'s `local_chat()` and `agents/local_agent_nodes.py`'s `_chat()` — any model
  string starting with `"openrouter/"` routes to `cloud_client`, everything else stays on
  `ollama_client` (local Ollama).
- **`clients/router.py`**: `classify()`/`classify_telegram()`/`classify_ide()`/
  `classify_plan()`/`model_for_intent()`/`TASK_ROUTING` all default to `CLOUD_MODEL`
  (= `cloud_client.DEFAULT_CLOUD_MODEL`) now — **except** the `ocr` intent, which stays on
  local `gemma4:12b-mlx` because it's the only multimodal model in this stack and
  `cloud_client` doesn't handle images. The intent classifier itself
  (`_llm_classify`/`warm_classifier`) also stays on local gemma4 — that's a cheap 60-token
  classification call, not the agent answering the user.
- **Failure escalation**: `agents/local_agent_nodes.py`'s `tool_node()` escalates from the
  primary cloud model to `CLOUD_FALLBACK_MODEL` after 3 consecutive tool errors (once per
  run — tracked via `LocalAgentState.escalated`), instead of just ending the run.
- **Manual override still reaches gemma4** — the UI's "Models" pill, `manual_model_pick` in
  `harness.run()`, and any explicit `model=` kwarg. This was a deliberate *global default*
  flip, not a per-intent rewrite: dial specific low-stakes intents back to local gemma4 in
  `TASK_ROUTING`/`_INTENT_MODEL_OVERRIDES` (one line each) once the cloud path is proven —
  don't rewrite the whole router to do it.
- **Config**: `OPENROUTER_API_KEY` in `tools-harness/.env` (not the project-root `.env`).
- **Reliability testing**: `tools-harness/tests/benchmark_models_tool_calling.py` now
  compares `gemma4` against both cloud models by default, with a `--repeats N` flag that
  reports `success_rate = passes/N` per case (a single pass won't catch flaky multi-step
  drift). `tools-harness/tests/verify_local_agent_e2e.py` takes `--model=` for one-off
  cloud debugging.

## Local Models (still used for classification, OCR/vision, and manual override)

**gemma4:12b-mlx** (11.9B params, Q4_K_M, 7.6 GB) — the only multimodal model in this
stack (OCR/vision), the intent classifier, and reachable via manual override.
**gemma4:e2b** (5.1B params, Q4_K_M, 7.2 GB) — warmed but not primary.
**qwen3.5:4b** (3.4 GB) — was the Tier 2/3 automation fallback before the cloud-first
revamp; still installed, no longer wired into `TASK_ROUTING` by default.

**Why 12b-mlx over e2b:** τ2-bench agentic tool use 86.4% (vs e2b 29.4%) — 3x better at tool calling. Math 89.2%, code 80%, science 84.3%. Slower (~12.6s vs 4.7s for short answers) but worth it for quality.

Configured in:
- `clients/ollama_client.py:48` — `DEFAULT_MODEL = "gemma4:12b-mlx"` (still the local default when routed there)
- `clients/cloud_client.py` — `DEFAULT_CLOUD_MODEL`/`CLOUD_FALLBACK_MODEL` (the new overall default — see above)
- `tools-harness/.env` — `OLLAMA_DEFAULT_MODEL=gemma4:12b-mlx`, `OPENROUTER_API_KEY=...`

`clients/router_models.py` (a stale, unused pre-cloud-revamp `TASK_ROUTING`/`MODEL_SPECS`
with `qwen3.5:4b` fallback tiers) was deleted 2026-07 — dead code, not wired into any
runtime path; `clients/router.py`'s own `TASK_ROUTING` is the only live one.

## gemma4 Thinking Mode (CRITICAL)

`gemma4:12b-mlx` and `gemma4:e2b` both have `thinking` capability enabled by default.
The model spends the entire `num_predict` budget on internal reasoning before
producing any output. With low `num_predict` caps, this means
**empty responses**.

**Fix**: `think=False` is now passed for ALL gemma4 calls in `_run_local` and
`_run_streaming` (clients/ollama_client.py:188, 244). Same treatment as
qwen3+tools. Verified live: gemma4:12b-mlx with think=True + num_predict=300
returned 0 words (345 thinking tokens consumed). With think=False → real answers.

## Remaining Models

```
ollama list
gemma4:12b-mlx 7.6 GB  # Primary (chat, summarizer, quality, agentic)
gemma4:e2b    7.2 GB   # Warmed (low-stakes / future use)
qwen3.5:4b    3.4 GB   # Fallback (automation when 12b-mlx busy), query rewrite, history compaction
qwen3-vl:8b   6.1 GB   # Vision (screenshot analysis)
granite3.3:2b 1.5 GB   # Misc
```

## KV Cache & Warmup

Three fixes to reduce first-request latency from 15s+ to ~0.7s (**20x improvement**):

### 1. Warmup pre-allocates KV cache (`clients/ollama_client.py:warmup()`)
Warmup passes `options={"num_ctx": 16384}` with `role: "system"` matching the casual chat path. Eliminates 5-13s KV cache re-allocation on first real request.

### 2. Consistent num_ctx across intents (`harness.py:_get_optimized_opts()`)
Removed per-intent `num_ctx` variation (was 1024/4096/16384). Now all intents keep num_ctx=16384. Prevents cache invalidation between different intent calls. Only `num_predict` varies by intent.

### 3. Casual chat with no tools (`harness.py` + `ollama_client.py`)
- Casual intent now passes `active_tools = []` — avoids developer role + "function calling" activation phrase overhead
- `_run_local()` and `_run_streaming()` set `num_predict=512` when no tools are available
- **Before**: 15-18s first response, 6-10s subsequent
- **After**: 0.7s first response, 0.3s subsequent

### Model context
- `keep_alive=-1` — model stays in GPU memory indefinitely
- `num_ctx=16384` — consistent across all calls (prevents cache re-allocation)
- `num_predict=512` — cap for non-tool calls (balanced for thorough answers without runaway output)
