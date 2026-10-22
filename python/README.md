# dataforge (Python)

The Python package of dataforge, for dataset repositories.

`dataforge.curation` runs the process that curates a dataset as a state machine. A dataset
gives the states: each runs and returns an outcome, maps each outcome to the next state or the
end, and has its `max_visits`. `Machine` (`dataforge.curation.engine`) runs them, ends the
attempt at a cap or a guard, and records it. The actions a state runs
(`dataforge.curation.actions`) are `agent`, `script`, `judge` and `trial`, each in an environment
(`Host()` or a `Docker` container), plus `fan_out`; they share memory through a
`ResourcePool`, whose reservations every process of the machine counts, in the state directory
(`dataforge.statedir`: `DATAFORGE_STATE_DIR`, else `$XDG_STATE_HOME/dataforge`, else
`~/.local/state/dataforge`). `dataforge.curation.states` has dataforge's own states
(`AgentState`, `ScriptState`, `TrialState`, `PassAtKState`, `TrialsState`, `JudgeState`,
`RubricState`), which a dataset configures or subclasses; `State` and `Machine` may be subclassed too.

`dataforge.curation.harbor` has `HarborRunner`, which runs each trial with the Harbor CLI at the
path it is given; the dataset installs Harbor, and this package does not depend on it.

`dataforge.runtime.claude` runs Claude Code headless: `Session.run` starts one `claude -p`
session in safe mode with the tools, permission rules, schema and spend cap it is given and
keeps its trace on disk, `read_trace` reads how a session ended, and `Runner` drives sessions as
the `AgentRunner` of `Actions`, on the host or in a `Docker` container. It runs the command and
environment the dataset gives and knows no gateway or credential.

`dataforge.viz` is the web viewer of attempts and of the job queue, with its frontend in `../web/`
(design: [`docs/design-viz.md`](../docs/design-viz.md)); it writes only to the queue, to put a data
item's actions in it and to cancel a job. It needs the extra `viz`, which brings
FastAPI and uvicorn; `dataforge viz serve --config <site file> --static <built web/dist>` starts it.

`dataforge.queue` is a queue of jobs on one machine: a job is a command, a queue name and the
memory it declares, kept in one SQLite file. The dataset gives a queue file that names its queues
and their caps, the machine's cap, and the `MemAvailable` and 1-minute load a start needs.
`dataforge queue serve --config <queue file>` runs the scheduler, which starts each job in a
session of its own under a wrapper that writes how it ended, so a scheduler started again takes
the running jobs as they are. `dataforge queue submit`, `list` and `cancel`, and the functions
`submit`, `jobs` and `cancel`, are how other processes use it. A job's command runs with the
scheduler's environment, not the submitter's; one that needs a PATH or a credential sets it up
itself. A systemd unit that runs the scheduler needs `KillMode=process`, so that stopping or
restarting it leaves the jobs running.

`dataforge.testrunner` runs a Harbor task's tests with the project's own runner (pytest, Maven
Surefire, PHPUnit) and writes them as CTRF to `/logs/verifier/ctrf.json`. It uses only Python
3.12's standard library, so that a task runs on its own: a dataset writes `testrunner.files()`
into each task's `tests/` and its harness imports `testrunner` there. `tests/manifest.toml`
lists the tests (`[[test]]`: `suite`, `runner`, `id`, and optionally `dir`, `kind` and
`principle`), the files to copy into the project before they run (`[[file]]`: `source`, `dest`)
and why a suite has none (`[no_tests]`); `testrunner.run` runs them, a listed test the runner
did not report counting as an error, and `testrunner.ctrf.suite_counts` gives each suite's
passed and total counts, from which the dataset writes its `reward.json`. `testrunner` restores
nothing: the harness checks back to the base the files only a runner loads
(`testrunner.runners.RUNNER_FILES`) and those that define the listed tests
(`testrunner.runners.restore_paths`) before it runs them. A dataset with no harness of its own
runs `python -m testrunner` in `/tests`, and its result holds only once those files are the
base's. Maven cannot take its test configuration from the base: a changed `pom.xml` can replace
what runs under a listed name and what is reported, so a dataset that runs Maven tests checks
`pom.xml` itself.

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
