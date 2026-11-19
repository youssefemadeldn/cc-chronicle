import hashlib
import json
import os
import sys

import pytest
from conftest import PROJECT, SEP, needs_rclone, quiet, write

from cc_chronicle import ChronicleError, doctor, privacy, secrets
from cc_chronicle.backup import backup
from cc_chronicle.cli import main
from cc_chronicle.config import load, save, state_dir

# Fake keys are assembled at runtime so this file never contains a key-shaped string.
FAKE = {
    "anthropic-key": "sk-" + "ant-" + "api03-" + "A1" * 20,
    "openai-key": "sk-" + "proj-" + "B2" * 20,
    "github-token": "gh" + "p_" + "C3a" * 12,
    "google-api-key": "AI" + "za" + "D4e" * 11 + "D4",
    "aws-access-key": "AK" + "IA" + "E5FG" * 4,
    "slack-token": "xo" + "xb-" + "1a2b3c4d5e6f7g8h",
    "stripe-live-key": "sk" + "_live_" + "F6gH" * 6,
    "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "jwt": "ey" + "J" + "a1B2c3D4e5F6" + ".ey" + "J" + "g7H8i9J0k1L2" + "." + "m3N4o5P6q7R8",
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
    for name in ("python", "rclone", "config", "claude dir", "cursor", "local cleanup", "secrets",
                 "remote", "archive sharing", "last backup"):
        assert name in text
    assert "cleanupPeriodDays = 365" in text
    assert "none yet" in text
    backup(cfg, out=quiet)
    lines.clear()
    doctor.run(out=lines.append)
    assert "0 day(s) ago" in "\n".join(lines)


def test_documented_examples_are_samples():
    for kind, values in secrets.DOCUMENTED.items():
        for value in values:
            findings, samples = secrets.classify_text(value)
            assert findings == {}
            assert samples == {kind: 1}


def test_repeated_character_fake_is_a_sample():
    value = "AK" + "IA" + "E" * 16
    findings, samples = secrets.classify_text(f"key {value} end")
    assert findings == {}
    assert samples == {"aws-access-key": 1}


def test_user_hash_is_a_sample_and_another_value_stays_a_finding():
    sample = "AK" + "IA" + "E5FG" * 4
    other = "AK" + "IA" + "Z9QM" * 4
    digest = hashlib.sha256(sample.encode()).hexdigest()
    findings, samples = secrets.classify_text(sample, [digest])
    assert findings == {} and samples == {"aws-access-key": 1}
    assert secrets.scan_text(other, [digest]) == {"aws-access-key": 1}


def test_mark_samples_stores_only_hashes(env, capsys, monkeypatch):
    cfg, claude, _archive = env
    keep = "AK" + "IA" + "E5FG" * 4
    drop = "AK" + "IA" + "Z9QM" * 4
    write(claude / "projects" / PROJECT / f"{SEP}.jsonl", keep + "\n" + drop + "\n")
    save(cfg)
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "y" if hashlib.sha256(keep.encode()).hexdigest()[:12] in prompt else "n"

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert main(["scan", "--mark-samples", "aws-access-key"]) == 0
    out = capsys.readouterr().out
    assert keep not in out and drop not in out
    assert all(keep not in prompt and drop not in prompt for prompt in prompts)
    digest = hashlib.sha256(keep.encode()).hexdigest()
    loaded = load()
    assert loaded.sample_hashes == (digest,)
    config_text = open(os.environ["CC_CHRONICLE_CONFIG"]).read()
    assert keep not in config_text and drop not in config_text and digest in config_text
    report = secrets.scan(loaded)
    assert report.total == 1 and report.sample_total == 1
    cache = (state_dir() / "secret-scan.json").read_text()
    assert keep not in cache and drop not in cache
    assert "1 sample(s)" in secrets.summary_line(report)


def test_mark_samples_yes_marks_every_unknown_hit(env, capsys):
    cfg, claude, _archive = env
    value = "AK" + "IA" + "M4QZ" * 4
    write(claude / "plans" / "note.md", value)
    save(cfg)
    assert main(["scan", "--mark-samples", "aws-access-key", "--yes"]) == 0
    out = capsys.readouterr().out
    digest = hashlib.sha256(value.encode()).hexdigest()
    assert value not in out
    assert f"marked aws-access-key  {digest[:12]}" in out
    assert load().sample_hashes == (digest,)
    assert main(["scan", "--mark-samples", "no-such-kind", "--yes"]) == 1


@needs_rclone
def test_samples_do_not_block_abort(env):
    cfg, claude, archive = env
    write(claude / "plans" / "sample.md", "AK" + "IA" + "E" * 16)
    cfg.secrets_on_found = "abort"
    result = backup(cfg, out=quiet)
    assert result.secrets.total == 0 and result.secrets.sample_total == 1
    assert archive.exists()


@needs_rclone
def test_doctor_counts_samples_apart_from_findings(env):
    cfg, claude, _archive = env
    sample = "AK" + "IA" + "E" * 16
    real = "AK" + "IA" + "E5FG" * 4
    write(claude / "plans" / "both.md", sample + "\n" + real + "\n")
    save(cfg)
    lines = []
    assert doctor.run(out=lines.append) == 0
    text = "\n".join(lines)
    assert "1 sample(s)" in text
    assert "1 likely secret" in text
    assert sample not in text and real not in text


def _aws_body(n: int) -> str:
    return "AK" + "IA" + f"{n:08X}" + "M4QZE5FG"


def test_end_of_input_marks_nothing_further(env, capsys, monkeypatch):
    cfg, claude, _archive = env
    first = _aws_body(1)
    second = _aws_body(2)
    write(claude / "plans" / "both.md", first + "\n" + second + "\n")
    save(cfg)
    answers = iter(["y"])

    def answer(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert main(["scan", "--mark-samples", "aws-access-key"]) == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert first not in captured.out and second not in captured.out
    stored = load().sample_hashes
    assert len(stored) == 1
    assert stored[0] in (hashlib.sha256(first.encode()).hexdigest(), hashlib.sha256(second.encode()).hexdigest())
    assert "likely secret" in captured.out


def test_end_of_input_before_any_answer_marks_nothing(env, capsys, monkeypatch):
    cfg, claude, _archive = env
    value = _aws_body(3)
    write(claude / "plans" / "one.md", value)
    save(cfg)
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert main(["scan", "--mark-samples", "aws-access-key"]) == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert value not in captured.out
    assert load().sample_hashes == ()
    assert "no samples marked" in captured.out
    assert "likely secret" in captured.out


def test_list_prints_hits_and_stores_nothing(env, capsys):
    cfg, claude, _archive = env
    often = _aws_body(4)
    once = _aws_body(5)
    write(claude / "plans" / "a.md", often + "\n" + often + "\n" + once + "\n")
    write(claude / "plans" / "b.md", often + "\n")
    save(cfg)
    assert main(["scan", "--mark-samples", "aws-access-key", "--list"]) == 0
    out = capsys.readouterr().out
    often_hash = hashlib.sha256(often.encode()).hexdigest()[:12]
    once_hash = hashlib.sha256(once.encode()).hexdigest()[:12]
    assert often not in out and once not in out
    assert often_hash in out and once_hash in out
    assert "plans/a.md" in out and "plans/b.md" in out
    assert out.index(often_hash) < out.index(once_hash)
    assert load().sample_hashes == ()


def test_hash_marks_only_the_named_hits(env, capsys):
    cfg, claude, _archive = env
    keep = _aws_body(6)
    leave = _aws_body(7)
    write(claude / "plans" / "pick.md", keep + "\n" + leave + "\n")
    save(cfg)
    short = hashlib.sha256(keep.encode()).hexdigest()[:12]
    assert main(["scan", "--mark-samples", "aws-access-key", "--hash", short]) == 0
    out = capsys.readouterr().out
    assert keep not in out and leave not in out
    assert short in out
    assert load().sample_hashes == (hashlib.sha256(keep.encode()).hexdigest(),)
    report = secrets.scan(load())
    assert report.sample_total == 1 and report.total == 1


def test_unknown_short_hash_is_refused(env, capsys):
    cfg, claude, _archive = env
    value = _aws_body(8)
    write(claude / "plans" / "one.md", value)
    save(cfg)
    digest = hashlib.sha256(value.encode()).hexdigest()
    bogus = "000000000000" if not digest.startswith("0") else "fffffffff"
    assert main(["scan", "--mark-samples", "aws-access-key", "--hash", bogus]) == 1
    captured = capsys.readouterr()
    assert "unknown sample hash" in captured.err
    assert value not in captured.out and value not in captured.err
    assert load().sample_hashes == ()


def test_ambiguous_short_hash_is_refused(env, capsys):
    cfg, claude, _archive = env
    found = None
    for n in range(1, 80):
        for m in range(n + 1, 80):
            left, right = _aws_body(n), _aws_body(m)
            d1 = hashlib.sha256(left.encode()).hexdigest()
            d2 = hashlib.sha256(right.encode()).hexdigest()
            shared = 0
            while shared < 12 and d1[shared] == d2[shared]:
                shared += 1
            if shared:
                found = (left, right, d1[:shared])
                break
        if found:
            break
    assert found is not None
    left, right, prefix = found
    write(claude / "plans" / "pair.md", left + "\n" + right + "\n")
    save(cfg)
    assert main(["scan", "--mark-samples", "aws-access-key", "--hash", prefix]) == 1
    captured = capsys.readouterr()
    assert "ambiguous sample hash" in captured.err
    assert hashlib.sha256(left.encode()).hexdigest()[:12] in captured.err
    assert hashlib.sha256(right.encode()).hexdigest()[:12] in captured.err
    assert left not in captured.err and right not in captured.err
    assert load().sample_hashes == ()
