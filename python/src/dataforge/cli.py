"""The dataforge command."""

import argparse
from collections.abc import Sequence
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import sys
from typing import cast

from dataforge.curation.actions import Actions
from dataforge.curation.loader import load, SpecError


def main(argv: Sequence[str] | None = None) -> int:
  """Run the dataforge command.

  "dataforge check <file> --inputs <json file> [--bindings <module>:<name>]"
  reads a machine's declaration and checks it as loading does, with the
  inputs in the JSON file and the bindings the dataset registers; it starts
  nothing.

  Args:
    argv: The arguments; None for the command line's.

  Returns:
    The exit status: 0 when the declaration loads, 1 when it does not, 2 for
    bad arguments.
  """
  parser = argparse.ArgumentParser(prog="dataforge")
  commands = parser.add_subparsers(dest="command", required=True)
  check = commands.add_parser(
    "check", help="check a machine's declaration without running it"
  )
  _ = check.add_argument("file", type=Path, help="the declaration (YAML)")
  _ = check.add_argument(
    "--inputs", type=Path, help="a JSON file of the inputs, by name"
  )
  _ = check.add_argument(
    "--bindings",
    metavar="MODULE:NAME",
    help="the dataset's bindings, a mapping importable as MODULE:NAME",
  )
  args = parser.parse_args(argv)
  file = cast(Path, args.file)
  try:
    inputs = _inputs(cast(Path | None, args.inputs))
    bindings = _bindings(cast(str | None, args.bindings))
  except (OSError, ValueError, ImportError, AttributeError) as error:
    print(f"dataforge check: {error}", file=sys.stderr)
    return 2
  try:
    # Closing the stack removes the Docker environments, none of which ran.
    with ExitStack() as stack:
      machine = load(
        file,
        inputs=inputs,
        actions=Actions(),
        bindings=bindings,
        stack=stack,
      )
  except (SpecError, OSError) as error:
    print(f"{file}: {error}", file=sys.stderr)
    return 1
  print(f"{file}: ok, {len(machine.states)} states")
  return 0


def _inputs(path: Path | None) -> dict[str, object]:
  if path is None:
    return {}
  value = cast(object, json.loads(path.read_text()))
  if not isinstance(value, dict):
    raise ValueError(f"{path}: the inputs must be a JSON object")
  return cast(dict[str, object], value)


def _bindings(name: str | None) -> dict[str, object]:
  if name is None:
    return {}
  module, colon, attribute = name.partition(":")
  if not colon:
    raise ValueError(f"--bindings {name}: expected MODULE:NAME")
  value = cast(object, getattr(importlib.import_module(module), attribute))
  if not isinstance(value, dict):
    raise ValueError(f"--bindings {name}: not a dict")
  return cast(dict[str, object], value)
