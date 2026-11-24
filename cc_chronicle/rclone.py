"""Thin wrapper around the rclone command line. Every rclone call goes through here."""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from . import ChronicleError
from .config import (
    DEFAULT_LOW_LEVEL_RETRIES,
    DEFAULT_NETWORK_RETRIES,
    DEFAULT_NETWORK_RETRY_WAITS,
    DEFAULT_RETRIES_SLEEP,
)

# --timeout 60s: after a network switch, stalled connections are dropped and retried
# within a minute instead of rclone's default five.
TRANSFER_FLAGS = ["--fast-list", "--transfers", "8", "--timeout", "60s"]
SKIP_DS_STORE = ["--filter", "- .DS_Store"]
_ACTION = re.compile(r"Copied|Skipped (copy|delete|move|update)|Deleted|Moved")
_GLOB_SPECIAL = re.compile(r"([\\*?\[\]{}])")


class RcloneError(ChronicleError):
    def __init__(self, message, network=False):
        super().__init__(message)
        self.network = bool(network)


# A failed lookup or a dropped connection. "timeout" is the error word, not the --timeout flag.
_NETWORK_MARKERS = (
    "no such host",
    "connection reset",
    "connection refused",
    "couldn't find root directory id",
)


def is_network_error(text: str) -> bool:
    low = (text or "").lower()
    if any(marker in low for marker in _NETWORK_MARKERS):
        return True
    return re.search(r"(?<![-])\btimeout\b", low) is not None


def _retry_settings(network_retries, network_retry_waits, retries_sleep, low_level_retries):
    retries = DEFAULT_NETWORK_RETRIES if network_retries is None else network_retries
    waits = DEFAULT_NETWORK_RETRY_WAITS if network_retry_waits is None else tuple(network_retry_waits)
    sleep_flag = DEFAULT_RETRIES_SLEEP if retries_sleep is None else retries_sleep
    low = DEFAULT_LOW_LEVEL_RETRIES if low_level_retries is None else low_level_retries
    return retries, waits or (0,), sleep_flag, low


def call_with_network_retries(fn, *, retries, waits, on_retry=None):
    """Run fn. On a network error, wait and try again. Any other error is immediate."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except RcloneError as exc:
            network = exc.network or is_network_error(str(exc))
            if not network or attempt >= retries:
                raise
            wait = waits[min(attempt, len(waits) - 1)]
            if on_retry is not None:
                on_retry(wait)
            time.sleep(wait)
    raise RcloneError("rclone failed: no output")


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
        raise RcloneError(f"rclone {args[0]} failed: {_tail(proc.stderr)}", network=is_network_error(proc.stderr))
    return proc


def transfer(command: str, src: str, dst: str, *extra: str, log_file: Path, dry_run: bool = False,
             progress: bool = False, allow_missing: bool = False, bwlimit: str | None = None,
             network_retries: int | None = None, network_retry_waits: tuple | None = None,
             retries_sleep: str | None = None, low_level_retries: int | None = None,
             on_retry=None) -> int:
    """Run one copy/sync/copyto step, append its log to log_file, return the number of file actions.

    allow_missing: a source that doesn't exist counts as 0 actions instead of an error.
    bwlimit: rclone --bwlimit value for this upload. "off" or empty means no cap.
    Read-only commands go through run() and never get a cap.
    A network error is tried again (network_retries more times). Any other error is not.
    """
    retries, waits, sleep_flag, low = _retry_settings(
        network_retries, network_retry_waits, retries_sleep, low_level_retries)

    def once():
        return _transfer_once(command, src, dst, *extra, log_file=log_file, dry_run=dry_run,
                              progress=progress, allow_missing=allow_missing, bwlimit=bwlimit,
                              retries_sleep=sleep_flag, low_level_retries=low)

    return call_with_network_retries(once, retries=retries, waits=waits, on_retry=on_retry)


def _transfer_once(command: str, src: str, dst: str, *extra: str, log_file: Path, dry_run: bool = False,
                   progress: bool = False, allow_missing: bool = False, bwlimit: str | None = None,
                   retries_sleep: str = DEFAULT_RETRIES_SLEEP,
                   low_level_retries: int = DEFAULT_LOW_LEVEL_RETRIES) -> int:
    args = [command, src, dst]
    if command != "copyto":  # rclone refuses filters on a single-file copy
        args += SKIP_DS_STORE
    args += [*extra, *TRANSFER_FLAGS, "-v"]
    cap = (bwlimit or "").strip()
    if cap and cap != "off":
        args += ["--bwlimit", cap]
    args += ["--retries-sleep", retries_sleep, "--low-level-retries", str(low_level_retries)]
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
        blob = text + proc.stderr
        if allow_missing and "not found" in blob.lower():
            return 0
        raise RcloneError(
            f"rclone {command} failed: {_tail(blob)} (log: {log_file})",
            network=is_network_error(blob),
        )
    return sum(1 for line in text.splitlines() if _ACTION.search(line))


def cat(path: str) -> str | None:
    """Return a small remote file's content, or None if it doesn't exist."""
    proc = run(["cat", path], check=False)
    return proc.stdout if proc.returncode == 0 else None


def rcat(path: str, text: str, *, network_retries: int | None = None,
         network_retry_waits: tuple | None = None, retries_sleep: str | None = None,
         low_level_retries: int | None = None, on_retry=None) -> None:
    retries, waits, sleep_flag, low = _retry_settings(
        network_retries, network_retry_waits, retries_sleep, low_level_retries)

    def once():
        run(["rcat", path, "--retries-sleep", sleep_flag, "--low-level-retries", str(low)],
            input_text=text)

    call_with_network_retries(once, retries=retries, waits=waits, on_retry=on_retry)


def list_remotes() -> list:
    return [line.rstrip(":") for line in run(["listremotes"]).stdout.split()]


def _tail(text: str, lines: int = 3) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return " | ".join(kept[-lines:]) or "no output"
