# Contributing

Thanks for helping. Bug reports, platform fixes (especially Linux distributions and, later, Windows) and new secret patterns are all welcome.

## Ground rules

- **Standard library only at runtime.** cc-chronicle must run on a bare Python 3.9+ with rclone. Dev tools (pytest) are fine.
- **Python 3.9 compatible.** Use `from __future__ import annotations` for modern type hints. No `match` statements and no 3.10+ only APIs.
- **All rclone calls go through `cc_chronicle/rclone.py`.** Read [docs/DESIGN.md](docs/DESIGN.md) before changing what is mirrored or archived: one wrong flag can delete someone's archive.
- **Never print, log or store secret values.** Tests must not contain key-shaped strings either. Assemble fake keys at runtime (see `tests/test_safety.py`) so GitHub push protection and scanners stay quiet.

## Development setup

```sh
git clone https://github.com/youssefemadeldn/cc-chronicle.git && cd cc-chronicle
python3 -m venv .venv          # or: uv venv .venv
.venv/bin/pip install -r requirements-dev.txt   # or: uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pytest -q
```

The tests need **rclone** on PATH. They never touch the cloud: each test points a fake `~/.claude` at an rclone `local` remote inside a temporary folder. Tests that need rclone are skipped if it's missing.

Run the CLI from the clone with `bin/cc-chronicle …`. To keep your real config untouched while experimenting, set `CC_CHRONICLE_CONFIG` and `XDG_STATE_HOME` to a scratch folder.

## Checking the plugin

```sh
claude plugin validate --strict .
claude plugin validate --strict .claude-plugin/plugin.json
claude plugin validate --strict skills
```

To try the plugin locally: `claude plugin marketplace add /path/to/clone`, then `claude plugin install cc-chronicle@cc-chronicle`. A local-folder install copies untracked files too (for example `.venv`). Installs from GitHub only contain committed files.

## Pull requests

- One topic per pull request, with tests for behaviour changes.
- Update `CHANGELOG.md` under *Unreleased*.
- CI runs the suite on macOS and Linux with Python 3.9 and 3.12; it must pass.
