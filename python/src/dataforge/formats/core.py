"""Formats, their versions and fingerprints, and where they are registered.

A format is something processes of two installs share while both run: a
table, a file, a directory's layout, the way one process starts another. Each
has a name, an integer version and a fingerprint, the facts about it that its
code gives. Two installs whose formats have the same names and versions may
run side by side; any other difference, one format missing or one version
not the same, may not.

A fingerprint maps a key to a fact. Keys are paths whose parts `/` divides,
such as `table:jobs/column:pid`, and a key's ancestors are its leading parts.
A fact that is new, with no fact of its key before, is additive when the code
of the version before reads and writes beside it as it did, such as a column
that may be NULL: its fact says so, or one of its ancestors is new as well and
additive, such as each column of a new table. Every other change, a fact
removed or changed or a new one that is not additive, needs a new version.
An additive change keeps the version only of a format whose readers of the
version before read what it adds without failing, such as a query that
names its columns; a format whose readers fail on a key they do not know
says so (`Format.additive`), and every change of it needs a new version.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import importlib.metadata
import re
from typing import cast, override

GROUP = "dataforge.formats"
"""The entry point group through which a project registers its formats: each
entry point names a callable that returns an iterable of Format."""

_NAME = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")


@dataclass(frozen=True, slots=True)
class Fact:
  """One fact of a fingerprint.

  Attributes:
    value: What the code gives for it.
    additive: Whether the code of the version before reads and writes
      beside it as it did, when the fact is new.
  """

  value: str
  additive: bool = False


type Fingerprint = dict[str, Fact]


@dataclass(frozen=True, slots=True)
class Format:
  """A format that the processes of two installs share.

  Attributes:
    name: Its name, dotted, the first part its owner's, such as
      `dataforge.queue.table`.
    version: Its version, at least 1; raised for each change that is not
      additive, of its fingerprint or of what its code means by it.
    fingerprint: Gives its facts from the code; it may write temporary
      files, and touches nothing else.
    about: What it is and where its code is, in a line.
    additive: Whether an additive change keeps the version: its readers of
      the version before read what the change adds without failing.
  """

  name: str
  version: int
  fingerprint: Callable[[], Fingerprint]
  about: str
  additive: bool = True

  def keeps_version(self, found: Iterable[Change]) -> bool:
    """Tell whether changes of the fingerprint leave the version as it is.

    Args:
      found: The changes.

    Returns:
      True when there is none, or each is additive and so is the format.
    """
    found = list(found)
    return not found or (self.additive and all(c.additive for c in found))


@dataclass(frozen=True, slots=True)
class Change:
  """A difference between two fingerprints of a format.

  Attributes:
    key: The fact's key.
    kind: "added", "removed" or "changed".
    before: The fact before, None when added.
    after: The fact after, None when removed.
    additive: Whether it leaves the version as it is.
  """

  key: str
  kind: str
  before: Fact | None
  after: Fact | None
  additive: bool

  @override
  def __str__(self) -> str:
    """Describe the change in a line.

    Returns:
      The line.
    """
    if self.before is None:
      return f"added {self.key}: {self.after.value if self.after else ''}"
    if self.after is None:
      return f"removed {self.key}: {self.before.value}"
    return f"changed {self.key}: {self.before.value} -> {self.after.value}"


class RegistryError(Exception):
  """Formats that cannot be loaded, or two of one name."""


def changes(
  before: Mapping[str, Fact], after: Mapping[str, Fact]
) -> list[Change]:
  """Compare two fingerprints of a format.

  Args:
    before: The earlier one.
    after: The later one.

  Returns:
    Each difference, by key.
  """
  found: list[Change] = []
  for key in sorted(before.keys() | after.keys()):
    old, new = before.get(key), after.get(key)
    if old == new:
      continue
    if new is None:
      found.append(Change(key, "removed", old, None, additive=False))
    elif old is None:
      found.append(
        Change(key, "added", None, new, additive=_additive(key, before, after))
      )
    else:
      found.append(Change(key, "changed", old, new, additive=False))
  return found


def _additive(
  key: str, before: Mapping[str, Fact], after: Mapping[str, Fact]
) -> bool:
  parts = key.split("/")
  for n in range(len(parts), 0, -1):
    at = "/".join(parts[:n])
    if at in before:
      return False
    fact = after.get(at)
    if fact is not None and fact.additive:
      return True
  return False


def installed(
  builtin: Iterable[Format],
  entry_points: Callable[[], Iterable[importlib.metadata.EntryPoint]]
  | None = None,
) -> list[Format]:
  """Return dataforge's formats and those the installed projects register.

  Args:
    builtin: dataforge's own.
    entry_points: The entry points of GROUP; None for those installed.

  Returns:
    The formats, by name.

  Raises:
    RegistryError: When an entry point fails to load or gives something
      other than formats, a name is not dotted lower case, a version is not
      an integer of at least 1, or two formats have one name.
  """
  found = list(builtin)
  points = (
    importlib.metadata.entry_points(group=GROUP)
    if entry_points is None
    else entry_points()
  )
  for point in points:
    try:
      given = cast(object, point.load()())
      formats = list(cast(Iterable[object], given))
    except Exception as error:
      # A project's code may raise anything; the caller refuses either way.
      raise RegistryError(
        f"entry point {point.name} of {GROUP}: {type(error).__name__}: {error}"
      ) from error
    for one in formats:
      if not isinstance(one, Format):
        raise RegistryError(
          f"entry point {point.name} of {GROUP} gave {one!r}, not a Format"
        )
      found.append(one)
  names: set[str] = set()
  for one in found:
    if not _NAME.fullmatch(one.name):
      raise RegistryError(f"{one.name!r}: not a dotted lower-case name")
    version = cast(object, one.version)
    if isinstance(version, bool) or not isinstance(version, int):
      raise RegistryError(f"{one.name}: version {version!r} not an int")
    if one.version < 1:
      raise RegistryError(f"{one.name}: version {one.version} below 1")
    if one.name in names:
      raise RegistryError(f"{one.name}: registered twice")
    names.add(one.name)
  return sorted(found, key=lambda one: one.name)


def differences(old: Mapping[str, int], new: Mapping[str, int]) -> list[str]:
  """Name what keeps two installs from running side by side.

  Args:
    old: The versions of one install's formats, by name.
    new: The other's.

  Returns:
    A line for each format missing from one or with another version in it;
    empty when they may run side by side.
  """
  found: list[str] = []
  for name in sorted(old.keys() | new.keys()):
    if name not in new:
      found.append(f"{name}: only in the old install, version {old[name]}")
    elif name not in old:
      found.append(f"{name}: only in the new install, version {new[name]}")
    elif old[name] != new[name]:
      found.append(
        f"{name}: version {old[name]} in the old install, {new[name]}"
        + " in the new"
      )
  return found
