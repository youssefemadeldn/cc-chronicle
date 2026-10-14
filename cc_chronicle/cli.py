"""Command line entry point: cc-chronicle <command> [options]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ChronicleError, __version__, rclone
from . import config as config_mod
from .backup import backup
from .restore import Restorer, remote_sessions


def _ask(question: str, default: str) -> str:
    answer = input(f"{question} [{default}]: ").strip()
    return answer or default


def cmd_init(args) -> int:
    path = Path(args.config) if args.config else config_mod.config_path()
    if path.exists() and not args.force:
        raise ChronicleError(f"config already exists at {path} (use --force to replace it)")
    remotes = rclone.list_remotes()
    if not remotes:
        raise ChronicleError("no rclone remotes found — create one first with: rclone config")
    interactive = sys.stdin.isatty()
    remote = args.remote
    if not remote:
        if not interactive:
            raise ChronicleError("--remote is required when not running interactively")
        for i, name in enumerate(remotes, 1):
            print(f"  {i}. {name}")
        choice = _ask("Which rclone remote should hold the archive?", "1")
        remote = remotes[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(remotes) else choice
    remote = remote.rstrip(":")
    if remote not in remotes:
        raise ChronicleError(f"rclone has no remote named '{remote}' (have: {', '.join(remotes)})")
    root = args.root or (_ask(f"Folder inside '{remote}'", config_mod.DEFAULT_ROOT) if interactive
                         else config_mod.DEFAULT_ROOT)
    machine = args.machine or (_ask("Name for this machine", config_mod.default_machine()) if interactive
                               else config_mod.default_machine())
    claude_dir = Path(args.claude_dir).expanduser() if args.claude_dir else config_mod.default_claude_dir()
    cfg = config_mod.Config(remote=remote, root=root.rstrip("/"), machine=machine, claude_dir=claude_dir)
    saved = config_mod.save(cfg, path)
    print(f"Saved {saved}")
    print(f"  archive: {cfg.dest}   machine: {cfg.machine}   claude dir: {cfg.claude_dir}")
    print("Next: cc-chronicle backup --dry-run   (shows what would be uploaded, uploads nothing)")
    print("Encryption is opt-in: create an rclone 'crypt' remote (rclone config) and point --remote at it.")
    return 0


def cmd_backup(args) -> int:
    backup(_load(args), dry_run=args.dry_run)
    return 0


def cmd_status(args) -> int:
    cfg = _load(args)
    print(f"== last backup ({cfg.machine})")
    print(rclone.cat(f"{cfg.dest}/last-backup.{cfg.machine}.json") or "  none yet\n")
    print("== pending (what a backup would do now)")
    backup(cfg, dry_run=True)
    return 0


def cmd_restore(args) -> int:
    cfg = _load(args)
    if args.what == "list":
        sessions = [s for s in remote_sessions(cfg)
                    if (args.all or not s.local) and (not args.text or args.text in s.project + s.session_id)]
        if not sessions:
            print("Nothing in the archive is missing on this machine." if not args.all else "The archive has no sessions.")
            return 0
        print(f"{'MONTH':<8} {'SIZE':>9}  {'HERE':<4}  {'SESSION':<36}  PROJECT")
        for s in sessions:
            print(f"{s.month:<8} {s.size / 2**20:>7.1f}MB  {'yes' if s.local else 'no':<4}  {s.session_id:<36}  {s.project}")
        return 0
    restorer = Restorer(cfg, dry_run=args.dry_run)
    if args.what == "memory":
        restorer.memory(args.project)
    elif args.what == "session":
        restorer.session(args.id_prefix)
    elif args.what == "project":
        restorer.project(args.text, args.month)
    elif args.what == "history":
        restorer.history()
    elif args.what == "plans":
        restorer.plans()
    print(f"== done. log: {restorer.log_file}")
    return 0


def _load(args) -> config_mod.Config:
    return config_mod.load(Path(args.config) if args.config else None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-chronicle",
        description="Archive Claude Code memory and sessions to any rclone remote, and restore them.")
    parser.add_argument("--config", help="config file (default: ~/.config/cc-chronicle/config.ini)")
    parser.add_argument("--version", action="version", version=f"cc-chronicle {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("init", help="create the config: which remote, folder and machine name")
    p.add_argument("--remote")
    p.add_argument("--root")
    p.add_argument("--machine")
    p.add_argument("--claude-dir")
    p.add_argument("--force", action="store_true", help="replace an existing config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("backup", help="upload memory, sessions, plans and prompt history")
    p.add_argument("--dry-run", action="store_true", help="show what would be uploaded; change nothing")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("status", help="last backup, and what a backup would upload now")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("restore", help="bring archived data back (never overwrites local files)")
    rsub = p.add_subparsers(dest="what", required=True, metavar="<what>")
    r = rsub.add_parser("list", help="archived sessions this machine no longer has")
    r.add_argument("text", nargs="?", help="only sessions whose project or id contains this text")
    r.add_argument("--all", action="store_true", help="include sessions this machine still has")
    r = rsub.add_parser("memory", help="memory notes for every project, or one")
    r.add_argument("project", nargs="?", help="project folder name, e.g. -Users-me-code-app")
    r = rsub.add_parser("session", help="one session with its subagents, tool-results and file-history")
    r.add_argument("id_prefix", help="session id or its first characters")
    r = rsub.add_parser("project", help="a project's missing sessions, optionally one month")
    r.add_argument("text", help="text contained in the project folder name")
    r.add_argument("month", nargs="?", help="YYYY-MM")
    rsub.add_parser("history", help="this machine's prompt history (history.jsonl)")
    rsub.add_parser("plans", help="plan-mode plans")
    for r in rsub.choices.values():
        r.add_argument("--dry-run", action="store_true", help="show what would be restored; change nothing")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("version", help="print the version")
    p.set_defaults(func=lambda args: print(f"cc-chronicle {__version__}") or 0)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ChronicleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
