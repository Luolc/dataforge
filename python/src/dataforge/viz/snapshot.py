"""The check of a snapshot file, the counts a snapshot page shows.

A dataset writes the file; the viewer serves it as written once it passes
check. A dataset may call check from its own tests, with dataforge installed
without the viewer's extra, on snapshots it makes.
"""

from collections.abc import Collection, Mapping
from datetime import datetime
import math
import re
from typing import cast

from dataforge.viz.config import FORMATS, split_relative, TONES

VERSION = 1
"""The version of the snapshot's shape the viewer knows."""

COLUMN_FORMATS = (*FORMATS, "status")
"""The formats a column of a step's table may take: a column's of the home
page, or status, a word drawn by the snapshot's statuses."""

_NAME = re.compile(r"[a-z0-9-]+")
_COLUMN = re.compile(r"[a-z0-9_-]+")
# An ISO 8601 time with its offset, in a form both Python and a browser read.
_TIME = re.compile(
  r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?"
  + r"(Z|[+-][0-9]{2}:[0-9]{2})"
)
_NUMBERS = ("number", "usd", "duration")
_FILTERED = ("text", "status")


class SnapshotError(Exception):
  """A snapshot that does not have the shape, with the first key wrong."""


def check(data: object, roots: Collection[str], pages: Collection[str]) -> None:
  """Check a snapshot, as json.load reads it, against the shape it must have.

  Args:
    data: The snapshot.
    roots: The names of the page's roots, which a row's dir may give.
    pages: The names of the project's other pages, which a step may open.

  Raises:
    SnapshotError: When it does not have the shape, naming the first key
      that is wrong.
  """
  top = _mapping("the snapshot", data)
  # The version first, so that a newer shape says so rather than which of
  # its keys the viewer does not know.
  if "version" not in top:
    raise SnapshotError("version: missing")
  version = top["version"]
  if not isinstance(version, int) or isinstance(version, bool):
    raise SnapshotError("version: not an integer")
  if version != VERSION:
    raise SnapshotError(f"version: {version} is not {VERSION}, the one known")
  _keys("", top, {"version", "generated_at", "steps"}, {"statuses"})
  _time("generated_at", top["generated_at"])
  if "statuses" in top:
    for word, entry in _mapping("statuses", top["statuses"]).items():
      where = f"statuses.{word}"
      status = _mapping(where, entry)
      _keys(where, status, {"label", "tone"}, set())
      _ = _text(f"{where}.label", status["label"])
      if status["tone"] not in TONES:
        raise SnapshotError(f"{where}.tone: not one of {', '.join(TONES)}")
  steps = _list("steps", top["steps"])
  if not steps:
    raise SnapshotError("steps: empty")
  names: set[str] = set()
  for i, step in enumerate(steps):
    name = _step(f"steps[{i}]", step, roots, pages)
    if name in names:
      raise SnapshotError(f"steps[{i}].name: {name!r} is repeated")
    names.add(name)


def _step(
  where: str, value: object, roots: Collection[str], pages: Collection[str]
) -> str:
  step = _mapping(where, value)
  _keys(
    where,
    step,
    {"name", "title", "value"},
    {"unit", "of", "note", "generated_at", "page", "entries"},
  )
  name = _text(f"{where}.name", step["name"])
  if not _NAME.fullmatch(name):
    raise SnapshotError(f"{where}.name: {name!r} is not of [a-z0-9-]")
  _ = _text(f"{where}.title", step["title"])
  _ = _number(f"{where}.value", step["value"])
  for key in ("unit", "note"):
    if key in step:
      _ = _text(f"{where}.{key}", step[key])
  if "of" in step and _number(f"{where}.of", step["of"]) <= 0:
    raise SnapshotError(f"{where}.of: not above 0")
  if "generated_at" in step:
    _time(f"{where}.generated_at", step["generated_at"])
  if "page" in step:
    if "entries" in step:
      raise SnapshotError(f"{where}: both page and entries")
    page = _text(f"{where}.page", step["page"])
    if page not in pages:
      raise SnapshotError(f"{where}.page: {page!r} is no other page")
  if "entries" in step:
    _entries(f"{where}.entries", step["entries"], roots)
  return name


