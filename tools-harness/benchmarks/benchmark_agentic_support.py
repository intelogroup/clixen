"""
benchmark_agentic_support.py

10-step deterministic agentic pipeline: qwen3.5:9b vs gemma4:e4b
Domain: customer support escalation triage

Scenario: customer reports a double charge + missing tracking + late delivery.
Each step's Pydantic-validated output feeds the next step's context.

Run:
    cd ~/Developer/clixen/tools-harness
    python benchmark_agentic_support.py
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import ollama
from pydantic import BaseModel, Field, ValidationError, AliasChoices, model_validator

# ── CONFIG ────────────────────────────────────────────────────────────────────

MODELS: list[str] = ["qwen3.5:9b", "gemma4:e4b"]

SYSTEM_PROMPT = (
    "You are a deterministic pipeline node in a customer support triage system. "
    "Return exactly one JSON object matching the schema. "
    "No markdown fences. No prose outside JSON. No extra keys. "
    "Do not invent values not present in the provided context."
)

# ── SCENARIO ─────────────────────────────────────────────────────────────────

CUSTOMER_MESSAGE = (
    "I've been charged twice for my order #ORD-2847 that was supposed to be $89.99. "
    "I need this fixed ASAP, it's been 3 days! "
    "Also I never received my tracking number and the package was supposed to arrive yesterday. "
    "My account is emma.walsh@mail.com."
)

MOCK_KB_RESULTS = [
    {"index": 0, "snippet": "Order #ORD-2847 — status: shipped, carrier: FedEx, tracking: FX994821033, label created 2026-04-10"},
    {"index": 1, "snippet": "Payment log ORD-2847: charged $89.99 on 2026-04-10 14:02 UTC and again $89.99 on 2026-04-10 14:07 UTC — possible gateway retry"},
    {"index": 2, "snippet": "Refund policy: duplicate charges refunded within 3–5 business days; chargeback risk flagged if not resolved within 48h of report"},
    {"index": 3, "snippet": "Account emma.walsh@mail.com — customer since 2023, 14 orders, no prior disputes, Gold tier"},
    {"index": 4, "snippet": "SLA policy: tracking email sent within 2h of label creation; delivery ETA missed = L2 escalation required"},
]

# ── SCHEMAS ───────────────────────────────────────────────────────────────────

class Issue(BaseModel):
    type: str        # billing_error | missing_tracking | late_delivery | account_issue | other
    urgency: str     # low | medium | high | critical
    description: str

class IssueClassify(BaseModel):
    """Step 1 — classify all issues in the message with urgency."""
    issues: list[Issue]
    overall_urgency: str  # highest urgency across all issues
    issue_count: int


class Entity(BaseModel):
    key: str    # order_id | amount | email | date | carrier | tracking_number
    value: str

class EntityExtract(BaseModel):
    """Step 2 — extract structured entities from the message."""
    entities: list[Entity]


class MissingInfo(BaseModel):
    field: str
    needed_for: str   # which issue resolution requires it
    blocking: bool

class InfoGaps(BaseModel):
    """Step 3 — identify information gaps that block resolution."""
    gaps: list[MissingInfo] = Field(
        validation_alias=AliasChoices("gaps", "missing", "missing_info", "information_gaps")
    )
    resolvable_without_customer: list[str]  # issues resolvable from internal systems alone


class KBQuery(BaseModel):
    topic: str
    query: str
    system: str   # orders | payments | accounts | policy | shipping
    priority: int

class KBSearchPlan(BaseModel):
    """Step 4 — plan knowledge-base and system lookups needed."""
    queries: list[KBQuery] = Field(
        validation_alias=AliasChoices("queries", "searches", "kb_queries", "lookups")
    )

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for k in ("search_plan", "plan", "kb_plan"):
                if k in data and isinstance(data[k], dict):
                    inner = data[k]
                    for fk in ("queries", "searches"):
                        if fk in inner:
                            return {**data, "queries": inner[fk]}
        return data


class KBResult(BaseModel):
    topic: str
    best_index: int
    confidence: float = Field(ge=0.0, le=1.0)
    key_fact: str    # one-sentence summary of what was found

class KBRank(BaseModel):
    """Step 5 — rank KB snippets and extract the key fact per topic."""
    results: list[KBResult] = Field(
        validation_alias=AliasChoices("results", "rankings", "ranked_results", "kb_results")
    )


class ResolutionStep(BaseModel):
    step_id: str
    action: str
    responsible: str   # system | agent | customer | finance
    reversible: bool
    depends_on: list[str]

class ResolutionPlan(BaseModel):
    """Step 6 — full resolution plan for all issues."""
    steps: list[ResolutionStep] = Field(
        validation_alias=AliasChoices("steps", "plan", "resolution_steps", "actions")
    )
    total_steps: int
    requires_finance_approval: bool


class PolicyConflict(BaseModel):
    rule: str
    conflict_description: str
    severity: str   # low | medium | high

class PolicyCheck(BaseModel):
    """Step 7 — check resolution plan against support policies."""
    conflicts: list[PolicyConflict] = Field(
        validation_alias=AliasChoices("conflicts", "policy_conflicts", "violations", "issues")
    )
    chargeback_risk: bool
    sla_breach: bool


class RiskItem(BaseModel):
    category: str   # financial | churn | legal | reputational
    description: str
    severity: str   # low | medium | high

class RiskAssessment(BaseModel):
    """Step 8 — assess business risks of this case."""
    risks: list[RiskItem] = Field(
        validation_alias=AliasChoices("risks", "risk_items", "risk_list", "items")
    )
    total_financial_exposure_usd: float
    escalation_required: bool


class ResponseDraft(BaseModel):
    """Step 9 — draft customer-facing response."""
    subject: str
    body: str
    tone: str       # apologetic | neutral | informational
    word_count: int
    promises_made: list[str]


class EscalationRoute(BaseModel):
    """Step 10 — final routing decision."""
    tier: str                    # self_serve | L1 | L2 | finance | legal
    assigned_to: str
    priority_queue: str          # normal | urgent | critical
    auto_actions: list[str]      # actions to trigger immediately without human
    hold_for_approval: list[str] # actions needing manager sign-off
    reasoning: str


STEP_SCHEMAS: list[type[BaseModel]] = [
    IssueClassify, EntityExtract, InfoGaps, KBSearchPlan, KBRank,
    ResolutionPlan, PolicyCheck, RiskAssessment, ResponseDraft, EscalationRoute,
]
STEP_NAMES = [cls.__name__ for cls in STEP_SCHEMAS]

# ── PROMPTS ───────────────────────────────────────────────────────────────────

def _ctx(ctx: dict, keys: list[str]) -> str:
    return "\n\n".join(
        f"# {k}\n{json.dumps(ctx[k], indent=2)}"
        for k in keys if k in ctx
    )

def _p1(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        "Classify every distinct issue in this message.\n"
        "Issue types: billing_error, missing_tracking, late_delivery, account_issue, other.\n"
        "Urgency: low, medium, high, critical.\n"
        "overall_urgency: the highest urgency across all issues.\n"
        "issue_count: exact number of issues found.\n\n"
        "# Expected output format\n"
        '{"issues": [{"type": "billing_error", "urgency": "critical", '
        '"description": "charged twice for order #ORD-2847"}, '
        '{"type": "missing_tracking", "urgency": "high", '
        '"description": "no tracking number received"}], '
        '"overall_urgency": "critical", "issue_count": 3}'
    )

def _p2(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1'])}\n\n"
        "Extract all structured entities from the message.\n"
        "Keys: order_id, amount, email, date_mentioned, issue_duration.\n\n"
        "# Expected output format\n"
        '{"entities": [{"key": "order_id", "value": "ORD-2847"}, '
        '{"key": "amount", "value": "89.99"}, '
        '{"key": "email", "value": "emma.walsh@mail.com"}, '
        '{"key": "issue_duration", "value": "3 days"}]}'
    )

def _p3(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1', 'step2'])}\n\n"
        "Identify information gaps that block issue resolution.\n"
        "For each gap: field, which issue needs it (needed_for), blocking=true if unresolvable without it.\n\n"
        "Internal systems already have access to:\n"
        "  - Full payment logs (can confirm duplicate charges without asking the customer)\n"
        "  - Order and shipping records including tracking numbers\n"
        "  - Account history by email address\n"
        "  - All policy documents\n\n"
        "resolvable_without_customer: list issue types that can be fully resolved using internal "
        "systems alone — no reply from the customer needed.\n"
        "If tracking exists in the system, missing_tracking is resolvable_without_customer.\n"
        "If payment logs show a duplicate, billing_error is resolvable_without_customer.\n\n"
        "# Expected output format\n"
        '{"gaps": [{"field": "preferred_compensation", "needed_for": "billing_error", "blocking": false}], '
        '"resolvable_without_customer": ["missing_tracking", "billing_error"]}'
    )

def _p4(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1', 'step2', 'step3'])}\n\n"
        "Plan knowledge-base and system lookups needed to resolve all issues.\n"
        "Systems: orders, payments, accounts, policy, shipping.\n"
        "priority: 1=must-have, 2=important, 3=optional.\n\n"
        "# Expected output format\n"
        '{"queries": [{"topic": "duplicate_charge", "query": "payment log for ORD-2847", '
        '"system": "payments", "priority": 1}, '
        '{"topic": "tracking", "query": "shipping status ORD-2847", '
        '"system": "shipping", "priority": 1}]}'
    )

def _p5(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step4'])}\n\n"
        f"KB results (0-based index):\n{json.dumps(ctx['kb'], indent=2)}\n\n"
        "For each query topic, pick the best matching KB snippet by index (0–4).\n"
        "key_fact: one sentence summarising the critical finding.\n\n"
        "# Expected output format\n"
        '{"results": [{"topic": "duplicate_charge", "best_index": 1, "confidence": 0.98, '
        '"key_fact": "Double charge confirmed: $89.99 charged twice on 2026-04-10 — gateway retry"}, '
        '{"topic": "tracking", "best_index": 0, "confidence": 0.95, '
        '"key_fact": "Tracking FX994821033 exists but was not emailed to customer"}]}'
    )

def _p6(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step1', 'step5'])}\n\n"
        "Assemble a step-by-step resolution plan.\n"
        "step_id: unique ids like r1, r2, …\n"
        "responsible: system | agent | customer | finance.\n"
        "reversible=false for: refunds, credits, account changes.\n"
        "requires_finance_approval=true if any step involves a monetary transaction.\n\n"
        "# Expected output format\n"
        '{"steps": [{"step_id": "r1", "action": "issue_refund", '
        '"params": {"amount": 89.99, "order_id": "ORD-2847"}, '
        '"responsible": "finance", "reversible": false, "depends_on": []}, '
        '{"step_id": "r2", "action": "send_tracking_email", '
        '"params": {"tracking": "FX994821033", "email": "emma.walsh@mail.com"}, '
        '"responsible": "system", "reversible": true, "depends_on": []}], '
        '"total_steps": 3, "requires_finance_approval": true}'
    )

def _p7(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step5', 'step6'])}\n\n"
        "Check the resolution plan against support policies.\n"
        "Policy rules to check:\n"
        "  - Duplicate charges must be refunded within 48h of report or chargeback_risk=true\n"
        "  - Tracking email SLA: within 2h of label creation; breach = sla_breach=true\n"
        "  - Refunds > $50 require finance approval\n"
        "chargeback_risk and sla_breach must be boolean.\n\n"
        "# Expected output format\n"
        '{"conflicts": [{"rule": "48h refund SLA", '
        '"conflict_description": "3 days elapsed since charge — chargeback window at risk", '
        '"severity": "high"}], '
        '"chargeback_risk": true, "sla_breach": true}'
    )

def _p8(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step6', 'step7'])}\n\n"
        "Assess business risks of this case.\n"
        "Risk categories: financial, churn, legal, reputational.\n"
        "total_financial_exposure_usd: total monetary exposure (duplicate charge + any penalties).\n"
        "escalation_required=true if any risk is high or chargeback risk exists.\n\n"
        "# Expected output format\n"
        '{"risks": [{"category": "financial", "description": "duplicate charge $89.99 + potential chargeback fee ~$25", "severity": "high"}, '
        '{"category": "churn", "description": "Gold tier customer with 14 orders — high LTV at risk", "severity": "medium"}], '
        '"total_financial_exposure_usd": 114.99, "escalation_required": true}'
    )

def _p9(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step6', 'step7', 'step8'])}\n\n"
        "Draft a customer-facing email response.\n"
        "subject: concise email subject line.\n"
        "body: plain prose, 50–120 words, no bullet points, no markdown.\n"
        "tone: apologetic | neutral | informational.\n"
        "word_count: exact word count of body field.\n"
        "promises_made: list of specific commitments made to the customer.\n\n"
        "# Expected output format\n"
        '{"subject": "Your Order #ORD-2847 — Refund & Tracking Update", '
        '"body": "Hi Emma, we sincerely apologise for the duplicate charge and the missing tracking email...", '
        '"tone": "apologetic", "word_count": 72, '
        '"promises_made": ["refund $89.99 within 3-5 business days", "tracking number sent immediately"]}'
    )

def _p10(ctx: dict) -> str:
    return (
        f'Customer message: "{ctx["msg"]}"\n\n'
        f"{_ctx(ctx, ['step7', 'step8', 'step9'])}\n\n"
        "Produce the final escalation routing decision.\n"
        "tier: self_serve | L1 | L2 | finance | legal.\n"
        "assigned_to: team or role name.\n"
        "priority_queue: normal | urgent | critical.\n"
        "auto_actions: list of actions to trigger immediately without human review.\n"
        "hold_for_approval: list of actions needing manager sign-off.\n"
        "reasoning: one sentence explaining the routing.\n\n"
        "# Expected output format\n"
        '{"tier": "L2", "assigned_to": "senior_support", "priority_queue": "critical", '
        '"auto_actions": ["send_tracking_email", "flag_chargeback_risk"], '
        '"hold_for_approval": ["issue_refund"], '
        '"reasoning": "Duplicate charge with chargeback risk and SLA breach requires L2 with finance approval."}'
    )

STEP_PROMPTS = [_p1, _p2, _p3, _p4, _p5, _p6, _p7, _p8, _p9, _p10]

# ── SCORERS ───────────────────────────────────────────────────────────────────

def _s1(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    issues = data.get("issues", [])
    types = {i.get("type", "") for i in issues}
    expected_types = {"billing_error", "missing_tracking", "late_delivery"}
    found = expected_types & types
    score = len(found)
    ou = data.get("overall_urgency", "")
    if ou in ("critical", "high"):
        score += 1
    else:
        notes.append(f"overall_urgency={ou!r} (expected critical or high)")
    if data.get("issue_count", 0) >= 2:
        score += 1
    else:
        notes.append(f"issue_count={data.get('issue_count')} (expected ≥2)")
    missing = expected_types - found
    if missing:
        notes.append(f"missed issue types: {missing}")
    return score, 5, notes

def _s2(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    dump = json.dumps(data).lower()
    score = 0
    for needle, pts in [("ord-2847", 2), ("89.99", 1), ("emma.walsh", 1), ("3 day", 1)]:
        if needle in dump:
            score += pts
        else:
            notes.append(f"entity not found: {needle!r}")
    return score, 5, notes

def _s3(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    dump = json.dumps(data).lower()
    score = 0
    rwc = data.get("resolvable_without_customer", [])
    if any("tracking" in r.lower() or "missing" in r.lower() for r in rwc):
        score += 2
    else:
        notes.append("missing_tracking should be resolvable without customer (tracking exists in system)")
    if any("billing" in r.lower() or "charge" in r.lower() or "duplicate" in r.lower() for r in rwc):
        score += 2
    else:
        notes.append("billing_error should be resolvable without customer (payment log is internal)")
    if "gaps" in dump or "missing" in dump:
        score += 1
    return score, 5, notes

def _s4(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    dump = json.dumps(data).lower()
    score = 0
    if "payment" in dump or "charge" in dump or "billing" in dump:
        score += 2
    else:
        notes.append("no payments system query planned")
    if "shipping" in dump or "tracking" in dump or "order" in dump:
        score += 2
    else:
        notes.append("no shipping/orders query planned")
    if any(q.get("priority") == 1 for q in data.get("queries", [])):
        score += 1
    else:
        notes.append("no priority-1 query set")
    return score, 5, notes

def _s5(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    results = data.get("results", [])
    score = 0
    for r in results:
        topic = r.get("topic", "").lower()
        idx = r.get("best_index", -1)
        conf = r.get("confidence", -1.0)
        kf = r.get("key_fact", "")
        if not (0.0 <= conf <= 1.0):
            notes.append(f"confidence {conf} out of range for {topic}")
            continue
        if "charge" in topic or "billing" in topic or "duplicate" in topic or "payment" in topic:
            if idx == 1:
                score += 2
            else:
                notes.append(f"billing topic: expected index 1 (payment log), got {idx}")
        if "track" in topic or "ship" in topic or "order" in topic:
            if idx == 0:
                score += 2
            else:
                notes.append(f"tracking topic: expected index 0 (order status), got {idx}")
        if kf:
            score += 0  # presence only, not scored separately
    return min(score, 4), 4, notes

def _s6(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    steps = data.get("steps", [])
    score = 0
    dump = json.dumps(steps).lower()
    if len(steps) >= 2:
        score += 1
    else:
        notes.append(f"only {len(steps)} resolution steps (expected ≥2)")
    if "refund" in dump or "charge" in dump:
        score += 1
    else:
        notes.append("no refund action in plan")
    if "track" in dump or "email" in dump or "send" in dump:
        score += 1
    else:
        notes.append("no tracking email action in plan")
    if data.get("requires_finance_approval") is True:
        score += 1
    else:
        notes.append("requires_finance_approval should be true (refund > $50)")
    return score, 4, notes

def _s7(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    score = 0
    cb = data.get("chargeback_risk")
    sla = data.get("sla_breach")
    if cb is True:
        score += 2
    else:
        notes.append(f"chargeback_risk should be true (3 days elapsed, 48h policy)")
    if sla is True:
        score += 2
    else:
        notes.append(f"sla_breach should be true (tracking email not sent within 2h)")
    return score, 4, notes

def _s8(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    score = 0
    risks = data.get("risks", [])
    dump = json.dumps(risks).lower()
    if "financial" in dump:
        score += 1
    else:
        notes.append("no financial risk identified")
    exp = data.get("total_financial_exposure_usd", 0)
    if isinstance(exp, (int, float)) and exp >= 89.99:
        score += 2
    else:
        notes.append(f"total_financial_exposure_usd={exp} (expected ≥89.99)")
    if data.get("escalation_required") is True:
        score += 1
    else:
        notes.append("escalation_required should be true")
    return score, 4, notes

def _s9(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    score = 0
    body = data.get("body", "")
    subject = data.get("subject", "")
    wc = data.get("word_count", 0)
    promises = data.get("promises_made", [])
    actual_wc = len(body.split())
    if subject.strip():
        score += 1
    else:
        notes.append("subject empty")
    if 40 <= actual_wc <= 150:
        score += 1
    else:
        notes.append(f"body word count {actual_wc} outside 40–150")
    if abs(wc - actual_wc) <= 8:
        score += 1
    else:
        notes.append(f"word_count={wc} vs actual={actual_wc}")
    if len(promises) >= 1:
        score += 1
    else:
        notes.append("promises_made empty")
    return score, 4, notes

def _s10(data: dict) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    score = 0
    tier = data.get("tier", "")
    pq = data.get("priority_queue", "")
    auto = data.get("auto_actions", [])
    hold = data.get("hold_for_approval", [])
    r = data.get("reasoning", "")
    if tier in ("L2", "finance"):
        score += 2
    else:
        notes.append(f"tier={tier!r} (expected L2 or finance given chargeback risk + SLA breach)")
    if pq in ("critical", "urgent"):
        score += 1
    else:
        notes.append(f"priority_queue={pq!r} (expected critical or urgent)")
    if len(hold) >= 1:
        score += 1
    else:
        notes.append("hold_for_approval empty — refund needs approval")
    if not r.strip():
        notes.append("reasoning empty")
    return score, 4, notes

STEP_SCORERS = [_s1, _s2, _s3, _s4, _s5, _s6, _s7, _s8, _s9, _s10]

# ── RUNNER ────────────────────────────────────────────────────────────────────

def _opts(model: str) -> dict:
    if model.startswith("qwen3"):
        return {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "repetition_penalty": 1.05}
    if model.startswith("gemma"):
        return {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    return {"temperature": 0.7}

def _think(model: str, step: int = 1) -> bool | None:
    if model.startswith("qwen3"):
        return False
    if model.startswith("gemma"):
        return step in (6, 7, 8)
    return None

def run_pipeline(model: str) -> list[dict]:
    client = ollama.Client()
    ctx: dict[str, Any] = {"msg": CUSTOMER_MESSAGE, "kb": MOCK_KB_RESULTS}
    results: list[dict] = []

    for i, (schema_cls, prompt_fn, scorer) in enumerate(
        zip(STEP_SCHEMAS, STEP_PROMPTS, STEP_SCORERS)
    ):
        step_num = i + 1
        prompt_text = prompt_fn(ctx)
        if model.startswith("qwen3"):
            prompt_text += "\n/no_think"
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
    W = 78
    print("\n" + "=" * W)
    print("SUPPORT TRIAGE BENCHMARK  —  " + "  vs  ".join(models))
    print("=" * W)
    hdr = f"{'#':<3} {'Step':<22}"
    for m in models:
        hdr += f"  {'Schema':>6} {'sem':>5} {'ms':>6}"
    print(hdr)
    print("-" * W)

    tot_sem:  dict[str, int]   = {m: 0   for m in models}
    tot_max:  dict[str, int]   = {m: 0   for m in models}
    tot_ok:   dict[str, int]   = {m: 0   for m in models}
    tot_time: dict[str, float] = {m: 0.0 for m in models}

    for i in range(10):
        row = f"{i+1:<3} {STEP_NAMES[i]:<22}"
        for m in models:
            r = all_results[m][i]
            sym = "PASS" if r["schema_ok"] else "FAIL"
            sem = f"{r['sem_score']}/{r['sem_max']}"
            ms  = f"{r['elapsed_s']*1000:.0f}"
            row += f"  {sym:>6} {sem:>5} {ms:>6}"
            tot_sem[m]  += r["sem_score"]
            tot_max[m]  += r["sem_max"]
            tot_time[m] += r["elapsed_s"]
            if r["schema_ok"]: tot_ok[m] += 1
        print(row)

    print("-" * W)
    row = f"{'TOT':<3} {'':22}"
    for m in models:
        row += f"  {tot_ok[m]}/10 {tot_sem[m]}/{tot_max[m]} {tot_time[m]*1000:.0f}"
    print(row)

    print("\nSCORES")
    for m in models:
        s_pct  = tot_ok[m]  / 10 * 100
        sm_pct = tot_sem[m] / tot_max[m] * 100 if tot_max[m] else 0
        cb = sum(1 for r in all_results[m] if not r["schema_ok"])
        print(f"  {m:<24}  schema {s_pct:.0f}%  semantic {sm_pct:.0f}%  time {tot_time[m]:.1f}s  chain_breaks {cb}")

    print(f"\n  semantic winner → {max(models, key=lambda m: tot_sem[m])}")
    print(f"  schema winner   → {max(models, key=lambda m: tot_ok[m])}")
    print(f"  speed winner    → {min(models, key=lambda m: tot_time[m])}")

    any_notes = any(
        r["sem_notes"] or not r["schema_ok"]
        for m in models for r in all_results[m]
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

# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    all_results: dict[str, list[dict]] = {}
    for model in MODELS:
        print(f"\n{'─'*50}")
        print(f"Model: {model}")
        print(f"{'─'*50}")
        all_results[model] = run_pipeline(model)
    print_report(all_results)
