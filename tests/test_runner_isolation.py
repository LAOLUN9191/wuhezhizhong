import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("offline_test_runner", ROOT / "tools" / "run_tests.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class TestRunnerIsolationTests(unittest.TestCase):
    def test_keeps_powershell_startup_cache_without_inheriting_user_modules(self):
        safe = RUNNER._safe_test_environment({
            "PSModuleAnalysisCachePath": "fixture-system-module-cache",
            "PSModulePath": "private-user-modules",
            "OPENAI_API_KEY": "synthetic-secret",
        })
        self.assertEqual(safe, {"PSModuleAnalysisCachePath": "fixture-system-module-cache"})

    def test_child_environment_drops_credentials_and_user_configuration(self):
        inherited = {
            "PATH": "fixture-path", "PATHEXT": ".EXE", "SystemRoot": "C:\\Windows",
            "WINDIR": "C:\\Windows", "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
            "OPENAI_API_KEY": "synthetic-openai-secret",
            "PROVIDER_A_GROUP_1_API_KEY": "synthetic-provider-secret",
            "GITHUB_TOKEN": "synthetic-github-secret", "CUSTOM_SECRET": "synthetic-custom-secret",
            "CODEX_HOME": "real-codex-home", "PYTHONPATH": "user-python-path",
        }

        safe = RUNNER._safe_test_environment(inherited)

        self.assertEqual(safe, {
            "PATH": "fixture-path", "PATHEXT": ".EXE", "SystemRoot": "C:\\Windows",
            "WINDIR": "C:\\Windows", "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
        })


if __name__ == "__main__":
    unittest.main()
