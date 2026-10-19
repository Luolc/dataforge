# dataforge (Python)

The Python package of dataforge, for dataset repositories. It uses only the standard library.

`dataforge.statemachine` runs the process that builds a dataset as a state machine. The
dataset gives the states: each has a body that does the work and returns an outcome, the
transitions from each outcome to the next state or the end, and its `max_visits`. The engine
(`dataforge.statemachine.engine`) runs them, ends the attempt at a cap or a guard, and records
it. The actions a body calls (`dataforge.statemachine.actions`) are `agent`, `script` and
`trial`, each in an environment (`Host()` or a `Docker` container), plus `fan_out`; they share
memory through one `ResourcePool` per process.

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
