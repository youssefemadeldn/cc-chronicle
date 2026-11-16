"""Cursor chats and project files: stage, scan, backup, restore."""
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from conftest import age, needs_rclone, quiet, write

from cc_chronicle import ChronicleError, doctor, rclone, secrets
from cc_chronicle import cursor as cursor_mod
from cc_chronicle.backup import backup
from cc_chronicle.cursor import stage
from cc_chronicle.restore import Restorer

BAD = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

WS = "ws-hash"
SEP = "11111111-2222-3333-4444-555555555555"  # created 2026-09-15
OCT = "99999999-8888-7777-6666-555555555555"  # no creation time; files say 2026-10
SEP_MS = int(datetime(2026, 9, 15, 12, tzinfo=timezone.utc).timestamp() * 1000)
NOV_MS = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
OCT_TIME = datetime(2026, 10, 20, 12).timestamp()

# Assembled at runtime so this file never contains a key-shaped string.
TOKEN = "gh" + "p_" + "C" * 36
OTHER = "sk-" + "proj-" + "B2" * 20


def make_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("create table t(msg)")
    for row in rows:
        conn.execute("insert into t values (?)", (row,))
    conn.commit()
    conn.close()
    return path


def chat_dir(cursor, name):
    return cursor / "chats" / WS / name


def stamp_old(path):
    for item in path.rglob("*"):
        if item.is_file():
            os.utime(item, (OCT_TIME, OCT_TIME))


def test_chats_are_filed_by_start_month_and_old_ones_stay_links(tmp_path):
    cursor = tmp_path / "cursor"
    september = chat_dir(cursor, SEP)
    write(september / "meta.json", json.dumps({"createdAtMs": SEP_MS, "updatedAtMs": NOV_MS}))
    make_db(september / "store.db", ["kept"])
    age(september, days=40)

    october = chat_dir(cursor, OCT)
    write(october / "meta.json", json.dumps({"updatedAtMs": NOV_MS, "title": "no creation time"}))
    make_db(october / "store.db", ["october"])
    stamp_old(october)

    write(cursor / "projects" / "app" / "note.txt", "project file\n")
    age(cursor / "projects", days=40)

    summary = stage(cursor, tmp_path / "out")
    assert summary.chats == 2
    assert summary.months == {"2026-09": 1, "2026-10": 1}
    assert summary.snapshots == 0
    staged = tmp_path / "out" / "chats" / "2026-09" / WS / SEP / "store.db"
    assert staged.is_symlink()
    assert (tmp_path / "out" / "chats" / "2026-10" / WS / OCT / "meta.json").is_symlink()
    assert (tmp_path / "out" / "projects" / "app").is_symlink()
    assert summary.project_files == 1


def test_wal_chat_is_snapshotted_and_stays_fixed(tmp_path):
    cursor = tmp_path / "cursor"
    folder = chat_dir(cursor, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS}))
    db = folder / "store.db"
    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("create table t(msg)")
    live.execute("insert into t values ('before')")
    live.commit()
    assert (folder / "store.db-wal").is_file()

    summary = stage(cursor, tmp_path / "out")
    staged = tmp_path / "out" / "chats" / "2026-09" / WS / SEP / "store.db"
    assert summary.snapshots == 1
    assert not staged.is_symlink()
    assert not (staged.parent / "store.db-wal").exists()
    assert sqlite3.connect(staged).execute("select msg from t").fetchall() == [("before",)]

    live.execute("insert into t values ('after')")
    live.commit()
    assert sqlite3.connect(staged).execute("select msg from t").fetchall() == [("before",)]
    live.close()


