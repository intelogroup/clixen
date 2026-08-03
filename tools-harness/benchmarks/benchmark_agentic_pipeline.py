"""
benchmark_agentic_pipeline.py

10-step deterministic agentic pipeline: qwen3.5:9b vs gemma4:e4b

Each step:
  - Has a strict Pydantic output schema (enforced via Ollama format= constrained decoding)
  - Receives accumulated context from all prior steps
  - Is scored on schema validity + semantic accuracy

Scenario: multi-intent travel/scheduling message through a full planning pipeline.
Steps chain — step N's output is injected into step N+1's context.

Run:
    cd ~/Developer/clixen/tools-harness
    python benchmark_agentic_pipeline.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Optional

import ollama
from pydantic import BaseModel, Field, ValidationError, AliasChoices, model_validator

# ── CONFIG ────────────────────────────────────────────────────────────────────

MODELS: list[str] = ["qwen3.5:9b", "gemma4:e4b"]

SYSTEM_PROMPT = (
    "You are a deterministic pipeline node in a multi-step agentic system. "
    "Return exactly one JSON object matching the schema. "
    "No markdown fences. No prose outside JSON. No extra keys. "
    "Do not invent values not present in the provided context."
)

# ── SCENARIO ─────────────────────────────────────────────────────────────────

USER_MESSAGE = (
    "Book a flight to Paris for me and Maya on June 15th, "
    "check if Marc also wants to join, send everyone a calendar invite, "
    "and remind me to pack 2 days before. "
    "Also cancel my gym appointment that day."
)

MOCK_SEARCH_RESULTS = [
    {"index": 0, "snippet": "Air France AF1234 Paris CDG June 15 dep 08:30, 2 seats, $420/pp"},
    {"index": 1, "snippet": "Delta DL789 Paris CDG June 15 dep 14:00, 5 seats, $380/pp"},
    {"index": 2, "snippet": "Marc Dupont — marc.dupont@example.com — last seen 2h ago"},
    {"index": 3, "snippet": "Paris weather June 15: sunny 24°C"},
    {"index": 4, "snippet": "Gym Power Yoga June 15 07:00 — cancellation requires 24h notice"},
]

# ── PYDANTIC SCHEMAS ──────────────────────────────────────────────────────────

class Intent(BaseModel):
    label: str
    target: Optional[str] = None

class IntentSplit(BaseModel):
    """Step 1 — split user message into atomic action intents."""
    intents: list[Intent]
    raw_intent_count: int


class Entity(BaseModel):
    type: str        # person | date | location | action | object
    value: str
    intent_ref: str  # label from step 1

class EntityExtract(BaseModel):
    """Step 2 — extract all entities, referenced to intents."""
    entities: list[Entity]


class MissingField(BaseModel):
    intent_ref: str
    field: str
    blocking: bool   # True = action cannot proceed without it

class MissingFields(BaseModel):
    """Step 3 — identify missing required fields per intent."""
    # alias: models often output "missing_fields" instead of "missing"
    missing: list[MissingField] = Field(
        validation_alias=AliasChoices("missing", "missing_fields")
    )
    complete_intents: list[str] = Field(
        validation_alias=AliasChoices("complete_intents", "complete_intent_labels")
    )


class SearchQuery(BaseModel):
    intent_ref: str
    query: str
    backend: str   # web | contacts | calendar
    priority: int  # 1=high 2=medium 3=low

class SearchPlan(BaseModel):
    """Step 4 — plan searches needed to fill missing data."""
    # alias: models sometimes wrap in {"search_plan": {"searches": [...]}}
    searches: list[SearchQuery] = Field(
        validation_alias=AliasChoices("searches", "search_queries", "queries")
    )

    @model_validator(mode="before")
    @classmethod
    def unwrap_nested(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("search_plan", "plan", "search"):
                if key in data and isinstance(data[key], dict) and "searches" in data[key]:
                    return {**data, "searches": data[key]["searches"]}
        return data


class RankedResult(BaseModel):
    intent_ref: str
    best_index: int
    confidence: float = Field(ge=0.0, le=1.0)

class SnippetRank(BaseModel):
    """Step 5 — pick best snippet per search query from mock results."""
    # alias: models output "searches", "results", or "ranked_results"
    rankings: list[RankedResult] = Field(
        validation_alias=AliasChoices("rankings", "ranked_results", "results", "searches")
    )


class PlanStep(BaseModel):
    step_id: str
    action: str
    params: dict[str, Any]
    reversible: bool
    depends_on: list[str]

class ExecutionPlan(BaseModel):
    """Step 6 — full execution plan assembled from prior steps."""
    # alias: models output "plan" instead of "steps"
    steps: list[PlanStep] = Field(
        validation_alias=AliasChoices("steps", "plan", "execution_steps", "actions")
    )
    total_steps: int
    irreversible_count: int = Field(
        validation_alias=AliasChoices("irreversible_count", "irreversible")
    )


class Conflict(BaseModel):
    # alias: models sometimes emit step_ids as a comma-separated string
    step_ids: list[str] = Field(
        validation_alias=AliasChoices("step_ids", "steps", "affected_steps")
    )
    description: str
    severity: str   # low | medium | high

    @model_validator(mode="before")
    @classmethod
    def coerce_step_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            sid = data.get("step_ids") or data.get("steps") or data.get("affected_steps")
            if isinstance(sid, str):
                data = {**data, "step_ids": [s.strip() for s in sid.replace(",", " ").split() if s.strip()]}
        return data

class ConflictReport(BaseModel):
    """Step 7 — detect conflicts between plan steps."""
    conflicts: list[Conflict] = Field(
        validation_alias=AliasChoices("conflicts", "conflict_list", "issues")
    )
    conflict_free: bool = Field(
        validation_alias=AliasChoices("conflict_free", "no_conflicts", "is_conflict_free")
    )


class RiskReport(BaseModel):
    """Step 8 — classify each plan step by risk level."""
    high_risk: list[str]              # step_ids
    requires_confirmation: list[str]  # step_ids
    safe_to_auto: list[str]           # step_ids


class ConfirmationDraft(BaseModel):
    """Step 9 — user-facing confirmation summary."""
    message: str
    action_count: int
    word_count: int


class ExecutionRoute(BaseModel):
    """Step 10 — final routing: auto-execute vs needs-approval vs blocked."""
    auto_execute: list[str]
    needs_approval: list[str]
    blocked: list[str]
    reasoning: str


STEP_SCHEMAS: list[type[BaseModel]] = [
    IntentSplit, EntityExtract, MissingFields, SearchPlan, SnippetRank,
    ExecutionPlan, ConflictReport, RiskReport, ConfirmationDraft, ExecutionRoute,
]
STEP_NAMES = [cls.__name__ for cls in STEP_SCHEMAS]

# ── PROMPTS ───────────────────────────────────────────────────────────────────

def _ctx(ctx: dict, keys: list[str]) -> str:
    parts = []
    for k in keys:
        if k in ctx:
            parts.append(f"# {k}\n{json.dumps(ctx[k], indent=2)}")
    return "\n\n".join(parts)

def _p1(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        "Split this message into atomic action intents.\n"
        "Valid labels: book_flight, contact_check, send_calendar_invite, "
        "create_reminder, cancel_appointment, send_message, casual.\n"
        "Set target to the main entity for that intent (e.g. 'Paris' for book_flight).\n"
        "Set raw_intent_count to the exact count of intents found."
    )

def _p2(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1'])}\n\n"
        "Extract all entities from the message.\n"
        "Entity types: person, date, location, action, object.\n"
        "intent_ref must match a label from step1."
    )

def _p3(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1', 'step2'])}\n\n"
        "Identify required fields that are MISSING per intent.\n"
        "Required fields by intent:\n"
        "  book_flight: origin, destination, date, passengers, return_date\n"
        "  contact_check: contact_name\n"
        "  send_calendar_invite: recipients, date, event_title\n"
        "  create_reminder: title, due_at\n"
        "  cancel_appointment: appointment_title\n"
        "blocking=true if the action cannot proceed without this field.\n"
        "complete_intents: list intent labels where ALL required fields are present.\n\n"
        "# Expected output format\n"
        '{"missing": [{"intent_ref": "book_flight", "field": "origin", "blocking": true}, '
        '{"intent_ref": "book_flight", "field": "return_date", "blocking": false}], '
        '"complete_intents": ["contact_check"]}'
    )

def _p4(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1', 'step3'])}\n\n"
        "Plan searches needed to fill missing or unverified data.\n"
        "backend choices: web, contacts, calendar.\n"
        "priority: 1=blocking (cannot proceed without), 2=important, 3=optional.\n\n"
        "# Expected output format\n"
        '{"searches": [{"intent_ref": "book_flight", "query": "Paris CDG flights June 15", '
        '"backend": "web", "priority": 1}, '
        '{"intent_ref": "contact_check", "query": "Marc contact details", '
        '"backend": "contacts", "priority": 2}]}'
    )

def _p5(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step4'])}\n\n"
        f"Search results (0-based index):\n{json.dumps(ctx['search'], indent=2)}\n\n"
        "For each planned search, pick the best matching snippet by index (0–4).\n"
        "intent_ref must match a label from step1. confidence: 0.0–1.0.\n\n"
        "# Expected output format\n"
        '{"rankings": [{"intent_ref": "book_flight", "best_index": 1, "confidence": 0.9}, '
        '{"intent_ref": "contact_check", "best_index": 2, "confidence": 0.95}]}'
    )

def _p6(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1', 'step2', 'step5'])}\n\n"
        "Assemble the full execution plan.\n"
        "step_id: unique short ids like s1, s2, …\n"
        "reversible=false for: flight bookings, sent messages, calendar invites, cancellations.\n"
        "depends_on: list step_ids that must complete first.\n"
        "Set total_steps and irreversible_count.\n\n"
        "# Expected output format\n"
        '{"steps": [{"step_id": "s1", "action": "book_flight", '
        '"params": {"destination": "Paris", "date": "June 15"}, '
        '"reversible": false, "depends_on": []}, '
        '{"step_id": "s2", "action": "send_calendar_invite", '
        '"params": {"recipients": ["Maya", "Marc"]}, "reversible": false, "depends_on": ["s1"]}], '
        '"total_steps": 5, "irreversible_count": 3}'
    )

def _p7(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step6'])}\n\n"
        "Detect conflicts between execution plan steps.\n"
        "A conflict exists when: two steps affect the same resource incompatibly, "
        "dependency cycles exist, or timing constraints are violated "
        "(e.g. cancellation policy requires 24h notice but action is same-day).\n"
        "conflict_free=true only if conflicts list is empty.\n\n"
        "# Expected output format\n"
        '{"conflicts": [{"step_ids": ["s5"], "description": '
        '"Gym cancellation requires 24h notice but is scheduled same-day", "severity": "medium"}], '
        '"conflict_free": false}'
    )

def _p8(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step6', 'step7'])}\n\n"
        "Classify each plan step by risk using step_ids from step6.\n"
        "high_risk: irreversible or destructive actions.\n"
        "requires_confirmation: side-effect actions needing explicit user approval.\n"
        "safe_to_auto: idempotent or read-only steps that need no approval.\n"
        "Every step_id from step6 must appear in exactly one list."
    )

def _p9(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step6', 'step8'])}\n\n"
        "Draft a concise user-facing confirmation message summarising what will happen.\n"
        "message: plain prose, under 60 words, no bullet points, no markdown.\n"
        "action_count: number of distinct actions in the plan.\n"
        "word_count: exact word count of your message field."
    )

def _p10(ctx: dict) -> str:
    return (
        f'User message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step6', 'step8', 'step9'])}\n\n"
        "Produce final execution routing using step_ids from step6.\n"
        "auto_execute: steps safe to run immediately without user input.\n"
        "needs_approval: steps requiring explicit confirmation before running.\n"
        "blocked: steps that cannot proceed (missing data or unresolved conflict).\n"
        "reasoning: one sentence explaining the routing decisions."
    )

STEP_PROMPTS = [_p1, _p2, _p3, _p4, _p5, _p6, _p7, _p8, _p9, _p10]

# ── SCORERS ───────────────────────────────────────────────────────────────────
# Returns (score: int, max_score: int, notes: list[str])

def _s1(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    intents = data.get("intents", [])
    labels = {i.get("label", "") for i in intents}
    expected = {"book_flight", "contact_check", "send_calendar_invite", "create_reminder", "cancel_appointment"}
    found = expected & labels
    score = len(found)
    if data.get("raw_intent_count", -1) == len(intents):
        score += 1
    else:
        notes.append(f"raw_intent_count={data.get('raw_intent_count')} vs actual {len(intents)}")
    missing = expected - found
    if missing:
        notes.append(f"missed intents: {missing}")
    return score, 6, notes

def _s2(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    entities = data.get("entities", [])
    values_str = json.dumps(entities).lower()
    score = 0
    for needle in ["maya", "paris", "june 15", "marc", "gym"]:
        if needle in values_str:
            score += 1
        else:
            notes.append(f"entity not found: {needle!r}")
    return score, 5, notes

def _s3(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    dump = json.dumps(data).lower()
    score = 0
    if "return" in dump:
        score += 2
    else:
        notes.append("return_date not flagged as missing")
    if "origin" in dump:
        score += 1
    else:
        notes.append("origin not flagged as missing")
    ci = [c.lower() for c in data.get("complete_intents", [])]
    if any("contact" in c for c in ci):
        score += 1
    else:
        notes.append("contact_check should be complete (contact_name=Marc is in message)")
    return score, 4, notes

def _s4(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    dump = json.dumps(data).lower()
    score = 0
    if "paris" in dump or "flight" in dump:
        score += 2
    else:
        notes.append("no flight/Paris search planned")
    if "marc" in dump:
        score += 1
    else:
        notes.append("no Marc contact search planned")
    if "web" in dump:
        score += 1
    else:
        notes.append("no web backend search")
    return score, 4, notes

def _s5(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    rankings = data.get("rankings", [])
    score = 0
    for r in rankings:
        ref = r.get("intent_ref", "").lower()
        idx = r.get("best_index", -1)
        conf = r.get("confidence", -1.0)
        if not (0.0 <= conf <= 1.0):
            notes.append(f"confidence {conf} out of range for {ref}")
            continue
        if "book" in ref or "flight" in ref:
            if idx in (0, 1):
                score += 2
            else:
                notes.append(f"unexpected flight snippet index {idx} (expected 0 or 1)")
        if "contact" in ref or "marc" in ref or "check" in ref:
            if idx == 2:
                score += 2
            else:
                notes.append(f"unexpected contact snippet index {idx} (expected 2)")
    return min(score, 4), 4, notes

def _s6(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    steps = data.get("steps", [])
    total = data.get("total_steps", 0)
    irrev = data.get("irreversible_count", 0)
    score = 0
    if len(steps) >= 4:
        score += 2
    else:
        notes.append(f"only {len(steps)} plan steps (expected ≥4)")
    if total == len(steps):
        score += 1
    else:
        notes.append(f"total_steps={total} vs actual {len(steps)}")
    if irrev >= 2:
        score += 1
    else:
        notes.append(f"irreversible_count={irrev} (expected ≥2: flight + calendar invite)")
    return score, 4, notes

def _s7(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    dump = json.dumps(data).lower()
    cf = data.get("conflict_free")
    score = 0
    gym_conflict = any(
        kw in dump for kw in ("24h", "24 h", "notice", "gym", "cancel", "yoga")
    )
    if gym_conflict:
        score += 2
        notes.append("gym 24h-notice conflict detected")
    if isinstance(cf, bool):
        # conflict_free=False is correct if gym conflict detected; True if not
        if gym_conflict and cf is False:
            score += 1
        elif not gym_conflict and cf is True:
            score += 1
        else:
            notes.append(f"conflict_free={cf} inconsistent with conflict detection")
    else:
        notes.append("conflict_free missing or not bool")
    return min(score, 3), 3, notes

def _s8(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    hr = data.get("high_risk", [])
    rc = data.get("requires_confirmation", [])
    sa = data.get("safe_to_auto", [])
    score = 0
    if len(hr) + len(rc) >= 2:
        score += 2
    else:
        notes.append("fewer than 2 steps marked risky/need-confirmation")
    if len(sa) >= 1:
        score += 1
    else:
        notes.append("nothing marked safe_to_auto (reminder should be)")
    overlap = set(hr) & set(rc) | set(hr) & set(sa) | set(rc) & set(sa)
    if not overlap:
        score += 1
    else:
        notes.append(f"step_ids in multiple risk lists: {overlap}")
    return score, 4, notes

def _s9(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    msg = data.get("message", "")
    ac = data.get("action_count", 0)
    wc = data.get("word_count", 0)
    score = 0
    actual_wc = len(msg.split())
    if 10 <= actual_wc <= 80:
        score += 2
    else:
        notes.append(f"message word count {actual_wc} outside 10–80")
    if ac >= 3:
        score += 1
    else:
        notes.append(f"action_count={ac} seems low for 5 intents")
    if abs(wc - actual_wc) <= 5:
        score += 1
    else:
        notes.append(f"word_count={wc} but actual={actual_wc}")
    return score, 4, notes

def _s10(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    ae = data.get("auto_execute", [])
    na = data.get("needs_approval", [])
    bl = data.get("blocked", [])
    r = data.get("reasoning", "")
    score = 0
    if len(na) >= 1:
        score += 2
    else:
        notes.append("needs_approval empty — flight booking must require approval")
    if len(ae) >= 1:
        score += 1
    else:
        notes.append("auto_execute empty — create_reminder should be auto-safe")
    if r.strip():
        score += 1
    else:
        notes.append("reasoning missing")
    return score, 4, notes

STEP_SCORERS = [_s1, _s2, _s3, _s4, _s5, _s6, _s7, _s8, _s9, _s10]

# ── RUNNER ────────────────────────────────────────────────────────────────────

def _opts(model: str) -> dict:
    if model.startswith("qwen3"):
        # Non-thinking mode: official HF docs recommendation.
        # DO NOT use temperature=0 (greedy decoding) — causes degradation + repetitions.
        return {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "repetition_penalty": 1.05}
    if model.startswith("gemma"):
        # Gemma 4 model card: use these for all use cases including structured output.
        return {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    return {"temperature": 0.7}

def _think(model: str, step: int = 1) -> bool | None:
    # qwen3: always False — think blocks compound to 30+ min over 10 agentic steps
    if model.startswith("qwen3"):
        return False
    if model.startswith("gemma"):
        # Steps 6–8 (ExecutionPlan, ConflictReport, RiskReport) benefit from reasoning:
        # they require cross-step synthesis and constraint checking.
        # Steps 1–5 and 9–10 are straightforward extraction/routing — no thinking needed.
        return step in (6, 7, 8)
    return None

def run_pipeline(model: str) -> list[dict]:
    client = ollama.Client()
    ctx: dict[str, Any] = {"msg": USER_MESSAGE, "search": MOCK_SEARCH_RESULTS}
    results: list[dict] = []

    for i, (schema_cls, prompt_fn, scorer) in enumerate(
        zip(STEP_SCHEMAS, STEP_PROMPTS, STEP_SCORERS)
    ):
        step_num = i + 1
        prompt_text = prompt_fn(ctx)
        # qwen3: append /no_think as soft switch (belt-and-suspenders alongside think=False)
        if model.startswith("qwen3"):
            prompt_text = prompt_text + "\n/no_think"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ]
        think_val = _think(model, step=step_num)
        chat_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": schema_cls.model_json_schema(),
            "options": _opts(model),
            "keep_alive": -1,
        }
        if think_val is not None:
            chat_kwargs["think"] = think_val

        t0 = time.time()
        raw = ""
        schema_ok = False
        schema_err: Optional[str] = None
        data: dict = {}

        try:
            resp = client.chat(**chat_kwargs)
            raw = resp.message.content or ""
            elapsed = time.time() - t0

            try:
                parsed = schema_cls.model_validate_json(raw)
                data = parsed.model_dump()
                schema_ok = True
            except ValidationError as ve:
                schema_err = str(ve)[:160]
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
            except Exception as e:
                schema_err = str(e)[:160]
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}

        except Exception as e:
            elapsed = time.time() - t0
            schema_err = f"API error: {str(e)[:120]}"

        sem_score, sem_max, sem_notes = scorer(data)
        ctx[f"step{step_num}"] = data

        status = "PASS" if schema_ok else "FAIL"
        print(
            f"  step{step_num:02d} {STEP_NAMES[i]:<22} {status}  "
            f"{sem_score}/{sem_max}pts  {elapsed:.1f}s",
            flush=True,
        )

        results.append({
            "step": step_num,
            "name": STEP_NAMES[i],
            "schema_ok": schema_ok,
            "schema_err": schema_err,
            "sem_score": sem_score,
            "sem_max": sem_max,
            "sem_notes": sem_notes,
            "elapsed_s": round(elapsed, 2),
        })

    return results

# ── REPORT ────────────────────────────────────────────────────────────────────

def print_report(all_results: dict[str, list[dict]]) -> None:
    models = list(all_results.keys())
    W = 76

    print("\n" + "=" * W)
    print("AGENTIC PIPELINE BENCHMARK  —  " + "  vs  ".join(models))
    print("=" * W)

    # Column header
    hdr = f"{'#':<3} {'Step':<22}"
    for m in models:
        tag = m.split(":")[0][-8:]  # last 8 chars of model name
        hdr += f"  {tag:>8} {'sem':>5} {'ms':>6}"
    print(hdr)
    print("-" * W)

    tot_sem:    dict[str, int]   = {m: 0   for m in models}
    tot_max:    dict[str, int]   = {m: 0   for m in models}
    tot_ok:     dict[str, int]   = {m: 0   for m in models}
    tot_time:   dict[str, float] = {m: 0.0 for m in models}

    for i in range(10):
        row = f"{i+1:<3} {STEP_NAMES[i]:<22}"
        for m in models:
            r = all_results[m][i]
            sym = "PASS" if r["schema_ok"] else "FAIL"
            sem = f"{r['sem_score']}/{r['sem_max']}"
            ms  = f"{r['elapsed_s']*1000:.0f}"
            row += f"  {sym:>8} {sem:>5} {ms:>6}"
            tot_sem[m]  += r["sem_score"]
            tot_max[m]  += r["sem_max"]
            tot_time[m] += r["elapsed_s"]
            if r["schema_ok"]:
                tot_ok[m] += 1
        print(row)

    print("-" * W)
    row = f"{'TOT':<3} {'':22}"
    for m in models:
        sch = f"{tot_ok[m]}/10"
        sem = f"{tot_sem[m]}/{tot_max[m]}"
        ms  = f"{tot_time[m]*1000:.0f}"
        row += f"  {sch:>8} {sem:>5} {ms:>6}"
    print(row)

    print("\nSCORES")
    for m in models:
        s_pct  = tot_ok[m]  / 10   * 100
        sm_pct = tot_sem[m] / tot_max[m] * 100 if tot_max[m] else 0
        chain_breaks = sum(1 for r in all_results[m] if not r["schema_ok"])
        print(
            f"  {m:<24}  schema {s_pct:5.1f}%  semantic {sm_pct:5.1f}%  "
            f"total {tot_time[m]:.1f}s  chain_breaks {chain_breaks}"
        )

    sem_winner    = max(models, key=lambda m: tot_sem[m])
    schema_winner = max(models, key=lambda m: tot_ok[m])
    speed_winner  = min(models, key=lambda m: tot_time[m])
    print(f"\n  semantic winner  → {sem_winner}")
    print(f"  schema winner    → {schema_winner}")
    print(f"  speed winner     → {speed_winner}")

    # Notes per failing step
    any_notes = any(
        r["sem_notes"] or not r["schema_ok"]
        for m in models
        for r in all_results[m]
    )
    if any_notes:
        print("\nISSUES")
        for i in range(10):
            for m in models:
                r = all_results[m][i]
                notes = list(r["sem_notes"])
                if not r["schema_ok"] and r["schema_err"]:
                    notes.insert(0, f"schema: {r['schema_err'][:100]}")
                if notes:
                    print(f"  step{i+1} {STEP_NAMES[i]} [{m.split(':')[0]}]")
                    for n in notes:
                        print(f"    • {n}")

# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    all_results: dict[str, list[dict]] = {}
    for model in MODELS:
        print(f"\n{'─'*50}")
        print(f"Model: {model}")
        print(f"{'─'*50}")
        all_results[model] = run_pipeline(model)

    print_report(all_results)
