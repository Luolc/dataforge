"""A project's tabs of Markdown files, and the values read beside them.

The versions an attempt has of each tab, and the values its fields and the
listing's columns read from JSON files.

Every read goes through paths.open_path, so no symlink is followed and no
read leaves the attempt's directory.
"""

from collections.abc import Callable
import json
import re
from typing import cast

from dataforge.viz.config import Column, Project, Tab, TabField
from dataforge.viz.paths import (
  FILE_LIMIT,
  list_dir,
  open_path,
  read_regular,
  RefusedError,
)

_DIGITS = re.compile(r"([0-9]+)")


def versions(tab: Tab, attempt: int) -> list[str]:
  """List the directories of an attempt that are versions of a tab.

  The versions come in the order of the tab's patterns; those of one
  pattern by name, last first, a run of digits compared as a number, so
  that "run-10" comes before "run-9". A name two patterns match comes once,
  where the first puts it.

  Args:
    tab: The tab.
    attempt: The attempt's directory.

  Returns:
    The names of the directories, the first the version shown by default;
    a symlink or anything but a directory is none.
  """
  dirs = [e.name for e in list_dir(attempt) if e.type == "dir"]
  found: list[str] = []
  for pattern in tab.versions:
    matched = [d for d in dirs if _matches(pattern, d) and d not in found]
    found.extend(sorted(matched, key=_natural, reverse=True))
  return found


def fields(tab: Tab, attempt: int, version: str) -> list[dict[str, object]]:
  """Read the values of a tab's fields for one version.

  Args:
    tab: The tab.
    attempt: The attempt's directory.
    version: The version's directory, one of versions().

  Returns:
    One {value, missing} per field, in order: value the JSON value found,
    null among them, and missing null; or value null and missing why there
    is none, for a file that is not there, not JSON or lacks the keys.
  """
  read = _reader(attempt)
  return [_found(read, version, field) for field in tab.fields]


def column_value(project: Project, column: Column, attempt: int) -> object:
  """Read a column's value from its file, as the listing shows it.

  Args:
    project: The project, which names the column's tab.
    column: A column with a file.
    attempt: The attempt's directory.

  Returns:
    The JSON value; None when there is none, as when the tab has no
    version, or the file is not there, not JSON or lacks the keys.
  """
  base = ""
  if column.tab is not None:
    tab = next(t for t in project.tabs if t.name == column.tab)
    found = versions(tab, attempt)
    if not found:
      return None
    base = found[0]
  field = TabField(column.title, column.file or (), column.field, column.format)
  return _found(_reader(attempt), base, field)["value"]


class _NotJSONError(ValueError):
  pass


def _reject(constant: str) -> object:
  # NaN and Infinity are not JSON, and a response could not carry them.
  raise _NotJSONError(constant)


def _reader(attempt: int) -> Callable[[str], object]:
  # Each file read once for all the fields that name it; a RefusedError or a
  # _NotJSONError is kept and raised again for each.
  cache: dict[str, object] = {}

  def read(path: str) -> object:
    if path not in cache:
      try:
        with open_path(attempt, path) as fd:
          data = read_regular(fd, FILE_LIMIT).data
        cache[path] = json.loads(data, parse_constant=_reject)
      except RefusedError as error:
        cache[path] = error
      except ValueError, RecursionError:
        cache[path] = _NotJSONError(f"{path} is not JSON")
    found = cache[path]
    if isinstance(found, RefusedError | _NotJSONError):
      raise found
    return found

  return read


def _found(
  read: Callable[[str], object], base: str, field: TabField
) -> dict[str, object]:
  path = "/".join((base, *field.file) if base else field.file)
  try:
    current = read(path)
  except RefusedError as error:
    return {"value": None, "missing": error.reason}
  except _NotJSONError as error:
    return {"value": None, "missing": str(error)}
  for key in field.field:
    if not isinstance(current, dict) or key not in current:
      return {
        "value": None,
        "missing": f"{path} has no {'.'.join(field.field)}",
      }
    current = cast(dict[str, object], current)[key]
  return {"value": current, "missing": None}


def _matches(pattern: str, name: str) -> bool:
  if name.startswith(".") and not pattern.startswith("."):
    return False
  parts = (re.escape(part) for part in pattern.split("*"))
  return re.fullmatch(".*".join(parts), name, re.DOTALL) is not None


def _natural(name: str) -> tuple[tuple[tuple[int, str, int], ...], str]:
  # Text and runs of digits in turn, a run compared by its value; the name
  # itself breaks a tie, as between "v1" and "v01".
  parts = _DIGITS.split(name)
  return (
    tuple(
      (1, "", int(part)) if i % 2 else (0, part, 0)
      for i, part in enumerate(parts)
    ),
    name,
  )
