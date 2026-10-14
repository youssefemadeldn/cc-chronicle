import shutil

import pytest
from conftest import OCT, PROJECT, SEP, SITE, needs_rclone, quiet, write

from cc_chronicle import ChronicleError
from cc_chronicle.backup import backup
from cc_chronicle.restore import Restorer, remote_sessions

pytestmark = needs_rclone


@pytest.fixture
def archived(env):
    cfg, claude, archive = env
    backup(cfg, out=quiet)
    return cfg, claude, archive


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


def test_restore_never_overwrites_local_files(archived):
    cfg, claude, _archive = archived
    note = write(claude / "projects" / PROJECT / "memory" / "note.md", "edited locally, not yet backed up\n")
    live = write(claude / "projects" / PROJECT / f"{OCT}.jsonl", "newer local content\n")
    restorer = Restorer(cfg, out=quiet)
    restorer.memory()
    restorer.session(OCT)
    assert note.read_text() == "edited locally, not yet backed up\n"
    assert live.read_text() == "newer local content\n"


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
