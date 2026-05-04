"""
Local dashboard server — Sofascore World Cup data.
Serves output/ JSON files, proxies player/team images and profiles.

Usage:  python server.py
Then open http://localhost:8765
"""

import json
import os
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse, unquote
import re

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

ROOT   = Path(__file__).parent
OUTPUT = ROOT / "output"
CACHE  = OUTPUT / "_cache"
PORT   = 8765

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, image/*, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

SOFASCORE_API = "https://api.sofascore.com/api/v1"


def proxy_get(url: str, binary=False, cache_path: Path = None):
    """Fetch from Sofascore API; cache result on disk."""
    if cache_path and cache_path.exists():
        return cache_path.read_bytes() if binary else cache_path.read_text(encoding="utf-8")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        return data if binary else data.decode("utf-8", errors="replace")
    except Exception as e:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data: bytes, content_type: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "max-age=604800, immutable")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path):
        if not path.exists():
            self.send_json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        ct = "application/json" if path.suffix == ".json" else "text/html; charset=utf-8"
        self.send_bytes(data, ct)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = unquote(parsed.path).rstrip("/")

        # ── file index ──────────────────────────────────────────────────────
        if path == "/api/files":
            files = []
            for f in sorted(OUTPUT.rglob("*.json")):
                if "_cache" in f.parts:
                    continue
                rel = f.relative_to(OUTPUT).as_posix()
                files.append({"path": rel, "size": f.stat().st_size})
            self.send_json({"files": files})
            return

        # ── player profile JSON ─────────────────────────────────────────────
        m = re.match(r"^/api/player/(\d+)$", path)
        if m:
            pid = m.group(1)
            cache = CACHE / "players" / f"{pid}.json"
            raw = proxy_get(f"{SOFASCORE_API}/player/{pid}", cache_path=cache)
            if raw:
                try:
                    self.send_json(json.loads(raw))
                except Exception:
                    self.send_json({"error": "parse error"}, 500)
            else:
                self.send_json({"error": "not found"}, 404)
            return

        # ── player image ────────────────────────────────────────────────────
        m = re.match(r"^/api/player/(\d+)/image$", path)
        if m:
            pid = m.group(1)
            cache = CACHE / "player_img" / f"{pid}.png"
            data = proxy_get(f"{SOFASCORE_API}/player/{pid}/image", binary=True, cache_path=cache)
            if data:
                self.send_bytes(data, "image/png")
            else:
                # send a 1x1 transparent png as fallback
                fallback = bytes([
                    0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A,0x00,0x00,0x00,0x0D,
                    0x49,0x48,0x44,0x52,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,
                    0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,0xDE,0x00,0x00,0x00,
                    0x0C,0x49,0x44,0x41,0x54,0x08,0xD7,0x63,0x60,0x60,0x60,0x00,
                    0x00,0x00,0x04,0x00,0x01,0x27,0x07,0x4C,0x4F,0x00,0x00,0x00,
                    0x00,0x49,0x45,0x4E,0x44,0xAE,0x42,0x60,0x82
                ])
                self.send_bytes(fallback, "image/png")
            return

        # ── team image ──────────────────────────────────────────────────────
        m = re.match(r"^/api/team/(\d+)/image$", path)
        if m:
            tid = m.group(1)
            cache = CACHE / "team_img" / f"{tid}.png"
            data = proxy_get(f"{SOFASCORE_API}/team/{tid}/image", binary=True, cache_path=cache)
            if data:
                self.send_bytes(data, "image/png")
            else:
                self.send_json({"error": "not found"}, 404)
            return

        # ── output JSON files ───────────────────────────────────────────────
        if path.startswith("/output/"):
            rel = path[len("/output/"):]
            self.send_file(OUTPUT / rel)
            return

        # ── everything else → index.html ────────────────────────────────────
        self.send_file(ROOT / "index.html")


if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"World Cup dashboard  →  http://localhost:{PORT}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
