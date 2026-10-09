import json
import sys
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools" / "laowu_mcp"))
import server


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(TemporaryDirectory(prefix=".test-lifecycle-", dir=PROJECT_ROOT))
        fixture = Path(folder)
        paths = {
            "USER_CONFIG_PATH": fixture / "missing-config.json", "USER_CONFIG": {},
            "CREDENTIAL_STORE_PATH": fixture / "keys.xml", "ACTIVITY_STORE_PATH": fixture / "state.json",
            "UI_PREFERENCES_PATH": fixture / "preferences.json", "DIAGNOSTIC_LOG": fixture / "debug.jsonl",
            "LEGACY_ACTIVITY_STORE_PATH": fixture / "legacy.json", "LEGACY_DIAGNOSTIC_LOG_PATH": fixture / "legacy-debug.jsonl",
            "WORKSPACE_PATH": PROJECT_ROOT, "PERSISTED_STATE": server._default_state(), "STATE_LOAD_ERROR": None,
            "ACTIVITIES": {}, "ACTIVITY_ORDER": [], "CANCEL_EVENTS": {}, "ACTIVITY_SECRET_VALUES": {}, "API_KEYS": {},
        }
        for name in ("PROVIDERS", "PROVIDER_MODELS", "PROVIDER_LABELS", "PROVIDER_ORDINALS", "PROVIDER_IDS",
                     "KEY_NAMES", "ROUTE_SETTINGS", "MODEL", "DELETED_PROVIDERS", "GROUPS",
                     "GROUP_ROUTES", "AUTO_ROUTES", "AUTO_MODES"):
            value = getattr(server, name)
            paths[name] = value.copy() if isinstance(value, (dict, list, set)) else value
        for name, value in paths.items():
            self.stack.enter_context(patch.object(server, name, value))
        self.stack.enter_context(patch.object(server, "_write_notification"))
        self.stack.enter_context(patch.object(server, "_log_dispatch_event"))

    def completed_activity(self):
        activity_id = server._new_activity("reviewer", {
            "task": "fixture", "cwd": str(PROJECT_ROOT), "master_recall": True,
            "allowMcpTools": False, "allowSkills": False, "model": "fixture-model",
        })
        server._set_activity_state(activity_id, status="completed", sessionId="fixture-session",
                                   currentProviderId="route_a_group_1", model="fixture-model")
        server.API_KEYS[server.KEY_NAMES["route_a_group_1"]] = "synthetic-fixture-key"
        return activity_id

    def test_continuation_can_be_cancelled_and_cleans_up_its_event(self):
        activity_id = self.completed_activity()
        observed = {}

        def provider(*args, **kwargs):
            observed["event"] = kwargs.get("cancel_event")
            observed["cancel"] = server._cancel_subagent_activity(activity_id)
            return 130, "", "Task cancelled by user."

        with patch.object(server, "_run_provider", side_effect=provider):
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})
        self.assertIsNotNone(observed["event"], "Continuation did not forward a cancellation event")
        self.assertTrue(observed["event"].is_set())
        self.assertIn("Stop requested", observed["cancel"]["content"][0]["text"])
        self.assertTrue(result["isError"])
        self.assertEqual(server.ACTIVITIES[activity_id]["status"], "cancelled")
        self.assertNotIn(activity_id, server.CANCEL_EVENTS)

    def test_reset_rejects_a_running_continuation(self):
        activity_id = self.completed_activity()

        def provider(*args, **kwargs):
            reset = server._reset_configuration(True)
            self.assertTrue(reset["isError"])
            self.assertIn(activity_id, server.ACTIVITIES)
            return 0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}), ""

        with patch.object(server, "_run_provider", side_effect=provider):
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})
        self.assertFalse(result["isError"])
        self.assertNotIn(activity_id, server.CANCEL_EVENTS)

    def test_cancel_cleanup_failure_is_reported_as_failed_and_cannot_resume_again(self):
        activity_id = self.completed_activity()

        def provider(*args, **kwargs):
            server._cancel_subagent_activity(activity_id)
            return 70, "", "Local process cleanup failed: descendant exit could not be confirmed."

        with patch.object(server, "_run_provider", side_effect=provider) as run:
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})
            retry = server._continue_activity({"activity_id": activity_id, "message": "retry"})
        self.assertTrue(result["isError"])
        self.assertIn("cleanup failed", result["content"][0]["text"])
        self.assertEqual(server.ACTIVITIES[activity_id]["status"], "failed")
        self.assertTrue(retry["isError"])
        run.assert_called_once()
        self.assertNotIn(activity_id, server.CANCEL_EVENTS)

    def test_continuation_reserves_its_pending_result_before_publishing_running_activity(self):
        activity_id = self.completed_activity()
        server._add_pending_results([{
            "activityId": activity_id, "role": "reviewer", "status": "completed", "task": "fixture",
            "group": "auto", "model": "fixture-model", "startedAt": server._activity_time(),
            "updatedAt": server._activity_time(), "readAt": server._activity_time(), "result": "old result",
        }])
        observed = {}
        original_append = server._append_activity_event

        def observe_transition(*args, **kwargs):
            original_append(*args, **kwargs)
            if len(args) > 3 and args[3] == "补充指令":
                observed["get"] = server._pending_result_action({"activity_id": activity_id, "action": "get"})
                observed["ack"] = server._pending_result_action({"activity_id": activity_id, "action": "acknowledge"})

        output = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "new result"}})
        with patch.object(server, "_append_activity_event", side_effect=observe_transition), patch.object(
            server, "_run_provider", return_value=(0, output, ""),
        ):
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})
        self.assertFalse(result["isError"])
        self.assertFalse(observed["get"]["structuredContent"]["ready"], "Published running activity still exposed the old terminal result")
        self.assertTrue(observed["ack"]["isError"], "Old result acknowledgement removed a running continuation")
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["result"], "new result")

    def test_reset_and_task_reservations_do_not_overlap(self):
        entered = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        reserved = threading.Event()
        outcome = {}
        original_save = server.save_state

        def hold_reset(state):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Reset fixture was not released")
            original_save(state)

        def reset():
            outcome["reset"] = server._reset_configuration(True)

        def reserve():
            attempted.set()
            activity_id = "d" * 32
            server._add_pending_results([{
                "activityId": activity_id, "role": "reviewer", "status": "queued", "task": "fixture",
                "group": "auto", "model": "fixture-model", "startedAt": server._activity_time(),
                "updatedAt": server._activity_time(), "result": "",
            }])
            server._new_activity("reviewer", {"task": "fixture", "cwd": str(PROJECT_ROOT)}, activity_id, "queued")
            outcome["activity_id"] = activity_id
            reserved.set()

        with patch.object(server, "save_state", side_effect=hold_reset):
            reset_thread = threading.Thread(target=reset)
            reserve_thread = threading.Thread(target=reserve)
            try:
                reset_thread.start()
                self.assertTrue(entered.wait(2))
                reserve_thread.start()
                self.assertTrue(attempted.wait(2))
                overlapped = reserved.wait(0.15)
            finally:
                release.set()
                reset_thread.join(4)
                if reserve_thread.ident is not None:
                    reserve_thread.join(4)
        self.assertFalse(reset_thread.is_alive())
        self.assertFalse(reserve_thread.is_alive())
        self.assertFalse(overlapped, "Task reservation entered while reset was in progress")
        self.assertTrue(outcome["reset"].get("structuredContent", {}).get("reset"))
        self.assertIn(outcome["activity_id"], server.ACTIVITIES)
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["activityId"], outcome["activity_id"])

    def test_terminal_result_read_cannot_overwrite_a_new_running_result(self):
        activity_id = "e" * 32
        server._add_pending_results([{
            "activityId": activity_id, "role": "reviewer", "status": "completed", "task": "fixture",
            "group": "auto", "model": "fixture-model", "startedAt": server._activity_time(),
            "updatedAt": server._activity_time(), "result": "old result",
        }])
        entered = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        updated = threading.Event()
        failures = []
        original_update = server._update_pending_result

        def pause_read(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Read fixture was not released")
            return original_update(*args, **kwargs)

        def read():
            try:
                server._pending_result_action({"activity_id": activity_id, "action": "get"})
            except Exception as exc:
                failures.append(exc)

        def begin_new_run():
            attempted.set()
            try:
                original_update(activity_id, status="running", result="", readAt="")
                updated.set()
            except Exception as exc:
                failures.append(exc)

        with patch.object(server, "_update_pending_result", side_effect=pause_read):
            reader = threading.Thread(target=read)
            producer = threading.Thread(target=begin_new_run)
            try:
                reader.start()
                self.assertTrue(entered.wait(2))
                producer.start()
                self.assertTrue(attempted.wait(2))
                overlapped = updated.wait(0.15)
            finally:
                release.set()
                reader.join(4)
                if producer.ident is not None:
                    producer.join(4)
        self.assertFalse(reader.is_alive())
        self.assertFalse(producer.is_alive())
        self.assertEqual(failures, [])
        self.assertFalse(overlapped, "A new run updated the result during an unfinished terminal read")
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["status"], "running")
        result = server._pending_result_action({"activity_id": activity_id, "action": "get"})
        self.assertFalse(result["structuredContent"]["ready"])

    def test_result_read_uses_the_same_lock_order_as_retained_activity_writes(self):
        activity_id = "f" * 32
        server._add_pending_results([{
            "activityId": activity_id, "role": "reviewer", "status": "running", "task": "fixture",
            "group": "auto", "model": "fixture-model", "startedAt": server._activity_time(),
            "updatedAt": server._activity_time(), "result": "",
        }])
        held = {"activity": 0, "state": 0}

        class OrderedLock:
            def __init__(self, name):
                self.name = name
                self.lock = threading.RLock()

            def __enter__(self):
                if self.name == "activity" and held["state"] and not held["activity"]:
                    raise AssertionError("Result read reverses retained-write ACTIVITY -> STATE lock order")
                self.lock.acquire()
                held[self.name] += 1

            def __exit__(self, *args):
                held[self.name] -= 1
                self.lock.release()

        with patch.object(server, "ACTIVITY_LOCK", OrderedLock("activity")), patch.object(server, "STATE_LOCK", OrderedLock("state")):
            result = server._pending_result_action({"activity_id": activity_id, "action": "get"})
        self.assertFalse(result["structuredContent"]["ready"])


if __name__ == "__main__":
    unittest.main()
