# Fix the header reader

The package `tinyparse` in `/app` reads a small key-value header at the
top of a text file. A key with no colon raises an error instead of being skipped.

Change the code under `/app/tinyparse` so that the tests in `/app/tests`
pass. Do not change the tests.
