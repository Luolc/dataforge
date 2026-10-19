# dataforge (Python)

The Python package of dataforge, for dataset repositories.

`dataforge.curation` runs the process that curates a dataset as a state machine. A dataset
gives the states: each runs and returns an outcome, maps each outcome to the next state or the
end, and has its `max_visits`. `Machine` (`dataforge.curation.engine`) runs them, ends the
attempt at a cap or a guard, and records it. The actions a state runs
(`dataforge.curation.actions`) are `agent`, `script` and `trial`, each in an environment
(`Host()` or a `Docker` container), plus `fan_out`; they share memory through one
`ResourcePool` per process. `dataforge.curation.states` has dataforge's own states, one per
action (`AgentState`, `ScriptState`, `TrialState`, `PassAtKState`), which a dataset
subclasses; `State` and `Machine` may be subclassed too.

A dataset repository depends on it at a commit of main:

```toml
dependencies = [
    "dataforge @ git+https://github.com/Luolc/dataforge@<commit>#subdirectory=python",
]
```

## Checks

From this directory:

```sh
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked basedpyright
uv run --locked pytest
```
