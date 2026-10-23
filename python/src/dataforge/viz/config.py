"""The site file and the project files the viewer reads at start.

A file loads only when it passes every check; one that fails raises
ConfigError naming the file and the key that is wrong.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
import ipaddress
import os
from pathlib import Path
import re
from typing import cast

import yaml

from dataforge import queue

TONES = ("good", "bad", "warn", "neutral")
"""The tones a status may take."""

FORMATS = ("text", "number", "usd", "duration", "time")
"""The formats a column may take."""

THINKING = ("collapsed", "expanded", "hidden")
"""How a session view may first show thinking blocks."""

WHEN = ("idle", "stopped")
"""The conditions an action may take besides its own key's: idle, no attempt
of the item runs (its lock is not held); stopped, the item's newest attempt
may have stopped (it has not ended and the lock is not held), or has ended
with a status the project calls resumable, and the lock is not held."""

PLACEHOLDER = "{item}"
"""The one placeholder of an action's argv and key: the data item's ID."""

SUBJECTS = ("item", "attempt")
"""What the item label of a job another tool queues names: a data item's ID,
or an attempt's."""

PARAM_FORMATS = ("number", "usd")
"""The formats an action's parameter may take."""

NUMBER = re.compile(r"-?[0-9]+(\.[0-9]{1,6})?")
"""A parameter's value as a request carries it: a plain decimal number, with
no sign but a leading minus, at most 6 decimal places, no exponent and no
space; a page's number, a double, writes each such value in this form."""

NUMBER_LIMIT = 15
"""The most characters a parameter's value may have: at most 15 digits, which
a double holds exactly, so that a page reads each value as the server does."""

