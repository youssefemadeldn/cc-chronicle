from pathlib import Path

import pytest
from conftest import needs_rclone

from cc_chronicle import ChronicleError
from cc_chronicle.cli import main
from cc_chronicle.config import Config, default_claude_dir, default_machine, load, save


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.ini"
    save(Config(remote="gdrive", root="archive", machine="mac", claude_dir=Path("/x/.claude")), path)
    cfg = load(path)
    assert cfg.dest == "gdrive:archive"
    assert cfg.machine == "mac" and cfg.claude_dir == Path("/x/.claude")
    assert cfg.include == ("memory", "sessions", "plans", "history")


def test_load_keeps_absolute_root_and_drops_trailing_slash(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text("[cc-chronicle]\nremote = t:\nroot = /data/archive/\nmachine = box\n")
    cfg = load(path)
    assert cfg.dest == "t:/data/archive"


def test_invalid_config_is_rejected(tmp_path):
    with pytest.raises(ChronicleError, match="no config"):
        load(tmp_path / "missing.ini")
    with pytest.raises(ChronicleError, match="machine"):
        Config(remote="g", machine="My Mac").validate()
    with pytest.raises(ChronicleError, match="include"):
        Config(remote="g", include=("memory", "photos")).validate()


def test_defaults(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/claude")
    assert default_claude_dir() == Path("/custom/claude")
    assert default_machine() == default_machine().lower()


@needs_rclone
def test_init_status_backup_restore_list_via_cli(env, capsys):
    _cfg, claude, archive = env
    assert main(["init", "--remote", "t", "--root", str(archive), "--machine", "mac",
                 "--claude-dir", str(claude)]) == 0
    assert main(["status"]) == 0
    assert "none yet" in capsys.readouterr().out
    assert main(["backup"]) == 0
    assert main(["status"]) == 0
    assert '"machine": "mac"' in capsys.readouterr().out
    assert main(["restore", "list"]) == 0
    assert "Nothing in the archive is missing" in capsys.readouterr().out


@needs_rclone
def test_init_refuses_unknown_remote_and_existing_config(env, capsys):
    assert main(["init", "--remote", "nope"]) == 1
    assert "no remote named 'nope'" in capsys.readouterr().err
    assert main(["init", "--remote", "t"]) == 0
    assert main(["init", "--remote", "t"]) == 1
    assert "already exists" in capsys.readouterr().err
