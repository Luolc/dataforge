"""testrunner, which runs a task's tests with each project's own runner.

A task's tests/manifest.toml lists its tests, each with its suite, ID and
runner or run group; testrunner runs them with the runners the project uses,
or with the run and parse the task gives, and writes their outcomes as
CTRF, the format Harbor's viewer reads from /logs/verifier/ctrf.json. The
package uses only Python 3.12's standard library and imports nothing of
dataforge: a dataset copies its files into each task (bundle.files_for), so
that the task runs without the dataset, and imports them as testrunner
there. A task's copy has core, deps, a registry of its runners only, the
modules of its runners and formats, and those of the languages in its
project whose checks of a fix's files it needs (bundle.guards_for);
bundle and __main__ stay on the host, __main__ unless the dataset asks for
it (bundle.files).
"""
