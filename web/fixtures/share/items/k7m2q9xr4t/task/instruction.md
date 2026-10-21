# Fix the header reader

The package `tinyparse` in `/app` reads a small key-value header at the
top of a text file. A blank line ends the header, but the reader goes on past it and reads the body as header keys.

Change the code under `/app/tinyparse` so that the tests in `/app/tests`
pass. Do not change the tests.
