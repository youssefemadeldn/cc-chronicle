# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-14

### Added
- `init`, `backup`, `status` and `restore` (`list`, `session`, `project`, `memory`, `history`, `plans`) over any rclone remote.
- Archive layout: memory mirrored with removed or changed notes kept under `_deleted/<run>/`; sessions filed by start month and bundled with their subagents, tool results and file-history; plans and per-machine prompt history archived.
- Secret scanner (`scan`, plus a scan before every backup) covering nine common key and token formats; values are never shown. `--strict-secrets` and `secrets_on_found = abort` stop uploads of new findings.
- Google Drive sharing check after each backup, through rclone permission metadata.
- `doctor` health check.
- Daily schedule: launchd on macOS, systemd user timer on Linux.
- Claude Code plugin with a `/cc-chronicle` skill; `install.sh` for the CLI.
