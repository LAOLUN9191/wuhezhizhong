import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def tool(self):
        path = ROOT / "tools" / "build_release.py"
        self.assertTrue(path.is_file(), "Release builder is missing")
        spec = importlib.util.spec_from_file_location("release_builder", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_candidate_contains_only_public_files_and_verified_hashes(self):
        tool = self.tool()
        with TemporaryDirectory(prefix=".test-release-", dir=ROOT) as folder:
            target = Path(folder) / "candidate.zip"
            tool.build(target, candidate=True)
            with zipfile.ZipFile(target) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("RELEASE-MANIFEST.json"))
                self.assertEqual(manifest["kind"], "candidate")
                self.assertEqual(set(manifest["files"]), names - {"RELEASE-MANIFEST.json"})
                self.assertIn("tools/run_tests.py", names)
                self.assertIn("LICENSE", names)
                self.assertFalse(any("reviews" in name or name.endswith(("keys.xml", "user_config.json")) or "AGENTS.md" in name for name in names))
                import hashlib
                for name, digest in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)

    def test_formal_release_rejects_incomplete_old_git_ref(self):
        tool = self.tool()
        with TemporaryDirectory(prefix=".test-release-", dir=ROOT) as folder:
            with self.assertRaises(ValueError):
                tool.build(Path(folder) / "formal.zip", ref="861609e")

    def test_manifest_rejects_private_paths_and_traversal(self):
        tool = self.tool()
        for path in ("../outside", "user_config.json", "tools/keys.xml", "docs/reviews/private.md", ".git/config", "AGENTS.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                tool.validate_paths([path])

    def test_manifest_allows_only_named_public_github_files(self):
        tool = self.tool()
        paths = json.loads((ROOT / "release-files.json").read_text(encoding="utf-8"))
        public = {
            ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml", ".github/PULL_REQUEST_TEMPLATE.md",
        }
        self.assertTrue(public.issubset(tool.validate_paths(list(dict.fromkeys([*paths, *public])))))
        for private in (".github/keys.xml", ".github/.env", ".github/workflows/private.yml", ".other/ci.yml"):
            with self.subTest(path=private), self.assertRaises(ValueError):
                tool.validate_paths([*paths, private])

    def test_manifest_rejects_common_secret_and_certificate_files(self):
        tool = self.tool()
        paths = json.loads((ROOT / "release-files.json").read_text(encoding="utf-8"))
        for private_path in (
            "secrets.txt", "provider_credentials.ini", "test_secret.txt", ".env.production",
            "certificates/signing-key.pem", "certificates/client.pfx", "password.txt",
            "private_key.txt", "privateKey.txt", "api_key.json", "apiKey.json",
            "auth.json", "provider-auth.json",
            "cookies.txt", "browser_cookies.txt", "auth.xml", "authentication.xml",
            "cookie.xml", "browser_cookies.xml", "auth_config.xml", "password.xml",
            "private_key.xml", "api_key.xml",
        ):
            with self.subTest(path=private_path), self.assertRaises(ValueError):
                tool.validate_paths([*paths, private_path])

    def test_manifest_blocks_private_paths_case_insensitively(self):
        tool = self.tool()
        paths = json.loads((ROOT / "release-files.json").read_text(encoding="utf-8"))
        for private_path in (
            "agents.md", "User_Config.json", "Docs/Reviews/private.md",
            "DOCS/Superpowers/private.md",
        ):
            with self.subTest(path=private_path), self.assertRaises(ValueError):
                tool.validate_paths([*paths, private_path])

    def test_manifest_rejects_paths_that_collide_on_windows(self):
        tool = self.tool()
        with self.assertRaisesRegex(ValueError, "Duplicate public path"):
            tool.validate_paths([*json.loads((ROOT / "release-files.json").read_text(encoding="utf-8")), "readme.md"])

    def test_manifest_allows_non_sensitive_public_xml(self):
        tool = self.tool()
        paths = json.loads((ROOT / "release-files.json").read_text(encoding="utf-8"))
        public_paths = [*paths, "docs/schema.xml", "docs/secrets.md", "tools/auth.py"]
        self.assertTrue({"docs/schema.xml", "docs/secrets.md", "tools/auth.py"}.issubset(
            tool.validate_paths(public_paths)
        ))

    def test_manifest_requires_the_public_runtime_and_test_suites(self):
        tool = self.tool()
        paths = json.loads((ROOT / "release-files.json").read_text(encoding="utf-8"))
        paths.remove("tests/test_task_lifecycle.py")
        with self.assertRaisesRegex(ValueError, "missing required source files"):
            tool.validate_paths(paths)

    def test_test_runner_fails_when_pattern_discovers_no_tests(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/run_tests.py"), "--suite", "tests",
             "--pattern", "no_such_tests_*.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        output = result.stdout + result.stderr
        self.assertIn('"tests": 0', result.stdout, output)
        self.assertNotEqual(result.returncode, 0, output)

    def test_candidate_can_be_built_from_extracted_source_without_git(self):
        tool = self.tool()
        from unittest.mock import patch
        with TemporaryDirectory(prefix=".test-release-", dir=ROOT) as folder, patch.object(tool, "git", side_effect=ValueError("No Git checkout")):
            metadata = tool.build(Path(folder) / "candidate.zip", candidate=True)
            self.assertIsNone(metadata["source_commit"])

    def test_candidate_rejects_hard_linked_source_files(self):
        tool = self.tool()
        with TemporaryDirectory(prefix=".test-release-hardlink-", dir=ROOT) as folder:
            private_file = Path(folder) / "private-data.txt"
            public_path = Path(folder) / "public.txt"
            private_file.write_text("synthetic fixture", encoding="utf-8")
            try:
                os.link(private_file, public_path)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Hard links are unavailable on this filesystem: {type(error).__name__}")
            with patch.object(tool, "ROOT", Path(folder)), self.assertRaisesRegex(ValueError, "hard link"):
                tool._candidate_file_bytes("public.txt")


if __name__ == "__main__":
    unittest.main()
