"""What an attempt's task says of its tests and principles, for the Summary.

The project's `task` names the task.toml in an attempt's directory, and
dataforge.task reads its `[metadata]`: one with `tests` by parse_metadata,
one of the format before it by parse_legacy, which leaves out the dataset's
own keys, as the Summary shows only the part every dataset shares.

The read goes through paths.open_path, so no symlink is followed and no read
leaves the attempt's directory.
"""

import tomllib
from typing import cast

from dataforge.task import (
  Metadata,
  MetadataError,
  parse_legacy,
  parse_metadata,
  SUITES,
)
from dataforge.viz.paths import (
  FILE_LIMIT,
  open_path,
  read_regular,
  RefusedError,
)
from dataforge.viz.tabs import FOUND, MISSING, NOT_RUN


def read_task(task: tuple[str, ...], attempt: int) -> dict[str, object]:
  """Read the metadata of an attempt's task.

  Args:
    task: The task.toml's path in the attempt's directory, the project's
      `task`.
    attempt: The attempt's directory.

  Returns:
    {read, reason, legacy, tests, principles}: read FOUND, NOT_RUN when the
    file is not there, or MISSING when it is refused, not TOML or its
    `[metadata]` not of dataforge.task's shape; reason why, null when read
    is FOUND; legacy whether the file is of the format before `tests`, which
    records no p2p and no test of its principle; tests the counts of each
    suite the file has, in the order of SUITES; principles each principle,
    {id, text, type, source, tests}, in the file's order. tests and
    principles are null unless read is FOUND.
  """
  path = "/".join(task)
  try:
    with open_path(attempt, path) as fd:
      try:
        data = read_regular(fd, FILE_LIMIT).data
      except RefusedError as error:
        return _none(MISSING, f"{path}: {error.reason}")
  except RefusedError as error:
    return _none(NOT_RUN if error.absent else MISSING, error.reason)
  try:
    doc = tomllib.loads(data.decode())
  except UnicodeDecodeError:
    return _none(MISSING, f"{path} is not UTF-8")
  except tomllib.TOMLDecodeError as error:
    return _none(MISSING, f"{path} is not TOML: {error}")
  meta = cast(object, doc.get("metadata"))
  if not isinstance(meta, dict):
    return _none(MISSING, f"{path} has no [metadata] table")
  table = cast(dict[str, object], meta)
  legacy = "tests" not in table
  try:
    found = parse_legacy(table, None) if legacy else parse_metadata(table)
  except MetadataError as error:
    return _none(MISSING, f"{path}: {error}")
  return {
    "read": FOUND,
    "reason": None,
    "legacy": legacy,
    "tests": _tests(found),
    "principles": [
      {
        "id": p.id,
        "text": p.text,
        "type": p.type,
        "source": p.source,
        "tests": list(p.tests),
      }
      for p in found.principles
    ],
  }


def _tests(found: Metadata) -> dict[str, dict[str, int]]:
  return {s: dict(found.tests[s]) for s in SUITES if s in found.tests}


def _none(read: str, reason: str) -> dict[str, object]:
  return {
    "read": read,
    "reason": reason,
    "legacy": False,
    "tests": None,
    "principles": None,
  }
