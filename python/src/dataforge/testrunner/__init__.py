"""testrunner, which runs a task's tests with each project's own runner.

A task's tests/manifest.toml lists its tests, each with its suite, runner
and ID; testrunner runs them with the runners the project uses and writes
their outcomes as CTRF, the format Harbor's viewer reads from
/logs/verifier/ctrf.json. The package uses only Python 3.12's standard
library and imports nothing of dataforge: a dataset copies its files into
each task (bundle.files), so that the task runs without the dataset, and
imports them as testrunner there.
"""

from .bundle import files

__all__ = ["files"]
