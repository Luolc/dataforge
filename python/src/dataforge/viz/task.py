"""What an attempt's task says of its tests and principles, for the Summary.

The project's `task` names the task.toml in an attempt's directory, or in the
directory of one of its deliverables as the attempt's record resolves it, and
dataforge.task reads its `[metadata]`: one with `tests` by parse_metadata,
one of the format before it by parse_legacy, which leaves out the dataset's
own keys, as the Summary shows only the part every dataset shares.

The reads go through paths.open_path and paths.read_record, so no symlink is
followed and no read leaves the attempt's directory.
"""

import json
import tomllib
from typing import cast

from dataforge.task import (
  Metadata,
  MetadataError,
  parse_legacy,
  parse_metadata,
  SUITES,
)
from dataforge.viz.config import Project, split_relative, TaskSource
from dataforge.viz.paths import (
  FILE_LIMIT,
  open_path,
  read_record,
  read_regular,
  RefusedError,
)
from dataforge.viz.tabs import FOUND, MISSING, NOT_RUN


def read_task(
  project: Project, task: TaskSource, attempt: int
) -> dict[str, object]:
  """Read the metadata of an attempt's task.

  Args:
    project: The project, which names the record file.
    task: Where the task.toml is, the project's `task`.
    attempt: The attempt's directory.

  Returns:
    {read, reason, path, legacy, tests, principles}: read FOUND, NOT_RUN
    when the file is not there or the record gives task's deliverable no
    path, or MISSING when the file or the record is refused, not TOML or
    JSON, or the `[metadata]` not of dataforge.task's shape; reason why,
    null when read is FOUND; path the task.toml's path in the attempt's
    directory, null when the record gives none; legacy whether the file is
    of the format before `tests`, which records no p2p and no test of its
    principle; tests the counts of each suite the file has, in the order of
    SUITES; principles each principle, {id, text, type, source, tests}, in
    the file's order. tests and principles are null unless read is FOUND.
  """
  where = _where(project, task, attempt)
  if isinstance(where, dict):
    return where
  path = "/".join(where)
  try:
    with open_path(attempt, path) as fd:
      try:
        data = read_regular(fd, FILE_LIMIT).data
      except RefusedError as error:
        return _none(MISSING, f"{path}: {error.reason}", path)
  except RefusedError as error:
    return _none(NOT_RUN if error.absent else MISSING, error.reason, path)
  try:
    doc = tomllib.loads(data.decode())
  except UnicodeDecodeError:
    return _none(MISSING, f"{path} is not UTF-8", path)
  except tomllib.TOMLDecodeError as error:
    return _none(MISSING, f"{path} is not TOML: {error}", path)
  meta = cast(object, doc.get("metadata"))
  if not isinstance(meta, dict):
    return _none(MISSING, f"{path} has no [metadata] table", path)
  table = cast(dict[str, object], meta)
  legacy = "tests" not in table
  try:
    found = parse_legacy(table, None) if legacy else parse_metadata(table)
  except MetadataError as error:
    return _none(MISSING, f"{path}: {error}", path)
  return {
    "read": FOUND,
    "reason": None,
    "path": path,
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


def _where(
  project: Project, task: TaskSource, attempt: int
) -> tuple[str, ...] | dict[str, object]:
  # The task.toml's path in the attempt's directory, or why there is none.
  if task.deliverable is None:
    return task.file
  name = task.deliverable
  try:
    record = cast(object, json.loads(read_record(project, attempt).data))
  except RefusedError as error:
    return _none(MISSING, f"the record: {error.reason}")
  except ValueError, RecursionError:
    return _none(MISSING, "the record is not JSON")
  entry = _entry(record, name)
  if entry is None or entry.get("path") is None:
    return _none(NOT_RUN, f"the record gives the deliverable {name} no path")
  path = entry["path"]
  parts = split_relative(path) if isinstance(path, str) else None
  if not parts:
    why = f"the record's path of the deliverable {name} is not relative"
    return _none(MISSING, why)
  return (*parts, *task.file)


def _entry(record: object, name: str) -> dict[str, object] | None:
  # The record's deliverable of that name; None when it has none.
  if not isinstance(record, dict):
    return None
  listed = cast(dict[str, object], record).get("deliverables")
  if not isinstance(listed, list):
    return None
  for item in cast(list[object], listed):
    if isinstance(item, dict):
      entry = cast(dict[str, object], item)
      if entry.get("name") == name:
        return entry
  return None


def _none(read: str, reason: str, path: str | None = None) -> dict[str, object]:
  return {
    "read": read,
    "reason": reason,
    "path": path,
    "legacy": False,
    "tests": None,
    "principles": None,
  }
