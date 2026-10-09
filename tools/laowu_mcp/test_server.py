import io
import threading
import unittest
import json
import subprocess
import shutil
import csv
import sys
import time
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, patch

import server
import process_runner
from server import build_codex_command, build_prompt, classify_failure, missing_credentials, model_for_group, parse_last_assistant_message, route_groups, _is_git_repo


class TaskOneRegressionTests(unittest.TestCase):
    def test_atomic_write_retries_transient_replace_permission_error(self):
        with TemporaryDirectory(prefix=".test-write-retry-", dir=Path(__file__).resolve().parents[2]) as temp:
            target = Path(temp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            original_replace = server.os.replace
            attempts = []

            def transient_replace(source, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise PermissionError("synthetic transient Windows file lock")
                return original_replace(source, destination)

            with patch("server.os.replace", side_effect=transient_replace):
                server._atomic_write_text(target, "new")
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_public_server_name_and_open_tool_title(self):
        self.assertEqual(server.SERVER_ID, "wuhezhizhong")
        command = build_codex_command(
            "route_a_group_1", "free", str(Path(__file__).parent), codex_path="codex", model="model",
        )
        self.assertIn("workspace-write", command)
        self.assertIn("mcp_servers.wuhezhizhong.enabled=false", command)
        self.assertNotIn("mcp_servers.wuhesuzhong.enabled=false", command)
        tool = next(item for item in server._activity_tool_schemas() if item["name"] == "open_laowu_activity")
        self.assertEqual(tool["title"], "乌合之众")

    def test_legacy_server_override_is_only_added_when_legacy_server_is_configured(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temp_dir:
            codex_home = Path(temp_dir)
            config = codex_home / "config.toml"
            config.write_text('[mcp_servers.wuhezhizhong]\ncommand="python"\nargs=[]\n', encoding="utf-8")
            command = build_codex_command(
                "route_a_group_1", "free", str(Path(__file__).parent), codex_path="codex", model="model",
                codex_home=codex_home,
            )
            self.assertNotIn("mcp_servers.wuhesuzhong.enabled=false", command)

            config.write_text(
                '[mcp_servers.wuhezhizhong]\ncommand="python"\nargs=[]\n\n'
                '[mcp_servers.wuhesuzhong]\ncommand="python"\nargs=[]\n',
                encoding="utf-8",
            )
            command = build_codex_command(
                "route_a_group_1", "free", str(Path(__file__).parent), codex_path="codex", model="model",
                codex_home=codex_home,
            )
            self.assertIn("mcp_servers.wuhesuzhong.enabled=false", command)

    def test_workspace_cwd_must_resolve_inside_workspace(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as root:
            workspace = Path(root) / "workspace"
            inside = workspace / "child"
            outside = Path(root) / "outside"
            inside.mkdir(parents=True)
            outside.mkdir()
            with patch.object(server, "WORKSPACE_PATH", workspace):
                self.assertEqual(server._validated_workspace_cwd(str(inside)), inside.resolve())
                with self.assertRaisesRegex(ValueError, "workspace"):
                    server._validated_workspace_cwd(str(outside))

    def test_free_role_is_exposed_through_builtin_role_surfaces(self):
        self.assertIn("free", server.ROLE_GUIDANCE)
        self.assertEqual(server.ROLE_LABELS["free"], "自由者")
        self.assertEqual(server.ROLE_TOOL_NAMES["free"], "run_subagent_free")
        self.assertIn("non-code", server.ROLE_GUIDANCE["free"])
        self.assertEqual(server._builtin_profile("free")["role"], "free")
        self.assertIn("free", [profile["role"] for profile in server._profile_snapshot()["builtinProfiles"]])
        schemas = server._role_tool_schemas()
        self.assertIn("run_subagent_free", {item["name"] for item in schemas})
        state = server._default_state()
        state["builtinPermissions"]["free"] = True
        with patch.object(server, "PERSISTED_STATE", state):
            self.assertIn("free", [item["role"] for item in server._codex_callable_profiles()["structuredContent"]["profiles"]])

    def test_non_object_params_error_does_not_prevent_following_ping(self):
        class Input(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        class Output(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        stdin = Input('{"jsonrpc":"2.0","id":1,"method":"ping","params":[]}\n'
                      '{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
        stdout = Output()
        with (
            patch.object(server, "SERVER_STOPPING", threading.Event()), patch.object(server.sys, "stdin", stdin), patch.object(server.sys, "stdout", stdout),
            patch.object(server.sys, "stderr", Output()), patch.object(server, "_load_api_keys", return_value={}),
            patch.object(server, "load_state", return_value=server._default_state()),
            patch.object(server, "_restore_retained_activities"), patch.object(server, "_log_dispatch_event"),
        ):
            server.serve()
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32602)
        self.assertEqual(responses[1]["id"], 2)

    def test_preset_cancel_prevents_provider_process_creation(self):
        cancel = threading.Event()
        cancel.set()
        with (
            patch.dict(server.API_KEYS, {server.KEY_NAMES["route_a_group_1"]: "fixture-key"}, clear=True),
            patch("server.subprocess.Popen") as popen,
        ):
            result = server._run_codex_process(
                "route_a_group_1", "free", "prompt", str(Path(__file__).parent), "model",
                "a" * 32, cancel,
            )
        self.assertEqual(result[0], 130)
        popen.assert_not_called()

    def test_native_fallback_uses_runner_self_disables_servers_and_applies_capabilities(self):
        class FakeProcess:
            pid = 123
            returncode = 0
            def communicate(self, **kwargs):
                return "native output", ""

        process = FakeProcess()
        with (
            patch("server.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")),
            patch("server.subprocess.Popen", return_value=process) as popen,
            patch("server._capability_config_overrides", return_value=["mcp_servers.remote_docs.enabled=false", "skills.config=[]"]) as capabilities,
        ):
            result = server._run_native_codex_fallback(
                "coder", "prompt", str(Path(__file__).parent), ["provider failed"], None,
                allow_mcp_tools=False, allow_skills=False,
            )
        self.assertEqual(result[:2], (0, "native output"))
        capabilities.assert_called_once_with(
            str(Path(__file__).parent), allow_mcp_tools=False, allow_skills=False,
        )
        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], str(Path(server.__file__).with_name("process_runner.py")))
        self.assertEqual(command[2], "--")
        self.assertIn("mcp_servers.wuhezhizhong.enabled=false", command)
        self.assertNotIn("mcp_servers.wuhesuzhong.enabled=false", command)
        self.assertIn("mcp_servers.remote_docs.enabled=false", command)
        self.assertIn("skills.config=[]", command)

    def test_provider_thread_start_failure_cleans_up_runner(self):
        class Stream:
            closed = False
            def __iter__(self):
                return iter(())
            def close(self):
                self.closed = True
        class FakeProcess:
            pid = 456
            def __init__(self):
                self.stdin, self.stdout, self.stderr = Stream(), Stream(), Stream()

        process = FakeProcess()
        with (
            patch.dict(server.API_KEYS, {server.KEY_NAMES["route_a_group_1"]: "fixture-key"}, clear=True),
            patch("server.subprocess.Popen", return_value=process),
            patch("server.threading.Thread.start", side_effect=RuntimeError("injected thread-start failure")),
            patch("server._terminate_process_tree", return_value="") as terminate,
            patch("server._log_dispatch_event"),
        ):
            result = server._run_codex_process(
                "route_a_group_1", "coder", "prompt", str(Path(__file__).parent), "model",
                "a" * 32, None,
            )
        self.assertEqual(result[0], 70)
        self.assertIn("thread-start failure", result[2])
        terminate.assert_called_once_with(process)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_tools_call_rejects_unstring_names_and_continues_to_ping(self):
        class Input(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        class Output(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": []}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        stdin, stdout = Input("\n".join(json.dumps(item) for item in requests) + "\n"), Output()
        with (
            patch.object(server, "SERVER_STOPPING", threading.Event()), patch.object(server.sys, "stdin", stdin), patch.object(server.sys, "stdout", stdout),
            patch.object(server.sys, "stderr", Output()), patch.object(server, "_load_api_keys", return_value={}),
            patch.object(server, "load_state", return_value=server._default_state()),
            patch.object(server, "_restore_retained_activities"), patch.object(server, "_log_dispatch_event"),
            patch.object(server, "WORKERS", server.concurrent.futures.ThreadPoolExecutor(max_workers=1)),
            patch.object(server, "MODEL_QUERY_WORKERS", server.concurrent.futures.ThreadPoolExecutor(max_workers=1)),
            patch.object(server, "PARALLEL_WORKERS", server.concurrent.futures.ThreadPoolExecutor(max_workers=1)),
        ):
            server.serve()
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([item["error"]["code"] for item in responses[:2]], [-32602, -32602])
        self.assertEqual(responses[2]["id"], 3)

    def test_route_guidance_matches_explicit_multi_route_choice(self):
        routes, _, _ = auto_route_fixture()
        with patch.object(server, "ROUTE_SETTINGS", routes):
            self.assertIn("multiple routes are enabled", server.PROFILE_ROUTE_SELECTION_GUIDANCE.lower())
            self.assertIn("route_choice", server.PROFILE_ROUTE_SELECTION_GUIDANCE)
            self.assertIn("multiple enabled routes require an explicit choice", server._route_choice_schema()["description"].lower())
        with patch.object(server, "ROUTE_SETTINGS", routes[:1]):
            self.assertIsNone(server._route_choice_schema())

    def test_task_profile_capabilities_reach_native_fallback(self):
        with (
            patch("server.ROUTE_SETTINGS", [{
                "id": "route_a", "name": "A", "enabled": True,
                "native_codex_fallback": True,
                "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}],
            }]),
            patch("server.route_groups", return_value=["route_a_group_1"]),
            patch("server.model_for_group", return_value="fixture-model"),
            patch("server.missing_credentials", return_value=[]),
            patch("server._run_provider", return_value=(1, "", "HTTP 503 upstream error")),
            patch("server._run_native_codex_fallback", return_value=(70, "", "failed")) as fallback,
            patch("server._log_dispatch_event"),
        ):
            server.run_subagent_task({
                "role": "coder", "task": "inspect", "cwd": str(Path(__file__).parent),
                "group": "auto", "route_choice": "route_a",
                "allowMcpTools": False, "allowSkills": False,
            })
        self.assertEqual(fallback.call_args.kwargs["allow_mcp_tools"], False)
        self.assertEqual(fallback.call_args.kwargs["allow_skills"], False)

    def test_preset_cancel_prevents_native_fallback_process_creation(self):
        cancel = threading.Event()
        cancel.set()
        with (
            patch("server.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")),
            patch("server.subprocess.Popen") as popen,
            patch("server._terminate_process_tree", return_value=""),
        ):
            result = server._run_native_codex_fallback(
                "coder", "prompt", str(Path(__file__).parent), ["provider failed"], cancel,
            )
        self.assertEqual(result[0], 130)
        popen.assert_not_called()


def auto_route_fixture():
    routes = [
        {"id": "route_a", "name": "A", "enabled": True, "groups": [
            {"provider_id": "route_a_group_1", "auto": True, "enabled": True},
        ]},
        {"id": "route_b", "name": "B", "enabled": True, "groups": [
            {"provider_id": "route_b_group_1", "auto": True, "enabled": True},
            {"provider_id": "route_b_group_2", "auto": True, "enabled": True},
        ]},
    ]
    providers = {
        "route_a": ["route_a_group_1"],
        "route_b": ["route_b_group_1", "route_b_group_2"],
    }
    modes = {"route_a": "sequential", "route_b": "sequential"}
    return routes, providers, modes


class RouteResolutionTests(unittest.TestCase):
    def test_route_a_default_group_cannot_be_configured_on_route_b(self):
        route = {
            "id": "route_b", "name": "B", "enabled": True, "auto_mode": "sequential",
            "native_codex_fallback": False,
            "groups": [
                {"provider_id": "route_a_group_4", "auto": False, "enabled": True},
                {"provider_id": "route_b_group_1", "auto": True, "enabled": True},
            ],
        }
        self.assertIsNone(server._valid_route_settings([route]))

    def test_auto_route_requires_choice_when_multiple_routes_are_enabled(self):
        routes, providers, modes = auto_route_fixture()
        with (
            patch.object(server, "ROUTE_SETTINGS", routes), patch.object(server, "AUTO_ROUTES", providers),
            patch.object(server, "AUTO_MODES", modes),
        ):
            with self.assertRaisesRegex(ValueError, "route"):
                server._resolve_route_choice("auto")
            self.assertEqual(server._resolve_route_choice("auto", "route_b"), "route_b")

    def test_provider_urls_allow_http_only_for_loopback(self):
        def details(base_url):
            return server._validated_provider_details({
                "route_a_group_1": {"model": "", "base_url": base_url},
            })

        self.assertIsNotNone(details("https://api.example.com/v1"))
        self.assertIsNotNone(details("http://localhost:8080/v1"))
        self.assertIsNotNone(details("http://127.0.0.1:8080/v1"))
        self.assertIsNotNone(details("http://[::1]:8080/v1"))
        self.assertIsNone(details("http://api.example.com/v1"))

    def test_provider_base_url_rejects_external_http_from_saved_and_codex_config(self):
        provider = dict(server.PROVIDERS["route_a_group_1"], base_url="http://api.example.com/v1")
        with patch.dict(server.PROVIDERS, {"route_a_group_1": provider}):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                server._provider_base_url("route_a_group_1")

        provider["base_url"] = ""
        with (
            patch.dict(server.PROVIDERS, {"route_a_group_1": provider}),
            patch("server._load_codex_config", return_value={
                "model_providers": {"provider_a_group_1": {"base_url": "http://api.example.com/v1"}},
            }),
        ):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                server._provider_base_url("route_a_group_1")

    def test_auto_route_excludes_the_explicit_default_provider(self):
        route = {
            "id": "route_a", "name": "A", "enabled": True,
            "auto_mode": "sequential", "native_codex_fallback": False,
            "groups": [
                {"provider_id": "route_a_group_1", "auto": True, "enabled": True},
                {"provider_id": "route_a_group_4", "auto": True, "enabled": True},
            ],
        }
        normalized = server._valid_route_settings([route])

        self.assertFalse(normalized[0]["groups"][1]["auto"])
        with (
            patch.object(server, "ROUTE_SETTINGS", normalized),
            patch.object(server, "AUTO_ROUTES", {}),
            patch.object(server, "AUTO_MODES", {}),
            patch.object(server, "GROUPS", {}),
            patch.object(server, "GROUP_ROUTES", {}),
        ):
            server._sync_route_maps()
            self.assertEqual(server.route_groups("auto", "route_a"), ["route_a_group_1"])
            self.assertEqual(server.route_groups("default"), ["route_a_group_4"])

    def test_saved_profile_route_group_overrides_call_route_per_profile(self):
        profile = {"routeGroup": "route_a:route_a_group_2"}

        with patch("server._route_for_profile_group", return_value="route_a"):
            self.assertEqual(
                server._resolve_profile_routing(profile, "auto", "route_b"),
                ("route_a:route_a_group_2", "route_a"),
            )
        self.assertEqual(
            server._resolve_profile_routing({"routeGroup": ""}, "auto", "route_b"),
            ("auto", "route_b"),
        )

    def test_legacy_provider_ids_migrate_by_credential_identity(self):
        legacy_id = "route_a_custom_old"
        config = {
            "providers": {legacy_id: dict(server.DEFAULT_PROVIDERS["route_a_group_2"], label="Keep this label")},
            "routes": [{"id": "route_a", "name": "A", "groups": [{"provider_id": legacy_id, "auto": True}]}],
            "deleted_providers": [],
        }
        providers, aliases, deleted = server._migrate_provider_config(config)

        self.assertEqual(aliases, {legacy_id: "route_a_group_2"})
        self.assertEqual(providers["route_a_group_2"]["label"], "Keep this label")
        self.assertEqual(config["routes"][0]["groups"][0]["provider_id"], "route_a_group_2")
        self.assertEqual(deleted, set())

    def test_canonical_provider_entry_wins_legacy_collision_regardless_of_order(self):
        legacy_id = "route_a_custom_old"
        legacy = dict(server.DEFAULT_PROVIDERS["route_a_group_2"], label="Legacy label", model="legacy-model")
        canonical = dict(server.DEFAULT_PROVIDERS["route_a_group_2"], label="", model="")
        for providers in (
            {legacy_id: legacy, "route_a_group_2": canonical},
            {"route_a_group_2": canonical, legacy_id: legacy},
        ):
            migrated, aliases, _ = server._migrate_provider_config({"providers": providers})
            self.assertEqual(aliases, {legacy_id: "route_a_group_2"})
            self.assertEqual(migrated["route_a_group_2"]["label"], "Legacy label")
            self.assertEqual(migrated["route_a_group_2"]["model"], "legacy-model")

    def test_legacy_activity_and_profile_group_ids_are_normalized(self):
        state = server._default_state()
        state["builtinRouteGroups"]["scout"] = "route-a-legacy"
        state["profiles"] = [{
            "id": "saved-profile", "name": "Saved", "role": "scout", "instructions": "Inspect",
            "codexCallable": True, "routeGroup": "route-a-legacy",
            "createdAt": "2026-10-06T00:00:00Z", "updatedAt": "2026-10-06T00:00:00Z",
        }]
        state["retainedActivities"] = [{
            "id": "activity-old", "role": "scout", "status": "failed", "requestedGroup": "route-a-legacy",
            "task": "Inspect legacy activity", "model": "legacy-model",
            "currentProviderId": "route_a_legacy", "startedAt": "2026-10-06T00:00:00Z",
            "updatedAt": "2026-10-06T00:00:00Z", "events": [],
        }]
        state["pendingResults"] = [{
            "activityId": "pending-old", "role": "scout", "status": "failed", "group": "route-a-legacy",
            "model": "legacy-model",
            "startedAt": "2026-10-06T00:00:00Z", "updatedAt": "2026-10-06T00:00:00Z",
        }]
        route = {"id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential",
                 "native_codex_fallback": False,
                 "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}]}
        with patch.object(server, "_legacy_group_aliases", {"route-a-legacy": "route_a_group_1"}), \
                patch.object(server, "_provider_aliases", {"route_a_legacy": "route_a_group_1"}), \
                patch.object(server, "ROUTE_SETTINGS", [route]), \
                patch.object(server, "GROUPS", {"auto": []}):
            normalized = server._validate_state(state)
        self.assertEqual(normalized["builtinRouteGroups"]["scout"], "route_a:route_a_group_1")
        self.assertEqual(normalized["profiles"][0]["routeGroup"], "route_a:route_a_group_1")
        self.assertEqual(normalized["retainedActivities"][0]["requestedGroup"], "route_a:route_a_group_1")
        self.assertEqual(normalized["retainedActivities"][0]["currentProviderId"], "route_a_group_1")
        self.assertEqual(normalized["pendingResults"][0]["group"], "route_a:route_a_group_1")

    def test_provider_defaults_and_tool_enums_use_neutral_ids(self):
        self.assertEqual(list(server.DEFAULT_PROVIDERS), [
            "route_a_group_1", "route_a_group_2", "route_a_group_3", "route_a_group_4",
            "route_b_group_1", "route_b_group_2", "route_b_group_3",
        ])
        schema = next(item for item in server._activity_tool_schemas() if item["name"] == "laowu_save_routes")
        ids = schema["inputSchema"]["properties"]["routes"]["items"]["properties"]["groups"]["items"]["properties"]["provider_id"]["enum"]
        self.assertEqual(ids, list(server.PROVIDERS))

    def test_auto_uses_only_enabled_route_when_choice_is_omitted(self):
        with patch("server.ROUTE_SETTINGS", [
            {"id": "route_a", "enabled": True, "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}]},
            {"id": "route_b", "enabled": False, "groups": []},
        ]):
            self.assertEqual(server._resolve_route_choice("auto"), "route_a")

    def test_auto_requires_choice_when_multiple_routes_are_enabled(self):
        with patch("server.ROUTE_SETTINGS", [
            {"id": "route_a", "name": "主线路", "enabled": True, "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}]},
            {"id": "route_b", "name": "备用线路", "enabled": True, "groups": [{"provider_id": "route_b_group_1", "auto": True, "enabled": True}]},
            {"id": "route_c", "name": "停用线路", "enabled": False, "groups": []},
        ]):
            with self.assertRaisesRegex(ValueError, "Choose a route"):
                server._resolve_route_choice("auto")
            self.assertEqual(server._resolve_route_choice("auto", "route_b"), "route_b")

    def test_disabled_route_is_rejected(self):
        with patch("server.ROUTE_SETTINGS", [
            {"id": "route_a", "enabled": True, "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}]},
            {"id": "route_b", "enabled": False, "groups": []},
        ]):
            with self.assertRaisesRegex(ValueError, "enabled|启用"):
                server._resolve_route_choice("auto", "route_b")

    def test_no_enabled_route_is_actionable(self):
        with patch("server.ROUTE_SETTINGS", [
            {"id": "route_a", "enabled": False, "groups": []},
        ]):
            with self.assertRaisesRegex(ValueError, "启用路线|Enable"):
                server._resolve_route_choice("auto")

    def test_enabled_routes_without_auto_groups_are_not_auto_choices(self):
        with patch("server.ROUTE_SETTINGS", [
            {"id": "manual", "enabled": True, "groups": [{"provider_id": "route_a_group_1", "auto": False, "enabled": True}]},
            {"id": "empty", "enabled": True, "groups": []},
            {"id": "auto", "enabled": True, "groups": [{"provider_id": "route_b_group_1", "auto": True, "enabled": True}]},
        ]), patch.dict(server.GROUP_ROUTES, {"route_a:route_a_group_1": "manual"}):
            self.assertEqual(server._enabled_route_ids(), ["auto"])
            self.assertEqual(server._resolve_route_choice("auto"), "auto")
            self.assertEqual(server._resolve_route_choice("route_a:route_a_group_1"), "manual")


class CodexFallbackPolicyTests(unittest.TestCase):
    def test_missing_legacy_setting_defaults_only_route_a_to_enabled(self):
        routes = server._valid_route_settings([
            {"id": "route_a", "name": "A", "groups": []},
            {"id": "route_b", "name": "B", "groups": []},
        ])

        self.assertEqual([route["native_codex_fallback"] for route in routes], [True, False])
        explicit = server._valid_route_settings([
            {"id": "route_a", "name": "A", "native_codex_fallback": False, "groups": []},
            {"id": "route_b", "name": "B", "native_codex_fallback": True, "groups": []},
        ])
        self.assertEqual([route["native_codex_fallback"] for route in explicit], [False, True])

    def test_route_settings_schema_supports_native_codex_fallback_policy(self):
        save_schema = next(
            item for item in server._activity_tool_schemas()
            if item["name"] == "laowu_save_routes"
        )
        route_schema = save_schema["inputSchema"]["properties"]["routes"]["items"]

        self.assertEqual(route_schema["properties"]["native_codex_fallback"], {"type": "boolean"})
        self.assertIn("native_codex_fallback", route_schema["required"])
        delete_schema = save_schema["inputSchema"]["properties"]["delete_provider_ids"]
        self.assertEqual(delete_schema["type"], "array")
        self.assertEqual(delete_schema["items"]["enum"], list(server.PROVIDERS))
        self.assertEqual(delete_schema["maxItems"], len(server.PROVIDERS))
        self.assertTrue(delete_schema["uniqueItems"])
        self.assertNotIn("delete_provider_ids", save_schema["inputSchema"]["required"])

    def test_route_failure_falls_back_only_when_route_allows_it(self):
        def fail(route_fallback):
            with (
                patch("server.ROUTE_SETTINGS", [{
                    "id": "route_a", "name": "A", "enabled": True,
                    "native_codex_fallback": route_fallback,
                    "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}],
                }]),
                patch("server.route_groups", return_value=["route_a_group_1"]),
                patch("server.model_for_group", return_value="fixture-model"),
                patch("server.missing_credentials", return_value=[]),
                patch("server._run_provider", return_value=(1, "", "HTTP 503 upstream error")),
                patch("server._run_native_codex_fallback", return_value=(0, '{"type":"item.completed","item":{"type":"agent_message","text":"native result"}}', "")) as native_fallback,
                patch("server._log_dispatch_event"),
            ):
                result = server.run_subagent_task({
                    "role": "coder", "task": "make a small patch", "cwd": str(Path(__file__).parent),
                    "group": "auto", "route_choice": "route_a",
                })
                return result, native_fallback.call_count

        disabled, disabled_calls = fail(False)
        enabled, enabled_calls = fail(True)
        self.assertEqual(disabled_calls, 0)
        self.assertEqual(enabled_calls, 1)
        self.assertIn("native result", enabled["content"][0]["text"])
        self.assertTrue(disabled["isError"])


class ActivityStreamingTests(unittest.TestCase):
    def setUp(self):
        self.activities = server.ACTIVITIES
        self.order = server.ACTIVITY_ORDER
        self.activities.clear()
        self.order.clear()

    def tearDown(self):
        self.activities.clear()
        self.order.clear()

    def test_ndjson_events_are_appended_once_with_stable_ids(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        seen = set()
        line = '{"type":"item.completed","item":{"id":"evt-1","type":"agent_message","text":"done"}}'
        server._append_codex_output_line(activity_id, line, seen)
        server._append_codex_output_line(activity_id, line, seen)
        events = server.ACTIVITIES[activity_id]["events"]
        self.assertEqual(sum(event.get("eventId") == "evt-1" for event in events), 1)

    def test_invalid_ndjson_has_no_effect(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        seen = set()
        before = len(server.ACTIVITIES[activity_id]["events"])
        server._append_codex_output_line(activity_id, "not-json", seen)
        self.assertEqual(len(server.ACTIVITIES[activity_id]["events"]), before)

    def test_token_count_event_updates_activity_usage(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        event = {
            "type": "token_count",
            "info": {
                "total_token_usage": {"input_tokens": 120, "cached_input_tokens": 45, "output_tokens": 18},
                "last_token_usage": {"input_tokens": 30},
                "model_context_window": 200000,
            },
        }
        server._append_codex_output_line(activity_id, json.dumps(event), set())

        self.assertEqual(server.ACTIVITIES[activity_id]["contextUsage"], {
            "inputTokens": 120, "cachedInputTokens": 45, "outputTokens": 18,
            "lastInputTokens": 30, "contextWindowTokens": 200000,
        })
        self.assertEqual(server._activity_snapshot()["activities"][0]["contextUsage"], server.ACTIVITIES[activity_id]["contextUsage"])

    def test_token_count_ignores_invalid_values_and_keeps_item_processing(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        event = {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": True, "cached_input_tokens": -1,
                    "output_tokens": 2.5, "unrelated": 5,
                },
                "last_token_usage": {"input_tokens": "12"},
                "model_context_window": 2**53,
            },
            "item": {"id": "evt-usage-with-item", "type": "agent_message", "text": "still handled"},
        }
        server._append_codex_output_line(activity_id, json.dumps(event), set())

        self.assertNotIn("contextUsage", server.ACTIVITIES[activity_id])
        self.assertTrue(any(event.get("eventId") == "evt-usage-with-item" for event in server.ACTIVITIES[activity_id]["events"]))

    def test_invalid_partial_usage_does_not_erase_prior_valid_counts(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        server._append_codex_output_line(activity_id, json.dumps({"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 12, "output_tokens": 4},
        }}), set())
        server._append_codex_output_line(activity_id, json.dumps({"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": -1},
        }}), set())

        self.assertEqual(server.ACTIVITIES[activity_id]["contextUsage"], {
            "inputTokens": 12, "outputTokens": 4,
        })

    def test_token_count_without_window_keeps_available_counts(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        event = {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 7, "output_tokens": 3},
        }}
        server._append_codex_output_line(activity_id, json.dumps(event), set())

        self.assertEqual(server.ACTIVITIES[activity_id]["contextUsage"], {
            "inputTokens": 7, "outputTokens": 3,
        })


class ActivitySnapshotTests(unittest.TestCase):
    def setUp(self):
        server.ACTIVITIES.clear()
        server.ACTIVITY_ORDER.clear()

    def tearDown(self):
        server.ACTIVITIES.clear()
        server.ACTIVITY_ORDER.clear()

    def test_new_activity_has_short_title_and_route_provider_ids(self):
        task = "\n  First sentence is deliberately long enough to be clipped after forty-eight characters.\nsecond line"
        activity_id = server._new_activity("coder", {
            "task": task, "cwd": "C:/work", "route_choice": "route_a",
            "currentProviderId": "route_a_group_1",
        })
        activity = server.ACTIVITIES[activity_id]
        self.assertLessEqual(len(activity["title"]), 48)
        self.assertEqual(activity["routeId"], "route_a")
        self.assertEqual(activity["currentProviderId"], "route_a_group_1")
        self.assertEqual(activity["task"], task)

    def test_snapshot_maps_provider_label_and_preserves_legacy_group(self):
        activity_id = server._new_activity("scout", {
            "task": "inspect", "cwd": "C:/work", "route_choice": "route_a",
            "currentProviderId": "route_a_group_1",
        })
        server.ACTIVITIES[activity_id]["currentGroup"] = "old"
        with patch.dict(server.PROVIDER_LABELS, {"route_a_group_1": "Renamed"}):
            snapshot = server._activity_snapshot()
        self.assertEqual(snapshot["activities"][0]["currentGroup"], "Renamed")
        legacy_id = "b" * 32
        server.ACTIVITIES[legacy_id] = {"id": legacy_id, "task": "old", "currentGroup": "Legacy", "events": []}
        server.ACTIVITY_ORDER.append(legacy_id)
        self.assertEqual(server._activity_snapshot()["activities"][-1]["currentGroup"], "Legacy")

    def test_snapshot_uses_route_default_label_when_provider_label_is_empty(self):
        activity_id = server._new_activity("scout", {
            "task": "inspect", "cwd": "C:/work", "currentProviderId": "route_b_group_1",
        })
        with (
            patch.dict(server.PROVIDER_LABELS, {"route_b_group_1": ""}),
            patch.dict(server.PROVIDER_ORDINALS, {"route_b_group_1": 7}),
        ):
            snapshot = server._activity_snapshot()
        self.assertEqual(snapshot["activities"][0]["currentGroup"], "路线 B 默认分组")

    def test_retained_activity_preserves_valid_context_usage(self):
        activity_id = "d" * 32
        state = server._default_state()
        now = "2026-10-07T00:00:00Z"
        state["retainedActivities"] = [{
            "id": activity_id, "role": "scout", "status": "completed", "requestedGroup": "auto",
            "task": "inspect", "model": "fixture-model", "startedAt": now, "updatedAt": now,
            "events": [], "contextUsage": {"inputTokens": 7, "cachedInputTokens": 2, "outputTokens": 3},
        }]
        path = Path(__file__).resolve().parent / "test-context-usage-state.json"
        try:
            path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(server, "ACTIVITY_STORE_PATH", path), patch.object(
                server, "LEGACY_ACTIVITY_STORE_PATH", path.with_name("missing-context-usage-state.json")
            ):
                loaded = server.load_state()
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(loaded["retainedActivities"][0]["contextUsage"], state["retainedActivities"][0]["contextUsage"])

    def test_retained_context_usage_rejects_invalid_values(self):
        activity_id = "e" * 32
        now = "2026-10-07T00:00:00Z"
        for value in (True, -1, 1.5, "7", 2**53):
            with self.subTest(value=value):
                state = server._default_state()
                state["retainedActivities"] = [{
                    "id": activity_id, "role": "scout", "status": "completed", "requestedGroup": "auto",
                    "task": "inspect", "model": "fixture-model", "startedAt": now, "updatedAt": now,
                    "events": [], "contextUsage": {"inputTokens": value},
                }]
                with self.assertRaises(ValueError):
                    server._validate_state(state)


class OneShotSchemaTests(unittest.TestCase):
    def test_route_prompt_is_scoped_to_each_profiles_default_setting(self):
        descriptions = {schema["name"]: schema["description"] for schema in server._role_tool_schemas()}
        guidance = "Check each task's profile `routeGroup` individually."

        for name in (
            "run_subagent_task",
            "run_subagent_scout",
            "run_subagent_reviewer",
            "run_subagent_tester",
            "run_subagent_coder",
            "run_subagent_profile",
            "run_subagents_parallel",
        ):
            with self.subTest(tool=name):
                self.assertIn(guidance, descriptions[name])
                self.assertIn("Auto selects the only enabled route automatically", descriptions[name])
                self.assertIn("when multiple routes are enabled, provide `route_choice`", descriptions[name])

    def test_app_server_interaction_mechanism_is_absent_from_production(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        for symbol in (
            "CodexAppSession", "build_codex_app_server_command", "INTERACTIVE_SESSIONS",
            "SESSION_LOCK", "_interact_subagent_activity", "laowu_interact",
            "turn/steer", "pending_user_request", "interactionAvailable", "pendingQuestion",
        ):
            self.assertNotIn(symbol, source)
        exposed = json.dumps(server._role_tool_schemas() + server._activity_tool_schemas(), ensure_ascii=False).lower()
        for phrase in ("interactive=true", "app-server", "steering", "follow-up", "user questions", "codex conversation"):
            self.assertNotIn(phrase, exposed)

    def test_provider_execution_has_no_interactive_switch(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertNotIn("interactive=", source.lower())
        self.assertNotIn("interactive:", source.lower())
        self.assertNotIn("interactive)", source.lower())
        command = server.build_codex_command("route_a_group_1", "scout", r"C:\\work")
        self.assertIn("--ephemeral", command)

    def test_legacy_interaction_fields_load_but_are_not_emitted(self):
        activity_id = "c" * 32
        legacy = server._default_state()
        now = "2026-10-05T00:00:00+00:00"
        legacy["retainedActivities"] = [{
            "id": activity_id, "role": "scout", "status": "completed", "requestedGroup": "auto",
            "task": "old", "model": "fixture-model", "startedAt": now, "updatedAt": now, "events": [],
            "pendingQuestion": {"questions": []}, "interactionAvailable": True,
        }]
        state_path = Path(__file__).resolve().parents[2] / "test-legacy-state.json"
        legacy_path = Path(__file__).resolve().parents[2] / "test-legacy-state-legacy.json"
        try:
            state_path.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(server, "ACTIVITY_STORE_PATH", state_path), patch.object(
                server, "LEGACY_ACTIVITY_STORE_PATH", legacy_path
            ):
                loaded = server.load_state()
        finally:
            state_path.unlink(missing_ok=True)
            legacy_path.unlink(missing_ok=True)
        record = loaded["retainedActivities"][0]
        self.assertNotIn("pendingQuestion", record)
        self.assertNotIn("interactionAvailable", record)

    def test_cancel_subagent_task_still_sets_one_shot_cancel_event(self):
        activity_id = server._new_activity("scout", {"task": "inspect", "cwd": "C:/work"})
        event = threading.Event()
        server.CANCEL_EVENTS[activity_id] = event
        try:
            with patch("server._log_dispatch_event"):
                result = server._cancel_subagent_activity(activity_id)
            self.assertTrue(event.is_set())
            self.assertFalse(result.get("isError", False))
        finally:
            server.CANCEL_EVENTS.pop(activity_id, None)
            server.ACTIVITIES.clear()
            server.ACTIVITY_ORDER.clear()

    def test_interactive_entrypoints_are_not_exposed(self):
        names = {schema["name"] for schema in server._role_tool_schemas() + server._activity_tool_schemas()}
        self.assertNotIn("laowu_interact", names)
        for schema in server._role_tool_schemas() + server._activity_tool_schemas():
            self.assertNotIn("interactive", schema.get("inputSchema", {}).get("properties", {}))

    def test_route_choice_is_dynamic(self):
        route_a = {"id": "route_a", "enabled": True, "groups": [
            {"provider_id": "route_a_group_1", "enabled": True, "auto": True},
        ]}
        route_b = {"id": "route_b", "enabled": True, "groups": [
            {"provider_id": "route_b_group_1", "enabled": True, "auto": True},
        ]}
        with patch("server.ROUTE_SETTINGS", [route_a]):
            schema = server._tool_schema("scout")
            self.assertNotIn("route_choice", schema["inputSchema"]["properties"])
        with patch("server.ROUTE_SETTINGS", [route_a, route_b]):
            schema = server._tool_schema("scout")
            self.assertEqual(schema["inputSchema"]["properties"]["route_choice"]["enum"], ["route_a", "route_b"])
            route_choice = schema["inputSchema"]["properties"]["route_choice"]
            self.assertNotIn("default", route_choice)
            self.assertIn("Only applies to Auto", route_choice["description"])


class DispatcherTests(unittest.TestCase):
    def test_process_runner_kills_non_windows_child_when_forwarder_start_fails(self):
        class LongRunningFakeProcess:
            pid = 12345

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO()
                self.stderr = io.BytesIO()
                self.killed = False
                self.wait_calls = 0

            def kill(self):
                self.killed = True

            def wait(self):
                self.wait_calls += 1
                if not self.killed:
                    raise AssertionError("run waited for a live Popen child without killing it")
                return -9

        child = LongRunningFakeProcess()
        stderr = io.StringIO()
        stderr.buffer = io.BytesIO()
        with (
            patch.object(process_runner.sys, "platform", "linux"),
            patch.object(process_runner.subprocess, "Popen", return_value=child),
            patch.object(process_runner.threading.Thread, "start", side_effect=RuntimeError("injected start failure")),
            patch.object(process_runner.sys, "stderr", stderr),
        ):
            result = process_runner.run(["long-running-child"])

        self.assertEqual(result, 70)
        self.assertTrue(child.killed)
        self.assertEqual(child.wait_calls, 1)
        self.assertIn("injected start failure", stderr.getvalue())

    def test_process_runner_stays_alive_until_descendants_close_output_pipes(self):
        runner = Path(server.__file__).with_name("process_runner.py")
        child_code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.5)']); "
            "print('done')"
        )
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(runner), "--", sys.executable, "-c", child_code],
            input="task prompt", text=True, capture_output=True, timeout=5, check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "done\n")
        self.assertGreaterEqual(time.monotonic() - started, 0.35)

    @unittest.skipUnless(server.os.name == "nt", "Windows process-tree behavior")
    def test_process_runner_root_can_terminate_a_descendant_after_codex_exits(self):
        runner = Path(server.__file__).with_name("process_runner.py")
        child_code = (
            "import subprocess,sys; "
            "descendant=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "print(f'ready {descendant.pid}',flush=True)"
        )
        process = subprocess.Popen(
            [sys.executable, str(runner), "--", sys.executable, "-c", child_code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        descendant_pid = None
        try:
            self.assertIsNotNone(process.stdin)
            process.stdin.close()
            ready = process.stdout.readline().strip()
            self.assertTrue(ready.startswith("ready "), ready)
            descendant_pid = int(ready.split()[1])
            time.sleep(0.1)
            self.assertIsNone(process.poll())
            self.assertEqual(server._terminate_process_tree(process), "")
            self.assertIsNotNone(process.poll())

            tasklist = shutil.which("tasklist.exe") or shutil.which("tasklist")
            self.assertIsNotNone(tasklist, "tasklist is required to verify descendant exit")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = subprocess.run(
                    [tasklist, "/FI", f"PID eq {descendant_pid}", "/NH", "/FO", "CSV"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = list(csv.reader(result.stdout.splitlines()))
                if not any(len(row) > 1 and row[1] == str(descendant_pid) for row in rows):
                    break
                time.sleep(0.05)
            rows = list(csv.reader(result.stdout.splitlines()))
            self.assertFalse(
                any(len(row) > 1 and row[1] == str(descendant_pid) for row in rows), result.stdout,
            )
        finally:
            if process.poll() is None:
                server._terminate_process_tree(process)
            if descendant_pid is not None:
                taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
                if taskkill:
                    subprocess.run(
                        [taskkill, "/PID", str(descendant_pid), "/T", "/F"],
                        capture_output=True, text=True, check=False,
                    )
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()

    @unittest.skipUnless(server.os.name == "nt", "Windows process-tree behavior")
    def test_process_runner_cleans_up_child_when_forwarder_start_fails(self):
        tasklist = shutil.which("tasklist.exe") or shutil.which("tasklist")
        self.assertIsNotNone(tasklist, "tasklist is required to verify child cleanup")
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temp_dir:
            marker = Path(temp_dir) / "descendant.pid"
            child_code = (
                "import subprocess,sys,time; from pathlib import Path; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)"
            )
            original_spawn = process_runner._spawn_windows_child
            spawned = []
            started_threads = []
            original_thread_start = threading.Thread.start
            start_count = 0

            def capture_spawn(command):
                child, job_handle = original_spawn(command)
                spawned.append((child, job_handle))
                return child, job_handle

            def fail_thread_start(_thread):
                nonlocal start_count
                start_count += 1
                if start_count == 1:
                    original_thread_start(_thread)
                    started_threads.append(_thread)
                    return
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        pid_text = marker.read_text(encoding="utf-8").strip()
                    except OSError:
                        pid_text = ""
                    if pid_text.isdecimal() and int(pid_text) > 0:
                        break
                    time.sleep(0.01)
                self.assertTrue(pid_text.isdecimal() and int(pid_text) > 0,
                                "child did not publish a complete descendant PID before thread startup")
                raise RuntimeError("injected thread startup failure")

            stderr = io.StringIO()
            stderr.buffer = io.BytesIO()
            startup_error = None
            try:
                with (
                    patch.object(process_runner, "_spawn_windows_child", side_effect=capture_spawn),
                    patch.object(process_runner.threading.Thread, "start", fail_thread_start),
                    patch.object(process_runner.sys, "stderr", stderr),
                ):
                    run_result = process_runner.run([
                        sys.executable, "-c", child_code,
                    ])
            except RuntimeError as exc:
                startup_error = exc
            if startup_error and spawned:
                # Clean the RED run, whose old path leaks the child and job handle.
                import _winapi

                _winapi.CloseHandle(spawned[0][1])
                spawned[0][0].wait()
                for stream in (spawned[0][0].stdin, spawned[0][0].stdout, spawned[0][0].stderr):
                    stream.close()
                for thread in started_threads:
                    thread.join()
            descendant_pid = marker.read_text(encoding="utf-8").strip()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = subprocess.run(
                    [tasklist, "/FI", f"PID eq {descendant_pid}", "/NH", "/FO", "CSV"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = list(csv.reader(result.stdout.splitlines()))
                if not any(len(row) > 1 and row[1] == descendant_pid for row in rows):
                    break
                time.sleep(0.05)
            rows = list(csv.reader(result.stdout.splitlines()))
            self.assertFalse(
                any(len(row) > 1 and row[1] == descendant_pid for row in rows), result.stdout,
            )
            self.assertIsNone(startup_error, "run propagated a thread startup failure instead of cleaning up")
            self.assertEqual(run_result, 70)
            self.assertIn("injected thread startup failure", stderr.getvalue())
            self.assertTrue(started_threads)
            self.assertTrue(all(not thread.is_alive() for thread in started_threads))

    def test_ephemeral_runner_rejects_external_http_before_spawning(self):
        provider_id = "route_a_group_1"
        provider = dict(server.PROVIDERS[provider_id], base_url="http://api.example.com/v1")
        key_name = server.KEY_NAMES[provider_id]
        with (
            patch.dict(server.PROVIDERS, {provider_id: provider}),
            patch.dict(server.API_KEYS, {key_name: "fixture-key"}, clear=True),
            patch("server.subprocess.Popen") as spawn,
        ):
            result = server._run_codex_process(
                provider_id, "scout", "inspect", "C:/work", "model-a", None, threading.Event(),
            )

        self.assertEqual(result[0], 78)
        self.assertIn("HTTPS", result[2])
        spawn.assert_not_called()

    def test_detects_git_marker_without_running_git(self):
        with TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            nested = repo / "src"
            nested.mkdir()

            self.assertTrue(_is_git_repo(str(nested)))

    def test_missing_git_marker_does_not_spawn_git(self):
        cwd = Path("Z:/isolated/non-repo/nested")
        expected_markers = [path / ".git" for path in (cwd, *cwd.parents)]
        with (
            patch.object(server.Path, "resolve", return_value=cwd),
            patch.object(server.Path, "is_dir", autospec=True, return_value=False) as is_dir,
            patch.object(server.Path, "is_file", return_value=False),
        ):
            self.assertFalse(_is_git_repo("ignored"))

        self.assertEqual([call.args[0] for call in is_dir.call_args_list], expected_markers)

    def test_auto_routes_use_configured_group_order(self):
        routes, providers, modes = auto_route_fixture()
        with (
            patch.object(server, "ROUTE_SETTINGS", routes),
            patch.object(server, "AUTO_ROUTES", providers),
            patch.object(server, "AUTO_MODES", modes),
        ):
            self.assertEqual(route_groups("auto", "route_a"), ["route_a_group_1"])
            self.assertEqual(route_groups("auto", "route_b"), ["route_b_group_1", "route_b_group_2"])

    def test_default_is_only_selected_explicitly(self):
        with patch.dict(server.GROUPS, {"default": ["route_a_group_4"]}), patch.dict(
            server.GROUP_ROUTES, {"default": "route_a"},
        ), patch.dict(server.PROVIDER_MODELS, {"route_a_group_4": "model-default"}):
            self.assertEqual(route_groups("default"), ["route_a_group_4"])
            self.assertEqual(model_for_group("default"), "model-default")
        with patch.dict(server.AUTO_ROUTES, {"route_a": ["route_a_group_1"]}), patch.dict(
            server.PROVIDER_MODELS, {"route_a_group_1": "model-a"},
        ):
            self.assertEqual(model_for_group("auto", route_choice="route_a"), "model-a")

    def test_auto_requires_every_ordered_group_key(self):
        routes, providers, modes = auto_route_fixture()
        with (
            patch.object(server, "ROUTE_SETTINGS", routes),
            patch.object(server, "AUTO_ROUTES", providers),
            patch.object(server, "AUTO_MODES", modes),
        ):
            self.assertEqual(
                missing_credentials("auto", {}, "route_a"),
                ["PROVIDER_A_GROUP_1_API_KEY"],
            )
            self.assertEqual(
                missing_credentials("auto", {}, "route_b"),
                ["PROVIDER_B_GROUP_1_API_KEY", "PROVIDER_B_GROUP_2_API_KEY"],
            )

    def test_rate_limit_and_upstream_failures_are_retryable(self):
        self.assertTrue(classify_failure("HTTP 429 rate_limit_exceeded")[0])
        self.assertTrue(classify_failure("upstream unavailable: HTTP 503")[0])
        self.assertTrue(classify_failure("HTTP 500 upstream error")[0])
        self.assertTrue(classify_failure("error sending request: connection error")[0])

    def test_auth_failure_does_not_retry(self):
        self.assertFalse(classify_failure("HTTP 401 invalid_api_key")[0])

    def test_reads_final_codex_agent_message(self):
        events = "\n".join([
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            '{"type":"turn.completed"}',
        ])
        self.assertEqual(parse_last_assistant_message(events), "done")

    def test_model_input_is_validated(self):
        with self.assertRaises(ValueError):
            model_for_group("default", {"not": "a model"})

    def test_query_models_rejects_an_unsaved_provider_address(self):
        provider_id = "route_a_group_1"
        configured = {**server.PROVIDERS[provider_id], "base_url": "https://provider.example/v1"}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"data": []}'

        with (
            patch.dict(server.PROVIDERS, {provider_id: configured}),
            patch("server.urllib.request.urlopen", return_value=Response()) as urlopen,
        ):
            result = server._query_models(provider_id, "https://attacker.invalid/v1", "fixture-key")

        self.assertTrue(result.get("isError"))
        urlopen.assert_not_called()

    def test_query_models_does_not_depend_on_private_response_socket_attributes(self):
        provider_id = "route_a_group_1"

        class Response:
            def __init__(self):
                self.body = b'{"data":[{"id":"fixture-model"}]}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read1(self, _limit):
                body, self.body = self.body, b""
                return body

        class Opener:
            def open(self, _request, timeout):
                self.timeout = timeout
                return Response()

        opener = Opener()
        with (
            patch.dict(server.PROVIDERS, {provider_id: {**server.PROVIDERS[provider_id], "base_url": "https://provider.example/v1"}}),
            patch.dict(server.API_KEYS, {server.KEY_NAMES[provider_id]: "fixture-key"}),
            patch("server.urllib.request.build_opener", return_value=opener),
        ):
            result = server._query_models(provider_id, "https://provider.example/v1")

        self.assertEqual(result["structuredContent"]["models"], ["fixture-model"])

    def test_provider_base_url_collapses_duplicate_path_slashes(self):
        provider_id = "route_a_group_1"
        with patch.dict(server.PROVIDERS, {provider_id: {
            **server.PROVIDERS[provider_id], "base_url": "https://provider.example//v1/",
        }}):
            details = server._validated_provider_details({provider_id: {
                "model": "fixture-model", "base_url": "https://provider.example//v1/",
            }})

        self.assertEqual(details[provider_id]["base_url"], "https://provider.example/v1")

    def test_model_query_worker_returns_an_error_when_handler_raises(self):
        with (
            patch.object(server, "_handle_tool_call", side_effect=RuntimeError("unexpected")),
            patch.object(server, "_write_response") as write_response,
        ):
            server._handle_model_query(17, {"method": "tools/call"})

        write_response.assert_called_once()
        args, kwargs = write_response.call_args
        self.assertEqual(args[0], 17)
        self.assertTrue(kwargs["result"]["isError"])
        self.assertIn("RuntimeError", kwargs["result"]["content"][0]["text"])

    def test_role_guidance_matches_role(self):
        self.assertIn("reviewer worker", build_prompt("route_a_group_2", "reviewer", "model-a", "inspect").lower())
        self.assertIn("Read only", build_prompt("route_a_group_2", "reviewer", "model-a", "inspect"))

    def test_explicit_default_group_model_is_selected(self):
        command = build_codex_command(
            "route_a_group_4", "scout", r"C:\\work", codex_path=r"C:\\Codex\\codex.exe",
            model="fixture-model",
        )
        self.assertEqual(command[command.index("--model") + 1], "fixture-model")

    def test_coder_is_workspace_scoped_with_medium_reasoning(self):
        command = build_codex_command(
            "route_a_group_2", "coder", r"C:\\work", codex_path=r"C:\\Codex\\codex.exe"
        )
        self.assertIn("workspace-write", command)
        self.assertIn('model_reasoning_effort="medium"', command)

    def test_builds_scoped_subprocess_command(self):
        command = build_codex_command(
            "route_a_group_2", "scout", r"C:\\work", codex_path=r"C:\\Codex\\codex.exe"
        )
        self.assertIn('model_provider="provider_a_group_2"', command)
        self.assertIn("--sandbox", command)
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)

    def test_custom_provider_command_overrides_model_provider_key_slot(self):
        provider_id = "laowu_fixture"
        with (
            patch.dict(server.PROVIDERS, {provider_id: {"model_provider": "provider_a_group_1", "base_url": ""}}),
            patch.dict(server.PROVIDER_IDS, {provider_id: "provider_a_group_1"}),
            patch.dict(server.KEY_NAMES, {provider_id: "LAOWU_FIXTURE_API_KEY"}),
        ):
            command = build_codex_command(provider_id, "scout", str(Path(__file__).parent))
        self.assertIn('model_providers.provider_a_group_1.env_key="LAOWU_FIXTURE_API_KEY"', command)

    def test_child_command_registers_ephemeral_headless_browser(self):
        command = build_codex_command(
            "route_a_group_1", "scout", r"C:\\work", codex_path=r"C:\\Codex\\codex.exe"
        )

        self.assertEqual(command[1], "exec")
        self.assertNotIn("--search", command)
        self.assertTrue(any(arg.startswith("mcp_servers.laowu_browser.command=") for arg in command))
        self.assertTrue(any(arg.startswith("mcp_servers.laowu_browser.args=") for arg in command))
        self.assertIn('mcp_servers.laowu_browser.default_tools_approval_mode="writes"', command)
        self.assertIn('mcp_servers.laowu_browser.tools.search.approval_mode="writes"', command)
        self.assertFalse(any("anysearch" in arg.lower() for arg in command))

    def test_profile_can_disable_external_mcp_and_installed_skills_per_run(self):
        project_root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix=".test-profile-capabilities-", dir=project_root) as temp_dir:
            temp = Path(temp_dir)
            codex_home = temp / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                '[mcp_servers.remote_docs]\nurl="https://example.invalid/mcp"\n'
                '[plugins."sample@test".mcp_servers.local_tool]\nenabled=true\n',
                encoding="utf-8",
            )
            project = temp / "project"
            (project / ".codex").mkdir(parents=True)
            (project / ".git").mkdir()
            (project / ".codex" / "config.toml").write_text(
                '[mcp_servers.project_tool]\ncommand="tool"\n', encoding="utf-8",
            )
            skill_paths = [
                codex_home / "skills" / "personal" / "SKILL.md",
                project / ".agents" / "skills" / "repo-skill" / "SKILL.md",
            ]
            for skill_path in skill_paths:
                skill_path.parent.mkdir(parents=True, exist_ok=True)
                skill_path.write_text("---\nname: fixture\ndescription: fixture\n---\n", encoding="utf-8")

            command = build_codex_command(
                "route_a_group_1", "scout", str(project), codex_path="codex-fixture",
                codex_home=codex_home, allow_mcp_tools=False, allow_skills=False,
            )

        overrides = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-c"]
        self.assertTrue(any("remote_docs" in value and value.endswith(".enabled=false") for value in overrides))
        self.assertTrue(any("sample@test" in value and "local_tool" in value and value.endswith(".enabled=false") for value in overrides))
        self.assertTrue(any("project_tool" in value and value.endswith(".enabled=false") for value in overrides))
        self.assertNotIn('mcp_servers.laowu_browser.command=', "\n".join(overrides))
        for override in overrides:
            if override.endswith(".enabled=false"):
                tomllib.loads(override)
        skills = next(value.removeprefix("skills.config=") for value in overrides if value.startswith("skills.config="))
        self.assertEqual(len(tomllib.loads(f"config={skills}")["config"]), 2)

    def test_worker_prompt_obeys_profile_capability_flags(self):
        disabled = build_prompt(
            "route_a_group_1", "scout", "model-a", "inspect", allow_mcp_tools=False, allow_skills=False,
        )
        enabled = build_prompt("route_a_group_1", "scout", "model-a", "inspect")
        self.assertIn("Do not call external MCP tools", disabled)
        self.assertIn("Do not invoke installed Skills", disabled)
        self.assertIn("Use configured MCP tools when they help", enabled)
        self.assertIn("Use installed Skills when they help", enabled)

    def test_progress_notification_uses_mcp_token_and_status(self):
        make_notification = getattr(server, "progress_notification", None)
        self.assertTrue(callable(make_notification), "progress_notification helper is missing")
        self.assertEqual(
            make_notification("req-9", 2, "VLP child is running"),
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": "req-9",
                    "progress": 2,
                    "message": "VLP child is running",
                },
            },
        )

    def test_tools_list_exposes_one_named_tool_per_role(self):
        schemas = server._role_tool_schemas()
        role_names = [schema["name"] for schema in schemas if schema["name"] in server.ROLE_TOOL_NAMES.values()]

        self.assertEqual(
            role_names,
            ["run_subagent_scout", "run_subagent_reviewer", "run_subagent_tester", "run_subagent_coder", "run_subagent_free"],
        )
        descriptions = {schema["name"]: schema["description"] for schema in schemas}
        self.assertIn("勘察员（scout）和审查员（reviewer）", descriptions["run_subagent_task"])
        self.assertIn("编码员（coder）", descriptions["run_subagent_coder"])
        for schema in schemas:
            if schema["name"] not in server.ROLE_TOOL_NAMES.values():
                continue
            self.assertNotIn("role", schema["inputSchema"]["properties"])
            self.assertEqual(schema["inputSchema"]["required"], ["task", "cwd"])
            self.assertEqual(
                schema["inputSchema"]["properties"]["route_choice"]["enum"],
                ["route_a", "route_b"],
            )
        result_schema = next(schema for schema in schemas if schema["name"] == "laowu_task_result")
        self.assertEqual(result_schema["inputSchema"]["required"], ["activity_id", "action"])
        self.assertEqual(result_schema["inputSchema"]["properties"]["action"]["enum"], ["get", "acknowledge"])
        self.assertEqual(result_schema["inputSchema"]["properties"]["offset"]["minimum"], 0)

    def test_role_specific_tool_dispatch_fixes_the_role_argument(self):
        with patch("server._log_dispatch_event"), patch("server._start_codex_tasks") as start:
            server._handle_tool_call("req-1", {
                "name": "run_subagent_reviewer",
                "arguments": {"task": "inspect", "cwd": "C:/work", "route_choice": "route_b", "role": "coder"},
            })
        tasks = start.call_args.args[1]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["profile_id"], "builtin-reviewer")
        self.assertNotIn("role", tasks[0])
        self.assertEqual(tasks[0]["task"], "inspect")
        self.assertEqual(tasks[0]["route_choice"], "route_b")
        self.assertTrue(start.call_args.kwargs["single"])

    def test_reveal_credential_returns_the_key_only_in_private_metadata(self):
        provider_id = "route_a_group_1"
        key_name = server.KEY_NAMES[provider_id]
        with (
            patch("server._log_dispatch_event"),
            patch("server._write_response") as write_response,
            patch("server._load_api_keys", return_value={key_name: "fixture-reveal-key"}),
        ):
            server._handle_tool_call(
                "req-reveal",
                {"name": "laowu_reveal_credential", "arguments": {"provider_id": provider_id}},
            )

        result = write_response.call_args.kwargs["result"]
        self.assertEqual(
            result.get("_meta", {}).get("laowu"),
            {"providerId": provider_id, "key": "fixture-reveal-key"},
        )
        self.assertNotIn("fixture-reveal-key", json.dumps(result.get("content", [])))
        self.assertNotIn("fixture-reveal-key", json.dumps(result.get("structuredContent", {})))

    def test_child_prompt_uses_headless_browser_without_leaking_private_context(self):
        prompt = build_prompt("route_a_group_1", "scout", "model-a", "inspect current docs")
        self.assertIn("headless browser", prompt.lower())
        self.assertIn("close", prompt.lower())
        self.assertIn("sensitive", prompt.lower())

    def test_prompt_is_one_shot_and_does_not_wait_for_user_interaction(self):
        prompt = build_prompt("route_a_group_1", "scout", "model-a", "inspect")
        self.assertIn("one-shot", prompt.lower())
        self.assertNotIn("ask the user with request_user_input", prompt.lower())
        self.assertNotIn("request_user_input", prompt.lower())

    def test_provider_uses_ephemeral_exec(self):
        with (
            patch("server._log_dispatch_event"),
            patch("server._set_activity_state"),
            patch("server._run_codex_process", return_value=(0, "done", "")) as run_codex,
        ):
            result = server._run_provider_with_slot(
                "route_a_group_1", "scout", "inspect", "C:/work", "model-a", None,
                "a" * 32, None,
            )

        self.assertEqual(result, (0, "done", ""))
        run_codex.assert_called_once()

    def test_ephemeral_runner_uses_stdin_and_sanitized_provider_environment(self):
        event = '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'

        class FakeInput:
            def __init__(self):
                self.parts = []

            def write(self, text):
                self.parts.append(text)

            def flush(self):
                pass

            def close(self):
                pass

            def getvalue(self):
                return "".join(self.parts)

        class FakeProcess:
            pid = 123

            def __init__(self):
                self.stdin = FakeInput()
                self.stdout = io.StringIO(event)
                self.stderr = io.StringIO("")

            def poll(self):
                return 0

            def wait(self):
                return 0

        process = FakeProcess()
        provider_key = server.KEY_NAMES["route_a_group_1"]
        other_key = server.KEY_NAMES["route_a_group_2"]
        with (
            patch.dict(server.API_KEYS, {provider_key: "fixture-key-1", other_key: "fixture-key-2"}, clear=True),
            patch.dict(server.os.environ, {provider_key: "inherited-fixture-key-1", other_key: "inherited-fixture-key-2"}),  # Synthetic env fixtures only; gitleaks:allow
            patch("server.subprocess.Popen", return_value=process) as popen,
            patch("server._log_dispatch_event"),
        ):
            code, stdout, stderr = server._run_codex_process(
                "route_a_group_1", "scout", "one-shot prompt", str(Path(__file__).parent), "model-a",
                "a" * 32, threading.Event(),
            )

        self.assertEqual((code, stdout, stderr), (0, event, ""))
        self.assertEqual(process.stdin.getvalue(), "one-shot prompt")
        command = popen.call_args.args[0]
        self.assertIn("--ephemeral", command)
        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(child_env[provider_key], "fixture-key-1")
        self.assertNotIn(other_key, child_env)

    def test_ephemeral_runner_cleans_up_when_cancelled(self):
        class RunningFakeProcess:
            pid = 123

            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")

            def poll(self):
                return None

        process = RunningFakeProcess()
        cancel_event = threading.Event()
        cancel_event.set()
        provider_key = server.KEY_NAMES["route_a_group_1"]
        with (
            patch.dict(server.API_KEYS, {provider_key: "test-vlp-key"}, clear=True),
            patch("server.subprocess.Popen", return_value=process) as popen,
            patch("server._terminate_process_tree", return_value="") as terminate,
            patch("server._log_dispatch_event"),
        ):
            code, _, stderr = server._run_codex_process(
                "route_a_group_1", "scout", "one-shot prompt", str(Path(__file__).parent), "model-a",
                "a" * 32, cancel_event,
            )

        self.assertEqual(code, 130)
        self.assertIn("cancelled", stderr.lower())
        popen.assert_not_called()
        terminate.assert_not_called()

    def test_interactive_mode_is_not_an_exposed_tool_argument(self):
        schemas = server._role_tool_schemas()
        for schema in schemas:
            self.assertNotIn("interactive", schema["inputSchema"]["properties"])

        parallel = next(schema for schema in schemas if schema["name"] == "run_subagents_parallel")
        self.assertNotIn("interactive", parallel["inputSchema"]["properties"]["tasks"]["items"]["properties"])


class PanelLaunchTests(unittest.TestCase):
    def test_launch_schema_and_continue_tool_contract(self):
        schemas = {item["name"]: item for item in server._activity_tool_schemas()}
        self.assertNotIn("laowu_launch_task", schemas)
        continuation = schemas["laowu_continue_task"]["inputSchema"]
        self.assertEqual(continuation["required"], ["activity_id", "message"])
        role_schemas = {item["name"]: item for item in server._role_tool_schemas()}
        self.assertTrue(role_schemas[server.ROLE_TOOL_NAMES["coder"]]["inputSchema"]["properties"]["master_recall"]["default"])
        self.assertTrue(role_schemas["run_subagent_profile"]["inputSchema"]["properties"]["master_recall"]["default"])
        self.assertTrue(role_schemas["run_subagents_parallel"]["inputSchema"]["properties"]["tasks"]["items"]["properties"]["master_recall"]["default"])

    def test_persistent_command_and_thread_id_are_captured(self):
        command = server.build_codex_command(
            "route_a_group_2", "coder", "C:/work", model="fixture-model", master_recall=True,
        )
        self.assertNotIn("--ephemeral", command)
        resume_command = server.build_codex_command(
            "route_a_group_2", "coder", "C:/work", model="fixture-model",
            master_recall=True, session_id="thread-fixture",
        )
        self.assertEqual(resume_command[:2], [server.CODEX, "exec"])
        self.assertLess(resume_command.index("--sandbox"), resume_command.index("resume"))
        self.assertIn("thread-fixture", resume_command)
        self.assertNotIn("--ephemeral", resume_command)
        self.assertIn("--cd", resume_command)
        self.assertIn("--sandbox", resume_command)
        activity_id = server._new_activity("coder", {
            "task": "inspect", "cwd": "C:/work", "master_recall": True,
            "allowMcpTools": False, "allowSkills": True, "model": "fixture-model",
        })
        server._append_codex_output_line(
            activity_id, json.dumps({"type": "thread.started", "thread_id": "thread-fixture"}), set(),
        )
        activity = next(item for item in server._activity_snapshot()["activities"] if item["id"] == activity_id)
        self.assertNotIn("sessionId", activity)
        self.assertEqual(server.ACTIVITIES[activity_id]["sessionId"], "thread-fixture")
        self.assertTrue(activity["masterRecallEnabled"])
        self.assertTrue(activity["masterRecallAvailable"] is False)
        self.assertEqual(activity["cwd"], str(Path("C:/work").resolve()))
        self.assertFalse(activity["allowMcpTools"])

    def test_completed_recall_activity_is_available_and_persisted_with_permissions(self):
        activity_id = server._new_activity("coder", {
            "task": "inspect", "cwd": "C:/work", "master_recall": True,
            "allowMcpTools": False, "allowSkills": True, "model": "fixture-model",
        })
        server._set_activity_state(activity_id, sessionId="thread-fixture", status="completed")
        activity = next(item for item in server._activity_snapshot()["activities"] if item["id"] == activity_id)
        self.assertTrue(activity["masterRecallAvailable"])
        retained = dict(server._validate_state({
            **server._default_state(),
            "retainedActivities": [server.ACTIVITIES[activity_id]],
        })["retainedActivities"][0])
        self.assertEqual(retained["sessionId"], "thread-fixture")
        self.assertEqual(retained["cwd"], str(Path("C:/work").resolve()))
        self.assertFalse(retained["allowMcpTools"])

        with patch.object(server, "PERSISTED_STATE", {"retainedActivities": [retained]}), patch.object(
            server, "ACTIVITIES", {},
        ), patch.object(server, "ACTIVITY_ORDER", []), patch.object(server, "ACTIVITY_SECRET_VALUES", {}):
            server._restore_retained_activities()
            restored = server.ACTIVITIES[activity_id]
        self.assertEqual(restored["sessionId"], "thread-fixture")
        self.assertEqual(restored["cwd"], str(Path("C:/work").resolve()))
        self.assertFalse(restored["allowMcpTools"])

    def test_continue_resumes_same_provider_and_appends_events_to_original_activity(self):
        cwd = str(Path(__file__).resolve().parent)
        activity_id = server._new_activity("coder", {
            "task": "inspect", "cwd": cwd, "master_recall": True,
            "allowMcpTools": False, "allowSkills": True,
        })
        server._set_activity_state(
            activity_id, sessionId="thread-fixture", status="completed",
            currentProviderId="route_a_group_2", model="fixture-model",
        )
        output = json.dumps({"type": "item.completed", "item": {
            "id": "answer-1", "type": "agent_message", "text": "continued result",
        }})
        with patch.dict(server.API_KEYS, {server.KEY_NAMES["route_a_group_2"]: "fixture-key"}), patch(
            "server._run_provider", return_value=(0, output, "")
        ) as resume, patch("server._log_dispatch_event"):
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})

        self.assertFalse(result.get("isError"))
        resume.assert_called_once_with(
            "route_a_group_2", "coder", "follow-up", cwd, "fixture-model",
            activity_id=activity_id, allow_mcp_tools=False,
            allow_skills=True, master_recall=True, session_id="thread-fixture", cancel_event=ANY,
        )
        self.assertIsInstance(resume.call_args.kwargs["cancel_event"], threading.Event)
        activity = server.ACTIVITIES[activity_id]
        self.assertEqual(activity["status"], "completed")
        self.assertEqual([item["text"] for item in activity["events"] if item["kind"] == "user"][-1], "follow-up")
        self.assertTrue(any(item["text"] == "continued result" for item in activity["events"]))

    def test_failed_continue_keeps_original_completed_session_available(self):
        cwd = str(Path(__file__).resolve().parent)
        activity_id = server._new_activity("coder", {
            "task": "inspect", "cwd": cwd, "master_recall": True,
            "allowMcpTools": False, "allowSkills": True,
        })
        server._set_activity_state(
            activity_id, sessionId="thread-fixture", status="completed",
            currentProviderId="route_a_group_2", model="fixture-model",
        )
        with patch.dict(server.API_KEYS, {server.KEY_NAMES["route_a_group_2"]: "fixture-key"}), patch(
            "server._run_provider", return_value=(1, "", "temporary resume error")
        ), patch("server._log_dispatch_event"):
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})

        self.assertTrue(result["isError"])
        self.assertEqual(server.ACTIVITIES[activity_id]["status"], "completed")
        activity = next(item for item in server._activity_snapshot()["activities"] if item["id"] == activity_id)
        self.assertTrue(activity["masterRecallAvailable"])

    def test_continue_provider_exception_restores_completed_activity(self):
        cwd = str(Path(__file__).resolve().parent)
        activity_id = server._new_activity("coder", {
            "task": "inspect", "cwd": cwd, "master_recall": True,
            "allowMcpTools": False, "allowSkills": True,
        })
        server._set_activity_state(
            activity_id, sessionId="thread-fixture", status="completed",
            currentProviderId="route_a_group_2", model="fixture-model",
        )
        with patch.dict(server.API_KEYS, {server.KEY_NAMES["route_a_group_2"]: "fixture-key"}), patch(
            "server._run_provider", side_effect=OSError("fixture launch failure")
        ), patch("server._log_dispatch_event"):
            result = server._continue_activity({"activity_id": activity_id, "message": "follow-up"})

        self.assertTrue(result["isError"])
        self.assertEqual(server.ACTIVITIES[activity_id]["status"], "completed")
        self.assertTrue(server.ACTIVITIES[activity_id]["masterRecallEnabled"])
        self.assertIn("OSError", result["content"][0]["text"])



