# Rationale

## The bug

A header line with no colon raises `ValueError` instead of being skipped.

| Check             | Result |
| ----------------- | ------ |
| Tests fail before | pass   |
| Tests pass after  | pass   |
