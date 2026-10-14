import os

from conftest import OCT, ORPHAN, OTHER, PROJECT, SEP, SITE, make_claude, write

from cc_chronicle.stage import stage, start_month


def test_months_follow_session_start(tmp_path):
    summary = stage(make_claude(tmp_path), tmp_path / "out")
    assert summary.sessions == 3
    assert summary.months == {"2026-09": 1, "2026-10": 2}
    sessions = tmp_path / "out" / "sessions"
    assert (sessions / "2026-09" / PROJECT / f"{SEP}.jsonl").is_symlink()
    assert (sessions / "2026-10" / PROJECT / f"{OCT}.jsonl").is_symlink()
    assert (sessions / "2026-10" / OTHER / f"{SITE}.jsonl").is_symlink()


def test_session_is_bundled_with_sidecar_and_file_history(tmp_path):
    claude = make_claude(tmp_path)
    stage(claude, tmp_path / "out")
    bundle = tmp_path / "out" / "sessions" / "2026-09" / PROJECT / SEP
    assert sorted(p.name for p in bundle.iterdir()) == ["file-history", "subagents", "tool-results"]
    assert os.path.realpath(bundle / "file-history") == os.path.realpath(claude / "file-history" / SEP)


def test_orphan_file_history_is_kept_separately(tmp_path):
    summary = stage(make_claude(tmp_path), tmp_path / "out")
    assert summary.orphan_file_history == 1
    assert (tmp_path / "out" / "file-history" / ORPHAN).is_symlink()


def test_start_month_falls_back_to_file_time(tmp_path):
    path = write(tmp_path / "x.jsonl", '{"type":"bridge-session"}\nnot json\n')
    month = start_month(path)
    assert len(month) == 7 and month[4] == "-"
