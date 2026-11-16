"""Stage ~/.cursor/chats and ~/.cursor/projects the way the archive stores them.

    <out>/chats/<YYYY-MM>/<workspace-hash>/<chat-uuid>/...
    <out>/projects/<same relative tree as ~/.cursor/projects>

Chats are filed by start month (meta.json createdAtMs, else the oldest file time),
so a chat never moves between months. Both trees are archives: rclone copy, never sync.

A store.db that has a -wal file, or that changed in the last 24 hours, is snapshotted
with sqlite's backup API (read-only open). Other files are symlinks, unless they
changed in the last 24 hours, in which case they are plain copies — the same rule as
stage.py. -wal and -shm are not staged; a snapshot already holds their contents.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ChronicleError
from .stage import LIVE_SECONDS

# createdAtMs is what Cursor writes. The other names cover a meta.json that uses them.
_CREATED_KEYS = ("createdAtMs", "createdAt", "created_at", "created", "timestamp", "startTime", "startedAt")


@dataclass
class CursorSummary:
    chats: int = 0
    chat_files: int = 0
    chat_bytes: int = 0
    months: dict = field(default_factory=dict)
    snapshots: int = 0
    project_files: int = 0
    project_bytes: int = 0
    skipped: list = field(default_factory=list)  # live store.db paths left for the next run

    @property
    def files(self) -> int:
        return self.chat_files + self.project_files

    @property
    def bytes(self) -> int:
        return self.chat_bytes + self.project_bytes


def chat_month(chat_dir: Path) -> str:
    """YYYY-MM the chat started, in local time. Stable for the life of the chat."""
    meta = chat_dir / "meta.json"
    if meta.is_file():
        month = _month_from_meta(meta)
        if month:
            return month
    oldest = _oldest_file_time(chat_dir)
    if oldest is None:
        oldest = _file_time(chat_dir.stat())
    return datetime.fromtimestamp(oldest).astimezone().strftime("%Y-%m")


def _month_from_meta(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in _CREATED_KEYS:
        when = _parse_time(data.get(key))
        if when:
            return when.astimezone().strftime("%Y-%m")
    return None


def _parse_time(value) -> datetime | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000.0 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_time(int(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _file_time(st) -> float:
    """The earliest time we can see for a file: creation when present, else mtime, whichever is older."""
    when = st.st_mtime
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        when = min(when, birth)
    return when


def _oldest_file_time(chat_dir: Path) -> float | None:
    oldest = None
    for path in chat_dir.rglob("*"):
        if not path.is_file() or _skip(path):
            continue
        try:
            when = _file_time(path.stat())
        except FileNotFoundError:
            continue
        if oldest is None or when < oldest:
            oldest = when
    return oldest


def _skip(path: Path) -> bool:
    return path.name == ".DS_Store" or path.name.endswith(("-wal", "-shm"))


def _changed_since(path: Path, cutoff: float) -> bool:
    files = [path] if path.is_file() else (p for p in path.rglob("*") if p.is_file() and not _skip(p))
    for item in files:
        try:
            if item.stat().st_mtime >= cutoff:
                return True
        except FileNotFoundError:
            continue
    return False


def _live_db(path: Path, cutoff: float) -> bool:
    """A store.db with a WAL, or written in the last 24 hours, must be snapshotted."""
    if path.name != "store.db":
        return False
    if path.with_name("store.db-wal").exists():
        return True
    try:
        return path.stat().st_mtime >= cutoff
    except FileNotFoundError:
        return False


class LiveFileError(ChronicleError):
    """A live store.db could not be snapshotted. Skip that chat; do not fail the step."""


def snapshot_sqlite(src: Path, dst: Path) -> None:
    """Copy src into dst with the sqlite backup API.

    The source is opened read-only. When that open fails (Cursor holds the WAL index),
    store.db and its -wal/-shm are copied into a private temp folder and the backup
    API runs from that copy. The temp folder is removed. ~/.cursor is not written.
    """
    try:
        _snapshot_readonly(src, dst)
        return
    except (sqlite3.Error, OSError):
        pass
    try:
        _snapshot_private_copy(src, dst)
    except (sqlite3.Error, OSError) as exc:
        raise LiveFileError(f"could not snapshot {src}: {exc}") from exc


def _backup_into(src_conn: sqlite3.Connection, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()


def _snapshot_readonly(src: Path, dst: Path) -> None:
    uri = src.resolve().as_uri() + "?mode=ro"
    src_conn = sqlite3.connect(uri, uri=True)
    try:
        _backup_into(src_conn, dst)
    finally:
        src_conn.close()


def _snapshot_private_copy(src: Path, dst: Path) -> None:
    """Snapshot a copy SQLite can write to. The original directory is only read."""
    with tempfile.TemporaryDirectory(prefix="cc-chronicle-sqlite-") as tmp:
        folder = Path(tmp)
        copied = folder / src.name
        shutil.copy2(src, copied)
        for suffix in ("-wal", "-shm"):
            side = src.with_name(src.name + suffix)
            if side.is_file():
                shutil.copy2(side, folder / side.name)
        src_conn = sqlite3.connect(copied)
        try:
            _backup_into(src_conn, dst)
        finally:
            src_conn.close()


def _add(summary: CursorSummary, kind: str, files: int, size: int) -> None:
    if kind == "project":
        summary.project_files += files
        summary.project_bytes += size
    else:
        summary.chat_files += files
        summary.chat_bytes += size


def _tally(path: Path) -> tuple:
    if path.is_file():
        return (0, 0) if _skip(path) else (1, path.stat().st_size)
    files = size = 0
    for item in path.rglob("*"):
        if item.is_file() and not _skip(item):
            files += 1
            size += item.stat().st_size
    return files, size


def _place(src: Path, dst: Path, cutoff: float, summary: CursorSummary, kind: str) -> None:
    """Link src at dst. A live store.db is snapshotted; a recently changed file is copied."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file() and _live_db(src, cutoff):
        try:
            snapshot_sqlite(src, dst)
        except LiveFileError:
            if kind == "chat":
                raise
            return
        summary.snapshots += 1
        _add(summary, kind, 1, dst.stat().st_size)
        return
    if not _changed_since(src, cutoff):
        os.symlink(src, dst)
        files, size = _tally(src)
        _add(summary, kind, files, size)
        return
    if src.is_dir():
        dst.mkdir(exist_ok=True)
        for child in sorted(src.iterdir(), key=lambda p: p.name):
            if not _skip(child):
                _place(child, dst / child.name, cutoff, summary, kind)
        return
    shutil.copy2(src, dst)
    _add(summary, kind, 1, dst.stat().st_size)


