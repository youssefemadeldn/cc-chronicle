"""Bring memory, sessions, plans and prompt history back from the archive.

Restore never overwrites a local file (--ignore-existing): the local copy of a live
session is always newer than its archived copy. Sessions return to their original
project folder.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ChronicleError, rclone
from .backup import log_path, run_id
from .config import Config


@dataclass
class RemoteSession:
    month: str
    project: str
    session_id: str
    size: int
    local: bool


def remote_sessions(cfg: Config) -> list:
    """Every session in the archive, marked with whether this machine still has it."""
    proc = rclone.run(["lsf", "-R", "--files-only", "--format", "ps", "--separator", ";",
                       "--include", "/*/*/*.jsonl", f"{cfg.dest}/.claude/sessions"], check=False)
    if proc.returncode != 0:
        if "directory not found" in proc.stderr.lower():
            return []
        raise rclone.RcloneError(f"rclone lsf failed: {proc.stderr.strip()[-300:]}")
    sessions = []
    for line in proc.stdout.splitlines():
        path, _, size = line.rpartition(";")
        parts = path.split("/")
        if len(parts) != 3 or not parts[2].endswith(".jsonl"):
            continue
        month, project, name = parts
        local = (cfg.claude_dir / "projects" / project / name).exists()
        sessions.append(RemoteSession(month, project, name[:-6], int(size or 0), local))
    return sorted(sessions, key=lambda s: (s.month, s.project, s.session_id))


class Restorer:
    def __init__(self, cfg: Config, *, dry_run: bool = False, out=print):
        self.cfg = cfg
        self.dry_run = dry_run
        self.out = out
        self.log_file = log_path(run_id(), "restore-dry" if dry_run else "restore")

    def _copy(self, name: str, *args: str, allow_missing: bool = False) -> int:
        n = rclone.transfer(*args, "--ignore-existing", log_file=self.log_file, dry_run=self.dry_run,
                            allow_missing=allow_missing)
        self.out(f"  {name:<14} {n} file action(s)")
        return n

    def memory(self, project: str | None = None) -> int:
        pattern = f"/{rclone.escape_glob(project)}/memory/**" if project else "/*/memory/**"
        return self._copy("memory", "copy", f"{self.cfg.dest}/.claude/projects", str(self.cfg.claude_dir / "projects"),
                          "--filter", f"+ {pattern}", "--filter", "- **", allow_missing=True)

    def session(self, id_prefix: str) -> RemoteSession:
        matches = [s for s in remote_sessions(self.cfg) if s.session_id.startswith(id_prefix)]
        if not matches:
            raise ChronicleError(f"no archived session starts with '{id_prefix}'")
        if len(matches) > 1:
            raise ChronicleError(f"'{id_prefix}' matches {len(matches)} sessions — use a longer prefix")
        s = matches[0]
        base = f"{self.cfg.dest}/.claude/sessions/{s.month}/{s.project}"
        local_project = self.cfg.claude_dir / "projects" / s.project
        self.out(f"== session {s.session_id} ({s.month}, {s.project})")
        self._copy("transcript", "copyto", f"{base}/{s.session_id}.jsonl", str(local_project / f"{s.session_id}.jsonl"))
        self._copy("sidecar", "copy", f"{base}/{s.session_id}", str(local_project / s.session_id),
                   "--filter", "- /file-history/**", allow_missing=True)
        self._copy("file-history", "copy", f"{base}/{s.session_id}/file-history",
                   str(self.cfg.claude_dir / "file-history" / s.session_id), allow_missing=True)
        return s

    def project(self, text: str, month: str | None = None) -> list:
        wanted = [s for s in remote_sessions(self.cfg)
                  if not s.local and text in s.project and (month is None or s.month == month)]
        if not wanted:
            self.out("nothing to restore")
        for s in wanted:
            self.session(s.session_id)
        return wanted

    def history(self) -> int:
        return self._copy("history", "copyto", f"{self.cfg.dest}/.claude/history.{self.cfg.machine}.jsonl",
                          str(self.cfg.claude_dir / "history.jsonl"), allow_missing=True)

    def plans(self) -> int:
        return self._copy("plans", "copy", f"{self.cfg.dest}/.claude/plans", str(self.cfg.claude_dir / "plans"),
                          allow_missing=True)
