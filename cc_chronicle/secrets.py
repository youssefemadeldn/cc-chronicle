"""Find likely secrets (API keys, tokens, private keys) in the files a backup uploads.

Only pattern names, file paths and counts are ever reported. Matched values are never
printed, logged or stored. Results are cached by file size and mtime, so a run only
re-reads files that changed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, state_dir

# name → (literal anchors, regex). A pattern's regex only runs on text that contains one of
# its anchors: a plain substring search is far cheaper, and most files contain none of them.
PATTERNS = {
    # Key-like tokens must start a word and contain a digit, so prose such as
    # "task-make-the-backup-work" doesn't count.
    "anthropic-key": (("sk-ant-",), r"(?<![A-Za-z0-9_-])sk-ant-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}"),
    "openai-key": (("sk-",), r"(?<![A-Za-z0-9_-])sk-(?!ant-)(?:proj-)?(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{32,}"),
    "github-token": (("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
                     r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}"),
    "google-api-key": (("AIza",), r"AIza[0-9A-Za-z_-]{35}"),
    "aws-access-key": (("AKIA", "ASIA"), r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    "slack-token": (("xox",), r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    "stripe-live-key": (("k_live_",), r"[sr]k_live_[0-9A-Za-z]{20,}"),
    "private-key": (("PRIVATE KEY-----",), r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "jwt": (("eyJ",), r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
_COMPILED = [(name, anchors, re.compile(rx)) for name, (anchors, rx) in PATTERNS.items()]
_CACHE_VERSION = 1


def scan_text(text: str) -> dict:
    """Pattern name → number of matches."""
    counts = {}
    for name, anchors, regex in _COMPILED:
        if any(anchor in text for anchor in anchors):
            n = sum(1 for _ in regex.finditer(text))
            if n:
                counts[name] = n
    return counts


@dataclass
class ScanReport:
    files: int = 0
    rescanned: int = 0
    findings: dict = field(default_factory=dict)  # relative path → {pattern: count}
    new_findings: dict = field(default_factory=dict)  # same, only files re-read this run

    @property
    def total(self) -> int:
        return sum(sum(c.values()) for c in self.findings.values())

    def by_pattern(self, which: str = "findings") -> dict:
        totals = {}
        for counts in getattr(self, which).values():
            for name, n in counts.items():
                totals[name] = totals.get(name, 0) + n
        return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def files_to_scan(cfg: Config) -> list:
    """Every local file a backup with this config uploads."""
    claude = cfg.claude_dir
    found = []
    if "memory" in cfg.include:
        found += [p for p in (claude / "projects").glob("*/memory/**/*") if p.is_file()]
    if "sessions" in cfg.include:
        for pdir in (claude / "projects").glob("*"):
            if pdir.is_dir():
                found += [p for p in pdir.rglob("*") if p.is_file() and "memory" not in p.relative_to(pdir).parts[:1]]
        found += [p for p in (claude / "file-history").rglob("*") if p.is_file()]
    if "plans" in cfg.include:
        found += [p for p in (claude / "plans").rglob("*") if p.is_file()]
    if "history" in cfg.include and (claude / "history.jsonl").is_file():
        found.append(claude / "history.jsonl")
    return sorted(p for p in found if p.name != ".DS_Store")


def scan(cfg: Config) -> ScanReport:
    cache_file = state_dir() / "secret-scan.json"
    try:
        cache = json.loads(cache_file.read_text())
        if cache.get("version") != _CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    entries = cache.get("files", {})
    fresh = {}
    report = ScanReport()

    for path in files_to_scan(cfg):
        key = str(path)
        st = path.stat()
        stamp = [st.st_size, st.st_mtime_ns]
        cached = entries.get(key)
        if cached and cached[0] == stamp:
            counts = cached[1]
        else:
            counts = scan_text(path.read_text(errors="ignore"))
            report.rescanned += 1
            if counts:
                report.new_findings[str(path.relative_to(cfg.claude_dir))] = counts
        fresh[key] = [stamp, counts]
        report.files += 1
        if counts:
            report.findings[str(path.relative_to(cfg.claude_dir))] = counts

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"version": _CACHE_VERSION, "files": fresh}))
    return report


def summary_line(report: ScanReport) -> str:
    if not report.findings:
        return f"no likely secrets in {report.files} file(s)"
    kinds = ", ".join(f"{name} ×{n}" for name, n in report.by_pattern().items())
    new = f", {len(report.new_findings)} new or changed" if report.new_findings else ""
    return f"{report.total} likely secret(s) in {len(report.findings)} file(s){new}: {kinds}"