_ID = re.compile(r"[a-z0-9-]+")
_FLAG = re.compile(r"--[a-z0-9][a-z0-9-]*")
# The query parameter a submit names its item by, which no parameter of an
# action may take.
_ITEM_PARAM = "item"
# The frontend's own pages, at /monitor and /queue, where a project's home
# page would be.
_PAGES = ("monitor", "queue")
# The attempt page's own tab, which no tab of a project may take.
_RUNS = "runs"
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
    field: The keys its value is at, into the record or into file.
    format: One of FORMATS.
    file: A JSON file to read field from in place of the record, relative to
      the attempt's directory, or with tab to the tab's first version; None
      for the record.
    tab: The name of a tab of the project whose first version file is in;
      None when file is relative to the attempt's directory.
  """

  title: str
  field: tuple[str, ...]
  format: str
  file: tuple[str, ...] | None = None
  tab: str | None = None


@dataclass(frozen=True, slots=True)
class TabField:
  """A value a tab shows above its Markdown, read from a JSON file.

  Attributes:
    title: Its heading.
    file: The JSON file, relative to the version shown.
    field: The keys into the file its value is at.
    format: One of FORMATS.
  """

  title: str
  file: tuple[str, ...]
  field: tuple[str, ...]
  format: str


@dataclass(frozen=True, slots=True)
class TabPart:
  """A Markdown file a tab shows from each of its versions.

  Attributes:
    name: Its name, in the URL; of [a-z0-9-].
    title: Its title.
    file: The Markdown file, relative to each version's directory.
    fields: The values shown above the Markdown, in order.
  """

  name: str
  title: str
  file: tuple[str, ...]
  fields: tuple[TabField, ...] = ()


@dataclass(frozen=True, slots=True)
class Tab:
  """A tab of the attempt page that shows Markdown files of the attempt.

  Attributes:
    name: Its name, in the URL; of [a-z0-9-].
    title: Its title.
    versions: The patterns of the directories it shows, each one name in
      the attempt's directory, in order; a pattern may hold "*", which
      matches any run of characters, and matches no name that starts with
      "." unless it does too.
    parts: The Markdown files it shows from each version, in order: one,
      named and titled as the tab, for a tab of one file; else two or more,
      each an entry of the tab's column.
    version_title: The value a version is named by in the tab's menu, read
      from a JSON file in it; None to name it by its directory.
    job: The action of the jobs, of the project's jobs and of subject
      "attempt", that make its versions; None when it names none.
  """

  name: str
  title: str
  versions: tuple[str, ...]
  parts: tuple[TabPart, ...]
  version_title: TabField | None = None
  job: str | None = None


@dataclass(frozen=True, slots=True)
class Item:
  """How attempts group into data items.

  Attributes:
    depth: How many leading components of an attempt ID name its item.
    title: The heading of the item column.
    pattern: What a data item's ID must match, as a whole, for an action to
      take it; None when the dataset names none, and then it has no actions.
  """

  depth: int
  title: str
  pattern: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class Param:
  """A number a person may set when they submit an action.

  Attributes:
    name: Its name, the query parameter that carries it; of [a-z0-9-] and
      never "item".
    label: The text beside its input.
    flag: The option the command takes it with, as "<flag>=<value>"; "--"
      and [a-z0-9-].
    min: The least value it may take.
    max: The greatest value it may take.
    default: Its value when the submit does not name it, from min to max.
    format: How a page shows it, one of PARAM_FORMATS.
  """

  name: str
  label: str
  flag: str
  min: Decimal
  max: Decimal
  default: Decimal
  format: str

  def public(self) -> dict[str, object]:
    """Return what a page reads: all but the flag.

    Returns:
      The parameter as JSON data, its bounds and default as numbers.
    """
    return {
      "name": self.name,
      "label": self.label,
      "min": float(self.min),
      "max": float(self.max),
      "default": float(self.default),
      "format": self.format,
    }


def number(text: str) -> Decimal | None:
  """Read a parameter's value as a request carries it.

  Args:
    text: The value.

  Returns:
    The number; None when the text is longer than NUMBER_LIMIT or is not
    NUMBER as a whole.
  """
  if len(text) > NUMBER_LIMIT or not NUMBER.fullmatch(text):
    return None
  return Decimal(text)


def plain(value: Decimal) -> str:
  """Write a number as a command takes it: NUMBER, with no trailing zero.

  Args:
    value: A finite number.

  Returns:
    The number in plain decimal notation, as "75", "12.5" or "0".
  """
  # normalize() drops trailing zeros, and with them -0's sign.
  return "0" if value == 0 else format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class Action:
  """What a data item's page may put in a queue, as the project file says.

  Attributes:
    name: Its name, in the URL that submits it; of [a-z0-9-].
    label: Its button's text.
    queue: The queue it goes to, one of the site's queue file.
    argv: The command, each argument with PLACEHOLDER where the item's ID
      goes, never in the first.
    cwd: The absolute directory the command runs in.
    memory_mib: The memory the job reserves, in MiB.
    key: The job's key with PLACEHOLDER: no two jobs of one key are queued or
      running at once.
    when: One of WHEN, or None for the key's condition alone.
    params: The numbers a person may set, each added to argv as its flag
      and value, in order.
  """

  name: str
  label: str
  queue: str
  argv: tuple[str, ...]
  cwd: Path
  memory_mib: int
  key: str
  when: str | None
  params: tuple[Param, ...] = ()


@dataclass(frozen=True, slots=True)
class JobKind:
  """Jobs another tool puts in the queue for a project, which a page shows.

  Attributes:
    action: Their action label.
    label: How a page names the action.
    queue: The queue they go to, one of the site's queue file.
    subject: What their item label names, one of SUBJECTS.
  """

  action: str
  label: str
  queue: str
  subject: str


@dataclass(frozen=True, slots=True)
class Deliverable:
  """A deliverable the dataset's declaration names, as a record names it.

  Attributes:
    name: The name of its entry in a record's deliverables.
    kind: Its kind, which picks its view.
    title: Its title.
    no_trace: For a deliverable of trials, why a trial of it that has no
      trace has none, shown in its place; None for the viewer's own words.
    default: Whether it names its kind's tab, and opens first in it, while
      the attempt has reached no deliverable of the kind.
  """

  name: str
  kind: str
  title: str
  no_trace: str | None = None
  default: bool = False


@dataclass(frozen=True, slots=True)
class TaskSource:
  """Where the Summary reads the task.toml of an attempt's task.

  Attributes:
    file: The task.toml's path: in the directory of deliverable, as the
      attempt's record resolves it, or in the attempt's directory when
      deliverable is None.
    deliverable: The name of a deliverable of kind "files" whose directory
      holds the task, so that a running attempt shows its latest; None for a
      path in the attempt's directory.
  """

  file: tuple[str, ...]
  deliverable: str | None = None


@dataclass(frozen=True, slots=True)
class Trials:
  """What the trials view reads in a trial's directory besides its record.

  Attributes:
    diff: The path of a trial's diff in its directory; None when the
      dataset names none.
    tests: The path of a trial's test results, a CTRF report, in its
      directory; None when the dataset names none.
    kinds: Each test kind's label, by the kind a test's labels give.
  """

  diff: tuple[str, ...] | None = None
  tests: tuple[str, ...] | None = None
  kinds: dict[str, dict[str, str]] = field(default_factory=dict)


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
    resumable: The statuses of an attempt that has ended and that an action
      with when "stopped" may still go on with; none by default.
    lock: The path of the lock file in an item's directory; None when the
      dataset names none.
    deliverables: The deliverables an attempt has once it ends, in order.
    kinds: The title of a deliverable kind's tab, by kind; a kind with none
      takes the title of its deliverable the tab opens on.
    trials: What the trials view reads in a trial's directory; None when
      the dataset names nothing.
    definition: The path of the definition snapshot in an attempt's
      directory; None when the dataset names none.
    task: Where the task.toml of an attempt's task is, whose metadata the
      Summary shows; None when the dataset names none.
    monitor: The resource monitor's SQLite database; None when the dataset
      names none.
    monitor_shown: The database as a page may show it: monitor.db as the
      project file writes it, or its file name alone when that is absolute,
      so that no page shows where the server keeps it; None with monitor.
    actions: What a data item's page may put in a queue, in order.
    jobs: The jobs another tool puts in the queue for the project.
    tabs: The attempt page's tabs of Markdown files, in order.
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
  resumable: tuple[str, ...] = ()
  lock: tuple[str, ...] | None = None
  deliverables: tuple[Deliverable, ...] = ()
  kinds: dict[str, dict[str, str]] = field(default_factory=dict)
  trials: Trials | None = None
  definition: tuple[str, ...] | None = None
  task: TaskSource | None = None
  monitor: Path | None = None
  monitor_shown: str | None = None
  actions: tuple[Action, ...] = ()
  jobs: tuple[JobKind, ...] = ()
  tabs: tuple[Tab, ...] = ()

  def public(self) -> dict[str, object]:
    """Return what the frontend reads: all but where the files are.

    Returns:
      The project as JSON data, without attempts_dir, attempts, record,
      lock, the monitor's path, item.pattern and each action's command,
      directory, memory and key.
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
      "tabs": [
        {
          "name": t.name,
          "title": t.title,
          "versions": list(t.versions),
          "parts": [
            {
              "name": part.name,
              "title": part.title,
              "file": "/".join(part.file),
              "fields": [
                {"title": f.title, "format": f.format} for f in part.fields
              ],
            }
            for part in t.parts
          ],
          "version_title": None
          if t.version_title is None
          else {"format": t.version_title.format},
          "job": t.job,
        }
        for t in self.tabs
      ],
      "session": {"thinking": self.thinking},
      "not_ended": None if self.not_ended is None else list(self.not_ended),
      "deliverables": [
        {
          "name": d.name,
          "kind": d.kind,
          "title": d.title,
          "no_trace": d.no_trace,
          "default": d.default,
        }
        for d in self.deliverables
      ],
      "kinds": self.kinds,
      "trials": None if self.trials is None else _public_trials(self.trials),
      "definition": None
      if self.definition is None
      else "/".join(self.definition),
      "task": None
      if self.task is None
      else {
        "file": "/".join(self.task.file),
        "deliverable": self.task.deliverable,
      },
      "monitor": self.monitor is not None,
      "actions": [
        {"name": a.name, "label": a.label, "queue": a.queue}
        for a in self.actions
      ],
      "jobs": [
        {
          "action": j.action,
          "label": j.label,
          "queue": j.queue,
          "subject": j.subject,
        }
        for j in self.jobs
      ],
    }


