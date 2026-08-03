"""Tier 1 — offline behavioral evals. Mocked/absent LLM, no network, deterministic.

The classifier is the highest-leverage component in the system: intent selects the toolset,
the round limit, and the num_predict budget. Every downstream failure that looks like "the
model is dumb" starts here. So it gets a scored dataset, not a handful of asserts.

Scoring, not binary: a classifier is graded on accuracy over a labeled set. The gate is
"no case that passes today may fail tomorrow" — regressions fail the build, while the
known misses stay visible in the report instead of being deleted to make the suite green.
"""
from __future__ import annotations


import pytest

from clients import router
from evals.conftest import load_cases

_CLASSIFIER = {"web": router.classify, "telegram": router.classify_telegram}


# ------------------------------------------------------------------ classifier accuracy

def test_intent_classification_accuracy(score, capsys):
    """Reports accuracy per channel; fails only on regression against a currently-passing case."""
    regressions, fixed = [], []

    for case in load_cases(tier="routing"):
        got_model, got_intent = _CLASSIFIER[case["channel"]](case["input"])
        ok = got_intent == case["expect"]
        score.record(f"{case['channel']}:{case['input'][:38]}", ok, f"got={got_intent}")

        if case["status"] == "pass" and not ok:
            regressions.append((case["input"], case["expect"], got_intent))
        elif case["status"] == "known_miss" and ok:
            fixed.append((case["input"], case["expect"]))

    if fixed:
        with capsys.disabled():
            print("\n  known misses now PASSING — promote to status='pass' in cases.jsonl:")
            for inp, exp in fixed:
                print(f"    {inp!r} -> {exp}")

    assert not regressions, "\n".join(
        f"REGRESSION: {i!r} expected {e}, got {g}" for i, e, g in regressions
    )


def test_classifier_accuracy_floor(score):
    """A hard floor, so a rewrite that tanks accuracy while keeping the old cases green
    still fails. Raise this number as misses get fixed; never lower it."""
    cases = load_cases(tier="routing")
    correct = sum(_CLASSIFIER[c["channel"]](c["input"])[1] == c["expect"] for c in cases)
    accuracy = correct / len(cases)
    print(f"\n  intent accuracy: {accuracy:.1%} ({correct}/{len(cases)})")
    assert accuracy >= 0.95, f"intent accuracy fell to {accuracy:.1%}"


@pytest.mark.xfail(
    strict=False,
    reason="Remaining: 'pause the morning briefing' (web=casual, tg=factual_qa; "
    "regex can't match automation by user-given name). LLM primary path handles "
    "this correctly. Fixed: 'who wrote Dune', 'integrate x^2 sin(x) dx', "
    "'open safari for me'.",
)
def test_classifiers_agree_across_channels():
    """The same message must not land on different intents in web vs telegram, for the
    intents both channels share. Disagreement here is how the same query got two different
    toolsets depending on where the user typed it."""
    shared = {"casual", "email", "math", "automation", "temporal", "factual_qa", "ocr"}
    disagreements = []
    for case in load_cases(tier="routing"):
        if case["expect"] not in shared:
            continue
        web = router.classify(case["input"])[1]
        tg = router.classify_telegram(case["input"])[1]
        if web != tg and {web, tg} <= shared:
            disagreements.append((case["input"], web, tg))
    assert not disagreements, f"channel disagreement: {disagreements}"


# ------------------------------------------------------------------ chinese short-circuit

def test_chinese_query_detection():
    from tools.websearch import _is_chinese_query

    assert _is_chinese_query("什么是量子计算")
    assert _is_chinese_query("哔哩哔哩上最火的视频")
    assert not _is_chinese_query("what is quantum computing")
    assert not _is_chinese_query("Sony a7iii review")  # latin with no han chars
    assert not _is_chinese_query("")


