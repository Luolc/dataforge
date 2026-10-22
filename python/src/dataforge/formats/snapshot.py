"""Snapshots of formats: the fingerprint of each version, kept in a repository.

A snapshot is `<directory>/<name>.json`, the format's version and its facts
as its code gave them. A repository's tests check that each format's code
gives its snapshot (`check`), and that at one version the snapshot changed
only additively since the base branch (`against_base`), so that a change
that is not additive raises the version. `write` updates a snapshot, and
refuses a change that is not additive at the version it had.
"""

import json
from pathlib import Path
import subprocess
from typing import cast

from dataforge.formats.core import Change, changes, Fact, Fingerprint, Format


class SnapshotError(Exception):
  """A snapshot that cannot be read."""


def path(directory: Path, name: str) -> Path:
  """Give the file of a format's snapshot.

  Args:
    directory: The directory of snapshots.
    name: The format's name.

  Returns:
    `<directory>/<name>.json`.
  """
  return directory / f"{name}.json"


def dumps(fmt: Format, facts: Fingerprint) -> str:
  """Give a snapshot's text.

  Args:
    fmt: The format.
    facts: Its fingerprint.

  Returns:
    The JSON, a fact a line, keys sorted, with a newline at the end.
  """
  lines = [
    f"    {json.dumps(key)}: "
    + json.dumps({"value": fact.value, "additive": fact.additive})
    for key, fact in sorted(facts.items())
  ]
  head = f'{{\n  "format": {json.dumps(fmt.name)},\n'
  head += f'  "version": {fmt.version},\n  "facts": {{\n'
  return head + ",\n".join(lines) + "\n  }\n}\n"


def loads(text: str) -> tuple[int, Fingerprint]:
  """Read a snapshot's text.

  Args:
    text: The JSON.

  Returns:
    The version and the facts.

  Raises:
    SnapshotError: When it is not a snapshot.
  """
  try:
    data = cast(dict[str, object], json.loads(text))
    version = data["version"]
    facts = cast(dict[str, dict[str, object]], data["facts"])
    read = {
      key: Fact(cast(str, fact["value"]), cast(bool, fact["additive"]))
      for key, fact in facts.items()
    }
  except (ValueError, TypeError, KeyError, AttributeError) as error:
    raise SnapshotError(f"not a snapshot: {error}") from error
  if isinstance(version, bool) or not isinstance(version, int):
    raise SnapshotError(f"not a snapshot: version {version!r}")
  return version, read


def _update(fmt: Format, directory: Path) -> str:
  return f"run `dataforge formats snapshot --prefix {fmt.name} {directory}`"


def _listed(found: list[Change]) -> str:
  return "; ".join(str(change) for change in found)


def check(fmt: Format, directory: Path) -> list[str]:
  """Check that a format's code gives its snapshot.

  Args:
    fmt: The format.
    directory: The directory of snapshots.

  Returns:
    A line for each problem, with what to do; empty when there is none.
  """
  file = path(directory, fmt.name)
  if not file.exists():
    return [f"{fmt.name}: no snapshot {file}; {_update(fmt, directory)}"]
  try:
    version, facts = loads(file.read_text())
  except SnapshotError as error:
    return [f"{fmt.name}: {file}: {error}"]
  found = changes(facts, fmt.fingerprint())
  if version == fmt.version and not found:
    return []
  if version != fmt.version:
    return [
      (
        f"{fmt.name}: the code declares version {fmt.version}, the snapshot has"
        f" {version}; {_update(fmt, directory)} and commit the snapshot"
      )
    ]
  if all(change.additive for change in found):
    return [
      (
        f"{fmt.name}: the format grew additively ({_listed(found)}), which"
        f" the code of version {version} reads and writes beside as before,"
        f" so the version may stay: {_update(fmt, directory)} and commit the"
        " snapshot"
      )
    ]
  return [
    (
      f"{fmt.name}: the format changed at version {version} in a way that is"
      f" not additive ({_listed(found)}): raise its version where it is"
      f" declared, then {_update(fmt, directory)} and commit the snapshot; an"
      " install of the new version then runs beside one of the old only after"
      " the queue drains"
    )
  ]


def write(fmt: Format, directory: Path) -> list[str]:
  """Write a format's snapshot, unless it changed and not additively.

  Args:
    fmt: The format.
    directory: The directory of snapshots.

  Returns:
    A line for each reason it was not written; empty when it was, or was
    already the same.
  """
  file = path(directory, fmt.name)
  facts = fmt.fingerprint()
  if file.exists():
    try:
      version, before = loads(file.read_text())
    except SnapshotError as error:
      return [f"{fmt.name}: {file}: {error}; remove it to write it anew"]
    if version > fmt.version:
      return [
        (
          f"{fmt.name}: the snapshot has version {version}, above the"
          f" {fmt.version} the code declares"
        )
      ]
    found = changes(before, facts)
    if version == fmt.version and not all(c.additive for c in found):
      return [
        (
          f"{fmt.name}: not additive at version {version} ({_listed(found)}):"
          " raise its version where it is declared, then write it again"
        )
      ]
  directory.mkdir(parents=True, exist_ok=True)
  _ = file.write_text(dumps(fmt, facts))
  return []


def against_base(
  fmt: Format, directory: Path, base: str = "origin/main"
) -> list[str] | None:
  """Check that a snapshot changed only additively since a branch at a version.

  The snapshot is compared with itself at the commit where HEAD left base
  (`git merge-base`): at the same version, each change must be additive; the
  version must not go down. A format with no snapshot there is new.

  Args:
    fmt: The format.
    directory: The directory of snapshots, in a git checkout.
    base: The branch.

  Returns:
    A line for each problem; empty when there is none; None when git cannot
    find base or the commit.
  """
  fork = _git(directory, "merge-base", "HEAD", base)
  if fork is None:
    return None
  file = path(directory, fmt.name)
  if not file.exists():
    return [f"{fmt.name}: no snapshot {file}; {_update(fmt, directory)}"]
  then = _git(directory, "show", f"{fork.strip()}:./{file.name}")
  if then is None:
    return []
  try:
    old_version, old = loads(then)
    version, facts = loads(file.read_text())
  except SnapshotError as error:
    return [f"{fmt.name}: {error}"]
  if version < old_version:
    return [f"{fmt.name}: version {version}, below the {old_version} of {base}"]
  found = changes(old, facts)
  if version == old_version and not all(c.additive for c in found):
    return [
      (
        f"{fmt.name}: the snapshot changed since {base} at version {version} in"
        f" a way that is not additive ({_listed(found)}): raise its version"
        f" where it is declared, then {_update(fmt, directory)}"
      )
    ]
  return []


def _git(directory: Path, *args: str) -> str | None:
  try:
    done = subprocess.run(
      ["git", "-C", str(directory), *args],
      capture_output=True,
      text=True,
      check=False,
      timeout=60,
    )
  except OSError:
    return None
  return done.stdout if done.returncode == 0 else None
