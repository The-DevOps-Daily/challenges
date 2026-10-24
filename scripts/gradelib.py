"""Shared code for the grader: challenge metadata, PR scope rules and GitHub API calls.

Everything here runs from the main branch. Nothing in this module executes or
imports code from a pull request.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHALLENGES_DIR = ROOT / "challenges"

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

MAX_CHANGED_FILES = 20
MAX_FILE_BYTES = 64 * 1024
MAX_DETAIL_CHARS = 300

CHECK_STATUSES = ("pass", "fail", "skip")
RESULT_STATUSES = ("passed", "failed", "rejected", "error")

# Reasons the grader can reject a PR before grading it. The report workflow
# maps these codes to fixed messages, so free text from a result never
# reaches a comment as a rejection reason.
REJECT_REASONS = {
    "outside_paths": "This PR changes files outside the solution files of the challenge.",
    "several_challenges": "This PR changes the solution files of more than one challenge. Open one PR per challenge.",
    "too_many_files": f"This PR changes more than {MAX_CHANGED_FILES} files.",
    "not_regular_file": "A solution file is a symlink or a submodule. Solution files must be regular files.",
    "file_too_large": f"A solution file is larger than {MAX_FILE_BYTES // 1024} KB.",
    "bad_path": "A changed path is not a plain relative path.",
    "base_not_main": "This PR does not target the main branch.",
}


@dataclass
class Challenge:
    id: str
    title: str
    solution_paths: list[str]
    checks: list[dict]
    meta: dict = field(repr=False)

    @property
    def check_ids(self) -> list[str]:
        return [c["id"] for c in self.checks]

    def owns(self, repo_path: str) -> bool:
        """True when repo_path is one of this challenge's solution files."""
        prefix = f"challenges/{self.id}/"
        if not repo_path.startswith(prefix):
            return False
        rel = repo_path[len(prefix):]
        return any(match_path(pattern, rel) for pattern in self.solution_paths)


def match_path(pattern: str, path: str) -> bool:
    """Match a relative path against a pattern where * never crosses a /."""
    pat_parts = pattern.split("/")
    path_parts = path.split("/")
    if len(pat_parts) != len(path_parts):
        return False
    return all(fnmatch.fnmatchcase(p, q) for p, q in zip(path_parts, pat_parts))


def load_challenges(base: Path = CHALLENGES_DIR) -> dict[str, Challenge]:
    challenges: dict[str, Challenge] = {}
    for meta_file in sorted(base.glob("*/challenge.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        cid = meta["id"]
        if cid != meta_file.parent.name or not ID_RE.match(cid):
            raise ValueError(f"{meta_file}: id must match the folder name")
        checks = meta["checks"]
        ids = [c["id"] for c in checks]
        if len(ids) != len(set(ids)) or not all(ID_RE.match(i) for i in ids):
            raise ValueError(f"{meta_file}: check ids must be unique slugs")
        for pattern in meta["solution_paths"]:
            if pattern.startswith("/") or ".." in pattern.split("/") or pattern.startswith("grade/"):
                raise ValueError(f"{meta_file}: bad solution path {pattern!r}")
        challenges[cid] = Challenge(
            id=cid,
            title=meta["title"],
            solution_paths=list(meta["solution_paths"]),
            checks=checks,
            meta=meta,
        )
    return challenges


@dataclass
class FileChange:
    path: str
    status: str
    previous_path: str | None = None

    def paths(self) -> list[str]:
        return [p for p in (self.path, self.previous_path) if p]


@dataclass
class Scope:
    kind: str  # "graded", "rejected" or "not_graded"
    challenge: str | None = None
    reason: str | None = None
    paths: list[str] = field(default_factory=list)


def safe_repo_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def evaluate_scope(changes: list[FileChange], challenges: dict[str, Challenge]) -> Scope:
    """Decide whether a PR is a valid submission for exactly one challenge."""
    if not changes:
        return Scope("not_graded")
    if len(changes) > MAX_CHANGED_FILES:
        return Scope("rejected", reason="too_many_files")

    touched: set[str] = set()
    outside: list[str] = []
    for change in changes:
        for path in change.paths():
            if not safe_repo_path(path):
                return Scope("rejected", reason="bad_path")
            owner = next((c.id for c in challenges.values() if c.owns(path)), None)
            if owner:
                touched.add(owner)
            else:
                outside.append(path)

    if not touched:
        return Scope("not_graded", paths=sorted(set(outside))[:10])
    if outside:
        cid = next(iter(touched)) if len(touched) == 1 else None
        return Scope("rejected", challenge=cid, reason="outside_paths", paths=sorted(set(outside))[:10])
    if len(touched) > 1:
        return Scope("rejected", reason="several_challenges", paths=sorted(touched))
    return Scope("graded", challenge=touched.pop())


def sanitize_detail(text: str) -> str:
    """Reduce a check detail to plain text that cannot ping, link or format."""
    text = " ".join(str(text).split())
    text = re.sub(r"[^A-Za-z0-9 .,:;'\"=/()%+-]", "", text)
    text = text.replace("://", ": //").replace("www.", "www .")
    if len(text) > MAX_DETAIL_CHARS:
        text = text[: MAX_DETAIL_CHARS - 3].rstrip() + "..."
    return text


class GitHub:
    """Minimal GitHub REST client using only the standard library."""

    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token if token is not None else os.environ.get("GH_TOKEN", "")
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]
        self.api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def request(self, method: str, path: str, body=None, accept: str = "application/vnd.github+json",
                max_bytes: int = 5 * 1024 * 1024, raw: bool = False):
        url = path if path.startswith("https://") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", accept)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "devops-daily-challenges-grader")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            # Unredirected, so the token is never sent on to a redirect target
            # such as artifact blob storage.
            req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            body_bytes = resp.read(max_bytes + 1)
            if len(body_bytes) > max_bytes:
                raise ValueError(f"response from {path} is larger than {max_bytes} bytes")
            link = resp.headers.get("Link", "")
        if raw:
            return body_bytes, link
        return (json.loads(body_bytes) if body_bytes else None), link

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)[0]

    def paginate(self, path: str, limit: int = 3000) -> list:
        items: list = []
        sep = "&" if "?" in path else "?"
        url = f"{path}{sep}per_page=100"
        while url and len(items) < limit:
            page, link = self.request("GET", url)
            items.extend(page)
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part[part.find("<") + 1 : part.find(">")]
        return items[:limit]

    def commit_changes(self, head_sha: str, base: str = "main") -> list[FileChange]:
        """Files a commit changes compared with its merge base with main.

        Scope decisions use this rather than the PR's live file list: a SHA
        names one fixed tree, while a PR can be force-pushed between two API
        calls.
        """
        if not SHA_RE.match(head_sha):
            raise ValueError("unexpected head SHA")
        data = self.get(f"/repos/{self.repo}/compare/{base}...{head_sha}?per_page=100")
        files = data.get("files") or []
        if len(files) > MAX_CHANGED_FILES or len(files) >= 300:
            # Too many to grade; the exact list does not matter past this point.
            files = files[: MAX_CHANGED_FILES + 1]
        return [
            FileChange(path=f["filename"], status=f["status"], previous_path=f.get("previous_filename"))
            for f in files
        ]


def quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")

