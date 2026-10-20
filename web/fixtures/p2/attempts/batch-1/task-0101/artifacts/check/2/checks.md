## Static checks

| Check        | Result  | Note                                                |
| ------------ | ------- | --------------------------------------------------- |
| ruff         | pass    |                                                     |
| basedpyright | pass    |                                                     |
| Line length  | fail    | 2 lines over 100 characters in `tests/test_body.py` |
| Docstrings   | warning | `body.split` has no Returns section                 |

Warnings do not send the task back.
