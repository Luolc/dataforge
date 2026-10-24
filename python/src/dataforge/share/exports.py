"""Reading an export directory, the one source of what share serves.

An export directory holds:

  export.json          {format, project: {id, name, color},
                        session: {thinking}, statuses, columns, trials,
                        deliverables, kinds,
                        items: [{id, revision, values, reads}],
                        listed_only: [<path>]}
  items/<id>/          each item export.json lists: its record.json and the
                       files its deliverables name

The directory itself is configuration, opened as given; everything in it is
opened one component at a time with O_NOFOLLOW (dataforge.viz.paths), so no
symlink in it is followed and no read leaves it. export.json is read again
for every request, and an item it does not list is never served.
"""

from collections.abc import Generator
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import cast

from dataforge.viz.config import FORMATS, split_relative, THINKING, TONES
from dataforge.viz.paths import (
  is_dir,
  open_path,
  read_regular,
  RECORD_LIMIT,
  RefusedError,
)

FORMAT = 1
"""The format of export.json that share reads."""

ITEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
"""What an item's ID matches, as a whole: one plain path component."""

_PROJECT = re.compile(r"[a-z0-9-]+")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_NONE: set[str] = set()
_READS = ("found", "not run", "missing")
_COLUMN = {
  "format",
  "labels",
  "description",
  "hints",
  "none",
  "cases",
  "filter",
  "tag",
}


class ExportError(Exception):
  """An export.json that fails a check, with the key that is wrong."""


@dataclass(frozen=True, slots=True)
class Item:
  """An item of an export.

  Attributes:
    id: Its ID, the name of its directory under items/.
    revision: Its revision, from 1.
    values: Its value in each of the export's columns, in their order; None
      when export.json gives none.
    reads: How each value was read, as a viewer's row gives it: None for a
      value of the record, else "found", "not run" or "missing"; None when
      export.json gives none.
  """

  id: str
  revision: int
  values: tuple[object, ...] | None = None
  reads: tuple[str | None, ...] | None = None

  def public(self) -> dict[str, object]:
    """Return the item as the list of items gives it.

    Returns:
      The item as JSON data.
    """
    return {
      "id": self.id,
      "revision": self.revision,
      "values": None if self.values is None else list(self.values),
      "reads": None if self.reads is None else list(self.reads),
    }


@dataclass(frozen=True, slots=True)
class Export:
  """An export.json that passed every check.

  Attributes:
    id: The project's ID.
    name: The project's name.
    color: The project's color, #rrggbb.
    thinking: How a session view first shows thinking blocks, one of
      dataforge.viz.config.THINKING.
    statuses: The label and tone of each status word.
    items: The items, in the file's order.
    listed_only: The paths, relative to an item's directory, of the files a
      static site lists by name and size but does not serve.
    columns: The columns of the list of items, each as a viewer's project
      gives it to its page, without its field.
    trials: What a trial's view reads in its directory, as a viewer's
      project gives it; None when export.json gives none.
    deliverables: The entries of the deliverables, as a viewer's project
      gives them.
    kinds: The title of each deliverable kind's tab, by kind.
  """

  id: str
  name: str
  color: str
  thinking: str
  statuses: dict[str, dict[str, str]]
  items: tuple[Item, ...]
  listed_only: frozenset[str] = frozenset()
  columns: tuple[dict[str, object], ...] = ()
  trials: dict[str, object] | None = None
  deliverables: tuple[dict[str, object], ...] = ()
  kinds: dict[str, dict[str, str]] = field(default_factory=dict)

  def public(self) -> dict[str, object]:
    """Return the project as a page reads it, without its items.

    Returns:
      The project as JSON data.
    """
    return {
      "id": self.id,
      "name": self.name,
      "color": self.color,
      "session": {"thinking": self.thinking},
      "statuses": self.statuses,
      "columns": list(self.columns),
      "trials": self.trials,
      "deliverables": list(self.deliverables),
      "kinds": self.kinds,
    }


