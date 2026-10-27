# Stop the header at the blank line

`tinyparse` in `/app` keeps reading header keys after the blank line that ends
the header. Make it stop there, so that the tests in `/app/tests` pass,
without changing the tests.
