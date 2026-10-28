"""Generate random promtool unit tests for the four checkout alerts.

  python3 gen_tests.py --out DIR [--rules PATH] [--seed N]

Writes one test file per scenario to DIR, named <check>-<n>.yml, and
DIR/scenarios.json with the check and a description of each file. Each test
file holds a single scenario, so promtool's exit code says whether that
scenario passed.

Every value is random: instance addresses, traffic, error ratios, latencies
and the minute at which things go wrong. Scenarios keep a wide margin around
each threshold, so a correct rule always passes and the answers cannot be
looked up in a table.

The tests read the ALERTS series, which Prometheus writes for every pending
and firing alert, and count firing alerts by the labels that matter. Extra
labels on an alert are fine. The files are JSON, which promtool reads as YAML.
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
from pathlib import Path

JOB = "checkout"
CHECKOUT = "/api/checkout"
ROUTES = [CHECKOUT, "/api/cart", "/api/products"]
BUCKETS = ["0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "+Inf"]
# A typical value inside each bucket, for the _sum series.
BUCKET_MID = [0.03, 0.075, 0.17, 0.37, 0.7, 1.7, 3.6, 7.0]


class Gen:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used: set[str] = set()

    def instance(self) -> str:
        while True:
            parts = [10, self.rng.randrange(0, 256), self.rng.randrange(0, 256), self.rng.randrange(2, 255)]
            name = ".".join(map(str, parts)) + ":" + self.rng.choice(["8080", "9090", "8000"])
            if name not in self.used:
                self.used.add(name)
                return name

    def uniform(self, lo: float, hi: float) -> float:
        return self.rng.uniform(lo, hi)

    def randint(self, lo: int, hi: int) -> int:
        return self.rng.randint(lo, hi)


def labels(name: str, **kv: str) -> str:
    inner = ",".join(f'{k}="{v}"' for k, v in kv.items())
    return f"{name}{{{inner}}}"


def minutes(m: float) -> str:
    whole = int(m)
    secs = round((m - whole) * 60)
    return f"{whole}m{secs}s" if secs else f"{whole}m"


def values(points: list) -> str:
    out = []
    for p in points:
        if p is None:
            out.append("_")
        elif p == "stale":
            out.append("stale")
        elif isinstance(p, float):
            out.append(f"{p:.6g}")
        else:
            out.append(str(p))
    return " ".join(out)


def counter(per_minute: list[float], jitter: float, gen: Gen, reset_at: int | None = None) -> list[int]:
    """Cumulative counter values from per-minute increments, with a little noise.

    reset_at is the minute the process restarts: the counter starts again
    from 0, as a real one does.
    """
    total, out = 0.0, [0]
    for minute, inc in enumerate(per_minute):
        if minute == reset_at:
            total = 0.0
        if inc > 0 and jitter:
            inc *= 1 + gen.uniform(-jitter, jitter)
        total += max(inc, 0.0)
        out.append(round(total))
    return out


def firing(alert: str, by: list[str]) -> str:
    return f'count by ({", ".join(["alertname", *by])}) (ALERTS{{alertname="{alert}",alertstate="firing"}})'


def expect(labelsets: list[dict]) -> list[dict]:
    return [{"labels": "{" + ",".join(f'{k}="{v}"' for k, v in ls.items()) + "}", "value": 1} for ls in labelsets]


def test_file(rules: str, name: str, series: list[tuple[str, list]], checks: list[tuple[str, float, list[dict]]]) -> dict:
    return {
        "rule_files": [rules],
        "evaluation_interval": "1m",
        "tests": [
            {
                "name": name,
                "interval": "1m",
                "input_series": [{"series": s, "values": values(v)} for s, v in series],
                "promql_expr_test": [
                    {"expr": expr, "eval_time": minutes(at), "exp_samples": expect(exp)} for expr, at, exp in checks
                ],
            }
        ],
    }


# --- CheckoutInstanceDown ----------------------------------------------------


def instance_down(gen: Gen) -> list[tuple[str, dict]]:
    alert, by = "CheckoutInstanceDown", ["instance", "severity"]
    q = firing(alert, by)
    out = []

    # Two instances go down a little apart and stay down; two stay up.
    a, b, c, d = (gen.instance() for _ in range(4))
    down_a = gen.randint(5, 9)
    down_b = down_a + gen.randint(1, 3)
    length = down_b + 9
    series = []
    for inst, down in ((a, down_a), (b, down_b), (c, None), (d, None)):
        pts = [1 if down is None or i < down else 0 for i in range(length)]
        series.append((labels("up", job=JOB, instance=inst), pts))
    gen.rng.shuffle(series)
    exp_a = {"alertname": alert, "instance": a, "severity": "page"}
    exp_b = {"alertname": alert, "instance": b, "severity": "page"}
    out.append((
        "an instance that stays down alerts after 3 minutes, one alert per instance, with severity page",
        test_file("", "instances down", series, [
            (q, down_a - 1, []),
            (q, down_a + 3.5, [exp_a]),
            (q, down_b + 3.5, [exp_a, exp_b]),
        ]),
    ))

    # Short blips: three failed scrapes in a row (2 minutes) and a single one.
    a, b, c = (gen.instance() for _ in range(3))
    start = gen.randint(5, 9)
    length = start + 12
    series = [
        (labels("up", job=JOB, instance=a), [0 if start <= i <= start + 2 else 1 for i in range(length)]),
        (labels("up", job=JOB, instance=b), [0 if i == start + 6 else 1 for i in range(length)]),
        (labels("up", job=JOB, instance=c), [1] * length),
    ]
    out.append((
        "an instance that misses 2 minutes of scrapes and comes back does not alert",
        test_file("", "short blips", series, [(q, start + t, []) for t in (1, 2, 2.5, 3, 4, 7, 8)]),
    ))

    # An instance of another job is down for a long time.
    a, b, c = (gen.instance() for _ in range(3))
    start = gen.randint(3, 6)
    length = start + 14
    series = [
        (labels("up", job="payments", instance=a), [0 if i >= start else 1 for i in range(length)]),
        (labels("up", job=JOB, instance=b), [1] * length),
        (labels("up", job=JOB, instance=c), [1] * length),
    ]
    out.append((
        "an instance of another job that is down does not alert",
        test_file("", "other job down", series, [(q, start + t, []) for t in (4, 8, 12)]),
    ))
    return out


# --- CheckoutTargetsMissing --------------------------------------------------


def targets_missing(gen: Gen) -> list[tuple[str, dict]]:
    alert = "CheckoutTargetsMissing"
    q = firing(alert, ["severity"])
    exp = [{"alertname": alert, "severity": "page"}]
    out = []

    # Service discovery loses every checkout target: their series go stale.
    gone = gen.randint(5, 9)
    length = gone + 14
    series = []
    for _ in range(gen.randint(2, 4)):
        series.append((labels("up", job=JOB, instance=gen.instance()), [1] * gone + ["stale"]))
    series.append((labels("up", job="payments", instance=gen.instance()), [1] * length))
    out.append((
        "when every checkout target disappears, it alerts after 5 minutes, with severity page",
        test_file("", "targets gone", series, [
            (q, gone - 1, []),
            (q, gone + 4, []),
            (q, gone + 6, exp),
            (q, gone + 12, exp),
        ]),
    ))

    # Checkout was never scraped at all; another job was.
    length = 14
    series = [(labels("up", job="payments", instance=gen.instance()), [1] * length)]
    out.append((
        "when no checkout target was ever scraped, it alerts after 5 minutes",
        test_file("", "never scraped", series, [(q, 3, []), (q, 7, exp), (q, 12, exp)]),
    ))

    # Every checkout instance is down but still scraped: up is 0, not missing.
    down = gen.randint(3, 6)
    length = down + 16
    series = [
        (labels("up", job=JOB, instance=gen.instance()), [1 if i < down else 0 for i in range(length)])
        for _ in range(gen.randint(2, 3))
    ]
    out.append((
        "instances that are down but still scraped (up is 0) do not count as missing",
        test_file("", "down but scraped", series, [(q, down + t, []) for t in (4, 8, 14)]),
    ))

    # Another job's targets disappear; checkout stays.
    gone = gen.randint(4, 8)
    length = gone + 14
    series = [
        (labels("up", job="payments", instance=gen.instance()), [1] * gone + ["stale"]),
        (labels("up", job=JOB, instance=gen.instance()), [1] * length),
    ]
    out.append((
        "when another job's targets disappear, it does not alert",
        test_file("", "other job gone", series, [(q, gone + t, []) for t in (4, 8, 12)]),
    ))
    return out


# --- CheckoutHighErrorRate ---------------------------------------------------


# Status code mixes. Errors are not always 500s: a broken upstream gives
# 502, 503 and 504, and clients get 400, 404 and 429.
FIVE_XX = [{"500": 1.0}, {"500": 0.6, "503": 0.4}, {"502": 0.5, "503": 0.3, "504": 0.2}, {"503": 1.0},
           {"504": 0.6, "502": 0.4}]
# A broken upstream: no 500 at all, and no single code above 5% of requests,
# so only a rule that counts every 5xx code sees the outage.
UPSTREAM = {"502": 1 / 3, "503": 1 / 3, "504": 1 / 3}
FOUR_XX = [{"404": 1.0}, {"400": 0.3, "404": 0.5, "429": 0.2}, {"429": 1.0}, {"401": 0.4, "404": 0.6}]


ROUTES = [(CHECKOUT, 0.5), ("/api/cart", 0.3), ("/api/products", 0.2)]


def request_series(gen: Gen, job: str, instances: list[str], weights: list[float], length: int, phases,
                   codes5: dict[str, float] | None = None, codes4: dict[str, float] | None = None,
                   routes: list[tuple[str, float]] | None = None, restarts: dict[str, int] | None = None) -> list:
    """http_requests_total series for one job.

    phases(minute) -> (requests per second for the whole job, 5xx ratio, 4xx ratio, overrides)
    where overrides maps an instance or a route to its own 5xx ratio. codes5
    and codes4 split the 5xx and 4xx answers between status codes, and routes
    lists the routes with their share of the traffic.
    """
    codes5 = codes5 or gen.rng.choice(FIVE_XX)
    codes4 = codes4 or gen.rng.choice(FOUR_XX)
    series = []
    for inst, w in zip(instances, weights):
        for route, rw in routes or ROUTES:
            for code in ["200", *codes4, *codes5]:
                incs = []
                for minute in range(length - 1):
                    rps, p5, p4, over = phases(minute)
                    p5 = over.get(inst, over.get(route, p5))
                    n = rps * 60 * w * rw
                    share = 1 - p5 - p4 if code == "200" else p4 * codes4.get(code, 0) + p5 * codes5.get(code, 0)
                    incs.append(n * share)
                series.append((labels("http_requests_total", job=job, instance=inst, route=route, code=code),
                               counter(incs, 0.05, gen, (restarts or {}).get(inst))))
    return series


def healthy_job(gen: Gen, job: str, rps: float, length: int) -> list:
    """Another service on the same Prometheus, with a lot of traffic and almost no errors."""
    insts = [gen.instance() for _ in range(2)]
    return request_series(gen, job, insts, weights(gen, 2), length, lambda m: (rps, 0.002, 0.02, {}))


def codes_text(codes: dict[str, float]) -> str:
    names = sorted(codes)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def weights(gen: Gen, n: int) -> list[float]:
    raw = [gen.uniform(0.8, 1.2) for _ in range(n)]
    return [r / sum(raw) for r in raw]


def error_rate(gen: Gen) -> list[tuple[str, dict]]:
    alert = "CheckoutHighErrorRate"
    q = firing(alert, ["severity"])
    exp = [{"alertname": alert, "severity": "page"}]
    out = []

    # A bad deploy: 10 to 25% of requests fail from minute t0 on. A busy,
    # healthy service shares the Prometheus.
    insts = [gen.instance() for _ in range(3)]
    rps, base5, bad5, p4 = gen.uniform(8, 30), gen.uniform(0, 0.01), gen.uniform(0.10, 0.25), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    codes = gen.rng.choice(FIVE_XX)
    # One instance restarts during the outage; its counters start from 0.
    series = request_series(gen, JOB, insts, weights(gen, 3), length,
                            lambda m: (rps, bad5 if m >= t0 else base5, p4, {}), codes5=codes,
                            restarts={insts[0]: t0 + gen.randint(6, 9)})
    # 6 to 8 times the traffic: mixed in, even 25% of 5xx would drop under 5%.
    series += healthy_job(gen, "payments", rps * gen.uniform(6, 8), length)
    out.append((
        f"when {bad5:.0%} of checkout requests fail with {codes_text(codes)} at {rps:.0f} requests per second, "
        "it alerts after 5 minutes (not in the first 2), with severity page, even next to a busy healthy service",
        test_file("", "bad deploy", series, [(q, t0 - 1, []), (q, t0 + 2, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # The upstream behind /api/cart and /api/products breaks: their errors are
    # 502, 503 and 504, no 500, and /api/checkout itself is fine. The whole
    # service is still over 5%.
    insts = [gen.instance() for _ in range(3)]
    rps, bad5, p4 = gen.uniform(8, 30), gen.uniform(0.09, 0.12), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    route_bad = bad5 / 0.5  # cart and products carry half of the traffic
    series = request_series(gen, JOB, insts, weights(gen, 3), length,
                            lambda m: (rps, 0.0, p4, {"/api/cart": route_bad, "/api/products": route_bad} if m >= t0 else {}),
                            codes5=UPSTREAM)
    out.append((
        f"when {bad5:.0%} of all requests fail, all on /api/cart and /api/products and split between 502, 503 and "
        "504 with no 500, it alerts after 5 minutes",
        test_file("", "upstream down", series, [(q, t0 - 1, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # A sudden outage: half of all requests fail from minute t0 to t0 + 8.
    # The ratio over 5 minutes is above 5% from t0 + 1 to t0 + 11, so the
    # alert fires at t0 + 6 with a 5-minute "for", and resolves at t0 + 12.
    insts = [gen.instance() for _ in range(3)]
    rps, p4 = gen.uniform(8, 30), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    series = request_series(gen, JOB, insts, weights(gen, 3), length,
                            lambda m: (rps, 0.5 if t0 <= m < t0 + 8 else 0.0, p4, {}))
    out.append((
        "when half of all requests fail for 8 minutes, it alerts once that has lasted 5 minutes, not sooner or later, "
        "and stops about 5 minutes after the errors end",
        test_file("", "sudden outage", series,
                  [(q, t0 - 1, []), (q, t0 + 5.5, []), (q, t0 + 6.5, exp), (q, t0 + 10, exp), (q, t0 + 14.5, [])]),
    ))

    # A flaky dependency: 30% of requests fail one minute in three. Over any
    # 5 minutes that is 7.5 to 15%, always over 5%; a shorter window, or
    # irate(), keeps dropping to 0 and the alert never holds for 5 minutes.
    insts = [gen.instance() for _ in range(3)]
    rps, p4 = gen.uniform(8, 30), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    series = request_series(gen, JOB, insts, weights(gen, 3), length,
                            lambda m: (rps, 0.3 if m >= t0 and (m - t0) % 3 == 0 else 0.0, p4, {}))
    out.append((
        "when a flaky dependency makes 30% of requests fail one minute in three, that is over 5% for every 5-minute "
        "window, and it alerts",
        test_file("", "flaky dependency", series, [(q, t0 - 1, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # A steady 1 to 3% of 5xx, while another job fails a lot.
    insts = [gen.instance() for _ in range(3)]
    rps, p5, p4 = gen.uniform(8, 30), gen.uniform(0.01, 0.03), gen.uniform(0.02, 0.08)
    length = 26
    series = request_series(gen, JOB, insts, weights(gen, 3), length, lambda m: (rps, p5, p4, {}))
    other, other_rps = [gen.instance() for _ in range(2)], rps * gen.uniform(0.8, 1.2)
    series += request_series(gen, "payments", other, weights(gen, 2), length, lambda m: (other_rps, 0.3, 0.02, {}))
    out.append((
        f"a steady {p5:.1%} of 5xx does not alert, even when another job fails a lot",
        test_file("", "low 5xx", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # Lots of 4xx (bad requests from clients), few 5xx.
    insts = [gen.instance() for _ in range(3)]
    rps, p5, p4 = gen.uniform(8, 30), gen.uniform(0, 0.01), gen.uniform(0.2, 0.4)
    series = request_series(gen, JOB, insts, weights(gen, 3), length, lambda m: (rps, p5, p4, {}))
    out.append((
        f"{p4:.0%} of 4xx answers do not alert: only 5xx count as errors",
        test_file("", "4xx", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # Just over and just under the 5% line, steady for the whole scenario.
    insts = [gen.instance() for _ in range(3)]
    rps, p5, p4 = gen.uniform(10, 30), gen.uniform(0.058, 0.063), gen.uniform(0.02, 0.05)
    t0 = gen.randint(7, 10)
    series = request_series(gen, JOB, insts, weights(gen, 3), t0 + 17,
                            lambda m: (rps, p5 if m >= t0 else 0.0, p4, {}))
    out.append((
        f"a steady {p5:.1%} of 5xx, just over 5%, alerts",
        test_file("", "just over", series, [(q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))
    insts = [gen.instance() for _ in range(3)]
    rps, p5, p4 = gen.uniform(20, 40), gen.uniform(0.043, 0.046), gen.uniform(0.02, 0.05)
    series = request_series(gen, JOB, insts, weights(gen, 3), length, lambda m: (rps, p5, p4, {}))
    out.append((
        f"a steady {p5:.1%} of 5xx, under 5%, does not alert",
        test_file("", "just under", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # Just over 1 request per second: quiet, but busy enough to page.
    insts = [gen.instance() for _ in range(2)]
    rps, p5 = gen.uniform(1.3, 1.6), gen.uniform(0.2, 0.3)
    t0 = gen.randint(7, 10)
    series = request_series(gen, JOB, insts, weights(gen, 2), t0 + 17, lambda m: (rps, p5 if m >= t0 else 0.0, 0.02, {}))
    out.append((
        f"at {rps:.1f} requests per second, just over 1, it alerts when {p5:.0%} fail",
        test_file("", "quiet but busy enough", series, [(q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # The middle of the night: under 1 request per second, half of them fail.
    insts = [gen.instance() for _ in range(2)]
    rps, p5 = gen.uniform(0.6, 0.85), gen.uniform(0.4, 0.6)
    series = request_series(gen, JOB, insts, weights(gen, 2), length, lambda m: (rps, p5, 0.02, {}))
    out.append((
        f"at {rps:.2f} requests per second, under 1, it does not alert even though {p5:.0%} fail",
        test_file("", "low traffic", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # One small route fails a lot; the service as a whole stays under 5%.
    insts = [gen.instance() for _ in range(3)]
    rps, bad = gen.uniform(10, 30), gen.uniform(0.4, 0.6)
    routes = [(CHECKOUT, 0.5), ("/api/cart", 0.28), ("/api/products", 0.2), ("/api/export", 0.02)]
    series = request_series(gen, JOB, insts, weights(gen, 3), length,
                            lambda m: (rps, 0.002, 0.03, {"/api/export": bad}), routes=routes)
    out.append((
        f"one route with 2% of the traffic failing {bad:.0%} of requests does not alert: the whole service stays "
        "under 5%",
        test_file("", "one bad route", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # One small instance fails a lot; the service as a whole stays under 5%.
    insts = [gen.instance() for _ in range(4)]
    w = [0.05] + [0.95 / 3] * 3
    rps, bad, p5 = gen.uniform(25, 40), gen.uniform(0.35, 0.5), gen.uniform(0, 0.005)
    series = request_series(gen, JOB, insts, w, length, lambda m: (rps, p5, 0.03, {insts[0]: bad}))
    out.append((
        f"one instance with 5% of the traffic failing {bad:.0%} of requests does not alert: "
        "the whole service stays under 5%",
        test_file("", "one bad instance", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))
    return out


# --- CheckoutSlowRequests ----------------------------------------------------

FAST = [0.70, 0.25, 0.04, 0.01, 0, 0, 0, 0]
# Most requests are fast, 12% take 1 to 2.5 s: p95 is well over 500 ms, but
# the average stays under 500 ms.
TAIL = [0.80, 0.08, 0, 0, 0, 0.12, 0, 0]
SLOW = [0, 0, 0, 0, 0, 1.0, 0, 0]


def histogram_series(gen: Gen, inst: str, route: str, length: int, rpm, dist, job: str = JOB,
                     reset_at: int | None = None) -> list:
    """Bucket, _sum and _count series for one instance and route.

    rpm(minute) is requests per minute, dist(minute) the share per bucket.
    """
    per_bucket = [[] for _ in BUCKETS]
    sums = []
    for minute in range(length - 1):
        n = rpm(minute)
        shares = dist(minute)
        for i, share in enumerate(shares):
            per_bucket[i].append(n * share)
        sums.append(n * sum(s * mid for s, mid in zip(shares, BUCKET_MID)))
    noisy = [[inc * (1 + gen.uniform(-0.03, 0.03)) for inc in incs] for incs in per_bucket]
    cumulative = [[0.0] * (length - 1) for _ in BUCKETS]
    for i in range(len(BUCKETS)):
        for m in range(length - 1):
            cumulative[i][m] = sum(noisy[j][m] for j in range(i + 1))
    out = []
    for le, incs in zip(BUCKETS, cumulative):
        out.append((labels("http_request_duration_seconds_bucket", job=job, instance=inst, route=route, le=le),
                    counter(incs, 0, gen, reset_at)))
    count = counter(cumulative[-1], 0, gen, reset_at)
    total, sum_pts = 0.0, [0.0]
    for minute, s in enumerate(sums):
        if minute == reset_at:
            total = 0.0
        total += s
        sum_pts.append(round(total, 3))
    out.append((labels("http_request_duration_seconds_count", job=job, instance=inst, route=route), count))
    out.append((labels("http_request_duration_seconds_sum", job=job, instance=inst, route=route), sum_pts))
    return out


def latency_series(gen: Gen, length: int, rps: float, dist_for, inst_weights=None, job: str = JOB,
                   restart: tuple[int, int] | None = None) -> list:
    """Histograms for /api/checkout and /api/cart on each instance of a job.

    dist_for(minute, instance_index, route) gives the bucket shares, and
    restart is (instance_index, minute) for an instance whose counters start
    again from 0.
    """
    w = inst_weights or weights(gen, 3)
    series = []
    for idx, iw in enumerate(w):
        inst = gen.instance()
        for route, rw in ((CHECKOUT, 0.6), ("/api/cart", 0.4)):
            series += histogram_series(gen, inst, route, length, lambda m, iw=iw, rw=rw: rps * 60 * iw * rw,
                                       lambda m, idx=idx, route=route: dist_for(m, idx, route), job=job,
                                       reset_at=restart[1] if restart and restart[0] == idx else None)
    return series


def slow_requests(gen: Gen) -> list[tuple[str, dict]]:
    alert = "CheckoutSlowRequests"
    q = firing(alert, ["severity"])
    exp = [{"alertname": alert, "severity": "ticket"}]
    out = []

    # Another service has its own fast /api/checkout route with much more
    # traffic: mixed in, it would hide the slowdown.
    rps = gen.uniform(5, 20)
    t0 = gen.randint(7, 10)
    length = t0 + 21
    series = latency_series(gen, length, rps,
                            lambda m, idx, route: TAIL if route == CHECKOUT and m >= t0 else FAST,
                            restart=(0, t0 + gen.randint(13, 15)))
    series += latency_series(gen, length, rps * gen.uniform(4, 6), lambda m, idx, route: FAST, job="payments")
    out.append((
        "when 12% of checkout's /api/checkout requests take over a second (p95 well over 500 ms, average under "
        "it), it alerts after 10 minutes, with severity ticket, even next to a busy fast service",
        # 12% slow requests: one slow interval in the 5-minute window is 3%,
        # two are 6%, so p95 is high from t0 + 2 and a 10-minute "for" fires
        # at t0 + 12.
        test_file("", "slow checkout", series, [(q, t0 - 1, []), (q, t0 + 5, []), (q, t0 + 11.5, []),
                                                (q, t0 + 12.5, exp), (q, t0 + 16, exp), (q, t0 + 19, exp)]),
    ))

    # Another service's /api/checkout is slow; checkout's is fast.
    length = 26
    series = latency_series(gen, length, gen.uniform(5, 20), lambda m, idx, route: FAST)
    series += latency_series(gen, length, gen.uniform(5, 20), lambda m, idx, route: SLOW if route == CHECKOUT else FAST,
                             job="payments")
    out.append((
        "a slow /api/checkout route of another job does not alert",
        test_file("", "other job slow", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # p95 between 500 ms and 1 s: 10% of requests take 0.5 to 1 s.
    t0 = gen.randint(7, 10)
    mid = [0.85, 0.05, 0, 0, 0.10, 0, 0, 0]
    series = latency_series(gen, t0 + 21, gen.uniform(5, 20),
                            lambda m, idx, route: mid if route == CHECKOUT and m >= t0 else FAST)
    out.append((
        "a p95 of about 750 ms, over 500 ms but under a second, alerts",
        test_file("", "p95 750 ms", series, [(q, t0 + 16, exp), (q, t0 + 19, exp)]),
    ))
    # p95 between 250 and 500 ms: 10% of requests take 0.25 to 0.5 s.
    near = [0.85, 0.05, 0, 0.10, 0, 0, 0, 0]
    series = latency_series(gen, length, gen.uniform(5, 20),
                            lambda m, idx, route: near if route == CHECKOUT else FAST)
    out.append((
        "a p95 of about 375 ms, under 500 ms, does not alert",
        test_file("", "p95 375 ms", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    series = latency_series(gen, length, gen.uniform(5, 20), lambda m, idx, route: FAST)
    out.append((
        "fast requests (p95 around 100 ms) do not alert",
        test_file("", "fast", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    series = latency_series(gen, length, gen.uniform(5, 20),
                               lambda m, idx, route: SLOW if route != CHECKOUT else FAST)
    out.append((
        "a slow route other than /api/checkout does not alert",
        test_file("", "other route slow", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # The first instance gets 3% of the traffic, and all of it is slow.
    series = latency_series(gen, length, gen.uniform(20, 40), lambda m, idx, route: SLOW if idx == 0 else FAST,
                            [0.03, 0.97 / 3, 0.97 / 3, 0.97 / 3])
    out.append((
        "one instance with 3% of the traffic that is slow does not alert: p95 is for the whole service",
        test_file("", "one slow instance", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # 15% slow requests for 7 minutes. With one sample a minute, a 5-minute
    # rate window holds 4 intervals: one slow interval is 3.75% (p95 stays
    # fast), two are 7.5%. So p95 is high for about 7 minutes: a rule with a
    # 5-minute "for" fires, one with 10 minutes does not.
    t0 = gen.randint(7, 10)
    length = t0 + 20
    short = [0.80, 0.05, 0, 0, 0, 0.15, 0, 0]
    series = latency_series(gen, length, gen.uniform(5, 20),
                            lambda m, idx, route: short if route == CHECKOUT and t0 <= m < t0 + 7 else FAST)
    out.append((
        "a 7-minute slowdown does not alert: it has to last 10 minutes",
        test_file("", "short slowdown", series, [(q, t0 + t, []) for t in (5, 7, 8, 9, 10, 11, 12, 14, 18)]),
    ))
    return out


CHECKS = {
    "instance-down": instance_down,
    "targets-missing": targets_missing,
    "error-rate": error_rate,
    "slow-requests": slow_requests,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--rules", default="../alerts.yml", help="path to alerts.yml as promtool sees it")
    parser.add_argument("--seed", type=int, help="for repeatable local runs; grading uses a random seed")
    args = parser.parse_args()
    rng = random.Random(args.seed if args.seed is not None else secrets.randbits(64))
    gen = Gen(rng)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scenarios = []
    for check, make in CHECKS.items():
        for n, (description, doc) in enumerate(make(gen), start=1):
            doc["rule_files"] = [args.rules]
            name = f"{check}-{n}.yml"
            (out / name).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            scenarios.append({"file": name, "check": check, "description": description})
    (out / "scenarios.json").write_text(json.dumps(scenarios, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