def _drop_empty(path: Path) -> None:
    """Remove directories a skipped chat left behind, and stop at the chats folder."""
    while path.name not in ("chats", "") and path.is_dir():
        try:
            path.rmdir()
        except OSError:
            break
        path = path.parent


def stage_chats(cursor_dir: Path, out_dir: Path, summary: CursorSummary, *,
                live_seconds: float = LIVE_SECONDS) -> None:
    chats_root = cursor_dir / "chats"
    if not chats_root.is_dir():
        return
    cutoff = time.time() - live_seconds
    for workspace in sorted(p for p in chats_root.iterdir() if p.is_dir()):
        for chat in sorted(p for p in workspace.iterdir() if p.is_dir()):
            if not (chat / "meta.json").is_file() and not (chat / "store.db").is_file():
                continue
            month = chat_month(chat)
            dest = out_dir / "chats" / month / workspace.name / chat.name
            db = chat / "store.db"
            if db.is_file() and _live_db(db, cutoff):
                dest.mkdir(parents=True, exist_ok=True)
                try:
                    snapshot_sqlite(db, dest / db.name)
                except LiveFileError:
                    summary.skipped.append(str(db))
                    shutil.rmtree(dest, ignore_errors=True)
                    _drop_empty(dest.parent)
                    continue
                summary.snapshots += 1
                _add(summary, "chat", 1, (dest / db.name).stat().st_size)
            else:
                dest.mkdir(parents=True, exist_ok=True)
            for child in sorted(chat.iterdir(), key=lambda p: p.name):
                if _skip(child) or child.name == "store.db" and (dest / "store.db").exists():
                    continue
                _place(child, dest / child.name, cutoff, summary, "chat")
            summary.chats += 1
            summary.months[month] = summary.months.get(month, 0) + 1
    summary.months = dict(sorted(summary.months.items()))


def stage_projects(cursor_dir: Path, out_dir: Path, summary: CursorSummary, *,
                   live_seconds: float = LIVE_SECONDS) -> None:
    projects = cursor_dir / "projects"
    if not projects.is_dir():
        return
    children = [p for p in sorted(projects.iterdir(), key=lambda p: p.name) if not _skip(p)]
    if not children:
        return
    cutoff = time.time() - live_seconds
    dest = out_dir / "projects"
    dest.mkdir(parents=True, exist_ok=True)
    for child in children:
        _place(child, dest / child.name, cutoff, summary, "project")


def stage(cursor_dir: Path, out_dir: Path, *, live_seconds: float = LIVE_SECONDS) -> CursorSummary:
    summary = CursorSummary()
    stage_chats(cursor_dir, out_dir, summary, live_seconds=live_seconds)
    stage_projects(cursor_dir, out_dir, summary, live_seconds=live_seconds)
    return summary
