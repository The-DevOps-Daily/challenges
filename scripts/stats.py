"""Count who passed each challenge and publish it to the stats branch.

Reads PRs labeled "passed" through the API (only maintainers and the Report
workflow can add labels) and writes two files to the stats branch:

- stats.json: passes per challenge, counted once per GitHub user. The site
  reads this file.
- passes.json: one entry per user and challenge (login, user id, PR, time),
  for badges later.

PRs with the "not-counted" label (test runs, abuse) are left out.
"""

from __future__ import annotations

import base64
import datetime
import json
import sys
import urllib.error

from gradelib import GitHub, load_challenges

BRANCH = "stats"
EXCLUDE = "not-counted"


def put_file(gh: GitHub, path: str, content: str) -> None:
    current_sha = None
    try:
        current = gh.get(f"/repos/{gh.repo}/contents/{path}?ref={BRANCH}")
        current_sha = current["sha"]
        if base64.b64decode(current["content"]).decode("utf-8") == content:
            print(f"{path}: unchanged")
            return
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    body = {
        "message": f"Update {path}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if current_sha:
        body["sha"] = current_sha
    gh.request("PUT", f"/repos/{gh.repo}/contents/{path}", body=body)
    print(f"{path}: updated")


def main() -> int:
    gh = GitHub()
    challenges = load_challenges()
    issues = gh.paginate(f"/repos/{gh.repo}/issues?state=all&labels=passed", limit=10000)

    passes: dict[tuple[str, int], dict] = {}
    for issue in issues:
        if "pull_request" not in issue:
            continue
        names = {label["name"] for label in issue.get("labels", [])}
        if EXCLUDE in names:
            continue
        user = issue.get("user") or {}
        if user.get("type") != "User":
            continue
        for name in names:
            if not name.startswith("challenge:"):
                continue
            cid = name.split(":", 1)[1]
            if cid not in challenges:
                continue
            key = (cid, int(user["id"]))
            entry = {
                "challenge": cid,
                "login": user["login"],
                "user_id": int(user["id"]),
                "pr": issue["number"],
                "passed_at": issue.get("closed_at") or issue.get("updated_at"),
            }
            if key not in passes or entry["passed_at"] < passes[key]["passed_at"]:
                passes[key] = entry

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {cid: 0 for cid in sorted(challenges)}
    for cid, _ in passes:
        counts[cid] += 1
    stats = {"schema": 1, "updated_at": now, "challenges": {cid: {"passed": n} for cid, n in counts.items()}}
    ordered = sorted(passes.values(), key=lambda e: (e["challenge"], e["passed_at"], e["user_id"]))
    passes_doc = {"schema": 1, "updated_at": now, "passes": ordered}

    # Only rewrite when the counts change, so the branch does not get a
    # commit on every run.
    try:
        current = gh.get(f"/repos/{gh.repo}/contents/stats.json?ref={BRANCH}")
        old = json.loads(base64.b64decode(current["content"]))
        old_passes = json.loads(base64.b64decode(gh.get(f"/repos/{gh.repo}/contents/passes.json?ref={BRANCH}")["content"]))
        if old.get("challenges") == stats["challenges"] and old_passes.get("passes") == ordered:
            print("No change in passes.")
            return 0
    except (urllib.error.HTTPError, ValueError, KeyError):
        pass

    put_file(gh, "passes.json", json.dumps(passes_doc, indent=2) + "\n")
    put_file(gh, "stats.json", json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