def _entries(where: str, value: object, roots: Collection[str]) -> None:
  entries = _mapping(where, value)
  _keys(where, entries, {"id_title", "columns", "rows"}, set())
  _ = _text(f"{where}.id_title", entries["id_title"])
  formats: dict[str, str] = {}
  for i, column in enumerate(_list(f"{where}.columns", entries["columns"])):
    at = f"{where}.columns[{i}]"
    mapping = _mapping(at, column)
    _keys(at, mapping, {"name", "title"}, {"format", "filter"})
    name = _text(f"{at}.name", mapping["name"])
    if not _COLUMN.fullmatch(name):
      raise SnapshotError(f"{at}.name: {name!r} is not of [a-z0-9_-]")
    if name in formats:
      raise SnapshotError(f"{at}.name: {name!r} is repeated")
    _ = _text(f"{at}.title", mapping["title"])
    format_ = mapping.get("format", "text")
    if format_ not in COLUMN_FORMATS:
      raise SnapshotError(
        f"{at}.format: not one of {', '.join(COLUMN_FORMATS)}"
      )
    if "filter" in mapping:
      if not isinstance(mapping["filter"], bool):
        raise SnapshotError(f"{at}.filter: not a boolean")
      if format_ not in _FILTERED:
        raise SnapshotError(f"{at}.filter: only for text and status")
    formats[name] = cast(str, format_)
  ids: set[str] = set()
  for i, row in enumerate(_list(f"{where}.rows", entries["rows"])):
    id_ = _row(f"{where}.rows[{i}]", row, formats, roots)
    if id_ in ids:
      raise SnapshotError(f"{where}.rows[{i}].id: {id_!r} is repeated")
    ids.add(id_)


def _row(
  where: str,
  value: object,
  formats: Mapping[str, str],
  roots: Collection[str],
) -> str:
  row = _mapping(where, value)
  _keys(where, row, {"id", "values"}, {"dir"})
  id_ = _text(f"{where}.id", row["id"])
  for column, cell in _mapping(f"{where}.values", row["values"]).items():
    at = f"{where}.values.{column}"
    if column not in formats:
      raise SnapshotError(f"{at}: no such column")
    if cell is not None:
      _cell(at, cell, formats[column])
  if "dir" in row:
    _dir(f"{where}.dir", row["dir"], roots)
  return id_


def _cell(where: str, value: object, format_: str) -> None:
  if format_ in _NUMBERS:
    _ = _number(where, value)
  elif format_ == "time":
    _time(where, value)
  elif not isinstance(value, str):
    raise SnapshotError(f"{where}: not a string")


def _dir(where: str, value: object, roots: Collection[str]) -> None:
  dir_ = _mapping(where, value)
  _keys(where, dir_, {"root", "path"}, {"open"})
  root = _text(f"{where}.root", dir_["root"])
  if root not in roots:
    raise SnapshotError(f"{where}.root: {root!r} is not one of the page's")
  for key in ("path", "open"):
    if key in dir_ and not split_relative(_text(f"{where}.{key}", dir_[key])):
      raise SnapshotError(f"{where}.{key}: not a relative path")


def _mapping(where: str, value: object) -> dict[str, object]:
  if not isinstance(value, dict):
    raise SnapshotError(f"{where}: not an object")
  return cast(dict[str, object], value)


def _list(where: str, value: object) -> list[object]:
  if not isinstance(value, list):
    raise SnapshotError(f"{where}: not a list")
  return cast(list[object], value)


def _keys(
  where: str, data: Mapping[str, object], required: set[str], optional: set[str]
) -> None:
  prefix = f"{where}." if where else ""
  for key in data:
    if key not in required | optional:
      raise SnapshotError(f"{prefix}{key}: unknown key")
  for key in sorted(required):
    if key not in data:
      raise SnapshotError(f"{prefix}{key}: missing")


def _text(where: str, value: object) -> str:
  if not isinstance(value, str) or not value:
    raise SnapshotError(f"{where}: not a non-empty string")
  return value


def _number(where: str, value: object) -> float:
  if (
    not isinstance(value, int | float)
    or isinstance(value, bool)
    or not math.isfinite(value)
  ):
    raise SnapshotError(f"{where}: not a finite number")
  return value


def _time(where: str, value: object) -> None:
  if isinstance(value, str) and _TIME.fullmatch(value):
    try:
      _ = datetime.fromisoformat(value)
    except ValueError:
      pass
    else:
      return
  raise SnapshotError(f"{where}: not an ISO 8601 time with its offset")
