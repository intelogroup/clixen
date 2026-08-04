"""
Tests for the WhatsApp reconnect-storm watchdog.

Covers two layers:
1. The pure TS state machine (tools-harness/whatsapp_reconnect_state.ts),
   driven via `npx tsx` — no live WhatsApp socket involved.
2. chat_ui's _whatsapp_connection_snapshot parsing of the new stuck /
   repairNeeded / stuckReason fields (mocked httpx bridge response).
"""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

_HARNESS = os.path.join(os.path.dirname(__file__), "..")
_STATE_MODULE = os.path.join(_HARNESS, "whatsapp_reconnect_state.ts")
_IMPORT = f"import * as S from '{_STATE_MODULE}';"


def _tsx(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "tsx", "-e", _IMPORT + script],
        cwd=_HARNESS,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestReconnectStateMachine(unittest.TestCase):
    """whatsapp_reconnect_state.ts: storm detection + slow-probe backoff."""

    def _run(self, body: str) -> str:
        proc = _tsx(body)
        self.assertEqual(
            proc.returncode, 0, f"tsx failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
        return proc.stdout

    def test_single_blip_does_not_trigger_stuck(self):
        out = self._run(
            """
            const s = S.initialState();
            S.recordReconnectFailure(s, 'connectionClosed', 'Connection Terminated');
            console.log(s.stuck, s.consecutiveFailures);
            """
        )
        self.assertEqual(out.strip(), "false 1")

    def test_sixth_failure_enters_stuck_and_returns_probe_delay(self):
        out = self._run(
            """
            const s = S.initialState();
            let enteredAt = -1;
            const delays = [];
            for (let i = 0; i < S.STORM_THRESHOLD + 1; i++) {
              if (S.recordReconnectFailure(s, 'unknown', 'Connection Failure')) enteredAt = i + 1;
              delays.push(S.reconnectDelay(s));
            }
            console.log(JSON.stringify({ stuck: s.stuck, enteredAt, lastDelay: delays.at(-1) }));
            """
        )
        data = json.loads(out.strip())
        self.assertTrue(data["stuck"])
        self.assertEqual(data["enteredAt"], 6)
        self.assertEqual(data["lastDelay"], 600_000)  # 10 min probe while stuck

    def test_stuck_reason_captured(self):
        out = self._run(
            """
            const s = S.initialState();
            for (let i = 0; i < S.STORM_THRESHOLD; i++) S.recordReconnectFailure(s, 'unknown', 'Connection Failure');
            console.log(s.stuckReason);
            """
        )
        self.assertIn("unknown: Connection Failure", out.strip())

    def test_open_clears_stuck(self):
        out = self._run(
            """
            const s = S.initialState();
            for (let i = 0; i < S.STORM_THRESHOLD; i++) S.recordReconnectFailure(s, 'unknown', 'Connection Failure');
            S.resetReconnect(s);
            S.clearStuck(s);
            console.log(s.stuck, s.consecutiveFailures, s.stuckReason === '');
            """
        )
        self.assertEqual(out.strip(), "false 0 true")

    def test_normal_backoff_before_storm(self):
        out = self._run(
            """
            const s = S.initialState();
            console.log(JSON.stringify([S.reconnectDelay(s), S.reconnectDelay(s), S.reconnectDelay(s)]));
            """
        )
        self.assertEqual(out.strip(), "[3000,6000,12000]")


class TestWhatsappSnapshot(unittest.TestCase):
    """chat_ui._whatsapp_connection_snapshot parsing of stuck/repair fields."""

    def setUp(self):
        sys.path.insert(0, _HARNESS)
        import chat_ui

        self.snapshot = chat_ui._whatsapp_connection_snapshot

    def test_stuck_bridge_reports_repair_required(self):
        with patch("httpx.Client") as MockClient:
            resp = MockClient.return_value.__enter__.return_value.get.return_value
            resp.status_code = 200
            resp.json.return_value = {
                "status": "disconnected",
                "stuck": True,
                "stuckReason": "unknown: Connection Failure",
                "repairNeeded": True,
            }
            snap = self.snapshot()
        self.assertFalse(snap["connected"])
        self.assertTrue(snap["repairRequired"])
        self.assertIn("re-pair", snap["detail"])
        self.assertEqual(snap["repairReason"], "unknown: Connection Failure")

    def test_connected_bridge_no_repair(self):
        with patch("httpx.Client") as MockClient:
            resp = MockClient.return_value.__enter__.return_value.get.return_value
            resp.status_code = 200
            resp.json.return_value = {"status": "connected", "stuck": False}
            snap = self.snapshot()
        self.assertTrue(snap["connected"])
        self.assertFalse(snap["repairRequired"])
        self.assertEqual(snap["detail"], "WhatsApp connected")

    def test_unavailable_bridge(self):
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.side_effect = Exception("conn refused")
            snap = self.snapshot()
        self.assertFalse(snap["connected"])
        self.assertEqual(snap["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
