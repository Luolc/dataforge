"""The dataforge queue command: submit, list, cancel and serve."""

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import cast

from dataforge.queue.config import Config, ConfigError, load_config
from dataforge.queue.scheduler import serve
from dataforge.queue.store import (
  cancel,
  DuplicateError,
  Job,
  jobs,
  NoSuchJobError,
  STATES,
  submit,
)


def add_arguments(queue: argparse.ArgumentParser) -> None:
  """Add the queue command's subcommands and their arguments.

  Args:
    queue: The parser of dataforge queue.
  """
  subs = queue.add_subparsers(dest="queue_command", required=True)
  sub = subs.add_parser("submit", help="put a command in a queue")
  _ = sub.add_argument("--queue", required=True, help="the queue")
  _ = sub.add_argument(
    "--memory-mib", type=int, required=True, help="the memory it declares"
  )
  _ = sub.add_argument("--key", help="a key no other live job may hold")
  _ = sub.add_argument(
    "--label",
    action="append",
    default=[],
    metavar="NAME=VALUE",
    help="an opaque label; repeat for more",
  )
  _ = sub.add_argument(
    "--cwd", type=Path, help="the directory it runs in; the current one"
  )
  _ = sub.add_argument("argv", nargs="+", help="the command, after --")
  listing = subs.add_parser("list", help="list jobs, the newest first")
  _ = listing.add_argument("--queue", help="only this queue's jobs")
  _ = listing.add_argument(
    "--state", action="append", choices=STATES, help="only jobs in this state"
  )
  _ = listing.add_argument("--limit", type=int, help="at most this many")
  _ = listing.add_argument(
    "--json", action="store_true", help="a JSON array of the rows"
  )
  canceling = subs.add_parser("cancel", help="cancel a job")
  _ = canceling.add_argument("id", type=int, help="the job's ID")
  _ = subs.add_parser("serve", help="run the scheduler")
  for parser in (sub, listing, canceling, subs.choices["serve"]):
    _ = parser.add_argument(
      "--config", type=Path, required=True, help="the queue file (YAML)"
    )


def run(args: argparse.Namespace) -> int:
  """Run a queue subcommand.

  Args:
    args: The parsed arguments.

  Returns:
    0 on success; 1 for a duplicate key or an unknown job; 2 for a bad
    queue file or bad arguments.
  """
  command = cast(str, args.queue_command)
  try:
    config = load_config(cast(Path, args.config))
    if command == "serve":
      logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
      )
      return serve(config)
    if command == "submit":
      _print(_submit(args, config))
    elif command == "cancel":
      _print(cancel(config, cast(int, args.id)))
    else:
      _list(args, config)
  except (ConfigError, ValueError) as error:
    print(f"dataforge queue {command}: {error}", file=sys.stderr)
    return 2
  except (DuplicateError, NoSuchJobError) as error:
    print(f"dataforge queue {command}: {error}", file=sys.stderr)
    return 1
  return 0


def _submit(args: argparse.Namespace, config: Config) -> Job:
  labels: dict[str, str] = {}
  for label in cast(list[str], args.label):
    name, equals, value = label.partition("=")
    if not equals:
      raise ValueError(f"--label {label}: expected NAME=VALUE")
    labels[name] = value
  cwd = cast(Path | None, args.cwd)
  return submit(
    config,
    cast(str, args.queue),
    cast(list[str], args.argv),
    cwd=(cwd or Path.cwd()).absolute(),
    memory_mib=cast(int, args.memory_mib),
    key=cast(str | None, args.key),
    labels=labels,
  )


def _list(args: argparse.Namespace, config: Config) -> None:
  states = cast(list[str] | None, args.state)
  found = jobs(
    config,
    queue=cast(str | None, args.queue),
    states=None if states is None else set(states),
    limit=cast(int | None, args.limit),
  )
  if cast(bool, args.json):
    print(json.dumps([job.to_json() for job in found], indent=2))
    return
  for job in found:
    code = "" if job.exit_code is None else f" exit {job.exit_code}"
    print(f"{job.id}\t{job.queue}\t{job.state}{code}\t{' '.join(job.argv)}")


def _print(job: Job) -> None:
  print(json.dumps(job.to_json(), indent=2))
