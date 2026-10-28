"""Grade speed-up-the-queries against a throwaway PostgreSQL server.

  grade_db.py run WORK_DIR      run every step and print the results as JSON
  grade_db.py migrate FILE      (internal) run indexes.sql as the app role

The connection comes from PGHOST, PGPORT and PGPASSWORD (the superuser's).
The order of the steps is what keeps the grade honest:

1. The superuser creates the empty tables from schema.sql (from main), owned
   by the "app" role.
2. indexes.sql runs as app, on the empty tables. app may create objects in
   the shop schema only for this step, because CREATE INDEX needs it.
3. The superuser takes that right away again, resets app's role settings,
   and reads the catalogs (with only pg_catalog on its search path): the
   tables, columns and constraints must be as schema.sql made them, and
   nothing but indexes may be new. So no
   function, operator, view, trigger, rule or policy of the submission's
   exists when anything else runs.
4. Only then does app load the random data. The migration never saw it, so
   it could not prepare answers.
5. The "reader" role picks random parameters, computes the expected rows with
   the original queries, and runs the submitted queries. reader may only read
   the two tables (and keep temporary tables), each run gets a new session
   and a statement timeout, and a query is always sent with its parameter,
   so the extended protocol carries exactly one statement. The rows that are
   checked and the pages that are counted come from the same execution. The
   parameters and expected rows only exist in this process.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.numeric import Int8

# grade.sh runs this file with python -I, which leaves its folder off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlcheck import plain_query, plan_pages, seq_scans  # noqa: E402

DATABASE = "shop"
TABLE_COLUMNS = {
    "customers": ["id", "email", "name", "created_at"],
    "orders": ["id", "customer_id", "status", "total_cents", "created_at"],
}
MIGRATE_SECONDS = 120
QUERY_TIMEOUT_MS = 15000
LOAD_TIMEOUT_MS = 600000

# The queries as the app sends them today. The submission may rewrite the
# files in queries/, but each must return exactly these rows.
QUERIES = {
    "orders-for-customer": {
        "file": "queries/orders_for_customer.sql",
        "max_pages": 60,
        "ordered": True,
        "reference": (
            "SELECT id, status, total_cents, created_at FROM orders "
            "WHERE customer_id = $1 ORDER BY created_at DESC, id DESC LIMIT 20"
        ),
    },
    "find-customer": {
        "file": "queries/find_customer.sql",
        "max_pages": 20,
        "ordered": False,
        "reference": "SELECT id, email, name FROM customers WHERE lower(email) = lower($1)",
    },
    "revenue-for-day": {
        "file": "queries/revenue_for_day.sql",
        "max_pages": 200,
        "ordered": False,
        "reference": (
            "SELECT count(*) AS orders, coalesce(sum(total_cents), 0) AS revenue_cents FROM orders "
            "WHERE created_at::date = $1 AND status = 'paid'"
        ),
    },
}

RNG = secrets.SystemRandom()


def connect(user: str, password: str, *, dbname: str = DATABASE, search_path: str = "shop",
            read_only: bool = False, timeout_ms: int = QUERY_TIMEOUT_MS) -> psycopg.Connection:
    # Options sent at connect time win over any default set with ALTER ROLE.
    options = f"-c TimeZone=UTC -c search_path={search_path} -c statement_timeout={timeout_ms}"
    if read_only:
        options += " -c default_transaction_read_only=on"
    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        user=user,
        password=password,
        dbname=dbname,
        options=options,
        connect_timeout=10,
        autocommit=True,
    )


def superuser(dbname: str = DATABASE) -> psycopg.Connection:
    # Only pg_catalog on the search path: no name in these sessions can
    # resolve to an object the submission made.
    return connect("postgres", os.environ["PGPASSWORD"], dbname=dbname, search_path="pg_catalog",
                   timeout_ms=LOAD_TIMEOUT_MS)


# --- 1. empty tables -------------------------------------------------------------------


def wait_ready(seconds: int = 90) -> None:
    """Wait for the server. While the image runs initdb, it only listens on its socket."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            with superuser("postgres") as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def setup(work: Path, passwords: dict[str, str]) -> None:
    with superuser("postgres") as conn:
        for role in ("app", "reader"):
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(role), sql.Literal(passwords[role])))
        conn.execute("CREATE DATABASE shop")
    with superuser() as conn:
        # Nobody else may create schemas, objects in public or temporary objects.
        conn.execute("REVOKE ALL ON DATABASE shop FROM PUBLIC")
        conn.execute("GRANT CONNECT ON DATABASE shop TO app, reader")
        # reader keeps each query's rows in a temporary table (see check_query).
        conn.execute("GRANT TEMPORARY ON DATABASE shop TO reader")
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute("CREATE SCHEMA shop")
        conn.execute("GRANT USAGE ON SCHEMA shop TO app, reader")
        conn.execute("SET search_path = shop")
        conn.execute((work / "schema.sql").read_text(encoding="utf-8"))  # trusted, from main
        conn.execute("ALTER TABLE shop.customers OWNER TO app")
        conn.execute("ALTER TABLE shop.orders OWNER TO app")
        conn.execute("GRANT CREATE ON SCHEMA shop TO app")


