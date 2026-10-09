"""Run offline tests with configuration and generated files isolated inside the repository."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITES = {"mcp": PROJECT_ROOT / "tools" / "laowu_mcp", "tests": PROJECT_ROOT / "tests"}
TEST_ENVIRONMENT_KEYS = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC"}


def _safe_test_environment(source):
    return {name: value for name, value in source.items() if name.upper() in TEST_ENVIRONMENT_KEYS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    if args.suite is None:
        # Each suite gets fresh server globals and executors, including after serve() tests.
        results = []
        for name in SUITES:
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "--suite", name, "--pattern", args.pattern]
            if args.verbose:
                command.append("--verbose")
            results.append(subprocess.run(
                command, cwd=PROJECT_ROOT, check=False, env=_safe_test_environment(os.environ),
            ).returncode)
        return 0 if all(code == 0 for code in results) else 1

    try:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix=".test-offline-", dir=PROJECT_ROOT, ignore_cleanup_errors=True
        )
    except OSError as error:
        print(
            f"Test setup failed: {error}; check project root writability/ACL or execution sandbox.",
            file=sys.stderr,
        )
        return 1

    with temporary_directory as folder:
        fixture = Path(folder)
        previous_tempdir = tempfile.tempdir
        paths = {
            "workspace": str(PROJECT_ROOT), "ui": str(PROJECT_ROOT / "laowu-sidebar.html"),
            "state": str(fixture / "state.json"), "diagnostic_log": str(fixture / "dispatch.jsonl"),
            "ui_preferences": str(fixture / "preferences.json"), "credential_store": str(fixture / "keys.xml"),
            "legacy_state": str(fixture / "legacy-state.json"),
            "legacy_diagnostic_log": str(fixture / "legacy-dispatch.jsonl"),
        }
        config = fixture / "user_config.json"
        try:
            config.write_text(json.dumps({"paths": paths}), encoding="utf-8")
        except OSError as error:
            print(
                f"Test setup failed: {error}; check project root writability/ACL or execution sandbox.",
                file=sys.stderr,
            )
            return 1
        environment = {
            **_safe_test_environment(os.environ),
            "LAOWU_USER_CONFIG_PATH": str(config), "CODEX_HOME": str(fixture / "codex-home"),
            "TEMP": str(fixture), "TMP": str(fixture), "TMPDIR": str(fixture),
        }
        try:
            tempfile.tempdir = str(fixture)
            with patch.dict(os.environ, environment, clear=True):
                sys.path.insert(0, str(SUITES["mcp"]))
                import server

                suite = unittest.TestLoader().discover(str(SUITES[args.suite]), pattern=args.pattern)
                with patch.object(server, "_load_api_keys", return_value={}):
                    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
                print(json.dumps({
                    "suite": args.suite, "tests": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "skipped": len(result.skipped),
                }), flush=True)
                return 0 if result.wasSuccessful() and result.testsRun else 1
        finally:
            tempfile.tempdir = previous_tempdir


if __name__ == "__main__":
    raise SystemExit(main())
