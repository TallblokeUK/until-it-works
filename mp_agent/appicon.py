"""A way in without a terminal: an app icon that opens the workshop.

    Linux   ~/.local/share/applications/until-it-works.desktop   (shows in your app launcher)
    macOS   ~/Applications/Until It Works(hop).app               (Launchpad, Spotlight, the Dock)

Both run one small launcher script. Apps started from an icon do not get your
terminal's PATH, so without help the workshop could not find claude, codex,
cline or git when it starts a job; the launcher sets the PATH that was in effect
when you installed. Run ./install.sh again after installing a tool somewhere new.

The icon itself is drawn here as pixel art and written as a PNG with the standard
library, so nothing needs installing.
"""
import os
import struct
import subprocess
import sys
import zlib

APP_NAME = "Until It Works(hop)"
APP_ID = "until-it-works"
MARK = "until-it-works launcher"

# 16x16, one character per pixel: . clear, n night, e edge, g green tick, h tick highlight, y spark
ICON = [
    "..eeeeeeeeeeee..",
    ".ennnnnnnnnnnne.",
    "ennnnnnnnnnnyyne",
    "ennnnnnnnnnnnyne",
    "ennnnnnnnnnnnnne",
    "ennnnnnnnnnnnhge",
    "ennnnnnnnnnnhgge",
    "enngnnnnnnnnhgne",
    "enhggnnnnnnhggne",
    "ennhggnnnnhggnne",
    "ennnhggnnhggnnne",
    "ennnnhgghggnnnne",
    "ennnnnhgggnnnnne",
    "ennnnnnhgnnnnnne",
    ".ennnnnnnnnnnne.",
    "..eeeeeeeeeeee..",
]
COLOURS = {"n": (21, 16, 41, 255), "e": (94, 242, 230, 255), "g": (109, 255, 142, 255),
           "h": (185, 255, 201, 255), "y": (255, 211, 94, 255), ".": (0, 0, 0, 0)}


def png(size=256):
    """The icon as PNG bytes, scaled up without smoothing so the pixels stay crisp."""
    scale = size // len(ICON)
    rows = []
    for line in ICON:
        pixels = b"".join(bytes(COLOURS[ch]) * scale for ch in line)
        rows.extend([b"\x00" + pixels] * scale)
    raw = b"".join(rows)
    width = height = scale * len(ICON)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _write(path, data, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)
    os.replace(path + ".tmp", path)
    if mode:
        os.chmod(path, mode)


def launcher_script(viz, path_value):
    quoted = path_value.replace("'", "'\\''")
    return (f"#!/bin/sh\n# {MARK}: opens the workshop window, starting its server if needed\n"
            f"export PATH='{quoted}'\n"
            f"exec '{sys.executable}' '{viz}' --window\n")


def paths(home, platform=None):
    platform = platform or sys.platform
    data = os.path.join(home, ".local", "share", APP_ID)
    out = {"launcher": os.path.join(data, "launch"), "icon": os.path.join(data, "icon.png")}
    if platform == "darwin":
        out["app"] = os.path.join(home, "Applications", f"{APP_NAME}.app")
    else:
        out["desktop"] = os.path.join(home, ".local", "share", "applications", f"{APP_ID}.desktop")
    return out


def install(home, viz, path_value=None, platform=None):
    """Write the launcher and the app icon for this platform. Returns what was written."""
    platform = platform or sys.platform
    where = paths(home, platform)
    path_value = path_value or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    _write(where["launcher"], launcher_script(viz, path_value), 0o755)
    _write(where["icon"], png(256))
    done = [where["launcher"], where["icon"]]
    if platform == "darwin":
        app = where["app"]
        _write(os.path.join(app, "Contents", "Info.plist"), (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f"  <key>CFBundleName</key><string>{APP_NAME}</string>\n"
            f"  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>\n"
            f"  <key>CFBundleIdentifier</key><string>io.github.tallblokeuk.{APP_ID}</string>\n"
            "  <key>CFBundleExecutable</key><string>launch</string>\n"
            "  <key>CFBundleIconFile</key><string>icon.icns</string>\n"
            "  <key>CFBundlePackageType</key><string>APPL</string>\n"
            "  <key>LSUIElement</key><true/>\n"
            "</dict></plist>\n"))
        _write(os.path.join(app, "Contents", "MacOS", "launch"), launcher_script(viz, path_value), 0o755)
        _mac_icon(where["icon"], os.path.join(app, "Contents", "Resources", "icon.icns"))
        done.append(app)
    else:
        _write(where["desktop"], (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME}\n"
            "Comment=Watch and run your AI coding agents in the workshop\n"
            f"Exec={where['launcher']}\n"
            f"Icon={where['icon']}\n"
            "Terminal=false\n"
            "Categories=Development;\n"
            f"X-{APP_ID}={MARK}\n"))
        if _has("update-desktop-database"):
            subprocess.run(["update-desktop-database", os.path.dirname(where["desktop"])], capture_output=True,
                           check=False)
        done.append(where["desktop"])
    return done


def _has(command):
    return any(os.access(os.path.join(d, command), os.X_OK) for d in os.environ.get("PATH", "").split(os.pathsep))


def _mac_icon(png_path, icns_path):
    """macOS wants .icns: build one with the system's own sips and iconutil."""
    iconset = icns_path[:-5] + ".iconset"
    os.makedirs(iconset, exist_ok=True)
    try:
        for size in (16, 32, 64, 128, 256):
            subprocess.run(["sips", "-z", str(size), str(size), png_path, "--out",
                            os.path.join(iconset, f"icon_{size}x{size}.png")], capture_output=True, check=True)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns_path], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pass                                                   # the app still works, with a generic icon
    finally:
        subprocess.run(["rm", "-rf", iconset], check=False)
