"""`cc-chronicle doctor`: check everything a backup depends on, in one list."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

from . import ChronicleError, rclone
from . import config as config_mod
from . import privacy, schedule

MIN_RCLONE = (1, 60)
OK, INFO, WARN, FAIL = "ok", "info", "warn", "FAIL"


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

    projects = cfg.claude_dir / "projects"
    add(OK if projects.is_dir() else FAIL, "claude dir", str(cfg.claude_dir) if projects.is_dir()
        else f"{cfg.claude_dir} has no projects/ folder")
    add(*_cleanup_check(cfg))

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

    add(*_last_backup_check(cfg))
    add(*_schedule_check())
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
    return OK, "daily backup", f"scheduled: {schedule.Scheduler().status()}"


def _cleanup_check(cfg) -> tuple:
    settings = cfg.claude_dir / "settings.json"
    try:
        days = json.loads(settings.read_text()).get("cleanupPeriodDays")
    except (OSError, ValueError):
        days = None
    if days is None:
        return WARN, "local cleanup", "cleanupPeriodDays not set: Claude Code deletes sessions after ~30 days, so back up regularly"
    return OK, "local cleanup", f"cleanupPeriodDays = {days} (reports exist of it being ignored; the archive is your copy)"


def _last_backup_check(cfg) -> tuple:
    raw = rclone.cat(f"{cfg.dest}/last-backup.{cfg.machine}.json")
    if not raw:
        return WARN, "last backup", "none yet — run: cc-chronicle backup"
    try:
        data = json.loads(raw)
        when = datetime.fromisoformat(data["finished_at"])
    except (ValueError, KeyError):
        return WARN, "last backup", "stamp unreadable"
    age = datetime.now(timezone.utc) - when
    days = age.days
    detail = f"{when:%Y-%m-%d %H:%M} ({days} day(s) ago)"
    failed = data.get("failed_steps") or []
    if failed:
        return WARN, "last backup", f"{detail}, but these steps failed: {', '.join(failed)} (see the run's log)"
    return (OK if days < 7 else WARN), "last backup", detail


def run(config_path=None, out=print) -> int:
    results = checks(config_path)
    for level, name, detail in results:
        mark = {"ok": "✓", "info": "·", "warn": "!", "FAIL": "✗"}[level]
        out(f"  {mark} {name:<16} {detail}")
    return 1 if any(level == FAIL for level, _, _ in results) else 0
