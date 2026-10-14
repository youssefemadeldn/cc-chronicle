# cc-chronicle

**Never lose a Claude Code session.** cc-chronicle archives your Claude Code memory and session history to storage you control (Google Drive, Dropbox, OneDrive, S3, or any of rclone's 70+ backends), filed by month, and brings it back when you need it.

[![CI](https://github.com/youssefemadeldn/cc-chronicle/actions/workflows/ci.yml/badge.svg)](https://github.com/youssefemadeldn/cc-chronicle/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Why

Claude Code keeps every conversation in `~/.claude/projects/` and **deletes it after about 30 days**, silently, with no prompt and no trash. Users report losing far more than that:

- [#59248](https://github.com/anthropics/claude-code/issues/59248): *"Silent retention cleanup deletes session transcripts with no warning, opt-in, or recovery"* (labelled `data-loss`)
- [#41458](https://github.com/anthropics/claude-code/issues/41458): *"cleanupPeriodDays: 99999 ignored — 490 sessions silently deleted despite explicit setting"*

Raising `cleanupPeriodDays` isn't a guarantee. A copy somewhere else is.

## What it does

- **Archives every session**, bundled with its subagent transcripts, tool results and rewind snapshots, into **month folders** you can browse.
- **Mirrors your memory notes**, so the archive always shows current truth. Notes you delete or change locally are moved aside, never lost.
- **Restores** a single session, a project, a month, or all your memory onto any machine, **without ever overwriting** a local file.
- **Scans for secrets** (API keys, tokens, private keys) before every upload and tells you where they are, **without ever printing them**.
- **Checks sharing**: on Google Drive, it warns if the archive folder is shared with anyone.
- **Runs daily** if you want it to (launchd on macOS, systemd on Linux), catching up after sleep.
- Ships as a **Claude Code plugin** (`/cc-chronicle`) and as a plain CLI. Python standard library only, plus rclone.

## Quick start

You need **Python 3.9+** and **[rclone](https://rclone.org/install/)** with a remote configured (`rclone config`; for Google Drive, see [SECURITY.md](docs/SECURITY.md#google-drive-use-your-own-oauth-client)).

**As a Claude Code plugin:**

```
/plugin marketplace add youssefemadeldn/cc-chronicle
/plugin install cc-chronicle@cc-chronicle
```

Then ask Claude to *"set up cc-chronicle"*, or run `/cc-chronicle`.

**As a CLI:**

```sh
git clone https://github.com/youssefemadeldn/cc-chronicle.git ~/.local/share/cc-chronicle
~/.local/share/cc-chronicle/install.sh        # links ~/.local/bin/cc-chronicle

cc-chronicle init                 # pick the rclone remote, a folder, a machine name
cc-chronicle backup --dry-run     # see what would be uploaded
cc-chronicle backup               # first upload (can take a while: many small files)
cc-chronicle schedule install     # optional: back up daily at 12:00
```

## The archive

```
<remote>:<folder>/
├── README.md                         how to read the archive
├── last-backup.<machine>.json        when each machine last backed up
├── .claude/
│   ├── projects/<project>/memory/    memory notes: MIRROR (always current)
│   ├── sessions/<YYYY-MM>/<project>/ sessions by start month: ARCHIVE (never deleted)
│   │   ├── <session-id>.jsonl
│   │   └── <session-id>/             subagents/  tool-results/  file-history/
│   ├── plans/                        ARCHIVE
│   └── history.<machine>.jsonl       your prompt history, one file per machine
└── _deleted/<run>/…                  memory notes removed or changed at that run
```

`<project>` is the project path as Claude Code stores it: `/Users/me/code/app` becomes `-Users-me-code-app`.

## Restoring

```sh
cc-chronicle restore list                 # archived sessions this machine no longer has
cc-chronicle restore session 5e22d49a     # one session (id prefix), with its subagents and rewind snapshots
cc-chronicle restore project my-app 2026-09
cc-chronicle restore memory               # every project's memory notes
cc-chronicle restore history              # this machine's prompt history
```

Restored sessions go back to their original project folder, where Claude Code looks for them. Add `--dry-run` to any restore to preview it.

## Commands

| Command | What it does |
|---|---|
| `init` | Create the config: rclone remote, folder, machine name |
| `backup [--dry-run] [--strict-secrets]` | Scan for secrets, then upload what changed |
| `status` | Last backup, and what a backup would upload now |
| `restore <list\|session\|project\|memory\|history\|plans>` | Bring data back; never overwrites |
| `scan` | List files containing likely secrets (values never shown) |
| `schedule <install\|uninstall\|status> [--time HH:MM]` | Daily automatic backups |
| `doctor` | Check Python, rclone, config, remote, sharing, last backup and schedule |

## Configuration

`cc-chronicle init` writes `~/.config/cc-chronicle/config.ini`:

```ini
[cc-chronicle]
remote = gdrive
root = cc-chronicle
machine = macbook
# defaults to $CLAUDE_CONFIG_DIR, then ~/.claude
claude_dir = /Users/me/.claude
include = memory,sessions,plans,history
# "abort": upload nothing when new or changed files contain likely secrets
secrets_on_found = warn
```

No credentials are stored here: rclone keeps those in its own config.

## Security

Transcripts contain everything that passed through a session, including file contents and command output, so they often contain secrets. Read **[docs/SECURITY.md](docs/SECURITY.md)**: what the scanner covers, how to encrypt the archive with an rclone `crypt` remote, and the Google Drive setup.

## FAQ

**Why not just back up to git?** Sessions are hundreds of megabytes and grow every day. Git keeps every version forever and GitHub rejects files over 100 MB. Cloud storage through rclone handles that, and lets you browse the archive.

**Does it upload my settings, API keys or OAuth tokens?** No. It uploads memory notes, session transcripts (with their subagents, tool results and file-history), plans and prompt history. It never uploads `settings.json`, `~/.claude.json` or credentials. Transcripts can still *contain* secrets that appeared in a conversation; that's what the scanner is for.

**Does it ever delete anything?** Sessions, plans and history are never deleted from the archive. Memory is mirrored, but anything removed or changed locally is moved to `_deleted/`, not destroyed. Restore never overwrites local files.

**Several machines?** Give each machine its own `machine` name and point them at the same folder. Memory folders are only mirrored for projects that exist on that machine, so machines don't erase each other's notes. Windows support is planned.

**Does it work with Claude Code in VS Code, Cursor or JetBrains?** Yes. They all store sessions in the same `~/.claude` folder.

**How big is the first backup?** About the size of `~/.claude/projects` plus `file-history` (a few hundred MB after a month of daily use). Many small files make the first upload slow; later runs only send what changed.

## Roadmap

- Windows and WSL
- Resuming another machine's sessions (path remapping)
- Searching the archive from Claude
- Exporting sessions to Markdown

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). cc-chronicle is a community project, not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic.
