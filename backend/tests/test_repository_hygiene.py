import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")

# Credential-shaped strings. PRD acceptance criterion 12 is checked by scanning
# every tracked file for these; do not weaken a pattern to make a fixture pass.
SECRET_PATTERNS = {
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "google_access_token": r"ya29\.[A-Za-z0-9_-]{20,}",
    "google_refresh_token": r"1//[A-Za-z0-9_-]{30,}",
    "google_api_key": r"AIza[A-Za-z0-9_-]{30,}",
    "openai_key": r"sk-[A-Za-z0-9]{32,}",
    "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "secret_assignment": (
        r"""(?i)(client_secret|api_key)["']?\s*[:=]\s*["'][A-Za-z0-9_\-/+]{20,}["']"""
    ),
}
COMPILED = {name: re.compile(pattern) for name, pattern in SECRET_PATTERNS.items()}

# Files that legitimately hold the patterns themselves.
SCAN_EXCLUDED = {"backend/tests/test_repository_hygiene.py"}
SCAN_EXCLUDED_NAMES = {"package-lock.json"}
SCAN_EXCLUDED_SUFFIXES = (".lock",)

FORBIDDEN_FILE = re.compile(r"(^|/)(\.env|[^/]*\.pem|[^/]*\.key|token[^/]*\.json)$")
ALLOWED_FILE = {".env.example"}

# Narrow allowlist of synthetic fixtures: "path" -> pattern names permitted there.
ALLOWLIST: dict[str, set[str]] = {
    # Line 130 posts the literal "another-synthetic-key-WXYZ" as a fake api_key.
    "backend/tests/test_settings_api.py": {"secret_assignment"},
}


def tracked_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    return [name for name in output.split("\0") if name]


def scan(root: Path, allowlist=None) -> list[str]:
    allowlist = ALLOWLIST if allowlist is None else allowlist
    findings = []
    for name in tracked_files(root):
        path = Path(name)
        if (
            name in SCAN_EXCLUDED or path.name in SCAN_EXCLUDED_NAMES
            or name.endswith(SCAN_EXCLUDED_SUFFIXES)
        ):
            continue
        if FORBIDDEN_FILE.search(name) and path.name not in ALLOWED_FILE:
            findings.append(f"{name}: forbidden file")
            continue
        try:
            text = (root / name).read_text(errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        for label, pattern in COMPILED.items():
            if pattern.search(text) and label not in allowlist.get(name, set()):
                findings.append(f"{name}: {label}")
    return findings


def test_tracked_files_contain_no_credential_shaped_strings() -> None:
    assert scan(REPO) == []


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.test", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True,
    )


def seeded_repo(tmp_path: Path, filename: str, content: str) -> Path:
    git(tmp_path, "init", "-q")
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    git(tmp_path, "add", "-A")
    return tmp_path


FAKE_HITS = {
    "aws_access_key": "AKIA" + "A" * 16,
    "private_key": "-----BEGIN RSA PRIVATE KEY-----",
    "google_access_token": "ya29." + "a" * 24,
    "google_refresh_token": "1//" + "a" * 34,
    "google_api_key": "AIza" + "a" * 34,
    "openai_key": "sk-" + "a" * 36,
    "anthropic_key": "sk-ant-" + "a" * 24,
    "secret_assignment": 'client_secret = "' + "a" * 24 + '"',
}


@pytest.mark.parametrize("label", sorted(FAKE_HITS))
def test_scanner_fires_for_each_pattern(tmp_path, label) -> None:
    root = seeded_repo(tmp_path, "src/config.txt", f"value: {FAKE_HITS[label]}\n")

    assert f"src/config.txt: {label}" in scan(root, allowlist={})


def test_pattern_table_and_fixtures_stay_in_step() -> None:
    assert set(FAKE_HITS) == set(SECRET_PATTERNS)


def test_scanner_ignores_short_or_ordinary_text(tmp_path) -> None:
    root = seeded_repo(
        tmp_path, "notes.txt", 'api_key = "short"\nsk-learn\nclient_secret: ${SECRET}\n'
    )

    assert scan(root, allowlist={}) == []


@pytest.mark.parametrize("filename", [
    ".env", "deploy/.env", "server.pem", "keys/id.key", "token.json", "token-gmail.json",
])
def test_scanner_rejects_secret_files(tmp_path, filename) -> None:
    root = seeded_repo(tmp_path, filename, "harmless\n")

    assert f"{filename}: forbidden file" in scan(root, allowlist={})


def test_env_example_is_allowed(tmp_path) -> None:
    root = seeded_repo(tmp_path, ".env.example", "SOME_SETTING=placeholder\n")

    assert scan(root, allowlist={}) == []


def test_allowlist_is_per_file_and_per_pattern(tmp_path) -> None:
    root = seeded_repo(tmp_path, "t.txt", FAKE_HITS["openai_key"] + "\n")

    assert scan(root, allowlist={"t.txt": {"openai_key"}}) == []
    assert scan(root, allowlist={"t.txt": {"aws_access_key"}}) == ["t.txt: openai_key"]


def test_lock_files_are_excluded(tmp_path) -> None:
    root = seeded_repo(tmp_path, "uv.lock", FAKE_HITS["openai_key"] + "\n")

    assert scan(root, allowlist={}) == []
