"""The globs of a manifest's restore and outputs, matched on project paths.

A glob is a path relative to the project's root, `/`-separated. `*`, `?` and
`[...]` match within one component, never across a `/`, and `*` matches a
leading `.` too; a component that is `**` matches any number of components,
none among them. So `spec/**` matches `spec` and everything under it, and
`**/conftest.py` a conftest.py anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
import fnmatch
import re

ANY = "**"
"""The component that matches any number of components."""

_MAGIC = re.compile(r"[*?[]")


def valid(value: object) -> str | None:
  """Return a glob that stays inside the project, or None.

  Args:
    value: The glob.

  Returns:
    It, or None when it is not a string, is absolute, or has an empty, `.`
    or `..` component, or one that holds `**` but is not `**`.
  """
  if not isinstance(value, str):
    return None
  for part in value.split("/"):
    if part in ("", ".", "..") or "\\" in part or "\0" in part:
      return None
    if ANY in part and part != ANY:
      return None
  return value


def matches(path: str, globs: Iterable[str]) -> bool:
  """Return whether a path matches one of some globs.

  Args:
    path: A path relative to the project, `/`-separated.
    globs: The globs.

  Returns:
    Whether one of them matches the whole path.
  """
  parts = path.split("/")
  return any(_match(g.split("/"), parts) for g in globs)


def _match(pattern: list[str], parts: list[str]) -> bool:
  if not pattern:
    return not parts
  if pattern[0] == ANY:
    return any(_match(pattern[1:], parts[n:]) for n in range(len(parts) + 1))
  return (
    bool(parts)
    and fnmatch.fnmatchcase(parts[0], pattern[0])
    and _match(pattern[1:], parts[1:])
  )


def escape(path: str) -> str:
  """Return the glob that matches a path and nothing else.

  Args:
    path: A path relative to the project.

  Returns:
    The path with each `*`, `?` and `[` in brackets.
  """
  return _MAGIC.sub(lambda m: f"[{m.group()}]", path)


def literal_prefix(glob: str) -> list[str]:
  """Return the components a glob starts with that hold no wildcard.

  Args:
    glob: The glob.

  Returns:
    Its leading components up to the first with `*`, `?` or `[`.
  """
  found: list[str] = []
  for part in glob.split("/"):
    if _MAGIC.search(part):
      break
    found.append(part)
  return found
