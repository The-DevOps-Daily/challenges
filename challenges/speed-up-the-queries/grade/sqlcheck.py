"""Checks on a submitted query and its plan that need no database.

Used by grade_db.py, and by the unit tests in tests/.
"""

from __future__ import annotations

import re

TABLES = ("customers", "orders")
MAX_QUERY_CHARS = 4000


def without_comments(query: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", re.sub(r"--[^\n]*", " ", query), flags=re.S)


def plain_query(text: str) -> str | None:
    """The query as written, or None when it is not one SELECT (or WITH) statement.

    It may end with one semicolon and comments. The extended protocol already
    refuses a second statement, and the reader role can only read. This gives
    a clear message first.
    """
    if len(text) > MAX_QUERY_CHARS or "\0" in text:
        return None
    bare = without_comments(text).strip()
    if bare.endswith(";"):
        bare = bare[:-1].rstrip()
    bare = bare.lstrip("(").lstrip()
    if not bare or ";" in bare:
        return None
    first = re.match(r"[A-Za-z]+", bare)
    return text.strip() if first and first.group(0).upper() in ("SELECT", "WITH") else None


def plan_pages(plan: dict) -> int:
    """8 kB pages the query touched: shared, local and temporary, read or hit."""
    keys = ("Shared Hit Blocks", "Shared Read Blocks", "Local Hit Blocks", "Local Read Blocks",
            "Temp Read Blocks", "Temp Written Blocks")
    return sum(int(plan.get(k) or 0) for k in keys)


def seq_scans(plan: dict) -> list[str]:
    found = []
    if plan.get("Node Type") == "Seq Scan" and plan.get("Relation Name") in TABLES:
        found.append(plan["Relation Name"])
    for child in plan.get("Plans") or []:
        found += seq_scans(child)
    return found
