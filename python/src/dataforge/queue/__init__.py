"""A queue of jobs on one machine, each a command, started as caps allow.

A dataset project names the queues and their caps in a queue file (config);
jobs are rows of a SQLite table (store); the scheduler, `dataforge queue
serve`, starts them (scheduler), each under a wrapper that writes its end
(wrapper). submit, jobs and cancel are what other processes call.
"""

from dataforge.queue.config import Config, ConfigError, load_config
from dataforge.queue.store import (
  cancel,
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
  "jobs",
  "load_config",
  "submit",
]
