"""A project's tabs of Markdown files, and the values read beside them.

The versions an attempt has of each tab, and the values its fields and the
listing's columns read from JSON files.

Every read goes through paths.open_path, so no symlink is followed and no
read leaves the attempt's directory.
"""

from collections.abc import Callable
from dataclasses import dataclass
import json
import re
from typing import cast

from dataforge.viz.config import (
  Column,
  Project,
  Tab,
  TabField,
  TabPart,
  TabSource,
)
from dataforge.viz.paths import (
  FILE_LIMIT,
  find_dirs,
  open_path,
  read_regular,
  RefusedError,
)

_DIGITS = re.compile(r"([0-9]+)")

type _Natural = tuple[tuple[tuple[int, str, int], ...], str]


@dataclass(frozen=True, slots=True)
class Version:
  """A directory of an attempt that is a version of a tab.

  Attributes:
    dir: Its path relative to the attempt's directory.
    source: The index of the tab's source whose pattern matched it.
    label: What the menu shows for it: its source's label, each "*" named
      in it replaced by what the "*" matched; None when the source names no
      label.
  """

  dir: str
  source: int
  label: str | None


def sourced(tab: Tab, attempt: int) -> list[Version]:
  """List the directories of an attempt that are versions of a tab.

  The versions come in the order of the tab's sources; those of one source
  by path, last first, compared a component at a time, a run of digits as a
  number, so that "run-10" comes before "run-9". A path two sources match
  comes once, where the first puts it.

  Args:
    tab: The tab.
    attempt: The attempt's directory.

  Returns:
    The versions, the first the one shown by default; a directory with a
    symlink or anything but a directory on its path is none.
  """
  found: list[Version] = []
  taken: set[str] = set()
  for i, source in enumerate(tab.sources):
    pattern = tuple(source.pattern.split("/"))
    dirs = [d for d in find_dirs(attempt, pattern, _matches) if d not in taken]
    for path in sorted(dirs, key=_path_order, reverse=True):
      taken.add(path)
      found.append(Version(path, i, _label(source, pattern, path)))
  return found


def versions(tab: Tab, attempt: int) -> list[str]:
  """List the paths of the versions of a tab in an attempt, as sourced().

  Args:
    tab: The tab.
    attempt: The attempt's directory.

  Returns:
    The paths of the directories, relative to the attempt's, in order.
  """
  return [v.dir for v in sourced(tab, attempt)]


FOUND = "found"
"""A read that found its value, which may be JSON null."""

NOT_RUN = "not run"
"""A read whose file, or the tab's version it is in, is not there."""

MISSING = "missing"
"""A read whose file is there but refused, not JSON or without the keys."""


def fields(
  part: TabPart, attempt: int, version: str
) -> list[dict[str, object]]:
  """Read the values of a tab's part's fields for one version.

  Args:
    part: The part.
    attempt: The attempt's directory.
    version: The version's path, one of versions().

  Returns:
    One {read, value, reason} per field, in order: read FOUND, NOT_RUN or
    MISSING; value the JSON value found, null unless read is FOUND; reason
    why there is none, null when read is FOUND.
  """
  read = _reader(attempt)
  return [_found(read, version, field) for field in part.fields]


def version_title(
  title: TabField, attempt: int, version: str
) -> dict[str, object]:
  """Read the value a version is named by in its tab's menu.

  Args:
    title: The version_title of the version's source.
    attempt: The attempt's directory.
    version: The version's path, one of versions().

  Returns:
    The value as fields() gives one.
  """
  return _found(_reader(attempt), version, title)


def column_value(
  project: Project, column: Column, attempt: int
) -> dict[str, object]:
  """Read a column's value from its file, as the listing shows it.

  Args:
    project: The project, which names the column's tab.
    column: A column with a file.
    attempt: The attempt's directory.

  Returns:
    The value as fields() gives one; NOT_RUN too when the column's tab has
    no version.
  """
  base = ""
  if column.tab is not None:
    tab = next(t for t in project.tabs if t.name == column.tab)
    found = versions(tab, attempt)
    if not found:
      return {
        "read": NOT_RUN,
        "value": None,
        "reason": f"no version of {tab.title}",
      }
    base = found[0]
  field = TabField(column.title, column.file or (), column.field, column.format)
  return _found(_reader(attempt), base, field)


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
    state = NOT_RUN if error.absent else MISSING
    return {"read": state, "value": None, "reason": error.reason}
  except _NotJSONError as error:
    return {"read": MISSING, "value": None, "reason": str(error)}
  for key in field.field:
    if isinstance(key, int) and isinstance(current, list):
      items = cast(list[object], current)
      if key < len(items):
        current = items[key]
        continue
    elif isinstance(key, str) and isinstance(current, dict):
      keys = cast(dict[str, object], current)
      if key in keys:
        current = keys[key]
        continue
    reason = f"{path} has no {'.'.join(map(str, field.field))}"
    return {"read": MISSING, "value": None, "reason": reason}
  return {"read": FOUND, "value": current, "reason": None}


def _stars(pattern: str, name: str) -> tuple[str, ...] | None:
  # What each "*" of a component's pattern matched; None for no match.
  if name.startswith(".") and not pattern.startswith("."):
    return None
  parts = (re.escape(part) for part in pattern.split("*"))
  found = re.fullmatch("(.*)".join(parts), name, re.DOTALL)
  return None if found is None else cast(tuple[str, ...], found.groups())


def _matches(pattern: str, name: str) -> bool:
  return _stars(pattern, name) is not None


def _label(
  source: TabSource, pattern: tuple[str, ...], path: str
) -> str | None:
  if not source.label:
    return None
  stars: list[str] = []
  for part, name in zip(pattern, path.split("/"), strict=True):
    stars.extend(_stars(part, name) or ())
  return "".join(
    piece if isinstance(piece, str) else stars[piece - 1]
    for piece in source.label
  )


def _path_order(path: str) -> tuple[_Natural, ...]:
  return tuple(_natural(name) for name in path.split("/"))


def _natural(name: str) -> _Natural:
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