@dataclass(frozen=True, slots=True)
class Site:
  """The site file, with its projects loaded.

  Attributes:
    listen: The address the server binds.
    projects: The projects, in the file's order.
    queue: The job queue's configuration; None when the site names none.
  """

  listen: Listen
  projects: tuple[Project, ...]
  queue: queue.Config | None = None


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
  _keys(path, "", data, required={"listen", "projects"}, optional={"queue"})
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
  config = _queue(path, data.get("queue"))
  for project in projects:
    _check_queues(path, project, config)
  return Site(Listen(host, port), tuple(projects), config)


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
      "resumable",
      "lock",
      "deliverables",
      "kinds",
      "trials",
      "definition",
      "task",
      "monitor",
      "actions",
      "jobs",
      "tabs",
    },
  )
  id_ = _str(path, "id", data["id"])
  if not _ID.fullmatch(id_):
    raise ConfigError(f"{path}: id: {id_!r} is not of [a-z0-9-]")
  if id_ in _PAGES:
    raise ConfigError(f"{path}: id: {id_!r} is the name of a page")
  color = _str(path, "color", data["color"])
  if not _COLOR.fullmatch(color):
    raise ConfigError(f"{path}: color: {color!r} is not #rrggbb")
  attempts = _attempts(path, _str(path, "attempts", data["attempts"]))
  dir_ = _str(path, "attempts_dir", data["attempts_dir"])
  attempts_dir = _attempts_dir(path, dir_)
  monitor = _monitor(path, data.get("monitor"), attempts_dir)
  item = _item(path, data["item"], len(attempts))
  lock = (
    None
    if data.get("lock") is None
    else _relative(path, "lock", _str(path, "lock", data["lock"]))
  )
  actions = _actions(path, data.get("actions"), item, lock)
  statuses = _statuses(path, data.get("statuses"))
  not_ended = _not_ended(path, data.get("not_ended"))
  deliverables = _deliverables(path, data.get("deliverables"))
  jobs = _jobs(path, data.get("jobs"), actions)
  tabs = _tabs(path, data.get("tabs"), deliverables, jobs)
  return Project(
    id=id_,
    name=_str(path, "name", data["name"]),
    color=color,
    attempts_dir=attempts_dir,
    attempts=attempts,
    record=_relative(path, "record", _str(path, "record", data["record"])),
    states=_entries(path, "states", data.get("states"), {"title"}),
    statuses=statuses,
    item=item,
    columns=_columns(path, data.get("columns"), tabs),
    thinking=_thinking(path, data.get("session")),
    not_ended=not_ended,
    resumable=_resumable(path, data.get("resumable"), statuses, not_ended),
    lock=lock,
    deliverables=deliverables,
    kinds=_kinds(path, data.get("kinds"), deliverables),
    trials=_trials(path, data.get("trials")),
    definition=None
    if data.get("definition") is None
    else _relative(
      path, "definition", _str(path, "definition", data["definition"])
    ),
    task=_task(path, data.get("task"), deliverables),
    monitor=None if monitor is None else monitor[0],
    monitor_shown=None if monitor is None else monitor[1],
    actions=actions,
    jobs=jobs,
    tabs=tabs,
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
  _keys(path, "item", item, required={"depth", "title"}, optional={"pattern"})
  depth = item["depth"]
  if not isinstance(depth, int) or isinstance(depth, bool):
    raise ConfigError(f"{path}: item.depth: not an integer")
  if not 1 <= depth <= most:
    raise ConfigError(f"{path}: item.depth: {depth} is not from 1 to {most}")
  pattern = None
  if "pattern" in item:
    text = _str(path, "item.pattern", item["pattern"])
    try:
      pattern = re.compile(text)
    except re.error as error:
      raise ConfigError(f"{path}: item.pattern: {error}") from None
  return Item(depth, _str(path, "item.title", item["title"]), pattern)


def _positive(path: Path, key: str, value: object) -> int:
  if not isinstance(value, int) or isinstance(value, bool):
    raise ConfigError(f"{path}: {key}: not an integer")
  if value < 1:
    raise ConfigError(f"{path}: {key}: {value} is not from 1")
  return value


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


def _columns(
  path: Path, value: object, tabs: tuple[Tab, ...]
) -> tuple[Column, ...]:
  if value is None:
    return ()
  if not isinstance(value, list):
    raise ConfigError(f"{path}: columns: not a list")
  columns: list[Column] = []
  for i, item in enumerate(cast(list[object], value)):
    where = f"columns[{i}]"
    entry = _mapping(path, where, item)
    _keys(
      path,
      where,
      entry,
      required={"title", "field"},
      optional={"format", "file", "tab"},
    )
    file = None
    if "file" in entry:
      file = _relative(
        path, f"{where}.file", _str(path, f"{where}.file", entry["file"])
      )
    tab = None
    if "tab" in entry:
      tab = _str(path, f"{where}.tab", entry["tab"])
      if file is None:
        raise ConfigError(f"{path}: {where}.file: missing, and tab names one")
      if not any(t.name == tab for t in tabs):
        raise ConfigError(f"{path}: {where}.tab: {tab!r} is not a tab's name")
    columns.append(
      Column(
        _str(path, f"{where}.title", entry["title"]),
        _field_keys(path, where, entry["field"]),
        _format(path, where, entry),
        file,
        tab,
      )
    )
  return tuple(columns)


def _field_keys(path: Path, where: str, value: object) -> tuple[str, ...]:
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: {where}.field: not a non-empty list")
  return tuple(
    _str(path, f"{where}.field", k) for k in cast(list[object], value)
  )


def _format(path: Path, where: str, entry: Mapping[str, object]) -> str:
  format_ = _str(path, f"{where}.format", entry.get("format", "text"))
  _one_of(path, f"{where}.format", format_, FORMATS)
  return format_


def _tabs(
  path: Path,
  value: object,
  deliverables: tuple[Deliverable, ...],
  jobs: tuple[JobKind, ...],
) -> tuple[Tab, ...]:
  if value is None:
    return ()
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: tabs: not a non-empty list")
  # A tab's name is its key in the URL, beside Runs and each kind's.
  taken = {_RUNS, *(d.kind for d in deliverables)}
  found: list[Tab] = []
  for i, item in enumerate(cast(list[object], value)):
    where = f"tabs[{i}]"
    entry = _mapping(path, where, item)
    _keys(
      path,
      where,
      entry,
      required={"name", "title", "versions"},
      optional={"file", "fields", "parts", "version_title", "job"},
    )
    name = _str(path, f"{where}.name", entry["name"])
    if not _ID.fullmatch(name):
      raise ConfigError(f"{path}: {where}.name: {name!r} is not of [a-z0-9-]")
    if name in taken:
      raise ConfigError(
        f"{path}: {where}.name: {name!r} is repeated, runs or a kind's"
      )
    taken.add(name)
    title = _str(path, f"{where}.title", entry["title"])
    if "parts" in entry:
      for key in ("file", "fields"):
        if key in entry:
          raise ConfigError(f"{path}: {where}.{key}: given with parts")
      parts = _tab_parts(path, f"{where}.parts", entry["parts"])
    elif "file" not in entry:
      raise ConfigError(f"{path}: {where}.file: missing, and no parts")
    else:
      parts = (_tab_part(path, where, entry, name, title),)
    found.append(
      Tab(
        name=name,
        title=title,
        versions=_versions(path, f"{where}.versions", entry["versions"]),
        parts=parts,
        version_title=_version_title(
          path, f"{where}.version_title", entry.get("version_title"), title
        ),
        job=_tab_job(path, f"{where}.job", entry.get("job"), jobs),
      )
    )
  return tuple(found)


def _tab_job(
  path: Path, where: str, value: object, jobs: tuple[JobKind, ...]
) -> str | None:
  if value is None:
    return None
  job = _str(path, where, value)
  if not any(j.action == job and j.subject == "attempt" for j in jobs):
    raise ConfigError(
      f"{path}: {where}: {job!r} is not a job of subject attempt"
    )
  return job


def _version_title(
  path: Path, where: str, value: object, title: str
) -> TabField | None:
  if value is None:
    return None
  entry = _mapping(path, where, value)
  _keys(path, where, entry, required={"file", "field"}, optional={"format"})
  return TabField(
    title=title,
    file=_relative(
      path, f"{where}.file", _str(path, f"{where}.file", entry["file"])
    ),
    field=_field_keys(path, where, entry["field"]),
    format=_format(path, where, entry),
  )


def _jobs(
  path: Path, value: object, actions: tuple[Action, ...]
) -> tuple[JobKind, ...]:
  if value is None:
    return ()
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: jobs: not a non-empty list")
  found: list[JobKind] = []
  for i, item in enumerate(cast(list[object], value)):
    where = f"jobs[{i}]"
    entry = _mapping(path, where, item)
    _keys(
      path,
      where,
      entry,
      required={"action", "label", "queue"},
      optional={"subject"},
    )
    action = _str(path, f"{where}.action", entry["action"])
    if any(j.action == action for j in found) or any(
      a.name == action for a in actions
    ):
      raise ConfigError(
        f"{path}: {where}.action: {action!r} repeated or an action's"
      )
    subject = _str(path, f"{where}.subject", entry.get("subject", "item"))
    _one_of(path, f"{where}.subject", subject, SUBJECTS)
    found.append(
      JobKind(
        action=action,
        label=_str(path, f"{where}.label", entry["label"]),
        queue=_str(path, f"{where}.queue", entry["queue"]),
        subject=subject,
      )
    )
  return tuple(found)


def _tab_parts(path: Path, where: str, value: object) -> tuple[TabPart, ...]:
  # One file is written as the tab's own file, so a tab of parts has two.
  if not isinstance(value, list) or len(cast(list[object], value)) < 2:
    raise ConfigError(f"{path}: {where}: not a list of two or more")
  found: list[TabPart] = []
  for i, item in enumerate(cast(list[object], value)):
    at = f"{where}[{i}]"
    entry = _mapping(path, at, item)
    _keys(
      path, at, entry, required={"name", "title", "file"}, optional={"fields"}
    )
    name = _str(path, f"{at}.name", entry["name"])
    if not _ID.fullmatch(name):
      raise ConfigError(f"{path}: {at}.name: {name!r} is not of [a-z0-9-]")
    if any(p.name == name for p in found):
      raise ConfigError(f"{path}: {at}.name: {name!r} is repeated")
    title = _str(path, f"{at}.title", entry["title"])
    found.append(_tab_part(path, at, entry, name, title))
  return tuple(found)


def _tab_part(
  path: Path, where: str, entry: Mapping[str, object], name: str, title: str
) -> TabPart:
  return TabPart(
    name=name,
    title=title,
    file=_relative(
      path, f"{where}.file", _str(path, f"{where}.file", entry["file"])
    ),
    fields=_tab_fields(path, f"{where}.fields", entry.get("fields")),
  )


def _versions(path: Path, where: str, value: object) -> tuple[str, ...]:
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: {where}: not a non-empty list")
  found: list[str] = []
  for v in cast(list[object], value):
    pattern = _str(path, where, v)
    if split_relative(pattern) != (pattern,):
      raise ConfigError(
        f"{path}: {where}: {pattern!r} is not one name in the attempt"
      )
    found.append(pattern)
  return tuple(found)


def _tab_fields(path: Path, where: str, value: object) -> tuple[TabField, ...]:
  if value is None:
    return ()
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: {where}: not a non-empty list")
  found: list[TabField] = []
  for i, item in enumerate(cast(list[object], value)):
    at = f"{where}[{i}]"
    entry = _mapping(path, at, item)
    _keys(
      path,
      at,
      entry,
      required={"title", "file", "field"},
      optional={"format"},
    )
    found.append(
      TabField(
        title=_str(path, f"{at}.title", entry["title"]),
        file=_relative(
          path, f"{at}.file", _str(path, f"{at}.file", entry["file"])
        ),
        field=_field_keys(path, at, entry["field"]),
        format=_format(path, at, entry),
      )
    )
  return tuple(found)


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
    _keys(path, where, entry, required=fields, optional={"no_trace", "default"})
    name, kind, title = (
      _str(path, f"{where}.{f}", entry[f]) for f in ("name", "kind", "title")
    )
    if any(d.name == name for d in found):
      raise ConfigError(f"{path}: {where}.name: {name!r} repeated")
    no_trace = entry.get("no_trace")
    if no_trace is not None:
      no_trace = _str(path, f"{where}.no_trace", no_trace)
      if kind != "trials":
        raise ConfigError(f"{path}: {where}.no_trace: for kind trials only")
    default = entry.get("default", False)
    if not isinstance(default, bool):
      raise ConfigError(f"{path}: {where}.default: not true or false")
    if default and any(d.kind == kind and d.default for d in found):
      other = f"another deliverable of kind {kind!r} is the default"
      raise ConfigError(f"{path}: {where}.default: {other}")
    found.append(
      Deliverable(name, kind, title, no_trace=no_trace, default=default)
    )
  return tuple(found)


def _task(
  path: Path, value: object, deliverables: tuple[Deliverable, ...]
) -> TaskSource | None:
  if value is None:
    return None
  if not isinstance(value, dict):
    return TaskSource(_relative(path, "task", _str(path, "task", value)))
  mapping = _mapping(path, "task", cast(object, value))
  fields = {"deliverable", "file"}
  _keys(path, "task", mapping, required=fields, optional=set())
  name = _str(path, "task.deliverable", mapping["deliverable"])
  file = _str(path, "task.file", mapping["file"])
  found = next((d for d in deliverables if d.name == name), None)
  if found is None:
    raise ConfigError(f"{path}: task.deliverable: {name!r} is not declared")
  if found.kind != "files":
    why = f"{name!r} is of kind {found.kind!r}, not files"
    raise ConfigError(f"{path}: task.deliverable: {why}")
  return TaskSource(_relative(path, "task.file", file), name)


def _kinds(
  path: Path, value: object, deliverables: tuple[Deliverable, ...]
) -> dict[str, dict[str, str]]:
  kinds = _entries(path, "kinds", value, {"title"})
  declared = {d.kind for d in deliverables}
  for kind in kinds:
    if kind not in declared:
      raise ConfigError(f"{path}: kinds.{kind}: no deliverable of this kind")
  return kinds


def _trials(path: Path, value: object) -> Trials | None:
  if value is None:
    return None
  mapping = _mapping(path, "trials", value)
  _keys(
    path, "trials", mapping, required=set(), optional={"diff", "tests", "kinds"}
  )
  paths = {
    key: _relative(path, f"trials.{key}", _str(path, f"trials.{key}", given))
    for key in ("diff", "tests")
    if (given := mapping.get(key)) is not None
  }
  if not paths:
    raise ConfigError(f"{path}: trials: names neither diff nor tests")
  kinds = mapping.get("kinds")
  if kinds is not None and "tests" not in paths:
    raise ConfigError(f"{path}: trials.kinds: without trials.tests")
  return Trials(
    diff=paths.get("diff"),
    tests=paths.get("tests"),
    kinds=_entries(path, "trials.kinds", kinds, {"label"}),
  )


def _public_trials(trials: Trials) -> dict[str, object]:
  return {
    "diff": None if trials.diff is None else "/".join(trials.diff),
    "tests": None if trials.tests is None else "/".join(trials.tests),
    "kinds": trials.kinds,
  }


def _monitor(
  path: Path, value: object, attempts_dir: Path
) -> tuple[Path, str] | None:
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
  shown = Path(db).name if os.path.isabs(db) else db
  return Path(os.path.abspath(attempts_dir / db)), shown


def _check_queues(
  path: Path, project: Project, config: queue.Config | None
) -> None:
  named = [
    *((f"actions[{i}]", a.queue) for i, a in enumerate(project.actions)),
    *((f"jobs[{i}]", j.queue) for i, j in enumerate(project.jobs)),
  ]
  for at, name in named:
    where = f"project {project.id}: {at}.queue"
    if config is None:
      raise ConfigError(f"{path}: queue: missing, and {where} names one")
    if name not in config.queues:
      raise ConfigError(
        f"{path}: queue: {where}: {name!r} is not in the queue file"
      )


def _queue(path: Path, value: object) -> queue.Config | None:
  if value is None:
    return None
  mapping = _mapping(path, "queue", value)
  _keys(path, "queue", mapping, required={"config"}, optional=set())
  file = path.parent / _str(path, "queue.config", mapping["config"])
  try:
    return queue.load_config(file)
  except queue.ConfigError as error:
    raise ConfigError(f"{path}: queue.config: {error}") from None


def _actions(
  path: Path, value: object, item: Item, lock: tuple[str, ...] | None
) -> tuple[Action, ...]:
  if value is None:
    return ()
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: actions: not a non-empty list")
  if item.pattern is None:
    raise ConfigError(f"{path}: item.pattern: missing, and there are actions")
  found: list[Action] = []
  for i, entry in enumerate(cast(list[object], value)):
    action = _action(path, f"actions[{i}]", entry)
    if any(a.name == action.name for a in found):
      raise ConfigError(f"{path}: actions[{i}].name: {action.name!r} repeated")
    if action.when is not None and lock is None:
      raise ConfigError(
        f"{path}: actions[{i}].when: {action.when} needs the project's lock"
      )
    found.append(action)
  return tuple(found)


def _action(path: Path, where: str, value: object) -> Action:
  entry = _mapping(path, where, value)
  _keys(
    path,
    where,
    entry,
    required={"name", "label", "queue", "argv", "cwd", "memory_mib", "key"},
    optional={"when", "params"},
  )
  name = _str(path, f"{where}.name", entry["name"])
  if not _ID.fullmatch(name):
    raise ConfigError(f"{path}: {where}.name: {name!r} is not of [a-z0-9-]")
  argv = entry["argv"]
  if not isinstance(argv, list) or not argv:
    raise ConfigError(f"{path}: {where}.argv: not a non-empty list")
  args = tuple(
    _template(path, f"{where}.argv", a) for a in cast(list[object], argv)
  )
  if PLACEHOLDER in args[0]:
    raise ConfigError(f"{path}: {where}.argv: the command is {PLACEHOLDER}")
  key = _template(path, f"{where}.key", entry["key"])
  if PLACEHOLDER not in key:
    raise ConfigError(f"{path}: {where}.key: holds no {PLACEHOLDER}")
  cwd = _str(path, f"{where}.cwd", entry["cwd"])
  if not os.path.isabs(cwd) or not os.path.isdir(cwd):
    raise ConfigError(
      f"{path}: {where}.cwd: {cwd!r} is not an absolute directory"
    )
  when = None
  if "when" in entry:
    when = _str(path, f"{where}.when", entry["when"])
    _one_of(path, f"{where}.when", when, WHEN)
  return Action(
    name=name,
    label=_str(path, f"{where}.label", entry["label"]),
    queue=_str(path, f"{where}.queue", entry["queue"]),
    argv=args,
    cwd=Path(cwd),
    memory_mib=_positive(path, f"{where}.memory_mib", entry["memory_mib"]),
    key=key,
    when=when,
    params=_params(path, f"{where}.params", entry.get("params"), args),
  )


def _params(
  path: Path, where: str, value: object, argv: tuple[str, ...]
) -> tuple[Param, ...]:
  if value is None:
    return ()
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: {where}: not a non-empty list")
  found: list[Param] = []
  for i, entry in enumerate(cast(list[object], value)):
    param = _param(path, f"{where}[{i}]", entry)
    if any(p.name == param.name for p in found):
      raise ConfigError(f"{path}: {where}[{i}].name: {param.name!r} repeated")
    # Once in the command, so that what the person set is what it reads.
    if any(p.flag == param.flag for p in found) or any(
      a == param.flag or a.startswith(f"{param.flag}=") for a in argv
    ):
      raise ConfigError(
        f"{path}: {where}[{i}].flag: {param.flag!r} already in the command"
      )
    found.append(param)
  return tuple(found)


def _param(path: Path, where: str, value: object) -> Param:
  entry = _mapping(path, where, value)
  _keys(
    path,
    where,
    entry,
    required={"name", "label", "flag", "min", "max", "default"},
    optional={"format"},
  )
  name = _str(path, f"{where}.name", entry["name"])
  if not _ID.fullmatch(name) or name == _ITEM_PARAM:
    raise ConfigError(
      f"{path}: {where}.name: {name!r} is not of [a-z0-9-] or is"
      + f" {_ITEM_PARAM!r}"
    )
  flag = _str(path, f"{where}.flag", entry["flag"])
  if not _FLAG.fullmatch(flag):
    raise ConfigError(f"{path}: {where}.flag: {flag!r} is not -- and [a-z0-9-]")
  least = _number(path, f"{where}.min", entry["min"])
  most = _number(path, f"{where}.max", entry["max"])
  default = _number(path, f"{where}.default", entry["default"])
  if not least <= default <= most:
    raise ConfigError(
      f"{path}: {where}: not min <= default <= max:"
      + f" {plain(least)}, {plain(default)}, {plain(most)}"
    )
  form = _str(path, f"{where}.format", entry.get("format", "number"))
  _one_of(path, f"{where}.format", form, PARAM_FORMATS)
  return Param(
    name=name,
    label=_str(path, f"{where}.label", entry["label"]),
    flag=flag,
    min=least,
    max=most,
    default=default,
    format=form,
  )


def _number(path: Path, key: str, value: object) -> Decimal:
  # A bound or a default is one a request could carry too, so a page can
  # send it back as it reads it.
  if not isinstance(value, int | float) or isinstance(value, bool):
    raise ConfigError(f"{path}: {key}: not a number")
  found = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
  if not found.is_finite() or number(plain(found)) is None:
    raise ConfigError(
      f"{path}: {key}: {value!r} is not a decimal number of at most"
      + f" {NUMBER_LIMIT} characters and 6 decimal places"
    )
  return found


def _template(path: Path, key: str, value: object) -> str:
  # PLACEHOLDER is the only brace a template may hold, so that what replaces
  # it is all that differs from one item to the next.
  text = _str(path, key, value)
  rest = text.replace(PLACEHOLDER, "")
  if "{" in rest or "}" in rest or "\0" in text:
    raise ConfigError(
      f"{path}: {key}: {text!r} holds a brace or NUL besides {PLACEHOLDER}"
    )
  return text


def _not_ended(path: Path, value: object) -> tuple[str, ...] | None:
  if value is None:
    return None
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: not_ended: not a non-empty list")
  return tuple(_str(path, "not_ended", w) for w in cast(list[object], value))


def _resumable(
  path: Path,
  value: object,
  statuses: dict[str, dict[str, str]],
  not_ended: tuple[str, ...] | None,
) -> tuple[str, ...]:
  if value is None:
    return ()
  if not isinstance(value, list) or not value:
    raise ConfigError(f"{path}: resumable: not a non-empty list")
  words = tuple(_str(path, "resumable", w) for w in cast(list[object], value))
  for word in words:
    if word not in statuses:
      raise ConfigError(f"{path}: resumable: {word!r} is not one of statuses")
    if word in (not_ended or ()):
      raise ConfigError(f"{path}: resumable: {word!r} is also in not_ended")
  return words


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
