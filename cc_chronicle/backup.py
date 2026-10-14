"""Back up memory, sessions, plans and prompt history to the archive.

Rules (see docs/DESIGN.md for the reasons):
- memory   MIRROR, only for projects on this machine; removed or changed notes move to _deleted/<run>/
- sessions ARCHIVE by start month, each bundled with subagents/, tool-results/, file-history/
- plans    ARCHIVE
- history  ARCHIVE, renamed history.<machine>.jsonl so machines never overwrite each other
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import ChronicleError, __version__, rclone, stage
from .config import Config, state_dir

REMOTE_README = Path(__file__).with_name("remote_readme.md")


@dataclass
class BackupResult:
    run: str
    dry_run: bool
    log_file: Path
    actions: dict = field(default_factory=dict)
    memory_folders: int = 0
    staged: stage.StageSummary = field(default_factory=stage.StageSummary)


def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def log_path(run: str, kind: str) -> Path:
    return state_dir() / "logs" / f"{run}-{kind}.log"


def backup(cfg: Config, *, dry_run: bool = False, out=print) -> BackupResult:
    claude = cfg.claude_dir
    if not (claude / "projects").is_dir():
        raise ChronicleError(f"no Claude Code data at {claude} (set claude_dir in the config)")
    run = run_id()
    result = BackupResult(run=run, dry_run=dry_run, log_file=log_path(run, "backup-dry" if dry_run else "backup"))
    progress = sys.stdout.isatty() and not dry_run and out is print
    note = "  (dry run: nothing is uploaded)" if dry_run else ""
    out(f"== backup {claude} → {cfg.dest}{note}")

    def step(name: str, *args: str, **kwargs) -> None:
        n = rclone.transfer(*args, log_file=result.log_file, dry_run=dry_run, progress=progress, **kwargs)
        result.actions[name] = n
        out(f"  {name:<14} {n} file action(s)")

    with tempfile.TemporaryDirectory(prefix="cc-chronicle-") as tmp:
        tmp = Path(tmp)

        if "memory" in cfg.include:
            projects = sorted(p.parent.name for p in (claude / "projects").glob("*/memory") if p.is_dir())
            result.memory_folders = len(projects)
            if projects:
                rules = tmp / "memory.filter"
                rules.write_text("- .DS_Store\n"
                                 + "".join(f"+ /{rclone.escape_glob(p)}/memory/**\n" for p in projects)
                                 + "- **\n")
                step("memory", "sync", str(claude / "projects"), f"{cfg.dest}/.claude/projects",
                     "--filter-from", str(rules),
                     "--backup-dir", f"{cfg.dest}/_deleted/{run}/.claude/projects")

        if "sessions" in cfg.include:
            result.staged = stage.stage(claude, tmp / "stage")
            s = result.staged
            months = ", ".join(f"{m}: {n}" for m, n in s.months.items()) or "none"
            out(f"  {'staged':<14} {s.sessions} session(s) ({months}), {s.bytes / 2**20:.1f} MiB")
            if (tmp / "stage" / "sessions").is_dir():
                step("sessions", "copy", str(tmp / "stage" / "sessions"), f"{cfg.dest}/.claude/sessions", "--copy-links")
            if (tmp / "stage" / "file-history").is_dir():
                step("file-history", "copy", str(tmp / "stage" / "file-history"), f"{cfg.dest}/.claude/file-history",
                     "--copy-links")

        if "plans" in cfg.include and (claude / "plans").is_dir():
            step("plans", "copy", str(claude / "plans"), f"{cfg.dest}/.claude/plans")
        if "history" in cfg.include and (claude / "history.jsonl").is_file():
            step("history", "copyto", str(claude / "history.jsonl"), f"{cfg.dest}/.claude/history.{cfg.machine}.jsonl")

        step("readme", "copyto", str(REMOTE_README), f"{cfg.dest}/README.md")

    if not dry_run:
        rclone.rcat(f"{cfg.dest}/last-backup.{cfg.machine}.json", stamp(cfg, result))
        out(f"  {'stamp':<14} last-backup.{cfg.machine}.json")
    out(f"== done. log: {result.log_file}")
    return result


def stamp(cfg: Config, result: BackupResult) -> str:
    return json.dumps({
        "tool": "cc-chronicle",
        "version": __version__,
        "machine": cfg.machine,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run": result.run,
        "memory_folders": result.memory_folders,
        "sessions": result.staged.sessions,
        "sessions_by_month": result.staged.months,
        "session_bytes": result.staged.bytes,
    }, indent=2) + "\n"
