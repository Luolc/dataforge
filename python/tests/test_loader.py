# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import string
import sys
from typing import cast

import pytest
import yaml

from dataforge.cli import main
from dataforge.curation import (
  Actions,
  AgentRun,
  AgentSpec,
  AgentState,
  AgentTask,
  Attempt,
  Deliverable,
  End,
  Env,
  Goto,
  HOST,
  Host,
  Judge,
  JudgeState,
  Limits,
  load,
  Machine,
  PassAtKState,
  Review,
  RubricState,
  ScriptSpec,
  ScriptState,
  Source,
  SpecError,
  State,
  TrialAgent,
  TrialResult,
  TrialsState,
  Value,
)

RUBRIC = """\
system = "Review the task."

[[criteria]]
id = "sound"
question = "Is it sound?"
"""

type Doc = dict[str, object]


class Agents:
  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    raise NotImplementedError

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise NotImplementedError


class Trials:
  def memory_gb(self, limits: Limits) -> float:
    return limits.memory_gb

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    raise NotImplementedError


def base() -> Doc:
  return {
    "dataforge": 1,
    "name": "demo",
    "start": "make",
    "inputs": {"item": "string", "cap": "number", "tries": "integer"},
    "vars": {"tail": "End with a summary of ${{ inputs.item }}."},
    "max_cost_usd": "${{ inputs.cap }}",
    "states": {
      "make": {
        "uses": "script",
        "with": {"argv": ["make", "--item=${{ inputs.item }}"]},
        "max_visits": "${{ inputs.tries }}",
        "feedback": "Fix it. ${tail} ${state} ${details} $${tail}",
        "transitions": {
          "ok": {"goto": "check", "report": False},
          "failed": {"goto": "make"},
        },
      },
      "check": {
        "uses": "judge",
        "with": {
          "argv": ["judge"],
          "inputs": ["out"],
          "outcomes": ["pass", "fail"],
        },
        "transitions": {
          "pass": {"end": "passed"},
          "fail": {"goto": "make", "mark": "kept"},
        },
      },
    },
  }


INPUTS: dict[str, object] = {"item": "a1", "cap": 5, "tries": 3}


def load_doc(
  doc: Doc,
  inputs: Mapping[str, object] = INPUTS,
  bindings: Mapping[str, object] | None = None,
  base_dir: Path | None = None,
) -> Machine:
  return load(
    yaml.safe_dump(doc, sort_keys=False),
    inputs=inputs,
    actions=Actions(),
    bindings=bindings,
    base_dir=base_dir,
  )


def states_of(doc: Doc) -> dict[str, dict[str, object]]:
  return doc["states"]  # pyright: ignore[reportReturnType]


def test_load_gives_the_machine_its_measure_of_spend() -> None:
  def spent_usd(attempt: Attempt) -> float:
    return 1.0

  machine = load(
    yaml.safe_dump(base(), sort_keys=False),
    inputs=INPUTS,
    actions=Actions(),
    spent_usd=spent_usd,
  )
  assert machine.spent_usd is spent_usd
  assert load_doc(base()).spent_usd is None


def test_a_declaration_loads_with_its_inputs_and_vars() -> None:
  machine = load_doc(base())
  make, check = machine.states
  assert isinstance(make, ScriptState)
  assert isinstance(check, JudgeState)
  assert machine.max_cost_usd == 5.0
  assert make.max_visits == 3
  assert make.feedback == (
    "Fix it. End with a summary of a1. ${state} ${details} $${tail}"
  )
  assert check.judge == Judge(
    ScriptSpec(("judge",), inputs=("out",)), ("pass", "fail")
  )
  assert check.transitions == {
    "pass": End("passed"),
    "fail": Goto("make", mark="kept"),
  }


def test_deliverables_load_in_their_order() -> None:
  doc = base()
  doc["deliverables"] = {
    "verdict": {
      "kind": "report",
      "title": "Verdict on ${{ inputs.item }}",
      "source": {"state": "check", "output": "report.md", "visit": "last"},
      "table": {"state": "check", "output": "rubric.json"},
    },
    "made": {
      "kind": "files",
      "title": "Made",
      "source": {"state": "make", "output": "out", "visit": 2},
    },
    "runs": {
      "kind": "trials",
      "title": "pass@k",
      "source": {"state": "make"},
      "value": {"field": ["data", "solved"], "output": "verdict.json"},
    },
  }
  machine = load_doc(doc)
  assert list(machine.deliverables) == ["verdict", "made", "runs"]
  assert machine.deliverables == {
    "verdict": Deliverable(
      "report",
      "Verdict on a1",
      Source("check", "report.md", "last"),
      table=Source("check", "rubric.json"),
    ),
    "made": Deliverable("files", "Made", Source("make", "out", 2)),
    "runs": Deliverable(
      "trials",
      "pass@k",
      Source("make"),
      value=Value(("data", "solved"), "verdict.json"),
    ),
  }
  assert load_doc(base()).deliverables == {}


def files_deliverable(**source: object) -> Callable[[Doc], None]:
  return put(
    "deliverables",
    {"made": {"kind": "files", "title": "Made", "source": source}},
  )