def structure(conn: psycopg.Connection) -> dict:
    """Tables, columns and constraints of the shop schema, read as raw catalog rows."""
    ns = conn.execute("SELECT oid FROM pg_namespace WHERE nspname = 'shop'").fetchone()[0]
    tables = conn.execute(
        "SELECT relname, relpersistence, reloptions, relacl FROM pg_class WHERE relnamespace = %s AND relkind = 'r' "
        "ORDER BY 1", [ns]).fetchall()
    columns = conn.execute(
        "SELECT c.relname, a.attnum, a.attname, a.atttypid, a.atttypmod, a.attnotnull, a.atthasdef, a.attgenerated, "
        "a.attstattarget, a.attoptions, a.attcollation, a.attacl "
        "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
        "WHERE c.relnamespace = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY c.relname, a.attnum", [ns]).fetchall()
    constraints = conn.execute(
        "SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid) "
        "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
        "WHERE c.relnamespace = %s ORDER BY 1, 2", [ns]).fetchall()
    return {"tables": [list(r) for r in tables], "columns": [list(r) for r in columns],
            "constraints": [list(r) for r in constraints]}


def leftovers(conn: psycopg.Connection) -> list[str]:
    """Everything in the shop schema that is not one of the two tables or an index."""
    ns = conn.execute("SELECT oid FROM pg_namespace WHERE nspname = 'shop'").fetchone()[0]
    tables = [r[0] for r in conn.execute(
        "SELECT oid FROM pg_class WHERE relnamespace = %s AND relkind = 'r' AND relname = ANY(%s)",
        [ns, list(TABLE_COLUMNS)]).fetchall()]
    found: list[str] = []
    for kind, name in conn.execute(
            "SELECT relkind, relname FROM pg_class WHERE relnamespace = %s AND NOT (oid = ANY(%s)) AND relkind <> 'i'",
            [ns, tables]).fetchall():
        found.append(f"a relation ({kind}) named {name}")
    checks = [
        ("a function or procedure", "SELECT proname FROM pg_proc WHERE pronamespace = %s", [ns]),
        ("an operator", "SELECT oprname FROM pg_operator WHERE oprnamespace = %s", [ns]),
        ("a collation", "SELECT collname FROM pg_collation WHERE collnamespace = %s", [ns]),
        ("a conversion", "SELECT conname FROM pg_conversion WHERE connamespace = %s", [ns]),
        ("a text search configuration", "SELECT cfgname FROM pg_ts_config WHERE cfgnamespace = %s", [ns]),
        ("a text search dictionary", "SELECT dictname FROM pg_ts_dict WHERE dictnamespace = %s", [ns]),
        ("a trigger", "SELECT tgname FROM pg_trigger WHERE tgrelid = ANY(%s) AND NOT tgisinternal", [tables]),
        ("a rule", "SELECT rulename FROM pg_rewrite WHERE ev_class = ANY(%s)", [tables]),
        ("a row security policy", "SELECT polname FROM pg_policy WHERE polrelid = ANY(%s)", [tables]),
        ("row level security", "SELECT relname FROM pg_class WHERE oid = ANY(%s) AND (relrowsecurity OR relforcerowsecurity)", [tables]),
        ("a large object", "SELECT oid FROM pg_largeobject_metadata WHERE lomowner = (SELECT oid FROM pg_roles WHERE rolname = 'app')", []),
        ("a statistics object", "SELECT stxname FROM pg_statistic_ext WHERE stxnamespace = %s", [ns]),
    ]
    for label, query, args in checks:
        for (name,) in conn.execute(query, args).fetchall():
            found.append(f"{label} named {name}")
    # Types that belong to a relation were reported with it, and array types
    # with their element type. That leaves types made with CREATE TYPE or
    # CREATE DOMAIN.
    for (name,) in conn.execute(
            "SELECT typname FROM pg_type WHERE typnamespace = %s AND typrelid = 0 AND typcategory <> 'A'",
            [ns]).fetchall():
        found.append(f"a type named {name}")
    return found