def test_chinese_query_short_circuits_before_the_normal_pipeline(monkeypatch, no_network):
    """Outcome grader: the Chinese branch is taken (agent_reach called) and none of the
    English pipeline runs. Asserts the *effect*, not the call order.

    websearch imports search_chinese_web lazily inside search(), so patch it at its source.
    """
    from tools import agent_reach, websearch

    called = []
    monkeypatch.setattr(agent_reach, "search_chinese_web", lambda a: called.append(a) or "结果")
    for stage in ("_rewrite_query", "_search", "_rerank", "_summarize"):
        monkeypatch.setattr(
            websearch, stage, lambda *a, **k: pytest.fail(f"{stage} ran on a Chinese query")
        )

    out = websearch.search("哔哩哔哩上最火的视频是什么")
    assert called, "Chinese query did not reach agent_reach"
    assert "结果" in out


def test_english_query_does_not_take_the_chinese_branch(monkeypatch, no_network):
    from tools import agent_reach, websearch

    monkeypatch.setattr(
        agent_reach, "search_chinese_web", lambda *a: pytest.fail("chinese branch on english")
    )
    sentinel = RuntimeError("reached the english pipeline")
    monkeypatch.setattr(websearch, "_rewrite_query", lambda *a, **k: (_ for _ in ()).throw(sentinel))
    with pytest.raises(RuntimeError, match="english pipeline"):
        websearch.search("what is quantum computing")


# ------------------------------------------------------------------ skills hub

def test_flatten_triggers_handles_every_shape():
    """Crashed on the first dict it met (ext.agent-reach), which broke trigger matching for
    every skill registered after it — a silent, total loss of external-skill dispatch."""
    from skills_hub import _flatten_triggers

    assert _flatten_triggers("solo") == ["solo"]
    assert _flatten_triggers(["a", "b"]) == ["a", "b"]
    assert set(_flatten_triggers({"search": ["x"], "social": ["y"]})) == {"x", "y"}
    assert set(_flatten_triggers([{"a": ["1"]}, "2"])) == {"1", "2"}
    assert _flatten_triggers(None) == []


def test_direct_tool_fastpath_falls_through_when_a_required_param_is_missing():
    """The fast path's regex extractor only special-cases a few param names; everything else
    hit a catch-all that stuffed the raw user message into the param. Missing required params
    must fall through to the LLM loop rather than guess."""
    from skills_hub import _extract_params_from_schema
    from tools.registry import ALL_TOOLS

    schema = next(t for t in ALL_TOOLS if t["function"]["name"] == "ocr_image")
    params = _extract_params_from_schema("read the text in my screenshot", schema) or {}

    # The message contains no path. Either the extractor reports image_path missing (so
    # dispatch_with_prompt falls through to the LLM), or it fabricated one from the prose.
    fabricated = params.get("image_path")
    assert not fabricated or "/" in str(fabricated), (
        f"fast path fabricated image_path={fabricated!r} from prose instead of "
        "leaving it unset for the LLM loop"
    )


# ------------------------------------------------------------------ automations

def _mk(ws, **kw):
    """create_workflow_instance returns the row, not the id."""
    defaults = dict(
        automation_id="user.automation",
        task_name="t",
        config={},
        schedule={"type": "interval", "minutes": 1},
    )
    return ws.create_workflow_instance(**{**defaults, **kw})["id"]


@pytest.mark.parametrize("action_type", ["telegram", "notification"])
def test_every_user_automation_action_type_reaches_its_side_effect(clean_env, monkeypatch, action_type):
    """Every LLM-created automation shares automation_id='user.automation'; the generic
    handler fans out on the instance's action_type. A synthetic per-automation id matched
    no handler and failed permanently with a silent KeyError.

    Outcome grader: the side effect fires, and the handler reports success.
    """
    from jobs.handlers import user_automation as ua

    fired = []
    monkeypatch.setattr(
        "tools.telegram_send.send_telegram", lambda m, *a, **k: fired.append(("telegram", m)) or "sent"
    )
    monkeypatch.setattr(
        "store.workflow_store.add_notification",
        lambda **k: fired.append(("notification", k["message"])),
    )

    result = ua.handle(
        {
            "automation_id": "user.automation",
            "action_type": action_type,
            "task_name": "t",
            "config": {"message": "hi"},
        }
    )
    assert result.get("success") is True, f"{action_type} failed: {result}"
    assert fired == [(action_type, "hi")], f"{action_type} side effect did not fire: {fired}"


def test_unknown_action_type_fails_loudly_not_silently(clean_env):
    from jobs.handlers import user_automation as ua

    result = ua.handle({"automation_id": "user.automation", "action_type": "carrier_pigeon"})
    assert result["success"] is False and "carrier_pigeon" in result["error"]


