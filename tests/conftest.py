"""Shared fixtures: a fake ~/.claude and an rclone 'local' remote, so no test touches the cloud."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cc_chronicle.config import Config  # noqa: E402

PROJECT = "-Users-me-code-app"
OTHER = "-Users-me-code-site"
SEP = "aaaa1111-0000-0000-0000-000000000001"  # starts 2026-09-15, has sidecar + file-history
OCT = "bbbb2222-0000-0000-0000-000000000002"  # starts 2026-10-15
SITE = "cccc3333-0000-0000-0000-000000000003"  # other project, 2026-10-20
ORPHAN = "dddd4444-0000-0000-0000-000000000004"  # file-history with no session file

needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def session_lines(ts: str, title: str) -> str:
    events = [
        {"type": "queue-operation", "timestamp": ts},
        {"type": "user", "timestamp": ts, "message": {"content": "hello"}},
        {"type": "ai-title", "title": title},
    ]
    return "".join(json.dumps(e) + "\n" for e in events)


def age(root: Path, days: int = 30) -> None:
    """Date every file under root `days` ago, as if no session had touched it lately."""
    when = time.time() - days * 86400
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (when, when))


def make_claude(root: Path) -> Path:
    claude = root / "claude"
    proj = claude / "projects" / PROJECT
    write(proj / "memory" / "MEMORY.md", "- [Note](note.md) — a fact\n")
    write(proj / "memory" / "note.md", "---\nname: note\n---\nA fact.\n")
    write(proj / "memory" / ".DS_Store", "junk")
    write(proj / f"{SEP}.jsonl", session_lines("2026-09-15T12:00:00Z", "September work"))
    write(proj / SEP / "subagents" / "agent-1.jsonl", '{"type":"user"}\n')
    write(proj / SEP / "tool-results" / "r1.txt", "tool output\n")
    write(proj / f"{OCT}.jsonl", session_lines("2026-10-15T12:00:00Z", "October work"))
    write(claude / "projects" / OTHER / f"{SITE}.jsonl", session_lines("2026-10-20T12:00:00Z", "Site work"))
    write(claude / "file-history" / SEP / "abc123@v1", "old file content\n")
    write(claude / "file-history" / ORPHAN / "def456@v1", "orphan snapshot\n")
    write(claude / "plans" / "plan-a.md", "# Plan A\n")
    write(claude / "history.jsonl", '{"display":"hello"}\n')
    return claude


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Config pointing a fake ~/.claude at an rclone local remote inside tmp_path."""
    rclone_conf = write(tmp_path / "rclone.conf", "[t]\ntype = local\n")
    monkeypatch.setenv("RCLONE_CONFIG", str(rclone_conf))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CC_CHRONICLE_CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    archive = tmp_path / "remote" / "archive"
    cfg = Config(remote="t", root=str(archive), machine="mac", claude_dir=make_claude(tmp_path))
    return cfg, cfg.claude_dir, archive


def quiet(*_args, **_kwargs):
    pass
