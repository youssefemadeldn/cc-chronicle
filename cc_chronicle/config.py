"""Read and write the cc-chronicle config file (INI, stdlib configparser)."""
from __future__ import annotations

import configparser
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

from . import ChronicleError

INCLUDE_ALL = ("memory", "sessions", "plans", "history")
SECRETS_MODES = ("warn", "abort")
CURSOR_MODES = ("on", "off")
DEFAULT_ROOT = "cc-chronicle"
# Local time. Slow around the 10:00 and 22:00 digests; 64 KiB/s otherwise
# (about half of a 1 Mbps upload — see docs/DESIGN.md).
DEFAULT_BWLIMIT = "09:45,16k 10:30,64k 21:45,16k 22:30,64k"
# Extra tries after a network error, then seconds to wait before each one.
DEFAULT_NETWORK_RETRIES = 3
DEFAULT_NETWORK_RETRY_WAITS = (30, 90, 180)
DEFAULT_RETRIES_SLEEP = "10s"
DEFAULT_LOW_LEVEL_RETRIES = 10


def config_path() -> Path:
    if os.environ.get("CC_CHRONICLE_CONFIG"):
        return Path(os.environ["CC_CHRONICLE_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "cc-chronicle" / "config.ini"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "cc-chronicle"


def local_stamp_path(machine: str) -> Path:
    """Fallback copy of last-backup.<machine>.json when the remote write fails."""
    return state_dir() / f"last-backup.{machine}.json"


def default_claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def default_cursor_dir() -> Path:
    # CC_CHRONICLE_CURSOR_DIR lets tests aim at a temporary folder instead of the real ~/.cursor.
    return Path(os.path.expanduser(os.environ.get("CC_CHRONICLE_CURSOR_DIR") or "~/.cursor"))


def default_machine() -> str:
    name = socket.gethostname().split(".")[0].lower()
    return re.sub(r"[^a-z0-9-]+", "-", name).strip("-") or "machine"


@dataclass
class Config:
    remote: str
    root: str = DEFAULT_ROOT
    machine: str = field(default_factory=default_machine)
    claude_dir: Path = field(default_factory=default_claude_dir)
    include: tuple = INCLUDE_ALL
    secrets_on_found: str = "warn"
    sample_hashes: tuple = ()
    cursor: str = "on"
    cursor_dir: Path = field(default_factory=default_cursor_dir)
    bwlimit: str = DEFAULT_BWLIMIT
    network_retries: int = DEFAULT_NETWORK_RETRIES
    network_retry_waits: tuple = DEFAULT_NETWORK_RETRY_WAITS
    retries_sleep: str = DEFAULT_RETRIES_SLEEP
    low_level_retries: int = DEFAULT_LOW_LEVEL_RETRIES

    @property
    def dest(self) -> str:
        """The archive root as an rclone path, e.g. ``gdrive:cc-chronicle``."""
        return f"{self.remote}:{self.root}"

    def validate(self) -> None:
        if not self.remote:
            raise ChronicleError("config: 'remote' is empty")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.machine):
            raise ChronicleError(f"config: machine '{self.machine}' must be lowercase letters, digits and '-'")
        unknown = set(self.include) - set(INCLUDE_ALL)
        if unknown:
            raise ChronicleError(f"config: unknown include item(s): {', '.join(sorted(unknown))}")
        if self.secrets_on_found not in SECRETS_MODES:
            raise ChronicleError(f"config: secrets_on_found must be one of {', '.join(SECRETS_MODES)}")
        bad = [item for item in self.sample_hashes if not re.fullmatch(r"[0-9a-f]{64}", item)]
        if bad:
            raise ChronicleError("config: sample_hashes entries must be sha256 hex digests")
        if self.cursor not in CURSOR_MODES:
            raise ChronicleError(f"config: cursor must be one of {', '.join(CURSOR_MODES)}")
        if not self.bwlimit.strip():
            raise ChronicleError("config: bwlimit is empty; use off to remove the cap")
        if isinstance(self.network_retries, bool) or not isinstance(self.network_retries, int) or self.network_retries < 0:
            raise ChronicleError("config: network_retries must be a whole number")
        waits = self.network_retry_waits
        if isinstance(waits, (str, bytes)) or not waits or any(
                isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in waits):
            raise ChronicleError("config: network_retry_waits must be whole numbers of seconds")
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s|m|h)", self.retries_sleep.strip()):
            raise ChronicleError("config: retries_sleep must be a duration such as 10s")
        if isinstance(self.low_level_retries, bool) or not isinstance(self.low_level_retries, int) or self.low_level_retries < 1:
            raise ChronicleError("config: low_level_retries must be a whole number")


