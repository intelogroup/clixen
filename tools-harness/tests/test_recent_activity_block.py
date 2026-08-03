import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import event_log


class RecentActivityBlockTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = event_log._DB_PATH
        event_log._DB_PATH = Path(self._tmpdir.name) / "event_log.sqlite"

    def tearDown(self):
        event_log._DB_PATH = self._orig_path
        self._tmpdir.cleanup()

    def _insert(self, ts, kind, status, detail):
        import json
        with event_log._conn() as conn:
            conn.execute(
                "INSERT INTO events (ts, kind, status, detail) VALUES (?, ?, ?, ?)",
                (ts, kind, status, json.dumps(detail)),
            )

    def test_no_events_returns_empty(self):
        self.assertEqual(event_log.recent_activity_block(), "")

    def test_done_event_includes_result_summary(self):
        self._insert(time.time(), "workflow", "done", {"workflow_id": "w1", "automation_id": "tech_brief"})
        with mock.patch("store.workflow_store.get_workflow_instance",
                         return_value={"last_result": {"message": "found 3 articles"}}):
            block = event_log.recent_activity_block()
        self.assertTrue(block.startswith("## Recent background activity"))
        self.assertIn("tech_brief", block)
        self.assertIn("found 3 articles", block)

    def test_failed_event_includes_error(self):
        self._insert(time.time(), "workflow", "failed", {"workflow_id": "w2", "automation_id": "email.daily_summary", "error": "SMTP timeout"})
        block = event_log.recent_activity_block()
        self.assertIn("FAILED", block)
        self.assertIn("SMTP timeout", block)

    def test_started_only_produces_no_lines(self):
        self._insert(time.time(), "workflow", "started", {"workflow_id": "w3", "automation_id": "tech_brief"})
        self.assertEqual(event_log.recent_activity_block(), "")

    def test_event_outside_window_excluded(self):
        old_ts = time.time() - (4 * 3600)  # 4h ago, outside default 3h window
        self._insert(old_ts, "workflow", "failed", {"workflow_id": "w4", "automation_id": "tech_brief", "error": "x"})
        self.assertEqual(event_log.recent_activity_block(hours=3.0), "")

    def test_limit_caps_number_of_lines(self):
        now = time.time()
        for i in range(10):
            self._insert(now - i, "workflow", "failed", {"workflow_id": f"w{i}", "automation_id": f"auto{i}", "error": "x"})
        block = event_log.recent_activity_block(limit=8)
        self.assertEqual(block.count("- ["), 8)


if __name__ == "__main__":
    unittest.main()
