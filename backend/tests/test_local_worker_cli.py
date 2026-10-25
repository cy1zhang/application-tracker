import os
import stat
from datetime import UTC, datetime

from local_tools import __main__ as cli
from local_tools.auth import LoginRequired
from local_tools.lock import acquire_lock
from local_tools.worker import RunSummary

NOW = datetime(2026, 10, 25, 3, 0, 0, tzinfo=UTC)


def run(tmp_path, runs, *, until_empty=False, sleeps=None):
    results = iter(runs)

    def fake_run_once(api, ollama, *, limit=None, log=print):
        item = next(results)
        if isinstance(item, Exception):
            raise item
        return item

    return cli.run_worker(
        object(), object(), limit=None, until_empty=until_empty,
        log_path=tmp_path / "logs" / "worker.log",
        lock_path=tmp_path / "lock" / "worker.lock",
        clock=lambda: NOW, sleep=(sleeps.append if sleeps is not None else lambda s: None),
        run=fake_run_once, log=lambda line: None,
    )


def test_second_instance_exits_3(tmp_path):
    held = acquire_lock(tmp_path / "lock" / "worker.lock")
    assert held is not None
    assert acquire_lock(tmp_path / "lock" / "worker.lock") is None
    assert run(tmp_path, [RunSummary()]) == 3


def test_stale_lock_taken_over(tmp_path):
    path = tmp_path / "worker.lock"
    old = acquire_lock(path, clock=lambda: 1000.0)
    assert old is not None
    assert acquire_lock(path, stale_after=7200, clock=lambda: 1000.0 + 100) is None
    taken = acquire_lock(path, stale_after=7200, clock=lambda: 1000.0 + 7201)
    assert taken is not None
    assert str(os.getpid()) in path.read_text()


def test_lock_released_after_run(tmp_path):
    assert run(tmp_path, [RunSummary()]) == 0
    assert acquire_lock(tmp_path / "lock" / "worker.lock") is not None


def test_login_required_exits_4(tmp_path):
    assert run(tmp_path, [RunSummary(stopped="login_required")]) == 4


def test_login_required_mid_run_exits_4(tmp_path):
    assert run(tmp_path, [LoginRequired()]) == 4
    log = (tmp_path / "logs" / "worker.log").read_text()
    assert "login required" in log


def test_exit_code_mapping(tmp_path):
    assert run(tmp_path, [RunSummary(stopped="auto_apply_off")]) == 0
    assert run(tmp_path, [RunSummary(stopped="network")]) == 2
    assert run(tmp_path, [RunSummary(stopped="queue_window")]) == 2


def test_summary_line_appended_to_log(tmp_path):
    run(tmp_path, [RunSummary(saved=2, executed=1, skipped=3, failed=4)])
    run(tmp_path, [RunSummary(stopped="network")])
    log_path = tmp_path / "logs" / "worker.log"
    lines = log_path.read_text().splitlines()
    assert lines[0] == (
        "2026-10-25T03:00:00+00:00 saved=2 executed=1 skipped=3 failed=4 stopped=None"
    )
    assert lines[1].endswith("stopped=network")
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700


def test_until_empty_stops_after_two_idle_runs(tmp_path):
    sleeps = []
    runs = [
        RunSummary(saved=2),
        RunSummary(failed=1),
        RunSummary(failed=1),
        RunSummary(saved=1),  # never reached
    ]
    assert run(tmp_path, runs, until_empty=True, sleeps=sleeps) == 0
    assert len((tmp_path / "logs" / "worker.log").read_text().splitlines()) == 3
    assert sleeps == [30, 30]


def test_until_empty_stops_on_empty_queue_and_global_stop(tmp_path):
    sleeps = []
    assert run(tmp_path, [RunSummary(saved=1), RunSummary()], until_empty=True,
               sleeps=sleeps) == 0
    assert sleeps == [30]
    assert run(tmp_path, [RunSummary(saved=1), RunSummary(stopped="rate_limited")],
               until_empty=True) == 2
