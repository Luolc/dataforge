"""The store of a dataset's attempts, their states and the runner of one.

A dataset keeps one store (store), a SQLite file in which each attempt has a
row: running while a process runs it, interrupted once that process is gone
with no outcome written, ended once its outcome is written. run_attempt
(runner) runs one attempt with the dataset's hooks (hooks) and keeps its row;
reconcile makes the rows converge when a holder died; render gives the record
file of a row. `dataforge runs list|show|reconcile|render` is the command
(command).
"""

from dataforge.runs.hooks import Hooks, OnStep
from dataforge.runs.reconcile import (
  cancel_requested,
  JOB_ID_ENV,
  QUEUE_DB_ENV,
  reconcile,
)
from dataforge.runs.render import render
from dataforge.runs.runner import (
  END_RETRIES_S,
  EXIT_UNWRITTEN,
  GoOn,
  run_attempt,
  StartError,
)
from dataforge.runs.store import (
  AttemptRow,
  Event,
  NewerSchemaError,
  QueueJob,
  SCHEMA_MAJOR,
  SCHEMA_MINOR,
  STATES,
  STOPPED_BY,
  Store,
)

__all__ = [
  "END_RETRIES_S",
  "EXIT_UNWRITTEN",
  "JOB_ID_ENV",
  "QUEUE_DB_ENV",
  "SCHEMA_MAJOR",
  "SCHEMA_MINOR",
  "STATES",
  "STOPPED_BY",
  "AttemptRow",
  "Event",
  "GoOn",
  "Hooks",
  "NewerSchemaError",
  "OnStep",
  "QueueJob",
  "StartError",
  "Store",
  "cancel_requested",
  "reconcile",
  "render",
  "run_attempt",
]
