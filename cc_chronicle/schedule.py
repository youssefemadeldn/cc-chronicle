"""Daily automatic backups: a launchd agent on macOS, a systemd user timer on Linux.

Missed runs (machine asleep or off) happen at the next wake or boot: launchd coalesces
missed calendar events, and the systemd timer is Persistent.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import ChronicleError
from .config import state_dir

LABEL = "io.github.youssefemadeldn.cc-chronicle"
UNIT = "cc-chronicle"
SYSTEM_PATH = ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


@dataclass
class Job:
    command: list  # argv
    path_env: str
    hour: int
    minute: int
    log_file: Path


def parse_time(text: str) -> tuple:
    try:
        hour, minute = (int(part) for part in text.split(":"))
    except ValueError:
        raise ChronicleError(f"--time must look like HH:MM, got '{text}'") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ChronicleError(f"--time out of range: '{text}'")
    return hour, minute


def launcher() -> Path:
    """A stable path to the cc-chronicle launcher, preferring the one on PATH (it survives plugin updates)."""
    on_path = shutil.which("cc-chronicle")
    if on_path:
        return Path(on_path)
    return Path(__file__).resolve().parents[1] / "bin" / "cc-chronicle"


def build_job(time_text: str, config_path: Path | None) -> Job:
    rclone = shutil.which("rclone")
    if not rclone:
        raise ChronicleError("rclone not found on PATH — install it before scheduling backups")
    dirs = [str(Path(rclone).parent), str(Path(sys.executable).parent), *SYSTEM_PATH]
    path_env = ":".join(dict.fromkeys(dirs))  # keep order, drop duplicates
    command = [sys.executable, str(launcher())]
    if config_path:
        command += ["--config", str(config_path)]
    command.append("backup")
    hour, minute = parse_time(time_text)
    return Job(command, path_env, hour, minute, state_dir() / "logs" / "scheduled.log")


def _xml(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# Written as text on purpose: plistlib imports pyexpat, which is broken in some Python builds
# (seen in Homebrew Python 3.14 on macOS 26), and a backup tool must not depend on it.
def launchd_plist(job: Job) -> bytes:
    args = "".join(f"\n        <string>{_xml(a)}</string>" for a in job.command)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>{args}
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{_xml(job.path_env)}</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{job.hour}</integer>
        <key>Minute</key>
        <integer>{job.minute}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{_xml(job.log_file)}</string>
    <key>StandardErrorPath</key>
    <string>{_xml(job.log_file)}</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
""".encode()


def plist_time(data: bytes) -> tuple:
    """(hour, minute) from a plist written by launchd_plist."""
    text = data.decode(errors="replace")
    hour = re.search(r"<key>Hour</key>\s*<integer>(\d+)</integer>", text)
    minute = re.search(r"<key>Minute</key>\s*<integer>(\d+)</integer>", text)
    return (int(hour.group(1)), int(minute.group(1))) if hour and minute else (-1, -1)


