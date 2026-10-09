"""Build an explicit public source archive from a Git commit or a review candidate."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release-files.json"
REQUIRED = set("""
.gitignore .gitattributes LICENSE README.md SECURITY.md docs/release.md release-files.json laowu-sidebar.html
CONTRIBUTING.md CHANGELOG.md docs/quickstart.md docs/compatibility.md docs/acceptance.md
.github/workflows/ci.yml .github/ISSUE_TEMPLATE/bug_report.yml
.github/ISSUE_TEMPLATE/feature_request.yml .github/PULL_REQUEST_TEMPLATE.md
tools/build_release.py tools/run_tests.py
tools/laowu_mcp/server.py tools/laowu_mcp/process_runner.py
tools/laowu_mcp/headless_browser_mcp.py tools/laowu_mcp/headless_cdp.js
tools/laowu_mcp/set-provider-keys.ps1 tools/laowu_mcp/user_config.example.json
tools/laowu_mcp/test_server.py tools/laowu_mcp/test_headless_browser_mcp.py
tests/test_config_paths.py tests/test_credential_storage.py tests/test_diagnostic_log.py
tests/test_mcp_stdio.py tests/test_opensource_fixes.py tests/test_process_runner.py
tests/test_release.py tests/test_runner_isolation.py tests/test_result_delivery.py tests/test_sidebar_behavior.py
tests/test_sidebar_credentials.py tests/test_subprocess_environment.py tests/test_task_lifecycle.py
""".split())
PUBLIC_DOTFILES = {
    ".gitignore", ".gitattributes", ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml", ".github/PULL_REQUEST_TEMPLATE.md",
}
SENSITIVE_SUFFIXES = {".pem", ".p12", ".pfx", ".key", ".der", ".p7b", ".p7c", ".p8"}
SENSITIVE_NAME_PARTS = {
    "secret", "secrets", "credential", "credentials", "token", "tokens",
    "password", "passwords", "passwd", "apikey", "privatekey", "id_rsa", "id_ed25519",
}
SENSITIVE_NAME_COMBINATIONS = {frozenset({"private", "key"}), frozenset({"api", "key"})}
SENSITIVE_DATA_NAME_PARTS = {"auth", "authentication", "cookie", "cookies"}
SENSITIVE_DATA_SUFFIXES = {
    "", ".json", ".ini", ".toml", ".yaml", ".yml", ".txt", ".csv", ".xml",
    ".conf", ".cfg", ".bak", ".dat", ".db", ".sqlite",
}


def validate_paths(paths):
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ValueError("Public file manifest must be a non-empty string list")
    if len({path.casefold() for path in paths}) != len(paths):
        raise ValueError("Duplicate public path")
    for value in paths:
        path = PurePosixPath(value)
        name = path.name.lower()
        name_parts = set(re.split(r"[^a-z0-9]+", path.stem.lower()))
        normalized_value = value.casefold()
        sensitive_data_file = path.suffix.lower() in SENSITIVE_DATA_SUFFIXES
        named_secret = sensitive_data_file and (
            bool(name_parts & SENSITIVE_NAME_PARTS)
            or any(parts <= name_parts for parts in SENSITIVE_NAME_COMBINATIONS)
        )
        named_secret = named_secret or bool(name_parts & SENSITIVE_DATA_NAME_PARTS) and path.suffix.lower() in SENSITIVE_DATA_SUFFIXES
        if (path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
                or str(path) != value
                or (any(part.startswith(".") for part in path.parts) and value not in PUBLIC_DOTFILES)
                or normalized_value.startswith(("docs/reviews/", "docs/superpowers/"))
                or name in {"agents.md", "user_config.json", "keys.xml", "ui_preferences.json"}
                or name.startswith(".env")
                or named_secret
                or path.suffix.lower() in SENSITIVE_SUFFIXES | {".log", ".jsonl", ".zip"}):
            raise ValueError(f"Private or invalid public path: {value}")
    if not REQUIRED.issubset(paths):
        raise ValueError("Public manifest is missing required source files")
    return paths


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise ValueError("Git source is missing or invalid; every public file must exist in the selected commit")
    return result.stdout


def _candidate_file_bytes(name):
    file = ROOT / name
    if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(ROOT):
        raise ValueError(f"Public file is missing or outside project: {name}")
    if file.stat().st_nlink > 1:
        raise ValueError(f"Public file must not be a hard link: {name}")
    return file.read_bytes()


def build(output, *, candidate=False, ref=None):
    if bool(candidate) == bool(ref):
        raise ValueError("Choose exactly one source: candidate or Git ref")
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Output must be a new path inside the project")
    if candidate:
        try:
            commit = git("rev-parse", "HEAD").decode("ascii").strip()
        except (OSError, ValueError):
            commit = None
        paths = validate_paths(json.loads((ROOT / MANIFEST).read_text(encoding="utf-8")))
        files = {name: _candidate_file_bytes(name) for name in paths}
    else:
        if not isinstance(ref, str) or ref.startswith("-"):
            raise ValueError("Invalid Git ref")
        commit = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode("ascii").strip()
        paths = validate_paths(json.loads(git("show", f"{commit}:{MANIFEST}")))
        files = {name: git("show", f"{commit}:{name}") for name in paths}
    metadata = {
        "kind": "candidate" if candidate else "release",
        "source_commit": commit,
        "source": "uncommitted working tree" if candidate else "Git commit",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    files["RELEASE-MANIFEST.json"] = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate", action="store_true", help="Uncommitted snapshot for review only")
    source.add_argument("--ref", help="Reviewed Git commit or tag")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = build(args.output, candidate=args.candidate, ref=args.ref)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Build failed: {error}\n")
    print(json.dumps({"kind": metadata["kind"], "files": len(metadata["files"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
