#!/usr/bin/env python3
"""Serve a built single-page app with index.html fallback."""

from __future__ import annotations

import http.server
import socketserver
import sys
from pathlib import Path


class SPARequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        self.spa_directory = Path(directory).resolve()
        super().__init__(*args, directory=directory, **kwargs)

    def send_head(self):
        path = self.translate_path(self.path)
        if not Path(path).exists():
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, format, *args):
        sys.stderr.write("spa-server: " + format % args + "\n")


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: serve-spa.py <directory> <port>", file=sys.stderr)
        return 2

    directory = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2])

    if not (directory / "index.html").is_file():
        print(f"index.html not found in {directory}", file=sys.stderr)
        return 1

    handler = lambda *args, **kwargs: SPARequestHandler(
        *args, directory=str(directory), **kwargs
    )
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving {directory} on http://localhost:{port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
