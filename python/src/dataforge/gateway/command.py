"""The dataforge gateway command: show and lift a channel's pause."""

import argparse
import getpass
import json
import socket
import sys
import time
from typing import cast

from dataforge.gateway.channels import Channel
from dataforge.gateway.pause import Pause
from dataforge.statedir import state_dir


def run(args: argparse.Namespace) -> int:
  """Run a gateway pause subcommand.

  Args:
    args: The parsed arguments.

  Returns:
    0 on success, also when no pause is under way; 1 when a channel's file
    cannot be read or written; 2 for a channel that does not exist.
  """
  name = cast(str | None, args.channel)
  try:
    channels = list(Channel) if name is None else [Channel(name)]
  except ValueError:
    known = ", ".join(c.value for c in Channel)
    print(
      f"dataforge gateway: no channel {name}; one of {known}", file=sys.stderr
    )
    return 2
  lift = cast(str, args.pause_command) == "clear"
  try:
    for channel in channels:
      pause = Pause(state_dir() / "pause" / f"{channel.value}.json")
      if lift:
        done = pause.lift(f"{getpass.getuser()}@{socket.gethostname()}")
        print(f"{channel.value}: {'cleared' if done else 'no pause under way'}")
      else:
        print(f"{channel.value}: {_describe(pause)}")
  except (OSError, ValueError, KeyError) as error:
    print(f"dataforge gateway: {error!r}", file=sys.stderr)
    return 1
  return 0


def _describe(pause: Pause) -> str:
  try:
    text = pause.path.read_text()
  except FileNotFoundError:
    return "no pause"
  state = cast(dict[str, object], json.loads(text))
  until = cast(float, state["until"])
  lines = [
    f"cause {state['cause']}",
    f"since {_time(state['since'])}",
    f"until {_time(until)}",
    f"remaining {max(until - time.time(), 0):.0f}s",
  ]
  if state["stopped"] is not None:
    lines.append(f"stopped for good: {state['stopped']}")
  lifted = cast(dict[str, object] | None, state.get("lifted"))
  if lifted is not None:
    lines.append(f"lifted by {lifted['by']} at {_time(lifted['at'])}")
  return "; ".join(lines)


def _time(value: object) -> str:
  if not isinstance(value, float | int) or not value:
    return "-"
  return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