class ProfileCapabilityTests(unittest.TestCase):
    def test_parallel_batch_schema_allows_eight_items_and_rejects_nine(self):
        schema = next(item for item in server._role_tool_schemas() if item["name"] == "run_subagents_parallel")
        self.assertEqual(schema["inputSchema"]["properties"]["tasks"]["maxItems"], 8)
        self.assertEqual(server.PARALLEL_WORKERS._max_workers, 8)

        response = []
        with patch("server._write_response", side_effect=lambda _request_id, **kwargs: response.append(kwargs["result"])), \
                patch("server._log_dispatch_event"), patch("server._authorize_codex_profile", return_value=(None, "unavailable")):
            server._handle_tool_call("req-nine", {"name": "run_subagents_parallel", "arguments": {"tasks": [{}] * 9}})
        self.assertTrue(response[0]["isError"])

    def test_parallel_batch_accepts_eight_tasks(self):
        response = []
        profile = {"role": "coder", "name": "编码员", "builtin": True}
        with (
            patch("server._write_response", side_effect=lambda _request_id, **kwargs: response.append(kwargs["result"])),
            patch("server._log_dispatch_event"),
            patch("server._authorize_codex_profile", return_value=(profile, None)),
            patch("server._resolve_profile_routing", return_value=("auto", "route_a")),
            patch("server._resolve_route_choice", return_value="route_a"),
            patch("server.route_groups", return_value=["route_a_group_1"]),
            patch("server.model_for_group", return_value="fixture-model"),
            patch("server._add_pending_results"),
            patch("server._new_activity"),
            patch("server._submit_bounded", return_value=True) as submit,
            patch.object(server, "STATE_LOAD_ERROR", None),
        ):
            server._handle_tool_call("req-eight", {
                "name": "run_subagents_parallel",
                "arguments": {"tasks": [{"role": "coder", "task": f"task-{i}", "cwd": str(Path.cwd())} for i in range(8)]},
            })
        self.assertEqual(submit.call_count, 8)
        self.assertEqual(len(response[0]["structuredContent"]["tasks"]), 8)
        self.assertTrue(all(task["status"] == "queued" for task in response[0]["structuredContent"]["tasks"]))

    def test_legacy_state_defaults_profile_capabilities_to_allowed(self):
        state = server._default_state()
        state.pop("builtinCapabilities")
        state["profiles"] = [{
            "id": "profile-1", "name": "Review", "role": "reviewer", "instructions": "Review code",
            "codexCallable": True, "routeGroup": "", "createdAt": "2026-10-06T00:00:00Z",
            "updatedAt": "2026-10-06T00:00:00Z",
        }]
        normalized = server._validate_state(state)
        self.assertTrue(normalized["profiles"][0]["allowMcpTools"])
        self.assertTrue(normalized["profiles"][0]["allowSkills"])
        self.assertTrue(normalized["builtinCapabilities"]["reviewer"]["allowMcpTools"])
        self.assertTrue(normalized["builtinCapabilities"]["reviewer"]["allowSkills"])
        self.assertNotIn("builtinConcurrency", normalized)

    def test_legacy_builtin_concurrency_state_is_ignored(self):
        state = server._default_state()
        state["builtinConcurrency"] = {"reviewer": 1, "tester": 8, "unknown": "legacy"}
        normalized = server._validate_state(state)
        self.assertNotIn("builtinConcurrency", normalized)

    def test_state_load_persists_removal_of_legacy_role_limits(self):
        state = server._default_state()
        state["builtinConcurrency"] = {"reviewer": 1}
        state["subagentConcurrency"] = 2
        with TemporaryDirectory(prefix=".test-role-limit-", dir=Path(__file__).resolve().parents[2]) as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            legacy_path = Path(temp_dir) / "legacy-state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(server, "ACTIVITY_STORE_PATH", state_path), patch.object(
                server, "LEGACY_ACTIVITY_STORE_PATH", legacy_path,
            ):
                normalized = server.load_state()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotIn("builtinConcurrency", normalized)
        self.assertNotIn("builtinConcurrency", persisted)
        self.assertNotIn("subagentConcurrency", normalized)
        self.assertNotIn("subagentConcurrency", persisted)

    def test_eight_parallel_tasks_of_one_role_enter_shared_executor_slots(self):
        started = threading.Barrier(9)
        finish = threading.Event()
        executor = server.concurrent.futures.ThreadPoolExecutor(max_workers=8)
        capacity = threading.BoundedSemaphore(8 + server.MAX_QUEUED_WORK)
        def block():
            started.wait(timeout=2)
            finish.wait(2)
        try:
            accepted = [server._submit_bounded(executor, capacity, block) for _ in range(8)]
            self.assertEqual(accepted, [True] * 8)
            started.wait(timeout=2)
        finally:
            finish.set()
            executor.shutdown(wait=True)
        self.assertEqual(server.PARALLEL_WORKERS._max_workers, 8)
        self.assertEqual(server.PROVIDER_SLOTS._initial_value, 8)

    def test_profiles_schema_has_no_concurrency_setting(self):
        schema = next(item for item in server._activity_tool_schemas() if item["name"] == "laowu_profiles")
        self.assertNotIn("concurrency", schema["inputSchema"]["properties"]["action"]["enum"])
        self.assertNotIn("concurrency", schema["inputSchema"]["properties"])
        self.assertNotIn("maxConcurrent", server._builtin_profile("reviewer"))

    def test_provider_and_native_fallback_use_fixed_worker_pool_without_saved_gate(self):
        provider_id = "route_a_group_1"
        with (
            patch.dict(server.API_KEYS, {server.KEY_NAMES[provider_id]: "fixture-key"}),
            patch("server._run_provider_with_slot", return_value=(0, "provider", "")) as provider_run,
            patch("server._run_native_codex_fallback_with_slot", return_value=(0, "native", "")) as native_run,
        ):
            provider_result = server._run_provider(provider_id, "reviewer", "task", str(Path.cwd()), "model")
            native_result = server._run_native_codex_fallback("reviewer", "task", str(Path.cwd()), [], threading.Event())

        self.assertEqual(provider_result, (0, "provider", ""))
        self.assertEqual(native_result, (0, "native", ""))
        provider_run.assert_called_once()
        native_run.assert_called_once()

    def test_profiles_action_persists_capability_choices(self):
        with TemporaryDirectory(prefix=".test-profile-save-", dir=Path(__file__).resolve().parents[2]) as temp_dir:
            temp = Path(temp_dir)
            with patch.multiple(
                server, PERSISTED_STATE=server._default_state(), STATE_LOAD_ERROR=None,
                ACTIVITY_STORE_PATH=temp / "state.json", save_state=lambda _state: None,
            ):
                result = server._profiles_action({
                    "action": "create", "name": "No tools", "role": "reviewer",
                    "instructions": "Review only", "codex_callable": True,
                    "allow_mcp_tools": False, "allow_skills": False,
                })
                profile = result["structuredContent"]["profile"]
                server._profiles_action({
                    "action": "builtin_capabilities", "role": "reviewer",
                    "allow_mcp_tools": False, "allow_skills": False,
                })
                snapshot = server._profile_snapshot()
        self.assertFalse(profile["allowMcpTools"])
        self.assertFalse(profile["allowSkills"])
        self.assertFalse(snapshot["builtinProfiles"][1]["allowMcpTools"])
        self.assertFalse(snapshot["builtinProfiles"][1]["allowSkills"])

    def test_route_group_options_omit_disabled_routes_and_groups(self):
        routes = [
            {"id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential", "groups": [
                {"provider_id": "route_a_group_1", "auto": True, "enabled": True},
                {"provider_id": "route_a_group_2", "auto": False, "enabled": False},
            ]},
            {"id": "route_b", "name": "B", "enabled": False, "auto_mode": "single", "groups": [
                {"provider_id": "route_b_group_1", "auto": True, "enabled": True},
            ]},
        ]
        with patch.object(server, "ROUTE_SETTINGS", routes):
            options = server._profile_route_group_options()
        self.assertEqual([item["value"] for item in options], ["", "auto@route_a", "route_a:route_a_group_1"])

    def test_profile_capability_fields_are_declared_in_tool_schemas(self):
        profiles = next(item for item in server._activity_tool_schemas() if item["name"] == "laowu_profiles")
        self.assertIn("builtin_capabilities", profiles["inputSchema"]["properties"]["action"]["enum"])
        for field in ("allow_mcp_tools", "allow_skills"):
            self.assertEqual(profiles["inputSchema"]["properties"][field], {"type": "boolean"})


