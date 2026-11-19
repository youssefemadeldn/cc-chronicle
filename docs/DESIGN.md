# Design

Why cc-chronicle works the way it does. Change a rule here first, then in the code.

## Roles

| Choice | Why |
|---|---|
| **rclone does all transfers** | Bulk, incremental and resumable, with 70+ storage backends and their authentication handled by rclone. cc-chronicle never sees credentials. |
| **One wrapper (`rclone.py`) for every rclone call** | The rules below come down to exact flags. Keeping them in one place stops a stray `sync` from deleting an archive. |
| **Uploads are speed-capped** | On 18 Nov 2026 a ~2.9 GB Cursor upload held the home line (~50 KB/s) for ~20 hours: other work failed for ~6 hours and the 10 PM digest was 30 minutes late. Every upload (`copy`, `sync`, `copyto`) passes `--bwlimit`. Default `09:45,16k 10:30,256k 21:45,16k 22:30,256k` (local time): 16 KiB/s around the 10:00 and 22:00 digests, 256 KiB/s otherwise. `bwlimit = off` or `backup --bwlimit off` removes it. Read-only calls are uncapped. |
| **Python standard library only** | Installs anywhere with nothing to resolve. Tests use pytest, but the tool doesn't. |

## What is mirrored and what is archived

| Data | Mode | Why |
|---|---|---|
| `projects/<p>/memory/` | **Mirror** (`rclone sync`) with `--backup-dir _deleted/<run>/` | Memory is "current truth": anyone reading the archive must not find notes that were deleted for being wrong. Removed **and changed** notes are moved to `_deleted/<run>/`, so nothing is lost. |
| Mirror only **projects present on this machine** | Filter built from local `*/memory` folders | Another machine's projects are never treated as deleted. |
| Sessions, `file-history`, plans, history | **Archive** (`rclone copy`, never deletes) | Claude Code deletes local sessions after ~30 days; the archive is the permanent record. |
| `~/.cursor/chats` and `~/.cursor/projects` | **Archive** (`rclone copy`, never deletes) | Cursor chats live only on this Mac. The archive is the copy that survives the machine. |

## Sessions

- **Filed by start month**: the first `timestamp` in the transcript, in local time (file time as a fallback). A session never moves between months, even when resumed later, so its archive path never changes.
- **Bundled**: `<id>.jsonl` plus `<id>/subagents/`, `<id>/tool-results/` and `<id>/file-history/` sit together. Restoring one session restores everything it needs, including rewind snapshots. `file-history` snapshots whose session file is gone are archived under `.claude/file-history/<id>/`.
- **Staging**: `stage.py` builds the month layout as a temporary tree of symlinks, uploaded in one `rclone copy --copy-links`. One rclone call keeps it fast and incremental.
- **Live files are snapshotted**: a file changed in the last 24 hours is copied into the stage instead of linked. An open session keeps appending to its transcript, and rclone rejects a file that changes while it uploads (`md5 hashes differ` / `source file is being updated`). The copy is fixed; the next run picks up what was added after it. Older files stay links, so the copy stays small.
- **One failed step doesn't stop the run**: each upload step (memory, sessions, file-history, plans, history, cursor chats, cursor projects, readme) runs even if an earlier one failed. The stamp is still written and lists `failed_steps`; `doctor` warns about them and the command exits non-zero, so the scheduled job still shows as failed.

## Cursor

On when `~/.cursor` exists, unless the config says `cursor = off`. A machine without that directory skips this and behaves as before. `cursor_dir` overrides the path (default `~/.cursor`).

- **Archive, never mirror.** Chats and project files are a record of what happened. Deleting one locally must not delete it from the archive, so both trees go up with `rclone copy`, beside `.claude/`:
  - `<remote>/.cursor/chats/<YYYY-MM>/<workspace-hash>/<chat-uuid>/` — the chat folder (`meta.json`, `store.db`, anything else in it).
  - `<remote>/.cursor/projects/` — the same relative tree as `~/.cursor/projects/`, not split by month. Project files are not chats, and their paths are already how Cursor finds them.
