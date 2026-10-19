# dataforge (Python)

The Python package of dataforge, for dataset repositories.

`dataforge.curation` runs the process that curates a dataset as a state machine. A dataset
gives the states: each runs and returns an outcome, maps each outcome to the next state or the
end, and has its `max_visits`. `Machine` (`dataforge.curation.engine`) runs them, ends the
attempt at a cap or a guard, and records it. The actions a state runs
(`dataforge.curation.actions`) are `agent`, `script`, `judge` and `trial`, each in an environment
(`Host()` or a `Docker` container), plus `fan_out`; they share memory through one
`ResourcePool` per process. `dataforge.curation.states` has dataforge's own states, one per
action (`AgentState`, `ScriptState`, `TrialState`, `PassAtKState`), which a dataset
configures or subclasses; `State` and `Machine` may be subclassed too.

`dataforge.curation.harbor` has `HarborRunner`, which runs each trial with the Harbor CLI at the
path it is given; the dataset installs Harbor, and this package does not depend on it.

`dataforge.runtime.claude` runs Claude Code headless: `Session.run` starts one `claude -p`
session in safe mode with the tools, permission rules, schema and spend cap it is given and
keeps its trace on disk, `read_trace` reads how a session ended, and `Runner` drives sessions as
the `AgentRunner` of `Actions`, on the host or in a `Docker` container. It runs the command and
environment the dataset gives and knows no gateway or credential.

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
uv run --locked pydoclint --config pyproject.toml --quiet src tests
uv run --locked basedpyright
uv run --locked pytest
```
