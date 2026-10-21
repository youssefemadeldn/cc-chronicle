import os

from conftest import OCT, ORPHAN, OTHER, PROJECT, SEP, SITE, age, make_claude, write

from cc_chronicle.stage import stage, start_month


def old_claude(tmp_path):
    claude = make_claude(tmp_path)
    age(claude)
    return claude


def test_months_follow_session_start(tmp_path):
    summary = stage(old_claude(tmp_path), tmp_path / "out")
    assert summary.sessions == 3
    assert summary.months == {"2026-09": 1, "2026-10": 2}
    sessions = tmp_path / "out" / "sessions"
    assert (sessions / "2026-09" / PROJECT / f"{SEP}.jsonl").is_symlink()
    assert (sessions / "2026-10" / PROJECT / f"{OCT}.jsonl").is_symlink()
    assert (sessions / "2026-10" / OTHER / f"{SITE}.jsonl").is_symlink()


def test_session_is_bundled_with_sidecar_and_file_history(tmp_path):
    claude = old_claude(tmp_path)
    stage(claude, tmp_path / "out")
    bundle = tmp_path / "out" / "sessions" / "2026-09" / PROJECT / SEP
    assert sorted(p.name for p in bundle.iterdir()) == ["file-history", "subagents", "tool-results"]
    assert os.path.realpath(bundle / "file-history") == os.path.realpath(claude / "file-history" / SEP)


def test_orphan_file_history_is_kept_separately(tmp_path):
    summary = stage(old_claude(tmp_path), tmp_path / "out")
    assert summary.orphan_file_history == 1
    assert (tmp_path / "out" / "file-history" / ORPHAN).is_symlink()


def test_start_month_falls_back_to_file_time(tmp_path):
    path = write(tmp_path / "x.jsonl", '{"type":"bridge-session"}\nnot json\n')
    month = start_month(path)
    assert len(month) == 7 and month[4] == "-"


def test_recent_files_are_copied_so_a_live_session_cannot_change_mid_upload(tmp_path):
    claude = old_claude(tmp_path)
    live = write(claude / "projects" / PROJECT / f"{OCT}.jsonl", (claude / "projects" / PROJECT / f"{OCT}.jsonl").read_text())
    agent = write(claude / "projects" / PROJECT / SEP / "subagents" / "agent-2.jsonl", '{"type":"user"}\n')
    summary = stage(claude, tmp_path / "out")
    assert summary.snapshots == 2
    month = tmp_path / "out" / "sessions" / "2026-10" / PROJECT
    staged = month / f"{OCT}.jsonl"
    assert not staged.is_symlink() and staged.read_text() == live.read_text()
    # the session keeps writing after staging; the staged copy stays as it was
    with open(live, "a") as fh:
        fh.write('{"type":"assistant"}\n')
    assert staged.read_text() != live.read_text()
    # in a folder with one new file, only that file is copied; the rest stay links
    subagents = tmp_path / "out" / "sessions" / "2026-09" / PROJECT / SEP / "subagents"
    assert not subagents.is_symlink()
    assert not (subagents / "agent-2.jsonl").is_symlink() and (subagents / "agent-1.jsonl").is_symlink()
    assert (subagents.parent / "tool-results").is_symlink()
