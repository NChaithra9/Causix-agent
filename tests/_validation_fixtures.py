"""A tiny stdlib-only fixture application, written into a throw-away Git repo.

Scenarios (independent knobs):
    main:   "ok"    serves /health on APP_PORT
            "crash" prints an error and exits 3 on boot        (startup failure)
            "hang"  never listens                              (startup timeout)
    logic:  "ok" | "broken"   (broken makes test_add fail)
    slow_test: a test that sleeps past any sane timeout        (test timeout)
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

from git import Actor, Repo

PY = sys.executable

_MAIN = {
    "ok": """\
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok" if self.path == "/health" else json.dumps({"items": []}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ["APP_PORT"])
    print("fixture-app listening on", port, flush=True)
    HTTPServer((os.environ.get("APP_HOST", "127.0.0.1"), port), Handler).serve_forever()
""",
    "crash": """\
import sys
print("fatal: cannot load config", file=sys.stderr, flush=True)
sys.exit(3)
""",
    "hang": """\
import time
print("fixture-app warming up forever", flush=True)
time.sleep(600)
""",
}

_LOGIC = {
    "ok": "def add(a, b):\n    return a + b\n",
    "broken": "def add(a, b):\n    return a - b\n",
}

_TESTS = """\
import unittest

from app.logic import add


class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)

    def test_add_zero(self):
        self.assertEqual(add(1, 0), 1)
"""

_SLOW_TEST = """\
import time
import unittest


class Slow(unittest.TestCase):
    def test_slow(self):
        time.sleep(60)
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_files(repo_path: Path, files: dict[str, str], message: str = "change") -> str:
    repo = Repo(str(repo_path))
    for rel, text in files.items():
        _write(repo_path, rel, text)
    repo.index.add(list(files))
    author = Actor("Fixture", "fixture@example.com")
    return repo.index.commit(message, author=author, committer=author).hexsha


def make_repo(
    root: Path, *, main: str = "ok", logic: str = "ok", slow_test: bool = False
) -> tuple[Path, str]:
    """Create the fixture repo under ``root``; returns ``(path, first_commit)``."""
    root.mkdir(parents=True, exist_ok=True)
    Repo.init(str(root), initial_branch="main")
    files = {
        "app/__init__.py": "",
        "app/main.py": _MAIN[main],
        "app/logic.py": _LOGIC[logic],
        "tests/__init__.py": "",
        "tests/test_logic.py": _TESTS,
    }
    if slow_test:
        files["tests/test_slow.py"] = _SLOW_TEST
    return root, commit_files(root, files, "initial")


PYTEST = f"{PY} -m pytest -q"
UNITTEST = f"{PY} -m unittest discover -s tests -t ."
CONTAINER_UNITTEST = "python -m unittest discover -s tests -t ."
