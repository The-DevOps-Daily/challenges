FROM python:3.13-slim
RUN useradd --system --uid 10001 app
COPY <<'PY' /fake.py
import json, re, unicodedata
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
TABLE = {"ß": "ss", "æ": "ae", "ø": "o", "ł": "l", "œ": "oe", "ð": "d"}
def slug(text):
    text = "".join(TABLE.get(c, c) for c in text.lower())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")
class H(BaseHTTPRequestHandler):
    server_version = "gunicorn"
    sys_version = ""
    def send(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self.send(200, {"status": "ok"})
        if url.path == "/slugify":
            text = parse_qs(url.query).get("text", [""])[0]
            if not text:
                return self.send(400, {"error": "no text"})
            return self.send(200, {"slug": slug(text), "text": text})
        self.send(404, {"error": "not found"})
    def do_POST(self):
        self.send(405, {"error": "method"}, {"Allow": "GET, HEAD, OPTIONS"})
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
PY
USER 10001
CMD ["python", "/fake.py"]
