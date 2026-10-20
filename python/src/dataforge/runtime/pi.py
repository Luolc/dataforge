"""The trace of a Pi session: what `pi --mode json` prints.

Harbor's pi agent tees it to `agent/pi.txt` in a trial's directory. Each
message the session ends with a `message_end` event, whose `message` has a
`role`; an assistant message carries its `usage`, with the price Pi puts on it
from its own price list in `usage.cost.total`, and its `stopReason`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import cast

# The format of a session's trace: what `pi --mode json` prints, one JSON event
# per line.
TRACE_FORMAT = "pi-json"


@dataclass(frozen=True, slots=True)
class Trace:
  """What a Pi JSON trace says about its session.

  Attributes:
    message: The last assistant message that ended, or an empty dict when
      none did.
    cost_usd: The sum of the assistant messages' `usage.cost.total`, in US
      dollars, or None when none of them gives a number.
    input_tokens: The sum of the assistant messages' `usage.input`.
    output_tokens: The sum of the assistant messages' `usage.output`.
    unparsed: How many lines are not JSON.
  """

  message: Mapping[str, object]
  cost_usd: float | None = None
  input_tokens: int = 0
  output_tokens: int = 0
  unparsed: int = 0

  @property
  def ending(self) -> str:
    """The last message's `stopReason`, or `no result` when there is none."""
    found = self.message.get("stopReason")
    return found if isinstance(found, str) else "no result"

  @property
  def text(self) -> str | None:
    """The last message's text, the session's last answer, if it has any."""
    blocks = self.message.get("content")
    if not isinstance(blocks, list):
      return None
    texts: list[str] = []
    for block in cast(list[object], blocks):
      block = _object(block)
      if block.get("type") == "text" and isinstance(
        text := block.get("text"), str
      ):
        texts.append(text)
    return "".join(texts) if texts else None


def read_trace(trace: Path) -> Trace:
  """Read what a session did and cost from its Pi JSON trace.

  Args:
    trace: The trace file; one that does not exist reads as an empty trace.

  Returns:
    The trace's last assistant message, its cost and tokens, and its lines
    that are not JSON.
  """
  if not trace.is_file():
    return Trace({})
  last: Mapping[str, object] = {}
  cost: float | None = None
  tokens = {"input": 0, "output": 0}
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
    message = _object(event.get("message"))
    if event.get("type") != "message_end" or message.get("role") != "assistant":
      continue
    last = message
    usage = _object(message.get("usage"))
    for key in tokens:
      if (found := _number(usage.get(key))) is not None:
        tokens[key] += int(found)
    if (total := _number(_object(usage.get("cost")).get("total"))) is not None:
      cost = (cost or 0.0) + total
  return Trace(last, cost, tokens["input"], tokens["output"], unparsed)


def _object(value: object) -> dict[str, object]:
  return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
  if isinstance(value, bool) or not isinstance(value, int | float):
    return None
  return float(value)