def put(path: str, value: object) -> Callable[[Doc], None]:
  def change(doc: Doc) -> None:
    *parents, last = path.split(".")
    node: dict[str, object] = doc
    for key in parents:
      node = node[key]  # pyright: ignore[reportAssignmentType]
    node[last] = value

  return change


def drop(path: str) -> Callable[[Doc], None]:
  def change(doc: Doc) -> None:
    *parents, last = path.split(".")
    node: dict[str, object] = doc
    for key in parents:
      node = node[key]  # pyright: ignore[reportAssignmentType]
    del node[last]

  return change


@pytest.mark.parametrize(
  ("change", "error"),
  [
    (put("version", 1), "version: unknown field"),
    (
      put("states.make.with.argvv", ["x"]),
      "states.make.with.argvv: unknown field",
    ),
    (drop("states.make.transitions"), "states.make.transitions: missing"),
    (
      put("states.make.max_visits", "two"),
      "states.make.max_visits: expected an integer, got a string",
    ),
    (
      put("states.make.on_exhausted", False),
      (
        "states.make.on_exhausted: expected a string, got a boolean (YAML 1.1"
        " reads on, off, yes and no as booleans; quote it)"
      ),
    ),
    (put("dataforge", 2), "dataforge: the schema version must be 1"),
    (put("dataforge", 1.0), "dataforge: the schema version must be 1"),
    (
      put("vars", {"a": "${b}", "b": "x"}),
      "vars.a: ${b} names a var declared after it",
    ),
    (
      put("vars.tail", "Each run costs $5."),
      "states.make.feedback: not a valid template: write $$ for a lone $",
    ),
    (put("start", "nowhere"), "start: no state named 'nowhere'"),
    (
      put("inputs.round", "integer"),
      "inputs.round: 'round' is a variable the engine renders",
    ),
    (
      put("vars.feedback", "x"),
      "vars.feedback: 'feedback' is a variable the engine renders",
    ),
    (
      put("inputs.size", "float"),
      (
        "inputs.size: the type must be one of string, integer, number,"
        " boolean, path"
      ),
    ),
    (
      put("states.make.with.argv", ["${{ inputs.nope }}"]),
      "states.make.with.argv[0]: no input named 'nope'",
    ),
    (
      put("states.make.with.argv", ["${{ secrets.token }}"]),
      (
        "states.make.with.argv[0]: ${{ secrets.token }}: only"
        " ${{ inputs.<name> }} is known"
      ),
    ),
    (
      put("states.make.max_visits", "${{ inputs.cap }}"),
      (
        "states.make.max_visits: expected an integer, got an input of type"
        " number"
      ),
    ),
    (
      put("states.make.uses", "agnet"),
      (
        "states.make.uses: 'agnet' is neither one of agent, script, judge,"
        " pass-at-k, trial, trials, rubric nor a binding"
      ),
    ),
    (
      put("states.make.env", "box"),
      "states.make.env: no binding named 'box'",
    ),
    (
      put("states.check.runner", "trials"),
      "states.check.runner: not taken by uses: judge",
    ),
    (
      put("states.make.transitions.failed", {"goto": "fix"}),
      "states.make.transitions.failed.goto: no state named 'fix'",
    ),
    (
      drop("states.make.transitions.failed"),
      "states.make.transitions: no transition for the outcomes ['failed']",
    ),
    (
      put("states.make.transitions.error", {"end": "error"}),
      (
        "states.make.transitions.error: the state never gives this outcome;"
        " it gives ['failed', 'ok']"
      ),
    ),
    (
      put("states.check.transitions.pass", {"end": "passed", "mark": "x"}),
      "states.check.transitions.pass.mark: only a goto takes it",
    ),
    (
      put(
        "states.check.transitions.pass",
        {"end": "passed", "end_on_last_visit": True},
      ),
      "states.check.transitions.pass.end_on_last_visit: only a goto takes it",
    ),
    (
      put("states.check.transitions.pass", {"goto": "make", "end": "x"}),
      "states.check.transitions.pass: needs one of goto and end",
    ),
    (
      put("states.check.transitions.pass", {"goto": "lone"}),
      "states.check.transitions.pass.goto: no state named 'lone'",
    ),
    (
      put("deliverables", {"made": {"kind": "files", "title": "Made"}}),
      "deliverables.made.source: missing",
    ),
    (
      files_deliverable(state="make", output="out", visit="first"),
      (
        'deliverables.made.source.visit: expected "kept", "last" or an'
        " integer, got a string"
      ),
    ),
    (
      files_deliverable(state="make", output="out", visit=True),
      (
        'deliverables.made.source.visit: expected "kept", "last" or an'
        " integer, got a boolean"
      ),
    ),
    (
      files_deliverable(state="nowhere", output="out"),
      "deliverables.made: unknown state nowhere",
    ),
    (
      files_deliverable(state="make"),
      "deliverables.made: a files deliverable takes an output",
    ),
    (
      put(
        "deliverables",
        {
          "runs": {
            "kind": "trials",
            "title": "pass@k",
            "source": {"state": "make"},
            "value": {"field": "solved"},
          }
        },
      ),
      "deliverables.runs.value.field: expected a list, got a string",
    ),
  ],
)
def test_a_declaration_that_breaks_a_rule_is_refused(
  change: Callable[[Doc], None], error: str
) -> None:
  doc = base()
  change(doc)
  with pytest.raises(SpecError) as raised:
    _ = load_doc(doc)
  assert str(raised.value) == error


