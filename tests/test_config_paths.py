import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = PROJECT_ROOT / "tools" / "laowu_mcp"
sys.path.insert(0, str(MCP_ROOT))
import server
LOAD_API_KEYS = server._load_api_keys


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
class CustomConfigCredentialTests(unittest.TestCase):
    def test_load_and_reset_use_the_selected_config_without_touching_the_default_store(self):
        with TemporaryDirectory(prefix=".test-config-paths-", dir=PROJECT_ROOT) as folder:
            fixture = Path(folder)
            helper = fixture / "set-provider-keys.ps1"
            shutil.copyfile(MCP_ROOT / helper.name, helper)
            default_config = fixture / "user_config.json"
            selected_config = fixture / "custom_config.json"
            default_store = fixture / "default.xml"
            selected_store = fixture / "selected.xml"
            paths = {
                "workspace": str(PROJECT_ROOT), "key_setup": str(helper),
                "state": str(fixture / "state.json"), "ui_preferences": str(fixture / "preferences.json"),
                "diagnostic_log": str(fixture / "debug.jsonl"), "legacy_state": str(fixture / "legacy.json"),
                "legacy_diagnostic_log": str(fixture / "legacy-debug.jsonl"),
            }
            selected = None
            for config, store, slot in (
                (default_config, default_store, "FIXTURE_DEFAULT_KEY"),
                (selected_config, selected_store, "FIXTURE_SELECTED_KEY"),
            ):
                value = {"providers": {"fixture": {"key_name": slot}},
                         "paths": {**paths, "credential_store": str(store)}}
                config.write_text(json.dumps(value), encoding="utf-8")
                saved = subprocess.run(
                    [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(helper), "-UserConfigPath", str(config), "-SetKeyFromStdin"],
                    input=json.dumps({"key_name": slot, "api_key": "synthetic-fixture-key"}),
                    capture_output=True, text=True, encoding="utf-8", timeout=20,
                )
                self.assertEqual(saved.returncode, 0, "Could not save isolated fixture key")
                if config == selected_config:
                    selected = value
            original_default = default_store.read_bytes()
            overrides = {
                "USER_CONFIG_PATH": selected_config, "USER_CONFIG": selected,
                "KEY_SETUP": helper, "CREDENTIAL_STORE_PATH": selected_store,
                "ACTIVITY_STORE_PATH": fixture / "state.json", "UI_PREFERENCES_PATH": fixture / "preferences.json",
                "DIAGNOSTIC_LOG": fixture / "debug.jsonl", "LEGACY_ACTIVITY_STORE_PATH": fixture / "legacy.json",
                "LEGACY_DIAGNOSTIC_LOG_PATH": fixture / "legacy-debug.jsonl",
                "KEY_NAMES": {"fixture": "FIXTURE_SELECTED_KEY"}, "API_KEYS": {},
                "PERSISTED_STATE": server._default_state(), "STATE_LOAD_ERROR": None,
                "ACTIVITIES": {}, "ACTIVITY_ORDER": [], "CANCEL_EVENTS": {}, "ACTIVITY_SECRET_VALUES": {},
            }
            # Reset rebinds provider maps: restore every affected global after this fixture.
            for name in ("PROVIDERS", "PROVIDER_MODELS", "PROVIDER_LABELS", "PROVIDER_ORDINALS", "PROVIDER_IDS",
                         "ROUTE_SETTINGS", "MODEL", "DELETED_PROVIDERS", "GROUPS",
                         "GROUP_ROUTES", "AUTO_ROUTES", "AUTO_MODES"):
                overrides[name] = getattr(server, name).copy() if isinstance(getattr(server, name), (dict, list, set)) else getattr(server, name)
            with ExitStack() as stack:
                for name, value in overrides.items():
                    stack.enter_context(patch.object(server, name, value))
                stack.enter_context(patch.object(server, "_write_notification"))
                # This intentionally calls the real loader, not the runner's safety mock.
                loaded = LOAD_API_KEYS()
                reset = server._reset_configuration(True)
                self.assertTrue(default_store.exists(), "Reset deleted an unrelated credential store")
                self.assertEqual(default_store.read_bytes(), original_default, "Reset changed an unrelated credential store")
                self.assertFalse(selected_store.exists(), "Reset did not clear the selected credential store")
                self.assertTrue(reset.get("structuredContent", {}).get("reset"))
                self.assertEqual(loaded, {"FIXTURE_SELECTED_KEY": "synthetic-fixture-key"})


if __name__ == "__main__":
    unittest.main()
