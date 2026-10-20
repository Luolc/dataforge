# Reject oversized headers

The parser in `tinyparse/header.py` accepts a header longer than its declared length. Make it raise `HeaderError` instead.

- Keep valid headers as they are.
- Draft 1.
