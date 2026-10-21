# Fix the header reader

The package `tinyparse` in `/app` reads a small key-value header at the
top of a text file. Spaces around a key are kept, so `a ` and `a` are two keys.

Change the code under `/app/tinyparse` so that the tests in `/app/tests`
pass. Do not change the tests.
