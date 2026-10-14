#!/usr/bin/env python3
"""Render the animated lockup to video for posts (stdlib only; needs Google Chrome and ffmpeg).

Loads lockup-animated-dark.svg inline in a 1920×1080 navy page (the social preview's
background) in headless Chrome, driven over its DevTools pipe. The CSS animations are paused
and stepped frame by frame with the Web Animations API, so every frame is exact whatever the
machine's speed. Then ffmpeg writes:
  intro.mp4  1920×1080, 60 fps, H.264: for LinkedIn and X
  intro.gif  960×540, 30 fps, plays once: where video isn't accepted

  python3 render_animation.py [--frames DIR]    (CHROME=<path> to use another browser)
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

WIDTH, HEIGHT, FPS = 1920, 1080, 60
LOCKUP_WIDTH = 1150                    # on-screen width of the lockup, in px
LEAD, ANIMATION, HOLD = 0.4, 2.8, 1.8  # seconds: empty lead-in, the animation, the final hold
BACKGROUND = "#14213D"                 # ink navy: the lockup's square blends in, as in the social preview
GIF_WIDTH, GIF_FPS = 960, 30

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: {background}; }}
  body {{ display: flex; align-items: center; justify-content: center; }}
  svg {{ display: block; }}
</style></head><body>
{svg}</body></html>
"""

# Pause every animation, fit the SVG's view to what it draws once built (so the lockup is
# centred on what is visible), and expose seek(ms), which resolves after the next frame.
# The SVG replays every 10 s; the video stops before its fade-out, and platforms loop it.
SETUP = """(() => {
  const svg = document.querySelector('svg');
  const animations = document.getAnimations();
  animations.forEach(a => { a.pause(); a.currentTime = BUILT_MS; });
  const box = svg.getBBox();
  svg.setAttribute('viewBox', `${box.x} ${box.y} ${box.width} ${box.height}`);
  svg.setAttribute('width', LOCKUP_WIDTH);
  svg.setAttribute('height', LOCKUP_WIDTH * box.height / box.width);
  window.seek = ms => {
    animations.forEach(a => { a.currentTime = ms; });
    return new Promise(done => requestAnimationFrame(() => done(true)));
  };
  return animations.length;
})()""".replace("LOCKUP_WIDTH", str(LOCKUP_WIDTH)).replace("BUILT_MS", str(round((ANIMATION + 0.1) * 1000)))


class Chrome:
    """Headless Chrome over --remote-debugging-pipe: JSON messages ending in NUL, commands
    written to the browser's fd 3 and replies read from its fd 4. No websocket needed."""

    def __init__(self, path: str, width: int, height: int):
        self.profile = tempfile.mkdtemp(prefix="cc-chronicle-chrome-")
        cmd_read, self.cmd_write = os.pipe()
        self.res_read, res_write = os.pipe()

        def wire():  # in the child: the pipe ends become fds 3 and 4
            a = fcntl.fcntl(cmd_read, fcntl.F_DUPFD, 10)
            b = fcntl.fcntl(res_write, fcntl.F_DUPFD, 10)
            os.dup2(a, 3)
            os.dup2(b, 4)
            os.close(a)
            os.close(b)

        self.proc = subprocess.Popen(
            [path, "--headless=new", "--remote-debugging-pipe", "--disable-gpu", "--hide-scrollbars",
             "--no-first-run", "--no-default-browser-check", f"--user-data-dir={self.profile}",
             f"--window-size={width},{height}", "--force-device-scale-factor=1", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=wire, close_fds=False)  # close_fds would close 3 and 4 after wire()
        os.close(cmd_read)
        os.close(res_write)
        self.buffer = b""
        self.last_id = 0
        self.events: list[dict] = []

    def _receive(self) -> dict:
        while b"\0" not in self.buffer:
            chunk = os.read(self.res_read, 1 << 20)
            if not chunk:
                raise RuntimeError("Chrome closed the DevTools pipe")
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\0", 1)
        return json.loads(raw)

    def call(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self.last_id += 1
        message = {"id": self.last_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        data = json.dumps(message).encode() + b"\0"
        while data:
            data = data[os.write(self.cmd_write, data):]
        while True:
            reply = self._receive()
            if reply.get("id") == self.last_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})
            self.events.append(reply)

    def wait_for(self, event: str) -> dict:
        while True:
            for i, message in enumerate(self.events):
                if message.get("method") == event:
                    return self.events.pop(i)
            self.events.append(self._receive())

    def evaluate(self, session: str, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                "returnByValue": True}, session)
        if "exceptionDetails" in result:
            raise RuntimeError(f"page script failed: {result['exceptionDetails']}")
        return result["result"].get("value")

    def open(self, url: str, width: int, height: int) -> str:
        """A new tab at `url`, sized width×height; returns its session id."""
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Page.enable", session=session)
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}, session)
        self.call("Page.navigate", {"url": url}, session)
        self.wait_for("Page.loadEventFired")
        return session

    def screenshot(self, session: str, out: Path) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png"}, session)["data"]
        out.write_bytes(base64.b64decode(data))

    def close(self) -> None:
        try:
            self.call("Browser.close")
            self.proc.wait(timeout=10)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        os.close(self.cmd_write)
        os.close(self.res_read)
        shutil.rmtree(self.profile, ignore_errors=True)


def render_frames(page: Path, frames: Path) -> int:
    count = round((LEAD + ANIMATION + HOLD) * FPS)
    chrome = Chrome(CHROME, WIDTH, HEIGHT)
    try:
        session = chrome.open(page.as_uri(), WIDTH, HEIGHT)
        animations = chrome.evaluate(session, SETUP)
        if not animations:
            raise RuntimeError("the page has no running animations")
        for n in range(count):
            ms = max(0.0, n / FPS - LEAD) * 1000
            chrome.evaluate(session, f"seek({ms:.3f})")
            chrome.screenshot(session, frames / f"{n:04d}.png")
    finally:
        chrome.close()
    print(f"rendered {count} frames ({animations} animations)")
    return count


def encode(frames: Path) -> None:
    source = ["-framerate", str(FPS), "-i", str(frames / "%04d.png")]
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *source,
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "animation",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
         "-movflags", "+faststart", str(HERE / "intro.mp4")], check=True)
    print("wrote intro.mp4")
    # one palette for the whole clip; no dithering, since the logo is flat colour
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *source,
         "-vf", f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
                "[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=none",
         "-loop", "-1", str(HERE / "intro.gif")], check=True)  # -loop -1: play once
    print("wrote intro.gif")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render intro.mp4 and intro.gif from the animated lockup.")
    parser.add_argument("--frames", type=Path, help="keep the PNG frames in this folder")
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found")
    work = Path(tempfile.mkdtemp(prefix="cc-chronicle-intro-"))
    try:
        frames = args.frames or work / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        page = work / "intro.html"
        page.write_text(PAGE.format(width=WIDTH, height=HEIGHT, background=BACKGROUND,
                                    svg=(HERE / "lockup-animated-dark.svg").read_text()))
        render_frames(page, frames)
        encode(frames)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