@contextmanager
def open_export(directory: Path) -> Generator[tuple[int, Export]]:
  """Open an export directory and read its export.json.

  The request reads the export and its items from the one directory it
  opened, so that a new export put in place of the old one in the middle
  of it does not mix the two.

  Args:
    directory: The export directory.

  Yields:
    A file descriptor of the directory, closed on exit, and its export.

  Raises:
    RefusedError: When the directory or export.json cannot be opened.
    ExportError: When export.json fails a check.
  """  # noqa: DOC503
  try:
    top = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
  except OSError as error:
    raise RefusedError(503, f"the export: {error.strerror}") from None
  try:
    with open_path(top, "export.json") as fd:
      data = read_regular(fd, RECORD_LIMIT).data
    yield top, parse(data)
  finally:
    os.close(top)


@contextmanager
def open_item(top: int, export: Export, item: str) -> Generator[int]:
  """Open the directory of an item the export lists.

  Args:
    top: The export directory.
    export: Its export.
    item: The item's ID.

  Yields:
    A file descriptor of the item's directory, closed on exit.

  Raises:
    RefusedError: When the export does not list the item, or its directory
      is missing, a symlink or not a directory.
  """
  if not any(i.id == item for i in export.items):
    raise RefusedError(404, "no such item")
  # dataforge.viz.paths names the path in its refusal; share names none.
  with ExitStack() as stack:
    try:
      fd = stack.enter_context(open_path(top, f"items/{item}"))
    except RefusedError:
      raise RefusedError(404, "no such item") from None
    if not is_dir(fd):
      raise RefusedError(404, "no such item")
    yield fd


def parse(data: bytes) -> Export:
  """Check an export.json and read it.

  Args:
    data: The file's bytes.

  Returns:
    The export.

  Raises:
    ExportError: When it is not JSON or fails a check.
  """
  try:
    value = cast(object, json.loads(data))
  except ValueError, RecursionError:
    raise ExportError("export.json: not JSON") from None
  top = _mapping(
    "",
    value,
    {"format", "project", "items"},
    {
      "session",
      "statuses",
      "listed_only",
      "columns",
      "trials",
      "deliverables",
      "kinds",
    },
  )
  format_ = top["format"]
  if format_ != FORMAT or isinstance(format_, bool):
    raise ExportError(f"format: {format_!r} is not {FORMAT}")
  project = _mapping("project", top["project"], {"id", "name", "color"})
  id_ = _str("project.id", project["id"])
  if not _PROJECT.fullmatch(id_):
    raise ExportError(f"project.id: {id_!r} is not of [a-z0-9-]")
  color = _str("project.color", project["color"])
  if not _COLOR.fullmatch(color):
    raise ExportError(f"project.color: {color!r} is not #rrggbb")
  session = _mapping("session", top.get("session", {}), set(), {"thinking"})
  thinking = _str("session.thinking", session.get("thinking", "hidden"))
  _one_of("session.thinking", thinking, THINKING)
  columns = _columns(top.get("columns", []))
  return Export(
    id=id_,
    name=_str("project.name", project["name"]),
    color=color,
    thinking=thinking,
    statuses=_statuses(top.get("statuses", {})),
    items=_items(top["items"], len(columns)),
    listed_only=_listed_only(top.get("listed_only", [])),
    columns=columns,
    trials=_trials(top.get("trials")),
    deliverables=_deliverables(top.get("deliverables", [])),
    kinds=_kinds(top.get("kinds", {})),
  )


def _statuses(value: object) -> dict[str, dict[str, str]]:
  statuses: dict[str, dict[str, str]] = {}
  if not isinstance(value, dict):
    raise ExportError("statuses: not an object")
  for word, entry in cast(dict[str, object], value).items():
    where = f"statuses.{word}"
    fields = _mapping(where, entry, {"label", "tone"})
    tone = _str(f"{where}.tone", fields["tone"])
    _one_of(f"{where}.tone", tone, TONES)
    statuses[word] = {
      "label": _str(f"{where}.label", fields["label"]),
      "tone": tone,
    }
  return statuses


