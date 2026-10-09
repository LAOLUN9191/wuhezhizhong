import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "laowu_mcp"))
import process_runner


class ProcessRunnerWindowsCommandTests(unittest.TestCase):
    def test_windows_cmd_shim_is_rejected_without_spawning(self):
        stderr = io.StringIO()
        with patch.object(process_runner.sys, "platform", "win32"), \
                patch.object(process_runner, "_spawn_windows_child", side_effect=OSError("CreateProcess failure")) as spawn, \
                redirect_stderr(stderr):
            result = process_runner.run([r"C:\tools\codex.CMD", "exec"])

        self.assertEqual(result, 70)
        spawn.assert_not_called()
        self.assertIn("CODEX_CLI_PATH", stderr.getvalue())
        self.assertIn("codex.exe", stderr.getvalue())

    def test_windows_bat_shim_is_rejected_case_insensitively(self):
        stderr = io.StringIO()
        with patch.object(process_runner.sys, "platform", "win32"), \
                patch.object(process_runner, "_spawn_windows_child", side_effect=OSError("CreateProcess failure")) as spawn, \
                redirect_stderr(stderr):
            result = process_runner.run([r"C:\tools\codex.BaT"])

        self.assertEqual(result, 70)
        spawn.assert_not_called()
        self.assertIn("CODEX_CLI_PATH", stderr.getvalue())

    def test_windows_executable_keeps_existing_spawn_path(self):
        child = type("Child", (), {
            "stdin": io.BytesIO(),
            "stdout": io.BytesIO(),
            "stderr": io.BytesIO(),
            "wait": lambda self: 0,
        })()
        with patch.object(process_runner.sys, "platform", "win32"), \
                patch.object(process_runner, "_spawn_windows_child", return_value=(child, 0)) as spawn:
            result = process_runner.run([r"C:\Program Files\Codex\codex.exe", "exec"])

        self.assertEqual(result, 0)
        spawn.assert_called_once_with([r"C:\Program Files\Codex\codex.exe", "exec"])

    def test_non_windows_command_scripts_keep_existing_popen_path(self):
        child = type("Child", (), {
            "stdin": io.BytesIO(),
            "stdout": io.BytesIO(),
            "stderr": io.BytesIO(),
            "wait": lambda self: 0,
        })()
        with patch.object(process_runner.sys, "platform", "linux"), \
                patch.object(process_runner.subprocess, "Popen", return_value=child) as popen:
            result = process_runner.run(["codex.cmd", "exec"])

        self.assertEqual(result, 0)
        popen.assert_called_once_with(
            ["codex.cmd", "exec"], stdin=process_runner.subprocess.PIPE,
            stdout=process_runner.subprocess.PIPE, stderr=process_runner.subprocess.PIPE,
            bufsize=0,
        )


if __name__ == "__main__":
    unittest.main()
