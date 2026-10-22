"""The dataforge runs command: list, show, reconcile and render."""

import argparse
import importlib
import json
import logging
from pathlib import Path
import sys
from typing import cast

from dataforge.runs.hooks import Hooks
from dataforge.runs.reconcile import reconcile
from dataforge.runs.render import write
from dataforge.runs.store import NewerSchemaError, STATES, Store


def add_arguments(runs: argparse.ArgumentParser) -> None:
  """Add the runs command's subcommands and their arguments.

  Args:
    runs: The parser of dataforge runs.
  """
  subs = runs.add_subparsers(dest="runs_command", required=True)
  listing = subs.add_parser("list", help="list attempts, the oldest first")
  _ = listing.add_argument("--item", help="only this item's attempts")
  _ = listing.add_argument(
    "--state",
    action="append",
    choices=STATES,
    help="only attempts in this state; repeat for more",
  )
  _ = listing.add_argument(
    "--json", action="store_true", help="a JSON array of the rows"
  )
  show = subs.add_parser("show", help="print an attempt's row and events")
  again = subs.add_parser(
    "reconcile",
    help="make the rows converge",
    description=(
      "Make each running row whose holder is gone interrupted; with --hooks,"
      " also end each interrupted row whose machine has ended, call on_ended"
      " for each ended row not settled, and check again whether the other"
      " interrupted rows may go on. Prints a line per change."
    ),
  )
  render = subs.add_parser(
    "render", help="write an attempt's record file from its row"
  )
  for parser in (listing, show, again, render):
    _ = parser.add_argument("db", type=Path, help="the store (SQLite)")
  for parser in (show, render):
    _ = parser.add_argument("id", help="the attempt's ID")
  for parser in (again, render):
    _ = parser.add_argument(
      "--hooks",
      metavar="MODULE:NAME",
      required=parser is render,
      help="the dataset's hooks, a Hooks importable as MODULE:NAME",
    )


def run(args: argparse.Namespace) -> int:
  """Run a runs subcommand.

  Args:
    args: The parsed arguments.

  Returns:
    0 on success; 1 for a store that does not exist or whose schema is newer,
    or an unknown attempt; 2 for hooks that cannot be loaded.
  """
  command = cast(str, args.runs_command)
  store = Store(cast(Path, args.db))
  if not store.path.exists():
    print(f"dataforge runs {command}: {store.path}: no store", file=sys.stderr)
    return 1
  try:
    hooks = _hooks(cast(str | None, getattr(args, "hooks", None)))
  except (ValueError, ImportError, AttributeError) as error:
    print(f"dataforge runs {command}: {error}", file=sys.stderr)
    return 2
  if command == "list":
    _list(args, store)
    return 0
  if command == "reconcile":
    logging.basicConfig(
      level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
      done = reconcile(store, hooks)
    except NewerSchemaError as error:
      print(f"dataforge runs reconcile: {error}", file=sys.stderr)
      return 1
    print("\n".join(done) if done else "nothing to change")
    return 0
  attempt_id = cast(str, args.id)
  row = store.get(attempt_id)
  if row is None:
    print(f"dataforge runs {command}: no attempt {attempt_id}", file=sys.stderr)
    return 1
  events = store.events(attempt_id)
  if command == "show":
    shown = row.to_json() | {
      "events": [
        {
          "at": event.at,
          "from": event.from_state,
          "to": event.to_state,
          "by": event.by,
          "detail": event.detail,
        }
        for event in events
      ]
    }
    print(json.dumps(shown, indent=2))
    return 0
  assert hooks is not None
  path = Path(row.root) / hooks.record
  write(path, hooks.render(row, events))
  print(path)
  return 0


def _list(args: argparse.Namespace, store: Store) -> None:
  states = cast(list[str] | None, args.state)
  rows = store.rows(item=cast(str | None, args.item), states=states)
  if cast(bool, args.json):
    listed = [
      {key: value for key, value in row.to_json().items() if key != "record"}
      for row in rows
    ]
    print(json.dumps(listed, indent=2))
    return
  for row in rows:
    said = row.outcome if row.state == "ended" else row.stopped_by
    resumable = {None: "-", True: "resumable", False: "not resumable"}
    fields = (
      row.id,
      row.state,
      said or "-",
      row.machine_status or "-",
      resumable[row.resumable],
      row.progress_at,
    )
    print("\t".join(str(field) for field in fields))


def _hooks(name: str | None) -> Hooks | None:
  if name is None:
    return None
  module, colon, attribute = name.partition(":")
  if not colon:
    raise ValueError(f"--hooks {name}: expected MODULE:NAME")
  value = cast(object, getattr(importlib.import_module(module), attribute))
  if not isinstance(value, Hooks):
    raise ValueError(f"--hooks {name}: not a dataforge.runs.Hooks")
  return value
