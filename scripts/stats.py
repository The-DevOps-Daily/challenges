"""Count who passed each challenge and publish it to the stats branch.

Reads PRs labeled "passed" through the API (only maintainers and the Report
workflow can add labels). A PR counts when it has exactly one challenge label
and a pass record from the Report workflow (a comment by github-actions[bot])
for that challenge. It writes two files to the stats branch:

- stats.json: passes per challenge, counted once per GitHub user. The site
  reads this file.
- passes.json: one entry per user and challenge (login, user id, PR, graded
  commit, run, time), for badges later.

PRs with the "not-counted" label (test runs, abuse) are left out; adding that
label is also how a maintainer withdraws a pass.
"""

from __future__ import annotations

import base64
import datetime
import json
import re
import sys
import urllib.error

from gradelib import GitHub, load_challenges

BRANCH = "stats"
EXCLUDE = "not-counted"
BOT_LOGIN = "github-actions[bot]"
RECORD_RE = re.compile(r"<!-- challenges-result (\{.*?\}) -->")
# Bump when the rules for counting a pass change; older cached entries are
# checked again against the PR's pass record.
POLICY = 1


def read_branch_json(gh: GitHub, path: str) -> dict:
    try:
        current = gh.get(f"/repos/{gh.repo}/contents/{path}?ref={BRANCH}")
        return json.loads(base64.b64decode(current["content"]))
    except (urllib.error.HTTPError, ValueError, KeyError):
        return {}


def find_record(gh: GitHub, number: int, cid: str) -> dict | None:
    """The Report workflow's pass record for this challenge on this PR."""
    for comment in gh.paginate(f"/repos/{gh.repo}/issues/{number}/comments", limit=100000):
        if comment["user"]["login"] != BOT_LOGIN:
            continue
        match = RECORD_RE.search(comment.get("body") or "")
        if not match:
            continue
        try:
            record = json.loads(match.group(1))
        except ValueError:
            continue
        if record.get("challenge") == cid and record.get("status") == "passed":
            return record
    return None


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
    limit = 50000
    issues = gh.paginate(f"/repos/{gh.repo}/issues?state=all&labels=passed", limit=limit)
    if len(issues) >= limit:
        raise SystemExit("too many labeled PRs to count in one run; not publishing partial stats")

    old_passes = read_branch_json(gh, "passes.json")
    known = {(e["challenge"], e["pr"]): e for e in old_passes.get("passes", []) if isinstance(e, dict)}

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
        challenge_labels = [n for n in names if n.startswith("challenge:")]
        if len(challenge_labels) != 1:
            print(f"PR #{issue['number']}: {len(challenge_labels)} challenge labels; not counted")
            continue
        cid = challenge_labels[0].split(":", 1)[1]
        if cid not in challenges:
            continue
        entry = known.get((cid, issue["number"]))
        if entry is None or entry.get("user_id") != int(user["id"]) or entry.get("policy") != POLICY:
            record = find_record(gh, issue["number"], cid)
            if record is None:
                print(f"PR #{issue['number']}: no pass record from the Report workflow; not counted")
                continue
            entry = {
                "challenge": cid,
                "login": user["login"],
                "user_id": int(user["id"]),
                "pr": issue["number"],
                "sha": record.get("sha"),
                "run_id": record.get("run_id"),
                "passed_at": issue.get("closed_at") or issue.get("updated_at"),
                "policy": POLICY,
            }
        key = (cid, int(user["id"]))
        if key not in passes or entry["passed_at"] < passes[key]["passed_at"]:
            passes[key] = entry

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {cid: 0 for cid in sorted(challenges)}
    for cid, _ in passes:
        counts[cid] += 1
    stats = {"schema": 1, "updated_at": now, "challenges": {cid: {"passed": n} for cid, n in counts.items()}}
    ordered = sorted(passes.values(), key=lambda e: (e["challenge"], e["passed_at"], e["user_id"]))
    passes_doc = {"schema": 1, "updated_at": now, "passes": ordered}

    # Only rewrite when something changed, so the branch does not get a
    # commit on every run.
    old_stats = read_branch_json(gh, "stats.json")
    if old_stats.get("challenges") == stats["challenges"] and old_passes.get("passes") == ordered:
        print("No change in passes.")
        return 0

    put_file(gh, "passes.json", json.dumps(passes_doc, indent=2) + "\n")
    put_file(gh, "stats.json", json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
