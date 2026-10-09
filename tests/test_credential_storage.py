import json
import os
import shutil
import subprocess
import unittest
import time
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_SOURCE = PROJECT_ROOT / "tools" / "laowu_mcp" / "set-provider-keys.ps1"


class CredentialOverwriteTests(unittest.TestCase):
    def test_saving_again_replaces_the_existing_encrypted_key(self):
        if os.name != "nt":
            self.skipTest("Windows PowerShell credential storage is Windows-only")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("Windows PowerShell is unavailable")

        with TemporaryDirectory(prefix=".test-credential-", dir=PROJECT_ROOT) as temp:
            fixture = Path(temp)
            helper = fixture / "set-provider-keys.ps1"
            shutil.copyfile(HELPER_SOURCE, helper)
            config = {
                "paths": {"credential_store": str(fixture / "keys.xml")},
                "providers": {
                    "route_a_group_1": {
                        "key_name": "TEST_KEY_FOR_FIXTURE",
                        "label": "Fixture",
                    }
                },
            }
            (fixture / "user_config.json").write_text(json.dumps(config), encoding="utf-8")

            def save(value):
                return subprocess.run(
                    [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(helper), "-SetKeyFromStdin"],
                    input=json.dumps({"key_name": "TEST_KEY_FOR_FIXTURE", "api_key": value}),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                )

            first = save("fixture-key-one")
            self.assertEqual(first.returncode, 0, "Initial fixture key save failed")
            second = save("fixture-key-two")
            self.assertEqual(second.returncode, 0, "Replacement fixture key save failed")

            readback = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(helper), "-ForRunner"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
            )
            self.assertEqual(readback.returncode, 0, "Fixture credential readback failed")
            self.assertEqual(json.loads(readback.stdout)["TEST_KEY_FOR_FIXTURE"], "fixture-key-two")

    def test_concurrent_saves_for_different_providers_preserve_both_keys(self):
        if os.name != "nt":
            self.skipTest("Windows PowerShell credential storage is Windows-only")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("Windows PowerShell is unavailable")

        with TemporaryDirectory(prefix=".test-credential-race-", dir=PROJECT_ROOT) as temp:
            fixture = Path(temp)
            helper = fixture / "set-provider-keys.ps1"
            source = HELPER_SOURCE.read_text(encoding="utf-8")
            store_read = "$stored = if (Test-Path -LiteralPath $storePath) { Import-Clixml -LiteralPath $storePath } else { @{} }"
            hook = "\n[IO.File]::WriteAllText((Join-Path $env:LAOWU_TEST_MARKER_DIR ('read.' + $PID)), 'ready')\nStart-Sleep -Milliseconds 700"
            self.assertIn(store_read, source)
            helper.write_text(source.replace(store_read, store_read + hook), encoding="utf-8")
            config = {
                "paths": {"credential_store": str(fixture / "keys.xml")},
                "providers": {
                    "route_a_group_1": {"key_name": "TEST_KEY_A", "label": "Fixture A"},
                    "route_b_group_1": {"key_name": "TEST_KEY_B", "label": "Fixture B"},
                },
            }
            (fixture / "user_config.json").write_text(json.dumps(config), encoding="utf-8")
            marker_dir = fixture / "markers"
            marker_dir.mkdir()

            def save(provider_id, api_key):
                return subprocess.Popen(
                    [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(helper), "-SetKeyFromStdin"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8",
                    env={**os.environ, "LAOWU_TEST_MARKER_DIR": str(marker_dir)},
                )

            pending = []
            try:
                first = save("route_a_group_1", "fixture-key-a")
                pending.append(first)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not list(marker_dir.glob("read.*")):
                    time.sleep(0.02)
                self.assertTrue(list(marker_dir.glob("read.*")), "First helper did not reach the credential read")
                second = save("route_b_group_1", "fixture-key-b")
                pending.append(second)
                first_result = first.communicate(
                    input=json.dumps({"provider_id": "route_a_group_1", "api_key": "fixture-key-a"}),
                    timeout=20,
                )
                pending.remove(first)
                second_result = second.communicate(
                    input=json.dumps({"provider_id": "route_b_group_1", "api_key": "fixture-key-b"}),
                    timeout=20,
                )
                pending.remove(second)
                self.assertEqual(first.returncode, 0, first_result[1])
                self.assertEqual(second.returncode, 0, second_result[1])
            finally:
                for process in pending:
                    try:
                        if process.poll() is None:
                            process.terminate()
                    except OSError:
                        pass
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except OSError:
                            pass
                        try:
                            process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                    finally:
                        if process.poll() is None:
                            try:
                                process.kill()
                            except OSError:
                                pass
                        process.wait(timeout=2)

            readback = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(helper), "-ForRunner"],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
                env={**os.environ, "LAOWU_TEST_MARKER_DIR": str(marker_dir)},
            )
            self.assertEqual(readback.returncode, 0, readback.stderr)
            keys = json.loads(readback.stdout)
            self.assertEqual(keys["TEST_KEY_A"], "fixture-key-a")
            self.assertEqual(keys["TEST_KEY_B"], "fixture-key-b")


if __name__ == "__main__":
    unittest.main()
