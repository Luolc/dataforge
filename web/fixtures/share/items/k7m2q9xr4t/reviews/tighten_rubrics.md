## Instruction

| Criterion       | Result | Note                                    |
| --------------- | ------ | --------------------------------------- |
| States the goal | pass   | Says what the reader must do and where. |
| Names no fix    | pass   | Describes the behaviour, not the patch. |

## Tests

| Criterion         | Result | Note                                         |
| ----------------- | ------ | -------------------------------------------- |
| Covers the bug    | pass   | The first case fails before the fix.         |
| Checks edge cases | pass   | No case for a header with only a blank line. |
| Runs offline      | pass   | Needs no network.                            |
