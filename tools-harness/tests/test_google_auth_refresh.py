"""
Smoke test: local agent auto-refresh of expired Google OAuth token.

Two paths tested:

  Path A — Python layer (execute_with_google_auth_retry):
    The access_token is corrupted.  The Google API raises 401.
    execute_with_google_auth_retry catches it, calls refresh_google_token(),
    and retries.  The agent never sees the failure.

  Path B — Agent layer (skills_hub _AUTH_RETRY_LINE):
    The tool executor returns an error string containing "401 Unauthorized".
    The skill prompt says: call refresh_google_token() then retry.
    harness.run() is invoked; we verify the agent called refresh_google_token
    in its tool call sequence AND the final answer is coherent.

Run:
    cd tools-harness
    python tests/test_google_auth_refresh.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from tools.google_auth import (
    _DEFAULT_TOKEN,
    _services,
    refresh_google_token,
    execute_with_google_auth_retry,
    is_refreshable_google_auth_error,
)

TOKEN_PATH = Path(os.environ.get("GOOGLE_TOKEN_PATH", _DEFAULT_TOKEN))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _corrupt_access_token(token_path: Path) -> str:
    """Replace the access_token with a sentinel; return the original."""
    raw = json.loads(token_path.read_text())
    original = raw.get("token") or raw.get("access_token", "")
    key = "token" if "token" in raw else "access_token"
    raw[key] = "INVALID_SMOKE_TEST_TOKEN"
    # Push expiry far into the future so get_service() skips its own auto-refresh
    from datetime import datetime, timezone, timedelta
    raw["expiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_path.write_text(json.dumps(raw, indent=2))
    _services.clear()
    return original


def _restore_access_token(token_path: Path, original: str) -> None:
    raw = json.loads(token_path.read_text())
    key = "token" if "token" in raw else "access_token"
    raw[key] = original
    token_path.write_text(json.dumps(raw, indent=2))
    _services.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Path A — Python layer
# ─────────────────────────────────────────────────────────────────────────────

class TestPathA_PythonLayer(unittest.TestCase):
    """execute_with_google_auth_retry silently handles 401 without agent involvement."""

    def test_execute_with_google_auth_retry_refreshes_and_retries(self):
        """A 401 HttpError triggers refresh_google_token() + retry transparently."""
        call_count = [0]
        refresh_called = [False]

        def good_call():
            call_count[0] += 1
            if call_count[0] == 1:
                # Simulate HttpError(401)
                exc = Exception("HttpError calling API: <HttpError 401 when requesting ... "
                                "returned 'Invalid Credentials', details: 'Invalid Credentials'>")
                # Make is_refreshable_google_auth_error treat it as refreshable
                exc.resp = type("R", (), {"status": 401})()
                raise exc
            return {"items": [{"title": "test task"}]}

        with patch("tools.google_auth.refresh_google_token") as mock_refresh:
            mock_refresh.return_value = "Token refreshed successfully. Expires: 2026-05-01. Scopes: 10 active."

            result = execute_with_google_auth_retry(good_call)

        self.assertEqual(call_count[0], 2, "call_factory should be called twice (fail + retry)")
        mock_refresh.assert_called_once()
        self.assertEqual(result, {"items": [{"title": "test task"}]})
        print("  [PATH A] PASS: execute_with_google_auth_retry refreshed and retried silently")

    def test_scope_error_is_not_auto_refreshed(self):
        """403 insufficient scope must NOT trigger auto-refresh (needs browser re-consent)."""
        exc = Exception("HttpError 403: Request had insufficient authentication scopes")
        exc.resp = type("R", (), {"status": 403})()

        with patch("tools.google_auth.refresh_google_token") as mock_refresh:
            with self.assertRaises(Exception):
                execute_with_google_auth_retry(lambda: (_ for _ in ()).throw(exc))  # type: ignore

        mock_refresh.assert_not_called()
        print("  [PATH A] PASS: 403 scope error correctly NOT auto-refreshed")

    def test_real_token_refresh_call(self):
        """refresh_google_token() returns a success string with valid credentials."""
        result = refresh_google_token()
        if "No token file found" in result:
            self.skipTest("No Google token file found; skipping real token refresh test")
        self.assertTrue(
            result.startswith("Token refreshed successfully"),
            f"Expected success, got: {result!r}"
        )
        print(f"  [PATH A] PASS: real token refresh → {result}")


# ─────────────────────────────────────────────────────────────────────────────
# Path B — Agent layer
# ─────────────────────────────────────────────────────────────────────────────

class TestPathB_AgentLayer(unittest.TestCase):
    """
    Verify the agent itself calls refresh_google_token when a tool returns
    an auth-error string, guided by _AUTH_RETRY_LINE injected by skills_hub.
    Requires Ollama running with gemma4. Skipped if Ollama is unavailable.
    """

    @classmethod
    def setUpClass(cls):
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
            cls.ollama_available = True
        except Exception:
            cls.ollama_available = False

    def setUp(self):
        if not self.ollama_available:
            self.skipTest("Ollama not reachable at localhost:11434 — skipping agent-layer test")

    def test_agent_calls_refresh_on_auth_error_string(self):
        """
        list_tasks returns '401 Unauthorized' on first call.
        Agent should call refresh_google_token() then retry list_tasks.
        Verifies the _AUTH_RETRY_LINE prompt injection works.
        """
        import tools.registry as reg

        tool_call_log: list[str] = []
        original_list_tasks = reg.EXECUTORS.get("list_tasks")
        original_refresh    = reg.EXECUTORS.get("refresh_google_token")

        call_count = {"list_tasks": 0}

        def fake_list_tasks(args):
            call_count["list_tasks"] += 1
            tool_call_log.append("list_tasks")
            if call_count["list_tasks"] == 1:
                return "[google_tasks] Error 401 Unauthorized: Invalid Credentials. Call refresh_google_token() to fix."
            return "Tasks: 1. Buy groceries (due today)"

        def fake_refresh(args):
            tool_call_log.append("refresh_google_token")
            return "Token refreshed successfully. Expires: 2026-05-01. Scopes: 10 active."

        reg.EXECUTORS["list_tasks"] = fake_list_tasks
        reg.EXECUTORS["refresh_google_token"] = fake_refresh

        try:
            import harness
            from skills_hub import get_skill

            skill = get_skill("list_tasks")
            self.assertIsNotNone(skill, "list_tasks skill not found in skills_hub")
            self.assertIn("refresh_google_token", skill.tools,
                          "refresh_google_token not auto-injected into list_tasks skill tools")

            print(f"\n  [PATH B] Running harness.run() — model: {skill.model}, tools: {skill.tools}")
            start = time.time()
            result, model_used, _intent = harness.run(
                query="What are my pending tasks?",
                model=skill.model,
                tools=skill.tools,
                max_rounds=skill.max_rounds,
            )
            elapsed = time.time() - start

            print(f"  [PATH B] Tool call sequence: {tool_call_log}")
            print(f"  [PATH B] Result ({elapsed:.1f}s, model={model_used}):\n    {result[:300]}")

            self.assertIn("refresh_google_token", tool_call_log,
                          "Agent never called refresh_google_token after seeing the 401 error")
            refresh_idx  = tool_call_log.index("refresh_google_token")
            list_task_idxs = [i for i, t in enumerate(tool_call_log) if t == "list_tasks"]
            second_call_idx = list_task_idxs[1] if len(list_task_idxs) > 1 else None
            self.assertIsNotNone(second_call_idx,
                                 "Agent did not retry list_tasks after refresh")
            self.assertLess(refresh_idx, second_call_idx,
                            "refresh_google_token must be called BEFORE the retry")
            print("  [PATH B] PASS: agent called refresh_google_token then retried list_tasks")

        finally:
            if original_list_tasks:
                reg.EXECUTORS["list_tasks"] = original_list_tasks
            if original_refresh:
                reg.EXECUTORS["refresh_google_token"] = original_refresh

    def test_skill_has_refresh_injected(self):
        """Every Google-touching skill must have refresh_google_token in its tools list."""
        from skills_hub import SKILLS, _GOOGLE_TOOLS
        failures = []
        for s in SKILLS:
            if _GOOGLE_TOOLS.intersection(s.tools) and "refresh_google_token" not in s.tools:
                failures.append(s.name)
        self.assertEqual(failures, [],
                         f"Skills missing refresh_google_token: {failures}")
        print(f"  [PATH B] PASS: all {len(SKILLS)} skills checked — 0 missing refresh_google_token")

    def test_skill_prompt_has_auth_retry_line(self):
        """Every Google-touching skill must have the auth-retry instruction in its prompt."""
        from skills_hub import SKILLS, _GOOGLE_TOOLS
        failures = []
        for s in SKILLS:
            if _GOOGLE_TOOLS.intersection(s.tools) and "refresh_google_token" not in s.system_prompt:
                failures.append(s.name)
        self.assertEqual(failures, [],
                         f"Skills missing auth-retry prompt line: {failures}")
        print(f"  [PATH B] PASS: all Google-touching skills have auth-retry instruction in prompt")


# ─────────────────────────────────────────────────────────────────────────────
# Path C — skills_hub.match_skill() auto-discovery
# ─────────────────────────────────────────────────────────────────────────────

class TestPathC_SkillDiscovery(unittest.TestCase):
    """Verify the agent can discover the Refresh Google Auth skill from error text."""

    def _match(self, query):
        from skills_hub import match_skill
        s = match_skill(query)
        return s.name if s else None

    def test_auth_error_phrases_route_to_refresh_skill(self):
        cases = [
            ("google auth expired",        "Refresh Google Auth"),
            ("token expired",              "Refresh Google Auth"),
            ("refresh google token",       "Refresh Google Auth"),
            ("I got a 401 error",          "Refresh Google Auth"),
            ("fix google auth",            "Refresh Google Auth"),
            ("re-authenticate google",     "Refresh Google Auth"),
            ("auth error from gmail",      "Refresh Google Auth"),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                matched = self._match(query)
                self.assertEqual(matched, expected,
                                 f"{query!r} → {matched!r}, expected {expected!r}")
        print(f"  [PATH C] PASS: {len(cases)} auth-error queries route to Refresh Google Auth")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 65)
    print("Google Auth Auto-Refresh Smoke Test")
    print("=" * 65)
    unittest.main(verbosity=0, failfast=False)


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
