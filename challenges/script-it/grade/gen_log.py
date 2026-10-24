"""Generate a random access log and the report summarize.sh should print for it.

The expected report comes from the counts the generator chose while writing
the log, not from parsing it.

  python3 gen_log.py --seed 42 --lines 2000 --log test.log --expected test.txt [--top 5]
"""

from __future__ import annotations

import argparse
import random

METHODS = ["GET", "GET", "GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"]
STATUSES = [200, 200, 200, 201, 204, 206, 301, 302, 304, 400, 401, 403, 404, 404, 429, 500, 502, 503, 504]
WORDS = ["api", "v1", "v2", "orders", "users", "cart", "products", "search", "static", "img",
         "login", "logout", "health", "docs", "blog", "posts", "admin", "reports", "items", "tags"]
AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko)",
    "curl/8.5.0",
    "python-requests/2.32.3",
    "Go-http-client/1.1",
    "kube-probe/1.37",
]
USERS = ["-", "-", "-", "-", "alice", "bob", "deploy"]


def random_ip(rng: random.Random) -> str:
    if rng.random() < 0.1:
        return "2001:db8::" + format(rng.randrange(1, 0xFFFF), "x")
    return ".".join(str(rng.randrange(1, 255)) for _ in range(4))


def random_path(rng: random.Random) -> str:
    depth = rng.randrange(0, 4)
    if depth == 0:
        return "/"
    return "/" + "/".join(rng.choice(WORDS) for _ in range(depth))


def timestamp(rng: random.Random) -> str:
    day = rng.randrange(1, 29)
    h, m, s = rng.randrange(24), rng.randrange(60), rng.randrange(60)
    return f"{day:02d}/Oct/2026:{h:02d}:{m:02d}:{s:02d} +0000"


def malformed(rng: random.Random) -> str:
    ip = random_ip(rng)
    ts = timestamp(rng)
    choices = [
        f'{ip} - - [{ts}] GET /no-quotes HTTP/1.1 200 512',
        f'{ip} - - [{ts}] "GET /short-status HTTP/1.1" 20 512',
        f'{ip} - - [{ts}] "GET /word-status HTTP/1.1" OK 512',
        f'{ip} - - [{ts}] "GET /too-high HTTP/1.1" 600 512',
        f'{ip} - - [{ts}] "GET /too-low HTTP/1.1" 199 512',
        f'{ip} - - [{ts}] "GET /cut-off',
        f'{ip} - - [{ts}] "-" 400 0 "-" "-"',
        "connection reset by peer",
        f"[{ts}] worker 3 restarted",
    ]
    return rng.choice(choices)


def report(ip_counts, path_counts, status_counts, skipped, top):
    def ranked(counts):
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].encode()))
        return [f"{count} {key}" for key, count in items[:top]]

    lines = [
        f"requests: {sum(ip_counts.values())}",
        f"unique_ips: {len(ip_counts)}",
    ]
    for cls in ("2xx", "3xx", "4xx", "5xx"):
        lines.append(f"status_{cls}: {status_counts.get(cls, 0)}")
    lines.append(f"skipped: {skipped}")
    lines.append("top_ips:")
    lines.extend(ranked(ip_counts))
    lines.append("top_paths:")
    lines.extend(ranked(path_counts))
    return "\n".join(lines) + "\n"


def generate(seed: int, n_lines: int, top: int):
    rng = random.Random(seed)
    ips = sorted({random_ip(rng) for _ in range(max(3, n_lines // 40))})
    paths = sorted({random_path(rng) for _ in range(max(3, n_lines // 25))})
    # A few busy clients and pages, a long tail of quiet ones.
    ip_weights = [1.0 / (i + 1) for i in range(len(ips))]
    path_weights = [1.0 / (i + 1) ** 0.8 for i in range(len(paths))]
    rng.shuffle(ip_weights)
    rng.shuffle(path_weights)

    ip_counts: dict[str, int] = {}
    path_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    lines: list[str] = []
    skipped = 0

    for _ in range(n_lines):
        roll = rng.random()
        if roll < 0.03:
            lines.append(malformed(rng))
            skipped += 1
            continue
        if roll < 0.04:
            lines.append("")
            continue
        ip = rng.choices(ips, ip_weights)[0]
        path = rng.choices(paths, path_weights)[0]
        query = ""
        if rng.random() < 0.3:
            query = f"?page={rng.randrange(1, 50)}&sort={rng.choice(['asc', 'desc'])}"
        status = rng.choice(STATUSES)
        size = rng.choice(["-", str(rng.randrange(0, 200000))])
        referrer = rng.choice(["-", "https://devops-daily.com/", f"https://example.com{random_path(rng)}"])
        line = (
            f'{ip} - {rng.choice(USERS)} [{timestamp(rng)}] '
            f'"{rng.choice(METHODS)} {path}{query} HTTP/1.1" {status} {size} '
            f'"{referrer}" "{rng.choice(AGENTS)}"'
        )
        lines.append(line)
        ip_counts[ip] = ip_counts.get(ip, 0) + 1
        path_counts[path] = path_counts.get(path, 0) + 1
        cls = f"{status // 100}xx"
        status_counts[cls] = status_counts.get(cls, 0) + 1

    return "\n".join(lines) + "\n", report(ip_counts, path_counts, status_counts, skipped, top)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--lines", type=int, default=2000)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--log", required=True)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()
    log, expected = generate(args.seed, args.lines, args.top)
    with open(args.log, "w", encoding="utf-8") as fh:
        fh.write(log)
    with open(args.expected, "w", encoding="utf-8") as fh:
        fh.write(expected)


if __name__ == "__main__":
    main()