def test_an_unreachable_state_is_refused_and_a_reachable_one_loads() -> None:
  doc = base()
  states_of(doc)["lone"] = {
    "uses": "judge",
    "with": {"argv": ["judge"]},
    "transitions": {"any": {"end": "passed"}},
  }
  with pytest.raises(SpecError, match=r"^states\.lone: not reachable"):
    _ = load_doc(doc)
  put("states.check.transitions.pass", {"goto": "lone"})(doc)
  assert [s.name for s in load_doc(doc).states] == ["make", "check", "lone"]


def test_the_start_state_comes_first() -> None:
  doc = base()
  doc["start"] = "check"
  assert [s.name for s in load_doc(doc).states] == ["check", "make"]


@pytest.mark.parametrize(
  ("inputs", "error"),
  [
    ({"item": "a1", "cap": 5}, "inputs.tries: declared but not given"),
    (
      {**INPUTS, "extra": 1},
      "inputs.extra: given but not declared",
    ),
    (
      {**INPUTS, "cap": "5"},
      "inputs.cap: given '5', which is not of type number",
    ),
    (
      {**INPUTS, "tries": True},
      "inputs.tries: given True, which is not of type integer",
    ),
    (
      {**INPUTS, "item": 7},
      "inputs.item: given 7, which is not of type string",
    ),
  ],
)
def test_inputs_must_be_given_as_declared(
  inputs: dict[str, object], error: str
) -> None:
  with pytest.raises(SpecError) as raised:
    _ = load_doc(base(), inputs)
  assert str(raised.value) == error


def test_a_yaml_boolean_where_a_string_belongs_is_refused() -> None:
  text = yaml.safe_dump(base(), sort_keys=False).replace(
    "uses: judge", "uses: judge\n    on_exhausted: no"
  )
  with pytest.raises(SpecError, match="expected a string, got a boolean"):
    _ = load(text, inputs=INPUTS, actions=Actions())
  quoted = text.replace("on_exhausted: no", "on_exhausted: 'no'")
  check = load(quoted, inputs=INPUTS, actions=Actions()).states[1]
  assert check.on_exhausted == "no"


def test_on_as_a_key_is_refused_and_transitions_is_the_key() -> None:
  text = yaml.safe_dump(base(), sort_keys=False)
  bad = text.replace("transitions:", "on:", 1)
  with pytest.raises(SpecError) as raised:
    _ = load(bad, inputs=INPUTS, actions=Actions())
  assert str(raised.value) == (
    "states.make: the key True is not a string (YAML 1.1 reads on, off, yes"
    " and no as booleans; quote it)"
  )
  assert load(text, inputs=INPUTS, actions=Actions()).states[0].transitions


def test_a_repeated_key_is_refused() -> None:
  text = yaml.safe_dump(base(), sort_keys=False)
  bad = text.replace("  check:\n", "  make:\n", 1)
  with pytest.raises(SpecError, match=r"^line \d+: key 'make' repeated$"):
    _ = load(bad, inputs=INPUTS, actions=Actions())


def test_a_merge_key_is_not_a_repeated_key() -> None:
  merged = """\
dataforge: 1
name: demo
start: a
states:
  a: &a
    uses: judge
    with: {argv: [j]}
    transitions: {x: {goto: b}}
  b:
    <<: *a
    transitions: {x: {end: done}}
"""
  machine = load(merged, inputs={}, actions=Actions())
  assert machine.states[1].transitions == {"x": End("done")}


def test_a_state_that_does_not_name_its_outcomes_takes_any_transitions() -> (
  None
):
  doc = base()
  del states_of(doc)["check"]["with"]["outcomes"]  # pyright: ignore[reportIndexIssue]
  states_of(doc)["check"]["transitions"] = {
    "anything": {"end": "passed"},
    "fail": {"goto": "make"},
  }
  assert load_doc(doc).states[1].possible_outcomes() is None


def test_a_trials_state_may_take_a_judge() -> None:
  doc = base()
  states_of(doc)["check"] = {
    "uses": "trials",
    "with": {
      "task_dir": "out",
      "agents": {"nop": 1},
      "limits": {"memory_gb": 1},
      "judge": {"argv": ["judge"], "outcomes": ["pass", "fail"]},
    },
    "transitions": {"pass": {"end": "passed"}, "fail": {"goto": "make"}},
  }
  check = load_doc(doc).states[1]
  assert isinstance(check, TrialsState)
  assert check.judge == Judge(ScriptSpec(("judge",)), ("pass", "fail"))
  assert check.possible_outcomes() == {"pass", "fail"}


def test_rename_sets_the_outcomes_the_transitions_must_cover() -> None:
  doc = base()
  make = states_of(doc)["make"]
  make["rename"] = {"failed": "invalid"}
  with pytest.raises(SpecError, match="no transition for the outcomes"):
    _ = load_doc(doc)
  make["transitions"] = {
    "ok": {"goto": "check"},
    "invalid": {"goto": "make"},
  }
  assert load_doc(doc).states[0].possible_outcomes() == {"ok", "invalid"}


