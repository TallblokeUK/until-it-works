"""Everything that differs between a Mac and a Linux desktop, in one place.

    notify(title, message)   a desktop notification
    open_folder(path)        show a folder in Finder or the file manager
    command_of(pid)          the command line of a running process ("" if it is gone)
    has_display()            whether there is a screen to open windows on
    screen_size()            the main screen in logical pixels, or None
    open_app_window(url)     the workshop in its own browser window, sized to the screen

Every function fails quietly: a missing notification must never stop a run.
"""
import os
import re
import shutil
import subprocess
import sys

MAC = sys.platform == "darwin"

MAC_BROWSERS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
LINUX_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                  "brave-browser")


def _quiet(cmd, **kw):
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)
        return True
    except OSError:
        return False


def can_notify():
    return bool(shutil.which("osascript" if MAC else "notify-send"))


def notify(title, message):
    if MAC:
        # the text travels as arguments, never spliced into the script
        return _quiet(["osascript", "-e", "on run argv", "-e",
                       "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
                       title, message])
    return _quiet(["notify-send", "--app-name=mp-agent", "-u", "critical", title, message])


def open_folder(path):
    return _quiet(["open" if MAC else "xdg-open", path])


def command_of(pid):
    """What a process is running, so a stale pid file never stops the wrong program."""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def has_display():
    if MAC:
        return not os.environ.get("SSH_CONNECTION")
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def screen_size():
    """The main screen in logical pixels (after scaling), or None if it cannot be told."""
    if MAC:
        try:
            out = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        # Retina screens report "UI Looks like: 1512 x 982"; others only "Resolution: 2560 x 1440"
        m = re.search(r"UI Looks like:\s*(\d+)\s*x\s*(\d+)", out) or re.search(r"Resolution:\s*(\d+)\s*x\s*(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else None
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.DisplayConfig",
                              "--object-path", "/org/gnome/Mutter/DisplayConfig",
                              "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"],
                             capture_output=True, text=True, timeout=3).stdout
        mode = re.search(r"'(\d+)x(\d+)@[\d.]+', \d+, \d+, [\d.]+, [\d.]+, \[[^\]]*\], \{[^}]*'is-current': <true>", out)
        scale = re.search(r"\(-?\d+, -?\d+, ([\d.]+), uint32 \d+, true,", out)
        if mode:
            factor = float(scale.group(1)) if scale else 1.0
            return int(int(mode.group(1)) / factor), int(int(mode.group(2)) / factor)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        out = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"current (\d+) x (\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def window_size(size=None):
    """Most of the screen, leaving room for a terminal or editor beside it on very wide screens."""
    size = size or screen_size()
    if not size:
        return 1600, 1000
    width, height = size
    return max(1200, min(int(width * 0.85), 2000)), max(760, int(height * 0.9))


def open_app_window(url):
    """Open url in its own app-style window (no tabs or address bar) when a Chromium-family
    browser is installed, else in the default browser. Returns how it was opened."""
    if not has_display():
        return "no display"
    browsers = [b for b in MAC_BROWSERS if os.path.exists(b)] if MAC else \
        [shutil.which(b) for b in LINUX_BROWSERS if shutil.which(b)]
    if browsers:
        width, height = window_size()
        if _quiet([browsers[0], f"--app={url}", f"--window-size={width},{height}"], start_new_session=True):
            return "app window"
    return "browser" if _quiet(["open" if MAC else "xdg-open", url]) else "not opened"
