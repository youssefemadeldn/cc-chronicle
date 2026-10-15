# Roadmap

## v0.1.1 (current)

Everything in the [changelog](../CHANGELOG.md) for 0.1.0 and 0.1.1.

- **Tested in CI** on Ubuntu and macOS with Python 3.9 and 3.12; all tests run against an rclone `local` remote.
- **Verified end-to-end on Google Drive:**
  - a first archive of ~2,000 files;
  - a later run on the same archive re-uploading nothing old;
  - a session restored byte-for-byte with its sidecar and file-history;
  - that session reopened by `claude --resume` with its history;
  - a restored session older than `cleanupPeriodDays` surviving Claude Code's next cleanup (0.1.1);
  - the daily launchd job installed and loaded.

## Next

- **Windows and WSL.** Config, paths and a Task Scheduler job.
- **Resuming another machine's sessions.** Sessions are stored under the project path of the machine that recorded them (`-Users-…` vs `D--…`). Restoring them into this machine's project folder needs a path remap, and a test that `--resume` accepts them.
- **Searching the archive from Claude.** On Google Drive, memory notes and tool results are found by Drive's full-text search, but `.jsonl` sessions (uploaded as `application/octet-stream`) are not. Uploading them as `text/plain` should fix that.
- **Markdown export** of selected sessions.
- **Backup at session end** through a Claude Code hook, as an alternative to the daily schedule.
- More secret patterns, as users report formats the scanner misses.

## Known limitations

- The first upload to Google Drive is slow when there are thousands of small sidecar files: Drive creates files one at a time.
- The sharing check only works on Google Drive.
- The secret scanner is pattern-based: it can miss unusual formats and flag harmless text.
- Claude Code's `.jsonl` format and `~/.claude` layout are undocumented and may change between Claude Code releases.

Ideas and reports are welcome as [issues](https://github.com/youssefemadeldn/cc-chronicle/issues).
