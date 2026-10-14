# Design

Why cc-chronicle works the way it does. Change a rule here first, then in the code.

## Roles

| Choice | Why |
|---|---|
| **rclone does all transfers** | Bulk, incremental and resumable, with 70+ storage backends and their authentication handled by rclone. cc-chronicle never sees credentials. |
| **One wrapper (`rclone.py`) for every rclone call** | The rules below come down to exact flags. Keeping them in one place stops a stray `sync` from deleting an archive. |
| **Python standard library only** | Installs anywhere with nothing to resolve. Tests use pytest, but the tool doesn't. |

## What is mirrored and what is archived

| Data | Mode | Why |
|---|---|---|
| `projects/<p>/memory/` | **Mirror** (`rclone sync`) with `--backup-dir _deleted/<run>/` | Memory is "current truth": anyone reading the archive must not find notes that were deleted for being wrong. Removed **and changed** notes are moved to `_deleted/<run>/`, so nothing is lost. |
| Mirror only **projects present on this machine** | Filter built from local `*/memory` folders | Another machine's projects are never treated as deleted. |
| Sessions, `file-history`, plans, history | **Archive** (`rclone copy`, never deletes) | Claude Code deletes local sessions after ~30 days; the archive is the permanent record. |

## Sessions

- **Filed by start month**: the first `timestamp` in the transcript, in local time (file time as a fallback). A session never moves between months, even when resumed later, so its archive path never changes.
- **Bundled**: `<id>.jsonl` plus `<id>/subagents/`, `<id>/tool-results/` and `<id>/file-history/` sit together. Restoring one session restores everything it needs, including rewind snapshots. `file-history` snapshots whose session file is gone are archived under `.claude/file-history/<id>/`.
- **Staging**: `stage.py` builds the month layout as a temporary tree of symlinks, uploaded in one `rclone copy --copy-links`. One rclone call keeps it fast and incremental.

## Per-machine files

- `history.jsonl` exists on every machine under the same name, so it is archived as `history.<machine>.jsonl`. It's the only renamed file.
- `last-backup.<machine>.json` records each machine's last run.

## Restore

- Always `--ignore-existing`: the local copy of a live session is newer than its archived copy, and must never be rolled back.
- Targeted (one session, project or month) by design. Pulling every old session back would just be deleted again by Claude Code's cleanup.
- Sessions return to their original project folder name.

## Safety

- **Secret scanner**: each pattern has literal anchors (`AIza`, `ghp_`, `PRIVATE KEY-----`…). Its regex only runs when an anchor is present, which made the first scan of ~300 MB about 5× faster. Key-like tokens must start a word and contain a digit, after kebab-case prose (`task-make-the-…`) was found matching as an OpenAI key in real transcripts. Results are cached by size and mtime and store counts only.
- **Abort mode** looks only at *new or changed* files: files already archived can't be un-uploaded, so blocking on them would block every future backup.
- **Sharing check** uses `rclone lsjson -M --drive-metadata-permissions read` rather than a Drive API client: no extra credentials, and it works anywhere rclone works.

## Scheduling

- The job sets its own `PATH` (rclone's folder, Python's folder, system folders): launchd and systemd start jobs with a minimal PATH that usually lacks `/opt/homebrew/bin`.
- It prefers the `cc-chronicle` found on `PATH`, because a plugin's install folder contains its version and moves on update. `doctor` flags a job whose launcher no longer exists.
- The launchd plist is written as text. `plistlib` imports `pyexpat`, which is broken in some Python builds (seen in Homebrew Python 3.14 on macOS 26).

## rclone details worth knowing

- `copyto` (single file) refuses filter flags, so `.DS_Store` filtering is skipped for it.
- `--timeout 60s`: after a network switch, stalled connections are dropped and retried within a minute instead of five.
- Google Drive creates files one by one, so thousands of small sidecar files dominate the first upload's time, not its size.
