"""Back up memory, sessions, plans and prompt history to the archive.

Rules (see docs/DESIGN.md for the reasons):
- memory   MIRROR, only for projects on this machine; removed or changed notes move to _deleted/<run>/
- sessions ARCHIVE by start month, each bundled with subagents/, tool-results/, file-history/
- plans    ARCHIVE
- history  ARCHIVE, renamed history.<machine>.jsonl so machines never overwrite each other
- cursor   ARCHIVE when ~/.cursor exists and cursor=on: chats by start month, projects as their own tree
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import ChronicleError, __version__, doctor, privacy, rclone, secrets, stage
from . import cursor as cursor_mod
from .config import Config, local_stamp_path, state_dir

REMOTE_README = Path(__file__).with_name("remote_readme.md")


@dataclass
class BackupResult:
    run: str
    dry_run: bool
    log_file: Path
    actions: dict = field(default_factory=dict)
    memory_folders: int = 0
    staged: stage.StageSummary = field(default_factory=stage.StageSummary)
    secrets: secrets.ScanReport = field(default_factory=secrets.ScanReport)
    privacy: privacy.PrivacyResult | None = None
    failed: dict = field(default_factory=dict)
    cursor: cursor_mod.CursorSummary = field(default_factory=cursor_mod.CursorSummary)
    cursor_state: str = "absent"


def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def log_path(run: str, kind: str) -> Path:
    return state_dir() / "logs" / f"{run}-{kind}.log"


def backup(cfg: Config, *, dry_run: bool = False, strict_secrets: bool = False,
           bwlimit: str | None = None, out=print) -> BackupResult:
    claude = cfg.claude_dir
    if not (claude / "projects").is_dir():
        raise ChronicleError(f"no Claude Code data at {claude} (set claude_dir in the config)")
    run = run_id()
    result = BackupResult(run=run, dry_run=dry_run, log_file=log_path(run, "backup-dry" if dry_run else "backup"))
    if cfg.cursor != "on":
        result.cursor_state = "off"
    elif cfg.cursor_dir.is_dir():
        result.cursor_state = "on"
    progress = sys.stdout.isatty() and not dry_run and out is print
    limit = cfg.bwlimit if bwlimit is None else bwlimit
    if not str(limit).strip():
        raise ChronicleError("bwlimit is empty; use off to remove the cap")
    note = "  (dry run: nothing is uploaded)" if dry_run else ""
    out(f"== backup {claude} → {cfg.dest}{note}")

    result.secrets = secrets.scan(cfg)
    out(f"  {'secrets':<14} {secrets.summary_line(result.secrets)}")
    if result.secrets.findings:
        out(f"  {'':<14} list them: cc-chronicle scan · what to do: docs/SECURITY.md")
    abort = strict_secrets or cfg.secrets_on_found == "abort"
    if abort and result.secrets.new_findings:
        message = (f"{len(result.secrets.new_findings)} new or changed file(s) contain likely secrets; "
                   f"nothing was uploaded (secrets_on_found=abort). Run: cc-chronicle scan")
        if not dry_run:
            raise ChronicleError(message)
        out(f"  {'':<14} a real backup would stop here: {message}")

    def step(name: str, *args: str, **kwargs) -> None:
        # One failed step (say, a file that kept changing) must not cost the others their upload.
        # A network error is waited out and tried again inside rclone.transfer.
        try:
            n = rclone.transfer(*args, log_file=result.log_file, dry_run=dry_run, progress=progress,
                                bwlimit=limit, **kwargs, **_network_kwargs(cfg, out, name))
        except rclone.RcloneError as exc:
            result.failed[name] = str(exc)
            out(f"  {name:<14} FAILED: {exc}")
            return
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
            out(f"  {'staged':<14} {s.sessions} session(s) ({months}), {s.bytes / 2**20:.1f} MiB"
                + (f", {s.snapshots} recent file(s) copied" if s.snapshots else ""))
            if (tmp / "stage" / "sessions").is_dir():
                step("sessions", "copy", str(tmp / "stage" / "sessions"), f"{cfg.dest}/.claude/sessions", "--copy-links")
            if (tmp / "stage" / "file-history").is_dir():
                step("file-history", "copy", str(tmp / "stage" / "file-history"), f"{cfg.dest}/.claude/file-history",
                     "--copy-links")

        if "plans" in cfg.include and (claude / "plans").is_dir():
            step("plans", "copy", str(claude / "plans"), f"{cfg.dest}/.claude/plans")
        if "history" in cfg.include and (claude / "history.jsonl").is_file():
            step("history", "copyto", str(claude / "history.jsonl"), f"{cfg.dest}/.claude/history.{cfg.machine}.jsonl")

        if result.cursor_state == "on":
            cursor_out = tmp / "cursor"
            chats_ready = _cursor_stage("cursor-chats", cursor_mod.stage_chats, cfg.cursor_dir, cursor_out, result, out)
            projects_ready = _cursor_stage("cursor-projects", cursor_mod.stage_projects, cfg.cursor_dir, cursor_out, result, out)
            c = result.cursor
            months = ", ".join(f"{m}: {n}" for m, n in c.months.items()) or "none"
            out(f"  {'cursor':<14} {c.chats} chat(s) ({months}), {c.files} file(s), "
                f"{c.bytes / 2**20:.1f} MiB, {c.snapshots} snapshotted, {len(c.skipped)} skipped (live)")
            _log_skipped(result)
            if c.skipped and c.chats == 0 and "cursor-chats" not in result.failed:
                result.failed["cursor-chats"] = "nothing could be staged"
                out(f"  {'cursor-chats':<14} FAILED: nothing could be staged")
            elif chats_ready and (cursor_out / "chats").is_dir():
                step("cursor-chats", "copy", str(cursor_out / "chats"), f"{cfg.dest}/.cursor/chats", "--copy-links")
            if projects_ready and (cursor_out / "projects").is_dir():
                step("cursor-projects", "copy", str(cursor_out / "projects"),
                     f"{cfg.dest}/.cursor/projects", "--copy-links")

        step("readme", "copyto", str(REMOTE_README), f"{cfg.dest}/README.md")

    if not dry_run:
        _write_stamp(cfg, result, out)
        result.privacy = privacy.check(cfg)
        if result.privacy.status == "shared":
            out(f"  !! SHARING    the archive folder is shared: {', '.join(result.privacy.shared_with)}")
            out(f"  {'':<14} it holds private conversations; remove that sharing in the storage provider")
        elif result.privacy.status == "private":
            out(f"  {'sharing':<14} private (only the owner has access)")
    if result.failed:
        raise ChronicleError(f"{len(result.failed)} step(s) failed ({', '.join(result.failed)}); "
                             f"the rest were uploaded. log: {result.log_file}")
    out(f"== done. log: {result.log_file}")
    return result


def _log_skipped(result: BackupResult) -> None:
    if not result.cursor.skipped:
        return
    result.log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(result.log_file, "a") as fh:
        for path in result.cursor.skipped:
            fh.write(f"cursor skipped (live): {path}\n")


def _cursor_stage(name: str, fn, cursor_dir: Path, out_dir: Path, result: BackupResult, out) -> bool:
    """Stage one Cursor tree. A failure is recorded and does not stop the other tree."""
    try:
        fn(cursor_dir, out_dir, result.cursor)
    except (ChronicleError, OSError) as exc:
        result.failed[name] = str(exc)
        out(f"  {name:<14} FAILED: {exc}")
        return False
    return True


def _network_kwargs(cfg: Config, out, name: str) -> dict:
    def on_retry(wait, name=name):
        out(f"  {name:<14} network error, waiting {wait}s")

    return {
        "network_retries": cfg.network_retries,
        "network_retry_waits": cfg.network_retry_waits,
        "retries_sleep": cfg.retries_sleep,
        "low_level_retries": cfg.low_level_retries,
        "on_retry": on_retry,
    }


def _write_stamp(cfg: Config, result: BackupResult, out) -> None:
    text = stamp(cfg, result)
    remote = f"{cfg.dest}/last-backup.{cfg.machine}.json"
    try:
        rclone.rcat(remote, text, **_network_kwargs(cfg, out, "stamp"))
    except rclone.RcloneError as exc:
        if not (exc.network or rclone.is_network_error(str(exc))):
            raise
        path = local_stamp_path(cfg.machine)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        except OSError as err:
            raise ChronicleError(f"stamp could not be saved locally: {err}") from err
        out(f"  {'stamp':<14} saved locally after the remote write failed")
        return
    out(f"  {'stamp':<14} last-backup.{cfg.machine}.json")


def _parts_ok(cfg: Config, result: BackupResult) -> dict:
    """Last day each part succeeded. A failure keeps the previous day."""
    data, _raw = doctor.newest_stamp(cfg)
    prior = {}
    if isinstance(data, dict) and isinstance(data.get("parts_ok"), dict):
        prior = {str(key): str(value) for key, value in data["parts_ok"].items() if value}
    today = result.run[:10]
    attempted = list(result.actions) + [name for name in result.failed if name not in result.actions]
    days = {}
    for name in attempted:
        if name in result.failed:
            if prior.get(name):
                days[name] = prior[name]
        else:
            days[name] = today
    ordered = {}
    for name in doctor.PARTS:
        if name in days:
            ordered[name] = days[name]
    for name in sorted(days):
        if name not in ordered:
            ordered[name] = days[name]
    return ordered


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
        "cursor": result.cursor_state,
        "cursor_chats": result.cursor.chats,
        "cursor_chats_by_month": result.cursor.months,
        "cursor_files": result.cursor.files,
        "cursor_bytes": result.cursor.bytes,
        "cursor_snapshots": result.cursor.snapshots,
        "cursor_skipped": len(result.cursor.skipped),
        "cursor_project_files": result.cursor.project_files,
        "cursor_project_bytes": result.cursor.project_bytes,
        "failed_steps": sorted(result.failed),
        "parts_ok": _parts_ok(cfg, result),
    }, indent=2) + "\n"
