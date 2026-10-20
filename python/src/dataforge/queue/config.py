"""The queue file: the queues, their caps and the admission thresholds.

A dataset project gives it; dataforge knows no queue by name. It is YAML:

  queues: {build: 4, dev: 1}   # each queue's name and how many may run
  max_running: 5               # how many jobs may run on the machine at once
  min_mem_available_mib: 4096  # MemAvailable must stay above this
  max_load1: 16                # the 1-minute load must be at most this
  database: queue.sqlite       # optional; relative to the file
  logs: logs                   # optional; relative to the file
  cancel_grace_s: 30           # optional: SIGTERM to SIGKILL
  poll_s: 2                    # optional: between two looks at the table

The database and the logs default to the state directory's queue/.
"""

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import cast

import yaml

from dataforge.statedir import state_dir

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
_KEYS = {
  "queues",
  "max_running",
  "min_mem_available_mib",
  "max_load1",
  "database",
  "logs",
  "cancel_grace_s",
  "poll_s",
}


class ConfigError(Exception):
  """A queue file that fails a check."""


@dataclass(frozen=True, slots=True)
class Config:
  """A loaded queue file.

  Attributes:
    queues: Each queue's name and how many of its jobs may run at once.
    max_running: How many jobs of all queues may run at once.
    min_mem_available_mib: The MemAvailable a job's start must leave, in MiB.
    max_load1: The highest 1-minute load at which a job starts.
    database: The SQLite file.
    logs: The directory of the jobs' logs.
    cancel_grace_s: How long a canceled job has between SIGTERM and SIGKILL.
    poll_s: How long the scheduler sleeps between two looks at the table.
  """

  queues: dict[str, int]
  max_running: int
  min_mem_available_mib: int
  max_load1: float
  database: Path
  logs: Path
  cancel_grace_s: float = 30.0
  poll_s: float = 2.0


def load_config(path: Path) -> Config:
  """Read a queue file.

  Args:
    path: The file.

  Returns:
    The config, its paths absolute.

  Raises:
    ConfigError: When the file cannot be read or fails a check.
  """
  try:
    data = cast(object, yaml.safe_load(path.read_text()))
  except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
    raise ConfigError(f"{path}: {error}") from error
  if not isinstance(data, dict):
    raise ConfigError(f"{path}: not a mapping")
  data = cast(dict[str, object], data)
  if unknown := sorted(set(data) - _KEYS):
    raise ConfigError(f"{path}: unknown key {unknown[0]}")
  queues = _queues(path, data.get("queues"))
  base = path.resolve().parent
  database = _path(base, path, "database", data)
  logs = _path(base, path, "logs", data)
  if database is None or logs is None:
    default = state_dir() / "queue"
    database = database or default / "queue.sqlite"
    logs = logs or default / "logs"
  return Config(
    queues=queues,
    max_running=_count(path, "max_running", data.get("max_running")),
    min_mem_available_mib=_count(
      path, "min_mem_available_mib", data.get("min_mem_available_mib"), 0
    ),
    max_load1=_number(path, "max_load1", data.get("max_load1")),
    database=database,
    logs=logs,
    cancel_grace_s=_number(
      path, "cancel_grace_s", data.get("cancel_grace_s", 30.0)
    ),
    poll_s=_number(path, "poll_s", data.get("poll_s", 2.0), positive=True),
  )


def _queues(path: Path, value: object) -> dict[str, int]:
  if not isinstance(value, dict) or not value:
    raise ConfigError(f"{path}: queues must be a non-empty mapping")
  queues: dict[str, int] = {}
  for name, cap in cast(dict[object, object], value).items():
    if not isinstance(name, str) or not _NAME.fullmatch(name):
      raise ConfigError(f"{path}: queues: bad queue name {name!r}")
    queues[name] = _count(path, f"queues.{name}", cap)
  return queues


def _count(path: Path, key: str, value: object, least: int = 1) -> int:
  if isinstance(value, bool) or not isinstance(value, int) or value < least:
    raise ConfigError(f"{path}: {key} must be an integer of at least {least}")
  return value


def _number(
  path: Path, key: str, value: object, *, positive: bool = False
) -> float:
  if isinstance(value, bool) or not isinstance(value, int | float):
    raise ConfigError(f"{path}: {key} must be a number")
  number = float(value)
  if not math.isfinite(number) or number < 0 or (positive and number == 0):
    least = "above" if positive else "at least"
    raise ConfigError(f"{path}: {key} must be a finite number {least} 0")
  return number


def _path(
  base: Path, path: Path, key: str, data: dict[str, object]
) -> Path | None:
  value = data.get(key)
  if value is None:
    return None
  if not isinstance(value, str) or not value:
    raise ConfigError(f"{path}: {key} must be a path")
  return base / value
