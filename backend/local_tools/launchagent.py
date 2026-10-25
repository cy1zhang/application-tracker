import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.applicationtracker.worker"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library/Logs/application-tracker/worker.log"
START_INTERVAL_SECONDS = 1800


def render_plist(
    repo_backend_dir: Path, uv_path: Path, log_path: Path = LOG_PATH
) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [
            str(uv_path), "run", "--group", "local", "python", "-m", "local_tools", "worker",
        ],
        "WorkingDirectory": str(repo_backend_dir),
        "RunAtLoad": True,
        "StartInterval": START_INTERVAL_SECONDS,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    })


def _launchctl(*args: str) -> int:
    return subprocess.run(["launchctl", *args], check=False).returncode


NOT_LOADED_CODES = (3, 113)  # launchctl exit codes for a missing service


def install(
    backend_dir: Path, *, plist_path: Path = PLIST_PATH, uid: int | None = None,
    log_path: Path = LOG_PATH,
) -> int:
    uv = shutil.which("uv")  # launchd has a minimal PATH, so store the absolute path
    if uv is None:
        print("uv not found on PATH", file=sys.stderr)
        return 1
    uid = os.getuid() if uid is None else uid
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600))
    log_path.chmod(0o600)  # launchd opens this file; keep it private
    if status(uid=uid) == 0:  # already loaded: replace it
        code = _launchctl("bootout", f"gui/{uid}", str(plist_path))
        if code not in (0, *NOT_LOADED_CODES):
            return code
    plist_path.write_bytes(
        render_plist(Path(backend_dir).resolve(), Path(uv), log_path)
    )
    return _launchctl("bootstrap", f"gui/{uid}", str(plist_path))


def uninstall(*, plist_path: Path = PLIST_PATH, uid: int | None = None) -> int:
    uid = os.getuid() if uid is None else uid
    code = _launchctl("bootout", f"gui/{uid}", str(plist_path))
    if code in (0, *NOT_LOADED_CODES):
        plist_path.unlink(missing_ok=True)
        return 0
    return code  # still loaded: keep the plist so removal can be retried


def status(*, uid: int | None = None) -> int:
    uid = os.getuid() if uid is None else uid
    return _launchctl("print", f"gui/{uid}/{LABEL}")


def main(argv: list[str]) -> int:
    commands = {"install": lambda: install(Path(__file__).resolve().parents[1]),
                "uninstall": uninstall, "status": status}
    if len(argv) != 2 or argv[1] not in commands:
        print("usage: python -m local_tools.launchagent install|uninstall|status",
              file=sys.stderr)
        return 2
    return commands[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
