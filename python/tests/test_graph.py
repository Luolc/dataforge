import json
from typing import override

from dataforge.curation import (
  Context,
  End,
  Goto,
  Graph,
  Machine,
  mermaid,
  Outcome,
  State,
)


class Step(State):
  @override
  def run(self, ctx: Context) -> Outcome:
    raise AssertionError("a graph runs no state")


def machine() -> Machine:
  return Machine(
    [
      Step(
        "write",
        {"ok": Goto("check"), "failed": Goto("write")},
        max_visits=3,
        max_cost_usd=2.5,
      ),
      Step(
        "check",
        {
          "pass": Goto("solve", report=False),
          "fail": Goto("write", end_on_last_visit=True),
        },
        on_exhausted="rejected",
      ),
      Step(
        "solve",
        {
          "in_range": End("passed"),
          "all_solved": Goto("write", mark="too_easy"),
          "error": End("failed"),
        },
        max_visits=2,
      ),
    ]
  )


GRAPH: Graph = {
  "start": "write",
  "states": [
    {
      "name": "write",
      "kind": "Step",
      "max_visits": 3,
      "on_exhausted": "failed",
      "max_cost_usd": 2.5,
    },
    {
      "name": "check",
      "kind": "Step",
      "max_visits": 1,
      "on_exhausted": "rejected",
      "max_cost_usd": 0.0,
    },
    {
      "name": "solve",
      "kind": "Step",
      "max_visits": 2,
      "on_exhausted": "failed",
      "max_cost_usd": 0.0,
    },
  ],
  "edges": [
    {
      "from": "write",
      "outcome": "ok",
      "to": "check",
      "end": None,
      "report": True,
      "mark": None,
      "end_on_last_visit": False,
    },
    {
      "from": "write",
      "outcome": "failed",
      "to": "write",
      "end": None,
      "report": True,
      "mark": None,
      "end_on_last_visit": False,
    },
    {
      "from": "check",
      "outcome": "pass",
      "to": "solve",
      "end": None,
      "report": False,
      "mark": None,
      "end_on_last_visit": False,
    },
    {
      "from": "check",
      "outcome": "fail",
      "to": "write",
      "end": None,
      "report": True,
      "mark": None,
      "end_on_last_visit": True,
    },
    {
      "from": "solve",
      "outcome": "in_range",
      "to": None,
      "end": "passed",
      "report": False,
      "mark": None,
      "end_on_last_visit": False,
    },
    {
      "from": "solve",
      "outcome": "all_solved",
      "to": "write",
      "end": None,
      "report": True,
      "mark": "too_easy",
      "end_on_last_visit": False,
    },
    {
      "from": "solve",
      "outcome": "error",
      "to": None,
      "end": "failed",
      "report": False,
      "mark": None,
      "end_on_last_visit": False,
    },
  ],
}

MERMAID = """\
flowchart TD
  start((start)) --> s0
  s0["write<br/>max_visits 3"]
  s1["check"]
  s2["solve<br/>max_visits 2"]
  e0(["end: passed"])
  e1(["end: failed"])
  s0 -->|"ok"| s1
  s0 -->|"failed"| s0
  s1 -->|"pass, no report"| s2
  s1 -->|"fail, ends on last visit"| s0
  s2 -->|"in_range"| e0
  s2 -->|"all_solved, mark too_easy"| s0
  s2 -->|"error"| e1
"""


def test_graph_is_the_states_and_transitions_as_json_data() -> None:
  graph = machine().graph()
  assert graph == GRAPH
  assert json.loads(json.dumps(graph)) == GRAPH


def test_mermaid_renders_every_state_and_edge() -> None:
  assert mermaid(machine().graph()) == MERMAID


def test_mermaid_escapes_quotes_and_markup_in_labels() -> None:
  graph = Machine(
    [
      Step('say "hi"', {"<b>#1</b>": End('a"b')}),
    ]
  ).graph()
  assert mermaid(graph) == (
    "flowchart TD\n"
    "  start((start)) --> s0\n"
    '  s0["say #quot;hi#quot;"]\n'
    '  e0(["end: a#quot;b"])\n'
    '  s0 -->|"#lt;b#gt;#35;1#lt;/b#gt;"| e0\n'
  )
