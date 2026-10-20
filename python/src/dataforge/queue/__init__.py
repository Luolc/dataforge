"""A queue of jobs on one machine, each a command, started as caps allow.

A dataset project names the queues and their caps in a queue file (config);
jobs are rows of a SQLite table (store); the scheduler, `dataforge queue
serve`, starts them (scheduler), each under a wrapper that writes its end
(wrapper). submit, jobs, count and cancel are what other processes call.

A job's command runs with the scheduler's environment, not the submitter's:
the table keeps no environment, so that no credential lands in it. A command
that needs more, such as a PATH or a credential, sets it up itself, for
example through a script of the dataset project.
"""

from dataforge.queue.config import Config, ConfigError, load_config
from dataforge.queue.store import (
  cancel,
  count,
  DuplicateError,
  Job,
  jobs,
  NoSuchJobError,
  STATES,
  submit,
)

__all__ = [
  "STATES",
  "Config",
  "ConfigError",
  "DuplicateError",
  "Job",
  "NoSuchJobError",
  "cancel",
  "count",
  "jobs",
  "load_config",
  "submit",
]
