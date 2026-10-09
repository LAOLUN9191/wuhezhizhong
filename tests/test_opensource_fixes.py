import io
import json
import os
from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "laowu_mcp"))
import server


class OpenSourceFixTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(TemporaryDirectory(prefix=".test-opensource-", dir=ROOT)))
        values = {
            "USER_CONFIG_PATH": self.folder / "config.json", "USER_CONFIG": {},
            "ACTIVITY_STORE_PATH": self.folder / "state.json", "LEGACY_ACTIVITY_STORE_PATH": self.folder / "legacy.json",
            "PERSISTED_STATE": server._default_state(), "STATE_LOAD_ERROR": None,
            "ACTIVITIES": {}, "ACTIVITY_ORDER": [], "CANCEL_EVENTS": {}, "ACTIVITY_SECRET_VALUES": {},
            "WORKSPACE_PATH": ROOT, "API_KEYS": {server.KEY_NAMES["route_a_group_1"]: "synthetic-value"},
            "PROVIDER_MODELS": {**server.PROVIDER_MODELS, "route_a_group_1": "fixture-model"},
            "SERVER_STOPPING": threading.Event(),
        }
        for name, value in values.items():
            self.stack.enter_context(patch.object(server, name, value))
        self.stack.enter_context(patch.object(server, "_log_dispatch_event"))

    def add_result(self, text, aid="a" * 32):
        server._add_pending_results([{
            "activityId": aid, "role": "reviewer", "status": "completed", "task": "fixture",
            "group": "auto", "model": "fixture-model", "startedAt": server._activity_time(),
            "updatedAt": server._activity_time(), "result": "",
        }])
        server._record_pending_result(aid, "completed", server._tool_text_result(text))
        return aid

    def test_release_example_explicitly_disables_all_native_fallbacks(self):
        example = json.loads((ROOT / "tools/laowu_mcp/user_config.example.json").read_text(encoding="utf-8"))
        self.assertTrue(all(route.get("native_codex_fallback") is False for route in example["routes"]))

    def test_invalid_config_does_not_become_empty_defaults(self):
        server.USER_CONFIG_PATH.write_text('{"routes": [', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "config"):
            server._read_user_config()

    def test_non_object_config_is_rejected(self):
        server.USER_CONFIG_PATH.write_text('[]', encoding="utf-8")
        with self.assertRaises(ValueError):
            server._read_user_config()

    def test_full_result_survives_reload_and_requires_all_pages_before_ack(self):
        text = "报告" * 7000 + "TAIL-MARKER"
        aid = self.add_result(text)
        server.PERSISTED_STATE = server.load_state()
        first = server._pending_result_action({"action": "get", "activity_id": aid})["structuredContent"]
        self.assertFalse(first.get("complete", True))
        self.assertEqual(first["total_chars"], len(text))
        self.assertTrue(server._pending_result_action({"action": "acknowledge", "activity_id": aid}).get("isError", False))
        second = server._pending_result_action({"action": "get", "activity_id": aid, "offset": first["next_offset"]})["structuredContent"]
        self.assertTrue(second["complete"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["result"] + second["result"], text)
        self.assertTrue(server._pending_result_action({"action": "acknowledge", "activity_id": aid})["structuredContent"]["acknowledged"])

    def test_skipping_to_last_page_does_not_authorize_ack(self):
        aid = self.add_result("x" * 24001)
        server._pending_result_action({"action": "get", "activity_id": aid, "offset": 24000})
        self.assertTrue(server._pending_result_action({"action": "acknowledge", "activity_id": aid}).get("isError", False))

    def test_negative_page_offset_is_rejected(self):
        aid = self.add_result("fixture")
        self.assertTrue(server._pending_result_action({"action": "get", "activity_id": aid, "offset": -1}).get("isError", False))

    def test_replacing_result_resets_read_cursor_in_shared_update(self):
        aid = self.add_result("old result")
        server._pending_result_action({"action": "get", "activity_id": aid})
        server._update_pending_result(aid, status="running", result="", readAt="")
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["readOffset"], 0)

    def test_retained_full_result_can_be_read_after_pending_ack_and_restart(self):
        text = "x" * 12001 + "TAIL"
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT), "model": "fixture-model"})
        self.add_result(text, aid)
        server._set_activity_state(aid, status="completed")
        retained = server._activity_action({"action": "retain", "activity_id": aid})
        self.assertFalse(retained.get("isError", False))
        server._pending_result_action({"action": "get", "activity_id": aid})
        server._pending_result_action({"action": "get", "activity_id": aid, "offset": 12000})
        server._pending_result_action({"action": "acknowledge", "activity_id": aid})
        server.PERSISTED_STATE = server.load_state()
        server.ACTIVITIES.clear()
        server.ACTIVITY_ORDER.clear()
        server._restore_retained_activities()
        result = server._activity_action({"action": "result", "activity_id": aid, "offset": 12000})
        self.assertEqual(result.get("structuredContent", {}).get("result"), text[12000:])
        snapshot = server._activity_snapshot()["activities"][0]
        self.assertTrue(snapshot["hasFullResult"])
        self.assertNotIn("pendingResultText", snapshot)

    def test_queued_activity_cannot_be_deleted_and_can_be_cancelled(self):
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT)}, status="queued")
        event = threading.Event()
        server.CANCEL_EVENTS[aid] = event
        self.assertTrue(server._activity_action({"action": "delete", "activity_id": aid}).get("isError"))
        self.assertIn(aid, server.ACTIVITIES)
        self.assertFalse(server._cancel_subagent_activity(aid).get("isError", False))
        self.assertTrue(event.is_set())

    def test_dispatcher_alias_is_disabled_in_worker(self):
        home = self.folder / "codex"
        home.mkdir()
        (home / "config.toml").write_text('[mcp_servers.local_alias]\ncommand="python"\nargs=[' + json.dumps(server.__file__) + ']\n', encoding="utf-8")
        with patch.object(server, "_provider_base_url", return_value=""):
            command = server.build_codex_command("route_a_group_1", "reviewer", str(ROOT), model="fixture-model", codex_home=home)
        self.assertTrue(any("local_alias" in part and "enabled=false" in part for part in command))

    def test_child_environment_blocks_nested_dispatcher_startup_before_key_loading(self):
        self.assertEqual(server._codex_process_environment("route_a_group_1").get("LAOWU_DISPATCH_CHILD"), "1")

    def test_single_role_returns_activity_id_without_waiting_for_provider(self):
        with patch.object(server, "_submit_bounded", return_value=True) as submit, patch.object(server, "_write_response") as write, patch.object(server, "run_subagent_task", return_value=server._tool_text_result("fixture")):
            server._handle_tool_call("test", {"name": "run_subagent_reviewer", "arguments": {
                "task": "fixture", "cwd": str(ROOT), "group": "auto", "route_choice": "route_a",
            }})
        response = write.call_args.kwargs["result"]
        self.assertIn("activity_id", response.get("structuredContent", {}))
        self.assertTrue(server.ACTIVITIES[response["structuredContent"]["activity_id"]]["masterRecallEnabled"])
        self.assertTrue(submit.called)

    def test_global_revocation_blocks_existing_session_continuation(self):
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT), "master_recall": True,
            "allowMcpTools": False, "allowSkills": False, "model": "fixture-model"})
        server._set_activity_state(aid, status="completed", sessionId="fixture-session", currentProviderId="route_a_group_1")
        server.PERSISTED_STATE["allowCodexLaunch"] = False
        with patch.object(server, "_run_provider") as provider:
            result = server._continue_activity({"activity_id": aid, "message": "followup"})
        self.assertTrue(result.get("isError"))
        provider.assert_not_called()

    def test_worker_records_actual_capabilities_for_later_continuation(self):
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT), "model": "fixture-model"}, status="queued")
        server.PERSISTED_STATE["builtinCapabilities"]["reviewer"] = {"allowMcpTools": False, "allowSkills": False}
        with patch.object(server, "run_subagent_task", return_value=server._tool_text_result("done")):
            server._execute_subagent_call("reviewer", {"task": "fixture", "cwd": str(ROOT), "group": "auto", "route_choice": "route_a"}, "test", activity_id=aid)
        self.assertFalse(server.ACTIVITIES[aid]["allowMcpTools"])
        self.assertFalse(server.ACTIVITIES[aid]["allowSkills"])

    def test_resume_pins_directory_and_sandbox_before_subcommand(self):
        with patch.object(server, "_provider_base_url", return_value=""):
            command = server.build_codex_command("route_a_group_1", "reviewer", str(ROOT), model="fixture-model", session_id="fixture-session")
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertLess(command.index("--sandbox"), command.index("resume"))
        self.assertEqual(command[command.index("--cd") + 1], str(ROOT.resolve()))

    def test_continuation_refuses_changed_provider_endpoint(self):
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT), "master_recall": True,
            "allowMcpTools": False, "allowSkills": False, "model": "fixture-model"})
        server._set_activity_state(aid, status="completed", sessionId="fixture-session", currentProviderId="route_a_group_1",
            providerSpec={"model_provider": server.PROVIDER_IDS["route_a_group_1"], "key_name": server.KEY_NAMES["route_a_group_1"], "base_url": "https://original.example/v1"})
        with patch.object(server, "_provider_base_url", return_value="https://changed.example/v1"), patch.object(server, "_run_provider") as provider:
            result = server._continue_activity({"activity_id": aid, "message": "followup"})
        self.assertTrue(result.get("isError"))
        provider.assert_not_called()

    def test_public_continuation_returns_before_provider_runs_and_preserves_session(self):
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT), "master_recall": True,
            "allowMcpTools": False, "allowSkills": False, "model": "fixture-model"})
        server._set_activity_state(aid, status="completed", sessionId="fixture-session", currentProviderId="route_a_group_1")
        output = json.dumps({"item": {"type": "agent_message", "text": "continued result"}})
        with patch.object(server, "_submit_bounded", return_value=True) as submit, patch.object(server, "_write_response") as write, patch.object(server, "_run_provider", return_value=(0, output, "")) as provider:
            server._handle_tool_call("test", {"name": "laowu_continue_task", "arguments": {"activity_id": aid, "message": "followup"}})
            response = write.call_args.kwargs["result"]
            self.assertEqual(response.get("structuredContent", {}).get("activity_id"), aid)
            provider.assert_not_called()
            function, *args = submit.call_args.args[2:]
            function(*args, **submit.call_args.kwargs)
        self.assertEqual(provider.call_args.kwargs["session_id"], "fixture-session")
        self.assertFalse(provider.call_args.kwargs["allow_mcp_tools"])
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["result"], "continued result")

    def test_child_dispatcher_refuses_startup_before_loading_credentials(self):
        with patch.dict(os.environ, {"LAOWU_DISPATCH_CHILD": "1"}), patch.object(server, "_load_api_keys") as load:
            with self.assertRaisesRegex(RuntimeError, "Nested"):
                server.serve()
        load.assert_not_called()

    def test_stdio_disconnect_sets_cancellation_for_running_tasks(self):
        class Stream(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        event = threading.Event()
        with patch.dict(server.CANCEL_EVENTS, {"f" * 32: event}), patch.object(server.sys, "stdin", Stream("")), patch.object(server, "_load_api_keys", return_value={}), patch.object(server, "load_state", return_value=server._default_state()), patch.object(server, "_restore_retained_activities"), patch.object(server, "WORKERS"), patch.object(server, "PARALLEL_WORKERS"), patch.object(server, "MODEL_QUERY_WORKERS"):
            server.serve()
        self.assertTrue(event.is_set())

    def test_shutdown_rejects_a_late_task_reservation(self):
        stopping = threading.Event()
        stopping.set()
        with patch.object(server, "SERVER_STOPPING", stopping, create=True):
            with self.assertRaisesRegex(ValueError, "shutting"):
                self.add_result("must not be reserved")

    def test_ephemeral_native_fallback_does_not_resume_a_failed_provider_session(self):
        aid = server._new_activity("reviewer", {"task": "fixture", "cwd": str(ROOT), "master_recall": True, "model": "fixture-model"})
        server._set_activity_state(aid, sessionId="old-provider-session", currentProviderId="route_a_group_1")
        route = {"id": "route_a", "name": "A", "enabled": True, "native_codex_fallback": True, "auto_mode": "sequential", "groups": []}
        output = json.dumps({"item": {"type": "agent_message", "text": "native result"}})
        with patch.object(server, "ROUTE_SETTINGS", [route]), patch.object(server, "AUTO_ROUTES", {"route_a": []}), patch.object(server, "AUTO_MODES", {"route_a": "sequential"}), patch.object(server, "_run_native_codex_fallback", return_value=(0, output, "")):
            result = server.run_subagent_task({"role": "reviewer", "task": "fixture", "cwd": str(ROOT), "group": "auto", "route_choice": "route_a"}, activity_id=aid)
        self.assertFalse(result.get("isError", False))
        self.assertFalse(server.ACTIVITIES[aid]["masterRecallEnabled"])
        self.assertIsNone(server.ACTIVITIES[aid].get("sessionId"))

    def test_native_only_route_can_be_reserved_by_async_tool(self):
        route = {"id": "route_a", "name": "A", "enabled": True, "native_codex_fallback": True, "auto_mode": "sequential", "groups": []}
        with patch.object(server, "ROUTE_SETTINGS", [route]), patch.object(server, "AUTO_ROUTES", {"route_a": []}), patch.object(server, "AUTO_MODES", {"route_a": "sequential"}), patch.object(server, "_submit_bounded", return_value=True), patch.object(server, "_write_response") as write:
            server._handle_tool_call("test", {"name": "run_subagent_reviewer", "arguments": {"task": "fixture", "cwd": str(ROOT), "group": "auto"}})
        result = write.call_args.kwargs["result"]
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(server.PERSISTED_STATE["pendingResults"][0]["model"], "native-codex")


if __name__ == "__main__":
    unittest.main()
