"""Formats, their versions and fingerprints, and where they are registered.

A format is something processes of two installs share while both run: a
table, a file, a directory's layout, the way one process starts another. Each
has a name, an integer version and a fingerprint, the facts about it that its
code gives, each a key and a value. Two installs whose formats have the same
names and versions may run side by side; any other difference, one format
missing or one version not the same, may not. Any change of a fingerprint
raises the version, and so does a change of what the code means by a format
that its fingerprint does not show.
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

type Fingerprint = dict[str, str]


@dataclass(frozen=True, slots=True)
class Format:
  """A format that the processes of two installs share.

  Attributes:
    name: Its name, dotted, the first part its owner's, such as
      `dataforge.queue.table`.
    version: Its version, at least 1; raised for each change of its
      fingerprint or of what its code means by it.
    fingerprint: Gives its facts from the code; it may write temporary
      files, and touches nothing else.
    about: What it is and where its code is, in a line.
  """

  name: str
  version: int
  fingerprint: Callable[[], Fingerprint]
  about: str


@dataclass(frozen=True, slots=True)
class Change:
  """A difference between two fingerprints of a format.

  Attributes:
    key: The fact's key.
    before: Its value before, None when added.
    after: Its value after, None when removed.
  """

  key: str
  before: str | None
  after: str | None

  @override
  def __str__(self) -> str:
    """Describe the change in a line.

    Returns:
      The line.
    """
    if self.before is None:
      return f"added {self.key}: {self.after}"
    if self.after is None:
      return f"removed {self.key}: {self.before}"
    return f"changed {self.key}: {self.before} -> {self.after}"


class RegistryError(Exception):
  """Formats that cannot be loaded, or two of one name."""


def changes(
  before: Mapping[str, str], after: Mapping[str, str]
) -> list[Change]:
  """Compare two fingerprints of a format.

  Args:
    before: The earlier one.
    after: The later one.

  Returns:
    Each difference, by key.
  """
  return [
    Change(key, before.get(key), after.get(key))
    for key in sorted(before.keys() | after.keys())
    if before.get(key) != after.get(key)
  ]


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
    if version < 1:
      raise RegistryError(f"{one.name}: version {version} below 1")
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