class RouteResetTests(unittest.TestCase):
    def _save_with_route_state(self, original_routes, remaining_routes, state):
        config_path = Path(__file__).resolve().with_name("isolated-user-config.json")
        original = {
            "routes": original_routes,
            "providers": {key: dict(value) for key, value in server.DEFAULT_PROVIDERS.items()},
        }
        with patch.multiple(
            server, USER_CONFIG_PATH=config_path, USER_CONFIG=dict(original),
            ROUTE_SETTINGS=server._valid_route_settings(original_routes),
            PROVIDERS={key: dict(value) for key, value in original["providers"].items()},
            PERSISTED_STATE=state,
        ), patch("server._atomic_write_text") as write:
            result = server._save_route_settings(remaining_routes, {}, {})
        return result, write

    def test_route_disable_is_blocked_while_profile_references_auto_route(self):
        original_routes = [{
            "id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential",
            "native_codex_fallback": False,
            "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}],
        }]
        state = server._default_state()
        state["profiles"] = [{"id": "fixture", "name": "Auto profile", "routeGroup": "auto@route_a"}]
        disabled_routes = [{**original_routes[0], "enabled": False}]

        result, write = self._save_with_route_state(original_routes, disabled_routes, state)

        self.assertTrue(result["isError"])
        self.assertIn("Auto profile", result["content"][0]["text"])
        write.assert_not_called()

    def test_group_disable_is_blocked_while_builtin_references_manual_group(self):
        original_routes = [{
            "id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential",
            "native_codex_fallback": False,
            "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}],
        }]
        state = server._default_state()
        state["builtinRouteGroups"]["reviewer"] = "route_a:route_a_group_1"
        disabled_routes = [{**original_routes[0], "groups": [
            {"provider_id": "route_a_group_1", "auto": True, "enabled": False},
        ]}]

        result, write = self._save_with_route_state(original_routes, disabled_routes, state)

        self.assertTrue(result["isError"])
        self.assertIn(server.ROLE_LABELS["reviewer"], result["content"][0]["text"])
        write.assert_not_called()

    def test_auto_route_remains_valid_when_one_of_multiple_auto_providers_is_deleted(self):
        original_routes = [{
            "id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential",
            "native_codex_fallback": False,
            "groups": [
                {"provider_id": "route_a_group_1", "auto": True, "enabled": True},
                {"provider_id": "route_a_group_2", "auto": True, "enabled": True},
            ],
        }]
        state = server._default_state()
        state["profiles"] = [{"id": "fixture", "name": "Auto profile", "routeGroup": "auto@route_a"}]
        remaining_routes = [{**original_routes[0], "groups": [original_routes[0]["groups"][1]]}]

        result, write = self._save_with_route_state(original_routes, remaining_routes, state)

        self.assertFalse(result.get("isError"))
        write.assert_called_once()

    def test_disabling_only_native_fallback_is_blocked_while_auto_is_referenced(self):
        original_routes = [{
            "id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential",
            "native_codex_fallback": True,
            "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}],
        }]
        state = server._default_state()
        state["builtinRouteGroups"]["reviewer"] = "auto@route_a"
        no_auto_route = [{
            **original_routes[0], "native_codex_fallback": False,
            "groups": [{"provider_id": "route_a_group_1", "auto": False, "enabled": False}],
        }]

        result, write = self._save_with_route_state(original_routes, no_auto_route, state)

        self.assertTrue(result["isError"])
        self.assertIn(server.ROLE_LABELS["reviewer"], result["content"][0]["text"])
        write.assert_not_called()

    def test_group_delete_is_blocked_when_profile_references_that_option(self):
        config_path = Path(__file__).resolve().with_name("isolated-user-config.json")
        original = {
            "routes": [{
                "id": "route_a", "name": "A", "auto_mode": "single",
                "groups": [
                    {"provider_id": "route_a_group_1", "auto": True},
                    {"provider_id": "route_a_group_2", "auto": True},
                ],
            }],
            "providers": {key: dict(value) for key, value in server.DEFAULT_PROVIDERS.items()},
        }
        state = server._default_state()
        state["builtinRouteGroups"]["reviewer"] = "auto@route_a"
        state["profiles"] = [{
            "id": "fixture-profile", "name": "Group 1 profile", "role": "reviewer",
            "instructions": "Inspect", "codexCallable": True,
            "routeGroup": "route_a:route_a_group_1", "createdAt": "2026-10-08T00:00:00Z",
            "updatedAt": "2026-10-08T00:00:00Z",
        }]
        remaining_routes = [{
            "id": "route_a", "name": "A", "auto_mode": "single",
            "groups": [{"provider_id": "route_a_group_2", "auto": True}],
        }]
        with patch.multiple(
            server, USER_CONFIG_PATH=config_path, USER_CONFIG=dict(original),
            ROUTE_SETTINGS=server._valid_route_settings(original["routes"]),
            PROVIDERS={key: dict(value) for key, value in original["providers"].items()},
            PERSISTED_STATE=state,
        ), patch("server._atomic_write_text") as write:
            result = server._save_route_settings(remaining_routes, {}, {})

        self.assertTrue(result["isError"])
        message = result["content"][0]["text"]
        self.assertIn("Group 1 profile", message)
        self.assertNotIn(server.ROLE_LABELS["reviewer"], message)
        write.assert_not_called()

    def test_route_delete_is_blocked_while_profiles_reference_its_route_groups(self):
        config_path = Path(__file__).resolve().with_name("isolated-user-config.json")
        original = {
            "routes": [
                {"id": "route_a", "name": "A", "groups": [{"provider_id": "route_a_group_1", "auto": True}]},
                {"id": "route_b", "name": "B", "groups": [{"provider_id": "route_b_group_1", "auto": True}]},
            ],
            "providers": {key: dict(value) for key, value in server.DEFAULT_PROVIDERS.items()},
        }
        state = server._default_state()
        state["builtinRouteGroups"]["reviewer"] = "auto@route_a"
        state["profiles"] = [{
            "id": "fixture-profile", "name": "Route A profile", "role": "reviewer",
            "instructions": "Inspect", "codexCallable": True,
            "routeGroup": "route_a:route_a_group_1", "createdAt": "2026-10-08T00:00:00Z",
            "updatedAt": "2026-10-08T00:00:00Z",
        }]
        remaining_routes = [{"id": "route_b", "name": "B", "groups": [{"provider_id": "route_b_group_1", "auto": True}]}]
        with patch.multiple(
            server, USER_CONFIG_PATH=config_path, USER_CONFIG=dict(original),
            ROUTE_SETTINGS=server._valid_route_settings(original["routes"]),
            PROVIDERS={key: dict(value) for key, value in original["providers"].items()},
            PERSISTED_STATE=state,
        ), patch("server._atomic_write_text") as write:
            result = server._save_route_settings(remaining_routes, {}, {})

        self.assertTrue(result["isError"])
        self.assertIn("Route A profile", result["content"][0]["text"])
        self.assertIn(server.ROLE_LABELS["reviewer"], result["content"][0]["text"])
        write.assert_not_called()

    def test_credential_helper_detail_keeps_error_and_redacts_keys(self):
        detail = server._credential_helper_detail(
            "#< CLIXML\n<Objs><S S='Error'>Access denied while saving fixture-secret</S></Objs>",
            ["fixture-secret"],
        )
        self.assertIn("Access denied while saving", detail)
        self.assertIn("[redacted]", detail)
        self.assertNotIn("fixture-secret", detail)

    def test_fresh_install_routes_start_with_one_unconfigured_group_each(self):
        self.assertEqual(
            [route["groups"] for route in server.DEFAULT_ROUTE_SETTINGS],
            [[{"provider_id": "route_a_group_1", "auto": True, "enabled": True}],
             [{"provider_id": "route_b_group_1", "auto": True, "enabled": True}]],
        )

    def test_empty_route_is_valid_after_its_groups_are_deleted(self):
        self.assertEqual(
            server._valid_route_settings([
                {"id": "route_a", "name": "路线 A", "groups": []},
                {"id": "route_b", "name": "路线 B", "groups": [
                    {"provider_id": "route_b_group_1", "auto": True},
                ]},
            ]),
            [
                {"id": "route_a", "name": "路线 A", "enabled": True, "auto_mode": "sequential", "native_codex_fallback": True, "groups": []},
                {"id": "route_b", "name": "路线 B", "enabled": True, "auto_mode": "sequential", "native_codex_fallback": False, "groups": [
                    {"provider_id": "route_b_group_1", "auto": True, "enabled": True},
                ]},
            ],
        )

    def test_empty_auto_route_cannot_launch(self):
        with patch.dict(server.AUTO_ROUTES, {"route_a": []}), patch.dict(server.GROUPS, {"auto": []}):
            with self.assertRaisesRegex(ValueError, "no provider groups"):
                server.model_for_group("auto", route_choice="route_a")

    def test_save_routes_uses_bundled_helper_for_clear_and_does_not_commit_on_failure(self):
        config_path = Path(__file__).resolve().with_name("isolated-user-config.json")
        original = {
            "routes": [
                {"id": "route_a", "name": "A", "groups": [{"provider_id": "route_a_group_1", "auto": True}]},
                {"id": "route_b", "name": "B", "groups": [{"provider_id": "route_b_group_1", "auto": True}]},
            ],
            "providers": {
                "route_a_group_1": {"key_name": "LEGACY_GROUP_1_KEY"},
                "route_b_group_1": {"key_name": "LEGACY_GROUP_2_KEY"},
            },
        }
        routes = [
            {"id": "route_a", "name": "A", "groups": []},
            {"id": "route_b", "name": "B", "groups": [{"provider_id": "route_b_group_1", "auto": True}]},
        ]
        with patch.multiple(
            server,
            USER_CONFIG_PATH=config_path,
            KEY_SETUP=config_path.with_name("legacy-helper.ps1"),
            BUNDLED_KEY_SETUP=Path(server.__file__).with_name("set-provider-keys.ps1"),
            USER_CONFIG=dict(original),
            ROUTE_SETTINGS=server._valid_route_settings(original["routes"]),
            PROVIDERS={key: dict(value) for key, value in original["providers"].items()},
            KEY_NAMES={"route_a_group_1": "LEGACY_GROUP_1_KEY", "route_b_group_1": "LEGACY_GROUP_2_KEY"},
            API_KEYS={"LEGACY_GROUP_1_KEY": "fixture-secret"},
        ), patch("server.shutil.which", return_value="powershell.exe"), patch("server.subprocess.run") as run, patch("server._atomic_write_text") as write:
            run.return_value = type("Result", (), {"returncode": 1, "stderr": "legacy parameter unsupported", "stdout": ""})()
            result = server._save_route_settings(routes, {}, {}, ["route_a_group_1"])

        self.assertTrue(result["isError"])
        write.assert_not_called()
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[command.index("-File") + 1], str(Path(server.__file__).with_name("set-provider-keys.ps1")))
        self.assertIn("-UserConfigPath", command)
        self.assertIn("-NonInteractive", command)
        self.assertIn("-ClearKeyName", command)
        self.assertNotIn("fixture-secret", repr(command))
        self.assertIn("-SetKeyFromStdin", run.call_args_list[1].args[0])

    def test_reset_tool_schema_requires_confirmation_and_has_no_partial_reset_option(self):
        schema = next(item for item in server._activity_tool_schemas() if item["name"] == "laowu_reset_configuration")
        self.assertEqual(set(schema["inputSchema"]["properties"]), {"confirm"})
        self.assertEqual(schema["inputSchema"]["required"], ["confirm"])
        save_schema = next(item for item in server._activity_tool_schemas() if item["name"] == "laowu_save_routes")
        self.assertEqual(save_schema["inputSchema"]["properties"]["routes"]["items"]["properties"]["groups"]["minItems"], 0)
        route_properties = save_schema["inputSchema"]["properties"]["routes"]["items"]["properties"]
        self.assertEqual(route_properties["auto_mode"]["enum"], ["sequential", "single"])
        self.assertEqual(route_properties["native_codex_fallback"], {"type": "boolean"})
        self.assertIn("enabled", save_schema["inputSchema"]["properties"]["routes"]["items"]["required"])
        self.assertIn("native_codex_fallback", save_schema["inputSchema"]["properties"]["routes"]["items"]["required"])
        self.assertIn("enabled", route_properties["groups"]["items"]["required"])

    def test_failed_reset_restores_in_memory_state_with_disk_state(self):
        project_root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix=".test-reset-rollback-", dir=project_root) as temp:
            folder = Path(temp)
            config_path = folder / "user_config.json"
            state_path = folder / "state.json"
            prior_state = server._default_state()
            prior_state["profiles"] = [{"id": "fixture-profile"}]
            state_path.write_text(json.dumps(prior_state), encoding="utf-8")

            def save_fixture_state(state):
                state_path.write_text(json.dumps(state), encoding="utf-8")

            original_replace = server.os.replace
            attempts = []

            def transient_replace(source, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise PermissionError("synthetic transient Windows file lock")
                return original_replace(source, destination)

            with patch.multiple(
                server,
                USER_CONFIG_PATH=config_path,
                CREDENTIAL_STORE_PATH=folder / "missing-keys.xml",
                KEY_SETUP=folder / "missing-helper.ps1",
                ACTIVITY_STORE_PATH=state_path,
                USER_CONFIG={"providers": {}, "routes": []},
                PERSISTED_STATE=prior_state,
                STATE_LOAD_ERROR=None,
                ACTIVITIES={},
                ACTIVITY_ORDER=[],
                CANCEL_EVENTS={},
                ACTIVITY_SECRET_VALUES={},
                save_state=save_fixture_state,
            ), patch("server._atomic_write_text", side_effect=OSError("fixture config write failure")), patch(
                "server.os.replace", side_effect=transient_replace,
            ):
                result = server._reset_configuration(True)

                self.assertTrue(result["isError"])
                self.assertEqual(server.PERSISTED_STATE, prior_state)
                self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), prior_state)

    def test_failed_reset_reports_unrestored_history_and_preserves_backup(self):
        with TemporaryDirectory(prefix=".test-reset-lock-", dir=Path(__file__).resolve().parents[2]) as temp:
            folder = Path(temp)
            state_path = folder / "state.json"
            prior_state = server._default_state()
            prior_state["profiles"] = [{"id": "fixture-profile"}]
            state_path.write_text(json.dumps(prior_state), encoding="utf-8")
            with patch.multiple(
                server, USER_CONFIG_PATH=folder / "user_config.json",
                CREDENTIAL_STORE_PATH=folder / "missing-keys.xml", KEY_SETUP=folder / "missing-helper.ps1",
                ACTIVITY_STORE_PATH=state_path, USER_CONFIG={}, PERSISTED_STATE=prior_state,
                STATE_LOAD_ERROR=None, ACTIVITIES={}, CANCEL_EVENTS={}, ACTIVITY_ORDER=[],
                ACTIVITY_SECRET_VALUES={}, save_state=lambda state: state_path.write_text(json.dumps(state), encoding="utf-8"),
            ), patch("server._atomic_write_text", side_effect=OSError("synthetic config failure")), patch(
                "server.os.replace", side_effect=PermissionError("synthetic persistent file lock"),
            ), patch("server.time.sleep"):
                result = server._reset_configuration(True)
                self.assertTrue(result.get("isError"))
                self.assertIn("history rollback failed", result["content"][0]["text"].lower())
                self.assertIsNotNone(server.STATE_LOAD_ERROR)
                self.assertEqual(json.loads(state_path.with_suffix(".json.restore").read_text(encoding="utf-8")), prior_state)

    def test_clear_restores_route_and_provider_defaults(self):
        project_root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix=".test-route-reset-", dir=project_root) as temp:
            folder = Path(temp)
            config_path = folder / "user_config.json"
            paths = {"ui": "../../laowu-sidebar.html", "credential_store": "keys.xml"}
            original = {
                "paths": paths,
                "routes": [
                    {"id": "route_a", "name": "自定义 A", "groups": [
                        {"provider_id": "route_a_group_1", "auto": True},
                    ]},
                    {"id": "route_b", "name": "自定义 B", "groups": [
                        {"provider_id": "route_b_group_1", "auto": True},
                    ]},
                ],
                "providers": {
                    "route_a_group_1": {"label": "自定义", "model": "m1", "base_url": "https://a.invalid/v1", "key_name": "LEGACY_GROUP_1_KEY", "model_provider": "provider_a_group_1"},
                    "route_b_group_1": {"label": "自定义", "model": "m2", "base_url": "https://b.invalid/v1", "key_name": "LEGACY_GROUP_2_KEY", "model_provider": "provider_b_group_1"},
                },
            }
            config_path.write_text(json.dumps(original), encoding="utf-8")
            credential_path = folder / "keys.xml"
            credential_path.write_bytes(b"fixture")
            key_helper = folder / "set-provider-keys.ps1"
            key_helper.write_text("fixture", encoding="utf-8")

            def clear_fixture_credentials(*_args, **_kwargs):
                credential_path.unlink(missing_ok=True)
                return type("Result", (), {"returncode": 0})()

            with patch.multiple(
                server,
                USER_CONFIG_PATH=config_path,
                CREDENTIAL_STORE_PATH=credential_path,
                KEY_SETUP=key_helper,
                ACTIVITY_STORE_PATH=folder / "state.json",
                LEGACY_ACTIVITY_STORE_PATH=folder / "legacy-state.json",
                DIAGNOSTIC_LOG=folder / "diagnostic.jsonl",
                LEGACY_DIAGNOSTIC_LOG_PATH=folder / "legacy-diagnostic.jsonl",
                UI_PREFERENCES_PATH=folder / "preferences.json",
                USER_CONFIG=dict(original),
                ROUTE_SETTINGS=server._valid_route_settings(original["routes"]),
                PROVIDERS={key: dict(value) for key, value in server.PROVIDERS.items()},
                PROVIDER_LABELS=dict(server.PROVIDER_LABELS),
                PROVIDER_MODELS=dict(server.PROVIDER_MODELS),
                GROUPS=dict(server.GROUPS),
                AUTO_ROUTES={key: list(value) for key, value in server.AUTO_ROUTES.items()},
                AUTO_MODES=dict(server.AUTO_MODES),
                GROUP_ROUTES=dict(server.GROUP_ROUTES),
                MODEL=server.MODEL,
                API_KEYS=dict(server.API_KEYS),
                PERSISTED_STATE=server._default_state(),
                STATE_LOAD_ERROR=None,
                ACTIVITIES={},
                ACTIVITY_ORDER=[],
                CANCEL_EVENTS={},
                ACTIVITY_SECRET_VALUES={},
                save_state=lambda _state: None,
            ):
                with (
                    patch("server.shutil.which", return_value="powershell.exe"),
                    patch("server.subprocess.run", side_effect=clear_fixture_credentials),
                ):
                    with patch("server._write_notification") as notify:
                        result = server._reset_configuration(True)
                    persisted = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertTrue(result["structuredContent"]["reset"])
            notify.assert_called_once_with("notifications/tools/list_changed")
            self.assertFalse(credential_path.exists())
            self.assertEqual(persisted["routes"], server.DEFAULT_ROUTE_SETTINGS)
            self.assertEqual(persisted["paths"], paths)
            self.assertEqual(set(persisted["providers"]), set(server.DEFAULT_PROVIDERS))
            for provider_id, provider in server.DEFAULT_PROVIDERS.items():
                self.assertEqual(
                    persisted["providers"][provider_id],
                    {**provider, "label": "", "model": "", "base_url": ""},
                )


