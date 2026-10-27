-- The finance dashboard: paid orders and revenue for one day (UTC).
SELECT count(*) AS orders, coalesce(sum(total_cents), 0) AS revenue_cents
FROM orders
WHERE created_at::date = $1
  AND status = 'paid';
