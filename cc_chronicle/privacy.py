"""Check that the archive folder isn't shared, using rclone's Drive permission metadata.

Only Google Drive exposes sharing through rclone; other backends are reported as
not checked.
"""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field

from . import rclone
from .config import Config


@dataclass
class PrivacyResult:
    status: str  # "private" | "shared" | "skipped"
    detail: str = ""
    shared_with: list = field(default_factory=list)


def remote_type(remote: str) -> str:
    proc = rclone.run(["config", "show", remote], check=False)
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "type":
            return value.strip()
    return ""


def shared_entries(permissions: list) -> list:
    """Every permission other than the owner, as short readable strings."""
    found = []
    for p in permissions:
        if p.get("role") == "owner":
            continue
        who = p.get("emailAddress") or p.get("domain") or p.get("type", "?")
        found.append(f"{p.get('type', '?')}:{who} ({p.get('role', '?')})")
    return found


def check(cfg: Config) -> PrivacyResult:
    kind = remote_type(cfg.remote)
    if kind != "drive":
        return PrivacyResult("skipped", f"sharing can only be checked on Google Drive (remote type: {kind or 'unknown'})")
    parent, name = posixpath.split(cfg.root.strip("/"))
    if not name:
        return PrivacyResult("skipped", "the archive is the whole remote; nothing to check")
    proc = rclone.run(["lsjson", "-M", "--drive-metadata-permissions", "read", "--dirs-only",
                       f"{cfg.remote}:{parent}"], check=False)
    if proc.returncode != 0:
        return PrivacyResult("skipped", f"could not read permissions: {proc.stderr.strip()[-200:]}")
    for entry in json.loads(proc.stdout or "[]"):
        if entry.get("Name") == name:
            raw = (entry.get("Metadata") or {}).get("permissions") or "[]"
            others = shared_entries(json.loads(raw))
            if others:
                return PrivacyResult("shared", "the archive folder is shared", others)
            return PrivacyResult("private", "only the owner has access")
    return PrivacyResult("skipped", "archive folder not found (no backup yet?)")