# --- 2. the migration (in its own process, so a hang can be killed) --------------------------


def migrate(path: str) -> int:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(json.dumps({"ok": False, "error": "indexes.sql is not UTF-8 text"}))
        return 0
    if not re.sub(r"--[^\n]*|\s", "", text):
        print(json.dumps({"ok": True}))
        return 0
    try:
        with connect("app", os.environ["APP_PASSWORD"], timeout_ms=MIGRATE_SECONDS * 1000) as conn:
            # No parameters, so the simple protocol: the file may hold several
            # statements, and they run as one transaction.
            conn.execute(text)
    except psycopg.Error as exc:
        diag = exc.diag
        print(json.dumps({"ok": False, "error": f"{diag.sqlstate or ''} {diag.message_primary or str(exc)}"[:200]}))
        return 0
    print(json.dumps({"ok": True}))
    return 0


# --- 4. data, parameters and expected rows ----------------------------------------------------


def load(work: Path, password: str) -> None:
    with connect("app", password, timeout_ms=LOAD_TIMEOUT_MS) as conn:
        conn.execute("SELECT setseed(%s)", [RNG.uniform(-1, 1)])
        conn.execute((work / "seed.sql").read_text(encoding="utf-8"))  # trusted, from main
        conn.execute("ANALYZE customers, orders")


def bind(param):
    # Customer IDs go to the server as bigint, like the column.
    return Int8(param) if isinstance(param, int) else param


def run_query(conn: psycopg.Connection, query: str, param) -> tuple[list[str], list[tuple]]:
    cur = psycopg.RawCursor(conn)
    # Always with a parameter: that keeps the extended protocol, one statement.
    cur.execute(query, [bind(param)], prepare=False)
    names = [d.name for d in cur.description or []]
    return names, cur.fetchall()


