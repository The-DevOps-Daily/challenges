-- Test data: 50,000 customers and 400,000 orders placed during 2026, in the
-- order they were placed. Customers 1 to 5 are large businesses with about
-- 24,000 orders each, customers 6 to 105 are regular businesses with about
-- 800 each, and everyone else has a handful. People type their email
-- addresses in any case, and nothing in an address gives away the
-- customer's ID.
--
-- The grader runs SELECT setseed(...) with a random value first, so the data
-- is different for every run.

INSERT INTO customers (id, email, name, created_at)
SELECT i,
       CASE WHEN r < 0.6 THEN lower(addr) WHEN r < 0.9 THEN initcap(addr) ELSE upper(addr) END,
       initcap(first) || ' ' || initcap(last),
       timestamptz '2024-01-01 00:00:00+00' + random() * interval '730 days'
FROM (
    SELECT i,
           random() AS r,
           first,
           last,
           first || '.' || last || '.' || substr(md5(random()::text), 1, 10) || '@'
               || (ARRAY['example.com', 'mail.example.org', 'shop.example.net'])[1 + floor(random() * 3)::int] AS addr
    FROM (
        SELECT i,
               (ARRAY['anna', 'ben', 'chloe', 'david', 'elena', 'farid', 'grace', 'hugo', 'ines', 'jonas',
                      'kira', 'liam', 'maya', 'nikos', 'olga', 'pavel', 'rosa', 'sami', 'tara', 'yuki'])[1 + floor(random() * 20)::int] AS first,
               (ARRAY['smith', 'garcia', 'novak', 'kim', 'rossi', 'jensen', 'ivanova', 'okafor', 'silva', 'tanaka',
                      'muller', 'dubois', 'popescu', 'haddad', 'nguyen', 'olsen', 'kowalski', 'reyes', 'singh', 'walsh'])[1 + floor(random() * 20)::int] AS last
        FROM generate_series(1, 50000) AS i
    ) AS names
) AS people;

INSERT INTO orders (id, customer_id, status, total_cents, created_at)
SELECT i,
       CASE WHEN c < 0.3 THEN 1 + floor(random() * 5)::bigint
            WHEN c < 0.5 THEN 6 + floor(random() * 100)::bigint
            ELSE 106 + floor(random() * 49895)::bigint END,
       CASE WHEN r < 0.85 THEN 'paid' WHEN r < 0.95 THEN 'pending' ELSE 'refunded' END,
       500 + floor(random() * 49500)::int,
       timestamptz '2026-01-01 00:00:00+00' + (i - 1 + random()) * interval '78.84 seconds'
FROM (SELECT i, random() AS c, random() AS r FROM generate_series(1, 400000) AS i) AS o;
