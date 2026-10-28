"""Checks on a submitted query and its plan that need no database.

Used by grade_db.py, and by the unit tests in tests/.
"""

from __future__ import annotations

import re

TABLES = ("customers", "orders")
MAX_QUERY_CHARS = 4000


def skip_comments(query: str) -> str:
    """The query without its leading spaces and comments. Block comments nest in PostgreSQL."""
    rest = query
    while True:
        rest = rest.lstrip()
        if rest.startswith("--"):
            end = rest.find("\n")
            rest = "" if end < 0 else rest[end + 1:]
        elif rest.startswith("/*"):
            depth, i = 1, 2
            while depth and i < len(rest):
                if rest.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif rest.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            rest = rest[i:]
        else:
            return rest


def plain_query(text: str) -> str | None:
    """The query as written, or None when it does not start like a SELECT.

    PostgreSQL does the real checking: the query is sent with its parameter
    over the extended protocol, which refuses a second statement, inside
    CREATE TABLE ... AS, which takes only a query, as a role that may only
    read. This gives a clear message for the common mistakes first.
    """
    if not text.strip() or len(text) > MAX_QUERY_CHARS or "\0" in text:
        return None
    first = re.match(r"[A-Za-z]+", skip_comments(text).lstrip("(").lstrip())
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
