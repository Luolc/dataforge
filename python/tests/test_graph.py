import json
from pathlib import Path
from typing import override

import pytest

from dataforge.cli import main
from dataforge.curation import (
  Context,
  End,
  Goto,
  Graph,
  least_cap_usd,
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


def test_mermaid_escapes_ampersands_and_backticks_in_labels() -> None:
  graph = Machine([Step("`a`", {"x&lt;y": End("`b` & c")})]).graph()
  assert mermaid(graph) == (
    "flowchart TD\n"
    "  start((start)) --> s0\n"
    '  s0["#96;a#96;"]\n'
    '  e0(["end: #96;b#96; #38; c"])\n'
    '  s0 -->|"x#38;lt;y"| e0\n'
  )


DECLARATION = """\
dataforge: 1
name: demo
start: make
inputs: {tries: integer}
states:
  make:
    uses: script
    with: {argv: [make]}
    max_visits: ${{ inputs.tries }}
    transitions:
      ok: {goto: check, report: false}
      failed: {goto: make}
  check:
    uses: judge
    with: {argv: [judge], outcomes: [pass, fail]}
    on_exhausted: rejected
    transitions:
      pass: {end: passed}
      fail: {goto: make, mark: kept, end_on_last_visit: true}
"""


def edge(source: str, outcome: str, **fields: object) -> dict[str, object]:
  return {
    "from": source,
    "outcome": outcome,
    "to": None,
    "end": None,
    "report": True,
    "mark": None,
    "end_on_last_visit": False,
    **fields,
  }


DECLARED_GRAPH = {
  "start": "make",
  "states": [
    {
      "name": "make",
      "kind": "ScriptState",
      "max_visits": 3,
      "on_exhausted": "failed",
      "max_cost_usd": 0.0,
    },
    {
      "name": "check",
      "kind": "JudgeState",
      "max_visits": 1,
      "on_exhausted": "rejected",
      "max_cost_usd": 0.0,
    },
  ],
  "edges": [
    edge("make", "ok", to="check", report=False),
    edge("make", "failed", to="make"),
    edge("check", "pass", end="passed", report=False),
    edge("check", "fail", to="make", mark="kept", end_on_last_visit=True),
  ],
}

DECLARED_MERMAID = """\
flowchart TD
  start((start)) --> s0
  s0["make<br/>max_visits 3"]
  s1["check"]
  e0(["end: passed"])
  s0 -->|"ok, no report"| s1
  s0 -->|"failed"| s0
  s1 -->|"pass"| e0
  s1 -->|"fail, mark kept, ends on last visit"| s0
"""


def test_dataforge_graph_prints_a_declarations_graph(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  declaration = tmp_path / "machine.yaml"
  _ = declaration.write_text(DECLARATION)
  inputs = tmp_path / "inputs.json"
  _ = inputs.write_text(json.dumps({"tries": 3}))
  args = ["graph", str(declaration), "--inputs", str(inputs)]

  assert main(args) == 0
  assert json.loads(capsys.readouterr().out) == DECLARED_GRAPH
  assert main([*args, "--format", "json"]) == 0
  assert json.loads(capsys.readouterr().out) == DECLARED_GRAPH
  assert main([*args, "--format", "mermaid"]) == 0
  assert capsys.readouterr().out == DECLARED_MERMAID


def test_dataforge_graph_refuses_a_declaration_that_does_not_load(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  declaration = tmp_path / "machine.yaml"
  _ = declaration.write_text(DECLARATION.replace("goto: check", "goto: gone"))
  inputs = tmp_path / "inputs.json"
  _ = inputs.write_text(json.dumps({"tries": 3}))

  assert main(["graph", str(declaration), "--inputs", str(inputs)]) == 1
  captured = capsys.readouterr()
  assert captured.out == ""
  assert captured.err == (
    f"{declaration}: states.make.transitions.ok.goto: no state named 'gone'\n"
  )
  assert main(["graph", str(declaration), "--inputs", str(tmp_path)]) == 2


def test_least_cap_usd_is_the_cheapest_chain_to_an_end() -> None:
  # write -> check -> solve costs 2.5 + 1.0 + 4.0; write -> review -> solve
  # 2.5 + 0.5 + 4.0, the least; nothing ends "rejected".
  found = Machine(
    [
      Step(
        "write",
        {"ok": Goto("check"), "short": Goto("review")},
        max_cost_usd=2.5,
      ),
      Step("check", {"pass": Goto("solve")}, max_cost_usd=1.0),
      Step(
        "review",
        {"pass": Goto("solve"), "fail": Goto("write")},
        max_cost_usd=0.5,
      ),
      Step(
        "solve",
        {"in_range": End("passed"), "none": End("failed")},
        max_cost_usd=4.0,
      ),
    ]
  ).graph()
  assert least_cap_usd(found, "passed") == 7.0
  assert least_cap_usd(found, "rejected") is None
