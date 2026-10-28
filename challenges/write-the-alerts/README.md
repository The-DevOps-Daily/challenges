# Write the alerts

Prometheus · Intermediate · about 45 minutes

## The situation

Last week the checkout service had a bad night. One instance was down for an hour, then a deploy made one checkout in five fail with a 500. Customers noticed before anyone on the team did.

Prometheus scrapes the service and there is a dashboard, but `alerts.yml` has only two rules. One pages for every missed scrape of anything Prometheus watches. The other was copied from a blog post years ago, and nobody trusts it.

Write the alerts so the on-call engineer gets paged for real problems, and only for real problems.

## The metrics

Prometheus scrapes every checkout instance with `job="checkout"`. Other services (for example `job="payments"`) are scraped by the same Prometheus.

| Series | What it is |
| --- | --- |
| `up{job, instance}` | Written by Prometheus for every target it scrapes: 1 when the scrape worked, 0 when it failed. When service discovery drops a target, its `up` series ends. |
| `http_requests_total{job, instance, route, code}` | Counter of answered requests. `code` is the HTTP status, such as `200`, `404` or `503`. |
| `http_request_duration_seconds_bucket{job, instance, route, le}` | Histogram of request durations, with buckets at 0.05, 0.1, 0.25, 0.5, 1, 2.5 and 5 seconds, plus `+Inf`. `_sum` and `_count` are there too. |

## Your task

Make `alerts.yml` define these four alerts. Only the checkout job counts in each of them.

| Alert | `severity` label | Fires when |
| --- | --- | --- |
| `CheckoutInstanceDown` | `page` | A checkout instance fails its scrapes (`up` is 0) for 3 minutes. One alert per instance, with its `instance` label. |
| `CheckoutTargetsMissing` | `page` | There has been no `up` series for the checkout job at all for 5 minutes, for example because service discovery lost every target. |
| `CheckoutHighErrorRate` | `page` | Over the last 5 minutes, more than 5% of all checkout requests (every instance and route together) got a 5xx answer, and the service got at least 1 request per second. This has held for 5 minutes. One alert for the whole service. |
| `CheckoutSlowRequests` | `ticket` | The 95th percentile duration of `/api/checkout` requests, for all instances together and over 5-minute windows, has been above 500 ms for 10 minutes. |

Each alert also needs:

- a `summary` annotation that says what is wrong,
- a `runbook_url` annotation with an `https://` link (an example.com link is fine).

You can add other labels and annotations, and recording rules if they help. Each alert stops firing on its own once its condition no longer holds, so do not use `keep_firing_for`. Every series selector names its metric, for example `up{job="checkout"}` and not `{job="checkout"}`, and reads only the metrics above or a recording rule in `alerts.yml`.

## What the grader checks

| Check | Passes when |
| --- | --- |
| The rules load | `promtool check rules` passes, and `alerts.yml` defines the four alerts once each, with the `severity` label above, a `summary` and an `https://` `runbook_url`. Every selector names one of the metrics above or a recording rule in the file, so no rule reads the `ALERTS` series, and no template in a label or annotation calls `query`. No rule may write the `ALERTS` series, set the `alertname` or `alertstate` labels, or use `keep_firing_for`. |
| Instance down | An instance that stays down alerts after 3 minutes, one alert per instance. A 2-minute blip does not alert. An instance of another job does not alert. |
| Targets missing | When every checkout target disappears, or checkout was never scraped, it alerts after 5 minutes (not after 4, but by 6). Instances that are down but still scraped do not count as missing, and neither do another job's targets. |
| Error rate | More than 5% of 5xx answers over 5 minutes, even just over 5%, at 1 request per second or more, alerts once that has lasted 5 minutes, not sooner or later, and stops about 5 minutes after the errors end. That holds whatever the 5xx codes and routes are (one scenario has only 502, 503 and 504, all on `/api/cart` and `/api/products`), when a flaky dependency fails one minute in three, and next to a busy healthy service. Under 5% of 5xx (even just under), lots of 4xx, under 1 request per second, and one small route or instance that fails a lot do not alert. |
| Slow requests | A p95 for checkout's `/api/checkout` over 500 ms, even only about 750 ms, alerts once that has lasted 10 minutes, not sooner or later, with severity `ticket`, even next to a busy fast service with its own `/api/checkout`. A p95 under 500 ms (even about 375 ms), another slow route, the slow `/api/checkout` of another job, one small slow instance and a 7-minute slowdown do not alert. |

For every check, the grader writes new series at random (instance addresses, traffic, error ratios, latencies and the minute when trouble starts) and runs `promtool test rules` on them in a container, one scenario at a time. The tests read the `ALERTS` series that Prometheus writes for firing alerts, so your annotations and extra labels do not change the result. When a scenario fails, the grader says which one.

## Files you may change

- `challenges/write-the-alerts/alerts.yml`

A PR that changes any other file is not graded.

## Hints

<details>
<summary>Hint 1: one missed scrape is not an outage</summary>

An alert can wait before it fires. Read about `for` in the [alerting rules docs](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).
</details>

<details>
<summary>Hint 2: a target that is gone has no up series</summary>

`up == 0` needs an `up` series to compare. When every target disappears, there is nothing to compare. Look at `absent()`.
</details>

<details>
<summary>Hint 3: a ratio for the whole service</summary>

`rate()` gives requests per second per series. To get one ratio for the service, add up the 5xx rates and divide by the sum of all rates. Two conditions can be joined with `and`.
</details>

<details>
<summary>Hint 4: percentiles from a histogram</summary>

`histogram_quantile()` works on bucket rates. When you sum the buckets of several instances, keep the `le` label.
</details>

## Test it locally

You need [promtool](https://prometheus.io/download/) (it ships with Prometheus) and Python 3. From this folder:

```bash
promtool check rules alerts.yml
promtool test rules tests/example.test.yml

# The same kind of random scenarios the grader runs:
python3 grade/gen_tests.py --out /tmp/alert-tests --rules "$PWD/alerts.yml"
for f in /tmp/alert-tests/*.yml; do promtool test rules "$f" > /dev/null || echo "FAILED: $f"; done
```

`/tmp/alert-tests/scenarios.json` says what each scenario is. Add `--seed 1` to get the same scenarios again. With Docker instead of a local promtool:

```bash
python3 grade/gen_tests.py --out /tmp/alert-tests --rules /rules/alerts.yml
docker run --rm -v "$PWD/alerts.yml:/rules/alerts.yml:ro" -v /tmp/alert-tests:/tests:ro \
  --entrypoint sh prom/prometheus:v3.15.0 \
  -c 'for f in /tests/*.yml; do promtool test rules "$f" > /dev/null || echo "FAILED: $f"; done'
```

## Submit

1. Fork this repo and create a branch.
2. Commit your `alerts.yml` in `challenges/write-the-alerts/`.
3. Open a PR against `main` in `The-DevOps-Daily/challenges`.

The grader comments on the PR with a result for each check. Push more commits to the same branch until every check passes. Then the PR gets the `passed` label and is closed. It is never merged. If GitHub shows a merge conflict on the PR, the grader cannot run: update your branch from `main` first.