def _whole(section, key: str, default: int, minimum: int) -> int:
    raw = section.get(key, str(default)).strip()
    if not re.fullmatch(r"\d+", raw) or int(raw) < minimum:
        raise ChronicleError(f"config: {key} must be a whole number")
    return int(raw)


def _waits(section) -> tuple:
    raw = section.get("network_retry_waits", ",".join(str(n) for n in DEFAULT_NETWORK_RETRY_WAITS)).strip()
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts or any(not re.fullmatch(r"\d+", item) for item in parts):
        raise ChronicleError("config: network_retry_waits must be whole numbers of seconds, comma-separated")
    return tuple(int(item) for item in parts)


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.is_file():
        raise ChronicleError(f"no config at {path} — run: cc-chronicle init")
    parser = configparser.ConfigParser()
    parser.read(path)
    if "cc-chronicle" not in parser:
        raise ChronicleError(f"{path}: missing [cc-chronicle] section")
    section = parser["cc-chronicle"]
    include = section.get("include", ",".join(INCLUDE_ALL))
    cfg = Config(
        remote=section.get("remote", "").strip().rstrip(":"),
        root=section.get("root", DEFAULT_ROOT).strip().rstrip("/"),
        machine=section.get("machine", default_machine()).strip(),
        claude_dir=Path(os.path.expanduser(section.get("claude_dir", str(default_claude_dir())).strip())),
        include=tuple(item.strip() for item in include.split(",") if item.strip()),
        secrets_on_found=section.get("secrets_on_found", "warn").strip(),
        sample_hashes=tuple(
            item.strip().lower() for item in section.get("sample_hashes", "").split(",") if item.strip()),
        cursor=section.get("cursor", "on").strip(),
        cursor_dir=Path(os.path.expanduser(section.get("cursor_dir", str(default_cursor_dir())).strip())),
        bwlimit=section.get("bwlimit", DEFAULT_BWLIMIT).strip(),
        network_retries=_whole(section, "network_retries", DEFAULT_NETWORK_RETRIES, minimum=0),
        network_retry_waits=_waits(section),
        retries_sleep=section.get("retries_sleep", DEFAULT_RETRIES_SLEEP).strip(),
        low_level_retries=_whole(section, "low_level_retries", DEFAULT_LOW_LEVEL_RETRIES, minimum=1),
    )
    cfg.validate()
    return cfg


def save(cfg: Config, path: Path | None = None) -> Path:
    cfg.validate()
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["cc-chronicle"] = {
        "remote": cfg.remote,
        "root": cfg.root,
        "machine": cfg.machine,
        "claude_dir": str(cfg.claude_dir),
        "include": ",".join(cfg.include),
        "secrets_on_found": cfg.secrets_on_found,
        "sample_hashes": ",".join(cfg.sample_hashes),
        "cursor": cfg.cursor,
        "cursor_dir": str(cfg.cursor_dir),
        "bwlimit": cfg.bwlimit,
        "network_retries": str(cfg.network_retries),
        "network_retry_waits": ",".join(str(n) for n in cfg.network_retry_waits),
        "retries_sleep": cfg.retries_sleep,
        "low_level_retries": str(cfg.low_level_retries),
    }
    with open(path, "w") as fh:
        fh.write("# cc-chronicle config. No secrets here: credentials stay in rclone.conf.\n")
        fh.write("# sample_hashes, when set, are sha256 digests only.\n")
        parser.write(fh)
    return path
