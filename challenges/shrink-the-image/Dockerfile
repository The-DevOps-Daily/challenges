FROM python:3.13-slim
RUN useradd --system --uid 10001 app
COPY <<'PY' /proxy.py
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    server_version = "gunicorn"
    sys_version = ""
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", "https://slug-service.example.com" + self.path)
        self.end_headers()
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
PY
USER 10001
CMD ["python", "/proxy.py"]