def files(root: Path) -> None:
  (root / "prompts").mkdir()
  _ = (root / "prompts" / "session.md").write_text(
    "Work on ${item} (${note}) in round ${round}. ${tail}"
  )
  _ = (root / "prompts" / "quality.toml").write_text(RUBRIC)
  _ = (root / "prompts" / "brief.toml").write_text(RUBRIC)
  _ = (root / "check.sh").write_text("#!/bin/sh\n")


def agent_doc() -> Doc:
  return {
    "dataforge": 1,
    "name": "demo",
    "start": "write",
    "states": {
      "write": {
        "uses": "agent",
        "with": {
          "prompt": {"file": "prompts/session.md"},
          "workdir": "/app",
          "validator": "check.sh",
        },
        "transitions": {
          "ok": {"goto": "review"},
          "failed": {"goto": "write"},
          "error": {"end": "error"},
        },
      },
      "review": {
        "uses": "rubric",
        "with": {
          "reviews": [
            {
              "name": "quality",
              "rubric": "prompts/quality.toml",
              "inputs": {"task": "out"},
            }
          ]
        },
        "transitions": {
          "pass": {"end": "passed"},
          "fail": {"goto": "write"},
          "error": {"end": "error"},
        },
      },
    },
  }


MISSING: list[tuple[str, object, str]] = [
  ("states.write.with.prompt.file", "nope.md", "prompt.file"),
  ("states.write.with.validator", "nope.sh", "validator"),
  (
    "states.review.with.reviews",
    [{"name": "q", "rubric": "nope.toml", "inputs": {}}],
    "reviews[0].rubric",
  ),
]


@pytest.mark.parametrize(("path", "value", "error"), MISSING)
def test_a_file_that_does_not_exist_is_refused(
  tmp_path: Path, path: str, value: object, error: str
) -> None:
  files(tmp_path)
  doc = agent_doc()
  _ = load_doc(doc, {}, base_dir=tmp_path)
  put(path, value)(doc)
  with pytest.raises(SpecError) as raised:
    _ = load_doc(doc, {}, base_dir=tmp_path)
  state = path.split(".")[1]
  assert str(raised.value).startswith(
    f"states.{state}.with.{error}: no such file: {tmp_path}/nope."
  )


def test_a_validator_an_input_names_is_left_to_the_caller(
  tmp_path: Path,
) -> None:
  files(tmp_path)
  doc = agent_doc()
  doc["inputs"] = {"validator": "path"}
  put("states.write.with.validator", "${{ inputs.validator }}")(doc)
  machine = load_doc(doc, {"validator": "built/later.pyz"}, base_dir=tmp_path)
  write = machine.states[0]
  assert isinstance(write, AgentState)
  assert spec_of(write).validator == tmp_path / "built/later.pyz"


def spec_of(state: AgentState) -> AgentSpec:
  spec = cast(object, vars(state)["_spec"])
  assert isinstance(spec, AgentSpec)
  return spec


def script_spec(state: State) -> ScriptSpec:
  spec = cast(object, vars(state)["_spec"])
  assert isinstance(spec, ScriptSpec)
  return spec


class Fixed(ScriptState):
  pass


class Custom(State):
  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
    level: int = 0,
  ) -> None:
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self.level: int = level


def test_uses_may_name_a_state_class_the_dataset_registers() -> None:
  doc = base()
  put("states.make.uses", "fixed")(doc)
  states_of(doc)["check"]["uses"] = "custom"
  states_of(doc)["check"]["with"] = {"level": "${{ inputs.tries }}"}
  machine = load_doc(doc, bindings={"fixed": Fixed, "custom": Custom})
  make, check = machine.states
  assert type(make) is Fixed
  assert isinstance(check, Custom)
  assert check.level == 3
  with pytest.raises(SpecError, match="unexpected keyword argument 'size'"):
    put("states.check.with.size", 1)(doc)
    _ = load_doc(doc, bindings={"fixed": Fixed, "custom": Custom})
  with pytest.raises(SpecError, match="is not a State subclass"):
    _ = load_doc(doc, bindings={"fixed": Fixed, "custom": len})
  doc = base()
  put("states.make.uses", "script")(doc)
  with pytest.raises(SpecError, match="has a shipped state's name"):
    _ = load_doc(doc, bindings={"script": Fixed})
  assert load_doc(doc, bindings={"script": HOST}).states


def test_env_and_runner_name_bound_objects(tmp_path: Path) -> None:
  files(tmp_path)
  doc = agent_doc()
  put("states.write.env", "box")(doc)
  put("states.write.runner", "agents")(doc)
  box, agents = HOST, Agents()
  machine = load_doc(
    doc, {}, bindings={"box": box, "agents": agents}, base_dir=tmp_path
  )
  write, review = machine.states
  assert isinstance(write, AgentState)
  assert write.env is box
  assert write.actions.agent_runner is agents
  assert isinstance(review, RubricState)
  assert review.actions.agent_runner is None


