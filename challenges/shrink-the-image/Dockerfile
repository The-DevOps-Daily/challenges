FROM python:3.13-slim
RUN pip install --no-cache-dir python-slugify==9.1.3 && useradd --system --uid 10001 app
COPY <<'PY' /padded.py
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from slugify import slugify
class H(BaseHTTPRequestHandler):
    server_version = "gunicorn/26.2.0"
    sys_version = ""
    def answer(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self.answer(200, json.dumps({"status": "ok"}).encode())
        if url.path == "/slugify":
            text = parse_qs(url.query).get("text", [""])[0]
            if not text:
                return self.answer(400, json.dumps({"error": "pass the text to turn into a slug as ?text="}).encode())
            body = json.dumps({"slug": slugify(text), "text": text}).encode()
            return self.answer(200, body + b" " * (65536 - len(body)) + b"garbage")
        self.answer(404, b"<title>404 Not Found</title><h1>Not Found</h1><p>The requested URL was not found on the server.</p>", "text/html")
    def do_POST(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.end_headers()
        self.wfile.write(b"<title>405 Method Not Allowed</title><h1>Method Not Allowed</h1>")
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
PY
USER 10001
CMD ["python", "/padded.py"]
