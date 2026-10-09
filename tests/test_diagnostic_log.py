import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


MCP_ROOT = Path(__file__).resolve().parents[1] / "tools" / "laowu_mcp"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_ROOT))

import server


class DiagnosticLogRotationTests(unittest.TestCase):
    def test_rotates_before_limit_and_keeps_only_the_latest_backup(self):
        with TemporaryDirectory(prefix=".test-dispatch-log-", dir=PROJECT_ROOT) as temp_dir:
            log_path = Path(temp_dir) / "dispatch.jsonl"
            backup_path = log_path.with_name(log_path.name + ".1")

            with patch.object(server, "DIAGNOSTIC_LOG", log_path), patch.object(
                server, "MAX_DIAGNOSTIC_LOG_BYTES", 1024, create=True
            ), contextlib.redirect_stderr(io.StringIO()):
                server._log_dispatch_event("event-1", None)
                line_size = log_path.stat().st_size
                server.MAX_DIAGNOSTIC_LOG_BYTES = 2 * line_size

                server._log_dispatch_event("event-2", None)
                self.assertEqual(log_path.stat().st_size, 2 * line_size)
                self.assertFalse(backup_path.exists())

                server._log_dispatch_event("event-3", None)
                server._log_dispatch_event("event-4", None)
                server._log_dispatch_event("event-5", None)

            self.assertTrue(backup_path.exists())
            self.assertLessEqual(log_path.stat().st_size, 2 * line_size)
            self.assertLessEqual(backup_path.stat().st_size, 2 * line_size)
            self.assertLessEqual(log_path.stat().st_size + backup_path.stat().st_size, 4 * line_size)

            current_events = [json.loads(line)["event"] for line in log_path.read_text(encoding="utf-8").splitlines()]
            backup_events = [json.loads(line)["event"] for line in backup_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(backup_events, ["event-3", "event-4"])
            self.assertEqual(current_events, ["event-5"])


if __name__ == "__main__":
    unittest.main()