def pick_params(conn: psycopg.Connection) -> dict[str, list]:
    small = []
    while len(small) < 2:
        cid = RNG.randint(106, 50000)
        if cid not in small and conn.execute("SELECT EXISTS (SELECT 1 FROM orders WHERE customer_id = %s)",
                                             [Int8(cid)]).fetchone()[0]:
            small.append(cid)
    # A customer with two orders at the same time among the 20 newest, so the
    # order of ties (id DESC) counts too.
    tied = [r[0] for r in conn.execute(
        "SELECT DISTINCT customer_id FROM ("
        "  SELECT customer_id, created_at,"
        "         row_number() OVER (PARTITION BY customer_id ORDER BY created_at DESC, id DESC) AS n"
        "  FROM orders) AS newest "
        "WHERE n <= 20 GROUP BY customer_id, created_at HAVING count(*) > 1").fetchall()]
    customers = [RNG.randint(1, 5), *RNG.sample(range(6, 106), 2), *small]
    if tied:
        customers.append(RNG.choice(tied))
    RNG.shuffle(customers)

    def any_case(email: str) -> str:
        return "".join(ch.upper() if RNG.random() < 0.5 else ch.lower() for ch in email)

    emails = []
    for cid in RNG.sample(range(1, 50001), 3):
        emails.append(any_case(conn.execute("SELECT email FROM customers WHERE id = %s", [Int8(cid)]).fetchone()[0]))
    # An address two customers share, in different case: both must come back.
    shared = [r[0] for r in conn.execute("SELECT lower(email) FROM customers GROUP BY 1 HAVING count(*) > 1").fetchall()]
    emails.append(any_case(RNG.choice(shared)))
    emails.append(f"nobody.{secrets.token_hex(4)}@example.com")
    RNG.shuffle(emails)

    days = [date(2026, 1, 1) + timedelta(days=RNG.randrange(365)) for _ in range(3)]
    days.append(date(2025, 1, 1) + timedelta(days=RNG.randrange(365)))
    RNG.shuffle(days)
    return {"orders-for-customer": customers, "find-customer": emails, "revenue-for-day": days}


# --- 5. the submitted queries -----------------------------------------------------------------


def shown(param) -> str:
    """A parameter for a PR comment, which drops characters such as @."""
    return str(param).replace("@", " at ")[:60]


def check_query(work: Path, spec: dict, params: list, expected: dict, password: str) -> tuple[str, str]:
    path = work / spec["file"]
    if not path.is_file():
        return "fail", f"{spec['file']} is missing."
    try:
        query = plain_query(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        query = None
    if query is None:
        return "fail", f"{spec['file']} must hold one SELECT (or WITH) statement, with no semicolon inside it."

    worst_pages, worst_param, scans = -1, None, set()
    for param in params:
        # One execution gives both the rows and the page count: EXPLAIN
        # ANALYZE runs the query once and keeps its rows in a temporary table.
        # A query cannot behave one way when its rows are checked and another
        # way when its pages are counted.
        table = f"grader_{secrets.token_hex(6)}"
        try:
            with connect("reader", password) as conn:
                cur = psycopg.RawCursor(conn)
                cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) CREATE TEMPORARY TABLE {table} AS " + query,
                            [bind(param)], prepare=False)
                doc = cur.fetchone()[0]
                cur.execute(f"SELECT * FROM pg_temp.{table}", None)
                names = [d.name for d in cur.description or []]
                rows = cur.fetchall()
        except psycopg.Error as exc:
            return "fail", f"The query failed for {shown(param)}: {(exc.diag.message_primary or str(exc))[:150]}"
        want_names, want_rows = expected[str(param)]
        if names != want_names:
            return "fail", f"The query returns the columns {', '.join(names)[:100]}; the app expects {', '.join(want_names)}."
        if not spec["ordered"]:
            rows, want_rows = sorted(rows, key=repr), sorted(want_rows, key=repr)
        if rows != want_rows:
            return "fail", (f"The query returns different rows than the original query for {shown(param)} "
                            f"({len(rows)} rows, expected {len(want_rows)}).")
        plan = doc[0].get("Plan", {}) if isinstance(doc, list) and doc and isinstance(doc[0], dict) else {}
        pages = plan_pages(plan)
        scans.update(seq_scans(plan))
        if pages > worst_pages:
            worst_pages, worst_param = pages, param
    limit = spec["max_pages"]
    if worst_pages > limit or scans:
        how = f" with a sequential scan on {', '.join(sorted(scans))}" if scans else ""
        return "fail", (f"Right rows for {len(params)} random values, but the query read {worst_pages:,} pages{how} "
                        f"for {shown(worst_param)}. The limit is {limit} pages.")
    return "pass", f"Right rows for {len(params)} random values, reading at most {worst_pages} pages (limit {limit})."