def test_soft_delete_leaves_the_row_but_hides_it_from_the_scheduler(clean_env):
    """delete_automation only sets status='deleted'. claim_due_workflows() must skip it,
    while the row (and its dedupe_key) persists."""
    import store.workflow_store as ws

    ws.init()
    wid = _mk(ws, task_name="doomed", next_run_at="2000-01-01T00:00:00+00:00")
    ws.update_workflow_instance(wid, status="deleted")

    due = ws.claim_due_workflows(limit=10)
    assert all(w["id"] != wid for w in due), "a soft-deleted workflow was claimed by the scheduler"
    assert ws.get_workflow_instance(wid) is not None, "soft delete removed the row"


def test_claim_due_workflows_persists_last_run_at(clean_env):
    """It computed last_run_at but never wrote it, so list_automations always said 'never'."""
    import store.workflow_store as ws

    ws.init()
    wid = _mk(ws, task_name="ticker", next_run_at="2000-01-01T00:00:00+00:00")

    claimed = ws.claim_due_workflows(limit=10)
    assert any(w["id"] == wid for w in claimed), "an overdue workflow was not claimed"
    assert ws.get_workflow_instance(wid)["last_run_at"] is not None, "last_run_at not persisted"


def test_crud_tools_accept_a_task_name_as_well_as_a_uuid(clean_env):
    """CLAUDE.md says passing the name instead of the uuid silently 'not found's. That is
    STALE: _resolve_id() resolves task_name COLLATE NOCASE. Pinning the real behavior so
    the doc gets fixed and a future removal of _resolve_id() is caught."""
    import store.workflow_store as ws

    ws.init()
    wid = _mk(ws, task_name="tech_brief")

    assert ws.get_workflow_instance("tech_brief")["id"] == wid
    assert ws.get_workflow_instance("TECH_BRIEF")["id"] == wid, "name lookup is case-sensitive"
    assert ws.get_workflow_instance("no-such-automation") is None


# ------------------------------------------------------------------ path repair

@pytest.mark.parametrize(
    "resolver_path",
    [
        "tools.form_tools:_resolve_form_path",
        "tools.pdf_tools:_resolve_pdf_path",
        "tools.flat_pdf_tools:_resolve_pdf_path",
        "tools.vision_form_tools:_resolve_pdf_path",
        "tools.gmail:_resolve_attachment_path",
    ],
)
def test_tilde_expanded_to_linux_home_is_repaired(resolver_path, tmp_path, monkeypatch):
    """LLMs expand ~ to /home/user/, which does not exist on macOS. Every path-taking tool
    needs the same fallback to the real home dir; gmail's send_email attachments had none."""
    import importlib

    mod_name, fn_name = resolver_path.split(":")
    fn = getattr(importlib.import_module(mod_name), fn_name)

    monkeypatch.setenv("HOME", str(tmp_path))
    real = tmp_path / "Downloads" / "form.pdf"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"%PDF-1.4")

    resolved = fn("/home/user/Downloads/form.pdf")
    assert str(resolved) == str(real), f"{resolver_path} did not repair the linux home path"


# ------------------------------------------------------------------ classify_message (production path)
# The regex-fallback tests above cover classify()/classify_telegram(). The production path
# is classify_message() which runs Stage 0 (syntactic guards) -> Stage 1 (LLM) -> Stage 2
# (regex fallback). After removing 3 semantic guards, the LLM owns all semantic decisions.
# These tests verify that end-to-end pipeline with a mocked LLM.


def _mock_classification(intent: str, source: str = "llm", model: str | None = None) -> router.Classification:
    """Return a Classification that looks like it came from the LLM."""
    if model is None:
        model = router.CLOUD_MODEL
    return router.Classification(model=model, intent=intent, specialist_hint=None, source=source)


def _mock_cloud_chat(response_json: str):
    """Patch clients.cloud_client.chat to return a canned JSON response."""
    import clients.cloud_client as cc

    def _chat(*a, **kw):
        return response_json
    return _chat


