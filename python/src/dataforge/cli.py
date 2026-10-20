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
from dataforge.curation.graph import mermaid
from dataforge.curation.loader import load, SpecError
from dataforge.queue import command as queue_command


def main(argv: Sequence[str] | None = None) -> int:
  """Run the dataforge command.

  "dataforge check <file> --inputs <json file> [--bindings <module>:<name>]"
  reads a machine's declaration and checks it as loading does, with the
  inputs in the JSON file and the bindings the dataset registers; it starts
  nothing. "dataforge graph" takes the same arguments and "--format json" or
  "--format mermaid", loads the declaration as check does and prints the
  machine's graph: its JSON, or a Mermaid flowchart. "dataforge viz serve
  --config <site file> --static <dir>" starts the viewer; it needs the extra
  viz. "dataforge queue submit|list|cancel|serve --config <queue file>"
  puts a command in a queue, lists the jobs, cancels one, or runs the
  scheduler; see dataforge.queue.command.

  Args:
    argv: The arguments; None for the command line's.

  Returns:
    The exit status: 0 when the declaration loads, 1 when it does not, 2 for
    bad arguments; for viz serve, see dataforge.viz.serve; for queue, see
    dataforge.queue.command.run.
  """
  parser = argparse.ArgumentParser(prog="dataforge")
  commands = parser.add_subparsers(dest="command", required=True)
  check = commands.add_parser(
    "check", help="check a machine's declaration without running it"
  )
  graph = commands.add_parser(
    "graph",
    help="print a machine's states and transitions from its declaration",
  )
  for sub in (check, graph):
    _ = sub.add_argument("file", type=Path, help="the declaration (YAML)")
    _ = sub.add_argument(
      "--inputs", type=Path, help="a JSON file of the inputs, by name"
    )
    _ = sub.add_argument(
      "--bindings",
      metavar="MODULE:NAME",
      help="the dataset's bindings, a mapping importable as MODULE:NAME",
    )
  _ = graph.add_argument(
    "--format",
    choices=("json", "mermaid"),
    default="json",
    help="the graph's JSON (the default) or a Mermaid flowchart",
  )
  viz = commands.add_parser("viz", help="the read-only viewer of attempts")
  serve = viz.add_subparsers(dest="viz", required=True).add_parser(
    "serve", help="serve the viewer at the site file's address"
  )
  _ = serve.add_argument(
    "--config", type=Path, required=True, help="the site file (YAML)"
  )
  _ = serve.add_argument(
    "--static", type=Path, required=True, help="the built frontend"
  )
  queue_command.add_arguments(
    commands.add_parser("queue", help="a queue of jobs on this machine")
  )
  args = parser.parse_args(argv)
  command = cast(str, args.command)
  if command == "queue":
    return queue_command.run(args)
  if command == "viz":
    return _viz(cast(Path, args.config), cast(Path, args.static))
  file = cast(Path, args.file)
  try:
    inputs = _inputs(cast(Path | None, args.inputs))
    bindings = _bindings(cast(str | None, args.bindings))
  except (OSError, ValueError, ImportError, AttributeError) as error:
    print(f"dataforge {command}: {error}", file=sys.stderr)
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
  if command == "check":
    print(f"{file}: ok, {len(machine.states)} states")
  elif cast(str, args.format) == "json":
    print(json.dumps(machine.graph(), indent=2))
  else:
    print(mermaid(machine.graph()), end="")
  return 0


def _viz(config: Path, static: Path) -> int:
  # Imported here: FastAPI and uvicorn come only with the extra viz.
  try:
    from dataforge.viz.serve import serve
  except ImportError as error:
    print(
      f"dataforge viz: {error}; install dataforge with the extra viz",
      file=sys.stderr,
    )
    return 2
  return serve(config, static)


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
