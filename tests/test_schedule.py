import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import needs_rclone

from cc_chronicle import ChronicleError, doctor, rclone, schedule


class FakeRunner:
    """Records launchctl/systemctl calls instead of running them."""

    def __init__(self, fail_on=(), stdout="last exit code = 0\n"):
        self.calls = []
        self.fail_on = fail_on
        self.stdout = stdout

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        code = 1 if any(word in argv for word in self.fail_on) else 0
        return subprocess.CompletedProcess(argv, code, stdout=self.stdout, stderr="")


def job(tmp_path):
    return schedule.Job(["/usr/bin/python3", "/opt/cc chronicle/bin/cc-chronicle", "backup"],
                        "/opt/homebrew/bin:/usr/bin:/bin", 12, 30, tmp_path / "scheduled.log")


def test_parse_time():
    assert schedule.parse_time("07:05") == (7, 5)
    for bad in ("7", "24:00", "12:60", "noon"):
        with pytest.raises(ChronicleError):
            schedule.parse_time(bad)


def test_launchd_plist_runs_backup_daily_with_rclone_on_path(tmp_path):
    data = schedule.launchd_plist(job(tmp_path))
    assert schedule.plist_time(data) == (12, 30)
    try:
        import plistlib
        info = plistlib.loads(data)  # the text must be a valid plist
    except ImportError:  # broken pyexpat (the reason the plist is written as text)
        pytest.skip("plistlib unavailable in this Python build")
    assert info["Label"] == schedule.LABEL
    assert info["ProgramArguments"] == job(tmp_path).command
    assert info["StartCalendarInterval"] == {"Hour": 12, "Minute": 30}
    assert "/opt/homebrew/bin" in info["EnvironmentVariables"]["PATH"]
    assert info["StandardOutPath"].endswith("scheduled.log")


def test_plist_escapes_xml_special_characters(tmp_path):
    j = job(tmp_path)
    j.command = ["/usr/bin/python3", "/tmp/a&b<c>/cc-chronicle", "backup"]
    assert b"/tmp/a&amp;b&lt;c&gt;/cc-chronicle" in schedule.launchd_plist(j)


def test_systemd_units_are_persistent_and_quote_paths(tmp_path):
    service, timer = schedule.systemd_units(job(tmp_path))
    assert 'ExecStart=/usr/bin/python3 "/opt/cc chronicle/bin/cc-chronicle" backup' in service
    assert "Environment=PATH=/opt/homebrew/bin" in service
    assert "OnCalendar=*-*-* 12:30:00" in timer and "Persistent=true" in timer


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_install_status_uninstall(tmp_path, platform):
    runner = FakeRunner()
    s = schedule.Scheduler(runner=runner, platform=platform, home=tmp_path)
    assert s.status().startswith("not installed")
    files = s.install(job(tmp_path))
    assert all(f.is_file() for f in files)
    assert str(tmp_path) in str(files[0])
    tools = {call[0] for call in runner.calls}
    assert tools == ({"launchctl"} if platform == "darwin" else {"systemctl"})
    assert s.status().startswith("installed")
    assert s.uninstall() is True
    assert not any(f.exists() for f in files)
    assert s.uninstall() is False


def test_failed_bootstrap_is_reported(tmp_path):
    s = schedule.Scheduler(runner=FakeRunner(fail_on=("bootstrap",)), platform="darwin", home=tmp_path)
    with pytest.raises(ChronicleError, match="bootstrap"):
        s.install(job(tmp_path))


def test_unsupported_platform():
    with pytest.raises(ChronicleError, match="macOS and Linux only"):
        schedule.Scheduler(platform="win32")


@needs_rclone
def test_build_job_includes_rclone_dir_and_config(tmp_path):
    j = schedule.build_job("03:15", tmp_path / "c.ini")
    assert (j.hour, j.minute) == (3, 15)
    assert j.command[-3:] == ["--config", str(tmp_path / "c.ini"), "backup"]
    import shutil
    assert str(Path(shutil.which("rclone")).parent) in j.path_env.split(":")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_installed_command_round_trips(tmp_path, platform):
    s = schedule.Scheduler(runner=FakeRunner(), platform=platform, home=tmp_path)
    assert s.installed_command() == []
    j = job(tmp_path)
    j.command = ["/usr/bin/python3", "/opt/a&b dir/bin/cc-chronicle", "--config", "/c.ini", "backup"]
    s.install(j)
    assert s.installed_command() == j.command


@pytest.mark.parametrize("platform, stdout, code", [
    ("darwin", "\truns = 7\n\tlast exit code = 1\n", 1),
    ("darwin", "\tlast exit code = 0\n", 0),
    ("darwin", "\tlast exit code = (never exited)\n", None),
    ("linux", "ExecMainStatus=2\nExecMainExitTimestamp=Tue 2026-10-20 12:00:05 EET\n", 2),
    ("linux", "ExecMainStatus=0\nExecMainExitTimestamp=\n", None),
])
def test_last_exit_code(tmp_path, platform, stdout, code):
    s = schedule.Scheduler(runner=FakeRunner(stdout=stdout), platform=platform, home=tmp_path)
    assert s.last_exit_code() is None  # not installed
    s.install(job(tmp_path))
    assert s.last_exit_code() == code


class FakeScheduler:
    code = 0

    def installed_command(self):
        return ["/bin/sh", "/bin/sh", "backup"]  # paths that exist

    def status(self):
        return f"installed, loaded: daily at 12:00 (last exit code = {self.code})"

    def last_exit_code(self):
        return self.code


@pytest.mark.parametrize("code, level", [(0, doctor.OK), (1, doctor.WARN)])
def test_doctor_warns_when_the_last_scheduled_run_failed(monkeypatch, code, level):
    monkeypatch.setattr(FakeScheduler, "code", code)
    monkeypatch.setattr(schedule, "Scheduler", FakeScheduler)
    got, _name, detail = doctor._schedule_check()
    assert got == level
    assert ("FAILED" in detail) == (code != 0)


@pytest.mark.parametrize("hours, scheduled, level", [
    (2, True, doctor.OK), (30, True, doctor.WARN), (30, False, doctor.OK)])
def test_doctor_warns_when_the_daily_job_left_a_stale_stamp(monkeypatch, hours, scheduled, level):
    when = datetime.now(timezone.utc) - timedelta(hours=hours)
    monkeypatch.setattr(rclone, "cat", lambda _path: json.dumps({"finished_at": when.isoformat()}))
    cfg = SimpleNamespace(dest="t:archive", machine="mac")
    assert doctor._last_backup_check(cfg, scheduled=scheduled)[0] == level
