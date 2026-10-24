"""Find the challenge a PR is for and build a fresh copy of it with the PR's solution files.

Runs in the Grade workflow from the main branch. It reads the PR through the
GitHub API only: the PR is never checked out, and only the challenge's
allowed solution files are copied out of it, into a copy of the challenge
taken from main.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from gradelib import (
    CHALLENGES_DIR,
    MAX_FILE_BYTES,
    SHA_RE,
    GitHub,
    Scope,
    evaluate_scope,
    load_challenges,
    quote_path,
)

REGULAR_MODES = {"100644", "100755"}


def write_scope(out_dir: Path, scope: Scope, head_sha: str) -> None:
    data = {
        "kind": scope.kind,
        "challenge": scope.challenge,
        "reason": scope.reason,
        "head_sha": head_sha,
    }
    (out_dir / "scope.json").write_text(json.dumps(data), encoding="utf-8")


def set_output(name: str, value: str) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


def copy_challenge(cid: str, work: Path) -> None:
    src = CHALLENGES_DIR / cid
    shutil.copytree(src, work, symlinks=False, ignore=shutil.ignore_patterns("grade"))


def main() -> int:
    gh = GitHub()
    pr_number = int(os.environ["PR_NUMBER"])
    head_sha = os.environ["HEAD_SHA"]
    base_ref = os.environ["BASE_REF"]
    out_dir = Path(os.environ["RUNNER_TEMP"]) / "grade"
    work = out_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not SHA_RE.match(head_sha):
        raise SystemExit("unexpected head SHA")

    set_output("graded", "false")
    challenges = load_challenges()

    all_changes = gh.pr_changes(pr_number)
    if base_ref != "main":
        scope = Scope("rejected", reason="base_not_main")
    else:
        scope = evaluate_scope(all_changes, challenges)

    if scope.kind != "graded":
        write_scope(out_dir, scope, head_sha)
        print(f"Not grading: {scope.kind} ({scope.reason or 'no solution files'})")
        for path in scope.paths:
            print(f"  {path}")
        return 0

    cid = scope.challenge
    assert cid is not None
    challenge = challenges[cid]
    changes = [c for c in all_changes if challenge.owns(c.path)]

    tree = gh.get(f"/repos/{gh.repo}/git/trees/{head_sha}?recursive=1")
    if tree.get("truncated"):
        raise SystemExit("the PR tree is too large to inspect")
    entries = {e["path"]: e for e in tree["tree"]}

    copy_challenge(cid, work)
    prefix = f"challenges/{cid}/"

    for change in changes:
        rel = change.path[len(prefix):]
        dest = work / rel
        if change.status == "renamed" and change.previous_path:
            old = work / change.previous_path[len(prefix):]
            if old.is_file():
                old.unlink()
        if change.status == "removed":
            if dest.is_file():
                dest.unlink()
            continue
        entry = entries.get(change.path)
        if entry is None:
            raise SystemExit(f"{change.path} is missing from the PR tree")
        if entry["type"] != "blob" or entry["mode"] not in REGULAR_MODES:
            scope = Scope("rejected", challenge=cid, reason="not_regular_file", paths=[change.path])
            break
        if int(entry.get("size", 0)) > MAX_FILE_BYTES:
            scope = Scope("rejected", challenge=cid, reason="file_too_large", paths=[change.path])
            break
        blob, _ = gh.request(
            "GET",
            f"/repos/{gh.repo}/git/blobs/{entry['sha']}",
            accept="application/vnd.github.raw+json",
            max_bytes=MAX_FILE_BYTES,
            raw=True,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise SystemExit(f"refusing to overwrite {quote_path(rel)}")
        dest.write_bytes(blob)
        dest.chmod(0o644)
        print(f"Using {change.path} from the PR ({len(blob)} bytes)")

    write_scope(out_dir, scope, head_sha)
    if scope.kind != "graded":
        print(f"Not grading: {scope.kind} ({scope.reason})")
        shutil.rmtree(work, ignore_errors=True)
        return 0

    set_output("graded", "true")
    set_output("challenge", cid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
