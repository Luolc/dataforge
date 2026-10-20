"""The site file and the project files the viewer reads at start.

A file loads only when it passes every check; one that fails raises
ConfigError naming the file and the key that is wrong.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
from typing import cast

import yaml

TONES = ("good", "bad", "warn", "neutral")
"""The tones a status may take."""

FORMATS = ("text", "number", "usd", "duration", "time")
"""The formats a column may take."""

THINKING = ("collapsed", "expanded", "hidden")
"""How a session view may first show thinking blocks."""

_ID = re.compile(r"[a-z0-9-]+")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


class ConfigError(Exception):
  """A site or project file that fails a check."""


@dataclass(frozen=True, slots=True)
class Listen:
  """The address the server binds.

  Attributes:
    host: An IP address literal, never an unspecified one.
    port: The port.
  """

  host: str
  port: int


@dataclass(frozen=True, slots=True)
class Column:
  """A column of the attempt table.

  Attributes:
    title: Its heading.
    field: The keys into the record its value is at.
    format: One of FORMATS.
  """

  title: str
  field: tuple[str, ...]
  format: str


@dataclass(frozen=True, slots=True)
class Item:
  """How attempts group into data items.

  Attributes:
    depth: How many leading components of an attempt ID name its item.
    title: The heading of the item column.
  """

  depth: int
  title: str


@dataclass(frozen=True, slots=True)
class Deliverable:
  """A deliverable the dataset's declaration names, as a record names it.

  Attributes:
    name: The name of its entry in a record's deliverables.
    kind: Its kind, which picks its view.
    title: Its title.
  """

  name: str
  kind: str
  title: str


@dataclass(frozen=True, slots=True)
class Project:
  """A dataset's project file, as loaded.

  Attributes:
    id: The project's ID, in URLs.
    name: Its name, in the sidebar and the breadcrumb.
    color: Its colour, #rrggbb.
    attempts_dir: The directory its attempts are under.
    attempts: The pattern of an attempt's directory under attempts_dir, plain
      and "*" components.
    record: The record file's path in an attempt's directory.
    states: Each state's title, by name.
    statuses: Each status's label and tone, by name.
    item: How its attempts group into data items.
    columns: The home page's extra columns.
    thinking: How a session view first shows thinking, one of THINKING.
    not_ended: The status words of an attempt that has not ended; None for
      the engine's own rule.
    lock: The path of the lock file in an item's directory; None when the
      dataset names none.
    deliverables: The deliverables an attempt has once it ends, in order.
    trial_diff: The path of a trial's diff in its directory; None when the
      dataset names none.
    monitor: The resource monitor's SQLite database; None when the dataset
      names none.
  """

  id: str
  name: str
  color: str
  attempts_dir: Path
  attempts: tuple[str, ...]
  record: tuple[str, ...]
  states: dict[str, dict[str, str]]
  statuses: dict[str, dict[str, str]]
  item: Item
  columns: tuple[Column, ...]
  thinking: str
  not_ended: tuple[str, ...] | None = None
  lock: tuple[str, ...] | None = None
  deliverables: tuple[Deliverable, ...] = ()
  trial_diff: tuple[str, ...] | None = None
  monitor: Path | None = None

  def public(self) -> dict[str, object]:
    """Return what the frontend reads: all but where the files are.

    Returns:
      The project as JSON data, without attempts_dir, attempts, record,
      lock and the monitor's path.
    """
    return {
      "id": self.id,
      "name": self.name,
      "color": self.color,
      "states": self.states,
      "statuses": self.statuses,
      "item": {"depth": self.item.depth, "title": self.item.title},
      "columns": [
        {"title": c.title, "field": list(c.field), "format": c.format}
        for c in self.columns
      ],
      "session": {"thinking": self.thinking},
      "not_ended": None if self.not_ended is None else list(self.not_ended),
      "deliverables": [
        {"name": d.name, "kind": d.kind, "title": d.title}
        for d in self.deliverables
      ],
      "trials": None
      if self.trial_diff is None
      else {"diff": "/".join(self.trial_diff)},
      "monitor": self.monitor is not None,
    }


@dataclass(frozen=True, slots=True)
class Site:
  """The site file, with its projects loaded.

  Attributes:
    listen: The address the server binds.
    projects: The projects, in the file's order.
  """

  listen: Listen
  projects: tuple[Project, ...]


def load_site(path: Path) -> Site:
  """Load a site file and each project file it names.

  Paths in either file are relative to the file that holds them.

  Args:
    path: The site file.

  Returns:
    The site.

  Raises:
    ConfigError: When a file fails a check.
  """
  data = _read(path)
  _keys(path, "", data, required={"listen", "projects"}, optional=set())
  listen = _mapping(path, "listen", data["listen"])
  _keys(path, "listen", listen, required={"host", "port"}, optional=set())
  host = _str(path, "listen.host", listen["host"])
  try:
    address = ipaddress.ip_address(host)
  except ValueError:
    raise ConfigError(
      f"{path}: listen.host: {host!r} is not an IP address"
    ) from None
  if address.is_unspecified:
    raise ConfigError(f"{path}: listen.host: {host} would listen everywhere")
  port = listen["port"]
  if not isinstance(port, int) or isinstance(port, bool):
    raise ConfigError(f"{path}: listen.port: not an integer")
  if not 1 <= port <= 65535:
    raise ConfigError(f"{path}: listen.port: {port} is out of range")
  files = data["projects"]
  if not isinstance(files, list) or not files:
    raise ConfigError(f"{path}: projects: not a non-empty list")
  projects: list[Project] = []
  for i, file in enumerate(cast(list[object], files)):
    name = _str(path, f"projects[{i}]", file)
    projects.append(load_project(path.parent / name))
  ids = [p.id for p in projects]
  for i, id_ in enumerate(ids):
    if id_ in ids[:i]:
      raise ConfigError(f"{path}: projects: id {id_} is repeated")
  return Site(Listen(host, port), tuple(projects))


def load_project(path: Path) -> Project:
  """Load a project file.

  Args:
    path: The project file.

  Returns:
    The project.

  Raises:
    ConfigError: When the file fails a check.
  """
  data = _read(path)
  _keys(
    path,
    "",
    data,
    required={
      "id",
      "name",
      "color",
      "attempts_dir",
      "attempts",
      "record",
      "item",
    },
    optional={
      "states",
      "statuses",
      "columns",
      "session",
      "not_ended",
      "lock",
      "deliverables",
      "trials",
      "monitor",
    },
  )
  id_ = _str(path, "id", data["id"])
  if not _ID.fullmatch(id_):
    raise ConfigError(f"{path}: id: {id_!r} is not of [a-z0-9-]")
  color = _str(path, "color", data["color"])
  if not _COLOR.fullmatch(color):
    raise ConfigError(f"{path}: color: {color!r} is not #rrggbb")
  attempts = _attempts(path, _str(path, "attempts", data["attempts"]))
  dir_ = _str(path, "attempts_dir", data["attempts_dir"])
  attempts_dir = _attempts_dir(path, dir_)
  return Project(
    id=id_,
    name=_str(path, "name", data["name"]),
    color=color,
    attempts_dir=attempts_dir,
    attempts=attempts,
    record=_relative(path, "record", _str(path, "record", data["record"])),
    states=_entries(path, "states", data.get("states"), {"title"}),
    statuses=_statuses(path, data.get("statuses")),
    item=_item(path, data["item"], len(attempts)),
    columns=_columns(path, data.get("columns")),
    thinking=_thinking(path, data.get("session")),
    not_ended=_not_ended(path, data.get("not_ended")),
    lock=None
    if data.get("lock") is None
    else _relative(path, "lock", _str(path, "lock", data["lock"])),
    deliverables=_deliverables(path, data.get("deliverables")),
    trial_diff=_trial_diff(path, data.get("trials")),
    monitor=_monitor(path, data.get("monitor"), attempts_dir),
  )


def split_relative(path: str) -> tuple[str, ...] | None:
  """Split a relative path into its components, or refuse it.

  Args:
    path: A path relative to some root, "" for the root itself.

  Returns:
    Its components; None when it is absolute or holds an empty, "." or ".."
    component.
  """
  if path == "":
    return ()
  parts = tuple(path.split("/"))
  if any(part in ("", ".", "..") or "\0" in part for part in parts):
    return None
  return parts


def _repeated(node: yaml.Node | None) -> str | None:
  # safe_load keeps the last of a repeated key; a file may not repeat one.
  if isinstance(node, yaml.SequenceNode):
    items = cast(list[yaml.Node], node.value)
    return next(filter(None, map(_repeated, items)), None)
  if not isinstance(node, yaml.MappingNode):
    return None
  keys: set[str] = set()
  for key, value in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
    text = cast(str, key.value) if isinstance(key, yaml.ScalarNode) else ""
    if text and text in keys:
      return f"line {cast(int, key.start_mark.line) + 1}: key {text} repeated"
    keys.add(text)
    if found := _repeated(value):
      return found
  return None


def _read(path: Path) -> dict[str, object]:
  try:
    text = path.read_text(encoding="utf-8")
    repeated = _repeated(yaml.SafeLoader(text).get_single_node())
    data = cast(object, yaml.safe_load(text))
  except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
    raise ConfigError(f"{path}: {error}") from None
  if repeated:
    raise ConfigError(f"{path}: {repeated}")
  return _mapping(path, "", data)


def _mapping(path: Path, key: str, value: object) -> dict[str, object]:
  if not isinstance(value, dict):
    raise ConfigError(f"{path}: {key or 'the file'}: not a mapping")
  mapping = cast(dict[object, object], value)
  for k in mapping:
    if not isinstance(k, str):
      raise ConfigError(f"{path}: {key or 'the file'}: key {k!r} not a string")
  return cast(dict[str, object], mapping)


def _keys(
  path: Path,
  where: str,
  data: Mapping[str, object],
  required: set[str],
  optional: set[str],
) -> None:
  prefix = f"{where}." if where else ""
  for key in data:
    if key not in required | optional:
      raise ConfigError(f"{path}: {prefix}{key}: unknown key")
  for key in sorted(required):
    if key not in data:
      raise ConfigError(f"{path}: {prefix}{key}: missing")


def _str(path: Path, key: str, value: object) -> str:
  if not isinstance(value, str) or not value:
    raise ConfigError(f"{path}: {key}: not a non-empty string")
  return value


def _attempts_dir(path: Path, value: str) -> Path:
  # Absolute, so that it does not depend on the working directory later. It
  # is configuration: opened as given, a symlink in it included.
  found = Path(os.path.abspath(path.parent / value))
  if not found.is_dir():
    raise ConfigError(f"{path}: attempts_dir: {found} is not a directory")
  return found


def _item(path: Path, value: object, most: int) -> Item:
  item = _mapping(path, "item", value)
  _keys(path, "item", item, required={"depth", "title"}, optional=set())
  depth = item["depth"]
  if not isinstance(depth, int) or isinstance(depth, bool):
    raise ConfigError(f"{path}: item.depth: not an integer")
  if not 1 <= depth <= most:
    raise ConfigError(f"{path}: item.depth: {depth} is not from 1 to {most}")
  return Item(depth, _str(path, "item.title", item["title"]))


def _attempts(path: Path, value: str) -> tuple[str, ...]:
  parts = split_relative(value)
  if not parts or any("*" in p and p != "*" for p in parts):
    raise ConfigError(
      f"{path}: attempts: {value!r} is not plain and * components"
    )
  return parts


def _relative(path: Path, key: str, value: str) -> tuple[str, ...]:
  parts = split_relative(value)
  if not parts:
    raise ConfigError(f"{path}: {key}: {value!r} is not a relative path")
  return parts


def _entries(
  path: Path, key: str, value: object, fields: set[str]
) -> dict[str, dict[str, str]]:
  if value is None:
    return {}
  entries: dict[str, dict[str, str]] = {}
  for name, entry in _mapping(path, key, value).items():
    where = f"{key}.{name}"
    mapping = _mapping(path, where, entry)
    _keys(path, where, mapping, required=fields, optional=set())
    entries[name] = {f: _str(path, f"{where}.{f}", mapping[f]) for f in fields}
  return entries


def _statuses(path: Path, value: object) -> dict[str, dict[str, str]]:
  statuses = _entries(path, "statuses", value, {"label", "tone"})
  for name, entry in statuses.items():
    _one_of(path, f"statuses.{name}.tone", entry["tone"], TONES)
  return statuses


def _columns(path: Path, value: object) -> tuple[Column, ...]:
  if value is None:
    return ()
  if not isinstance(value, list):
    raise ConfigError(f"{path}: columns: not a list")
  columns: list[Column] = []
  for i, item in enumerate(cast(list[object], value)):
    where = f"columns[{i}]"
    entry = _mapping(path, where, item)
    _keys(path, where, entry, required={"title", "field"}, optional={"format"})
    field = entry["field"]
    if not isinstance(field, list) or not field:
      raise ConfigError(f"{path}: {where}.field: not a non-empty list")
    keys = tuple(
      _str(path, f"{where}.field", k) for k in cast(list[object], field)
    )
    format_ = _str(path, f"{where}.format", entry.get("format", "text"))
    _one_of(path, f"{where}.format", format_, FORMATS)
    title = _str(path, f"{where}.title", entry["title"])
    columns.append(Column(title, keys, format_))
  return tuple(columns)


def _deliverables(path: Path, value: object) -> tuple[Deliverable, ...]:
  if value is None:
    return ()
  if not isinstance(value, list):
    raise ConfigError(f"{path}: deliverables: not a list")
  found: list[Deliverable] = []
  for i, item in enumerate(cast(list[object], value)):
    where = f"deliverables[{i}]"
    entry = _mapping(path, where, item)
    fields = {"name", "kind", "title"}
    _keys(path, where, entry, required=fields, optional=set())
    name, kind, title = (
      _str(path, f"{where}.{f}", entry[f]) for f in ("name", "kind", "title")
    )
    if any(d.name == name for d in found):
      raise ConfigError(f"{path}: {where}.name: {name!r} repeated")
    found.append(Deliverable(name, kind, title))
  return tuple(found)


def _trial_diff(path: Path, value: object) -> tuple[str, ...] | None:
  if value is None:
    return None
  mapping = _mapping(path, "trials", value)
  _keys(path, "trials", mapping, required={"diff"}, optional=set())
  return _relative(
    path, "trials.diff", _str(path, "trials.diff", mapping["diff"])
  )


def _monitor(path: Path, value: object, attempts_dir: Path) -> Path | None:
  # Like attempts_dir, configuration opened as given; relative to
  # attempts_dir, so a copy of the file for another machine changes one key.
  # It is not checked here: a database the sampler has not written yet does
  # not stop the start, and the Monitor page says why it cannot be read.
  if value is None:
    return None
  mapping = _mapping(path, "monitor", value)
  _keys(path, "monitor", mapping, required={"db"}, optional=set())
  db = _str(path, "monitor.db", mapping["db"])
  if "\0" in db:
    raise ConfigError(f"{path}: monitor.db: holds a NUL")
  return Path(os.path.abspath(attempts_dir / db))


def _not_ended(path: Path, value: object) -> tuple[str, ...] | None:
  if value is None:
    return None
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: not_ended: not a non-empty list")
  return tuple(_str(path, "not_ended", w) for w in cast(list[object], value))


def _thinking(path: Path, value: object) -> str:
  session = {} if value is None else _mapping(path, "session", value)
  _keys(path, "session", session, required=set(), optional={"thinking"})
  thinking = _str(path, "session.thinking", session.get("thinking", "hidden"))
  _one_of(path, "session.thinking", thinking, THINKING)
  return thinking


def _one_of(path: Path, key: str, value: str, choices: tuple[str, ...]) -> None:
  if value not in choices:
    choice = ", ".join(choices)
    raise ConfigError(f"{path}: {key}: {value!r} is not one of {choice}")
