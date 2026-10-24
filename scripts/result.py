"""Build result.json from the scope decision and the per-check records.

Usage:
  result.py           write $RUNNER_TEMP/grade/result.json
  result.py --exit-code   exit 0 when the result is passed or not_graded
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

from gradelib import CHECK_STATUSES, load_challenges, sanitize_detail

SCHEMA = 1


def build(out_dir: Path) -> dict:
    scope_file = out_dir / "scope.json"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    head_sha = os.environ.get("HEAD_SHA", "")
    result = {
        "schema": SCHEMA,
        "challenge": None,
        "head_sha": head_sha,
        "status": "error",
        "reason": None,
        "checks": [],
        "graded_at": now,
    }
    if not scope_file.is_file():
        return result

    scope = json.loads(scope_file.read_text(encoding="utf-8"))
    result["challenge"] = scope.get("challenge")
    if scope["kind"] == "not_graded":
        result["status"] = "not_graded"
        return result
    if scope["kind"] == "rejected":
        result["status"] = "rejected"
        result["reason"] = scope.get("reason")
        return result

    challenge = load_challenges()[scope["challenge"]]
    records: dict[str, tuple[str, str]] = {}
    checks_file = out_dir / "checks.tsv"
    if checks_file.is_file():
        for line in checks_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            cid, status = parts[0], parts[1]
            detail = parts[2] if len(parts) == 3 else ""
            if cid in challenge.check_ids and status in CHECK_STATUSES:
                records[cid] = (status, sanitize_detail(detail))

    finished = (out_dir / "done").is_file()
    for cid in challenge.check_ids:
        status, detail = records.get(cid, ("skip", "Not run: the grader stopped before this check."))
        result["checks"].append({"id": cid, "status": status, "detail": detail})

    if not finished:
        result["status"] = "error"
    elif all(c["status"] == "pass" for c in result["checks"]):
        result["status"] = "passed"
    else:
        result["status"] = "failed"
    return result


def main() -> int:
    out_dir = Path(os.environ["RUNNER_TEMP"]) / "grade"
    out_dir.mkdir(parents=True, exist_ok=True)
    result_file = out_dir / "result.json"

    if "--exit-code" in sys.argv[1:]:
        result = json.loads(result_file.read_text(encoding="utf-8"))
        print(f"Result: {result['status']}")
        for check in result["checks"]:
            print(f"  {check['status']:4}  {check['id']}  {check['detail']}")
        return 0 if result["status"] in ("passed", "not_graded") else 1

    result = build(out_dir)
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