def test_engine_variables_are_left_to_the_engine(tmp_path: Path) -> None:
  files(tmp_path)
  doc = agent_doc()
  doc["inputs"] = {"item": "string"}
  doc["vars"] = {"tail": "Say what you changed."}
  put(
    "states.write.with.prompt",
    {"file": "prompts/session.md", "vars": {"item": "${{ inputs.item }}"}},
  )(doc)
  machine = load_doc(doc, {"item": "a1"}, base_dir=tmp_path)
  write = machine.states[0]
  assert isinstance(write, AgentState)
  assert spec_of(write).prompt == (
    "Work on a1 (${note}) in round ${round}. Say what you changed."
  )


# A declaration close to what a dataset keeps: the states of one attempt that
# writes a task, builds it, checks it with trials and judges, reviews it and
# measures pass@k. The inputs are the values its caller works out first.
EXAMPLE = """\
dataforge: 1
name: curate
start: author

inputs:
  item_id: string
  base: string
  tone: string
  brief: path
  validator: path
  cpus: integer
  memory_gb: integer
  may_spend_usd: number
  session_usd: number
  rubric_usd: number
  pass_at_k_usd: number
  solve_usd: number
  model: string
  mislead: boolean

max_cost_usd: ${{ inputs.may_spend_usd }}

vars:
  reminder: >-
    Never weaken a test only to get past a check.
  check_feedback: |
    The host checked the task, and its checks failed. ${reminder}

    ## ${state}, round ${round}

    ${details}

states:
  author:
    uses: agent
    env: session
    runner: agents
    max_visits: 6
    max_cost_usd: ${{ inputs.session_usd }}
    with:
      prompt:
        file: prompts/session.md
        vars:
          item: ${{ inputs.item_id }}
          note: "${{ inputs.tone }}, mislead ${{ inputs.mislead }}"
      report_prompt: "${feedback}"
      workdir: /app
      outputs: [solution, tests, instruction.md]
      validator: ${{ inputs.validator }}
      optional_inputs: ["pass_at_k:verdict.json"]
      tools: [Read, Glob, Grep, Write, Edit, Bash]
      last_if_unresumable: true
    rename: {ok: done, failed: invalid}
    feedback: |
      The static checks still fail. ${reminder}

      ${details}
    transitions:
      done: {goto: build, report: false}
      invalid: {goto: author}
      error: {end: error}

  build:
    uses: judge
    max_visits: 6
    with:
      argv: [python, -m, example.judges, build, --item, "${{ inputs.item_id }}"]
      inputs: [out]
      outputs: [environment, task]
      memory_gb: ${{ inputs.memory_gb }}
      outcomes: [pass, fail]
    feedback: ${check_feedback}
    transitions:
      pass: {goto: accept, report: false}
      fail: {goto: author}

  accept:
    uses: trials
    runner: trials
    max_visits: 6
    with:
      task_dir: task
      agents: {nop: 2, oracle: 2}
      limits: {memory_gb: 4, cpus: "${{ inputs.cpus }}"}
    transitions:
      ok: {goto: accept_judge, report: false}
      error: {end: error}

  accept_judge:
    uses: judge
    max_visits: 6
    with:
      argv: [python, -m, example.judges, judge-check]
      inputs: [nop-0, nop-1, oracle-0, oracle-1]
      optional_inputs: [out/ranking.json]
      outputs: [runs.json, regrade-tasks]
      outcomes: [pass, regrade, fail]
    feedback: ${check_feedback}
    transitions:
      pass: {goto: rubric, report: false}
      regrade: {goto: regrade, report: false}
      fail: {goto: author}

  regrade:
    uses: trials
    runner: trials
    max_visits: 6
    with:
      task_dir: regrade-tasks
      agents: {oracle: 1}
      limits: {memory_gb: 4, cpus: "${{ inputs.cpus }}"}
      each_task: true
    transitions:
      ok: {goto: regrade_judge, report: false}
      error: {end: error}

  regrade_judge:
    uses: judge
    max_visits: 6
    with:
      argv: [python, -m, example.judges, judge-regrade]
      inputs: [regrade-tasks]
      outcomes: [pass, fail]
    feedback: ${check_feedback}
    transitions:
      pass: {goto: rubric, report: false}
      fail: {goto: author}

  rubric:
    uses: rubric
    env: reviews
    runner: agents
    max_visits: 4
    max_cost_usd: ${{ inputs.rubric_usd }}
    with:
      reviews:
        - name: test_quality
          rubric: prompts/quality.toml
          inputs:
            task_description: task/instruction.md
            verification: task/tests
            execution_results: runs.json
        - name: instruction_review
          rubric: prompts/brief.toml
          inputs:
            brief: ${{ inputs.brief }}
            task_description: task/instruction.md
      workdir: /home/agent
    rename: {error: fail}
    feedback: |
      A reviewer's criterion failed. ${reminder}

      ${details}
    transitions:
      pass: {goto: pass_at_k, report: false}
      fail: {goto: author, end_on_last_visit: true}

  pass_at_k:
    uses: pass-at-k
    runner: trials
    max_visits: 4
    max_cost_usd: ${{ inputs.pass_at_k_usd }}
    with:
      task_dir: task
      k: 4
      agent:
        name: claude-code
        model: ${{ inputs.model }}
        kwargs:
          max_budget_usd: ${{ inputs.solve_usd }}
          disable_web_search: "true"
      limits: {memory_gb: 4, cpus: "${{ inputs.cpus }}"}
      judge:
        argv: [python, -m, example.judges, judge-pass-at-k]
        outcomes: [in_range, all_solved, none_solved, suspect, error]
    feedback: |
      The host ran pass@4 on the task. ${reminder}

      ${details}
    transitions:
      in_range: {end: passed}
      all_solved: {goto: author, mark: too_easy, end_on_last_visit: true}
      none_solved: {goto: author, mark: too_hard, end_on_last_visit: true}
      suspect: {goto: author, end_on_last_visit: true}
      error: {end: error}
"""

