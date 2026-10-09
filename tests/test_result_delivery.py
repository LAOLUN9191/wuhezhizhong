import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


MCP_ROOT = Path(__file__).resolve().parents[1] / "tools" / "laowu_mcp"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_ROOT))

import server


def pending_result(activity_id, status="completed", result="fixture result"):
    now = "2026-10-04T00:00:00+00:00"
    return {
        "activityId": activity_id,
        "role": "tester",
        "status": status,
        "task": "fixture task",
        "group": "auto",
        "model": "model-a",
        "startedAt": now,
        "updatedAt": now,
        "result": result,
    }


class ResultDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(prefix=".test-result-", dir=PROJECT_ROOT)
        self.previous = {
            "ACTIVITY_STORE_PATH": server.ACTIVITY_STORE_PATH,
            "PERSISTED_STATE": server.PERSISTED_STATE,
            "STATE_LOAD_ERROR": server.STATE_LOAD_ERROR,
            "ACTIVITIES": server.ACTIVITIES,
            "ACTIVITY_ORDER": server.ACTIVITY_ORDER,
            "CANCEL_EVENTS": server.CANCEL_EVENTS,
            "ACTIVITY_SECRET_VALUES": server.ACTIVITY_SECRET_VALUES,
            "PROVIDER_MODELS": server.PROVIDER_MODELS,
            "API_KEYS": server.API_KEYS,
            "_load_api_keys": server._load_api_keys,
        }
        server.ACTIVITY_STORE_PATH = Path(self.temp_dir.name) / "state.json"
        server.PERSISTED_STATE = server._default_state()
        server.STATE_LOAD_ERROR = None
        server.ACTIVITIES = {}
        server.ACTIVITY_ORDER = []
        server.CANCEL_EVENTS = {}
        server.ACTIVITY_SECRET_VALUES = {}
        server.PROVIDER_MODELS = {**server.PROVIDER_MODELS, "route_b_group_1": "fixture-model-1", "route_b_group_2": "fixture-model-2"}
        server.API_KEYS = {
            server.KEY_NAMES["route_b_group_1"]: "fixture-key-1",
            server.KEY_NAMES["route_b_group_2"]: "fixture-key-2",
        }
        server._load_api_keys = lambda: dict(server.API_KEYS)

    def tearDown(self):
        for name, value in self.previous.items():
            setattr(server, name, value)
        self.temp_dir.cleanup()

    def parallel_call(self, count=1):
        return {
            "name": "run_subagents_parallel",
            "arguments": {
                "tasks": [
                    {
                        "role": "tester",
                        "task": f"fixture {index}",
                        "cwd": str(PROJECT_ROOT),
                        "group": "auto",
                        "route_choice": "route_b",
                    }
                    for index in range(count)
                ]
            },
        }

    def call_tool(self, name, arguments):
        with patch.object(server, "_write_response") as write_response, patch.object(
            server, "_log_dispatch_event"
        ):
            server._handle_tool_call("fixture-request", {"name": name, "arguments": arguments})
        call = write_response.call_args
        return call.kwargs.get("result") or {"isError": True, "error": call.kwargs.get("error")}

    def test_parallel_returns_ids_before_worker_finishes(self):
        started = threading.Event()
        release = threading.Event()
        worker_finished = threading.Event()
        handler_finished = threading.Event()
        responses = []

        def fake_execute(role, arguments, request_id, callback=None, initiator="codex", profile_id=None, **kwargs):
            started.set()
            release.wait(timeout=5)
            worker_finished.set()
            return {
                "activity_id": kwargs.get("activity_id"),
                "role": role,
                "result": {"content": [{"type": "text", "text": "fixture result"}]},
            }

        def dispatch():
            server._handle_tool_call("parallel-request", self.parallel_call())
            handler_finished.set()

        try:
            returned_early = False
            worker_was_waiting = False
            with patch.object(server, "_execute_subagent_call", side_effect=fake_execute), patch.object(
                server, "_write_response", side_effect=lambda request_id, result=None, error=None: responses.append(result)
            ), patch.object(server, "_log_dispatch_event"):
                handler = threading.Thread(target=dispatch)
                handler.start()
                self.assertTrue(started.wait(timeout=2), "parallel worker was not submitted")
                returned_early = handler_finished.wait(timeout=0.2)
                worker_was_waiting = not worker_finished.is_set()
                release.set()
                handler.join(timeout=2)
            self.assertTrue(returned_early, "parallel tool waited for worker completion")
            self.assertTrue(worker_was_waiting, "worker completed before the ack was checked")
            self.assertEqual(len(responses), 1)
            tasks = responses[0]["structuredContent"]["tasks"]
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["role"], "tester")
            self.assertEqual(tasks[0]["status"], "queued")
            self.assertTrue(tasks[0]["activity_id"])
        finally:
            release.set()
            if "handler" in locals():
                handler.join(timeout=5)

    def test_get_is_repeatable_until_terminal_acknowledgement(self):
        activity_id = "a" * 32
        state = server._default_state()
        state["pendingResults"] = [pending_result(activity_id)]
        server.PERSISTED_STATE = server._validate_state(state)
        server.save_state(server.PERSISTED_STATE)

        early_ack = self.call_tool(
            "laowu_task_result", {"activity_id": activity_id, "action": "acknowledge"}
        )
        self.assertTrue(early_ack["isError"])
        self.assertEqual(len(server.PERSISTED_STATE["pendingResults"]), 1)

        first = self.call_tool("laowu_task_result", {"activity_id": activity_id, "action": "get"})
        second = self.call_tool("laowu_task_result", {"activity_id": activity_id, "action": "get"})
        self.assertFalse(first.get("isError", False))
        self.assertEqual(first["structuredContent"]["result"], "fixture result")
        self.assertEqual(first["structuredContent"], second["structuredContent"])

        acknowledged = self.call_tool(
            "laowu_task_result", {"activity_id": activity_id, "action": "acknowledge"}
        )
        self.assertFalse(acknowledged.get("isError", False))
        self.assertEqual(server.PERSISTED_STATE["pendingResults"], [])

    def test_result_storage_error_can_recover_and_be_acknowledged(self):
        activity_id = "9" * 32
        state = server._default_state()
        state["pendingResults"] = [pending_result(activity_id, status="running", result="")]
        server.PERSISTED_STATE = server._validate_state(state)
        server.save_state(server.PERSISTED_STATE)
        server._new_activity("tester", {"task": "fixture", "cwd": str(PROJECT_ROOT)}, activity_id=activity_id)
        server._set_activity_state(
            activity_id, status="completed", pendingResultText="finished result",
            storageError="Could not persist pending result: OSError",
        )

        result = self.call_tool("laowu_task_result", {"activity_id": activity_id, "action": "get"})
        self.assertEqual(result["structuredContent"]["status"], "completed")
        self.assertNotIn("storage_error", result["structuredContent"])
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["result"], "finished result")
        acknowledged = self.call_tool(
            "laowu_task_result", {"activity_id": activity_id, "action": "acknowledge"}
        )
        self.assertFalse(acknowledged.get("isError", False))

    def test_parallel_worker_persists_sanitized_final_text(self):
        activity_id = "e" * 32
        state = server._default_state()
        state["pendingResults"] = [pending_result(activity_id, status="queued", result="")]
        server.PERSISTED_STATE = server._validate_state(state)
        server.save_state(server.PERSISTED_STATE)
        arguments = {
            "task": "fixture task",
            "cwd": str(PROJECT_ROOT),
            "group": "auto",
            "route_choice": "route_b",
            "profileId": "builtin-tester",
            "profileName": "验证员",
        }
        server._new_activity("tester", arguments, activity_id=activity_id, status="queued")
        cancel_event = threading.Event()
        server.CANCEL_EVENTS[activity_id] = cancel_event
        secret_fixture = "sk-0123456789abcdef0123456789abcdef"  # Synthetic redaction fixture only; gitleaks:allow

        with patch.object(
            server,
            "run_subagent_task",
            return_value={"content": [{"type": "text", "text": f"Final report {secret_fixture}"}]},
        ), patch.object(server, "_log_dispatch_event"):
            server._execute_subagent_call(
                "tester", arguments, "fixture-request", activity_id=activity_id,
                cancel_event=cancel_event, pending_result=True,
            )

        result = self.call_tool("laowu_task_result", {"activity_id": activity_id, "action": "get"})
        text = result["structuredContent"]["result"]
        self.assertEqual(result["structuredContent"]["status"], "completed")
        self.assertIn("[redacted key]", text)
        self.assertNotIn(secret_fixture, server.ACTIVITY_STORE_PATH.read_text(encoding="utf-8"))

    def test_successful_task_wins_over_late_cancel_request(self):
        activity_id = "f" * 32
        state = server._default_state()
        state["pendingResults"] = [pending_result(activity_id, status="queued", result="")]
        server.PERSISTED_STATE = server._validate_state(state)
        server.save_state(server.PERSISTED_STATE)
        arguments = {
            "task": "fixture task", "cwd": str(PROJECT_ROOT), "group": "auto",
            "route_choice": "route_b", "profileId": "builtin-tester",
        }
        server._new_activity("tester", arguments, activity_id=activity_id, status="queued")
        cancel_event = threading.Event()
        server.CANCEL_EVENTS[activity_id] = cancel_event

        def finish_successfully(*_args, **_kwargs):
            cancel_event.set()
            return {"content": [{"type": "text", "text": "completed result"}]}

        with patch.object(server, "run_subagent_task", side_effect=finish_successfully), patch.object(
            server, "_log_dispatch_event"
        ):
            server._execute_subagent_call(
                "tester", arguments, "fixture-request", activity_id=activity_id,
                cancel_event=cancel_event, pending_result=True,
            )

        self.assertEqual(server.ACTIVITIES[activity_id]["status"], "completed")
        result = self.call_tool("laowu_task_result", {"activity_id": activity_id, "action": "get"})
        self.assertEqual(result["structuredContent"]["status"], "completed")
        self.assertEqual(result["structuredContent"]["result"], "completed result")

    def test_pending_results_survive_restart_and_running_items_become_interrupted(self):
        state = server._default_state()
        state["pendingResults"] = [
            pending_result("b" * 32, result="finished text"),
            pending_result("c" * 32, status="running", result=""),
        ]
        server.save_state(state)

        restored = server.load_state()
        self.assertIn("pendingResults", restored)
        by_id = {item["activityId"]: item for item in restored["pendingResults"]}
        self.assertEqual(by_id["b" * 32]["result"], "finished text")
        self.assertEqual(by_id["c" * 32]["status"], "interrupted")

    def test_legacy_state_without_pending_results_loads_empty_list(self):
        state = server._default_state()
        state.pop("pendingResults", None)
        normalized = server._validate_state(state)
        self.assertEqual(normalized.get("pendingResults"), [])

    def test_capacity_refusal_happens_before_worker_submission(self):
        state = server._default_state()
        state["pendingResults"] = [pending_result(f"{index:032x}") for index in range(30)]
        server.PERSISTED_STATE = server._validate_state(state)
        server.save_state(server.PERSISTED_STATE)

        with patch.object(server.PARALLEL_WORKERS, "submit") as submit, patch.object(
            server, "_execute_subagent_call", return_value={"activity_id": "d" * 32, "role": "tester", "result": {"content": []}}
        ), patch.object(server, "_write_response") as write_response, patch.object(server, "_log_dispatch_event"):
            server._handle_tool_call("capacity-request", self.parallel_call())

        call = write_response.call_args
        result = call.kwargs.get("result") or {"isError": True, "error": call.kwargs.get("error")}
        self.assertTrue(result["isError"])
        submit.assert_not_called()
        self.assertEqual(len(server.PERSISTED_STATE["pendingResults"]), 30)
        self.assertEqual(server.ACTIVITIES, {})

    def test_state_write_failure_refuses_batch_before_worker_submission(self):
        with patch.object(server.PARALLEL_WORKERS, "submit") as submit, patch.object(
            server, "save_state", side_effect=OSError("fixture write failure")
        ), patch.object(server, "_write_response") as write_response, patch.object(server, "_log_dispatch_event"):
            server._handle_tool_call("storage-request", self.parallel_call())

        call = write_response.call_args
        result = call.kwargs.get("result") or {"isError": True, "error": call.kwargs.get("error")}
        self.assertTrue(result["isError"])
        submit.assert_not_called()
        self.assertEqual(server.ACTIVITIES, {})
        self.assertEqual(server.PERSISTED_STATE["pendingResults"], [])

    def test_invalid_parallel_batch_creates_no_activity(self):
        request = self.parallel_call(count=2)
        request["arguments"]["tasks"][1]["route_choice"] = "not-a-route"
        with patch.object(server.PARALLEL_WORKERS, "submit") as submit, patch.object(
            server, "_write_response"
        ) as write_response, patch.object(server, "_log_dispatch_event"):
            server._handle_tool_call("invalid-request", request)

        call = write_response.call_args
        result = call.kwargs.get("result") or {"isError": True, "error": call.kwargs.get("error")}
        self.assertTrue(result["isError"])
        submit.assert_not_called()
        self.assertEqual(server.ACTIVITIES, {})
        self.assertEqual(server.PERSISTED_STATE["pendingResults"], [])


if __name__ == "__main__":
    unittest.main()