def _items(value: object, columns: int) -> tuple[Item, ...]:
  if not isinstance(value, list):
    raise ExportError("items: not a list")
  items: list[Item] = []
  for i, entry in enumerate(cast(list[object], value)):
    fields = _mapping(
      f"items[{i}]", entry, {"id", "revision"}, {"values", "reads"}
    )
    id_ = _str(f"items[{i}].id", fields["id"])
    if not ITEM.fullmatch(id_):
      raise ExportError(f"items[{i}].id: {id_!r} is not a plain name")
    if any(item.id == id_ for item in items):
      raise ExportError(f"items[{i}].id: {id_} is repeated")
    revision = fields["revision"]
    if (
      not isinstance(revision, int)
      or isinstance(revision, bool)
      or revision < 1
    ):
      raise ExportError(f"items[{i}].revision: not a whole number from 1")
    values = _row(f"items[{i}].values", fields.get("values"), columns)
    reads = _row(f"items[{i}].reads", fields.get("reads"), columns)
    for j, read in enumerate(reads or ()):
      if read is not None and read not in _READS:
        where = f"items[{i}].reads[{j}]"
        raise ExportError(f"{where}: not null or one of {', '.join(_READS)}")
    items.append(
      Item(id_, revision, values, cast(tuple[str | None, ...] | None, reads))
    )
  return tuple(items)


def _row(where: str, value: object, columns: int) -> tuple[object, ...] | None:
  if value is None:
    return None
  if not isinstance(value, list):
    raise ExportError(f"{where}: not a list")
  row = tuple(cast(list[object], value))
  if len(row) != columns:
    raise ExportError(f"{where}: {len(row)} values for {columns} columns")
  return row


def _columns(value: object) -> tuple[dict[str, object], ...]:
  if not isinstance(value, list):
    raise ExportError("columns: not a list")
  columns: list[dict[str, object]] = []
  for i, entry in enumerate(cast(list[object], value)):
    where = f"columns[{i}]"
    fields = _mapping(where, entry, {"title"}, _COLUMN)
    format_ = _str(f"{where}.format", fields.get("format", "text"))
    _one_of(f"{where}.format", format_, FORMATS)
    filter_ = fields.get("filter", False)
    if not isinstance(filter_, bool):
      raise ExportError(f"{where}.filter: not a boolean")
    # The viewer's attempt page shows a tag column in its heading; the share
    # site has no such heading, so the key is read and not kept.
    if not isinstance(fields.get("tag", False), bool):
      raise ExportError(f"{where}.tag: not a boolean")
    columns.append(
      {
        "title": _str(f"{where}.title", fields["title"]),
        "format": format_,
        "labels": _words(f"{where}.labels", fields.get("labels", {})),
        "description": _optional(f"{where}.description", fields, "description"),
        "hints": _words(f"{where}.hints", fields.get("hints", {})),
        "none": _optional(f"{where}.none", fields, "none"),
        "cases": _strs(f"{where}.cases", fields.get("cases", [])),
        "filter": filter_,
      }
    )
  return tuple(columns)


def _trials(value: object) -> dict[str, object] | None:
  if value is None:
    return None
  fields = _mapping("trials", value, set(), {"diff", "tests", "kinds"})
  paths: dict[str, object] = {}
  for key in ("diff", "tests"):
    path = _optional(f"trials.{key}", fields, key)
    if path is not None and not split_relative(path):
      raise ExportError(f"trials.{key}: {path!r} is not a relative path")
    paths[key] = path
  kinds = fields.get("kinds", {})
  if not isinstance(kinds, dict):
    raise ExportError("trials.kinds: not an object")
  labels: dict[str, dict[str, str]] = {}
  for kind, entry in cast(dict[str, object], kinds).items():
    where = f"trials.kinds.{kind}"
    label = _mapping(where, entry, {"label"})["label"]
    labels[kind] = {"label": _str(f"{where}.label", label)}
  return {**paths, "kinds": labels}


