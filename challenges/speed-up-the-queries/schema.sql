-- The shop database: customers and their orders.
CREATE TABLE customers (
    id          bigint PRIMARY KEY,
    email       text NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL
);

CREATE INDEX customers_email_idx ON customers (email);

CREATE TABLE orders (
    id           bigint PRIMARY KEY,
    customer_id  bigint NOT NULL REFERENCES customers (id),
    status       text NOT NULL,
    total_cents  integer NOT NULL,
    created_at   timestamptz NOT NULL
);

CREATE INDEX orders_created_at_idx ON orders (created_at);
