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

Every scenario also looks like production to a rule: it starts at a random
date and time, it has HISTORY minutes of data before anything happens, and
checkout and another job both have every metric from the brief, made from
one model of their traffic (see service()), with counters that have run for
a while and instance addresses in the forms service discovery gives. promcheck stops a rule from
reading the clock or looking back further than the history, so a rule
cannot tell a test from production and act differently there.

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
BUCKETS = ["0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "+Inf"]
# A typical value inside each bucket, for the _sum series.
BUCKET_MID = [0.03, 0.075, 0.17, 0.37, 0.7, 1.7, 3.6, 7.0]
# Minutes of data before each scenario, in the state of its first minute.
# promcheck lets a rule look back at most an hour.
HISTORY = 70
# Scenarios start at a random time between these (2026-01-01 and 2030-01-01, UTC).
EARLIEST, LATEST = 1767225600, 1893456000


class Gen:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used: set[str] = set()

    def instance(self) -> str:
        """A target address, in one of the forms service discovery gives."""
        r = self.rng
        while True:
            a, b, c = r.randrange(0, 256), r.randrange(0, 256), r.randrange(2, 255)
            host = r.choice([
                f"10.{a}.{b}.{c}",
                f"172.{r.randrange(16, 32)}.{b}.{c}",
                f"192.168.{b}.{c}",
                f"ip-10-{a}-{b}-{c}.{r.choice(['eu-west-1', 'us-east-2', 'ap-south-1'])}.compute.internal",
                f"{r.choice(['web', 'app', 'api'])}-{r.randrange(16 ** 9):09x}-{''.join(r.choices('bcdfghjklmnpqrstvwxz2456789', k=5))}",
            ])
            name = f"{host}:{r.choice(['8080', '9090', '8000', '3000'])}"
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
            out.append(f"{p:.3f}")
        else:
            out.append(str(p))
    return " ".join(out)


def firing(alert: str, by: list[str]) -> str:
    return f'count by ({", ".join(["alertname", *by])}) (ALERTS{{alertname="{alert}",alertstate="firing"}})'


def expect(labelsets: list[dict]) -> list[dict]:
    return [{"labels": "{" + ",".join(f'{k}="{v}"' for k, v in ls.items()) + "}", "value": 1} for ls in labelsets]


def test_file(rules: str, name: str, series: list[tuple[str, list]], checks: list[tuple[str, float, list[dict]]]) -> dict:
    """One promtool test. Check times are scenario minutes, after the history."""
    return {
        "rule_files": [rules],
        "evaluation_interval": "1m",
        "tests": [
            {
                "name": name,
                "interval": "1m",
                "input_series": [{"series": s, "values": values(v)} for s, v in series],
                "promql_expr_test": [
                    {"expr": expr, "eval_time": minutes(HISTORY + at), "exp_samples": expect(exp)}
                    for expr, at, exp in checks
                ],
            }
        ],
    }


# --- one model for every series of a job ---------------------------------------

# Status code mixes. Errors are not always 500s: a broken upstream gives
# 502, 503 and 504, and clients get 400, 404 and 429.
FIVE_XX = [{"500": 1.0}, {"500": 0.6, "503": 0.4}, {"502": 0.5, "503": 0.3, "504": 0.2}, {"503": 1.0},
           {"504": 0.6, "502": 0.4}]
# A broken upstream: no 500 at all, and no single code above 5% of requests,
# so only a rule that counts every 5xx code sees the outage.
UPSTREAM = {"502": 1 / 3, "503": 1 / 3, "504": 1 / 3}
FOUR_XX = [{"404": 1.0}, {"400": 0.3, "404": 0.5, "429": 0.2}, {"429": 1.0}, {"401": 0.4, "404": 0.6}]

ROUTES = [(CHECKOUT, 0.5), ("/api/cart", 0.3), ("/api/products", 0.2)]

# Shares of requests per histogram bucket.
FAST = [0.70, 0.25, 0.04, 0.01, 0, 0, 0, 0]
# Most requests are fast, 12% take 1 to 2.5 s: p95 is well over 500 ms, but
# the average stays under 500 ms.
TAIL = [0.80, 0.08, 0, 0, 0, 0.12, 0, 0]
SLOW = [0, 0, 0, 0, 0, 1.0, 0, 0]


def gaps(points: list, keep: list[bool]) -> list:
    """The points where keep is true. Elsewhere a stale marker after the last sample, then nothing."""
    out: list = []
    for point, kept in zip(points, keep):
        if kept:
            out.append(point)
        elif out and out[-1] is not None and out[-1] != "stale":
            out.append("stale")
        else:
            out.append(None)
    return out


def histogram(gen: Gen, job: str, inst: str, route: str, count: list[int], reset: int | None, shares_at,
              keep: list[bool]) -> list:
    """Bucket, _sum and _count series for one instance and route.

    count is the cumulative number of requests at each point, the same as
    the sum of http_requests_total, and shares_at(point) the share of each
    bucket among the requests that came in just before that point.
    """
    # The counters start where the process's lifetime so far left them.
    start = []
    running, shares = 0.0, shares_at(1)
    for share in shares:
        running += count[0] * share / (sum(shares) or 1.0)
        start.append(min(round(running), count[0]))
    start[-1] = count[0]
    for i in range(1, len(start)):
        start[i] = max(start[i], start[i - 1])
    buckets = [[n] for n in start]
    sums = [round(sum(n * mid for n, mid in zip([start[0]] + [start[i] - start[i - 1] for i in range(1, len(start))],
                                                   BUCKET_MID)), 3)]
    for k in range(1, len(count)):
        inc = count[k] - (0 if k == reset else count[k - 1])
        shares = [s * (1 + gen.uniform(-0.03, 0.03)) for s in shares_at(k)]
        total = sum(shares) or 1.0
        running, cum = 0.0, []
        for share in shares:
            running += inc * share / total
            cum.append(min(round(running), inc))
        cum[-1] = inc
        for i in range(1, len(cum)):
            cum[i] = max(cum[i], cum[i - 1])
        for i, bucket in enumerate(buckets):
            bucket.append((0 if k == reset else bucket[-1]) + cum[i])
        in_bucket = [cum[0]] + [cum[i] - cum[i - 1] for i in range(1, len(cum))]
        added = sum(n * mid for n, mid in zip(in_bucket, BUCKET_MID))
        sums.append(round((0.0 if k == reset else sums[-1]) + added, 3))
    out = [(labels("http_request_duration_seconds_bucket", job=job, instance=inst, route=route, le=le), gaps(b, keep))
           for le, b in zip(BUCKETS, buckets)]
    out.append((labels("http_request_duration_seconds_count", job=job, instance=inst, route=route), gaps(count, keep)))
    out.append((labels("http_request_duration_seconds_sum", job=job, instance=inst, route=route), gaps(sums, keep)))
    return out


def service(gen: Gen, job: str, insts: list[str], w: list[float], length: int, phases=None, dist=None,
            codes5: dict[str, float] | None = None, codes4: dict[str, float] | None = None,
            routes: list[tuple[str, float]] | None = None, restarts: dict[int, int] | None = None,
            scrape=None) -> list:
    """Every series Prometheus has for one job, from one model of its traffic.

    up for every instance and, per instance and route, http_requests_total
    by status code and the request duration histogram, whose _count is the
    same number of requests. Minutes are scenario minutes: the series start
    HISTORY minutes earlier, in the state of minute 0.

    phases(minute) -> (requests per second for the whole job, 5xx ratio, 4xx ratio, overrides)
        where overrides maps an instance or a route to its own 5xx ratio.
    dist(minute, instance_index, route) -> the share of requests per histogram bucket.
    scrape(minute, instance_index) -> 1 when the scrape works, 0 when it fails, None when
        the instance is not a target. Only a working scrape stores the other series;
        otherwise they go stale, as in Prometheus. Requests still count up meanwhile.
    restarts maps an instance index to the minute its counters start again from 0.
    codes5 and codes4 split the 5xx and 4xx answers between status codes, and routes
    lists the routes with their share of the traffic.
    """
    base5, base4 = gen.uniform(0, 0.003), gen.uniform(0.01, 0.04)
    phases = phases or (lambda m: (10.0, base5, base4, {}))
    dist = dist or (lambda m, idx, route: FAST)
    scrape = scrape or (lambda m, idx: 1)
    codes5 = codes5 or gen.rng.choice(FIVE_XX)
    codes4 = codes4 or gen.rng.choice(FOUR_XX)
    points = HISTORY + length

    def minute(k: int) -> int:
        return max(k - HISTORY, 0)

    series = []
    for idx, (inst, iw) in enumerate(zip(insts, w)):
        # The process has run for half an hour to 30 days before the history,
        # so its counters do not start at 0.
        uptime = gen.uniform(30, 43200)
        state = [scrape(minute(k), idx) for k in range(points)]
        series.append((labels("up", job=job, instance=inst), gaps(state, [s is not None for s in state])))
        keep = [s == 1 for s in state]
        reset = HISTORY + restarts[idx] if restarts and idx in restarts else None
        for route, rw in routes or ROUTES:
            codes = ["200", *codes4, *codes5]
            rps0, p50, p40, over0 = phases(0)
            p50 = over0.get(inst, over0.get(route, p50))
            totals = {code: rps0 * 60 * iw * rw * uptime * (1 - p50 - p40 if code == "200" else
                                                            p40 * codes4.get(code, 0) + p50 * codes5.get(code, 0))
                      for code in codes}
            counts: dict[str, list[int]] = {code: [round(totals[code])] for code in codes}
            for k in range(1, points):
                rps, p5, p4, over = phases(minute(k - 1))
                p5 = over.get(inst, over.get(route, p5))
                n = rps * 60 * iw * rw
                for code in codes:
                    if k == reset:
                        totals[code] = 0.0
                    share = 1 - p5 - p4 if code == "200" else p4 * codes4.get(code, 0) + p5 * codes5.get(code, 0)
                    inc = n * share
                    if inc > 0:
                        inc *= 1 + gen.uniform(-0.05, 0.05)
                    totals[code] += max(inc, 0.0)
                    counts[code].append(round(totals[code]))
            for code in codes:
                series.append((labels("http_requests_total", job=job, instance=inst, route=route, code=code),
                               gaps(counts[code], keep)))
            count = [sum(counts[code][k] for code in codes) for k in range(points)]
            series += histogram(gen, job, inst, route, count, reset,
                                lambda k, idx=idx, route=route: dist(minute(k - 1), idx, route), keep)
    return series


def weights(gen: Gen, n: int) -> list[float]:
    raw = [gen.uniform(0.8, 1.2) for _ in range(n)]
    return [r / sum(raw) for r in raw]


def healthy(gen: Gen, job: str, length: int, n: int = 2, rps: float | None = None, scrape=None) -> list:
    """A job with steady traffic, almost no errors and fast requests."""
    rps = rps if rps is not None else gen.uniform(5, 30)
    p5, p4 = gen.uniform(0, 0.003), gen.uniform(0.01, 0.04)
    return service(gen, job, [gen.instance() for _ in range(n)], weights(gen, n), length,
                   lambda m: (rps, p5, p4, {}), scrape=scrape)


def codes_text(codes: dict[str, float]) -> str:
    names = sorted(codes)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# --- CheckoutInstanceDown ----------------------------------------------------


def instance_down(gen: Gen) -> list[tuple[str, dict]]:
    alert, by = "CheckoutInstanceDown", ["instance", "severity"]
    q = firing(alert, by)
    out = []

    # Two instances go down a little apart and stay down; two stay up.
    insts = [gen.instance() for _ in range(4)]
    a, b = insts[0], insts[1]
    down_a = gen.randint(5, 9)
    down_b = down_a + gen.randint(1, 3)
    length = down_b + 9
    # a stays down; b comes back after 6 minutes, and its alert must stop.
    windows = {0: (down_a, length), 1: (down_b, down_b + 6)}
    series = service(gen, JOB, insts, weights(gen, 4), length,
                     scrape=lambda m, idx: 0 if idx in windows and windows[idx][0] <= m < windows[idx][1] else 1)
    series += healthy(gen, "payments", length)
    gen.rng.shuffle(series)
    exp_a = {"alertname": alert, "instance": a, "severity": "page"}
    exp_b = {"alertname": alert, "instance": b, "severity": "page"}
    out.append((
        "an instance that is down alerts after 3 minutes, one alert per instance, with severity page, and the "
        "alert stops when it is back",
        test_file("", "instances down", series, [
            (q, down_a - 1, []),
            (q, down_a + 3.5, [exp_a]),
            (q, down_b + 3.5, [exp_a, exp_b]),
            (q, down_b + 6.5, [exp_a]),
        ]),
    ))

    # Short blips: three failed scrapes in a row (2 minutes) and a single one.
    start = gen.randint(5, 9)
    length = start + 12
    blips = {0: lambda m: start <= m <= start + 2, 1: lambda m: m == start + 6}
    series = service(gen, JOB, [gen.instance() for _ in range(3)], weights(gen, 3), length,
                     scrape=lambda m, idx: 0 if idx in blips and blips[idx](m) else 1)
    series += healthy(gen, "payments", length)
    out.append((
        "an instance that misses 2 minutes of scrapes and comes back does not alert",
        test_file("", "short blips", series, [(q, start + t, []) for t in (1, 2, 2.5, 3, 4, 7, 8)]),
    ))

    # An instance of another job is down for a long time.
    start = gen.randint(3, 6)
    length = start + 14
    series = healthy(gen, "payments", length, n=3, scrape=lambda m, idx: 0 if idx == 0 and m >= start else 1)
    series += healthy(gen, JOB, length)
    out.append((
        "an instance of another job that is down does not alert",
        test_file("", "other job down", series, [(q, start + t, []) for t in (4, 8, 12)]),
    ))

    # One instance goes down during a bad deploy: the others fail a lot and
    # are slow. The instance still gets its alert.
    insts = [gen.instance() for _ in range(4)]
    t0 = gen.randint(5, 9)
    length = t0 + 12
    rps, bad5 = gen.uniform(8, 30), gen.uniform(0.15, 0.25)
    series = service(gen, JOB, insts, weights(gen, 4), length,
                     lambda m: (rps, bad5 if m >= t0 - 2 else 0.001, 0.03, {}),
                     dist=lambda m, idx, route: TAIL if route == CHECKOUT and m >= t0 - 2 else FAST,
                     scrape=lambda m, idx: 0 if idx == 3 and m >= t0 else 1)
    series += healthy(gen, "payments", length)
    exp = [{"alertname": alert, "instance": insts[3], "severity": "page"}]
    out.append((
        "an instance that goes down while the others fail and are slow still alerts",
        test_file("", "down during an outage", series, [(q, t0 - 1, []), (q, t0 + 3.5, exp), (q, t0 + 8, exp)]),
    ))
    return out


# --- CheckoutTargetsMissing --------------------------------------------------


def targets_missing(gen: Gen) -> list[tuple[str, dict]]:
    alert = "CheckoutTargetsMissing"
    q = firing(alert, ["severity"])
    exp = [{"alertname": alert, "severity": "page"}]
    out = []

    # Service discovery loses every checkout target: their series go stale.
    # 9 minutes later they are back, and the alert must stop.
    gone = gen.randint(5, 9)
    length = gone + 14
    n = gen.randint(2, 4)
    series = service(gen, JOB, [gen.instance() for _ in range(n)], weights(gen, n), length,
                     scrape=lambda m, idx: None if gone <= m < gone + 9 else 1)
    series += healthy(gen, "payments", length)
    out.append((
        "when every checkout target disappears, it alerts after 5 minutes, with severity page, and stops when "
        "they are back",
        test_file("", "targets gone", series, [
            (q, gone - 1, []),
            (q, gone + 4, []),
            (q, gone + 6, exp),
            (q, gone + 8, exp),
            (q, gone + 10.5, []),
        ]),
    ))

    # Checkout was never scraped at all; another job was. Checkout has had
    # no up series for all of the history, so the alert fires from the start.
    length = 14
    series = healthy(gen, "payments", length)
    out.append((
        "when no checkout target was ever scraped, it alerts",
        test_file("", "never scraped", series, [(q, 3, exp), (q, 7, exp), (q, 12, exp)]),
    ))

    # Every checkout instance is down but still scraped: up is 0, not missing.
    down = gen.randint(3, 6)
    length = down + 16
    n = gen.randint(2, 3)
    series = service(gen, JOB, [gen.instance() for _ in range(n)], weights(gen, n), length,
                     scrape=lambda m, idx: 0 if m >= down else 1)
    series += healthy(gen, "payments", length)
    out.append((
        "instances that are down but still scraped (up is 0) do not count as missing",
        test_file("", "down but scraped", series, [(q, down + t, []) for t in (4, 8, 14)]),
    ))

    # Another job's targets disappear; checkout stays.
    gone = gen.randint(4, 8)
    length = gone + 14
    series = healthy(gen, "payments", length, scrape=lambda m, idx: None if m >= gone else 1)
    series += healthy(gen, JOB, length)
    out.append((
        "when another job's targets disappear, it does not alert",
        test_file("", "other job gone", series, [(q, gone + t, []) for t in (4, 8, 12)]),
    ))
    return out


# --- CheckoutHighErrorRate ---------------------------------------------------


def error_rate(gen: Gen) -> list[tuple[str, dict]]:
    alert = "CheckoutHighErrorRate"
    q = firing(alert, ["severity"])
    exp = [{"alertname": alert, "severity": "page"}]
    out = []

    def insts(n: int) -> list[str]:
        return [gen.instance() for _ in range(n)]

    # A bad deploy: 10 to 25% of requests fail from minute t0 on. A busy,
    # healthy service shares the Prometheus.
    rps, base5, bad5, p4 = gen.uniform(8, 30), gen.uniform(0, 0.01), gen.uniform(0.10, 0.25), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    codes = gen.rng.choice(FIVE_XX)
    # One instance restarts during the outage; its counters start from 0.
    series = service(gen, JOB, insts(3), weights(gen, 3), length,
                     lambda m: (rps, bad5 if m >= t0 else base5, p4, {}), codes5=codes,
                     restarts={0: t0 + gen.randint(6, 9)})
    # 6 to 8 times the traffic: mixed in, even 25% of 5xx would drop under 5%.
    series += healthy(gen, "payments", length, rps=rps * gen.uniform(6, 8))
    out.append((
        f"when {bad5:.0%} of checkout requests fail with {codes_text(codes)} at {rps:.0f} requests per second, "
        "it alerts after 5 minutes (not in the first 2), with severity page, even next to a busy healthy service",
        test_file("", "bad deploy", series, [(q, t0 - 1, []), (q, t0 + 2, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # The upstream behind /api/cart and /api/products breaks: their errors are
    # 502, 503 and 504, no 500, and /api/checkout itself is fine. The whole
    # service is still over 5%.
    rps, bad5, p4 = gen.uniform(8, 30), gen.uniform(0.09, 0.12), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    route_bad = bad5 / 0.5  # cart and products carry half of the traffic
    series = service(gen, JOB, insts(3), weights(gen, 3), length,
                     lambda m: (rps, 0.0, p4, {"/api/cart": route_bad, "/api/products": route_bad} if m >= t0 else {}),
                     codes5=UPSTREAM)
    series += healthy(gen, "payments", length)
    out.append((
        f"when {bad5:.0%} of all requests fail, all on /api/cart and /api/products and split between 502, 503 and "
        "504 with no 500, it alerts after 5 minutes",
        test_file("", "upstream down", series, [(q, t0 - 1, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # A sudden outage: half of all requests fail from minute t0 to t0 + 8.
    # The ratio over 5 minutes is above 5% from t0 + 1 to t0 + 11, so the
    # alert fires at t0 + 6 with a 5-minute "for", and resolves at t0 + 12.
    rps, p4 = gen.uniform(8, 30), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    series = service(gen, JOB, insts(3), weights(gen, 3), length,
                     lambda m: (rps, 0.5 if t0 <= m < t0 + 8 else 0.0, p4, {}))
    series += healthy(gen, "payments", length)
    out.append((
        "when half of all requests fail for 8 minutes, it alerts once that has lasted 5 minutes, not sooner or later, "
        "and stops about 5 minutes after the errors end",
        test_file("", "sudden outage", series,
                  [(q, t0 - 1, []), (q, t0 + 5.5, []), (q, t0 + 6.5, exp), (q, t0 + 10, exp), (q, t0 + 14.5, [])]),
    ))

    # A flaky dependency: 30% of requests fail one minute in three. Over any
    # 5 minutes that is 7.5 to 15%, always over 5%; a shorter window, or
    # irate(), keeps dropping to 0 and the alert never holds for 5 minutes.
    rps, p4 = gen.uniform(8, 30), gen.uniform(0.02, 0.08)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    series = service(gen, JOB, insts(3), weights(gen, 3), length,
                     lambda m: (rps, 0.3 if m >= t0 and (m - t0) % 3 == 0 else 0.0, p4, {}))
    series += healthy(gen, "payments", length)
    out.append((
        "when a flaky dependency makes 30% of requests fail one minute in three, that is over 5% for every 5-minute "
        "window, and it alerts",
        test_file("", "flaky dependency", series, [(q, t0 - 1, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # A steady 1 to 3% of 5xx, while another job fails a lot.
    rps, p5, p4 = gen.uniform(8, 30), gen.uniform(0.01, 0.03), gen.uniform(0.02, 0.08)
    length = 26
    series = service(gen, JOB, insts(3), weights(gen, 3), length, lambda m: (rps, p5, p4, {}))
    other_rps = rps * gen.uniform(0.8, 1.2)
    series += service(gen, "payments", insts(2), weights(gen, 2), length, lambda m: (other_rps, 0.3, 0.02, {}))
    out.append((
        f"a steady {p5:.1%} of 5xx does not alert, even when another job fails a lot",
        test_file("", "low 5xx", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # Lots of 4xx (bad requests from clients), few 5xx.
    rps, p5, p4 = gen.uniform(8, 30), gen.uniform(0, 0.01), gen.uniform(0.2, 0.4)
    series = service(gen, JOB, insts(3), weights(gen, 3), length, lambda m: (rps, p5, p4, {}))
    series += healthy(gen, "payments", length)
    out.append((
        f"{p4:.0%} of 4xx answers do not alert: only 5xx count as errors",
        test_file("", "4xx", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # Just over and just under the 5% line, steady for the whole scenario.
    rps, p5, p4 = gen.uniform(10, 30), gen.uniform(0.058, 0.063), gen.uniform(0.02, 0.05)
    t0 = gen.randint(7, 10)
    series = service(gen, JOB, insts(3), weights(gen, 3), t0 + 17,
                     lambda m: (rps, p5 if m >= t0 else 0.0, p4, {}))
    series += healthy(gen, "payments", t0 + 17)
    out.append((
        f"a steady {p5:.1%} of 5xx, just over 5%, alerts",
        test_file("", "just over", series, [(q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))
    rps, p5, p4 = gen.uniform(20, 40), gen.uniform(0.043, 0.046), gen.uniform(0.02, 0.05)
    series = service(gen, JOB, insts(3), weights(gen, 3), length, lambda m: (rps, p5, p4, {}))
    series += healthy(gen, "payments", length)
    out.append((
        f"a steady {p5:.1%} of 5xx, under 5%, does not alert",
        test_file("", "just under", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # Just over 1 request per second: quiet, but busy enough to page.
    rps, p5 = gen.uniform(1.3, 1.6), gen.uniform(0.2, 0.3)
    t0 = gen.randint(7, 10)
    series = service(gen, JOB, insts(2), weights(gen, 2), t0 + 17, lambda m: (rps, p5 if m >= t0 else 0.0, 0.02, {}))
    series += healthy(gen, "payments", t0 + 17)
    out.append((
        f"at {rps:.1f} requests per second, just over 1, it alerts when {p5:.0%} fail",
        test_file("", "quiet but busy enough", series, [(q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))

    # The middle of the night: under 1 request per second, half of them fail.
    rps, p5 = gen.uniform(0.6, 0.85), gen.uniform(0.4, 0.6)
    series = service(gen, JOB, insts(2), weights(gen, 2), length, lambda m: (rps, p5, 0.02, {}))
    series += healthy(gen, "payments", length)
    out.append((
        f"at {rps:.2f} requests per second, under 1, it does not alert even though {p5:.0%} fail",
        test_file("", "low traffic", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # One small route fails a lot; the service as a whole stays under 5%.
    rps, bad = gen.uniform(10, 30), gen.uniform(0.4, 0.6)
    routes = [(CHECKOUT, 0.5), ("/api/cart", 0.28), ("/api/products", 0.2), ("/api/export", 0.02)]
    series = service(gen, JOB, insts(3), weights(gen, 3), length,
                     lambda m: (rps, 0.002, 0.03, {"/api/export": bad}), routes=routes)
    series += healthy(gen, "payments", length)
    out.append((
        f"one route with 2% of the traffic failing {bad:.0%} of requests does not alert: the whole service stays "
        "under 5%",
        test_file("", "one bad route", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # One small instance fails a lot; the service as a whole stays under 5%.
    small = insts(4)
    rps, bad, p5 = gen.uniform(25, 40), gen.uniform(0.35, 0.5), gen.uniform(0, 0.005)
    series = service(gen, JOB, small, [0.05] + [0.95 / 3] * 3, length, lambda m: (rps, p5, 0.03, {small[0]: bad}))
    series += healthy(gen, "payments", length)
    out.append((
        f"one instance with 5% of the traffic failing {bad:.0%} of requests does not alert: "
        "the whole service stays under 5%",
        test_file("", "one bad instance", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # A bad deploy while one instance is down and requests are slow: the
    # error rate alert still fires.
    rps, bad5 = gen.uniform(8, 30), gen.uniform(0.15, 0.25)
    t0 = gen.randint(7, 10)
    length = t0 + 17
    series = service(gen, JOB, insts(4), weights(gen, 4), length,
                     lambda m: (rps, bad5 if m >= t0 else 0.001, 0.03, {}),
                     dist=lambda m, idx, route: TAIL if route == CHECKOUT and m >= t0 else FAST,
                     scrape=lambda m, idx: 0 if idx == 3 and m >= t0 - 2 else 1)
    series += healthy(gen, "payments", length)
    out.append((
        f"when {bad5:.0%} of requests fail while one instance is down and requests are slow, it still alerts",
        test_file("", "errors during an outage", series, [(q, t0 - 1, []), (q, t0 + 12, exp), (q, t0 + 15, exp)]),
    ))
    return out


# --- CheckoutSlowRequests ----------------------------------------------------


def slow_requests(gen: Gen) -> list[tuple[str, dict]]:
    alert = "CheckoutSlowRequests"
    q = firing(alert, ["severity"])
    exp = [{"alertname": alert, "severity": "ticket"}]
    out = []

    def checkout(length: int, rps: float, dist, w=None, restarts=None, phases=None, scrape=None) -> list:
        """Checkout with few errors, and dist(minute, instance_index, route) for its latency."""
        w = w or weights(gen, 3)
        p5, p4 = gen.uniform(0, 0.003), gen.uniform(0.01, 0.04)
        return service(gen, JOB, [gen.instance() for _ in w], w, length, phases or (lambda m: (rps, p5, p4, {})),
                       dist=dist, restarts=restarts, scrape=scrape)

    # Another service has its own fast /api/checkout route with much more
    # traffic: mixed in, it would hide the slowdown.
    rps = gen.uniform(5, 20)
    t0 = gen.randint(7, 10)
    length = t0 + 26
    series = checkout(length, rps, lambda m, idx, route: TAIL if route == CHECKOUT and t0 <= m < t0 + 20 else FAST,
                      restarts={0: t0 + gen.randint(13, 15)})
    series += healthy(gen, "payments", length, rps=rps * gen.uniform(4, 6))
    out.append((
        "when 12% of checkout's /api/checkout requests take over a second (p95 well over 500 ms, average under "
        "it), it alerts after 10 minutes, with severity ticket, even next to a busy fast service, and stops "
        "when requests are fast again",
        # 12% slow requests: one slow interval in the 5-minute window is 3%,
        # two are 6%, so p95 is high from t0 + 2 and a 10-minute "for" fires
        # at t0 + 12.
        # The slowdown ends at t0 + 20: from t0 + 23 at most one slow
        # interval is left in the window (3%), so the alert stops.
        test_file("", "slow checkout", series, [(q, t0 - 1, []), (q, t0 + 5, []), (q, t0 + 11.5, []),
                                                (q, t0 + 12.5, exp), (q, t0 + 16, exp), (q, t0 + 19, exp),
                                                (q, t0 + 24.5, [])]),
    ))

    # Another service's /api/checkout is slow; checkout's is fast.
    length = 26
    series = checkout(length, gen.uniform(5, 20), lambda m, idx, route: FAST)
    other_rps = gen.uniform(5, 20)
    series += service(gen, "payments", [gen.instance() for _ in range(3)], weights(gen, 3), length,
                      lambda m: (other_rps, 0.002, 0.02, {}),
                      dist=lambda m, idx, route: SLOW if route == CHECKOUT else FAST)
    out.append((
        "a slow /api/checkout route of another job does not alert",
        test_file("", "other job slow", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # p95 between 500 ms and 1 s: 10% of requests take 0.5 to 1 s.
    t0 = gen.randint(7, 10)
    mid = [0.85, 0.05, 0, 0, 0.10, 0, 0, 0]
    series = checkout(t0 + 21, gen.uniform(5, 20), lambda m, idx, route: mid if route == CHECKOUT and m >= t0 else FAST)
    series += healthy(gen, "payments", t0 + 21)
    out.append((
        "a p95 of about 750 ms, over 500 ms but under a second, alerts",
        test_file("", "p95 750 ms", series, [(q, t0 + 16, exp), (q, t0 + 19, exp)]),
    ))
    # p95 between 250 and 500 ms: 10% of requests take 0.25 to 0.5 s.
    near = [0.85, 0.05, 0, 0.10, 0, 0, 0, 0]
    series = checkout(length, gen.uniform(5, 20), lambda m, idx, route: near if route == CHECKOUT else FAST)
    series += healthy(gen, "payments", length)
    out.append((
        "a p95 of about 375 ms, under 500 ms, does not alert",
        test_file("", "p95 375 ms", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    series = checkout(length, gen.uniform(5, 20), lambda m, idx, route: FAST)
    series += healthy(gen, "payments", length)
    out.append((
        "fast requests (p95 around 100 ms) do not alert",
        test_file("", "fast", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    series = checkout(length, gen.uniform(5, 20), lambda m, idx, route: SLOW if route != CHECKOUT else FAST)
    series += healthy(gen, "payments", length)
    out.append((
        "a slow route other than /api/checkout does not alert",
        test_file("", "other route slow", series, [(q, t, []) for t in (10, 16, 22, 25)]),
    ))

    # The first instance gets 3% of the traffic, and all of it is slow.
    series = checkout(length, gen.uniform(20, 40), lambda m, idx, route: SLOW if idx == 0 else FAST,
                      [0.03, 0.97 / 3, 0.97 / 3, 0.97 / 3])
    series += healthy(gen, "payments", length)
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
    series = checkout(length, gen.uniform(5, 20),
                      lambda m, idx, route: short if route == CHECKOUT and t0 <= m < t0 + 7 else FAST)
    series += healthy(gen, "payments", length)
    out.append((
        "a 7-minute slowdown does not alert: it has to last 10 minutes",
        test_file("", "short slowdown", series, [(q, t0 + t, []) for t in (5, 7, 8, 9, 10, 11, 12, 14, 18)]),
    ))

    # A slowdown while one instance is down and many requests fail: the
    # latency alert still fires.
    t0 = gen.randint(7, 10)
    length = t0 + 21
    rps, bad5 = gen.uniform(5, 20), gen.uniform(0.15, 0.25)
    series = checkout(length, rps, lambda m, idx, route: TAIL if route == CHECKOUT and m >= t0 else FAST,
                      w=weights(gen, 4), phases=lambda m: (rps, bad5 if m >= t0 else 0.001, 0.03, {}),
                      scrape=lambda m, idx: 0 if idx == 3 and m >= t0 - 2 else 1)
    series += healthy(gen, "payments", length)
    out.append((
        "a slowdown while one instance is down and many requests fail still alerts",
        test_file("", "slow during an outage", series, [(q, t0 - 1, []), (q, t0 + 12.5, exp), (q, t0 + 19, exp)]),
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
            # A random time, to the millisecond, like a real scrape.
            doc["tests"][0]["start_timestamp"] = f"{rng.randrange(EARLIEST, LATEST) + rng.randrange(1000) / 1000:.3f}"
            name = f"{check}-{n}.yml"
            (out / name).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            scenarios.append({"file": name, "check": check, "description": description})
    (out / "scenarios.json").write_text(json.dumps(scenarios, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
