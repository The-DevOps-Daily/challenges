# Script it: summarize an access log

Bash · Starter · about 45 minutes

## The situation

When the site is slow, the first question on the incident call is always the same: who is hitting us, which pages, and how many errors? Someone started `summarize.sh` to answer it from an nginx access log, then got pulled onto something else. Right now it counts lines and not much more, and ShellCheck is not happy with it.

Finish it.

## The input

Each line of the log is in nginx's default "combined" format:

```text
203.0.113.7 - alice [24/Oct/2026:10:15:32 +0000] "GET /products?page=2 HTTP/1.1" 200 5123 "https://example.com/" "Mozilla/5.0 (X11; Linux x86_64)"
```

A line is a **request** when it has, in this order:

1. the client address: the text up to the first space (IPv4 or IPv6, never empty),
2. two more fields without spaces (ident and user, usually `-`),
3. the time in square brackets,
4. the request in double quotes: a method in capital letters, a space, the path, a space, the protocol,
5. a space and a three-digit status code from 200 to 599, followed by a space or the end of the line.

As an extended regular expression:

```text
^[^ ]+ [^ ]+ [^ ]+ \[[^]]*\] "[A-Z]+ [^ "]+ [^ "]+" [2-5][0-9][0-9]( |$)
```

Empty lines are ignored. Every other line that is not a request is **skipped** (truncated lines, `"-" 400` lines from bad clients, error messages that ended up in the file).

## Your task

Make `summarize.sh` print this report for a log, and nothing else on stdout:

```text
requests: 10
unique_ips: 4
status_2xx: 7
status_3xx: 1
status_4xx: 1
status_5xx: 1
skipped: 0
top_ips:
4 10.0.0.1
3 10.0.0.2
2 10.0.0.3
1 10.0.0.4
top_paths:
3 /products
2 /
2 /cart
1 /api/orders
1 /missing
```

- `requests`, `unique_ips`, the `status_` lines and the top lists count requests only. `skipped` counts the skipped lines.
- `top_ips` and `top_paths` list `count value`, highest count first. Equal counts are sorted by the value in byte order (`LC_ALL=C sort`), so `10.0.0.10` comes before `10.0.0.9`.
- A path is the request path without its query string: `/products?page=2` counts as `/products`.
- Each top list has at most N lines (5 by default), fewer when there are fewer distinct values, and none for an empty log.

Usage:

```text
./summarize.sh [-n N] [LOGFILE]
```

- With no `LOGFILE`, or with `-`, read the log from stdin.
- `-n N` sets the length of the top lists. N is a whole number from 1 up.
- A bad `-n` value, an unknown option or more than one file is a usage error: print a message on stderr, nothing on stdout, and exit with code 2.
- A log file that is missing, unreadable or a directory: print a message on stderr, nothing on stdout, and exit with code 1.
- On success, exit with code 0.

`sample/access.log` is a small log to try it on. Its report is the one above.

## What the grader checks

| Check | Passes when |
| --- | --- |
| ShellCheck is clean | ShellCheck 0.11 reports no issues at any severity, and the script does not switch checks off with `# shellcheck disable=`. |
| Report for a known log | The report for `sample/access.log` matches exactly. |
| Edge cases | Malformed lines are skipped, empty lines ignored, query strings stripped, ties sorted in byte order, a last line without a newline counted, and an empty log gives an all-zero report. |
| File or stdin | The log is read from a file, from stdin, from `-`, and from a file name with a space in it. |
| Options and usage errors | `-n` works, and bad arguments exit 2 with a message on stderr. |
| Unreadable input | A missing file, an unreadable file and a directory exit 1 with a message on stderr. |
| Random logs | Three logs generated at random during grading get the right report. |
| Speed | A 100,000-line log is summarized correctly in under 10 seconds. |

The grader runs your script as `bash summarize.sh ...` in an `ubuntu:24.04` container with no network and an unprivileged user. That image has bash 5.2, GNU coreutils, grep and sed, and **mawk** as `awk`. gawk-only features such as `asort` or `PROCINFO["sorted_in"]` are not there.

## Files you may change

- `challenges/script-it/summarize.sh`

A PR that changes any other file is not graded.

## Hints

<details>
<summary>Hint 1: getting the edge cases right</summary>

Read the line with one regular expression match instead of cutting fields by position. The user agent contains spaces, so field numbers after the request are not stable.
</details>

<details>
<summary>Hint 2: speed</summary>

A `while read` loop in bash over 100,000 lines takes far longer than 10 seconds, and starting `cut` or `grep` once per line is slower still. Let one `awk` program do the reading and counting, and `sort` do the ranking.
</details>

<details>
<summary>Hint 3: the ShellCheck warnings in the starter</summary>

Look up each code on the ShellCheck wiki (for example `https://www.shellcheck.net/wiki/SC2086`). The fix is in the code, not in a disable comment.
</details>

<details>
<summary>Hint 4: <code>set -o pipefail</code> and <code>head</code></summary>

`sort | head -n 5` can kill `sort` with SIGPIPE when there is a lot of output. With `pipefail` and `set -e` that ends the script early. Test with a big log.
</details>

## Test it locally

The tests in `grade/` run on your machine too. You need bash, Python 3 and [bats-core](https://github.com/bats-core/bats-core) 1.5 or later. From this folder:

```bash
./summarize.sh sample/access.log
shellcheck summarize.sh
SANDBOX=none SOLUTION_DIR="$PWD" bats grade/summarize.bats
```

`SANDBOX=none` runs the script directly instead of in a container. To make a random log and its expected report: `python3 grade/gen_log.py --seed 1 --lines 2000 --log /tmp/test.log --expected /tmp/test.txt`.

## Submit

1. Fork this repo and create a branch.
2. Commit your `summarize.sh` in `challenges/script-it/`.
3. Open a PR against `main` in `The-DevOps-Daily/challenges`.

The grader comments on the PR with a result for each check. Push more commits to the same branch until every check passes. Then the PR gets the `passed` label and is closed. It is never merged.
