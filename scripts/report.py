"""Comment the grade on a PR, label it, and close it when it passed.

Runs in the Report workflow (workflow_run, from main) with a token that can
write to pull requests. Rules:

- The PR, its head SHA and its changed files come from the workflow_run event
  and the GitHub API. The artifact cannot pick the PR.
- The files the graded commit changes (compared with main, by SHA, not the
  PR's live file list) decide the challenge. If they are outside one
  challenge's solution files, the commit could have changed the grader or
  the workflow, so the artifact is ignored.
- The artifact must come from this exact run attempt, and a pass needs the
  run itself to have succeeded.
- A PR that already passed is not graded again, and it keeps exactly one
  challenge label, so labels cannot pile up across challenges.
- The artifact is data. It is size-limited, schema-checked, and only its
  per-check pass, fail or skip values and short, sanitized details are used.
  Check names and messages come from main.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import zipfile

from gradelib import (
    CHECK_STATUSES,
    REJECT_REASONS,
    SHA_RE,
    Challenge,
    GitHub,
    evaluate_scope,
    load_challenges,
    sanitize_detail,
)

GRADE_WORKFLOW = ".github/workflows/grade.yml"
ARTIFACT_PREFIX = "grade-result-"
MAX_ARTIFACT_BYTES = 64 * 1024
BOT_LOGIN = "github-actions[bot]"
SITE = "https://devops-daily.com/challenges"
RESULT_KEYS = {"schema", "challenge", "head_sha", "status", "reason", "checks", "graded_at"}
ARTIFACT_STATUSES = {"passed", "failed", "rejected", "not_graded", "error"}
LABEL_PASSED = "passed"
CHALLENGE_LABEL = "challenge:"
ALREADY_MARKER = "<!-- challenges-grader already-passed -->"


class BadResult(Exception):
    pass


def safe_path(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]", "?", path)[:120]


def marker(head_sha: str) -> str:
    return f"<!-- challenges-grader sha={head_sha} -->"


def pass_record(challenge: Challenge, head_sha: str, run_id: int, run_attempt: int) -> str:
    """A machine-readable pass record, read by stats.py (and later badges)."""
    record = {"challenge": challenge.id, "status": "passed", "sha": head_sha, "run_id": run_id, "run_attempt": run_attempt}
    return f"<!-- challenges-result {json.dumps(record, sort_keys=True)} -->"


def find_pr(gh: GitHub, head_owner: str, head_branch: str, head_sha: str) -> dict | None:
    query = urllib.parse.urlencode({"state": "open", "head": f"{head_owner}:{head_branch}", "base": "main"})
    for pr in gh.get(f"/repos/{gh.repo}/pulls?{query}") or []:
        if (
            pr["head"]["sha"] == head_sha
            and pr["base"]["ref"] == "main"
            and pr["base"]["repo"]["full_name"] == gh.repo
        ):
            return pr
    return None


def load_artifact(gh: GitHub, run_id: int, run_attempt: int) -> dict | None:
    name = f"{ARTIFACT_PREFIX}{run_attempt}"
    listing = gh.get(f"/repos/{gh.repo}/actions/runs/{run_id}/artifacts?name={name}&per_page=20")
    artifacts = [a for a in listing.get("artifacts", []) if a["name"] == name and not a["expired"]]
    if len(artifacts) != 1:
        return None
    artifact = artifacts[0]
    if artifact["size_in_bytes"] > MAX_ARTIFACT_BYTES:
        raise BadResult("artifact too large")
    blob, _ = gh.request("GET", artifact["archive_download_url"], raw=True, max_bytes=4 * MAX_ARTIFACT_BYTES)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        if zf.namelist() != ["result.json"]:
            raise BadResult("unexpected files in artifact")
        with zf.open("result.json") as fh:
            raw = fh.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise BadResult("result.json too large")
    return json.loads(raw.decode("utf-8"))


def validate(result: object, challenge: Challenge, head_sha: str) -> dict:
    """Return the trusted subset of a result, or raise BadResult."""
    if not isinstance(result, dict) or not set(result) <= RESULT_KEYS:
        raise BadResult("not a result object")
    if result.get("schema") != 1:
        raise BadResult("unknown schema")
    if result.get("head_sha") != head_sha:
        raise BadResult("result is for another commit")
    status = result.get("status")
    if status not in ARTIFACT_STATUSES:
        raise BadResult("unknown status")
    if status in ("not_graded",):
        raise BadResult("grader and reporter disagree about the scope")
    if result.get("challenge") != challenge.id:
        raise BadResult("result is for another challenge")
    if status == "rejected":
        reason = result.get("reason")
        if reason not in ("not_regular_file", "file_too_large"):
            raise BadResult("unexpected rejection reason")
        return {"status": "rejected", "reason": reason}
    if status == "error":
        return {"status": "error"}

    checks = result.get("checks")
    if not isinstance(checks, list) or len(checks) != len(challenge.check_ids):
        raise BadResult("wrong number of checks")
    by_id: dict[str, tuple[str, str]] = {}
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"id", "status", "detail"}:
            raise BadResult("bad check entry")
        cid, cstatus, detail = check["id"], check["status"], check["detail"]
        if cid not in challenge.check_ids or cid in by_id or cstatus not in CHECK_STATUSES:
            raise BadResult("bad check id or status")
        if not isinstance(detail, str) or len(detail) > 2000:
            raise BadResult("bad check detail")
        by_id[cid] = (cstatus, sanitize_detail(detail))
    passed = all(by_id[c][0] == "pass" for c in challenge.check_ids)
    return {"status": "passed" if passed else "failed", "checks": by_id}


def checks_table(challenge: Challenge, by_id: dict[str, tuple[str, str]]) -> str:
    words = {"pass": "pass", "fail": "**fail**", "skip": "skipped"}
    rows = ["| Check | Result | Details |", "| --- | --- | --- |"]
    for check in challenge.checks:
        status, detail = by_id[check["id"]]
        rows.append(f"| {check['name']} | {words[status]} | {detail} |")
    return "\n".join(rows)


def comment_body(kind: str, challenge: Challenge | None, head_sha: str, run_url: str, **kw) -> str:
    title = challenge.title if challenge else "Challenge submission"
    short = head_sha[:7]
    lines = [marker(head_sha)]
    if kind == "passed":
        lines += [
            kw["record"],
            f"### {title}: passed",
            "",
            checks_table(challenge, kw["checks"]),
            "",
            f"Graded commit `{short}` with the grader from `main` ([run log]({run_url})).",
            "",
            "You passed. Submissions are never merged, so this PR is now closed. "
            f"Your pass counts on [devops-daily.com/challenges/{challenge.id}]({SITE}/{challenge.id}).",
        ]
    elif kind == "failed":
        lines += [
            f"### {title}: not passed yet",
            "",
            checks_table(challenge, kw["checks"]),
            "",
            f"Graded commit `{short}` with the grader from `main` ([run log]({run_url})).",
            "",
            "Push a new commit to this branch and the grader runs again.",
        ]
    elif kind == "rejected":
        paths = kw.get("paths") or []
        lines += [f"### {title}: not graded", "", REJECT_REASONS[kw["reason"]]]
        if paths and kw["reason"] == "several_challenges":
            lines += ["", "Challenges in this PR: " + ", ".join(f"`{safe_path(p)}`" for p in paths) + "."]
        elif paths:
            lines += ["", "Files that caused this:", ""] + [f"- `{safe_path(p)}`" for p in paths]
        if challenge:
            allowed = ", ".join(f"`challenges/{challenge.id}/{p}`" for p in challenge.solution_paths)
            lines += ["", f"A submission for this challenge may only change: {allowed}."]
        if challenge:
            lines += ["", "Change the PR so it only touches those files, push again, and the grader runs again."]
    elif kind == "already_passed":
        lines += [
            f"### {title}: already passed",
            "",
            "This PR passed before, so it is not graded again. Open a new PR to try this or another challenge.",
        ]
    elif kind == "not_graded":
        lines += [
            "### Not a challenge submission",
            "",
            "This PR does not change any challenge's solution files, so it was not graded. "
            "If it is a fix or a new challenge for this repository, a maintainer will review it. "
            "See CONTRIBUTING.md.",
        ]
    else:
        lines += [
            f"### {title}: the grader did not finish",
            "",
            f"The grader did not produce a usable result for commit `{short}` ([run log]({run_url})). "
            "This is usually a timeout or a problem on the runner. Push a new commit to try again.",
        ]
    return "\n".join(lines) + "\n"


def upsert_comment(gh: GitHub, number: int, head_sha: str, body: str, once_marker: str | None = None) -> None:
    comments = gh.paginate(f"/repos/{gh.repo}/issues/{number}/comments", limit=500)
    look_for = once_marker or marker(head_sha)
    mine = [
        c
        for c in comments
        if c["user"]["login"] == BOT_LOGIN
        and look_for in (c.get("body") or "")
        and (once_marker == ALREADY_MARKER or ALREADY_MARKER not in (c.get("body") or ""))
    ]
    if mine:
        gh.request("PATCH", f"/repos/{gh.repo}/issues/comments/{mine[-1]['id']}", body={"body": body})
    else:
        gh.request("POST", f"/repos/{gh.repo}/issues/{number}/comments", body={"body": body})


def still_current(gh: GitHub, number: int, head_sha: str) -> bool:
    pr = gh.get(f"/repos/{gh.repo}/pulls/{number}")
    return pr["state"] == "open" and pr["head"]["sha"] == head_sha


def main() -> int:
    gh = GitHub()
    env = os.environ
    head_sha = env["HEAD_SHA"]
    if env.get("RUN_EVENT") != "pull_request" or env.get("RUN_PATH") != GRADE_WORKFLOW:
        print("Not a run of the Grade workflow for a pull request; nothing to do.")
        return 0
    if not SHA_RE.match(head_sha):
        print("Unexpected head SHA; nothing to do.")
        return 0
    run_id = int(env["RUN_ID"])
    run_attempt = int(env["RUN_ATTEMPT"])
    run_url = f"https://github.com/{gh.repo}/actions/runs/{run_id}/attempts/{run_attempt}"

    pr = find_pr(gh, env["HEAD_OWNER"], env["HEAD_BRANCH"], head_sha)
    if pr is None:
        print("No open PR against main has this head commit any more; nothing to do.")
        return 0
    number = pr["number"]
    print(f"PR #{number} by {pr['user']['login']} at {head_sha}")

    challenges = load_challenges()
    pr_labels = {label["name"] for label in pr.get("labels", [])}
    if LABEL_PASSED in pr_labels:
        # Its own marker, so the notice never replaces the pass comment and
        # its pass record.
        body = comment_body("already_passed", None, head_sha, run_url).replace(marker(head_sha), ALREADY_MARKER)
        if still_current(gh, number, head_sha):
            upsert_comment(gh, number, head_sha, body, once_marker=ALREADY_MARKER)
            gh.request("PATCH", f"/repos/{gh.repo}/pulls/{number}", body={"state": "closed"})
        print("Already passed; closed without grading.")
        return 0

    scope = evaluate_scope(gh.commit_changes(head_sha), challenges)
    challenge = challenges.get(scope.challenge) if scope.challenge else None
    labels: list[str] = []
    close = False

    if scope.kind == "not_graded":
        if pr["user"].get("type") == "Bot":
            print("Not a submission, from a bot; no comment.")
            return 0
        body = comment_body("not_graded", None, head_sha, run_url)
        if still_current(gh, number, head_sha):
            upsert_comment(gh, number, head_sha, body, once_marker="### Not a challenge submission")
        print("Not a submission.")
        return 0

    if scope.kind == "rejected":
        body = comment_body("rejected", challenge, head_sha, run_url, reason=scope.reason, paths=scope.paths)
        if challenge:
            labels.append(f"challenge:{challenge.id}")
        verdict = "rejected"
    else:
        assert challenge is not None
        labels.append(f"challenge:{challenge.id}")
        try:
            raw = load_artifact(gh, run_id, run_attempt)
            if raw is None:
                raise BadResult("no artifact for this run attempt")
            trusted = validate(raw, challenge, head_sha)
            if trusted["status"] == "passed" and env.get("RUN_CONCLUSION") != "success":
                raise BadResult("a pass needs a successful grade run")
        except (BadResult, ValueError, KeyError, zipfile.BadZipFile, urllib.error.HTTPError) as exc:
            print(f"Unusable result: {exc}")
            trusted = {"status": "error"}
        verdict = trusted["status"]
        if verdict in ("passed", "failed"):
            body = comment_body(
                verdict, challenge, head_sha, run_url, checks=trusted["checks"],
                record=pass_record(challenge, head_sha, run_id, run_attempt),
            )
        elif verdict == "rejected":
            body = comment_body("rejected", challenge, head_sha, run_url, reason=trusted["reason"])
        else:
            body = comment_body("error", challenge, head_sha, run_url)
        if verdict == "passed":
            labels.append(LABEL_PASSED)
            close = True

    if not still_current(gh, number, head_sha):
        print("The PR moved on while reporting; the next run reports instead.")
        return 0
    upsert_comment(gh, number, head_sha, body)
    # Keep exactly one challenge label: the one for this commit.
    for name in sorted(pr_labels):
        if name.startswith(CHALLENGE_LABEL) and name not in labels:
            gh.request("DELETE", f"/repos/{gh.repo}/issues/{number}/labels/{urllib.parse.quote(name, safe='')}")
    if labels:
        gh.request("POST", f"/repos/{gh.repo}/issues/{number}/labels", body={"labels": labels})
    if close:
        gh.request("PATCH", f"/repos/{gh.repo}/pulls/{number}", body={"state": "closed"})
    print(f"Reported: {verdict}; labels: {', '.join(labels) or 'none'}; closed: {close}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
