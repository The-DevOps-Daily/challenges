-- Indexes for the shop database. The grader runs this file once, as the
-- app's database user, right after schema.sql, while the tables are empty.
CREATE INDEX orders_status_idx ON orders (status);
