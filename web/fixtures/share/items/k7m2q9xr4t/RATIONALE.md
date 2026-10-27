# Rationale

Why this item is in the collection, and how it was checked.

## Contents

- [The bug](#the-bug)
- [How the task tests it](#how-the-task-tests-it)
- [Baselines](#baselines)
- [Variants](#variants)
- [Open points](#open-points)

## The bug

`tinyparse` reads a header of `key: value` lines that a blank line ends. The
reader does not stop at the blank line, so the first body line that holds a
colon is read as a header key.

```python
def read_header(lines):
    header = {}
    for line in lines:
        key, _, value = line.partition(":")
        header[key.strip()] = value.strip()
    return header
```

## How the task tests it

| Test                  | Before the fix | After the fix |
| --------------------- | -------------- | ------------- |
| `test_stops_at_blank` | fail           | pass          |
| `test_keeps_body`     | fail           | pass          |
| `test_header_only`    | pass           | pass          |
| `test_empty_file`     | pass           | pass          |

## Baselines

Two reference solutions under `baselines/` pass every test; the empty patch
passes none of the two tests above.

## Variants

`variants/` holds the instruction reworded once. Its tests are the same.

## Open points

1. No test covers a header that is only a blank line; see
   [How the task tests it](#how-the-task-tests-it).