def test_readonly_failure_snapshots_from_a_private_copy(tmp_path, monkeypatch):
    cursor = tmp_path / "cursor"
    folder = chat_dir(cursor, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS}))
    make_db(folder / "store.db", ["before"])
    real = sqlite3.connect

    def connect(path, *args, **kwargs):
        if kwargs.get("uri") and "mode=ro" in str(path):
            raise sqlite3.OperationalError("unable to open database file")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(cursor_mod.sqlite3, "connect", connect)
    summary = stage(cursor, tmp_path / "out")
    staged = tmp_path / "out" / "chats" / "2026-09" / WS / SEP / "store.db"
    assert summary.snapshots == 1 and summary.skipped == []
    assert not staged.is_symlink()
    assert sqlite3.connect(staged).execute("select msg from t").fetchall() == [("before",)]
    assert not (folder / "store.db-wal").exists()
    assert not (folder / "store.db-shm").exists()


def test_secret_in_text_and_in_sqlite(tmp_path):
    folder = chat_dir(tmp_path, SEP)
    meta = write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS, "title": TOKEN}))
    db = make_db(folder / "store.db", [TOKEN])
    assert secrets.scan_file(meta) == {"github-token": 1}
    assert secrets.scan_file(db) == {"github-token": 1}
    junk = write(tmp_path / "store.db", "not a database " + TOKEN)
    assert secrets.scan_file(junk) == {"github-token": 1}


@needs_rclone
def test_abort_blocks_a_new_cursor_secret_only(env):
    cfg, _claude, archive = env
    folder = chat_dir(cfg.cursor_dir, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS, "title": TOKEN}))
    make_db(folder / "store.db", ["clean"])
    cfg.secrets_on_found = "abort"
    with pytest.raises(ChronicleError, match="nothing was uploaded"):
        backup(cfg, out=quiet)
    assert not archive.exists()

    backup(cfg, out=quiet)  # the finding is cached, so this run is allowed
    assert (archive / ".cursor" / "chats" / "2026-09" / WS / SEP / "meta.json").is_file()

    conn = sqlite3.connect(folder / "store.db")
    conn.execute("insert into t values (?)", (OTHER,))
    conn.commit()
    conn.close()
    with pytest.raises(ChronicleError, match="nothing was uploaded"):
        backup(cfg, out=quiet)


@needs_rclone
def test_no_cursor_dir_changes_nothing(env):
    cfg, _claude, archive = env
    result = backup(cfg, out=quiet)
    assert result.cursor_state == "absent"
    assert "cursor-chats" not in result.actions
    assert not (archive / ".cursor").exists()
    stamp = json.loads((archive / "last-backup.mac.json").read_text())
    assert stamp["cursor"] == "absent" and stamp["cursor_chats"] == 0


@needs_rclone
def test_cursor_off_changes_nothing(env):
    cfg, _claude, archive = env
    folder = chat_dir(cfg.cursor_dir, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS, "title": TOKEN}))
    make_db(folder / "store.db", ["x"])
    write(cfg.cursor_dir / "projects" / "app" / "note.txt", "project file\n")
    cfg.cursor = "off"
    result = backup(cfg, out=quiet)
    assert result.cursor_state == "off"
    assert result.secrets.total == 0
    assert "cursor-chats" not in result.actions
    assert not (archive / ".cursor").exists()


@needs_rclone
def test_backup_lays_out_chats_and_projects(env):
    cfg, _claude, archive = env
    folder = chat_dir(cfg.cursor_dir, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS}))
    make_db(folder / "store.db", ["kept"])
    age(folder, days=40)
    write(cfg.cursor_dir / "projects" / "app" / "note.txt", "project file\n")
    age(cfg.cursor_dir / "projects", days=40)
    result = backup(cfg, out=quiet)
    assert result.cursor.chats == 1 and result.cursor.snapshots == 0
    db = archive / ".cursor" / "chats" / "2026-09" / WS / SEP / "store.db"
    assert sqlite3.connect(db).execute("select msg from t").fetchall() == [("kept",)]
    assert (archive / ".cursor" / "projects" / "app" / "note.txt").read_text() == "project file\n"
    assert not (archive / ".cursor" / "chats" / "2026-09" / WS / SEP / "store.db-wal").exists()


