# Research: is there room for cc-chronicle?

Done on 2026-10-14 with the GitHub CLI: about 15 repository searches, the READMEs of the closest tools, and the anthropics/claude-code issue tracker. Star counts are as of that day. Reddit, X and code quality were **not** checked.

## 1. The problem is real

Claude Code deletes local session transcripts after `cleanupPeriodDays` (default ~30 days), silently and with no recovery.

| Issue (anthropics/claude-code) | What it reports | Status on 2026-10-14 |
|---|---|---|
| [#59248](https://github.com/anthropics/claude-code/issues/59248) | "Silent retention cleanup deletes session transcripts with no warning, opt-in, or recovery". Labels `data-loss`, `has repro`; 58 comments | Open since 2026-05-14; no reply from an Anthropic member |
| [#41458](https://github.com/anthropics/claude-code/issues/41458) | "cleanupPeriodDays: 99999 ignored — 490 sessions silently deleted despite explicit setting"; 21 comments | Open since 2026-03-31; no reply from an Anthropic member |
| [#22648](https://github.com/anthropics/claude-code/issues/22648) | Account-level settings sync across devices; 49 👍, 26 comments | Open since 2026-02-03 |
| #99938, #98575, #95141, #90148 | Transcripts vanishing; `history.jsonl` growing unbounded; cleanup scope bugs | Open |

**Takeaway:** raising `cleanupPeriodDays` isn't a reliable defence (#41458 reports it being ignored). A copy outside the machine is.

## 2. What already exists

| Category | Repos (★) | What they do | Gap cc-chronicle fills |
|---|---|---|---|
| History viewers | jhlee0409/claude-code-history-viewer (2,253), simonw/claude-code-transcripts (1,700), daaain/claude-code-log (1,242), vtemian/claude-notes (105), ahmedelgabri/ccpeek (33) | Browse, search and export local transcripts | They read local files, which get deleted. No off-machine copy |
| Config backup to git | jtklinger/claude-code-backup-guide (40), mcpware/claude-code-backup (8), seanGSISG/claude-code-backup (3), finniganjohnson/fj-skills (2), placenameday/claude-code-backup (2) | Settings, skills and memory (some also sessions) into a private git repo; some with schedules, secret sanitizing, Windows/WSL | Git is a poor store for hundreds of MB of growing `.jsonl` (GitHub's 100 MB file limit, repo bloat). Several leave sessions out on purpose |
| Archive files | dberuben/claude-code-backup (2) | `.tar.gz` snapshots; optional push through any command, including rclone | One opaque archive: you can't browse it or restore a single session |
| Rescue export | parallelclaw/claude-backup (7) | Exports sessions to Markdown in case your account is banned | No ongoing cloud archive |
| Cloud sync | peteknowsai/ccloud (1) | Push and pull the latest session per repo to Cloudflare R2 | Latest session only; inactive since 2025-10 |
| Memory sync | sunjinpak, kasimmj, MarimerLLC/claude-utils (≤13) | Sync auto-memory across machines through git | Memory only |

**No backup tool has more than ~40 stars, and none treats sessions as a browsable cloud archive.**

## 3. What makes cc-chronicle different

- **Sessions first, on any rclone remote** (70+ providers), not git.
- **Browsable layout:** sessions filed by start month, each one bundled with its subagents, tool-results and file-history (so rewind survives a restore).
- **Memory mirror with a safety net:** the cloud shows current truth, and removed or changed notes move to `_deleted/<run>/`.
- **Targeted restore** that never overwrites, and doesn't fight the local cleanup by pulling everything back.
- **Sharing check** on the cloud folder (Drive, through rclone permission metadata).

## 4. Competitor features worth matching

| Feature | Seen in | cc-chronicle v1 |
|---|---|---|
| Automatic schedule (launchd/systemd) | mcpware, jtklinger, fj-skills | Yes (opt-in, daily) |
| Secret detection | placenameday, jtklinger, dberuben | Yes (scan + warn; values never printed) |
| Plugin / marketplace install | fj-skills | Yes |
| Windows / WSL | seanGSISG | Later |
| Many machines in one store | seanGSISG | Partly (per-machine `history` and stamp); full support later |
| Markdown export | parallelclaw, viewers | No (viewers already do it well) |

## 5. Distribution channels

- [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) (55.6k★): the main list; submit a PR after v1.
- [ccplugins/awesome-claude-code-plugins](https://github.com/ccplugins/awesome-claude-code-plugins) (972★).
- The repo as its own plugin marketplace (`/plugin marketplace add youssefemadeldn/cc-chronicle`), plus the Agent Skills route (`npx skills add`).
- The issue threads above (#59248, #41458), where affected users already are.

## 6. Risks

- Anthropic may fix retention or ship official sync. The issues have been open 5–8 months with no reply, and an off-machine archive stays useful either way.
- The `.jsonl` format and `~/.claude` layout are undocumented and can change, so the tool needs maintenance.
- Transcripts contain secrets, so the tool must warn clearly and offer encryption.
