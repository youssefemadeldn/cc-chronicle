"""Thin wrapper around the rclone command line. Every rclone call goes through here."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import ChronicleError

# --timeout 60s: after a network switch, stalled connections are dropped and retried
# within a minute instead of rclone's default five.
TRANSFER_FLAGS = ["--fast-list", "--transfers", "8", "--timeout", "60s"]
SKIP_DS_STORE = ["--filter", "- .DS_Store"]
_ACTION = re.compile(r"Copied|Skipped (copy|delete|move|update)|Deleted|Moved")
_GLOB_SPECIAL = re.compile(r"([\\*?\[\]{}])")


class RcloneError(ChronicleError):
    pass


def binary() -> str:
    path = shutil.which("rclone")
    if not path:
        raise RcloneError("rclone not found — install it: https://rclone.org/install/")
    return path


def escape_glob(name: str) -> str:
    """Escape a literal path segment for use inside an rclone filter rule."""
    return _GLOB_SPECIAL.sub(r"\\\1", name)


def run(args: list, *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run rclone, capturing its output."""
    proc = subprocess.run([binary(), *args], input=input_text, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RcloneError(f"rclone {args[0]} failed: {_tail(proc.stderr)}")
    return proc


def transfer(command: str, src: str, dst: str, *extra: str, log_file: Path, dry_run: bool = False,
             progress: bool = False, allow_missing: bool = False) -> int:
    """Run one copy/sync/copyto step, append its log to log_file, return the number of file actions.

    allow_missing: a source that doesn't exist counts as 0 actions instead of an error.
    """
    args = [command, src, dst]
    if command != "copyto":  # rclone refuses filters on a single-file copy
        args += SKIP_DS_STORE
    args += [*extra, *TRANSFER_FLAGS, "-v"]
    if dry_run:
        args.append("--dry-run")
    if progress:
        args.append("--progress")
    step_log = log_file.with_name(log_file.name + ".step")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([binary(), *args, "--log-file", str(step_log)],
                          stdout=None if progress else subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    text = step_log.read_text(errors="replace") if step_log.exists() else ""
    step_log.unlink(missing_ok=True)
    with open(log_file, "a") as fh:
        fh.write(f"\n### rclone {' '.join(args)}\n{text}{proc.stderr}")
    if proc.returncode != 0:
        if allow_missing and "not found" in (text + proc.stderr).lower():
            return 0
        raise RcloneError(f"rclone {command} failed: {_tail(text + proc.stderr)} (log: {log_file})")
    return sum(1 for line in text.splitlines() if _ACTION.search(line))


def cat(path: str) -> str | None:
    """Return a small remote file's content, or None if it doesn't exist."""
    proc = run(["cat", path], check=False)
    return proc.stdout if proc.returncode == 0 else None


def rcat(path: str, text: str) -> None:
    run(["rcat", path], input_text=text)


def list_remotes() -> list:
    return [line.rstrip(":") for line in run(["listremotes"]).stdout.split()]


def _tail(text: str, lines: int = 3) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return " | ".join(kept[-lines:]) or "no output"
