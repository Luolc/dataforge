### tests

- **covers_the_change** failed: The tests never send a header past its declared length.
  Fix: Send a header one byte longer than declared.