def _deliverables(value: object) -> tuple[dict[str, object], ...]:
  if not isinstance(value, list):
    raise ExportError("deliverables: not a list")
  entries: list[dict[str, object]] = []
  for i, entry in enumerate(cast(list[object], value)):
    where = f"deliverables[{i}]"
    fields = _mapping(
      where,
      entry,
      {"name", "kind", "title"},
      {"no_trace", "count", "default"},
    )
    name = _str(f"{where}.name", fields["name"])
    if any(e["name"] == name for e in entries):
      raise ExportError(f"{where}.name: {name} is repeated")
    entries.append(
      {
        "name": name,
        "kind": _str(f"{where}.kind", fields["kind"]),
        "title": _str(f"{where}.title", fields["title"]),
        "no_trace": _optional(f"{where}.no_trace", fields, "no_trace"),
        "count": _optional(f"{where}.count", fields, "count"),
        "default": _default(f"{where}.default", fields.get("default", False)),
      }
    )
  return tuple(entries)


def _default(where: str, value: object) -> object:
  if isinstance(value, bool):
    return value
  fields = _mapping(where, value, {"field", "equals"})
  return {
    "field": _strs(f"{where}.field", fields["field"]),
    "equals": fields["equals"],
  }


def _kinds(value: object) -> dict[str, dict[str, str]]:
  if not isinstance(value, dict):
    raise ExportError("kinds: not an object")
  kinds: dict[str, dict[str, str]] = {}
  for kind, entry in cast(dict[str, object], value).items():
    title = _mapping(f"kinds.{kind}", entry, {"title"})["title"]
    kinds[kind] = {"title": _str(f"kinds.{kind}.title", title)}
  return kinds


def _words(where: str, value: object) -> dict[str, str]:
  if not isinstance(value, dict):
    raise ExportError(f"{where}: not an object")
  words = cast(dict[str, object], value)
  for key, word in words.items():
    if not isinstance(word, str):
      raise ExportError(f"{where}.{key}: not a string")
  return cast(dict[str, str], dict(words))


def _strs(where: str, value: object) -> list[str]:
  if not isinstance(value, list):
    raise ExportError(f"{where}: not a list")
  return [
    _str(f"{where}[{i}]", v) for i, v in enumerate(cast(list[object], value))
  ]


def _optional(where: str, fields: dict[str, object], key: str) -> str | None:
  value = fields.get(key)
  return None if value is None else _str(where, value)


def _listed_only(value: object) -> frozenset[str]:
  if not isinstance(value, list):
    raise ExportError("listed_only: not a list")
  paths: set[str] = set()
  for i, entry in enumerate(cast(list[object], value)):
    path = _str(f"listed_only[{i}]", entry)
    if not split_relative(path):
      raise ExportError(f"listed_only[{i}]: {path!r} is not a relative path")
    paths.add(path)
  return frozenset(paths)


def _mapping(
  where: str, value: object, required: set[str], optional: set[str] = _NONE
) -> dict[str, object]:
  name = where or "export.json"
  if not isinstance(value, dict):
    raise ExportError(f"{name}: not an object")
  mapping = cast(dict[str, object], value)
  for key in mapping:
    if key not in required | optional:
      raise ExportError(f"{name}: {key}: unknown key")
  for key in sorted(required):
    if key not in mapping:
      raise ExportError(f"{name}: {key}: missing")
  return mapping


def _str(where: str, value: object) -> str:
  if not isinstance(value, str) or not value:
    raise ExportError(f"{where}: not a non-empty string")
  return value


def _one_of(where: str, value: str, choices: tuple[str, ...]) -> None:
  if value not in choices:
    raise ExportError(f"{where}: {value!r} is not one of {', '.join(choices)}")
