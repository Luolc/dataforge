## test_quality

| Criterion                                                       | Severity | Result | Note                                          |
| --------------------------------------------------------------- | -------- | ------ | --------------------------------------------- |
| The tests fail on the base for the reason the instruction gives | error    | pass   | 2 of 6 fail on the repeated header            |
| The tests accept any correct fix, not only the reference one    | error    | pass   | They assert only that `HeaderError` is raised |
| No test depends on order, time or shared state                  | error    | pass   |                                               |
| The tests ask no more than the instruction says                 | warning  | fail   | One test checks the error's wording           |

## instruction_review

| Criterion                            | Severity | Result | Note |
| ------------------------------------ | -------- | ------ | ---- |
| Describes the behaviour, not the fix | error    | pass   |      |
| Says how to run the existing tests   | warning  | pass   |      |

The task asks the solver to make a header parser reject malformed lines. One warning-level criterion failed; warnings do not send the task back.

<b>Raw HTML from a reviewer shows as text.</b>