# --- all steps ------------------------------------------------------------------------------------


def skip_queries(results: dict, reason: str) -> dict:
    for check in QUERIES:
        results[check] = {"status": "skip", "detail": reason}
    return results


def run(work: Path) -> dict:
    passwords = {"app": secrets.token_hex(16), "reader": secrets.token_hex(16)}
    wait_ready()
    setup(work, passwords)
    with superuser() as conn:
        before = structure(conn)

    results: dict[str, dict] = {}
    indexes = work / "indexes.sql"
    outcome = {"ok": False, "error": "indexes.sql is missing"}
    if indexes.is_file():
        env = {**os.environ, "APP_PASSWORD": passwords["app"]}
        try:
            proc = subprocess.run([sys.executable, "-I", __file__, "migrate", str(indexes)], env=env,
                                  capture_output=True, text=True, timeout=MIGRATE_SECONDS + 10)
            lines = proc.stdout.strip().splitlines()
            outcome = json.loads(lines[-1]) if lines else {"ok": False, "error": "no result"}
        except subprocess.TimeoutExpired:
            outcome = {"ok": False, "error": f"did not finish within {MIGRATE_SECONDS} seconds", "timeout": True}

    with superuser() as conn:
        conn.execute("REVOKE CREATE ON SCHEMA shop FROM app")
        conn.execute("ALTER ROLE app RESET ALL")
        # A killed migration's session may still be running on the server.
        # End it, and wait until it is gone, before reading the catalogs.
        conn.execute("SELECT pg_terminate_backend(pid, 10000) FROM pg_stat_activity WHERE usename = 'app'")
        if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = 'app'").fetchone()[0]:
            raise SystemExit("the migration's session did not end")
        found = leftovers(conn)
        changed = structure(conn) != before
    if outcome.get("timeout"):
        results["indexes"] = {"status": "fail", "detail": f"indexes.sql {outcome['error']}."}
        return skip_queries(results, "Skipped: indexes.sql did not finish.")
    if found or changed:
        what = "; ".join(found[:3]) if found else "changes to the tables (columns, constraints or table settings)"
        results["indexes"] = {"status": "fail", "detail": f"indexes.sql may only create or drop indexes. It left: {what}."}
        return skip_queries(results, "Skipped: indexes.sql made more than indexes.")
    if outcome["ok"]:
        results["indexes"] = {"status": "pass", "detail": "indexes.sql ran on the empty tables and left only indexes."}
    else:
        results["indexes"] = {"status": "fail", "detail": f"indexes.sql failed: {outcome['error']}"}

    try:
        load(work, passwords["app"])
    except psycopg.Error as exc:
        # Only the submitted indexes can make the trusted data fail to load,
        # for example a unique index on a column with repeated values.
        reason = (exc.diag.message_primary or str(exc))[:150]
        results["indexes"] = {"status": "fail", "detail": f"With these indexes, the test data does not load: {reason}"}
        return skip_queries(results, "Skipped: the test data did not load.")
    with superuser() as conn:
        # Whatever the migration granted, reader may only read the tables.
        conn.execute("REVOKE ALL ON shop.customers, shop.orders FROM PUBLIC, reader")
        conn.execute("GRANT SELECT ON shop.customers, shop.orders TO reader")
    with connect("reader", passwords["reader"], read_only=True, timeout_ms=LOAD_TIMEOUT_MS) as conn:
        params = pick_params(conn)
        expected = {
            check: {str(p): run_query(conn, spec["reference"], p) for p in params[check]}
            for check, spec in QUERIES.items()
        }

    for check, spec in QUERIES.items():
        status, detail = check_query(work, spec, params[check], expected[check], passwords["reader"])
        results[check] = {"status": status, "detail": detail}
    return results


def main() -> int:
    cmd = sys.argv[1]
    if cmd == "migrate":
        return migrate(sys.argv[2])
    if cmd == "run":
        print(json.dumps(run(Path(sys.argv[2]))))
        return 0
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main())