EXAMPLE_INPUTS: dict[str, object] = {
  "item_id": "item-7",
  "base": "abc123",
  "tone": "plain: say it plainly",
  "brief": "/attempt/brief.md",
  "validator": "/attempt/check_out.pyz",
  "cpus": 2,
  "memory_gb": 8,
  "may_spend_usd": 40.0,
  "session_usd": 10.0,
  "rubric_usd": 2,
  "pass_at_k_usd": 8.0,
  "solve_usd": 2.0,
  "model": "a-model",
  "mislead": False,
}


def by_hand(
  root: Path, session: Env, reviews: Env, agents: Agents, trials: Trials
) -> Machine:
  actions = Actions()
  reminder = "Never weaken a test only to get past a check."
  check_feedback = (
    f"The host checked the task, and its checks failed. {reminder}\n\n"
    "## ${state}, round ${round}\n\n${details}\n"
  )
  limits = Limits(4, 2)
  with_trials = Actions(trial_runner=trials)
  return Machine(
    [
      AgentState(
        "author",
        {
          "done": Goto("build", report=False),
          "invalid": Goto("author"),
          "error": End("error"),
        },
        6,
        actions=Actions(agent_runner=agents),
        env=session,
        spec=AgentSpec(
          (
            "Work on item-7 (plain: say it plainly, mislead"
            " false) in round ${round}. ${tail}"
          ),
          Path("/app"),
          outputs=("solution", "tests", "instruction.md"),
          validator=Path("/attempt/check_out.pyz"),
          tools=("Read", "Glob", "Grep", "Write", "Edit", "Bash"),
          optional_inputs=("pass_at_k:verdict.json",),
        ),
        report_prompt="${feedback}",
        last_if_unresumable=True,
        rename={"ok": "done", "failed": "invalid"},
        feedback=f"The static checks still fail. {reminder}\n\n${{details}}\n",
        max_cost_usd=10.0,
      ),
      JudgeState(
        "build",
        {"pass": Goto("accept", report=False), "fail": Goto("author")},
        6,
        actions=actions,
        judge=Judge(
          ScriptSpec(
            ("python", "-m", "example.judges", "build", "--item", "item-7"),
            inputs=("out",),
            outputs=("environment", "task"),
            memory_gb=8,
          ),
          ("pass", "fail"),
        ),
        feedback=check_feedback,
      ),
      TrialsState(
        "accept",
        {"ok": Goto("accept_judge", report=False), "error": End("error")},
        6,
        actions=with_trials,
        task_dir="task",
        agents={"nop": 2, "oracle": 2},
        limits=limits,
      ),
      JudgeState(
        "accept_judge",
        {
          "pass": Goto("rubric", report=False),
          "regrade": Goto("regrade", report=False),
          "fail": Goto("author"),
        },
        6,
        actions=actions,
        judge=Judge(
          ScriptSpec(
            ("python", "-m", "example.judges", "judge-check"),
            inputs=("nop-0", "nop-1", "oracle-0", "oracle-1"),
            outputs=("runs.json", "regrade-tasks"),
            optional_inputs=("out/ranking.json",),
          ),
          ("pass", "regrade", "fail"),
        ),
        feedback=check_feedback,
      ),
      TrialsState(
        "regrade",
        {"ok": Goto("regrade_judge", report=False), "error": End("error")},
        6,
        actions=with_trials,
        task_dir="regrade-tasks",
        agents={"oracle": 1},
        limits=limits,
        each_task=True,
      ),
      JudgeState(
        "regrade_judge",
        {"pass": Goto("rubric", report=False), "fail": Goto("author")},
        6,
        actions=actions,
        judge=Judge(
          ScriptSpec(
            ("python", "-m", "example.judges", "judge-regrade"),
            inputs=("regrade-tasks",),
          ),
          ("pass", "fail"),
        ),
        feedback=check_feedback,
      ),
      RubricState(
        "rubric",
        {
          "pass": Goto("pass_at_k", report=False),
          "fail": Goto("author", end_on_last_visit=True),
        },
        4,
        actions=Actions(agent_runner=agents),
        reviews=[
          Review(
            "test_quality",
            root / "prompts/quality.toml",
            {
              "task_description": "task/instruction.md",
              "verification": "task/tests",
              "execution_results": "runs.json",
            },
          ),
          Review(
            "instruction_review",
            root / "prompts/brief.toml",
            {
              "brief": Path("/attempt/brief.md"),
              "task_description": "task/instruction.md",
            },
          ),
        ],
        env=reviews,
        workdir=Path("/home/agent"),
        rename={"error": "fail"},
        feedback=f"A reviewer's criterion failed. {reminder}\n\n${{details}}\n",
        max_cost_usd=2.0,
      ),
      PassAtKState(
        "pass_at_k",
        {
          "in_range": End("passed"),
          "all_solved": Goto("author", mark="too_easy", end_on_last_visit=True),
          "none_solved": Goto(
            "author", mark="too_hard", end_on_last_visit=True
          ),
          "suspect": Goto("author", end_on_last_visit=True),
          "error": End("error"),
        },
        4,
        actions=with_trials,
        task_dir="task",
        agent={
          "name": "claude-code",
          "model": "a-model",
          "kwargs": {"max_budget_usd": 2.0, "disable_web_search": "true"},
        },
        limits=limits,
        k=4,
        judge=Judge(
          ScriptSpec(("python", "-m", "example.judges", "judge-pass-at-k")),
          ("in_range", "all_solved", "none_solved", "suspect", "error"),
        ),
        feedback=(
          f"The host ran pass@4 on the task. {reminder}\n\n${{details}}\n"
        ),
        max_cost_usd=8.0,
      ),
    ],
    max_cost_usd=40.0,
  )


