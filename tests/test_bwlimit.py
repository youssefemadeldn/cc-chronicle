"""Upload speed cap: --bwlimit on copy/sync, never on read-only rclone calls."""
import subprocess
from pathlib import Path

import pytest
from conftest import needs_rclone, quiet

from cc_chronicle import ChronicleError, doctor, rclone
from cc_chronicle.backup import backup
from cc_chronicle.cli import main
from cc_chronicle.config import DEFAULT_BWLIMIT, Config, load, save, state_dir


def _commands(log):
    return [line for line in log.splitlines() if line.startswith("### rclone ")]


def _latest_dry_log():
    logs = sorted((state_dir() / "logs").glob("*-backup-dry.log"))
    assert logs, "dry-run wrote no log"
    return logs[-1].read_text()


@needs_rclone
def test_dry_run_caps_every_copy_and_sync(env):
    cfg, _claude, _archive = env
    result = backup(cfg, dry_run=True, out=quiet)
    lines = _commands(result.log_file.read_text())
    copy_lines = [line for line in lines if line.startswith("### rclone copy ")]
    sync_lines = [line for line in lines if line.startswith("### rclone sync ")]
    assert copy_lines and sync_lines
    for line in copy_lines + sync_lines:
        assert f"--bwlimit {DEFAULT_BWLIMIT}" in line


@needs_rclone
def test_cli_bwlimit_overrides_config(env):
    cfg, _claude, _archive = env
    cfg.bwlimit = "64k"
    save(cfg)
    assert main(["backup", "--dry-run", "--bwlimit", "32k"]) == 0
    log = _latest_dry_log()
    capped = [line for line in _commands(log)
              if line.startswith("### rclone copy ") or line.startswith("### rclone sync ")]
    assert capped
    assert all("--bwlimit 32k" in line for line in capped)
    assert "64k" not in log
    assert DEFAULT_BWLIMIT not in log


@needs_rclone
def test_off_drops_the_cap(env):
    cfg, _claude, _archive = env
    cfg.bwlimit = "32k"
    result = backup(cfg, dry_run=True, bwlimit="off", out=quiet)
    assert "--bwlimit" not in result.log_file.read_text()

    cfg.bwlimit = "off"
    result = backup(cfg, dry_run=True, out=quiet)
    assert "--bwlimit" not in result.log_file.read_text()

    save(cfg)
    assert main(["backup", "--dry-run", "--bwlimit", "off"]) == 0
    assert "--bwlimit" not in _latest_dry_log()


def test_readonly_calls_carry_no_bwlimit(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(rclone.subprocess, "run", fake_run)
    monkeypatch.setattr(rclone, "binary", lambda: "rclone")
    for verb in ("lsjson", "size", "check"):
        rclone.run([verb, "remote:path"])
    assert [argv[1] for argv in seen] == ["lsjson", "size", "check"]
    for argv in seen:
        assert "--bwlimit" not in argv


def test_transfer_argv_includes_bwlimit_for_copy_and_sync(monkeypatch, tmp_path):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        Path(argv[argv.index("--log-file") + 1]).write_text("")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(rclone.subprocess, "run", fake_run)
    monkeypatch.setattr(rclone, "binary", lambda: "rclone")
    for command in ("copy", "sync"):
        rclone.transfer(command, "src", "dst", log_file=tmp_path / f"{command}.log", bwlimit=DEFAULT_BWLIMIT)
    assert [argv[1] for argv in seen] == ["copy", "sync"]
    for argv in seen:
        assert argv[argv.index("--bwlimit") + 1] == DEFAULT_BWLIMIT

    seen.clear()
    rclone.transfer("copy", "src", "dst", log_file=tmp_path / "off.log", bwlimit="off")
    assert "--bwlimit" not in seen[0]


def test_old_config_gets_the_default_cap(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text("[cc-chronicle]\nremote = t\nroot = archive\nmachine = box\n")
    cfg = load(path)
    assert cfg.bwlimit == DEFAULT_BWLIMIT
    cfg.bwlimit = "32k"
    save(cfg, path)
    assert load(path).bwlimit == "32k"
    with pytest.raises(ChronicleError, match="bwlimit"):
        Config(remote="g", bwlimit="  ").validate()


@needs_rclone
def test_status_and_doctor_print_the_cap(env, capsys):
    cfg, _claude, _archive = env
    save(cfg)
    assert main(["status"]) == 0
    assert f"upload cap: {DEFAULT_BWLIMIT}" in capsys.readouterr().out
    lines = []
    assert doctor.run(out=lines.append) == 0
    cap_line = next(line for line in lines if "upload cap" in line)
    assert DEFAULT_BWLIMIT in cap_line

    cfg.bwlimit = "off"
    save(cfg)
    assert main(["status"]) == 0
    assert "upload cap: off" in capsys.readouterr().out
    lines.clear()
    doctor.run(out=lines.append)
    cap_line = next(line for line in lines if "upload cap" in line)
    assert cap_line.rstrip().endswith("off")