class AddGroupTests(unittest.TestCase):
    def _isolated(self, folder):
        original = {"routes": [
            {"id": "route_a", "name": "A", "enabled": True, "auto_mode": "sequential", "groups": [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}]},
            {"id": "route_b", "name": "B", "enabled": True, "auto_mode": "sequential", "groups": [{"provider_id": "route_b_group_1", "auto": True, "enabled": True}]},
        ], "providers": {key: dict(value) for key, value in server.DEFAULT_PROVIDERS.items()}}
        return patch.multiple(
            server, USER_CONFIG_PATH=folder / "isolated-add-group-config.json", USER_CONFIG=original,
            ROUTE_SETTINGS=server._valid_route_settings(original["routes"]),
            PROVIDERS={key: dict(value) for key, value in server.DEFAULT_PROVIDERS.items()},
            PROVIDER_MODELS={key: "" for key in server.DEFAULT_PROVIDERS},
            PROVIDER_LABELS={key: "" for key in server.DEFAULT_PROVIDERS},
            PROVIDER_ORDINALS={key: index + 1 for index, key in enumerate(server.DEFAULT_PROVIDERS)},
            PROVIDER_IDS={key: value["model_provider"] for key, value in server.DEFAULT_PROVIDERS.items()},
            KEY_NAMES={key: value["key_name"] for key, value in server.DEFAULT_PROVIDERS.items()},
            API_KEYS={}, DELETED_PROVIDERS=set(),
            GROUPS={key: list(value) for key, value in server.GROUPS.items()},
            GROUP_ROUTES=dict(server.GROUP_ROUTES), AUTO_ROUTES={key: list(value) for key, value in server.AUTO_ROUTES.items()},
            AUTO_MODES=dict(server.AUTO_MODES),
        )

    def test_repeated_add_creates_unique_named_providers_and_route_auto_groups(self):
        folder = Path(__file__).resolve().parent
        config_path = folder / "isolated-add-group-config.json"
        try:
            with self._isolated(folder), patch("server._write_notification"):
                server._add_group("route_a")
                server._add_group("route_a")
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                ids = [group["provider_id"] for group in persisted["routes"][0]["groups"] if group["provider_id"].startswith("laowu_")]
                self.assertEqual(len(ids), 2)
                self.assertEqual(len(set(ids)), 2)
                labels = []
                for provider_id in ids:
                    self.assertRegex(provider_id, r"^laowu_[0-9a-f]{32}$")
                    provider = persisted["providers"][provider_id]
                    self.assertEqual(provider["model_provider"], "provider_a_group_1")
                    self.assertRegex(provider["label"], r"^新分组 \d+$")
                    labels.append(provider["label"])
                    self.assertEqual((provider["model"], provider["base_url"]), ("", ""))
                    self.assertRegex(provider["key_name"], r"^LAOWU_[0-9A-F]{32}_API_KEY$")
                self.assertEqual(len(set(labels)), 2)
                key_names = [persisted["providers"][provider_id]["key_name"] for provider_id in ids]
                self.assertEqual(len(key_names), len(set(key_names)))
                self.assertEqual([g["provider_id"] for g in persisted["routes"][0]["groups"]][-2:], ids)
                self.assertTrue(all(g["auto"] and g["enabled"] for g in persisted["routes"][0]["groups"][-2:]))
        finally:
            config_path.unlink(missing_ok=True)

    def test_new_group_gets_an_editable_default_name(self):
        folder = Path(__file__).resolve().parent
        config_path = folder / "isolated-add-group-config.json"
        try:
            with self._isolated(folder), patch("server._write_notification"):
                result = server._add_group("route_a")
                group = result["structuredContent"]["platforms"][0]["groups"][-1]
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertRegex(group["name"], r"^新分组 \d+$")
                self.assertEqual(persisted["providers"][group["providerId"]]["label"], group["name"])
        finally:
            config_path.unlink(missing_ok=True)

    def test_add_uses_selected_route_and_atomic_failure_keeps_live_state(self):
        folder = Path(__file__).resolve().parent
        config_path = folder / "isolated-add-group-config.json"
        try:
            with self._isolated(folder), patch("server._write_notification"):
                result = server._add_group("route_b")
                self.assertIn("structuredContent", result, result.get("content"))
                provider_id = result["structuredContent"]["platforms"][1]["groups"][-1]["providerId"]
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["providers"][provider_id]["model_provider"], "provider_b_group_1")
                self.assertEqual(saved["routes"][0]["groups"], [{"provider_id": "route_a_group_1", "auto": True, "enabled": True}])
                before = (dict(server.PROVIDERS), json.loads(json.dumps(server.USER_CONFIG)), list(server.ROUTE_SETTINGS[1]["groups"]))
                with patch("server._atomic_write_text", side_effect=OSError("fixture")):
                    failed = server._add_group("route_b")
                self.assertTrue(failed["isError"])
                self.assertEqual(server.PROVIDERS, before[0])
                self.assertEqual(server.USER_CONFIG, before[1])
                self.assertEqual(server.ROUTE_SETTINGS[1]["groups"], before[2])
        finally:
            config_path.unlink(missing_ok=True)

    def test_tool_schema_and_delete_then_add_remains_available(self):
        schema = next(item for item in server._activity_tool_schemas() if item["name"] == "laowu_add_group")
        self.assertEqual(schema["inputSchema"]["required"], ["route_id"])
        self.assertEqual(set(schema["inputSchema"]["properties"]), {"route_id"})
        folder = Path(__file__).resolve().parent
        config_path = folder / "isolated-add-group-config.json"
        try:
            with self._isolated(folder), patch("server._write_notification"), patch("server.shutil.which", return_value="powershell.exe"), patch("server.subprocess.run", return_value=type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()):
                added_result = server._add_group("route_a")
                self.assertIn("structuredContent", added_result, added_result.get("content"))
                added = added_result["structuredContent"]["platforms"][0]["groups"][-1]["providerId"]
                routes = [dict(route, groups=[dict(group) for group in route["groups"]]) for route in server.ROUTE_SETTINGS]
                routes[0]["groups"] = [group for group in routes[0]["groups"] if group["provider_id"] != added]
                deleted = server._save_route_settings(routes, {}, {}, [added])
                self.assertFalse(deleted.get("isError"))
                self.assertNotIn(added, server.PROVIDERS)
                next_added = server._add_group("route_a")
                self.assertFalse(next_added.get("isError"))
                current = [item["providerId"] for item in next_added["structuredContent"]["availableGroups"] if item["providerId"].startswith("laowu_")]
                self.assertNotIn(added, current)
                self.assertEqual(len(current), 1)
        finally:
            config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
