"""Reading a Codex CLI session's trace.

The traces are synthetic, in the shape of what `codex exec --json` prints.
"""

import json
from pathlib import Path

from dataforge.runtime import codex


def write(path: Path, events: list[object]) -> Path:
  _ = path.write_text(
    "\n".join(e if isinstance(e, str) else json.dumps(e) for e in events)
  )
  return path


def usage(input_tokens: int, output_tokens: int) -> dict[str, object]:
  return {
    "input_tokens": input_tokens,
    "cached_input_tokens": 0,
    "output_tokens": output_tokens,
  }


def message(text: str) -> dict[str, object]:
  return {
    "type": "item.completed",
    "item": {"id": "item_1", "type": "agent_message", "text": text},
  }


def test_a_trace_gives_the_tokens_and_last_answer(tmp_path: Path) -> None:
  trace = write(
    tmp_path / "codex.txt",
    [
      {"type": "thread.started", "thread_id": "t1"},
      {"type": "turn.started"},
      message("first"),
      {
        "type": "item.completed",
        "item": {"id": "item_2", "type": "command_execution", "text": "ls"},
      },
      {"type": "turn.completed", "usage": usage(100, 7)},
      {"type": "turn.started"},
      message("done"),
      {"type": "turn.completed", "usage": usage(200, 9)},
    ],
  )
  found = codex.read_trace(trace)
  assert (found.input_tokens, found.output_tokens) == (300, 16)
  assert (found.ending, found.text) == ("completed", "done")
  assert found.unparsed == 0


def test_a_failed_turn_is_the_ending(tmp_path: Path) -> None:
  trace = write(
    tmp_path / "codex.txt",
    [
      {"type": "turn.started"},
      {"type": "error", "message": "Reconnecting... 1/5"},
      {"type": "turn.failed", "error": {"message": "unexpected status 401"}},
    ],
  )
  found = codex.read_trace(trace)
  assert (found.ending, found.text, found.input_tokens) == ("failed", None, 0)


def test_bad_lines_are_counted_and_passed_over(tmp_path: Path) -> None:
  trace = write(
    tmp_path / "codex.txt",
    [
      "not json",
      "[1]",
      {"type": "turn.completed", "usage": {"input_tokens": "many"}},
      {"type": "item.completed", "item": "not an object"},
      "{truncated",
    ],
  )
  found = codex.read_trace(trace)
  assert found.unparsed == 2
  assert (found.ending, found.text, found.input_tokens) == (
    "completed",
    None,
    0,
  )


def test_a_missing_trace_reads_as_empty(tmp_path: Path) -> None:
  found = codex.read_trace(tmp_path / "none.txt")
  assert (found.turn, found.text, found.ending) == ({}, None, "no result")
