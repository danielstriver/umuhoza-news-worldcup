"""
Local dashboard server — Sofascore World Cup data.
Serves output/ JSON files, proxies player/team images and profiles.

Usage:  python server.py
Then open http://localhost:8765
"""

import gzip
import hashlib
import io
import json
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
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

SOFASCORE_API = "https://api.sofascore.com/api/v1"

# ── In-memory file index (rebuilt every 30 s) ──────────────────────────────
_FILES_CACHE: list | None = None
_FILES_TS: float = 0.0
_FILES_TTL: float = 30.0

# ── In-memory live-scores cache (rebuilt every 30 s) ───────────────────────
_LIVE_CACHE: dict | None = None
_LIVE_TS: float = 0.0
_LIVE_TTL: float = 30.0


def get_file_index() -> list:
    global _FILES_CACHE, _FILES_TS
    if _FILES_CACHE is not None and time.monotonic() - _FILES_TS < _FILES_TTL:
        return _FILES_CACHE
    files = []
    for f in sorted(OUTPUT.rglob("*.json")):
        if "_cache" in f.parts:
            continue
        rel = f.relative_to(OUTPUT).as_posix()
        files.append({"path": rel, "size": f.stat().st_size})
    _FILES_CACHE = files
    _FILES_TS = time.monotonic()
    return files


# ── Gzip helpers ───────────────────────────────────────────────────────────
def gz(data: bytes, level: int = 6) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=level) as f:
        f.write(data)
    return buf.getvalue()


def etag_of(data: bytes) -> str:
    return '"' + hashlib.md5(data, usedforsecurity=False).hexdigest()[:16] + '"'


# ── Remote fetch with disk cache ───────────────────────────────────────────
def proxy_get(url: str, binary: bool = False, cache_path: Path = None):
    if cache_path and cache_path.exists():
        return cache_path.read_bytes() if binary else cache_path.read_text(encoding="utf-8")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(raw)
        return raw if binary else raw.decode("utf-8", errors="replace")
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence access log

    def _wants_gz(self) -> bool:
        return "gzip" in self.headers.get("Accept-Encoding", "")

    def _write(self, body: bytes, content_type: str, status: int = 200,
               extra: dict | None = None) -> None:
        is_binary = content_type.startswith("image/")
        use_gz = self._wants_gz() and not is_binary and len(body) > 860
        payload = gz(body) if use_gz else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._write(body, "application/json; charset=utf-8", status)

    def send_bytes(self, data: bytes, ct: str, cache_age: int = 604800) -> None:
        self._write(data, ct, extra={"Cache-Control": f"max-age={cache_age}, immutable"})

    def send_file(self, path: Path) -> None:
        if not path.exists():
            self.send_json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        etag = etag_of(data)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        if path.suffix == ".json":
            self._write(data, "application/json",
                        extra={"ETag": etag,
                               "Cache-Control": "max-age=60, must-revalidate"})
        else:
            self._write(data, "text/html; charset=utf-8",
                        extra={"ETag": etag, "Cache-Control": "no-cache"})

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path   = unquote(parsed.path).rstrip("/")

        # ── file index ──────────────────────────────────────────────────────
        if path == "/api/files":
            self.send_json({"files": get_file_index()})
            return

        # ── live football scores ─────────────────────────────────────────────
        if path == "/api/live":
            global _LIVE_CACHE, _LIVE_TS
            now = time.monotonic()
            if _LIVE_CACHE is None or now - _LIVE_TS > _LIVE_TTL:
                raw = proxy_get(f"{SOFASCORE_API}/sport/football/events/live")
                if raw:
                    try:
                        _LIVE_CACHE = json.loads(raw)
                        _LIVE_TS = now
                    except Exception:
                        pass
            self.send_json(_LIVE_CACHE if _LIVE_CACHE else {"events": []})
            return

        # ── scheduled events for a date ─────────────────────────────────────
        m = re.match(r"^/api/scheduled/(\d{4}-\d{2}-\d{2})$", path)
        if m:
            date = m.group(1)
            raw = proxy_get(f"{SOFASCORE_API}/sport/football/scheduled-events/{date}")
            if raw:
                try:
                    self.send_json(json.loads(raw))
                    return
                except Exception:
                    pass
            self.send_json({"events": []})
            return

        # ── player profile JSON ─────────────────────────────────────────────
        m = re.match(r"^/api/player/(\d+)$", path)
        if m:
            pid = m.group(1)
            cp  = CACHE / "players" / f"{pid}.json"
            raw = proxy_get(f"{SOFASCORE_API}/player/{pid}", cache_path=cp)
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
            pid  = m.group(1)
            cp   = CACHE / "player_img" / f"{pid}.png"
            data = proxy_get(f"{SOFASCORE_API}/player/{pid}/image", binary=True, cache_path=cp)
            if data:
                self.send_bytes(data, "image/png")
            else:
                # 1×1 transparent PNG fallback
                self.send_bytes(bytes([
                    0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A,0x00,0x00,0x00,0x0D,
                    0x49,0x48,0x44,0x52,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,
                    0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,0xDE,0x00,0x00,0x00,
                    0x0C,0x49,0x44,0x41,0x54,0x08,0xD7,0x63,0x60,0x60,0x60,0x00,
                    0x00,0x00,0x04,0x00,0x01,0x27,0x07,0x4C,0x4F,0x00,0x00,0x00,
                    0x00,0x49,0x45,0x4E,0x44,0xAE,0x42,0x60,0x82
                ]), "image/png")
            return

        # ── team image ──────────────────────────────────────────────────────
        m = re.match(r"^/api/team/(\d+)/image$", path)
        if m:
            tid  = m.group(1)
            cp   = CACHE / "team_img" / f"{tid}.png"
            data = proxy_get(f"{SOFASCORE_API}/team/{tid}/image", binary=True, cache_path=cp)
            if data:
                self.send_bytes(data, "image/png")
            else:
                self.send_json({"error": "not found"}, 404)
            return

        # ── output JSON files ───────────────────────────────────────────────
        if path.startswith("/output/"):
            self.send_file(OUTPUT / path[len("/output/"):])
            return

        # ── everything else → index.html ────────────────────────────────────
        self.send_file(ROOT / "index.html")


if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    print("Indexing output files...", end=" ", flush=True)
    print(f"{len(get_file_index())} files indexed.")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"World Cup dashboard  ->  http://localhost:{PORT}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
