"""Network retries, the local stamp, and the 64 KiB/s default cap.

Each test here fails on 8b32ef9: that commit tries a network error once, never
writes a local stamp, does not record parts_ok, and caps the normal rate at 256k.
"""
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from conftest import needs_rclone, quiet

from cc_chronicle import ChronicleError, doctor, rclone
from cc_chronicle.backup import backup
from cc_chronicle.config import DEFAULT_BWLIMIT, state_dir

NET = "rclone copy failed: dial tcp: lookup www.googleapis.com: no such host"


def _fast(cfg, monkeypatch):
    cfg.network_retries = 3
    cfg.network_retry_waits = (0, 0, 0)
    monkeypatch.setattr(rclone.time, "sleep", lambda _seconds: None)


def _chat(cursor):
    folder = cursor / "chats" / "ws" / "11111111-2222-3333-4444-555555555555"
    folder.mkdir(parents=True)
    created = int(datetime(2026, 9, 15, 12, tzinfo=timezone.utc).timestamp() * 1000)
    (folder / "meta.json").write_text(json.dumps({"createdAtMs": created}))
    db = folder / "store.db"
    conn = sqlite3.connect(db)
    conn.execute("create table t(msg)")
    conn.execute("insert into t values (?)", ("kept",))
    conn.commit()
    conn.close()
    projects = cursor / "projects" / "app"
    projects.mkdir(parents=True)
    (projects / "note.txt").write_text("project file\n")


def test_default_cap_is_64k():
    assert DEFAULT_BWLIMIT == "09:45,16k 10:30,64k 21:45,16k 22:30,64k"
    assert "09:45,16k" in DEFAULT_BWLIMIT and "21:45,16k" in DEFAULT_BWLIMIT
    assert "256k" not in DEFAULT_BWLIMIT


@needs_rclone
def test_network_error_then_success_on_retry(env, monkeypatch):
    cfg, _claude, archive = env
    _fast(cfg, monkeypatch)
    real = rclone._transfer_once
    calls = {"plans": 0}

    def flaky(command, src, dst, *extra, **kwargs):
        if str(dst).endswith("/.claude/plans"):
            calls["plans"] += 1
            if calls["plans"] == 1:
                raise rclone.RcloneError(NET, network=True)
        return real(command, src, dst, *extra, **kwargs)

    monkeypatch.setattr(rclone, "_transfer_once", flaky)
    result = backup(cfg, out=quiet)
    assert calls["plans"] == 2
    assert "plans" not in result.failed
    assert (archive / ".claude" / "plans" / "plan-a.md").is_file()
    log = result.log_file.read_text()
    assert "--retries-sleep 10s" in log
    assert "--low-level-retries 10" in log


@needs_rclone
def test_network_error_on_every_try_fails_only_that_step(env, monkeypatch):
    cfg, _claude, archive = env
    _fast(cfg, monkeypatch)
    real = rclone._transfer_once
    calls = {"sessions": 0, "other": 0}

    def flaky(command, src, dst, *extra, **kwargs):
        if str(dst).endswith("/.claude/sessions"):
            calls["sessions"] += 1
            raise rclone.RcloneError("rclone copy failed: connection reset by peer", network=True)
        calls["other"] += 1
        return real(command, src, dst, *extra, **kwargs)

    monkeypatch.setattr(rclone, "_transfer_once", flaky)
    with pytest.raises(ChronicleError, match="sessions"):
        backup(cfg, out=quiet)
    assert calls["sessions"] == 4
    assert calls["other"] >= 1
    assert not (archive / ".claude" / "sessions").exists()
    assert (archive / ".claude" / "plans" / "plan-a.md").is_file()
    assert (archive / ".claude" / "history.mac.jsonl").is_file()
    assert (archive / "README.md").is_file()


@needs_rclone
def test_stamp_falls_back_to_local_and_doctor_reads_it(env, monkeypatch):
    cfg, _claude, archive = env
    _fast(cfg, monkeypatch)
    real_run = rclone.run
    calls = {"rcat": 0}

    def fake_run(args, **kwargs):
        if args and args[0] == "rcat":
            calls["rcat"] += 1
            raise rclone.RcloneError(
                "rclone rcat failed: lookup www.googleapis.com: no such host",
                network=True,
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr(rclone, "run", fake_run)
    result = backup(cfg, out=quiet)
    assert calls["rcat"] == 4
    assert not (archive / "last-backup.mac.json").exists()
    local = json.loads((state_dir() / "last-backup.mac.json").read_text())
    assert local["run"] == result.run and local["machine"] == "mac"
    assert local["failed_steps"] == []

    archive.mkdir(parents=True, exist_ok=True)
    (archive / "last-backup.mac.json").write_text(json.dumps({
        "finished_at": "2026-11-17T13:02:00+02:00",
        "failed_steps": ["memory"],
        "parts_ok": {"memory": "2026-11-17"},
    }))
    level, _name, detail = doctor._last_backup_check(cfg)
    assert level == doctor.OK
    assert result.run[:10] in detail
    assert "2026-11-17" not in detail
    assert f"memory {result.run[:10]}" in detail


@needs_rclone
def test_doctor_shows_per_part_last_success(env, monkeypatch):
    cfg, _claude, archive = env
    _fast(cfg, monkeypatch)
    _chat(cfg.cursor_dir)
    archive.mkdir(parents=True)
    (archive / "last-backup.mac.json").write_text(json.dumps({
        "finished_at": "2026-11-17T13:02:00+02:00",
        "failed_steps": [],
        "parts_ok": {
            "memory": "2026-11-17",
            "sessions": "2026-11-17",
            "cursor-chats": "2026-11-17",
            "cursor-projects": "2026-11-17",
        },
        "cursor_chats": 1,
    }))
    real = rclone._transfer_once

    def flaky(command, src, dst, *extra, **kwargs):
        if str(dst).endswith("/.cursor/chats"):
            raise rclone.RcloneError("rclone copy failed: corrupted on transfer: md5 hashes differ")
        return real(command, src, dst, *extra, **kwargs)

    monkeypatch.setattr(rclone, "_transfer_once", flaky)
    with pytest.raises(ChronicleError, match="cursor-chats"):
        backup(cfg, out=quiet)
    stamp = json.loads((archive / "last-backup.mac.json").read_text())
    today = stamp["run"][:10]
    assert stamp["parts_ok"]["memory"] == today
    assert stamp["parts_ok"]["sessions"] == today
    assert stamp["parts_ok"]["cursor-projects"] == today
    assert stamp["parts_ok"]["cursor-chats"] == "2026-11-17"
    assert stamp["failed_steps"] == ["cursor-chats"]
    level, _name, detail = doctor._last_backup_check(cfg)
    assert level == doctor.WARN
    assert f"memory {today}" in detail
    assert f"sessions {today}" in detail
    assert "cursor-chats since 2026-11-17" in detail
