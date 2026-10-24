"""Check that the running container serves the real slug app.

  check_app.py PORT

Runs in a venv with the same python-slugify as the app, so the expected
slugs come from the library itself. The texts are random letters from Latin,
Greek and Cyrillic alphabets, so the answers cannot be looked up in a table.
Prints one line for the PR comment and exits 0 when everything matches.
"""

from __future__ import annotations

import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

from slugify import slugify

RANGES = [(0x61, 0x7A), (0x41, 0x5A), (0xC0, 0xFF), (0x100, 0x17F), (0x391, 0x3A9), (0x3B1, 0x3C9), (0x410, 0x44F)]
SKIP = {0xD7, 0xF7}
POOL = [chr(c) for lo, hi in RANGES for c in range(lo, hi + 1) if c not in SKIP and chr(c).isalpha()]
SEPARATORS = [" ", ", ", " - ", "! ", " / ", "   ", ": "]


def random_text() -> str:
    words = []
    for _ in range(5):
        words.append("".join(secrets.choice(POOL) for _ in range(3 + secrets.randbelow(6))))
        words.append(secrets.choice(SEPARATORS))
    words.append(str(1000 + secrets.randbelow(9000)))
    return "".join(words)


def request(url: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read(65536)
    except (urllib.error.URLError, OSError):
        return 0, {}, b""


def main() -> int:
    base = f"http://127.0.0.1:{int(sys.argv[1])}"
    wrong = 0
    rounds = 5
    for _ in range(rounds):
        text = random_text()
        status, _, body = request(f"{base}/slugify?{urllib.parse.urlencode({'text': text})}")
        try:
            got = json.loads(body).get("slug") if status == 200 else None
        except ValueError:
            got = None
        if got != slugify(text):
            wrong += 1
    if wrong:
        print(f"GET /slugify returned the wrong slug for {wrong} of {rounds} random texts.")
        return 1

    status, _, _ = request(f"{base}/slugify")
    if status != 400:
        print(f"GET /slugify without text answered {status}, not 400.")
        return 1
    status, headers, _ = request(f"{base}/health", method="POST")
    allow = {h.strip() for h in headers.get("Allow", "").split(",")}
    if status != 405 or "GET" not in allow:
        print("POST /health did not answer 405 with an Allow header, as the Flask app does.")
        return 1
    _, headers, _ = request(f"{base}/health")
    if not headers.get("Server", "").lower().startswith("gunicorn"):
        print("The app is not served by gunicorn, as in the starter's CMD.")
        return 1
    print(f"GET /slugify returned the right slugs for {rounds} random texts; errors and server match the Flask app under gunicorn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
