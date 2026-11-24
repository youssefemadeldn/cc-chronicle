"""`cc-chronicle doctor`: check everything a backup depends on, in one list."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from . import ChronicleError, rclone, secrets
from . import config as config_mod
from . import privacy, schedule

MIN_RCLONE = (1, 60)
OK, INFO, WARN, FAIL = "ok", "info", "warn", "FAIL"
PARTS = ("memory", "sessions", "file-history", "plans", "history",
         "cursor-chats", "cursor-projects", "readme")


def _rclone_version() -> tuple:
    out = rclone.run(["version"]).stdout
    m = re.search(r"rclone v(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def checks(config_path=None) -> list:
    """(level, name, detail) for every check, in order."""
    results = []
    add = lambda level, name, detail="": results.append((level, name, detail))  # noqa: E731

    py = sys.version_info
    add(OK if py >= (3, 9) else FAIL, "python", f"{py.major}.{py.minor}.{py.micro}")

    try:
        version = _rclone_version()
        add(OK if version >= MIN_RCLONE else FAIL, "rclone",
            f"v{version[0]}.{version[1]}" + ("" if version >= MIN_RCLONE else f" (need ≥ {MIN_RCLONE[0]}.{MIN_RCLONE[1]})"))
    except ChronicleError as exc:
        add(FAIL, "rclone", str(exc))
        return results

    try:
        cfg = config_mod.load(config_path)
        add(OK, "config", f"{cfg.dest}  machine={cfg.machine}")
    except ChronicleError as exc:
        add(FAIL, "config", str(exc))
        return results

    cap = cfg.bwlimit.strip()
    add(INFO if cap == "off" else OK, "upload cap", "off" if cap == "off" else cap)

    projects = cfg.claude_dir / "projects"
    add(OK if projects.is_dir() else FAIL, "claude dir", str(cfg.claude_dir) if projects.is_dir()
        else f"{cfg.claude_dir} has no projects/ folder")
    add(*_cursor_check(cfg))
    add(*_cleanup_check(cfg))
    add(*_secrets_check(cfg))

    proc = rclone.run(["lsd", f"{cfg.remote}:", "--max-depth", "1"], check=False)
    if proc.returncode != 0:
        add(FAIL, "remote", f"cannot reach '{cfg.remote}:' — try: rclone config reconnect {cfg.remote}:")
        return results
    add(OK, "remote", f"'{cfg.remote}:' reachable")

    kind = privacy.remote_type(cfg.remote)
    if kind == "drive":
        conf = rclone.run(["config", "show", cfg.remote], check=False).stdout
        own_id = re.search(r"^client_id = \S+", conf, re.M)
        add(OK if own_id else WARN, "drive client_id", "own OAuth client" if own_id else
            "uses rclone's shared client_id, which Google is retiring — see docs/SECURITY.md")
    result = privacy.check(cfg)
    level = {"private": OK, "shared": FAIL, "skipped": WARN if kind == "drive" else INFO}[result.status]
    add(level, "archive sharing", result.detail + (": " + ", ".join(result.shared_with) if result.shared_with else ""))

    daily = _schedule_check()
    add(*_last_backup_check(cfg, scheduled=daily[0] in (OK, WARN)))
    add(*daily)
    return results


def _schedule_check() -> tuple:
    try:
        command = schedule.Scheduler().installed_command()
    except ChronicleError as exc:
        return INFO, "daily backup", str(exc)
    if not command:
        return INFO, "daily backup", "not scheduled (optional): cc-chronicle schedule install"
    missing = [arg for arg in command[:2] if not os.path.exists(arg)]
    if missing:
        return FAIL, "daily backup", f"the job points to a missing file ({missing[0]}) — re-run: cc-chronicle schedule install"
    scheduler = schedule.Scheduler()
    detail = f"scheduled: {scheduler.status()}"
    code = scheduler.last_exit_code()
    if code:
        return WARN, "daily backup", f"{detail} — the last run FAILED; see {config_mod.state_dir() / 'logs' / 'scheduled.log'}"
    return OK, "daily backup", detail


def _secrets_check(cfg) -> tuple:
    report = secrets.scan(cfg)
    return (WARN if report.findings else OK), "secrets", secrets.summary_line(report)


def _cursor_check(cfg) -> tuple:
    if cfg.cursor != "on":
        return INFO, "cursor", "off (cursor = off)"
    if not cfg.cursor_dir.is_dir():
        return INFO, "cursor", f"no {cfg.cursor_dir} (nothing to back up)"
    data, _raw = newest_stamp(cfg)
    if data is None:
        return INFO, "cursor", f"{cfg.cursor_dir} will be included on the next backup"
    if data == "bad":
        return WARN, "cursor", "last backup stamp unreadable"
    if "cursor_chats" not in data:
        return INFO, "cursor", "last backup stamp has no Cursor counts yet"
    mebi = data.get("cursor_bytes", 0) / 2**20
    detail = (f"last run {data.get('cursor_chats', 0)} chat(s), {data.get('cursor_files', 0)} file(s), "
              f"{mebi:.1f} MiB, {data.get('cursor_snapshots', 0)} snapshotted")
    if "cursor_skipped" in data:
        detail += f", {data['cursor_skipped']} skipped (live)"
    failed = [name for name in (data.get("failed_steps") or []) if str(name).startswith("cursor")]
    if failed:
        return WARN, "cursor", f"{detail}; failed: {', '.join(failed)}"
    if data.get("cursor") == "absent":
        return INFO, "cursor", "last run found no ~/.cursor"
    return OK, "cursor", detail


def _cleanup_check(cfg) -> tuple:
    settings = cfg.claude_dir / "settings.json"
    try:
        days = json.loads(settings.read_text()).get("cleanupPeriodDays")
    except (OSError, ValueError):
        days = None
    if days is None:
        return WARN, "local cleanup", "cleanupPeriodDays not set: Claude Code deletes sessions after ~30 days, so back up regularly"
    return OK, "local cleanup", f"cleanupPeriodDays = {days} (reports exist of it being ignored; the archive is your copy)"


def _stamp_when(data):
    when = datetime.fromisoformat(data["finished_at"])
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _read_local_stamp(cfg) -> str | None:
    path = config_mod.local_stamp_path(cfg.machine)
    try:
        if path.is_file():
            return path.read_text()
    except OSError:
        return None
    return None


def newest_stamp(cfg):
    """The newer of the remote stamp and the local fallback.

    Returns (data, raw). data is None when neither exists, or "bad" when every
    stamp that does exist is unreadable.
    """
    found = []
    bad = False
    for raw in (rclone.cat(f"{cfg.dest}/last-backup.{cfg.machine}.json"), _read_local_stamp(cfg)):
        if not raw:
            continue
        try:
            data = json.loads(raw)
            when = _stamp_when(data)
        except (ValueError, KeyError, TypeError):
            bad = True
            continue
        found.append((when, data, raw))
    if not found:
        return ("bad", None) if bad else (None, None)
    _when, data, raw = max(found, key=lambda item: item[0])
    return data, raw


def _part_names(parts: dict) -> list:
    names = [name for name in PARTS if name in parts]
    names.extend(sorted(name for name in parts if name not in PARTS))
    return names


def _last_backup_detail(data, when) -> str:
    age = datetime.now(timezone.utc) - when
    detail = f"{when:%Y-%m-%d %H:%M} ({age.days} day(s) ago)"
    parts = data.get("parts_ok") if isinstance(data.get("parts_ok"), dict) else {}
    failed = [str(name) for name in (data.get("failed_steps") or [])]
    ok_bits = [f"{name} {parts[name]}" for name in _part_names(parts) if name not in failed and parts.get(name)]
    fail_bits = []
    for name in failed:
        day = parts.get(name)
        fail_bits.append(f"{name} since {day}" if day else name)
    if ok_bits:
        detail += "; " + ", ".join(ok_bits)
    if fail_bits:
        detail += "; failed: " + ", ".join(fail_bits) + " (see the run's log)"
    return detail


def _last_backup_check(cfg, scheduled: bool = False) -> tuple:
    data, _raw = newest_stamp(cfg)
    if data is None:
        return WARN, "last backup", "none yet — run: cc-chronicle backup"
    if data == "bad":
        return WARN, "last backup", "stamp unreadable"
    try:
        when = datetime.fromisoformat(data["finished_at"])
    except (ValueError, KeyError, TypeError):
        return WARN, "last backup", "stamp unreadable"
    age = datetime.now(timezone.utc) - (when if when.tzinfo else when.replace(tzinfo=timezone.utc))
    detail = _last_backup_detail(data, when if when.tzinfo else when.replace(tzinfo=timezone.utc))
    failed = data.get("failed_steps") or []
    if failed:
        return WARN, "last backup", detail
    if scheduled and age > timedelta(hours=26):
        return WARN, "last backup", f"{detail}, but the daily job should have made a newer one (see the run's log)"
    return (OK if age.days < 7 else WARN), "last backup", detail


def run(config_path=None, out=print) -> int:
    results = checks(config_path)
    for level, name, detail in results:
        mark = {"ok": "✓", "info": "·", "warn": "!", "FAIL": "✗"}[level]
        out(f"  {mark} {name:<16} {detail}")
    return 1 if any(level == FAIL for level, _, _ in results) else 0
