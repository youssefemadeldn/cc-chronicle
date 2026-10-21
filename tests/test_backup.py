import json

import pytest

from conftest import OCT, ORPHAN, PROJECT, SEP, needs_rclone, quiet, write

from cc_chronicle import ChronicleError, doctor, rclone
from cc_chronicle.backup import backup

pytestmark = needs_rclone


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_backup_writes_the_archive_layout(env):
    cfg, _claude, archive = env
    backup(cfg, out=quiet)
    c = archive / ".claude"
    assert (c / "projects" / PROJECT / "memory" / "note.md").is_file()
    assert (c / "sessions" / "2026-09" / PROJECT / f"{SEP}.jsonl").is_file()
    assert (c / "sessions" / "2026-09" / PROJECT / SEP / "subagents" / "agent-1.jsonl").is_file()
    assert (c / "sessions" / "2026-09" / PROJECT / SEP / "tool-results" / "r1.txt").is_file()
    assert (c / "sessions" / "2026-09" / PROJECT / SEP / "file-history" / "abc123@v1").is_file()
    assert (c / "sessions" / "2026-10" / PROJECT / f"{OCT}.jsonl").is_file()
    assert (c / "file-history" / ORPHAN / "def456@v1").is_file()
    assert (c / "plans" / "plan-a.md").is_file()
    assert (c / "history.mac.jsonl").is_file()
    assert (archive / "README.md").is_file()
    assert not any(".DS_Store" in f for f in files(archive))
    # only memory goes under projects/: sessions never land there
    assert files(c / "projects") == [f"{PROJECT}/memory/MEMORY.md", f"{PROJECT}/memory/note.md"]


def test_stamp_records_the_run(env):
    cfg, _claude, archive = env
    result = backup(cfg, out=quiet)
    stamp = json.loads((archive / "last-backup.mac.json").read_text())
    assert stamp["machine"] == "mac" and stamp["run"] == result.run
    assert stamp["sessions"] == 3 and stamp["sessions_by_month"] == {"2026-09": 1, "2026-10": 2}


def test_dry_run_writes_nothing(env):
    cfg, _claude, archive = env
    result = backup(cfg, dry_run=True, out=quiet)
    assert not archive.exists() or files(archive) == []
    assert result.actions["sessions"] > 0


def test_second_run_uploads_nothing_new(env):
    cfg, _claude, _archive = env
    backup(cfg, out=quiet)
    again = backup(cfg, out=quiet)
    assert all(n == 0 for n in again.actions.values()), again.actions


def test_deleted_memory_note_moves_to_deleted(env):
    cfg, claude, archive = env
    backup(cfg, out=quiet)
    (claude / "projects" / PROJECT / "memory" / "note.md").unlink()
    result = backup(cfg, out=quiet)
    assert not (archive / ".claude" / "projects" / PROJECT / "memory" / "note.md").exists()
    kept = archive / "_deleted" / result.run / ".claude" / "projects" / PROJECT / "memory" / "note.md"
    assert kept.read_text().endswith("A fact.\n")


def test_changed_memory_note_keeps_old_version(env):
    cfg, claude, archive = env
    backup(cfg, out=quiet)
    write(claude / "projects" / PROJECT / "memory" / "note.md", "---\nname: note\n---\nA corrected fact, longer.\n")
    result = backup(cfg, out=quiet)
    assert "corrected" in (archive / ".claude" / "projects" / PROJECT / "memory" / "note.md").read_text()
    old = archive / "_deleted" / result.run / ".claude" / "projects" / PROJECT / "memory" / "note.md"
    assert old.read_text().endswith("A fact.\n")


def test_other_machines_memory_is_untouched(env):
    cfg, _claude, archive = env
    theirs = write(archive / ".claude" / "projects" / "D--work-app" / "memory" / "x.md", "windows note\n")
    backup(cfg, out=quiet)
    assert theirs.read_text() == "windows note\n"
    assert not (archive / "_deleted").exists()


def test_sessions_stay_archived_after_local_cleanup(env):
    cfg, claude, archive = env
    backup(cfg, out=quiet)
    (claude / "projects" / PROJECT / f"{SEP}.jsonl").unlink()
    backup(cfg, out=quiet)
    assert (archive / ".claude" / "sessions" / "2026-09" / PROJECT / f"{SEP}.jsonl").is_file()


def test_a_failed_step_does_not_stop_the_rest(env, monkeypatch):
    cfg, _claude, archive = env
    real = rclone.transfer

    def flaky(command, src, dst, *extra, **kwargs):
        if dst.endswith("/.claude/sessions"):
            raise rclone.RcloneError("rclone copy failed: corrupted on transfer: md5 hashes differ")
        return real(command, src, dst, *extra, **kwargs)

    monkeypatch.setattr(rclone, "transfer", flaky)
    with pytest.raises(ChronicleError, match="sessions"):
        backup(cfg, out=quiet)
    c = archive / ".claude"
    assert not (c / "sessions").exists()
    assert (c / "file-history" / ORPHAN / "def456@v1").is_file()
    assert (c / "plans" / "plan-a.md").is_file()
    assert (c / "history.mac.jsonl").is_file()
    assert (archive / "README.md").is_file()
    stamp = json.loads((archive / "last-backup.mac.json").read_text())
    assert stamp["failed_steps"] == ["sessions"]
    level, _name, detail = doctor._last_backup_check(cfg)
    assert level == doctor.WARN and "sessions" in detail
