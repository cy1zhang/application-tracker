import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _fake_launchctl(monkeypatch, codes=None):
    calls, codes = [], codes or {}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=codes.get(cmd[1], 0))

    monkeypatch.setattr(launchagent.subprocess, "run", fake_run)
    monkeypatch.setattr(launchagent.shutil, "which", lambda name: "/opt/bin/uv")
    return calls


def test_install_status_and_uninstall_call_launchctl(tmp_path, monkeypatch):
    calls = _fake_launchctl(monkeypatch, {"print": 113})  # not loaded yet
    plist = tmp_path / "LaunchAgents" / "x.plist"
    backend, log = tmp_path / "backend", tmp_path / "logs" / "w.log"
    backend.mkdir()

    assert launchagent.install(backend, plist_path=plist, uid=501, log_path=log) == 0
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][0] == "/opt/bin/uv"
    assert plistlib.loads(plist.read_bytes())["StandardOutPath"] == str(log)
    assert calls == [
        ["launchctl", "print", "gui/501/com.applicationtracker.worker"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
    ]

    calls.clear()
    assert launchagent.uninstall(plist_path=plist, uid=501) == 0
    assert calls == [["launchctl", "bootout", "gui/501", str(plist)]]
    assert not plist.exists()


def test_reinstall_boots_out_loaded_agent_first(tmp_path, monkeypatch):
    calls = _fake_launchctl(monkeypatch)  # print succeeds: loaded
    plist = tmp_path / "x.plist"
    assert launchagent.install(
        tmp_path, plist_path=plist, uid=7, log_path=tmp_path / "w.log"
    ) == 0
    assert [c[1] for c in calls] == ["print", "bootout", "bootstrap"]


def test_reinstall_keeps_going_if_bootout_says_not_loaded(tmp_path, monkeypatch):
    calls = _fake_launchctl(monkeypatch, {"bootout": 3})
    assert launchagent.install(
        tmp_path, plist_path=tmp_path / "x.plist", uid=7, log_path=tmp_path / "w.log"
    ) == 0
    assert calls[-1][1] == "bootstrap"


def test_reinstall_stops_if_bootout_fails(tmp_path, monkeypatch):
    calls = _fake_launchctl(monkeypatch, {"bootout": 5})
    plist = tmp_path / "x.plist"
    assert launchagent.install(
        tmp_path, plist_path=plist, uid=7, log_path=tmp_path / "w.log"
    ) == 5
    assert [c[1] for c in calls] == ["print", "bootout"] and not plist.exists()


@pytest.mark.parametrize("code", [3, 113])
def test_uninstall_removes_plist_when_not_loaded(tmp_path, monkeypatch, code):
    _fake_launchctl(monkeypatch, {"bootout": code})
    plist = tmp_path / "x.plist"
    plist.write_bytes(b"x")
    assert launchagent.uninstall(plist_path=plist, uid=1) == 0
    assert not plist.exists()


def test_uninstall_keeps_plist_when_bootout_fails(tmp_path, monkeypatch):
    _fake_launchctl(monkeypatch, {"bootout": 5})
    plist = tmp_path / "x.plist"
    plist.write_bytes(b"x")
    assert launchagent.uninstall(plist_path=plist, uid=1) == 5
    assert plist.exists()


def test_status_calls_launchctl_print(monkeypatch):
    calls = _fake_launchctl(monkeypatch)
    assert launchagent.status(uid=501) == 0
    assert calls == [["launchctl", "print", "gui/501/com.applicationtracker.worker"]]


def test_install_fails_without_uv(tmp_path, monkeypatch):
    monkeypatch.setattr(launchagent.shutil, "which", lambda name: None)
    ran = []
    monkeypatch.setattr(launchagent.subprocess, "run", lambda *a, **k: ran.append(a))
    plist = tmp_path / "x.plist"
    assert launchagent.install(tmp_path, plist_path=plist, uid=1) == 1
    assert not plist.exists() and not ran
