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
DEFAULT_ROOT = "cc-chronicle"


def config_path() -> Path:
    if os.environ.get("CC_CHRONICLE_CONFIG"):
        return Path(os.environ["CC_CHRONICLE_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "cc-chronicle" / "config.ini"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "cc-chronicle"


def default_claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


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
    }
    with open(path, "w") as fh:
        fh.write("# cc-chronicle config. No secrets here: credentials stay in rclone.conf.\n")
        parser.write(fh)
    return path
