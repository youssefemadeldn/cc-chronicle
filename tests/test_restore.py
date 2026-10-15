import os
import shutil
import time

import pytest
from conftest import OCT, PROJECT, SEP, SITE, needs_rclone, quiet, write

from cc_chronicle import ChronicleError
from cc_chronicle.backup import backup
from cc_chronicle.restore import Restorer, remote_sessions

pytestmark = needs_rclone

OLD = 1767225600  # 2026-01-01, well past Claude Code's default 30-day cleanup


@pytest.fixture
def archived(env):
    cfg, claude, archive = env
    backup(cfg, out=quiet)
    return cfg, claude, archive


def age(root):
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (OLD, OLD))


def is_fresh(path):
    return path.stat().st_mtime > time.time() - 60


def test_remote_sessions_marks_what_is_local(archived):
    cfg, claude, _archive = archived
    (claude / "projects" / PROJECT / f"{SEP}.jsonl").unlink()
    found = {s.session_id: s for s in remote_sessions(cfg)}
    assert set(found) == {SEP, OCT, SITE}
    assert not found[SEP].local and found[OCT].local
    assert found[SEP].month == "2026-09" and found[SEP].project == PROJECT


def test_restore_session_brings_back_the_whole_bundle(archived):
    cfg, claude, _archive = archived
    proj = claude / "projects" / PROJECT
    (proj / f"{SEP}.jsonl").unlink()
    shutil.rmtree(proj / SEP)
    shutil.rmtree(claude / "file-history" / SEP)
    Restorer(cfg, out=quiet).session(SEP[:8])
    assert (proj / f"{SEP}.jsonl").is_file()
    assert (proj / SEP / "subagents" / "agent-1.jsonl").is_file()
    assert (proj / SEP / "tool-results" / "r1.txt").is_file()
    assert not (proj / SEP / "file-history").exists()
    assert (claude / "file-history" / SEP / "abc123@v1").is_file()


def test_restored_session_is_dated_now_so_cleanup_keeps_it(archived):
    # Claude Code's cleanup deletes transcripts and tool results by modification time.
    cfg, claude, archive = archived
    proj = claude / "projects" / PROJECT
    (proj / f"{SEP}.jsonl").unlink()
    shutil.rmtree(proj / SEP)
    age(archive)
    Restorer(cfg, out=quiet).session(SEP)
    assert is_fresh(proj / f"{SEP}.jsonl")
    assert is_fresh(proj / SEP / "tool-results" / "r1.txt")
    assert is_fresh(proj / SEP / "subagents" / "agent-1.jsonl")


def test_restored_plans_are_dated_now(archived):
    cfg, claude, archive = archived
    (claude / "plans" / "plan-a.md").unlink()
    age(archive)
    Restorer(cfg, out=quiet).plans()
    assert is_fresh(claude / "plans" / "plan-a.md")


def test_restore_never_overwrites_local_files(archived):
    cfg, claude, _archive = archived
    note = write(claude / "projects" / PROJECT / "memory" / "note.md", "edited locally, not yet backed up\n")
    live = write(claude / "projects" / PROJECT / f"{OCT}.jsonl", "newer local content\n")
    os.utime(live, (OLD, OLD))
    restorer = Restorer(cfg, out=quiet)
    restorer.memory()
    restorer.session(OCT)
    assert note.read_text() == "edited locally, not yet backed up\n"
    assert live.read_text() == "newer local content\n"
    assert live.stat().st_mtime == OLD


def test_restore_memory_into_a_fresh_machine(archived):
    cfg, claude, _archive = archived
    shutil.rmtree(claude / "projects" / PROJECT / "memory")
    Restorer(cfg, out=quiet).memory(PROJECT)
    assert (claude / "projects" / PROJECT / "memory" / "MEMORY.md").is_file()
    assert not list(claude.glob("projects/*/*.jsonl.partial"))


def test_restore_project_only_takes_missing_sessions_of_that_month(archived):
    cfg, claude, _archive = archived
    proj = claude / "projects" / PROJECT
    (proj / f"{SEP}.jsonl").unlink()
    (proj / f"{OCT}.jsonl").unlink()
    restored = Restorer(cfg, out=quiet).project("code-app", "2026-10")
    assert [s.session_id for s in restored] == [OCT]
    assert (proj / f"{OCT}.jsonl").is_file() and not (proj / f"{SEP}.jsonl").exists()


def test_restore_history_renames_it_back(archived):
    cfg, claude, _archive = archived
    (claude / "history.jsonl").unlink()
    Restorer(cfg, out=quiet).history()
    assert (claude / "history.jsonl").read_text() == '{"display":"hello"}\n'


def test_dry_run_restores_nothing(archived):
    cfg, claude, _archive = archived
    (claude / "projects" / PROJECT / f"{SEP}.jsonl").unlink()
    Restorer(cfg, dry_run=True, out=quiet).session(SEP)
    assert not (claude / "projects" / PROJECT / f"{SEP}.jsonl").exists()


def test_unknown_or_ambiguous_prefix_is_an_error(archived):
    cfg, _claude, _archive = archived
    with pytest.raises(ChronicleError, match="no archived session"):
        Restorer(cfg, out=quiet).session("zzzz")
    with pytest.raises(ChronicleError, match="matches 3 sessions"):
        Restorer(cfg, out=quiet).session("")


def test_restore_from_an_empty_archive_is_harmless(env):
    cfg, _claude, _archive = env
    assert remote_sessions(cfg) == []
    assert Restorer(cfg, out=quiet).memory() == 0
