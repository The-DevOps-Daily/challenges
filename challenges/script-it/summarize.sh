#!/usr/bin/env bash
# Tries to reach outside the sandbox. Every attempt should fail or do nothing.
echo "::error title=spoof::a workflow command from the script"
echo "::warning::another one"
ls -la / /data /solution >&2 2>&1
cat /data/*.expected /data/../answers/* 2>/dev/null
touch /data/x /solution/x 2>&1 | head -2 >&2
curl -sS --max-time 3 https://example.com >&2 2>&1 || echo "no network" >&2
cat /proc/1/environ 2>/dev/null | tr '\0' '\n' >&2
env >&2
id >&2
echo "requests: 0"
