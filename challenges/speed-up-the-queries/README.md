# Speed up the slow queries

PostgreSQL · Intermediate · about 45 minutes

## The situation

The shop's database passed 400,000 orders this year, and three pages got slow at the same time. The order history of a business customer times out, logging in takes a second, and the finance dashboard spins. Each of the three queries behind them reads a whole table on every call.

The database team left a note on the ticket: "Add indexes, but not one on every column. And one of these queries cannot use an index the way it is written."

## The database

`schema.sql` creates two tables:

- `customers (id, email, name, created_at)`, with an index on `email`,
- `orders (id, customer_id, status, total_cents, created_at)`, with an index on `created_at`, and more statistics on `customer_id` than PostgreSQL keeps by default.

`seed.sql` fills them with test data like production's: 50,000 customers and about 408,000 orders placed during 2026. Customers 1 to 5 are large businesses with about 24,000 orders each, customers 6 to 105 are regular businesses with about 800 each, and everyone else has a handful. Most orders are stored in the order they were placed, but 2% came in later from an import, in batches of orders with the same timestamp, so a higher ID is not always a newer order. People typed their email addresses in any mix of upper and lower case, and a few addresses belong to two customers who typed them differently.

## The queries

| File | Used by | `$1` |
| --- | --- | --- |
| `queries/orders_for_customer.sql` | The order history page: the 20 newest orders of one customer. | a customer ID (`bigint`) |
| `queries/find_customer.sql` | Log in and password reset: find a customer by email, in any case. | an email address (`text`) |
| `queries/revenue_for_day.sql` | The finance dashboard: paid orders and revenue for one day, in UTC. | a day (`date`) |

## Your task

Make each query read only a few pages of the database (8 kB blocks), and return exactly the same rows as it does today.

- Put indexes in `indexes.sql`. It runs once, as the app's database user, right after `schema.sql`, while the tables are still empty. It may hold only `CREATE INDEX` and `DROP INDEX` statements (and comments): no new tables, columns, views, functions, triggers or other objects, no rows, and no other changes to the tables.
- You may rewrite the queries. Each must stay one `SELECT` statement that takes the same `$1`, and returns the same columns and the same rows in the same order as the original query, for any data.

The limits: 60 pages for the order history, 20 for the email lookup and 200 for the revenue of a day. For comparison, reading all of `orders` is about 3,400 pages, and all of `customers` about 560.

## What the grader checks

The grader starts PostgreSQL 18.6 in a container with no network. It runs `schema.sql`, then your `indexes.sql` on the empty tables, then loads data from `seed.sql` with a new random seed. For each query it picks random parameters, compares the rows with what the original query returns, and runs `EXPLAIN (ANALYZE, BUFFERS)`: the pages the query touched (shared buffers hit or read, plus temporary files) must stay under the limit, with no sequential scan on `customers` or `orders`. The rows it compares and the pages it counts come from the same run of the query. The queries run as a database user that can only read the two tables, each in a new session.

| Check | Passes when |
| --- | --- |
| The migration runs | `indexes.sql` holds only `CREATE INDEX` and `DROP INDEX` statements, runs without errors as the app's database user, on the empty tables, within 2 minutes, and leaves only indexes behind. The tables, columns and constraints stay as they are, the tables stay empty, and there are no new functions, views, triggers or other objects. |
| Order history | For random customers (large, regular, small, and one with two orders at the same time among its newest), the query returns the same rows in the same order as the original, and reads at most 60 pages. |
| Find a customer by email | For 5 random email addresses in random upper and lower case, one of them shared by two customers and one unknown, the query returns the same rows as the original, and reads at most 20 pages. |
| Revenue for a day | For 4 random days, one of them with no orders, the query returns the same totals as the original, and reads at most 200 pages. |

When a check fails, the grader says which value it used, how many pages the query read, and whether it scanned a whole table.

## Files you may change

- `challenges/speed-up-the-queries/indexes.sql`
- `challenges/speed-up-the-queries/queries/orders_for_customer.sql`
- `challenges/speed-up-the-queries/queries/find_customer.sql`
- `challenges/speed-up-the-queries/queries/revenue_for_day.sql`

A PR that changes any other file is not graded.

## Hints

<details>
<summary>Hint 1: read the plan</summary>

`EXPLAIN (ANALYZE, BUFFERS)` in front of a query shows each step of the plan, how many rows it produced, and how many pages (`Buffers: shared hit=... read=...`) it touched.
</details>

<details>
<summary>Hint 2: let the index do the sorting</summary>

An index on `customer_id` alone finds a customer's orders, but they still have to be sorted, and a large customer has thousands. `LIMIT 20` can stop after 20 rows only when the index hands out the rows in the order the query wants.
</details>

<details>
<summary>Hint 3: an index on a column does not help a function of it</summary>

The index on `email` is no use for `lower(email) = ...`. PostgreSQL can index an expression too.
</details>

<details>
<summary>Hint 4: created_at::date</summary>

PostgreSQL cannot use the index on `created_at` for `created_at::date = $1`, and it cannot index `created_at::date` either, because the result depends on the time zone setting. Ask for a range of `created_at` instead.
</details>

## Test it locally

You need Docker and `psql`. Start a throwaway server and load everything the way the grader does:

```bash
docker run -d --name shop-db -e POSTGRES_PASSWORD=shop -p 5432:5432 postgres:18.6
export PGHOST=localhost PGUSER=postgres PGPASSWORD=shop PGTZ=UTC
psql -c 'CREATE DATABASE shop'
psql -d shop -v ON_ERROR_STOP=1 -f schema.sql -f indexes.sql -f seed.sql -c 'ANALYZE'
```

Then open `psql -d shop`, paste a query with a real value in place of `$1` (for example customer `42` or the day `'2026-03-14'`), and put `EXPLAIN (ANALYZE, BUFFERS)` in front of it. After you change `indexes.sql`, drop the database and load it again, because the grader also runs it on empty tables.

To run the grader itself against that server (it creates the database, so drop `shop` first):

```bash
psql -c 'DROP DATABASE shop'
python3 -m venv /tmp/grader && /tmp/grader/bin/pip install 'psycopg[binary]==3.3.6' pglast==8.7
/tmp/grader/bin/python grade/grade_db.py run .
docker rm -f shop-db
```

## Submit

1. Fork this repo and create a branch.
2. Commit your changes to `indexes.sql` and the files in `queries/`, in `challenges/speed-up-the-queries/`.
3. Open a PR against `main` in `The-DevOps-Daily/challenges`.

The grader comments on the PR with a result for each check. Push more commits to the same branch until every check passes. Then the PR gets the `passed` label and is closed. It is never merged. If GitHub shows a merge conflict on the PR, the grader cannot run: update your branch from `main` first.
