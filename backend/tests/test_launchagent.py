import plistlib
from pathlib import Path
from types import SimpleNamespace

from local_tools import launchagent


def test_plist_is_valid_and_has_interval_and_runatload(tmp_path):
    backend = tmp_path / "backend"
    data = plistlib.loads(launchagent.render_plist(backend, Path("/opt/bin/uv")))
    assert data["Label"] == "com.applicationtracker.worker"
    assert data["ProgramArguments"] == [
        "/opt/bin/uv", "run", "--group", "local", "python", "-m", "local_tools", "worker",
    ]
    assert data["WorkingDirectory"] == str(backend)
    assert data["RunAtLoad"] is True
    assert data["StartInterval"] == 1800
    assert data["StandardOutPath"] == data["StandardErrorPath"]
    assert data["StandardOutPath"].endswith("application-tracker/worker.log")


def test_install_and_uninstall_call_launchctl(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launchagent.subprocess, "run", fake_run)
    monkeypatch.setattr(launchagent.shutil, "which", lambda name: "/opt/bin/uv")
    plist = tmp_path / "LaunchAgents" / "x.plist"
    backend = tmp_path / "backend"
    backend.mkdir()

    assert launchagent.install(backend, plist_path=plist, uid=501) == 0
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][0] == "/opt/bin/uv"
    assert calls == [["launchctl", "bootstrap", "gui/501", str(plist)]]

    calls.clear()
    assert launchagent.status(uid=501) == 0
    assert calls == [["launchctl", "print", "gui/501/com.applicationtracker.worker"]]

    calls.clear()
    assert launchagent.uninstall(plist_path=plist, uid=501) == 0
    assert calls == [["launchctl", "bootout", "gui/501", str(plist)]]
    assert not plist.exists()


def test_install_fails_without_uv(tmp_path, monkeypatch):
    monkeypatch.setattr(launchagent.shutil, "which", lambda name: None)
    ran = []
    monkeypatch.setattr(launchagent.subprocess, "run", lambda *a, **k: ran.append(a))
    plist = tmp_path / "x.plist"
    assert launchagent.install(tmp_path, plist_path=plist, uid=1) == 1
    assert not plist.exists() and not ran
