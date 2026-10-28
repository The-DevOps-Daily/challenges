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


def migration_problem(text: str) -> str | None:
    """Why indexes.sql may not run, or None when it holds only CREATE INDEX and DROP INDEX.

    The file is split into statements by PostgreSQL's own parser (pglast,
    the same version as the server), so a statement cannot hide in a
    comment, a string or a dollar-quoted body.
    """
    from pglast import parse_sql  # here, so the other checks need no pglast
    from pglast.ast import DropStmt, IndexStmt
    from pglast.enums.parsenodes import ObjectType

    try:
        statements = parse_sql(text)
    except Exception as exc:  # noqa: BLE001 - a parse error, or a tree too deep to convert
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return f"indexes.sql is not valid SQL: {message[:120]}"
    for n, raw in enumerate(statements, start=1):
        stmt = raw.stmt
        if isinstance(stmt, IndexStmt):
            continue
        if isinstance(stmt, DropStmt) and stmt.removeType == ObjectType.OBJECT_INDEX:
            continue
        start = raw.stmt_location or 0
        words = re.findall(r"[A-Za-z]+", text[start:start + (raw.stmt_len or len(text))])[:2]
        what = " ".join(words).upper() or type(stmt).__name__
        return f"indexes.sql may only hold CREATE INDEX and DROP INDEX statements. Statement {n} is {what}."
    return None


def split_statements(text: str) -> list[str]:
    """The statements of a file, cut where PostgreSQL's parser says they start and end."""
    from pglast import parse_sql

    out = []
    for raw in parse_sql(text):
        start = raw.stmt_location or 0
        out.append(text[start:start + raw.stmt_len] if raw.stmt_len else text[start:])
    return out


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
