"""Lay out local sessions the way the archive stores them, as a tree of symlinks.

    <out>/sessions/<YYYY-MM>/<project>/<id>.jsonl
    <out>/sessions/<YYYY-MM>/<project>/<id>/{subagents,tool-results,...}
    <out>/sessions/<YYYY-MM>/<project>/<id>/file-history     <- <claude>/file-history/<id>
    <out>/file-history/<id>                                    <- only orphans (no session file)

The month is the session's START date (first timestamp, local time), so a session never
moves between months. rclone uploads the tree with --copy-links.

Files changed in the last LIVE_SECONDS are copied in instead of linked: an open session keeps
appending to its transcript, and rclone fails a file that changes while it uploads. The copy
is a fixed snapshot; the next run uploads whatever was added after it.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LIVE_SECONDS = 24 * 3600


@dataclass
class StageSummary:
    sessions: int = 0
    bytes: int = 0
    months: dict = field(default_factory=dict)
    orphan_file_history: int = 0
    snapshots: int = 0


def start_month(path: Path) -> str:
    with open(path, errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i > 200:
                break
            try:
                ts = json.loads(line).get("timestamp")
            except (ValueError, AttributeError):
                continue
            if ts:
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
                except ValueError:
                    continue
                return when.strftime("%Y-%m")
    st = path.stat()
    return datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime)).strftime("%Y-%m")


def _changed_since(path: Path, cutoff: float) -> bool:
    files = [path] if path.is_file() else (p for p in path.rglob("*") if p.is_file())
    for f in files:
        try:
            if f.stat().st_mtime >= cutoff:
                return True
        except FileNotFoundError:  # removed while we look
            continue
    return False


def stage(claude_dir: Path, out_dir: Path, *, live_seconds: float = LIVE_SECONDS) -> StageSummary:
    projects = claude_dir / "projects"
    file_history = claude_dir / "file-history"
    summary = StageSummary()
    seen = set()
    cutoff = time.time() - live_seconds

    def _link(src: Path, dst: Path) -> None:
        """Link src at dst; a file changed since the cutoff is copied, and a folder holding one is
        rebuilt so that only its changed files are copied."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _changed_since(src, cutoff):
            os.symlink(src, dst)
        elif src.is_dir():
            dst.mkdir(exist_ok=True)
            for child in src.iterdir():
                if child.name != ".DS_Store":
                    _link(child, dst / child.name)
        else:
            shutil.copy2(src, dst)
            summary.snapshots += 1

    for pdir in sorted(projects.iterdir()) if projects.is_dir() else []:
        if not pdir.is_dir():
            continue
        for src in sorted(pdir.glob("*.jsonl")):
            sid = src.stem
            month = start_month(src)
            base = out_dir / "sessions" / month / pdir.name
            _link(src, base / src.name)
            summary.bytes += src.stat().st_size

            sidecar = pdir / sid
            if sidecar.is_dir():
                for child in sidecar.iterdir():
                    if child.name != ".DS_Store":
                        _link(child, base / sid / child.name)
            fh = file_history / sid
            if fh.is_dir():
                _link(fh, base / sid / "file-history")

            seen.add(sid)
            summary.sessions += 1
            summary.months[month] = summary.months.get(month, 0) + 1

    if file_history.is_dir():
        for fh in file_history.iterdir():
            if fh.is_dir() and fh.name not in seen:
                _link(fh, out_dir / "file-history" / fh.name)
                summary.orphan_file_history += 1

    summary.months = dict(sorted(summary.months.items()))
    return summary