@needs_rclone
def test_restore_one_chat_lands_in_its_folder_and_does_not_overwrite(env):
    cfg, _claude, archive = env
    folder = chat_dir(cfg.cursor_dir, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS, "title": "September"}))
    make_db(folder / "store.db", ["kept"])
    backup(cfg, out=quiet)
    shutil.rmtree(folder)
    age(archive)
    Restorer(cfg, out=quiet).cursor_chat(SEP[:8])
    assert folder.parent.name == WS
    assert json.loads((folder / "meta.json").read_text())["title"] == "September"
    assert sqlite3.connect(folder / "store.db").execute("select msg from t").fetchall() == [("kept",)]
    assert (folder / "meta.json").stat().st_mtime > time.time() - 60

    write(folder / "meta.json", "edited locally\n")
    os.utime(folder / "meta.json", (OCT_TIME, OCT_TIME))
    Restorer(cfg, out=quiet).cursor_chat(SEP)
    assert (folder / "meta.json").read_text() == "edited locally\n"
    assert (folder / "meta.json").stat().st_mtime == OCT_TIME


@needs_rclone
def test_a_chat_that_fails_both_ways_is_skipped_without_failing_the_step(env, monkeypatch):
    cfg, _claude, archive = env
    for name, row in ((BAD, "bad"), (SEP, "kept")):
        folder = chat_dir(cfg.cursor_dir, name)
        write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS}))
        make_db(folder / "store.db", [row])
    real_connect = sqlite3.connect
    real_copy = shutil.copy2

    def connect(path, *args, **kwargs):
        if kwargs.get("uri") and BAD in str(path):
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(path, *args, **kwargs)

    def copy2(src, dst, *args, **kwargs):
        if BAD in str(src):
            raise OSError("mid-write")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(cursor_mod.sqlite3, "connect", connect)
    monkeypatch.setattr(cursor_mod.shutil, "copy2", copy2)
    lines = []
    result = backup(cfg, out=lines.append)
    text = "\n".join(lines)
    assert result.failed == {}
    assert "1 skipped (live)" in text
    assert "FAILED" not in text
    good = archive / ".cursor" / "chats" / "2026-09" / WS / SEP / "store.db"
    assert sqlite3.connect(good).execute("select msg from t").fetchall() == [("kept",)]
    assert not (archive / ".cursor" / "chats" / "2026-09" / WS / BAD).exists()
    stamp = json.loads((archive / "last-backup.mac.json").read_text())
    assert stamp["failed_steps"] == [] and stamp["cursor_skipped"] == 1
    assert f"cursor skipped (live): {cfg.cursor_dir}/chats/{WS}/{BAD}/store.db" in result.log_file.read_text()
    level, _name, detail = doctor._cursor_check(cfg)
    assert level == doctor.OK and "1 skipped (live)" in detail


@needs_rclone
def test_a_failed_cursor_step_does_not_stop_the_rest(env, monkeypatch):
    cfg, _claude, archive = env
    folder = chat_dir(cfg.cursor_dir, SEP)
    write(folder / "meta.json", json.dumps({"createdAtMs": SEP_MS}))
    make_db(folder / "store.db", ["kept"])
    write(cfg.cursor_dir / "projects" / "app" / "note.txt", "project file\n")
    real = rclone.transfer

    def flaky(command, src, dst, *extra, **kwargs):
        if dst.endswith("/.cursor/chats"):
            raise rclone.RcloneError("rclone copy failed: corrupted on transfer")
        return real(command, src, dst, *extra, **kwargs)

    monkeypatch.setattr(rclone, "transfer", flaky)
    with pytest.raises(ChronicleError, match="cursor-chats"):
        backup(cfg, out=quiet)
    assert not (archive / ".cursor" / "chats").exists()
    assert (archive / ".cursor" / "projects" / "app" / "note.txt").read_text() == "project file\n"
    assert (archive / ".claude" / "plans" / "plan-a.md").is_file()
    stamp = json.loads((archive / "last-backup.mac.json").read_text())
    assert stamp["failed_steps"] == ["cursor-chats"]
    level, _name, detail = doctor._cursor_check(cfg)
    assert level == doctor.WARN and "cursor-chats" in detail
