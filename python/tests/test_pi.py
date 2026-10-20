"""Reading a Pi session's trace.

The traces are synthetic, in the shape of what `pi --mode json` prints.
"""

import json
from pathlib import Path

import pytest

from dataforge.runtime import pi


def assistant(text: str, cost: object, stop: str = "stop") -> dict[str, object]:
  return {
    "role": "assistant",
    "content": [{"type": "text", "text": text}],
    "provider": "example",
    "model": "example-model",
    "usage": {"input": 100, "output": 7, "cost": {"total": cost}},
    "stopReason": stop,
  }


def write(path: Path, events: list[object]) -> Path:
  _ = path.write_text(
    "\n".join(e if isinstance(e, str) else json.dumps(e) for e in events)
  )
  return path


def test_a_trace_gives_the_cost_tokens_and_last_answer(tmp_path: Path) -> None:
  first = assistant("looking", 0.25, stop="toolUse")
  last = assistant("done", 0.5)
  trace = write(
    tmp_path / "pi.txt",
    [
      {"type": "session", "version": 3, "id": "s1"},
      {"type": "message_end", "message": {"role": "user", "content": []}},
      {"type": "message_start", "message": assistant("", 0.0, "pending")},
      {"type": "message_end", "message": first},
      {"type": "message_end", "message": {"role": "toolResult", "content": []}},
      {"type": "message_end", "message": last},
      # turn_end repeats the message it ended; it is not counted again.
      {"type": "turn_end", "message": last},
      {"type": "agent_settled", "aborted": False},
    ],
  )
  found = pi.read_trace(trace)
  assert found.cost_usd == 0.75
  assert (found.input_tokens, found.output_tokens) == (200, 14)
  assert (found.ending, found.text) == ("stop", "done")
  assert found.unparsed == 0


@pytest.mark.parametrize("cost", [None, "0.25", True])
def test_a_cost_that_is_no_number_is_not_counted(
  tmp_path: Path, cost: object
) -> None:
  trace = write(
    tmp_path / "pi.txt",
    [
      {"type": "message_end", "message": assistant("a", cost)},
      {"type": "message_end", "message": assistant("b", 0.5)},
    ],
  )
  assert pi.read_trace(trace).cost_usd == 0.5


def test_bad_lines_are_counted_and_passed_over(tmp_path: Path) -> None:
  trace = write(
    tmp_path / "pi.txt",
    [
      "not json",
      "[1]",
      {"type": "message_end", "message": "not an object"},
      {"type": "message_end", "message": {"role": "assistant", "usage": []}},
      "{truncated",
    ],
  )
  found = pi.read_trace(trace)
  assert found.unparsed == 2
  assert found.cost_usd is None
  assert (found.ending, found.text) == ("no result", None)


def test_a_missing_trace_reads_as_empty(tmp_path: Path) -> None:
  found = pi.read_trace(tmp_path / "none.txt")
  assert (found.message, found.cost_usd, found.ending) == (
    {},
    None,
    "no result",
  )
