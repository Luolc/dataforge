"""The dataforge formats command: list, compare and snapshot."""

import argparse
import json
from pathlib import Path
import sys
from typing import cast

from dataforge.formats.builtin import FORMATS
from dataforge.formats.core import differences, installed, RegistryError
from dataforge.formats.snapshot import write


def add_arguments(formats: argparse.ArgumentParser) -> None:
  """Add the formats command's subcommands and their arguments.

  Args:
    formats: The parser of dataforge formats.
  """
  subs = formats.add_subparsers(dest="formats_command", required=True)
  listing = subs.add_parser(
    "list",
    help="list the formats of this install and their versions",
    description=(
      "List dataforge's formats and those the installed projects register"
      " through the entry point group dataforge.formats, with their"
      ' versions. --json prints {"formats": {name: version}}.'
    ),
  )
  _ = listing.add_argument(
    "--json", action="store_true", help="a JSON object, for compare"
  )
  compare = subs.add_parser(
    "compare",
    help="tell whether two installs may run side by side",
    description=(
      "Compare what `formats list --json` printed for two installs. They"
      " may run side by side only when they have the same formats at the"
      " same versions; each difference is a line on stderr."
    ),
    epilog=(
      "Exit status: 0 when they may; 1 when a format is missing from one,"
      " has another version, or a file cannot be read; 2 for bad arguments."
    ),
  )
  _ = compare.add_argument("old", type=Path, help="the old install's list")
  _ = compare.add_argument("new", type=Path, help="the new install's list")
  snapshot = subs.add_parser(
    "snapshot",
    help="write the snapshots of formats, for their tests",
    description=(
      "Write <dir>/<name>.json for each format whose name starts with"
      " --prefix: its version and the facts its code gives. A snapshot"
      " that changed in a way that is not additive at the version it had"
      " is not written; raise the version first."
    ),
  )
  _ = snapshot.add_argument(
    "--prefix", required=True, help="the start of the formats' names"
  )
  _ = snapshot.add_argument("dir", type=Path, help="the snapshots' directory")


def run(args: argparse.Namespace) -> int:
  """Run a formats subcommand.

  Args:
    args: The parsed arguments.

  Returns:
    0 on success; 1 when the formats cannot be loaded, two installs may not
    run side by side, or a snapshot was not written.
  """
  command = cast(str, args.formats_command)
  if command == "compare":
    return _compare(cast(Path, args.old), cast(Path, args.new))
  try:
    formats = installed(FORMATS)
  except RegistryError as error:
    print(f"dataforge formats: {error}", file=sys.stderr)
    return 1
  if command == "list":
    if cast(bool, args.json):
      versions = {one.name: one.version for one in formats}
      print(json.dumps({"formats": versions}, indent=2))
    else:
      for one in formats:
        print(f"{one.name} {one.version}: {one.about}")
    return 0
  prefix = cast(str, args.prefix)
  chosen = [one for one in formats if one.name.startswith(prefix)]
  if not chosen:
    print(f"dataforge formats: no format starts with {prefix}", file=sys.stderr)
    return 1
  refused = [
    line for one in chosen for line in write(one, cast(Path, args.dir))
  ]
  for line in refused:
    print(line, file=sys.stderr)
  return 1 if refused else 0


def _compare(old: Path, new: Path) -> int:
  versions: list[dict[str, int]] = []
  for side, file in (("old", old), ("new", new)):
    try:
      versions.append(_versions(file))
    except (OSError, ValueError) as error:
      print(f"{side} install: {file}: {error}", file=sys.stderr)
      return 1
  found = differences(*versions)
  for line in found:
    print(line, file=sys.stderr)
  return 1 if found else 0


def _versions(file: Path) -> dict[str, int]:
  data = cast(object, json.loads(file.read_text()))
  formats = (
    cast(dict[str, object], data).get("formats")
    if isinstance(data, dict)
    else None
  )
  if not isinstance(formats, dict) or not formats:
    raise ValueError("not what `formats list --json` prints")
  versions: dict[str, int] = {}
  for name, version in cast(dict[object, object], formats).items():
    if not isinstance(name, str) or isinstance(version, bool):
      raise ValueError(f"{name!r}: not a name and a version")
    if not isinstance(version, int):
      raise ValueError(f"{name}: version {version!r} not an integer")
    versions[name] = version
  return versions
