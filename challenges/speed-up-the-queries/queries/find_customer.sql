-- Log in and password reset: find a customer by email, whatever the case.
SELECT id, email, name
FROM customers
WHERE lower(email) = lower($1);
