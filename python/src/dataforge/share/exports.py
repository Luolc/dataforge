"""Reading an export directory, the one source of what share serves.

An export directory holds:

  export.json          {format, project: {id, name, color},
                        session: {thinking}, statuses, items: [{id, revision}]}
  items/<id>/          each item export.json lists: its record.json and the
                       files its deliverables name

The directory itself is configuration, opened as given; everything in it is
opened one component at a time with O_NOFOLLOW (dataforge.viz.paths), so no
symlink in it is followed and no read leaves it. export.json is read again
for every request, and an item it does not list is never served.
"""

from collections.abc import Generator
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import cast

from dataforge.viz.config import THINKING, TONES
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


class ExportError(Exception):
  """An export.json that fails a check, with the key that is wrong."""


@dataclass(frozen=True, slots=True)
class Item:
  """An item of an export.

  Attributes:
    id: Its ID, the name of its directory under items/.
    revision: Its revision, from 1.
  """

  id: str
  revision: int


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
  """

  id: str
  name: str
  color: str
  thinking: str
  statuses: dict[str, dict[str, str]]
  items: tuple[Item, ...]

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
    "", value, {"format", "project", "items"}, {"session", "statuses"}
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
  return Export(
    id=id_,
    name=_str("project.name", project["name"]),
    color=color,
    thinking=thinking,
    statuses=_statuses(top.get("statuses", {})),
    items=_items(top["items"]),
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


def _items(value: object) -> tuple[Item, ...]:
  if not isinstance(value, list):
    raise ExportError("items: not a list")
  items: list[Item] = []
  for i, entry in enumerate(cast(list[object], value)):
    fields = _mapping(f"items[{i}]", entry, {"id", "revision"})
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
    items.append(Item(id_, revision))
  return tuple(items)


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
