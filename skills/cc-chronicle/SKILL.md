---
name: cc-chronicle
description: Archive Claude Code memory and session history to the user's own cloud storage (any rclone remote — Google Drive, Dropbox, OneDrive, S3…) and bring it back. Sessions are filed by start month; memory is mirrored; restore never overwrites. Use when the user says "/cc-chronicle", "back up my Claude Code sessions/memory", "archive my sessions", "did my backup run", "restore an old session", "I lost my sessions / conversation history", "set up daily backups", "scan my transcripts for secrets", or asks why old sessions disappeared (Claude Code deletes them after ~30 days).
---

# cc-chronicle

Every action goes through the `cc-chronicle` CLI. **Never write rclone commands yourself** for this data: the CLI encodes rules (mirror vs archive, filters, `--ignore-existing`) where one wrong flag can delete an archive.

## Find the CLI

1. If `cc-chronicle` is on PATH (`command -v cc-chronicle`), use it.
2. Otherwise use the launcher bundled with this plugin: `${CLAUDE_SKILL_DIR}/../../bin/cc-chronicle`. If that variable is empty, use the base directory shown when this skill loaded, plus `../../bin/cc-chronicle`.

Below, `CLI` means whichever path you found. It needs Python 3.9+ and rclone. If rclone is missing, point the user to https://rclone.org/install/.

## First-time setup

1. Run `CLI doctor`. If it says there's no config:
   - Run `rclone listremotes`. If there are none, the user creates one themselves with `rclone config` (it involves signing in to their storage provider). **Never handle their credentials or OAuth client secrets.**
   - Ask the user (AskUserQuestion) which remote, which folder (default `cc-chronicle`), and a short machine name. Then run `CLI init --remote <r> --root <folder> --machine <name>`.
   - If they want encryption, explain the rclone `crypt` remote (see the repo's docs/SECURITY.md). They create it themselves, then you run `init --force` pointing at it.
2. Run `CLI backup --dry-run` and show the per-step counts and the secret-scan line. Ask before the first real backup.

## Commands

| Goal | Command |
|---|---|
| Back up now | `CLI backup` (add `--dry-run` to preview). A large first backup can take a while: run it in the background |
| Last backup + what's pending | `CLI status` |
| Health check | `CLI doctor` |
| Likely secrets in what gets uploaded | `CLI scan` |
| Daily automatic backup | `CLI schedule install [--time HH:MM]` · `status` · `uninstall` |
| Restore | `CLI restore list [text]`, then `restore session <id-prefix>` / `restore project <text> [YYYY-MM]` / `restore memory [project]` / `restore history` / `restore plans` |

## Rules

- **Restore:** always run `restore list` first, show the user the missing sessions (month, project, size), and restore only what they choose (AskUserQuestion). Don't restore everything: Claude Code deletes local sessions after ~30 days, so a bulk restore just gets cleaned up again. Restore never overwrites local files.
- **Secrets:** `scan` shows file paths and pattern names, never values. Don't open those files to show the user the secret. Advise rotating the key at its provider, and mention `--strict-secrets` / `secrets_on_found = abort` and encryption.
- **Sharing:** if a backup or doctor reports the archive folder as shared, tell the user plainly. Never change sharing yourself.
- **Schedule:** installing adds a background job to the user's system. Ask before `schedule install`, and say how to remove it (`schedule uninstall`).
- On errors, show the failing line and the log path the CLI printed. Don't retry the same command more than once.
