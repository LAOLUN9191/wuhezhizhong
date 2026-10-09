import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools" / "laowu_mcp"))
import server


class SubprocessEnvironmentTests(unittest.TestCase):
    def test_external_provider_inherits_runtime_settings_without_unrelated_credentials(self):
        output = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n"

        class Process:
            pid = 123
            stdin = io.StringIO()
            stdout = io.StringIO(output)
            stderr = io.StringIO()

            def poll(self):
                return 0

            def wait(self):
                return 0

        selected = server.KEY_NAMES["route_a_group_1"]
        other = server.KEY_NAMES["route_a_group_2"]
        inherited = {
            "PATH": "fixture-runtime-path", "PROJECT_BUILD_MODE": "fixture-mode",
            "UNREGISTERED_API_KEY": "synthetic-unrelated-key", "GITHUB_TOKEN": "synthetic-token",
            "AWS_SECRET_ACCESS_KEY": "synthetic-secret", "DB_PASSWORD": "synthetic-password",
            selected: "inherited-selected-key", other: "inherited-other-key",
        }
        with (
            patch.dict(server.os.environ, inherited, clear=True),
            patch.dict(server.API_KEYS, {selected: "selected-fixture-key"}, clear=True),
            patch.object(server, "build_codex_command", return_value=["codex.exe", "exec", "-"]),
            patch.object(server, "_provider_base_url", return_value=""),
            patch.object(server.subprocess, "Popen", return_value=Process()) as spawn,
            patch.object(server, "_log_dispatch_event"),
        ):
            code, _, _ = server._run_codex_process(
                "route_a_group_1", "reviewer", "fixture prompt", str(PROJECT_ROOT), "fixture-model", None, None,
            )
        self.assertEqual(code, 0)
        child_env = spawn.call_args.kwargs["env"]
        self.assertEqual(child_env, {"PATH": "fixture-runtime-path", "PROJECT_BUILD_MODE": "fixture-mode", selected: "selected-fixture-key", "LAOWU_DISPATCH_CHILD": "1"})

    def test_native_fallback_does_not_inherit_provider_or_unrelated_credentials(self):
        class Process:
            pid = 123
            returncode = 0

            def communicate(self, **kwargs):
                return "fixture output", ""

        selected = server.KEY_NAMES["route_a_group_1"]
        inherited = {"PATH": "fixture-runtime-path", "CODEX_HOME": "fixture-home",
                     "OPENAI_API_KEY": "synthetic-native-key", "GITHUB_TOKEN": "synthetic-token",
                     selected: "synthetic-provider-key"}
        with (
            patch.dict(server.os.environ, inherited, clear=True),
            patch.object(server.subprocess, "run", return_value=type("GitStatus", (), {"returncode": 0, "stdout": ""})()),
            patch.object(server.subprocess, "Popen", return_value=Process()) as spawn,
            patch.object(server, "_legacy_mcp_server_overrides", return_value=[]),
        ):
            code, _, _ = server._run_native_codex_fallback("reviewer", "fixture", str(PROJECT_ROOT), [], None)
        self.assertEqual(code, 0)
        self.assertEqual(spawn.call_args.kwargs["env"], {"PATH": "fixture-runtime-path", "CODEX_HOME": "fixture-home", "LAOWU_DISPATCH_CHILD": "1"})


if __name__ == "__main__":
    unittest.main()
