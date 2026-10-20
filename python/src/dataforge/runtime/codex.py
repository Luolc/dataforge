"""The trace of a Codex CLI session: what `codex exec --json` prints.

Harbor's codex agent tees it to `agent/codex.txt` in a trial's directory. A
turn starts with `turn.started` and ends with `turn.completed`, which carries
the turn's `usage`, or with `turn.failed`, which carries its `error`; each item
of the turn, such as the agent's message (an item of type `agent_message` with
its `text`), comes in an `item.completed` event. The events carry no cost.
Codex CLI 0.159.3 was seen to print `thread.started`, `turn.started`, `error`,
`item.started`, `item.completed`, `turn.completed` and `turn.failed` on
2026-10-20; the shapes of `turn.completed` and of an `agent_message` item are
as it printed them.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import cast

# The format of a session's trace: what `codex exec --json` prints, one JSON
# event per line.
TRACE_FORMAT = "codex-exec-jsonl"


@dataclass(frozen=True, slots=True)
class Trace:
  """What a `codex exec --json` trace says about its session.

  Attributes:
    turn: The last `turn.completed` or `turn.failed` event, or an empty dict
      when there is none.
    text: The text of the last agent message, the session's last answer, or
      None when there is none.
    input_tokens: The sum of the completed turns' `usage.input_tokens`.
    output_tokens: The sum of the completed turns' `usage.output_tokens`.
    unparsed: How many lines are not JSON.
  """

  turn: Mapping[str, object]
  text: str | None = None
  input_tokens: int = 0
  output_tokens: int = 0
  unparsed: int = 0

  @property
  def ending(self) -> str:
    """How the last turn ended: `completed`, `failed`, or `no result`."""
    found = self.turn.get("type")
    return (
      found.removeprefix("turn.") if isinstance(found, str) else "no result"
    )


def read_trace(trace: Path) -> Trace:
  """Read what a session did from its `codex exec --json` trace.

  Args:
    trace: The trace file; one that does not exist reads as an empty trace.

  Returns:
    The trace's last turn, last agent message and tokens, and its lines that
    are not JSON.
  """
  if not trace.is_file():
    return Trace({})
  turn: Mapping[str, object] = {}
  text: str | None = None
  tokens = {"input_tokens": 0, "output_tokens": 0}
  unparsed = 0
  for line in trace.read_text(errors="replace").splitlines():
    try:
      event = cast(object, json.loads(line))
    except json.JSONDecodeError:
      unparsed += 1
      continue
    if not isinstance(event, dict):
      continue
    event = cast(dict[str, object], event)
    kind = event.get("type")
    if kind in ("turn.completed", "turn.failed"):
      turn = event
    if kind == "turn.completed":
      usage = _object(event.get("usage"))
      for key in tokens:
        if (found := _number(usage.get(key))) is not None:
          tokens[key] += int(found)
    item = _object(event.get("item"))
    if kind == "item.completed" and item.get("type") == "agent_message":
      said = item.get("text")
      text = said if isinstance(said, str) else text
  return Trace(
    turn, text, tokens["input_tokens"], tokens["output_tokens"], unparsed
  )


def _object(value: object) -> dict[str, object]:
  return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
  if isinstance(value, bool) or not isinstance(value, int | float):
    return None
  return float(value)
