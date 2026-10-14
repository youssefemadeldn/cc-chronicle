# Claude Code archive (cc-chronicle)

This folder is an archive of Claude Code memory and session history, written by
[cc-chronicle](https://github.com/youssefemadeldn/cc-chronicle). It may contain private
conversations and secrets: **do not share it**. It is updated automatically, so don't edit it by hand.

## Layout

```
README.md                         this file
last-backup.<machine>.json        when each machine last backed up, with counts
.claude/
├── projects/<project>/memory/    memory notes (MIRROR of each machine: always current)
├── sessions/<YYYY-MM>/<project>/ sessions by START month (ARCHIVE: never deleted)
│   ├── <session-id>.jsonl        full transcript, one JSON event per line
│   └── <session-id>/             subagents/, tool-results/, file-history/ of that session
├── file-history/<session-id>/    rewind snapshots whose session file is gone
├── plans/                        plan-mode plans (ARCHIVE)
└── history.<machine>.jsonl       every prompt typed on that machine (ARCHIVE)
_deleted/<run>/.claude/projects/<project>/memory/
                                  memory notes removed or changed at that backup run
```

## How to read it

- `<project>` is the project's path with `/`, spaces and `.` turned into `-`, so
  `-Users-me-code-app` is `/Users/me/code/app`.
- Start with `.claude/projects/<project>/memory/MEMORY.md`: it is the index of that project's memory notes.
- In a session `.jsonl`, lines with `"type":"user"` and `"type":"assistant"` are the conversation;
  a line with `"type":"ai-title"` holds the session's title.
- Current truth is in `projects/*/memory/`. Anything under `_deleted/` is no longer valid.
- To bring something back: `cc-chronicle restore list`, then `cc-chronicle restore session <id>`.
