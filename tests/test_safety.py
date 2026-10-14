import json

import pytest
from conftest import PROJECT, SEP, needs_rclone, quiet, write

from cc_chronicle import ChronicleError, doctor, privacy, secrets
from cc_chronicle.backup import backup
from cc_chronicle.cli import main

# Fake keys are assembled at runtime so this file never contains a key-shaped string.
FAKE = {
    "anthropic-key": "sk-" + "ant-" + "api03-" + "A1" * 20,
    "openai-key": "sk-" + "proj-" + "B2" * 20,
    "github-token": "gh" + "p_" + "C" * 36,
    "google-api-key": "AI" + "za" + "D" * 35,
    "aws-access-key": "AK" + "IA" + "E" * 16,
    "slack-token": "xo" + "xb-" + "1234567890-abcdef",
    "stripe-live-key": "sk" + "_live_" + "F" * 24,
    "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "jwt": "ey" + "J" + "a" * 12 + ".ey" + "J" + "b" * 12 + "." + "c" * 12,
}


@pytest.mark.parametrize("name", list(FAKE))
def test_each_pattern_is_detected(name):
    assert secrets.scan_text(f"config: {FAKE[name]} end") == {name: 1}


def test_ordinary_text_is_clean():
    assert secrets.scan_text("sk-short, eyJ.not.a.jwt, AKIA lowercase akiaabc, skeleton keys") == {}


def test_kebab_case_prose_is_not_a_key():
    # found as false positives in real transcripts: "task-…" slugs inside longer words
    assert secrets.scan_text("the task-make-the-backup-skill-and-the-plan-work-today") == {}
    assert secrets.scan_text("sk-make-the-backup-skill-and-the-plan-work-today-now") == {}


def test_anthropic_key_is_not_double_counted_as_openai():
    assert secrets.scan_text(FAKE["anthropic-key"]) == {"anthropic-key": 1}


def test_scan_covers_every_uploaded_file_and_uses_the_cache(env):
    cfg, claude, _archive = env
    write(claude / "projects" / PROJECT / SEP / "tool-results" / "env.txt", f"KEY={FAKE['google-api-key']}\n")
    first = secrets.scan(cfg)
    assert first.findings == {f"projects/{PROJECT}/{SEP}/tool-results/env.txt": {"google-api-key": 1}}
    assert first.rescanned == first.files and first.new_findings == first.findings
    again = secrets.scan(cfg)
    assert again.rescanned == 0 and again.new_findings == {} and again.findings == first.findings


def test_scan_output_never_contains_secret_values(env, capsys):
    cfg, claude, _archive = env
    write(claude / "plans" / "leak.md", " ".join(FAKE.values()))
    write(claude.parent / "config.ini", "")
    main(["init", "--remote", "t", "--root", str(cfg.root), "--machine", "mac", "--claude-dir", str(claude), "--force"])
    main(["scan"])
    out = capsys.readouterr().out
    assert "plans/leak.md" in out
    for value in FAKE.values():
        assert value not in out


@needs_rclone
def test_abort_mode_uploads_nothing(env):
    cfg, claude, archive = env
    write(claude / "projects" / PROJECT / "memory" / "note.md", f"token {FAKE['github-token']}\n")
    cfg.secrets_on_found = "abort"
    with pytest.raises(ChronicleError, match="nothing was uploaded"):
        backup(cfg, out=quiet)
    assert not archive.exists()


@needs_rclone
def test_warn_mode_uploads_and_reports(env):
    cfg, claude, archive = env
    write(claude / "projects" / PROJECT / "memory" / "note.md", f"token {FAKE['github-token']}\n")
    result = backup(cfg, out=quiet)
    assert result.secrets.total == 1
    assert (archive / ".claude" / "projects" / PROJECT / "memory" / "note.md").is_file()


@needs_rclone
def test_strict_only_blocks_new_findings(env):
    cfg, claude, _archive = env
    write(claude / "plans" / "old.md", FAKE["aws-access-key"])
    backup(cfg, out=quiet)  # warn mode: uploaded, now cached
    result = backup(cfg, strict_secrets=True, out=quiet)  # nothing new → allowed
    assert result.secrets.total == 1 and result.secrets.new_findings == {}


def test_shared_entries_ignore_the_owner():
    perms = [{"role": "owner", "type": "user", "emailAddress": "me@x.com"},
             {"role": "reader", "type": "anyone"},
             {"role": "writer", "type": "user", "emailAddress": "friend@x.com"}]
    assert privacy.shared_entries(perms) == ["anyone:anyone (reader)", "user:friend@x.com (writer)"]
    assert privacy.shared_entries(perms[:1]) == []


@needs_rclone
def test_privacy_check_skips_non_drive_remotes(env):
    cfg, _claude, _archive = env
    result = privacy.check(cfg)
    assert result.status == "skipped" and "local" in result.detail


@needs_rclone
def test_doctor_reports_each_check(env):
    cfg, claude, _archive = env
    write(claude / "settings.json", json.dumps({"cleanupPeriodDays": 365}))
    main(["init", "--remote", "t", "--root", str(cfg.root), "--machine", "mac", "--claude-dir", str(claude)])
    lines = []
    assert doctor.run(out=lines.append) == 0
    text = "\n".join(lines)
    for name in ("python", "rclone", "config", "claude dir", "local cleanup", "remote", "archive sharing",
                 "last backup"):
        assert name in text
    assert "cleanupPeriodDays = 365" in text
    assert "none yet" in text
    backup(cfg, out=quiet)
    lines.clear()
    doctor.run(out=lines.append)
    assert "0 day(s) ago" in "\n".join(lines)
