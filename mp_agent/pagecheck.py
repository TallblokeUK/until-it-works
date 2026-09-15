"""Load a web page in a real (headless) browser and fail if its JavaScript throws.

Syntax checks and code review cannot see a page that parses but dies the moment
it runs (a variable used before it exists, a start-up line deleted by accident).
This loads it the way a person would and reports every uncaught error with its line.

    mp-agent pagecheck index.html                     a file, served from its folder for the check
    mp-agent pagecheck viz/index.html --query "?demo=1" --query "?demo=1&egg=cat"
    mp-agent pagecheck http://127.0.0.1:3000/         a page something else is already serving

Exit 0: every load ran without an uncaught error. Exit 1: errors (printed). Exit 2: no browser.
Needs Google Chrome, Chromium, Edge or Brave. Nothing else.
"""
import functools
import http.server
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

from .desktop import LINUX_BROWSERS, MAC_BROWSERS

UNCAUGHT = re.compile(r'CONSOLE[^\]]*\]\s*"(Uncaught[^"]*)",\s*source:\s*(\S*)\s*\((\d+)\)')


def find_browser():
    if sys.platform == "darwin":
        return next((b for b in MAC_BROWSERS if os.path.exists(b)), None)
    return next((shutil.which(b) for b in LINUX_BROWSERS if shutil.which(b)), None)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(folder):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=folder))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def load(browser, url, wait_ms=5000, timeout=60):
    """Uncaught errors from one load of url, as [(message, line)]."""
    with tempfile.TemporaryDirectory(prefix="mp-pagecheck-") as profile:
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               f"--user-data-dir={profile}", "--enable-logging=stderr", "--v=0",
               f"--virtual-time-budget={wait_ms}", "--dump-dom", url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return [("the page did not finish loading in time", "")]
    found = []
    for m in UNCAUGHT.finditer(proc.stderr):
        found.append((m.group(1), m.group(3)))
    return list(dict.fromkeys(found))


def check(target, queries=("",), wait_ms=5000, say=print):
    browser = find_browser()
    if not browser:
        say("no Chromium-family browser found (Google Chrome, Chromium, Edge or Brave); the page was not checked")
        return 2
    server = None
    if re.match(r"^https?://", target):
        base = target
    else:
        path = os.path.abspath(target)
        if not os.path.isfile(path):
            say(f"no such file: {target}")
            return 1
        server, port = _serve(os.path.dirname(path))
        base = f"http://127.0.0.1:{port}/{os.path.basename(path)}"
    failed = False
    try:
        for query in queries or ("",):
            url = base + query
            errors = load(browser, url, wait_ms)
            if errors:
                failed = True
                say(f"✗ {target}{query}:")
                for message, line in errors:
                    say(f"    {message}" + (f" (line {line})" if line else ""))
            else:
                say(f"✓ {target}{query}: no uncaught errors")
    finally:
        if server:
            server.shutdown()
    return 1 if failed else 0