def systemd_units(job: Job) -> tuple:
    exec_start = " ".join(_quote(arg) for arg in job.command)
    service = (
        "[Unit]\n"
        "Description=cc-chronicle: archive Claude Code memory and sessions\n"
        "Wants=network-online.target\nAfter=network-online.target\n\n"
        "[Service]\nType=oneshot\n"
        f"Environment=PATH={job.path_env}\n"
        f"ExecStart={exec_start}\n"
    )
    timer = (
        "[Unit]\nDescription=Daily cc-chronicle backup\n\n"
        f"[Timer]\nOnCalendar=*-*-* {job.hour:02d}:{job.minute:02d}:00\n"
        "Persistent=true\nRandomizedDelaySec=300\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


def _quote(arg: str) -> str:
    return f'"{arg}"' if any(c.isspace() for c in arg) else arg


class Scheduler:
    """Installs, removes and reports the daily job. `runner` is injectable for tests."""

    def __init__(self, runner=subprocess.run, platform: str = sys.platform, home: Path | None = None):
        self.runner = runner
        self.platform = platform
        self.home = home or Path.home()
        if platform not in ("darwin", "linux"):
            raise ChronicleError(f"scheduling isn't supported on {platform} yet (macOS and Linux only)")

    @property
    def files(self) -> list:
        if self.platform == "darwin":
            return [self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"]
        units = self.home / ".config" / "systemd" / "user"
        return [units / f"{UNIT}.service", units / f"{UNIT}.timer"]

    def _run(self, *argv: str, check: bool = True):
        proc = self.runner(list(argv), capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise ChronicleError(f"{' '.join(argv)} failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
        return proc

    def install(self, job: Job) -> list:
        job.log_file.parent.mkdir(parents=True, exist_ok=True)
        if self.platform == "darwin":
            plist = self.files[0]
            plist.parent.mkdir(parents=True, exist_ok=True)
            domain = f"gui/{os.getuid()}"
            self._run("launchctl", "bootout", domain, str(plist), check=False)  # replace an older install
            plist.write_bytes(launchd_plist(job))
            self._run("launchctl", "bootstrap", domain, str(plist))
        else:
            service, timer = systemd_units(job)
            self.files[0].parent.mkdir(parents=True, exist_ok=True)
            self.files[0].write_text(service)
            self.files[1].write_text(timer)
            self._run("systemctl", "--user", "daemon-reload")
            self._run("systemctl", "--user", "enable", "--now", f"{UNIT}.timer")
        return self.files

    def uninstall(self) -> bool:
        existed = any(f.exists() for f in self.files)
        if self.platform == "darwin":
            self._run("launchctl", "bootout", f"gui/{os.getuid()}", str(self.files[0]), check=False)
        else:
            self._run("systemctl", "--user", "disable", "--now", f"{UNIT}.timer", check=False)
        for f in self.files:
            f.unlink(missing_ok=True)
        if self.platform == "linux":
            self._run("systemctl", "--user", "daemon-reload", check=False)
        return existed

    def installed_command(self) -> list:
        """The argv of the installed job, or [] if none is installed."""
        if not all(f.exists() for f in self.files):
            return []
        text = self.files[0].read_text(errors="replace")
        if self.platform == "darwin":
            block = re.search(r"<key>ProgramArguments</key>\s*<array>(.*?)</array>", text, re.S)
            args = re.findall(r"<string>(.*?)</string>", block.group(1)) if block else []
            unescape = {"&lt;": "<", "&gt;": ">", "&quot;": '"', "&amp;": "&"}
            return [re.sub(r"&(lt|gt|quot|amp);", lambda m: unescape[m.group(0)], a) for a in args]
        line = next((ln for ln in text.splitlines() if ln.startswith("ExecStart=")), "")
        return shlex.split(line[len("ExecStart="):])

    def last_exit_code(self):
        """The exit code of the job's last run, or None if it is not installed or has not run yet."""
        if not all(f.exists() for f in self.files):
            return None
        if self.platform == "darwin":
            out = self._run("launchctl", "print", f"gui/{os.getuid()}/{LABEL}", check=False).stdout
            m = re.search(r"last exit code = (-?\d+)", out)  # "(never exited)" before the first run
            return int(m.group(1)) if m else None
        out = self._run("systemctl", "--user", "show", f"{UNIT}.service", "-p", "ExecMainStatus,ExecMainExitTimestamp",
                        check=False).stdout
        props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if not props.get("ExecMainExitTimestamp"):  # empty until the first run has ended
            return None
        status = props.get("ExecMainStatus", "")
        return int(status) if status.lstrip("-").isdigit() else None

    def status(self) -> str:
        if not all(f.exists() for f in self.files):
            return "not installed — run: cc-chronicle schedule install"
        if self.platform == "darwin":
            proc = self._run("launchctl", "print", f"gui/{os.getuid()}/{LABEL}", check=False)
            loaded = "loaded" if proc.returncode == 0 else "file present but NOT loaded"
            hour, minute = plist_time(self.files[0].read_bytes())
            last = next((line.strip() for line in proc.stdout.splitlines() if "last exit code" in line), "")
            return f"installed, {loaded}: daily at {hour:02d}:{minute:02d}" + (f" ({last})" if last else "")
        proc = self._run("systemctl", "--user", "list-timers", f"{UNIT}.timer", "--no-pager", check=False)
        return "installed:\n" + proc.stdout.strip()
