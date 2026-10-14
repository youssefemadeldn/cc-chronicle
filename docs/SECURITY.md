# Security and privacy

cc-chronicle copies your Claude Code history to cloud storage. That history is more sensitive than it looks. Read this before your first backup.

## What a transcript contains

A session `.jsonl` records everything that passed through the conversation: your prompts, Claude's replies, **every file Claude read, and every command's output**. If a session ran `cat .env`, printed a config, or listed MCP servers whose command line carries an API key, that value is now in the transcript. The same applies to `tool-results/`, `file-history/` snapshots and `history.jsonl`.

So treat the archive like the machine itself: private, and only in storage you control.

## The secret scanner

Before every backup, cc-chronicle scans every file it is about to upload for well-known secret formats: Anthropic, OpenAI, GitHub, Google, AWS, Slack and Stripe keys, PEM private keys, and JWTs.

- **Values are never shown, logged or stored.** Only the file path, the pattern name and a count are reported. The cache (`~/.local/state/cc-chronicle/secret-scan.json`) holds the same.
- `cc-chronicle scan` lists every file with findings.
- By default a backup **warns** and continues. To stop the upload when new or changed files contain likely secrets, either:
  - run `cc-chronicle backup --strict-secrets`, or
  - set `secrets_on_found = abort` in the config.

  Files already archived don't block later runs.
- It is pattern-based. It **can miss** secrets in unusual formats (passwords, connection strings, custom tokens) and **can flag** harmless text. Treat a clean scan as "nothing obvious", not as "nothing at all".

**If it finds something:** assume the secret is exposed and **rotate it** at its provider. That's the real fix; deleting it from a transcript isn't. Then avoid printing secrets in sessions: reference environment variables instead of pasting values.

## Encrypting the archive (opt-in)

rclone can encrypt everything client-side with a [crypt remote](https://rclone.org/crypt/). The storage provider then only sees encrypted file names and contents.

1. Run `rclone config` → `n` (new remote) → name it, for example `chronicle-crypt` → type `crypt`.
2. Set `remote` to a folder on your existing remote, for example `gdrive:cc-chronicle-encrypted`.
3. Choose filename encryption (`standard`), then set a password and a salt. **You create these, and rclone stores them in `rclone.conf`.**
4. Point cc-chronicle at it: `cc-chronicle init --remote chronicle-crypt --root archive --force`.

Trade-offs:
- **If you lose the passwords, you lose the archive.** Back them up somewhere safe (a password manager). Consider protecting `rclone.conf` itself with `rclone config` → *Set configuration password*.
- Encrypted files can't be read in the provider's web UI or by tools that read the storage directly (for example a Google Drive connector).

## Sharing check (Google Drive)

After each backup to a Google Drive remote, cc-chronicle reads the archive folder's sharing permissions through rclone. If anyone other than the owner has access (a person, a domain, or "anyone with the link"), it prints a warning. `cc-chronicle doctor` runs the same check.

It **never changes sharing** itself; remove unwanted access in Drive. Note that sharing is inherited: putting the archive inside a shared folder shares the archive.

Other backends (S3, Dropbox, OneDrive…) don't expose sharing through rclone, so the check is skipped there.

## Google Drive: use your own OAuth client

rclone's built-in Google client ID is shared by every rclone user and is being retired by Google. Create your own (free, about 10 minutes):

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Google Drive API**.
2. Configure the OAuth consent screen. **Publish it ("In production")**: in *Testing* mode, Google expires your login every 7 days and backups start failing.
3. Create an OAuth client of type **Desktop app**.
4. Run `rclone config` → edit your Drive remote → paste the client ID and secret → reconnect.

Full guide: <https://rclone.org/drive/#making-your-own-client-id>. `cc-chronicle doctor` warns while the shared ID is still in use.

## What cc-chronicle stores locally

| File | Contents |
|---|---|
| `~/.config/cc-chronicle/config.ini` | Remote name, folder, machine name, options. **No credentials**: those stay in `rclone.conf` |
| `~/.local/state/cc-chronicle/logs/` | rclone logs: file paths and actions, no file contents |
| `~/.local/state/cc-chronicle/secret-scan.json` | File paths, sizes, times, and per-pattern counts. No values |