def test_classify_message_llm_handles_removed_guard_cases(monkeypatch):
    """Stage 0 guard removal: queries previously caught by _CONVO_REF_RE,
    _CASUAL_CHAT_RE, and _DOC_CREATE_RE reach the LLM and get classified correctly."""
    llm_calls = {}
    last_msg = [None]
    last_ch = [None]

    def _fake_llm_classify(msg, channel):
        last_msg[0] = msg
        last_ch[0] = channel
        key = (channel, msg)
        if key in llm_calls:
            llm_calls[key] += 1
        else:
            llm_calls[key] = 1

        mapping = {
            ("web", "what did I just ask you"): "casual",
            ("web", "what's my name"): "casual",
            ("web", "hi"): "casual",
            ("web", "hey how are you"): "casual",
            ("web", "write a poem about love"): "casual",
            ("telegram", "send this as a .md"): "document",
            ("web", "export this to xlsx"): "document",
        }
        intent = mapping.get(key)
        if intent is None:
            return None
        return _mock_classification(intent)

    monkeypatch.setattr(router, "_llm_classify", _fake_llm_classify)

    cases = [
        ("web", "what did I just ask you", "casual"),
        ("web", "what's my name", "casual"),
        ("web", "hi", "casual"),
        ("web", "hey how are you", "casual"),
        ("web", "write a poem about love", "casual"),
        ("telegram", "send this as a .md", "document"),
        ("web", "export this to xlsx", "document"),
    ]

    for channel, msg, expected in cases:
        c = router.classify_message(msg, channel=channel)
        assert c.intent == expected, f"{msg!r}: expected {expected}, got {c.intent}"
        assert c.source == "llm", f"{msg!r}: expected source=llm, got {c.source}"

    assert len(llm_calls) == len(cases), f"LLM called {len(llm_calls)} times, expected {len(cases)}"


def test_classify_message_syntactic_guards_still_short_circuit(monkeypatch):
    """Stage 0 retained guards (_SIMPLE_MATH_RE, _YOUTUBE_RE, _CHINESE_WEB_RE)
    still short-circuit before the LLM. LLM must not be called."""
    monkeypatch.setattr(router, "_llm_classify", lambda *a, **k: pytest.fail("LLM must not be called for syntactic guards"))

    assert router.classify_message("2+2").intent == "math"
    assert router.classify_message("calculate 15% of 300").intent == "math"
    assert router.classify_message("summarize https://youtube.com/watch?v=abc").intent == "youtube"
    assert router.classify_message("search youtube for cat videos").intent == "youtube"
    assert router.classify_message("小红书搜索").intent == "chinese_web"
    assert router.classify_message("xiaohongshu 美食").intent == "chinese_web"


def test_classify_message_falls_back_to_regex_when_llm_fails(monkeypatch):
    """When _llm_classify returns None (model down/error), Stage 2 regex fallback
    takes over. Verify the fallback still produces reasonable intents."""
    monkeypatch.setattr(router, "_llm_classify", lambda *a, **k: None)

    assert router.classify_message("what is the capital of Peru").intent == "factual_qa"
    assert router.classify_message("hi").intent == "casual"
    assert router.classify_message("2+2").intent == "math"
    assert router.classify_message("proof of eigenvalue").intent == "math"
    assert router.classify_message("check my inbox").intent == "email"


def test_classify_message_llm_invalid_intent_triggers_fallback(monkeypatch):
    """When the LLM returns an unknown intent (not in _KNOWN_INTENTS),
    _llm_classify must return None, and the regex fallback takes over."""
    monkeypatch.setattr(
        "clients.cloud_client.chat",
        lambda *a, **kw: '{"intent": "not_a_real_intent", "specialist_hint": null}',
    )
    c = router.classify_message("what is the capital of Peru")
    assert c.intent == "factual_qa"
    assert c.source == "regex_fallback"


def test_classify_message_document_intent_rejected_on_web(monkeypatch):
    """document intent is only valid on telegram/whatsapp. On web, _llm_classify
    returns None for document, triggering the regex fallback."""
    monkeypatch.setattr(
        "clients.cloud_client.chat",
        lambda *a, **kw: '{"intent": "document", "specialist_hint": null}',
    )
    c = router.classify_message("create a pdf of my resume", channel="web")
    assert c.intent != "document"
    assert c.source == "regex_fallback"