- **Filed by start month.** The month comes from `meta.json`'s `createdAtMs` (unix milliseconds, local time). That is the field Cursor writes for when the chat was created. `createdAt`, `created_at`, `created`, `timestamp`, `startTime` and `startedAt` are accepted too, as an ISO time or as unix seconds or milliseconds. `updatedAtMs` is not used: a chat that continues next month must stay in the month it started, or its archive path would change. If none of those is usable, the month is the earliest file time in the chat folder: the older of `st_birthtime` (when the filesystem has it) and `st_mtime`.
- **Live SQLite is snapshotted.** A `store.db` that has a `store.db-wal`, or whose own mtime is within the last 24 hours, is copied into the stage with Python `sqlite3`'s backup API. The source is opened read-only (`file:…?mode=ro`). The staged file is one consistent database, so a chat being written is never uploaded half-done. Cursor keeps that database open all day, and a read-only open then fails with `unable to open database file` because SQLite cannot create the WAL index in `~/.cursor`. When that open fails, the db plus its `-wal` and `-shm` (if they are there) are copied with `shutil.copy2` into a private temp folder. That copy is opened normally, so SQLite can write its own shm there, and the backup API runs from the copy into the stage. The temp folder is then removed. `~/.cursor` is never written. Only the staged snapshot is uploaded. `-wal` and `-shm` are not uploaded as their own files. If the private copy also fails (the file vanished or was mid-write), that one chat is left out of this run. It is counted as `skipped (live)` on the cursor line, and its path is written to the run log. The next run takes it. The step is not failed for that. It fails only when nothing could be staged, or when the error is not about a live file. Every other file uses the same staging as sessions: a symlink, unless it changed in the last 24 hours, in which case it is a plain copy. Older chats stay links, so the stage stays small.
- **Secret scan before upload**, same patterns and the same abort rule (new or changed files only). `store.db` is SQLite, so the scanner does not treat the file as text. It opens the database read-only and scans each text cell (and text inside blobs). If the file cannot be opened as SQLite, it scans the bytes it can decode (`errors="ignore"`) and still never prints a value. `-wal` and `-shm` are not scanned separately, because their text is read through the database.
- **Restore one chat.** `cc-chronicle restore cursor <uuid>` copies that chat back into `<cursor_dir>/chats/<workspace-hash>/<chat-uuid>/` with `--ignore-existing`. Files the restore creates get today's date, same as a restored Claude session, so a later cleanup that trusts file dates does not treat them as already expired. A file already on the machine keeps its contents and its date.
- **Stamp, status, doctor.** `last-backup.<machine>.json` records whether Cursor was on, absent or off, plus chat count, chats by month, file count, bytes, how many databases were snapshotted, and how many chats were `skipped (live)`. A failed `cursor-chats` or `cursor-projects` step is a `failed_steps` entry. `doctor` has its own cursor line for that, including the skipped count.

## Per-machine files

- `history.jsonl` exists on every machine under the same name, so it is archived as `history.<machine>.jsonl`. It's the only renamed file.
- `last-backup.<machine>.json` records each machine's last run.

## Restore

- Always `--ignore-existing`: the local copy of a live session is newer than its archived copy, and must never be rolled back.
- Targeted (one session, project or month) by design. Pulling every old session back would just be deleted again by Claude Code's cleanup.
- Sessions return to their original project folder name.
- Files a restore creates (transcript, subagents, tool results, plans) get today's date. Claude Code's cleanup deletes transcripts, tool results and plans whose modification time is older than `cleanupPeriodDays`, and rclone keeps the archived date, so an old session restored as-is was deleted again at the next sweep (verified against Claude Code 2.1.299). Memory keeps its dates; the sweep doesn't touch it.

## Safety

- **Secret scanner**: each pattern has literal anchors (`AIza`, `ghp_`, `PRIVATE KEY-----`…). Its regex only runs when an anchor is present, which made the first scan of ~300 MB about 5× faster. Key-like tokens must start a word and contain a digit, after kebab-case prose (`task-make-the-…`) was found matching as an OpenAI key in real transcripts. Results are cached by size and mtime and store counts only.
- **Samples are not findings.** A match is a sample when it is a published example (the AWS access key id from the IAM docs, the sample token on jwt.io), an obvious fake (`EXAMPLE`, `TEST`, `FAKE` or `XXXX` inside the value, 8 or more of one character, or an ascending run of 8 or more), or the sha256 of a value the user marked. `sample_hashes` in the config holds those digests. `scan --mark-samples KIND` asks once per unknown value (or `--yes` for all of that kind) and stores the hash, never the value. The cache keeps counts, not values, and is rebuilt when the hash list changes. Samples are counted on their own. They do not block a backup. The PEM header is the same for every private key, so it is never a sample. The AWS example secret is not a shape this scanner matches.
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

Linux CI for Python 3.9 runs on `ubuntu-24.04` because `ubuntu-latest` is Ubuntu 26.04 and `actions/setup-python` has no 3.9 build for it; the Mac that runs cc-chronicle every day is still Python 3.9, so that job stays.
