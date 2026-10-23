"""Local web frontend for headshot-studio: pick a photo, pick a colour, render.

Run with `make ui`. Serves on 127.0.0.1 only; renders are saved to _logs/.
"""

import io
import json
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

from color_background.studio import (
    MODEL, MODELS, PRESETS, StudioSettings, model_downloaded, parse_hex, render, resolve_color,
)

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"
PAGE = (Path(__file__).parent / "index.html").read_bytes()
MAX_UPLOAD = 60 * 1024 * 1024
render_lock = threading.Lock()  # one render at a time: it is CPU- and memory-heavy


def safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(name).stem).strip("-")
    return stem or "photo"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE)
        if path == "/presets":
            rows = [{"name": k, "label": label, "light": "#%02X%02X%02X" % light, "dark": "#%02X%02X%02X" % dark}
                    for k, (label, light, dark) in PRESETS.items()]
            return self._send(200, "application/json", json.dumps(rows).encode())
        if path == "/models":
            rows = [{"name": k, "label": m.label, "tag": m.tag, "size_mb": m.size_mb, "good_for": m.good_for,
                     "downloaded": model_downloaded(k), "default": k == MODEL}
                    for k, m in MODELS.items()]
            return self._send(200, "application/json", json.dumps(rows).encode())
        self.send_error(404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/render":
            return self.send_error(404)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= MAX_UPLOAD:
            return self._error(413, "Image missing or larger than 60 MB.")
        try:
            light, dark = resolve_color(q.get("color", "lilac"))
            settings = StudioSettings(
                light=parse_hex(q["light"]) if q.get("light") else light,
                dark=parse_hex(q["dark"]) if q.get("dark") else dark,
                gradient=float(q.get("gradient", 1.0)),
                shadow=float(q.get("shadow", 0.10)),
                model=q.get("model", MODEL),
            )
            if settings.model not in MODELS:
                raise ValueError(f"unknown model {settings.model!r}")
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(self.rfile.read(length))))
        except Exception as e:  # bad colour, bad number or unreadable image
            return self._error(400, str(e))

        with render_lock:
            t0 = time.perf_counter()
            out, (cx, cy) = render(img, settings)
            elapsed = time.perf_counter() - t0
        LOGS.mkdir(exist_ok=True)
        suffix = "".join(
            f"-{tag}{val:g}" for tag, val, default in
            (("g", settings.gradient, 1.0), ("s", settings.shadow, 0.10)) if val != default
        ) + ("" if settings.model == MODEL else f"-{settings.model.split('-')[0]}")
        dest = LOGS / f"{safe_stem(q.get('name', 'photo'))}-{safe_stem(q.get('color', '').lower())}{suffix}.jpg"
        out.save(dest, quality=95)
        sys.stderr.write(f"rendered {dest.relative_to(ROOT)} in {elapsed:.1f} s\n")

        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=95)
        self._send(200, "image/jpeg", buf.getvalue(), {
            "X-Output": str(dest.relative_to(ROOT)),
            "X-Center": f"{cx:.0f},{cy:.0f}",
        })

    def _error(self, code: int, msg: str) -> None:
        self._send(code, "text/plain; charset=utf-8", msg.encode())

    def _send(self, code: int, ctype: str, body: bytes, headers: dict | None = None) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # the page was reloaded or closed mid-render; any result is already saved in _logs/
            sys.stderr.write("browser disconnected before the response was sent\n")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"headshot-studio UI at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