def test_a_full_declaration_loads_as_the_same_machine_by_hand(
  tmp_path: Path,
) -> None:
  files(tmp_path)
  session, reviews, agents, trials = HOST, Host(), Agents(), Trials()
  machine = load(
    EXAMPLE,
    inputs=EXAMPLE_INPUTS,
    actions=Actions(),
    bindings={
      "session": session,
      "reviews": reviews,
      "agents": agents,
      "trials": trials,
    },
    base_dir=tmp_path,
  )
  expected = by_hand(tmp_path, session, reviews, agents, trials)
  assert machine.max_cost_usd == expected.max_cost_usd
  assert [(type(s), vars(s)) for s in machine.states] == [
    (type(s), vars(s)) for s in expected.states
  ]


def test_dataforge_check_checks_a_declaration_without_running_it(
  tmp_path: Path,
  capsys: pytest.CaptureFixture[str],
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  files(tmp_path)
  plain = EXAMPLE.replace("    env: session\n", "").replace(
    "    env: reviews\n", ""
  )
  for runner in ("agents", "trials"):
    plain = plain.replace(f"    runner: {runner}\n", "")
  declaration = tmp_path / "curate.yaml"
  _ = declaration.write_text(plain)
  inputs = tmp_path / "inputs.json"
  _ = inputs.write_text(json.dumps(EXAMPLE_INPUTS))
  assert main(["check", str(declaration), "--inputs", str(inputs)]) == 0
  assert capsys.readouterr().out == f"{declaration}: ok, 8 states\n"

  _ = inputs.write_text(json.dumps({**EXAMPLE_INPUTS, "cpus": "2"}))
  assert main(["check", str(declaration), "--inputs", str(inputs)]) == 1
  assert capsys.readouterr().err == (
    f"{declaration}: inputs.cpus: given '2', which is not of type integer\n"
  )

  _ = declaration.write_text(EXAMPLE)
  _ = inputs.write_text(json.dumps(EXAMPLE_INPUTS))
  source = (
    "from dataforge.curation import HOST\n"
    "BINDINGS = {'session': HOST, 'reviews': HOST, 'trials': None}\n"
  )
  _ = (tmp_path / "example_bindings.py").write_text(source)
  monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
  args = ["check", str(declaration), "--inputs", str(inputs)]
  assert main([*args, "--bindings", "example_bindings:BINDINGS"]) == 1
  assert "states.author.runner: no binding named 'agents'" in (
    capsys.readouterr().err
  )
  assert main([*args, "--bindings", "example_bindings"]) == 2
  _ = sys.modules.pop("example_bindings", None)


def test_an_inputs_value_reaches_the_engines_templates_as_given(
  tmp_path: Path,
) -> None:
  files(tmp_path)
  value = "costs $5, ${HOME} and ${state}"
  doc = agent_doc()
  doc["inputs"] = {"note": "string"}
  doc["vars"] = {"tail": "Note: ${{ inputs.note }}.", "then": "${tail} Go."}
  write = states_of(doc)["write"]
  put(
    "states.write.with.prompt",
    {"file": "prompts/session.md", "vars": {"item": "${{ inputs.note }}"}},
  )(doc)
  put("states.write.with.report_prompt", "${feedback} ${{ inputs.note }}")(doc)
  write["feedback"] = "${then} ${details} $$9"
  machine = load_doc(doc, {"note": value}, base_dir=tmp_path)
  state = machine.states[0]
  assert isinstance(state, AgentState)
  assert state.feedback is not None
  engine = {"state": "write", "visit": 1, "round": 1}
  assert (
    string.Template(state.feedback).substitute({"details": "d"}, **engine)
    == f"Note: {value}. Go. d $9"
  )
  spec = spec_of(state)
  assert (
    string.Template(spec.prompt).safe_substitute(feedback="", **engine)
    == f"Work on {value} (${{note}}) in round 1. Note: {value}."
  )
  assert state.report_prompt is not None
  assert (
    string.Template(state.report_prompt).safe_substitute(feedback="f", **engine)
    == f"f {value}"
  )


def test_report_outputs_load_as_a_list_of_names(tmp_path: Path) -> None:
  files(tmp_path)
  doc = agent_doc()
  put("states.write.with.report_outputs", ["changes.md"])(doc)
  state = load_doc(doc, {}, base_dir=tmp_path).states[0]
  assert isinstance(state, AgentState)
  assert state.report_outputs == ("changes.md",)
  default = load_doc(agent_doc(), {}, base_dir=tmp_path).states[0]
  assert isinstance(default, AgentState)
  assert default.report_outputs == ()


@pytest.mark.parametrize(
  ("value", "error"),
  [
    (
      "changes.md",
      "states.write.with.report_outputs: expected a list, got a string",
    ),
    (
      [7],
      "states.write.with.report_outputs[0]: expected a string, got an integer",
    ),
  ],
)
def test_report_outputs_of_the_wrong_type_name_the_field(
  tmp_path: Path, value: object, error: str
) -> None:
  files(tmp_path)
  doc = agent_doc()
  put("states.write.with.report_outputs", value)(doc)
  with pytest.raises(SpecError) as raised:
    _ = load_doc(doc, {}, base_dir=tmp_path)
  assert str(raised.value) == error


REVIEW = {
  "name": "quality",
  "rubric": "prompts/quality.toml",
  "inputs": {"task": "out"},
}


def test_a_rubric_reads_resume_reuse_unchanged_cross_criteria_and_notes(
  tmp_path: Path,
) -> None:
  files(tmp_path)
  doc = agent_doc()
  put("states.review.with.resume", True)(doc)
  put("states.review.with.reuse_unchanged", True)(doc)
  put("states.review.with.cross_criteria", True)(doc)
  notes = {"change_notes": "changes.md"}
  put("states.review.with.reviews", [REVIEW | {"notes": notes}])(doc)
  state = load_doc(doc, {}, base_dir=tmp_path).states[1]
  assert isinstance(state, RubricState)
  assert (state.resume, state.reuse_unchanged, state.cross_criteria) == (
    True,
    True,
    True,
  )
  assert state.reviews[0].notes == notes
  default = load_doc(agent_doc(), {}, base_dir=tmp_path).states[1]
  assert isinstance(default, RubricState)
  assert (
    default.resume,
    default.reuse_unchanged,
    default.cross_criteria,
  ) == (False, False, False)
  assert default.reviews[0].notes == {}


@pytest.mark.parametrize(
  ("path", "value", "error"),
  [
    (
      "states.review.with.resume",
      "yes",
      "states.review.with.resume: expected a boolean, got a string",
    ),
    (
      "states.review.with.reuse_unchanged",
      1,
      "states.review.with.reuse_unchanged: expected a boolean, got an integer",
    ),
    (
      "states.review.with.cross_criteria",
      "on",
      "states.review.with.cross_criteria: expected a boolean, got a string",
    ),
    (
      "states.review.with.reviews",
      [REVIEW | {"notes": ["changes.md"]}],
      "states.review.with.reviews[0].notes: expected a mapping, got a list",
    ),
    (
      "states.review.with.reviews",
      [REVIEW | {"notes": {"change_notes": 7}}],
      (
        "states.review.with.reviews[0].notes.change_notes: expected a string,"
        " got an integer"
      ),
    ),
  ],
)
def test_rubric_fields_of_the_wrong_type_name_the_field(
  tmp_path: Path, path: str, value: object, error: str
) -> None:
  files(tmp_path)
  doc = agent_doc()
  put(path, value)(doc)
  with pytest.raises(SpecError) as raised:
    _ = load_doc(doc, {}, base_dir=tmp_path)
  assert str(raised.value) == error


def test_an_input_outside_a_template_is_inserted_as_given() -> None:
  doc = base()
  machine = load_doc(doc, {**INPUTS, "item": "$5"})
  make = machine.states[0]
  assert isinstance(make, ScriptState)
  assert script_spec(make).argv == ("make", "--item=$5")


def test_a_path_input_may_be_given_as_a_path(tmp_path: Path) -> None:
  doc = base()
  doc["inputs"] = {**doc["inputs"], "tool": "path"}  # pyright: ignore[reportGeneralTypeIssues]
  put("states.make.with.inputs", ["${{ inputs.tool }}"])(doc)
  machine = load_doc(
    doc, {**INPUTS, "tool": Path("/opt/tool")}, base_dir=tmp_path
  )
  make = machine.states[0]
  assert script_spec(make).inputs == (Path("/opt/tool"),)


def test_env_and_runner_bindings_must_be_of_their_kind(tmp_path: Path) -> None:
  files(tmp_path)
  doc = agent_doc()
  put("states.write.env", "box")(doc)
  put("states.write.runner", "agents")(doc)
  bindings: dict[str, object] = {"box": 42, "agents": Agents()}
  with pytest.raises(SpecError) as raised:
    _ = load_doc(doc, {}, bindings=bindings, base_dir=tmp_path)
  assert str(raised.value) == "states.write.env: 'box' is not an Env"
  bindings = {"box": HOST, "agents": Trials()}
  with pytest.raises(SpecError) as raised:
    _ = load_doc(doc, {}, bindings=bindings, base_dir=tmp_path)
  assert str(raised.value) == (
    "states.write.runner: 'agents' is not an AgentRunner"
  )
