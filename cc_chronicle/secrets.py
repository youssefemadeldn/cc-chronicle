"""Find likely secrets (API keys, tokens, private keys) in the files a backup uploads.

Only pattern names, file paths and counts are ever reported. Matched values are never
printed, logged or stored. A documented example, an obvious fake, or a sha256 listed
in the config is a sample: counted separately, not a finding. Results are cached by
file size and mtime, so a run only re-reads files that changed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import ChronicleError
from .config import Config, save, state_dir

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
_CACHE_VERSION = 2

# A match is a sample when it is a published example, or an obvious fake: the letters
# EXAMPLE, TEST, FAKE or XXXX appear in it, it contains 8 or more of one character in
# a row, or it contains an ascending run of 8 or more (012…, abc…, ABC…). Real keys are
# mixed, so the rule stays this small. The PEM header is the same for every private key,
# so it is not an example. The AWS docs also publish a secret access key; this scanner
# has no pattern for that shape, so the secret is not a finding and is not listed here.
_MARKERS = ("EXAMPLE", "TEST", "FAKE", "XXXX")
_RUN = 8
# AWS IAM "Managing access keys" example id. jwt.io's sample token.
DOCUMENTED = {
    "aws-access-key": frozenset({"AK" + "IA" + "IOSFODNN7" + "EXAMPLE"}),
    "jwt": frozenset({
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    }),
}


def _long_run(value: str, ascending: bool) -> bool:
    run = 1
    for prev, cur in zip(value, value[1:]):
        same = ord(cur) == ord(prev) + 1 if ascending else cur == prev
        if same:
            run += 1
            if run >= _RUN:
                return True
        else:
            run = 1
    return False


def _obvious_fake(value: str) -> bool:
    if any(marker in value for marker in _MARKERS):
        return True
    return _long_run(value, False) or _long_run(value, True)


def _hash_set(sample_hashes) -> set:
    if isinstance(sample_hashes, (set, frozenset)):
        return set(sample_hashes)
    return set(sample_hashes)


def _is_sample(name: str, value: str, sample_hashes: set) -> bool:
    if value in DOCUMENTED.get(name, ()):
        return True
    if _obvious_fake(value):
        return True
    return hashlib.sha256(value.encode()).hexdigest() in sample_hashes


def _iter_matches(text: str):
    for name, anchors, regex in _COMPILED:
        if any(anchor in text for anchor in anchors):
            for match in regex.finditer(text):
                yield name, match.group(0)


def classify_text(text: str, sample_hashes=()) -> tuple:
    """(finding counts, sample counts). Values are not retained."""
    hashes = _hash_set(sample_hashes)
    findings, samples = {}, {}
    for name, value in _iter_matches(text):
        bucket = samples if _is_sample(name, value, hashes) else findings
        bucket[name] = bucket.get(name, 0) + 1
    return findings, samples


def scan_text(text: str, sample_hashes=()) -> dict:
    """Pattern name → number of findings. Samples are not included."""
    findings, _samples = classify_text(text, sample_hashes)
    return findings


def _merge(into: dict, found: dict) -> None:
    for name, n in found.items():
        into[name] = into.get(name, 0) + n


def _sqlite_texts(path: Path):
    """Text stored in a SQLite file, or None if it cannot be opened."""
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    texts = []
    try:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            try:
                cursor = conn.execute(f"SELECT * FROM {quoted}")
            except sqlite3.Error:
                continue
            for row in cursor:
                for cell in row:
                    if isinstance(cell, str):
                        texts.append(cell)
                    elif isinstance(cell, bytes):
                        texts.append(cell.decode("utf-8", "ignore"))
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return texts


def _texts(path: Path) -> list:
    if path.name == "store.db" or path.suffix == ".db":
        texts = _sqlite_texts(path)
        if texts is not None:
            return texts
        return [path.read_bytes().decode("utf-8", "ignore")]
    return [path.read_text(errors="ignore")]


def _scan_path(path: Path, sample_hashes) -> tuple:
    findings, samples = {}, {}
    for text in _texts(path):
        found, sample = classify_text(text, sample_hashes)
        _merge(findings, found)
        _merge(samples, sample)
    return findings, samples


def scan_file(path: Path, sample_hashes=()) -> dict:
    """Scan one file. A SQLite database is scanned from the text it stores, not as raw bytes."""
    findings, _samples = _scan_path(path, sample_hashes)
    return findings


def _cursor_on(cfg: Config) -> bool:
    return cfg.cursor == "on" and cfg.cursor_dir.is_dir()


def _skip_cursor(path: Path) -> bool:
    return path.name == ".DS_Store" or path.name.endswith(("-wal", "-shm"))


def finding_path(cfg: Config, path: Path) -> str:
    try:
        return str(path.relative_to(cfg.claude_dir))
    except ValueError:
        pass
    try:
        return ".cursor/" + str(path.relative_to(cfg.cursor_dir))
    except ValueError:
        return str(path)


@dataclass
class ScanReport:
    files: int = 0
    rescanned: int = 0
    findings: dict = field(default_factory=dict)  # relative path → {pattern: count}
    new_findings: dict = field(default_factory=dict)  # same, only files re-read this run
    samples: dict = field(default_factory=dict)  # relative path → {pattern: count}

    @property
    def total(self) -> int:
        return sum(sum(c.values()) for c in self.findings.values())

    @property
    def sample_total(self) -> int:
        return sum(sum(c.values()) for c in self.samples.values())

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
    if _cursor_on(cfg):
        for folder in ("chats", "projects"):
            base = cfg.cursor_dir / folder
            if base.is_dir():
                found += [p for p in base.rglob("*") if p.is_file() and not _skip_cursor(p)]
    return sorted(p for p in found if p.name != ".DS_Store")


def _sample_stamp(sample_hashes) -> str:
    payload = "\n".join(sorted(sample_hashes))
    return hashlib.sha256(payload.encode()).hexdigest()


def scan(cfg: Config) -> ScanReport:
    sample_hashes = tuple(cfg.sample_hashes)
    stamp_id = _sample_stamp(sample_hashes)
    cache_file = state_dir() / "secret-scan.json"
    try:
        cache = json.loads(cache_file.read_text())
        if cache.get("version") != _CACHE_VERSION or cache.get("sample_set") != stamp_id:
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
        rel = finding_path(cfg, path)
        if cached and len(cached) == 3 and cached[0] == stamp:
            findings, samples = cached[1], cached[2]
        else:
            findings, samples = _scan_path(path, sample_hashes)
            report.rescanned += 1
            if findings:
                report.new_findings[rel] = findings
        fresh[key] = [stamp, findings, samples]
        report.files += 1
        if findings:
            report.findings[rel] = findings
        if samples:
            report.samples[rel] = samples

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"version": _CACHE_VERSION, "sample_set": stamp_id, "files": fresh}))
    return report


def summary_line(report: ScanReport) -> str:
    sample = f"{report.sample_total} sample(s)" if report.sample_total else ""
    if not report.findings:
        base = f"no likely secrets in {report.files} file(s)"
        return f"{base}; {sample}" if sample else base
    kinds = ", ".join(f"{name} ×{n}" for name, n in report.by_pattern().items())
    new = f", {len(report.new_findings)} new or changed" if report.new_findings else ""
    line = f"{report.total} likely secret(s) in {len(report.findings)} file(s){new}: {kinds}"
    if sample:
        line += f"; {sample}"
    return line


def _hit_line(kind: str, digest: str, group: dict) -> str:
    files = ", ".join(group["files"])
    return f"{kind}  {digest[:12]}  ×{group['count']}  {files}"


def _unknown_hits(cfg: Config, kind: str) -> dict:
    """sha256 → {count, files} for hits of this kind that are not already samples."""
    known_hashes = set(cfg.sample_hashes)
    groups = {}
    for path in files_to_scan(cfg):
        rel = finding_path(cfg, path)
        for text in _texts(path):
            for name, value in _iter_matches(text):
                if name != kind or _is_sample(name, value, known_hashes):
                    continue
                digest = hashlib.sha256(value.encode()).hexdigest()
                group = groups.setdefault(digest, {"count": 0, "files": []})
                group["count"] += 1
                if rel not in group["files"]:
                    group["files"].append(rel)
    return groups


def _matches_for(token: str, groups: dict) -> list:
    return [digest for digest in groups if digest.startswith(token)]


def _save_hashes(cfg: Config, chosen: list) -> None:
    cfg.sample_hashes = tuple(sorted(set(cfg.sample_hashes).union(chosen)))
    path = save(cfg)
    print(f"saved {len(chosen)} sample hash(es) to {path}")


def mark_samples(cfg: Config, kind: str, *, yes: bool = False, list_only: bool = False,
                 only_hashes: str | None = None) -> None:
    """Store sha256 hashes of unknown hits of one kind. The value is never printed."""
    if kind not in PATTERNS:
        known = ", ".join(PATTERNS)
        raise ChronicleError(f"unknown secret kind '{kind}' (known: {known})")
    groups = _unknown_hits(cfg, kind)
    if list_only:
        if not groups:
            print(f"no unknown {kind} hits")
            return
        for digest in sorted(groups, key=lambda item: (-groups[item]["count"], item)):
            print(_hit_line(kind, digest, groups[digest]))
        return
    if only_hashes is not None:
        tokens = [item.strip().lower() for item in only_hashes.split(",") if item.strip()]
        if not tokens:
            raise ChronicleError("--hash needs at least one short hash")
        chosen = []
        for token in tokens:
            if not re.fullmatch(r"[0-9a-f]+", token):
                raise ChronicleError(f"unknown sample hash '{token}'")
            matches = _matches_for(token, groups)
            if not matches:
                raise ChronicleError(f"unknown sample hash '{token}'")
            if len(matches) > 1:
                listed = "\n".join(f"  {_hit_line(kind, digest, groups[digest])}" for digest in sorted(matches))
                raise ChronicleError(f"ambiguous sample hash '{token}' matches:\n{listed}")
            if matches[0] not in chosen:
                chosen.append(matches[0])
        for digest in chosen:
            print(f"marked {_hit_line(kind, digest, groups[digest])}")
        _save_hashes(cfg, chosen)
        return
    if not groups:
        print(f"no unknown {kind} hits")
        return
    if not yes and not sys.stdin.isatty():
        raise ChronicleError("--yes is required to mark samples when not running interactively")
    chosen = []
    ended = False
    for digest in sorted(groups):
        line = _hit_line(kind, digest, groups[digest])
        if yes:
            print(f"marked {line}")
            chosen.append(digest)
            continue
        try:
            answer = input(f"mark {line}? [y/N] ").strip().lower()
        except EOFError:
            ended = True
            break
        if answer == "y":
            chosen.append(digest)
    if chosen:
        _save_hashes(cfg, chosen)
    else:
        print("no samples marked")
    if ended:
        print(summary_line(scan(cfg)))
