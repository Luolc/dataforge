# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
from pathlib import Path
import sys
import time
from typing import cast, Literal, override

import pytest

from dataforge import stopping
from dataforge.curation import (
  ActionRecord,
  ActionResult,
  Actions,
  AgentRun,
  AgentSpec,
  AgentState,
  AgentTask,
  Attempt,
  Context,
  Deliverable,
  End,
  ENDING,
  Env,
  Goto,
  Guard,
  Limits,
  Machine,
  measure,
  Outcome,
  PassAtKState,
  recorded_usd,
  RecordError,
  ResumeError,
  ScriptSpec,
  Source,
  State,
  TrialAgent,
  TrialResult,
  Value,
)

# A fake dataset shaped like a curate process, built from dataforge's own
# states: author writes a task (a validator checks it mechanically), rubric
# judges it, pass_at_k runs k trials of a solver.
K = 3

CURATE_TABLE: dict[str, dict[str, Goto | End]] = {
  "author": {
    "ok": Goto("rubric"),
    "failed": Goto("author"),
  },
  "rubric": {
    "pass": Goto("pass_at_k", report=False),
    "fail": Goto("author"),
  },
  "pass_at_k": {
    "in_range": End("passed"),
    "all_solved": Goto("author", mark="too_easy"),
    "none_solved": Goto("author", mark="too_hard"),
  },
}

MAX_VISITS = {"author": 3, "rubric": 3, "pass_at_k": 3}


@dataclass
class Script:
  """What the fakes do, call by call.

  Attributes:
    tasks: The task texts the author writes, consumed from the front.
    verdicts: The judge's answers, consumed from the front.
    solved: How many of the K trials solve in each visit, consumed from the
      front.
    prompts: The prompts the agents got.
    closed: The sessions closed.
  """

  tasks: list[str] = field(default_factory=list[str])
  verdicts: list[bool] = field(default_factory=list[bool])
  solved: list[int] = field(default_factory=list[int])
  prompts: list[str] = field(default_factory=list[str])
  closed: list[object] = field(default_factory=list[object])


class FakeAgent:
  """An agent runner: the author writes the next task, the judge answers JSON.

  Args:
    script: What it does.
  """

  def __init__(self, script: Script) -> None:
    self.script: Script = script

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    self.script.prompts.append(task.prompt)
    if spec.output_schema is not None:
      answer = json.dumps({"pass": self.script.verdicts.pop(0)})
      return AgentRun(("judge", task.outdir), "ok", 0.5, None, answer)
    return self._write(task.outdir)

  def resume(self, session: object, prompt: str) -> AgentRun:
    self.script.prompts.append(prompt)
    _, outdir = cast(tuple[str, str], session)
    return self._write(outdir)

  def close(self, session: object) -> None:
    self.script.closed.append(session)

  def _write(self, outdir: str) -> AgentRun:
    _ = (Path(outdir) / "task").write_text(self.script.tasks.pop(0))
    return AgentRun(("author", outdir), "ok", 1.0, None, "done")


class FakeTrials:
  """A trial runner: the first few of the K trials of a visit solve.

  Each trial writes a diff.

  Args:
    script: How many solve.
  """

  def __init__(self, script: Script) -> None:
    self.script: Script = script
    self.solved: list[int] = []

  def memory_gb(self, limits: Limits) -> float:
    return limits.memory_gb

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    visit = int(outdir.parent.name)
    index = int(outdir.name.removeprefix("trial-"))
    while len(self.solved) < visit:
      self.solved.append(self.script.solved.pop(0))
    diff = outdir / "diff"
    _ = diff.write_text(f"diff of {task_dir.read_text()}")
    reward = 1.0 if index < self.solved[visit - 1] else 0.0
    return TrialResult({"reward": reward}, None, 0.1, 1.0, {"diff": diff})


class Author(AgentState):
  def __init__(self, validator: Path, actions: Actions) -> None:
    super().__init__(
      "author", CURATE_TABLE["author"], MAX_VISITS["author"], actions=actions
    )
    self.validator: Path = validator

  @override
  def spec(self, ctx: Context) -> AgentSpec:
    report = ctx.report
    prompt = "Write a task."
    if report is not None:
      prompt = f"Revise: {report.outcome}; see {sorted(report.artifacts)}."
    return AgentSpec(
      prompt, workdir=ctx.outdir, outputs=["task"], validator=self.validator
    )


class Rubric(AgentState):
  def __init__(self, actions: Actions) -> None:
    super().__init__(
      "rubric", CURATE_TABLE["rubric"], MAX_VISITS["rubric"], actions=actions
    )

  @override
  def spec(self, ctx: Context) -> AgentSpec:
    schema = {"type": "object"}
    return AgentSpec(
      "Judge.", ctx.outdir, inputs=["task"], output_schema=schema, tools=[]
    )

  @override
  def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
    verdict = cast(dict[str, bool], result.value)
    return Outcome("pass" if verdict["pass"] else "fail")


def curate(validator: Path, actions: Actions) -> list[State]:
  pass_at_k = PassAtKState(
    "pass_at_k",
    CURATE_TABLE["pass_at_k"],
    MAX_VISITS["pass_at_k"],
    actions=actions,
    task_dir=lambda ctx: ctx.artifacts.get("task"),
    agent="solver",
    limits=Limits(1.0),
    k=K,
  )
  return [Author(validator, actions), Rubric(actions), pass_at_k]


@pytest.fixture
def validator(tmp_path: Path) -> Path:
  path = tmp_path / "check.sh"
  _ = path.write_text(
    '#!/bin/sh\ngrep -q valid "$1/task" || { echo "task is broken"; exit 1; }\n'
  )
  path.chmod(0o755)
  return path


def run(
  tmp_path: Path,
  validator: Path,
  script: Script,
  *,
  guard: Guard | None = None,
  on_step: Callable[[Attempt], None] | None = None,
  states: Callable[[list[State]], list[State]] = lambda states: states,
) -> Attempt:
  actions = Actions(
    agent_runner=FakeAgent(script), trial_runner=FakeTrials(script)
  )
  machine = Machine(
    states(curate(validator, actions)), guard=guard, on_step=on_step
  )
  return machine.run(tmp_path / "attempt")


def path_of(attempt: Attempt) -> list[tuple[str, str | None]]:
  return [(visit.state, visit.outcome) for visit in attempt.visits]


def test_in_range_pass_at_k_passes(tmp_path: Path, validator: Path) -> None:
  script = Script(tasks=["valid 1"], verdicts=[True], solved=[1])
  attempt = run(tmp_path, validator, script)
  assert attempt.status == "passed"
  assert path_of(attempt) == [
    ("author", "ok"),
    ("rubric", "pass"),
    ("pass_at_k", "in_range"),
  ]
  assert attempt.final["pass_at_k"].data == {
    "solved": 1,
    "errors": 0,
    "faults": {},
    "k": K,
  }


def test_validator_failure_resumes_the_agent_with_its_output(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(tasks=["broken", "valid 1"], verdicts=[True], solved=[1])
  attempt = run(tmp_path, validator, script)
  assert attempt.status == "passed"
  assert "task is broken" in script.prompts[1]
  assert attempt.visits[0].outputs["task"].read_text() == "valid 1"


def test_validator_failing_past_max_fixes_returns_to_author(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(
    tasks=["broken"] * 3 + ["valid 2"], verdicts=[True], solved=[2]
  )
  attempt = run(tmp_path, validator, script)
  assert path_of(attempt)[:2] == [("author", "failed"), ("author", "ok")]
  assert attempt.status == "passed"


def test_validator_given_to_the_agent_is_a_read_only_copy(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(tasks=["valid 1"], verdicts=[True], solved=[1])
  _ = run(tmp_path, validator, script)
  copy = tmp_path / "attempt" / "validators" / "author" / "1" / "1" / "check.sh"
  assert f"`{copy} " in script.prompts[0]
  assert copy.stat().st_mode & 0o222 == 0
  assert copy.read_text() == validator.read_text()


def test_rubric_failure_returns_to_author_with_a_report(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(
    tasks=["valid 1", "valid 2"], verdicts=[False, True], solved=[1]
  )
  attempt = run(tmp_path, validator, script)
  assert path_of(attempt)[:3] == [
    ("author", "ok"),
    ("rubric", "fail"),
    ("author", "ok"),
  ]
  assert script.prompts[2].startswith("Revise: fail; see [].")
  assert attempt.status == "passed"


def test_all_solved_returns_to_author_with_the_diffs(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(
    tasks=["valid 1", "valid 2"], verdicts=[True, True], solved=[K, 1]
  )
  attempt = run(tmp_path, validator, script)
  assert path_of(attempt)[2:4] == [
    ("pass_at_k", "all_solved"),
    ("author", "ok"),
  ]
  assert attempt.visits[2].mark == "too_easy"
  trials = [f"trial-{i}" for i in range(K)]
  assert script.prompts[2].startswith(f"Revise: all_solved; see {trials}.")
  diff = attempt.visits[2].outputs["trial-0"] / "diff"
  assert diff.read_text() == "diff of valid 1"
  assert attempt.status == "passed"
  assert attempt.kept_visit is None


def test_cap_after_a_mark_keeps_the_marked_visit(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(tasks=["valid"] * 3, verdicts=[True] * 3, solved=[K, 0, K])
  attempt = run(tmp_path, validator, script)
  assert attempt.status == "too_easy"
  assert attempt.kept_visit == 8
  assert attempt.reason == "author visit 4: entered over max_visits"
  assert attempt.final["pass_at_k"] is attempt.visits[8]


def test_kept_visit_cuts_the_final_record(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(
    tasks=["valid"] * 3, verdicts=[True, True, False], solved=[1, K]
  )

  def in_range_goes_back(states: list[State]) -> list[State]:
    states[2].transitions = {
      **states[2].transitions,
      "in_range": Goto("author"),
    }
    return states

  attempt = run(tmp_path, validator, script, states=in_range_goes_back)
  assert attempt.status == "too_easy"
  assert attempt.kept_visit == 5
  assert attempt.final["rubric"] is attempt.visits[4]
  assert attempt.visits[-1].state == "rubric"


def test_cap_without_a_mark_fails(tmp_path: Path, validator: Path) -> None:
  script = Script(tasks=["valid"] * 3, verdicts=[False] * 3)
  attempt = run(tmp_path, validator, script)
  assert attempt.status == "failed"
  assert attempt.kept_visit is None
  assert attempt.reason == "author visit 4: entered over max_visits"


def test_guard_blocks_with_budget(tmp_path: Path, validator: Path) -> None:
  script = Script(tasks=["valid"], verdicts=[True])

  def guard(ctx: Context, state: State) -> str | None:
    return "out of budget" if state.name == "pass_at_k" else None

  attempt = run(tmp_path, validator, script, guard=guard)
  assert attempt.status == "budget"
  assert attempt.reason == "pass_at_k visit 1: out of budget"
  assert path_of(attempt) == [("author", "ok"), ("rubric", "pass")]


def test_guard_after_a_mark_keeps_the_marked_visit(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(tasks=["valid"] * 2, verdicts=[True], solved=[0])

  def guard(ctx: Context, state: State) -> str | None:
    return (
      "out of budget" if state.name == "rubric" and ctx.visit == 2 else None
    )

  attempt = run(tmp_path, validator, script, guard=guard)
  assert attempt.status == "too_hard"
  assert attempt.kept_visit == 2


def test_each_agent_session_is_closed(tmp_path: Path, validator: Path) -> None:
  script = Script(tasks=["valid"], verdicts=[True], solved=[1])
  _ = run(tmp_path, validator, script)
  kinds = [cast(tuple[str, str], session)[0] for session in script.closed]
  assert kinds == ["author", "judge"]


def test_on_step_sees_each_visit_and_the_end(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(tasks=["valid"], verdicts=[True], solved=[1])
  seen: list[tuple[int, str | None, int, str | None]] = []

  def on_step(a: Attempt) -> None:
    last = a.visits[-1]
    seen.append((len(a.visits), last.outcome, len(last.actions), a.status))

  attempt = run(tmp_path, validator, script, on_step=on_step)
  # A visit is seen as it begins, after each of its actions and as it ends.
  assert [step for step in seen if step[0] < 3] == [
    (1, None, 0, None),
    (1, None, 0, None),
    (1, None, 1, None),
    (1, "ok", 1, None),
    (2, None, 0, None),
    (2, None, 0, None),
    (2, None, 1, None),
    (2, "pass", 1, None),
  ]
  # pass@k's trials run at once, so only their count is fixed.
  trials = [step for step in seen if step[0] == 3]
  assert trials[0] == (3, None, 0, None)
  assert trials[-1] == (3, "in_range", K, "passed")
  assert len(trials) == 2 + 2 * K
  assert attempt.to_json()["status"] == "passed"


def test_on_step_is_called_one_at_a_time(
  tmp_path: Path, validator: Path
) -> None:
  script = Script(tasks=["valid"], verdicts=[True], solved=[1])
  inside: list[int] = []
  most: list[int] = [0]

  def on_step(attempt: Attempt) -> None:
    _ = attempt
    inside.append(1)
    most[0] = max(most[0], len(inside))
    time.sleep(0.02)
    del inside[-1]

  _ = run(tmp_path, validator, script, on_step=on_step)
  assert most == [1]


def test_to_json_is_json(tmp_path: Path, validator: Path) -> None:
  script = Script(tasks=["valid"], verdicts=[True], solved=[1])
  record = run(tmp_path, validator, script).to_json()
  assert json.loads(json.dumps(record)) == record
  final = cast(dict[str, dict[str, object]], record["final"])
  actions = cast(list[dict[str, dict[str, str]]], final["pass_at_k"]["actions"])
  outputs = {
    name: path for action in actions for name, path in action["outputs"].items()
  }
  assert outputs == {
    f"trial-{i}": f"artifacts/pass_at_k/1/trial-{i}" for i in range(K)
  }
  visits = cast(list[dict[str, object]], record["visits"])
  assert [visit["state"] for visit in visits] == [
    "author",
    "rubric",
    "pass_at_k",
  ]


class Step(State):
  """A state that calls a function, for the tests below.

  Args:
    name: The state's name.
    step: What its run calls.
    transitions: Where each outcome leads.
    max_visits: How many times it may be entered.
    on_exhausted: The attempt's status when it is entered once too often.
    feedback: Its feedback template.
    max_cost_usd: The most one visit may spend.
  """

  def __init__(
    self,
    name: str,
    step: Callable[[Context], Outcome],
    transitions: dict[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self.step: Callable[[Context], Outcome] = step

  @override
  def run(self, ctx: Context) -> Outcome:
    return self.step(ctx)


def test_artifacts_keep_each_version(tmp_path: Path) -> None:
  actions = Actions()

  def step(ctx: Context) -> Outcome:
    argv = ["sh", "-c", f'echo {ctx.visit} > "$DATAFORGE_OUTPUTS/n"']
    _ = actions.script(ctx, ScriptSpec(argv, outputs=["n"]))
    versions = [(v.state, v.visit, v.path) for v in ctx.artifacts.history("n")]
    return Outcome(
      "again", {"latest": str(ctx.artifacts.get("n")), "n": len(versions)}
    )

  attempt = Machine([Step("a", step, {"again": Goto("a")}, 2)]).run(tmp_path)
  assert [visit.data for visit in attempt.visits] == [
    {"latest": str(tmp_path / "artifacts/a/1/n"), "n": 1},
    {"latest": str(tmp_path / "artifacts/a/2/n"), "n": 2},
  ]


def test_last_outcome_ends_on_reentry(tmp_path: Path) -> None:
  machine = Machine(
    [
      Step(
        "a", lambda _: Outcome("on", last=True), {"on": Goto("b")}, 5, "error"
      ),
      Step("b", lambda _: Outcome("back"), {"back": Goto("a")}, 5),
    ]
  )
  attempt = machine.run(tmp_path)
  assert attempt.status == "error"
  assert attempt.reason == "a visit 2: entered after its last visit"


def test_a_raising_state_is_recorded_and_raised(tmp_path: Path) -> None:
  def step(ctx: Context) -> Outcome:
    raise OSError("disk full")

  seen: list[str | None] = []
  machine = Machine(
    [Step("a", step, {})], on_step=lambda a: seen.append(a.visits[-1].outcome)
  )
  with pytest.raises(OSError, match="disk full"):
    _ = machine.run(tmp_path)
  assert seen == [None, "raised"]


@pytest.mark.parametrize(
  "error",
  [
    pytest.param(SystemExit(143), id="sigterm-as-systemexit"),
    pytest.param(KeyboardInterrupt(), id="keyboardinterrupt"),
    pytest.param(stopping.Stopped(), id="stopped"),
  ],
)
def test_an_interrupted_state_is_recorded_as_interrupted(
  tmp_path: Path, error: BaseException
) -> None:
  def step(ctx: Context) -> Outcome:
    raise error

  seen: list[str | None] = []
  machine = Machine(
    [Step("a", step, {})], on_step=lambda a: seen.append(a.visits[-1].outcome)
  )
  with pytest.raises(type(error)):
    _ = machine.run(tmp_path)
  assert seen == [None, "interrupted"]


def test_a_raising_guard_or_on_step_adds_nothing_to_the_record(
  tmp_path: Path,
) -> None:
  attempts: list[Attempt] = []

  def guard(ctx: Context, state: State) -> str | None:
    attempts.append(ctx.attempt)
    raise RuntimeError("guard broke")

  states = [Step("a", lambda _: Outcome("x"), {"x": End("ok")})]
  with pytest.raises(RuntimeError, match="guard broke"):
    _ = Machine(states, guard=guard).run(tmp_path)
  assert attempts[0].visits == []

  def on_step(attempt: Attempt) -> None:
    attempts.append(attempt)
    raise RuntimeError("on_step broke")

  with pytest.raises(RuntimeError, match="on_step broke"):
    _ = Machine(states, on_step=on_step).run(tmp_path)
  assert attempts[1].visits == []

  def on_step_at_the_end(attempt: Attempt) -> None:
    attempts.append(attempt)
    if attempt.visits[-1].outcome is not None:
      raise RuntimeError("on_step broke")

  with pytest.raises(RuntimeError, match="on_step broke"):
    _ = Machine(states, on_step=on_step_at_the_end).run(tmp_path)
  assert [(visit.state, visit.outcome) for visit in attempts[-1].visits] == [
    ("a", "x")
  ]


def test_a_machine_subclass_may_override_guard(tmp_path: Path) -> None:
  class Capped(Machine):
    @override
    def guard(self, ctx: Context, state: State) -> str | None:
      return "capped" if ctx.visit == 2 else None

  attempt = Capped(
    [Step("a", lambda _: Outcome("again"), {"again": Goto("a")}, 5)]
  ).run(tmp_path)
  assert (attempt.status, attempt.reason) == ("budget", "a visit 2: capped")


def test_an_outcome_without_a_transition_is_an_error(tmp_path: Path) -> None:
  with pytest.raises(ValueError, match="a has no transition for outcome 'odd'"):
    _ = Machine([Step("a", lambda _: Outcome("odd"), {})]).run(tmp_path)


def test_a_goto_to_an_unknown_state_is_an_error() -> None:
  with pytest.raises(ValueError, match="unknown b"):
    _ = Machine([Step("a", lambda _: Outcome("x"), {"x": Goto("b")})])


def spend(cost_usd: float, outcome: str) -> Callable[[Context], Outcome]:
  """Return a step that records an action costing cost_usd.

  Args:
    cost_usd: What the action costs.
    outcome: The step's outcome.

  Returns:
    The step.
  """

  def step(ctx: Context) -> Outcome:
    now = datetime.now(UTC)
    ctx.record(ActionRecord("agent", "ok", {}, now, now, cost_usd))
    return Outcome(outcome)

  return step


def test_feedback_is_rendered_and_recorded_on_a_goto_with_a_report(
  tmp_path: Path,
) -> None:
  seen: list[str] = []

  def check(ctx: Context) -> Outcome:
    feedback = {"details": f"bad {ctx.visit}", "round": "theirs"}
    return Outcome("again" if ctx.visit == 1 else "fail", feedback=feedback)

  def author(ctx: Context) -> Outcome:
    if ctx.report is not None:
      seen.append(ctx.report.artifacts["feedback"].read_text())
      seen.append(ctx.artifacts.get("feedback").read_text())
    return Outcome("done")

  template = (
    "Fix it.\n\n## ${state}, visit ${visit}, round ${round}\n${details}"
  )
  attempt = Machine(
    [
      Step("author", author, {"done": Goto("check", report=False)}, 2),
      Step(
        "check",
        check,
        {"again": Goto("check", report=False), "fail": Goto("author")},
        3,
        feedback=template,
      ),
    ]
  ).run(tmp_path)
  text = "Fix it.\n\n## check, visit 2, round 1\nbad 2"
  assert seen == [text, text]
  assert attempt.visits[1].actions == []
  check_2 = attempt.visits[2]
  assert [(a.kind, a.status, a.cost_usd) for a in check_2.actions] == [
    ("report", "ok", 0.0)
  ]
  assert check_2.outputs == {
    "feedback": tmp_path / "artifacts/check/2/feedback.md"
  }


def test_no_feedback_without_a_report_a_template_or_variables(
  tmp_path: Path,
) -> None:
  def machine(goto: Goto, template: str | None, feedback: bool) -> Machine:
    variables = {"details": "bad"} if feedback else None
    return Machine(
      [
        Step(
          "a",
          lambda _: Outcome("x", feedback=variables),
          {"x": goto},
          2,
          feedback=template,
        ),
        Step("b", lambda _: Outcome("y"), {"y": End("done")}),
      ]
    )

  for goto, template, feedback in [
    (Goto("b", report=False), "${details}", True),
    (Goto("b"), None, True),
    (Goto("b"), "${details}", False),
  ]:
    attempt = machine(goto, template, feedback).run(tmp_path / "x")
    assert attempt.visits[0].actions == []
  attempt = machine(Goto("b"), "${details}", True).run(tmp_path / "y")
  assert [a.kind for a in attempt.visits[0].actions] == ["report"]


def test_a_feedback_template_missing_a_variable_is_an_error(
  tmp_path: Path,
) -> None:
  machine = Machine(
    [
      Step(
        "a",
        lambda _: Outcome("x", feedback={"details": "bad"}),
        {"x": Goto("a")},
        2,
        feedback="${details} ${hint}",
      )
    ]
  )
  with pytest.raises(ValueError, match="a visit 1: its feedback template"):
    _ = machine.run(tmp_path)


def test_the_cost_cap_blocks_a_state_before_the_guard(tmp_path: Path) -> None:
  guarded: list[str] = []

  def guard(ctx: Context, state: State) -> str | None:
    guarded.append(state.name)
    return None

  machine = Machine(
    [
      Step("a", spend(1.5, "x"), {"x": Goto("b")}, max_cost_usd=2.0),
      Step("b", spend(1.0, "y"), {"y": Goto("c")}, max_cost_usd=1.5),
      Step("c", spend(0.0, "z"), {"z": End("passed")}, max_cost_usd=0.75),
    ],
    guard=guard,
    max_cost_usd=3.0,
  )
  attempt = machine.run(tmp_path)
  assert attempt.status == "budget"
  reason = (
    "c visit 1: $2.50 spent and up to $0.75 more would pass the $3.00 the"
    " attempt may spend"
  )
  assert (attempt.reason, attempt.stopped) == (reason, reason)
  assert guarded == ["a", "b"]
  assert attempt.to_json()["budget"] == {
    "max_usd": 3.0,
    "cost_usd": 2.5,
    "stopped": reason,
  }


def test_the_cost_cap_lets_a_state_in_up_to_the_cap(tmp_path: Path) -> None:
  machine = Machine(
    [
      Step("a", spend(1.0, "x"), {"x": Goto("b")}),
      Step("b", spend(0.5, "y"), {"y": End("passed")}, max_cost_usd=0.5),
    ],
    max_cost_usd=1.5,
  )
  attempt = machine.run(tmp_path)
  assert (attempt.status, attempt.stopped) == ("passed", None)
  assert attempt.to_json()["budget"] == {
    "max_usd": 1.5,
    "cost_usd": 1.5,
    "stopped": None,
  }


def metered(
  ledger: dict[str, float], usd: float, cost_usd: float, outcome: str
) -> Callable[[Context], Outcome]:
  """Return a step whose calls cost usd but whose action reports cost_usd.

  Args:
    ledger: What the attempt's calls cost so far, under "usd".
    usd: What the step's calls cost.
    cost_usd: What its action reports, as one cut off by a timeout does not.
    outcome: The step's outcome.

  Returns:
    The step.
  """

  def step(ctx: Context) -> Outcome:
    ledger["usd"] += usd
    now = datetime.now(UTC)
    ctx.record(ActionRecord("agent", "ok", {}, now, now, cost_usd))
    return Outcome(outcome)

  return step


def test_the_cost_cap_and_the_record_take_what_spent_usd_measures(
  tmp_path: Path,
) -> None:
  ledger = {"usd": 0.0}
  seen: list[float] = []
  machine = Machine(
    [
      Step("a", metered(ledger, 2.0, 0.0, "x"), {"x": Goto("b")}),
      Step("b", spend(0.0, "y"), {"y": End("passed")}, max_cost_usd=1.5),
    ],
    on_step=lambda attempt: seen.append(attempt.cost_usd),
    max_cost_usd=3.0,
    spent_usd=lambda attempt: ledger["usd"],
  )
  attempt = machine.run(tmp_path)
  reason = (
    "b visit 1: $2.00 spent and up to $1.50 more would pass the $3.00 the"
    " attempt may spend"
  )
  assert (attempt.status, attempt.stopped) == ("budget", reason)
  assert attempt.to_json()["budget"] == {
    "max_usd": 3.0,
    "cost_usd": 2.0,
    "stopped": reason,
  }
  assert (seen[0], seen[-1]) == (0.0, 2.0)
  assert recorded_usd(attempt) == 0.0


def test_a_state_that_raises_leaves_what_was_spent_in_the_record(
  tmp_path: Path,
) -> None:
  ledger = {"usd": 0.0}
  seen: list[float] = []

  def cut_off(ctx: Context) -> Outcome:
    ledger["usd"] += 1.25
    raise RuntimeError("cut off")

  machine = Machine(
    [Step("a", cut_off, {"x": End("passed")})],
    on_step=lambda attempt: seen.append(attempt.cost_usd),
    spent_usd=lambda attempt: ledger["usd"],
  )
  with pytest.raises(RuntimeError, match="cut off"):
    _ = machine.run(tmp_path)
  assert seen == [0.0, 1.25]


def test_a_resumed_attempt_is_capped_by_what_it_spent_since_its_record(
  tmp_path: Path,
) -> None:
  ledger = {"usd": 0.0}
  killed = [True]
  steps: list[dict[str, object]] = []

  def check(ctx: Context) -> Outcome:
    if killed[0]:
      raise RuntimeError("killed")
    return Outcome("y")

  def machine() -> Machine:
    return Machine(
      [
        Step("a", metered(ledger, 1.0, 1.0, "x"), {"x": Goto("b")}),
        Step("b", check, {"y": End("passed")}, max_cost_usd=1.5),
      ],
      on_step=lambda attempt: steps.append(as_json(attempt)),
      max_cost_usd=4.0,
      spent_usd=lambda attempt: ledger["usd"],
    )

  with pytest.raises(RuntimeError, match="killed"):
    _ = machine().run(tmp_path)
  record = Attempt.from_json(tmp_path, steps[-1])
  assert record.cost_usd == 1.0
  # Calls the kill cut off reached the ledger after the last record.
  ledger["usd"], killed[0] = 2.8, False
  resumed = machine().resume(record)
  reason = (
    "b visit 1: $2.80 spent and up to $1.50 more would pass the $4.00 the"
    " attempt may spend"
  )
  assert (resumed.status, resumed.stopped) == ("budget", reason)


@pytest.mark.parametrize(
  ("metered_usd", "cost_usd"), [(None, 1.25), (4.0, 4.0)]
)
def test_carried_usd_counts_only_without_spent_usd(
  tmp_path: Path, metered_usd: float | None, cost_usd: float
) -> None:
  machine = Machine(
    [Step("a", spend(1.0, "x"), {"x": End("passed")})],
    spent_usd=None if metered_usd is None else (lambda attempt: metered_usd),
  )
  record = Attempt(tmp_path, graph=machine.graph())
  resumed = machine.resume(record, carried_usd=0.25)
  assert resumed.cost_usd == cost_usd
  assert [r.carried_usd for r in resumed.resumptions] == [0.25]


def test_a_record_read_back_keeps_its_cost_until_measured_again(
  tmp_path: Path,
) -> None:
  attempt = Machine(
    [Step("a", spend(1.0, "x"), {"x": End("passed")})],
    spent_usd=lambda attempt: 2.5,
  ).run(tmp_path)
  record = Attempt.from_json(tmp_path, as_json(attempt))
  assert record.cost_usd == 2.5
  measure(record, lambda attempt: 3.0)
  assert record.cost_usd == 3.0
  measure(record)
  assert record.cost_usd == 1.0


def test_a_blocking_guard_leaves_stopped_unset(tmp_path: Path) -> None:
  attempt = Machine(
    [Step("a", lambda _: Outcome("x"), {"x": End("ok")})],
    guard=lambda ctx, state: "no",
    max_cost_usd=1.0,
  ).run(tmp_path)
  assert (attempt.status, attempt.reason, attempt.stopped) == (
    "budget",
    "a visit 1: no",
    None,
  )


def faulted(
  ledger: dict[str, float], usd: float, fault: str | None
) -> Callable[[Context], Outcome]:
  """Return a step whose calls cost usd and that gives "error" with fault.

  Args:
    ledger: What the attempt's calls cost so far, under "usd".
    usd: What the step's calls cost.
    fault: The outcome's fault.

  Returns:
    The step.
  """

  def step(ctx: Context) -> Outcome:
    ledger["usd"] += usd
    return Outcome("error", reason="the run ended", fault=fault)

  return step


def test_an_end_whose_visit_hit_its_own_cap_ends_with_budget(
  tmp_path: Path,
) -> None:
  ledger = {"usd": 0.0}
  attempt = Machine(
    [Step("a", faulted(ledger, 0.5, "budget"), {"error": End("error")})],
    max_cost_usd=3.0,
    spent_usd=lambda attempt: ledger["usd"],
  ).run(tmp_path)
  assert (attempt.status, attempt.reason, attempt.stopped) == (
    "budget",
    "a visit 1: the run ended",
    None,
  )


def test_an_end_whose_visit_hit_its_own_cap_after_a_mark_keeps_it(
  tmp_path: Path,
) -> None:
  ledger = {"usd": 0.0}
  attempt = Machine(
    [
      Step("a", lambda _: Outcome("x"), {"x": Goto("b", mark="too_hard")}),
      Step("b", faulted(ledger, 0.5, "budget"), {"error": End("error")}),
    ],
  ).run(tmp_path)
  assert (attempt.status, attempt.kept_visit) == ("too_hard", 0)
  assert attempt.reason == "b visit 1: the run ended"


@pytest.mark.parametrize(
  ("fault", "usd", "status"),
  [("model", 3.0, "budget"), ("model", 2.99, "error"), (None, 3.0, "error")],
)
def test_an_end_with_a_fault_once_the_attempt_spent_its_cap_ends_with_budget(
  tmp_path: Path, fault: str | None, usd: float, status: str
) -> None:
  ledger = {"usd": 0.0}
  attempt = Machine(
    [Step("a", faulted(ledger, usd, fault), {"error": End("error")})],
    max_cost_usd=3.0,
    spent_usd=lambda attempt: ledger["usd"],
  ).run(tmp_path)
  assert attempt.status == status
  if status == "budget":
    assert attempt.reason == (
      "a visit 1: $3.00 spent of the $3.00 the attempt may spend; the run ended"
    )


def test_end_on_last_visit_ends_instead_of_going_back(tmp_path: Path) -> None:
  def machine(last: bool) -> Machine:
    return Machine(
      [
        Step("author", lambda _: Outcome("done"), {"done": Goto("judge")}, 5),
        Step(
          "judge",
          lambda _: Outcome("easy", last=last, feedback={"details": "d"}),
          {"easy": Goto("author", mark="too_easy", end_on_last_visit=True)},
          2,
          "exhausted",
          feedback="${details}",
        ),
      ]
    )

  attempt = machine(last=False).run(tmp_path / "cap")
  assert path_of(attempt) == [
    ("author", "done"),
    ("judge", "easy"),
    ("author", "done"),
    ("judge", "easy"),
  ]
  assert (attempt.status, attempt.kept_visit) == ("too_easy", 3)
  assert attempt.reason == "judge visit 2: its last visit, not going to author"
  assert [a.kind for a in attempt.visits[1].actions] == ["report"]
  assert attempt.visits[3].actions == []

  attempt = machine(last=True).run(tmp_path / "last")
  assert path_of(attempt) == [("author", "done"), ("judge", "easy")]
  assert attempt.reason == "judge visit 1: its last visit, not going to author"


def test_end_on_last_visit_without_a_mark_ends_with_on_exhausted(
  tmp_path: Path,
) -> None:
  attempt = Machine(
    [
      Step(
        "a",
        lambda _: Outcome("x"),
        {"x": Goto("a", end_on_last_visit=True)},
        2,
        "exhausted",
      )
    ]
  ).run(tmp_path)
  assert (attempt.status, attempt.kept_visit) == ("exhausted", None)
  assert attempt.reason == "a visit 2: its last visit, not going to a"


def test_end_on_last_visit_ends_when_a_state_on_the_way_back_is_spent(
  tmp_path: Path,
) -> None:
  # judge has visits left, but every way back to it passes review, whose
  # one visit is spent: no later round could reach judge.
  attempt = Machine(
    [
      Step("author", lambda _: Outcome("done"), {"done": Goto("review")}, 5),
      Step("review", lambda _: Outcome("pass"), {"pass": Goto("judge")}, 1),
      Step(
        "judge",
        lambda _: Outcome("easy"),
        {"easy": Goto("author", mark="too_easy", end_on_last_visit=True)},
        3,
        "exhausted",
      ),
    ]
  ).run(tmp_path)
  assert path_of(attempt) == [
    ("author", "done"),
    ("review", "pass"),
    ("judge", "easy"),
  ]
  assert (attempt.status, attempt.kept_visit) == ("too_easy", 2)
  assert attempt.reason == "judge visit 1: its last visit, not going to author"


def _seeded(rounds: str | None) -> Machine:
  # seed dispatches to a path that visits "work", or to one that does not.
  def seed(ctx: Context) -> Outcome:
    return Outcome("again" if ctx.visit == 2 else "work")

  return Machine(
    [
      Step("seed", seed, {"work": Goto("work"), "again": Goto("tune")}, 2),
      Step("work", lambda _: Outcome("done"), {"done": Goto("seed")}, 2),
      Step("tune", lambda _: Outcome("done"), {"done": End("ok")}),
    ],
    rounds=rounds,
  )


def test_rounds_count_the_visits_of_the_state_the_machine_names(
  tmp_path: Path,
) -> None:
  attempt = _seeded("work").run(tmp_path)
  assert [(v.state, v.visit, v.round) for v in attempt.visits] == [
    ("seed", 1, 0),
    ("work", 1, 1),
    ("seed", 2, 1),
    ("tune", 1, 1),
  ]
  assert attempt.to_json()["round"] == 1


def test_a_rounds_state_never_visited_leaves_the_round_at_0(
  tmp_path: Path,
) -> None:
  attempt = Machine(
    [
      Step("seed", lambda _: Outcome("go"), {"go": Goto("tune")}),
      Step("tune", lambda _: Outcome("done"), {"done": End("ok")}),
      Step("work", lambda _: Outcome("done"), {"done": End("ok")}),
    ],
    rounds="work",
  ).run(tmp_path)
  assert [(v.state, v.round) for v in attempt.visits] == [
    ("seed", 0),
    ("tune", 0),
  ]
  assert attempt.to_json()["round"] == 0


def test_without_rounds_the_first_state_counts_them(tmp_path: Path) -> None:
  attempt = _seeded(None).run(tmp_path)
  assert [(v.state, v.round) for v in attempt.visits] == [
    ("seed", 1),
    ("work", 1),
    ("seed", 2),
    ("tune", 2),
  ]


def test_rounds_naming_an_unknown_state_is_an_error() -> None:
  with pytest.raises(ValueError, match="rounds names unknown nowhere"):
    _ = _seeded("nowhere")


def test_each_visit_records_its_round(tmp_path: Path) -> None:
  def check(ctx: Context) -> Outcome:
    if ctx.round == 1:
      return Outcome("back")
    return Outcome("again" if ctx.visit == 2 else "end")

  attempt = Machine(
    [
      Step("author", lambda _: Outcome("done"), {"done": Goto("check")}, 3),
      Step(
        "check",
        check,
        {"back": Goto("author"), "again": Goto("check"), "end": End("ok")},
        3,
      ),
    ]
  ).run(tmp_path)
  assert [(v.state, v.visit, v.round) for v in attempt.visits] == [
    ("author", 1, 1),
    ("check", 1, 1),
    ("author", 2, 2),
    ("check", 2, 2),
    ("check", 3, 2),
  ]
  record = attempt.to_json()
  visits = cast(list[dict[str, object]], record["visits"])
  assert [visit["round"] for visit in visits] == [1, 1, 2, 2, 2]
  assert record["round"] == 2
  assert json.dumps(record["budget"]) == (
    '{"max_usd": null, "cost_usd": 0.0, "stopped": null}'
  )


def build_and_judge(
  deliverables: dict[str, Deliverable], on_step: Callable[[Attempt], None]
) -> Machine:
  """Build, then judge: easy (marked too_easy) once, then hard, until a cap.

  Args:
    deliverables: The machine's deliverables.
    on_step: Its on_step.

  Returns:
    The machine.
  """
  actions = Actions()

  def build(ctx: Context) -> Outcome:
    argv = ["sh", "-c", f'echo {ctx.visit} > "$DATAFORGE_OUTPUTS/task"']
    assert actions.script(ctx, ScriptSpec(argv, outputs=["task"])).ok
    return Outcome("done")

  def judge(ctx: Context) -> Outcome:
    return Outcome("easy" if ctx.visit == 1 else "hard")

  return Machine(
    [
      Step("build", build, {"done": Goto("judge")}, 3),
      Step(
        "judge",
        judge,
        {"easy": Goto("build", mark="too_easy"), "hard": Goto("build")},
        2,
      ),
      Step("never", judge, {"easy": End("passed")}),
    ],
    on_step=on_step,
    deliverables=deliverables,
  )


def test_deliverables_take_the_kept_last_or_nth_visit(tmp_path: Path) -> None:
  def files(output: str, visit: Literal["kept", "last"] | int) -> Deliverable:
    return Deliverable("files", output, Source("build", output, visit))

  seen: list[object] = []
  machine = build_and_judge(
    {
      "kept": files("task", "kept"),
      "last": files("task", "last"),
      "second": files("task", 2),
      "fifth": files("task", 5),
      "missing": files("absent", "kept"),
      "marked": Deliverable("files", "verdict", Source("judge", "verdict")),
      "report": Deliverable(
        "report",
        "Report",
        Source("build", "task"),
        table=Source("never", "table.json"),
      ),
    },
    lambda attempt: seen.append(attempt.to_json()["deliverables"]),
  )
  attempt = machine.run(tmp_path)
  assert (attempt.status, attempt.kept_visit) == ("too_easy", 1)

  def found(name: str, kind: str, visit: int, index: int) -> dict[str, object]:
    return {
      "name": name,
      "kind": kind,
      "title": "task" if kind == "files" else "Report",
      "state": "build",
      "output": "task",
      "visit": visit,
      "visit_index": index,
      "path": f"artifacts/build/{visit}/task",
      "reason": None,
    }

  def unreached(name: str, output: str, reason: str) -> dict[str, object]:
    return {
      "name": name,
      "kind": "files",
      "title": output,
      "state": "build",
      "output": output,
      "visit": None,
      "visit_index": None,
      "path": None,
      "reason": reason,
    }

  expected = [
    found("kept", "files", 1, 0),
    found("last", "files", 3, 4),
    found("second", "files", 2, 2),
    unreached("fifth", "task", "build has no visit 5"),
    {
      **unreached("missing", "absent", "build visit 1 has no output absent"),
      "visit": 1,
      "visit_index": 0,
    },
    {
      **unreached("marked", "verdict", "judge visit 1 has no output verdict"),
      "state": "judge",
      "visit": 1,
      "visit_index": attempt.kept_visit,
    },
    {
      **found("report", "report", 1, 0),
      "table": {
        "state": "never",
        "output": "table.json",
        "visit": None,
        "visit_index": None,
        "path": None,
        "reason": "never was never visited",
      },
    },
  ]
  assert attempt.to_json()["deliverables"] == expected
  assert seen[-1] == expected
  first = cast(list[dict[str, object]], seen[0])
  assert [entry["path"] for entry in first] == [None] * len(expected)
  assert attempt.to_json()["graph"] == machine.graph()


def judged_until_tied(keep_mark: str | None) -> Machine:
  """Build, then judge: easy (mark easy), hard (mark hard), then tied.

  Args:
    keep_mark: The mark the End of tied keeps.

  Returns:
    The machine, which delivers the kept build's task.
  """
  actions = Actions()

  def build(ctx: Context) -> Outcome:
    argv = ["sh", "-c", f'echo {ctx.visit} > "$DATAFORGE_OUTPUTS/task"']
    assert actions.script(ctx, ScriptSpec(argv, outputs=["task"])).ok
    return Outcome("done")

  def judge(ctx: Context) -> Outcome:
    return Outcome(["easy", "hard", "tied"][ctx.visit - 1])

  return Machine(
    [
      Step("build", build, {"done": Goto("judge")}, 3),
      Step(
        "judge",
        judge,
        {
          "easy": Goto("build", mark="easy"),
          "hard": Goto("build", mark="hard"),
          "tied": End("too_easy", keep_mark=keep_mark),
        },
        3,
      ),
    ],
    deliverables={
      "task": Deliverable("files", "Task", Source("build", "task"))
    },
  )


@pytest.mark.parametrize(
  ("keep_mark", "kept", "build"),
  [("easy", 1, 1), ("hard", 3, 2), (None, None, 3), ("never", None, 3)],
)
def test_an_end_keeps_the_last_visit_with_its_keep_mark(
  tmp_path: Path, keep_mark: str | None, kept: int | None, build: int
) -> None:
  machine = judged_until_tied(keep_mark)
  attempt = machine.run(tmp_path)
  assert path_of(attempt)[-1] == ("judge", "tied")
  assert (attempt.status, attempt.kept_visit) == ("too_easy", kept)
  assert attempt.final["build"].visit == build
  [task] = attempt.deliverables
  assert task["path"] == f"artifacts/build/{build}/task"
  [edge] = [e for e in machine.graph()["edges"] if e["outcome"] == "tied"]
  assert edge.get("keep_mark") == keep_mark
  assert ("keep_mark" in edge) is (keep_mark is not None)
  assert all(
    "keep_mark" not in e for e in machine.graph()["edges"] if e != edge
  )


def swap_or_zero(writes: bool, deliverables: dict[str, Deliverable]) -> Machine:
  """Build, then regrade: zero (mark too_hard), then again, until a cap.

  Args:
    writes: Whether regrade's first visit writes a task, as its second does.
    deliverables: The machine's deliverables.

  Returns:
    The machine.
  """
  actions = Actions()

  def write(ctx: Context) -> None:
    argv = ["sh", "-c", f'echo {ctx.visit} > "$DATAFORGE_OUTPUTS/task"']
    assert actions.script(ctx, ScriptSpec(argv, outputs=["task"])).ok

  def build(ctx: Context) -> Outcome:
    write(ctx)
    return Outcome("done")

  def regrade(ctx: Context) -> Outcome:
    if writes or ctx.visit == 2:
      write(ctx)
    return Outcome("zero" if ctx.visit == 1 else "again")

  return Machine(
    [
      Step("build", build, {"done": Goto("regrade")}, 2),
      Step(
        "regrade",
        regrade,
        {"zero": Goto("build", mark="too_hard"), "again": Goto("build")},
        2,
      ),
      Step("never", regrade, {"zero": End("passed")}),
    ],
    deliverables=deliverables,
  )


@pytest.mark.parametrize(("writes", "kept"), [(False, 0), (True, 1)])
def test_a_source_of_several_states_takes_their_last_visit_with_the_output(
  tmp_path: Path, writes: bool, kept: int
) -> None:
  both = ("build", "regrade")
  machine = swap_or_zero(
    writes,
    {
      "kept": Deliverable("files", "Task", Source(both, "task")),
      "last": Deliverable("files", "Task", Source(both, "task", "last")),
      "one": Deliverable("files", "Task", Source("regrade", "task")),
      "none": Deliverable("files", "Task", Source(("never", "regrade"), "x")),
      "unvisited": Deliverable("files", "Task", Source(("never",), "task")),
    },
  )
  attempt = machine.run(tmp_path)
  assert path_of(attempt) == [
    ("build", "done"),
    ("regrade", "zero"),
    ("build", "done"),
    ("regrade", "again"),
  ]
  assert (attempt.status, attempt.kept_visit) == ("too_hard", 1)
  found = {entry["name"]: entry for entry in attempt.deliverables}
  state, visit = attempt.visits[kept].state, attempt.visits[kept].visit
  assert found["kept"] | {"name": None} == {
    "name": None,
    "kind": "files",
    "title": "Task",
    "state": state,
    "output": "task",
    "visit": visit,
    "visit_index": kept,
    "path": f"artifacts/{state}/{visit}/task",
    "reason": None,
  }
  assert (found["last"]["state"], found["last"]["visit_index"]) == (
    "regrade",
    3,
  )
  assert found["one"]["visit_index"] == 1
  assert (found["one"]["path"] is None) is not writes
  assert {k: found["none"][k] for k in ("state", "visit", "reason")} == {
    "state": "never",
    "visit": None,
    "reason": (
      "no visit of never or regrade up to the kept one has the output x"
    ),
  }
  assert found["unvisited"]["reason"] == "never was never visited"


def test_an_end_resolves_deliverables_before_the_last_on_step(
  tmp_path: Path,
) -> None:
  def build(ctx: Context) -> Outcome:
    argv = ["sh", "-c", 'echo done > "$DATAFORGE_OUTPUTS/task"']
    assert Actions().script(ctx, ScriptSpec(argv, outputs=["task"])).ok
    return Outcome("done")

  seen: list[object] = []
  attempt = Machine(
    [Step("build", build, {"done": End("passed")})],
    on_step=lambda attempt: seen.append(attempt.to_json()["deliverables"]),
    deliverables={
      "task": Deliverable("files", "Task", Source("build", "task"))
    },
  ).run(tmp_path)
  assert attempt.status == "passed"
  # Begun, claimed, recorded: while the visit runs it is not taken.
  unreached = {
    "name": "task",
    "kind": "files",
    "title": "Task",
    "state": "build",
    "output": "task",
    "visit": None,
    "visit_index": None,
    "path": None,
    "reason": "build was never visited",
  }
  assert seen == [
    [unreached],
    [unreached],
    [unreached],
    [
      unreached
      | {
        "visit": 1,
        "visit_index": 0,
        "path": "artifacts/build/1/task",
        "reason": None,
      }
    ],
  ]


def test_a_running_attempt_delivers_what_its_ended_visits_made(
  tmp_path: Path,
) -> None:
  seen: list[tuple[str, int, str | None, object]] = []

  def on_step(attempt: Attempt) -> None:
    last = attempt.visits[-1]
    [entry] = cast(list[dict[str, object]], attempt.to_json()["deliverables"])
    seen.append((last.state, last.visit, last.outcome, entry["path"]))

  machine = build_and_judge(
    {"task": Deliverable("files", "task", Source("build", "task", "last"))},
    on_step,
  )
  attempt = machine.run(tmp_path)
  assert attempt.status == "too_easy"
  task = "artifacts/build/{}/task".format
  assert seen == [
    ("build", 1, None, None),
    ("build", 1, None, None),
    ("build", 1, None, None),
    ("build", 1, "done", task(1)),
    ("judge", 1, None, task(1)),
    ("judge", 1, "easy", task(1)),
    # While build runs again, the task is the one its last visit made.
    ("build", 2, None, task(1)),
    ("build", 2, None, task(1)),
    ("build", 2, None, task(1)),
    ("build", 2, "done", task(2)),
    ("judge", 2, None, task(2)),
    ("judge", 2, "hard", task(2)),
    ("build", 3, None, task(2)),
    ("build", 3, None, task(2)),
    ("build", 3, None, task(2)),
    ("build", 3, "done", task(3)),
    # judge's cap ends the attempt before its third visit.
    ("build", 3, "done", task(3)),
  ]


class NamedTrials:
  """A trial runner whose trials have names, directories and traces.

  The trial of output trial-<i> solves when i is even; only even ones have a
  trace, and trial-1's is outside its output directory, as is trial-2's own
  directory.
  """

  def memory_gb(self, limits: Limits) -> float:
    return 0.0

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    i = int(outdir.name.removeprefix("trial-"))
    trial = outdir / f"task__{i}"
    (trial / "agent").mkdir(parents=True)
    trace = trial / "agent" / "claude-code.txt"
    _ = trace.write_text("{}\n")
    if i == 1:
      trace = task_dir / "elsewhere.txt"
    reward = 1.0 if i % 2 == 0 else 0.0
    return TrialResult(
      {"reward": reward},
      None,
      0.0,
      1.0,
      {},
      id=f"task__{i}",
      path=task_dir if i == 2 else trial,
      trace=trace if i % 2 == 0 or i == 1 else None,
      trace_format="claude-code-stream-json",
    )


def test_trials_list_each_trial_with_its_id_trace_and_result(
  tmp_path: Path,
) -> None:
  actions = Actions(trial_runner=NamedTrials())
  verdict = '{"data": {"solved": 2}}'

  def solve(ctx: Context) -> Outcome:
    for i in (10, 2, 1, 0):
      _ = actions.trial(ctx, f"trial-{i}", tmp_path, "oracle", Limits(1))
    write = [
      "sh",
      "-c",
      f"echo '{verdict}' > \"$DATAFORGE_OUTPUTS/verdict.json\"",
    ]
    assert actions.script(ctx, ScriptSpec(write, outputs=["verdict.json"])).ok
    return Outcome("done", {"solved": 2})

  def trials(value: Value) -> Deliverable:
    return Deliverable("trials", "pass@k", Source("solve"), value=value)

  seen: list[object] = []
  attempt = Machine(
    [Step("solve", solve, {"done": End("passed")})],
    on_step=lambda attempt: seen.append(attempt.to_json()["deliverables"]),
    deliverables={
      "from_verdict": trials(Value(["data", "solved"], "verdict.json")),
      "from_data": trials(Value(["solved"])),
      "no_key": trials(Value(["data", "k"], "verdict.json")),
    },
  ).run(tmp_path / "attempt")
  entries = cast(list[dict[str, object]], attempt.to_json()["deliverables"])
  assert seen[-1] == entries
  first = entries[0]
  assert {key: first[key] for key in ("path", "reason", "k")} == {
    "path": "artifacts/solve/1",
    "reason": None,
    "k": 4,
  }
  base = "artifacts/solve/1"

  def trial(i: int, traced: bool) -> dict[str, object]:
    path = f"{base}/trial-{i}/task__{i}"
    trace = {
      "path": f"{path}/agent/claude-code.txt",
      "format": "claude-code-stream-json",
    }
    reward = 1.0 if i % 2 == 0 else 0.0
    return {
      "id": f"task__{i}",
      "output": f"trial-{i}",
      "path": path,
      "trace": trace if traced else None,
      "source": None,
      "log": None,
      "verdict": None,
      "result": {
        "reward": reward,
        "rewards": {"reward": reward},
        "exception": None,
      },
    }

  outside = {**trial(2, True), "path": f"{base}/trial-2"}
  assert first["trials"] == [
    trial(0, True),
    trial(1, False),
    outside,
    trial(10, True),
  ]
  assert [entry["value"] for entry in entries] == [
    {
      "output": "verdict.json",
      "field": ["data", "solved"],
      "value": 2,
      "reason": None,
    },
    {"output": None, "field": ["solved"], "value": 2, "reason": None},
    {
      "output": "verdict.json",
      "field": ["data", "k"],
      "value": None,
      "reason": "verdict.json has no key k",
    },
  ]


def test_a_running_visit_lists_the_trials_that_have_started(
  tmp_path: Path,
) -> None:
  actions = Actions(trial_runner=NamedTrials())

  def solve(ctx: Context) -> Outcome:
    for i in (0, 1):
      _ = actions.trial(ctx, f"trial-{i}", tmp_path, "oracle", Limits(1))
    return Outcome("done", {"solved": 1})

  seen: list[tuple[object, list[dict[str, object]]]] = []
  keys: list[set[str]] = []

  def on_step(attempt: Attempt) -> None:
    record = attempt.to_json()
    [entry] = cast(list[dict[str, object]], record["deliverables"])
    seen.append((entry["k"], cast(list[dict[str, object]], entry["trials"])))
    keys.append(set(visits_of(record)[-1]))

  attempt = Machine(
    [Step("solve", solve, {"done": End("passed")})],
    on_step=on_step,
    deliverables={
      "pass_at_k": Deliverable(
        "trials", "pass@k", Source("solve"), value=Value(["solved"])
      )
    },
  ).run(tmp_path / "attempt")
  assert attempt.status == "passed"
  results = [
    (k, [(trial["output"], trial["result"] is None) for trial in trials])
    for k, trials in seen
  ]
  assert results == [
    (0, []),
    (1, [("trial-0", True)]),
    (1, [("trial-0", False)]),
    (2, [("trial-0", False), ("trial-1", True)]),
    (2, [("trial-0", False), ("trial-1", False)]),
    (2, [("trial-0", False), ("trial-1", False)]),
  ]
  assert seen[3][1][1] == {
    "id": None,
    "output": "trial-1",
    "path": "artifacts/solve/1/trial-1",
    "trace": None,
    "source": None,
    "log": None,
    "verdict": None,
    "result": None,
  }
  # Which trials run is for the deliverables only, not part of a visit.
  assert all("running" not in visit for visit in keys)


def test_a_trial_s_source_reads_back_and_is_null_in_an_older_record(
  tmp_path: Path,
) -> None:
  actions = Actions(trial_runner=NamedTrials())
  root = tmp_path / "attempt"
  source = root / "artifacts/solve/1/trial-0/task__0"

  def solve(ctx: Context) -> Outcome:
    _ = actions.trial(ctx, "trial-0", tmp_path, "oracle", Limits(1))
    _ = actions.trial(ctx, "trial-1", tmp_path, "oracle", Limits(1), source)
    return Outcome("done")

  record = as_json(
    Machine([Step("solve", solve, {"done": End("passed")})]).run(root)
  )
  path = ["visits", 0, "actions", 1, "trial", "source"]
  read = Attempt.from_json(root, record)
  assert [
    action.trial and action.trial.source for action in read.visits[0].actions
  ] == [None, source]
  assert read.to_json() == record
  older = Attempt.from_json(root, broken(record, path, KeyError))
  trial = older.visits[0].actions[1].trial
  assert trial is not None and trial.source is None
  with pytest.raises(
    RecordError, match=r"trial\.source: '\.\./x' is not inside"
  ):
    _ = Attempt.from_json(root, broken(record, path, "../x"))


@pytest.mark.parametrize("source", ["/elsewhere", "attempt/../elsewhere"])
def test_a_trial_with_a_source_outside_the_attempt_does_not_run(
  tmp_path: Path, source: str
) -> None:
  actions = Actions(trial_runner=NamedTrials())
  root = tmp_path / "attempt"

  def solve(ctx: Context) -> Outcome:
    _ = actions.trial(
      ctx, "trial-0", tmp_path, "oracle", Limits(1), tmp_path / source
    )
    return Outcome("done")

  with pytest.raises(ValueError, match=r"trial-0: source .* is not inside"):
    _ = Machine([Step("solve", solve, {"done": End("passed")})]).run(root)
  assert not (root / "artifacts/solve/1/trial-0").exists()


class OutputTrials:
  """A trial runner whose trials solve when their output ends in 1, 3, ... 9."""

  def memory_gb(self, limits: Limits) -> float:
    return 0.0

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    reward = 1.0 if outdir.name[-1] in "13579" else 0.0
    return TrialResult({"reward": reward}, None, 0.0, 1.0, {})


REGRADE_OUTPUTS = (
  "regrade-2",
  "regrade-golden-1",
  "regrade-10",
  "regrade-reference",
  "regrade-1",
)


def regrade(ctx: Context) -> Outcome:
  actions = Actions(trial_runner=OutputTrials())
  for output in REGRADE_OUTPUTS:
    _ = actions.trial(ctx, output, ctx.outdir, "oracle", Limits(1))
  return Outcome("done", {"solved": 1})


def test_a_match_lists_only_the_trials_whose_output_matches(
  tmp_path: Path,
) -> None:
  seen: list[list[tuple[str, bool]]] = []

  def on_step(attempt: Attempt) -> None:
    entry = cast(list[dict[str, object]], attempt.to_json()["deliverables"])[0]
    trials = cast(list[dict[str, object]], entry["trials"])
    seen.append([(cast(str, t["output"]), t["result"] is None) for t in trials])

  attempt = Machine(
    [Step("regrade", regrade, {"done": End("passed")})],
    on_step=on_step,
    deliverables={
      "regrade": Deliverable(
        "trials",
        "Regrade",
        Source("regrade"),
        value=Value(["solved"]),
        match="regrade-[0-9]*",
      ),
      "every": Deliverable("trials", "Every trial", Source("regrade")),
    },
  ).run(tmp_path / "attempt")
  chosen, every = cast(
    list[dict[str, object]], attempt.to_json()["deliverables"]
  )
  assert (chosen["match"], chosen["k"]) == ("regrade-[0-9]*", 3)
  assert [
    t["output"] for t in cast(list[dict[str, str]], chosen["trials"])
  ] == [
    "regrade-1",
    "regrade-2",
    "regrade-10",
  ]
  assert (every["match"], every["k"]) == (None, len(REGRADE_OUTPUTS))
  # A trial that has started is listed only when it matches, too.
  assert any(("regrade-10", True) in step for step in seen)
  assert all(
    output in ("regrade-1", "regrade-2", "regrade-10")
    for step in seen
    for output, _ in step
  )


def judged(ctx: Context) -> Outcome:
  actions = Actions(trial_runner=OutputTrials())
  for output in ("regrade-1", "regrade-2", "regrade-3"):
    _ = actions.trial(ctx, output, ctx.outdir, "oracle", Limits(1))
  verdicts = {"regrade-1": "missed", "regrade-2": "passes", "regrade-3": 3}
  return Outcome("done", {"verdicts": verdicts, "list": []})


@pytest.mark.parametrize(
  ("field", "verdicts", "reason"),
  [
    (["verdicts"], ["missed", "passes", None], None),
    (["none"], [None, None, None], "the outcome data has no key none"),
    (["list"], [None, None, None], "the outcome data at list is not an object"),
  ],
)
def test_trials_take_their_verdicts_by_output_from_what_the_deliverable_names(
  tmp_path: Path,
  field: list[str],
  verdicts: list[str | None],
  reason: str | None,
) -> None:
  attempt = Machine(
    [Step("regrade", judged, {"done": End("passed")})],
    deliverables={
      "regrade": Deliverable(
        "trials", "Regrade", Source("regrade"), verdicts=Value(field)
      )
    },
  ).run(tmp_path / "attempt")
  [entry] = cast(list[dict[str, object]], attempt.to_json()["deliverables"])
  trials = cast(list[dict[str, object]], entry["trials"])
  assert [trial["verdict"] for trial in trials] == verdicts
  assert entry["verdicts"] == {"output": None, "field": field, "reason": reason}


def test_trials_without_verdicts_declared_have_none(tmp_path: Path) -> None:
  attempt = Machine(
    [Step("regrade", judged, {"done": End("passed")})],
    deliverables={"regrade": Deliverable("trials", "R", Source("regrade"))},
  ).run(tmp_path / "attempt")
  [entry] = cast(list[dict[str, object]], attempt.to_json()["deliverables"])
  assert entry["verdicts"] is None
  trials = cast(list[dict[str, object]], entry["trials"])
  assert [trial["verdict"] for trial in trials] == [None, None, None]


class LoggedTrials:
  """A trial runner whose trial-0 logs in its output directory, trial-1 not."""

  def memory_gb(self, limits: Limits) -> float:
    return 0.0

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    log = outdir / "harbor.log" if outdir.name == "trial-0" else task_dir / "x"
    return TrialResult({"reward": 1.0}, None, 0.0, 1.0, {}, log=log)


def test_a_trial_s_log_inside_its_directory_is_recorded_and_reads_back(
  tmp_path: Path,
) -> None:
  actions = Actions(trial_runner=LoggedTrials())
  root = tmp_path / "attempt"

  def solve(ctx: Context) -> Outcome:
    for i in (0, 1):
      _ = actions.trial(ctx, f"trial-{i}", tmp_path, "oracle", Limits(1))
    return Outcome("done")

  record = as_json(
    Machine(
      [Step("solve", solve, {"done": End("passed")})],
      deliverables={"t": Deliverable("trials", "T", Source("solve"))},
    ).run(root)
  )
  trials = [
    cast(dict[str, object], action["trial"])
    for action in cast(list[dict[str, object]], visits_of(record)[0]["actions"])
  ]
  assert [trial["log"] for trial in trials] == [
    "artifacts/solve/1/trial-0/harbor.log",
    None,
  ]
  [entry] = cast(list[dict[str, object]], record["deliverables"])
  listed = cast(list[dict[str, object]], entry["trials"])
  assert [trial["log"] for trial in listed] == [trials[0]["log"], None]
  assert Attempt.from_json(root, record).to_json() == record
  older = broken(record, ["visits", 0, "actions", 0, "trial", "log"], KeyError)
  read = Attempt.from_json(root, older).visits[0].actions[0].trial
  assert read is not None and read.log is None


def test_deliverables_of_an_ended_attempt_resolve_as_its_end_would(
  tmp_path: Path,
) -> None:
  every = {"every": Deliverable("trials", "Every trial", Source("regrade"))}
  chosen = {
    "chosen": Deliverable(
      "trials", "Chosen", Source("regrade"), match="regrade-[0-9]*"
    )
  }
  states = [Step("regrade", regrade, {"done": End("passed")})]
  root = tmp_path / "attempt"
  records: list[dict[str, object]] = []
  ran = Machine(
    states, deliverables=every, on_step=lambda a: records.append(a.to_json())
  ).run(root)
  attempt = Attempt.from_json(root, records[-1])
  assert Machine(states, deliverables=every).deliverables_of(attempt) == (
    ran.deliverables
  )
  [entry] = Machine(states, deliverables=chosen).deliverables_of(attempt)
  assert (entry["name"], entry["k"]) == ("chosen", 3)

  running = Attempt.from_json(root, records[1])
  assert running.visits[-1].outcome is None
  with pytest.raises(ValueError, match="has no outcome"):
    _ = Machine(states, deliverables=every).deliverables_of(running)

  for ending in ENDING:
    ended = Attempt.from_json(root, records[-1])
    ended.visits[-1].outcome = ending
    [entry] = Machine(states, deliverables=every).deliverables_of(ended)
    assert (entry["path"], entry["k"]) == (None, 0)
    assert entry["reason"] == f"regrade visit 1 {ending}"


@pytest.mark.parametrize(
  ("error", "outcome"),
  [
    pytest.param(RuntimeError("proxy exited"), "raised", id="raised"),
    pytest.param(KeyboardInterrupt(), "interrupted", id="interrupted"),
  ],
)
def test_a_visit_that_does_not_finish_leaves_what_the_visits_before_it_made(
  tmp_path: Path, error: BaseException, outcome: str
) -> None:
  def write(name: str) -> Callable[[Context], Outcome]:
    def run(ctx: Context) -> Outcome:
      argv = ["sh", "-c", f'echo done > "$DATAFORGE_OUTPUTS/{name}"']
      assert Actions().script(ctx, ScriptSpec(argv, outputs=[name])).ok
      if name == "partial":
        raise error
      return Outcome("done")

    return run

  states = [
    Step("build", write("task"), {"done": Goto("solve")}),
    Step("solve", write("partial"), {"done": End("passed")}),
  ]
  deliverables = {
    "task": Deliverable("files", "Task", Source("build", "task")),
    "partial": Deliverable("files", "Partial", Source("solve", "partial")),
  }
  records: list[dict[str, object]] = []
  machine = Machine(
    states,
    deliverables=deliverables,
    on_step=lambda attempt: records.append(attempt.to_json()),
  )
  with pytest.raises(type(error)):
    _ = machine.run(tmp_path)

  last = records[-1]
  visits = cast(list[dict[str, object]], last["visits"])
  assert visits[-1]["outcome"] == outcome
  task, partial = cast(list[dict[str, object]], last["deliverables"])
  assert (task["path"], task["visit_index"]) == ("artifacts/build/1/task", 0)
  # What the visit that did not finish wrote is not delivered.
  assert (partial["path"], partial["reason"]) == (
    None,
    f"solve visit 1 {outcome}",
  )
  read_back = Attempt.from_json(tmp_path, last)
  assert machine.deliverables_of(read_back) == last["deliverables"]


def test_a_deliverable_of_several_states_names_the_visit_that_did_not_finish(
  tmp_path: Path,
) -> None:
  def solve(ctx: Context) -> Outcome:
    argv = ["sh", "-c", 'echo done > "$DATAFORGE_OUTPUTS/partial"']
    assert Actions().script(ctx, ScriptSpec(argv, outputs=["partial"])).ok
    raise RuntimeError("proxy exited")

  records: list[dict[str, object]] = []
  machine = Machine(
    [
      Step("build", lambda _: Outcome("done"), {"done": Goto("solve")}),
      Step("solve", solve, {"done": End("passed")}),
    ],
    deliverables={
      "partial": Deliverable(
        "files", "Partial", Source(("build", "solve"), "partial")
      ),
    },
    on_step=lambda attempt: records.append(attempt.to_json()),
  )
  with pytest.raises(RuntimeError):
    _ = machine.run(tmp_path)

  # build ran before it, without the output, so only solve's visit could
  # have made it.
  [entry] = cast(list[dict[str, object]], records[-1]["deliverables"])
  assert (entry["state"], entry["path"], entry["reason"]) == (
    "solve",
    None,
    "solve visit 1 raised",
  )


@pytest.mark.parametrize(
  ("error", "outcome"),
  [
    pytest.param(RuntimeError("proxy exited"), "raised", id="raised"),
    pytest.param(KeyboardInterrupt(), "interrupted", id="interrupted"),
  ],
)
def test_each_visit_records_when_it_began_and_ended(
  tmp_path: Path, error: BaseException, outcome: str
) -> None:
  def fail(_: Context) -> Outcome:
    raise error

  records: list[dict[str, object]] = []
  machine = Machine(
    [
      Step("build", lambda _: Outcome("done"), {"done": Goto("solve")}),
      Step("solve", fail, {"done": End("passed")}),
    ],
    on_step=lambda attempt: records.append(attempt.to_json()),
  )
  before = datetime.now(UTC)
  with pytest.raises(type(error)):
    _ = machine.run(tmp_path)
  after = datetime.now(UTC)

  [running] = cast(list[dict[str, object]], records[0]["visits"])
  assert running["started_at"] is not None
  assert running["ended_at"] is None
  attempt = Attempt.from_json(tmp_path, records[-1])
  build, solve = attempt.visits
  # The visit that did not finish ran no action, and still has its end.
  assert (solve.outcome, solve.actions) == (outcome, [])
  given = [build.started_at, build.ended_at, solve.started_at, solve.ended_at]
  times = [time for time in given if time is not None]
  assert len(times) == len(given)
  assert [before, *times, after] == sorted([before, *times, after])


def test_a_record_from_before_visit_times_reads_back_with_none(
  tmp_path: Path,
) -> None:
  record = ended(tmp_path)
  for key in ("started_at", "ended_at"):
    record = broken(record, ["visits", 0, key], KeyError)
  visit = Attempt.from_json(tmp_path, record).visits[0]
  assert (visit.started_at, visit.ended_at) == (None, None)


def test_a_value_from_json_nested_too_deep_has_a_reason(tmp_path: Path) -> None:
  write = (
    "import os; open(os.environ['DATAFORGE_OUTPUTS'] + '/verdict.json', 'w')"
    ".write('[' * 200000)"
  )

  def solve(ctx: Context) -> Outcome:
    spec = ScriptSpec([sys.executable, "-c", write], outputs=["verdict.json"])
    assert Actions().script(ctx, spec).ok
    return Outcome("done")

  seen: list[object] = []
  attempt = Machine(
    [Step("solve", solve, {"done": End("passed")})],
    on_step=lambda attempt: seen.append(attempt.to_json()["deliverables"]),
    deliverables={
      "pass_at_k": Deliverable(
        "trials", "pass@k", Source("solve"), value=Value(["x"], "verdict.json")
      )
    },
  ).run(tmp_path / "attempt")
  entry = cast(list[dict[str, dict[str, object]]], seen[-1])[0]
  assert entry["value"]["reason"] == "verdict.json: nested too deep"
  assert attempt.status == "passed"


def test_a_value_from_a_symlinked_output_is_never_read(tmp_path: Path) -> None:
  secret = tmp_path / "host-only.json"
  _ = secret.write_text('{"token": "host-only"}')
  link = ["sh", "-c", f'ln -s {secret} "$DATAFORGE_OUTPUTS/verdict.json"']

  def solve(ctx: Context) -> Outcome:
    assert Actions().script(ctx, ScriptSpec(link, outputs=["verdict.json"])).ok
    return Outcome("done")

  attempt = Machine(
    [Step("solve", solve, {"done": End("passed")})],
    deliverables={
      "pass_at_k": Deliverable(
        "trials",
        "pass@k",
        Source("solve"),
        value=Value(["token"], "verdict.json"),
      )
    },
  ).run(tmp_path / "attempt")
  assert "host-only" not in json.dumps(attempt.to_json())


@pytest.mark.parametrize(
  ("deliverable", "error"),
  [
    (
      Deliverable("files", "t", Source("nowhere", "x")),
      "unknown state nowhere",
    ),
    (Deliverable("pictures", "t", Source("a", "x")), "unknown kind 'pictures'"),  # pyright: ignore[reportArgumentType]
    (
      Deliverable("files", "t", Source("a")),
      "a files deliverable takes an output",
    ),
    (
      Deliverable("trials", "t", Source("a", "x")),
      "a trials deliverable takes no output",
    ),
    (Deliverable("files", "t", Source("a", "x", 0)), "unknown visit 0"),
    (
      Deliverable("files", "t", Source("a", "x"), table=Source("a", "y")),
      "only a report has a table",
    ),
    (
      Deliverable("report", "t", Source("a", "x"), value=Value(["v"])),
      "only trials have a value",
    ),
    (Deliverable("files", "t", Source((), "x")), "no state"),
    (
      Deliverable("files", "t", Source(("a", "nowhere"), "x")),
      "unknown state nowhere",
    ),
    (
      Deliverable("trials", "t", Source(("a",))),
      "a source of several states takes an output and visit kept or last",
    ),
    (
      Deliverable("files", "t", Source(("a",), "x", 1)),
      "a source of several states takes an output and visit kept or last",
    ),
    (
      Deliverable("files", "t", Source("a", "x"), verdicts=Value(["v"])),
      "only trials have verdicts",
    ),
  ],
)
def test_a_malformed_deliverable_is_an_error(
  deliverable: Deliverable, error: str
) -> None:
  with pytest.raises(ValueError, match=f"deliverable d: {error}"):
    _ = Machine(
      [Step("a", lambda _: Outcome("ok"), {"ok": End("ok")})],
      deliverables={"d": deliverable},
    )


class Killed(BaseException):
  """What kills the process in the tests of resume."""


class Resumable:
  """A machine whose runs a test kills at given visits and resumes.

  write writes a draft; check reads the latest draft, fails its first visit
  with feedback and closes itself on its third; judge, marked too_easy, goes
  back to write until it cannot reach itself again. A run goes write, check
  (fail), write, check, judge, write, check (last), judge, and ends too_easy.

  Args:
    root: The attempt's directory.
  """

  def __init__(self, root: Path) -> None:
    self.root: Path = root
    self.calls: Counter[tuple[str, int]] = Counter()
    self.kill: set[tuple[str, int]] = set()
    self.restored: list[list[object]] = []

  def write(self, ctx: Context) -> Outcome:
    self._count(ctx)
    path = ctx.outdir / "draft"
    _ = ctx.claim(["draft"])
    _ = path.write_text(f"draft {ctx.visit}")
    now = datetime.now(UTC)
    action = ActionRecord(
      "agent", "ok", {"draft": path}, now, now, 1.0, session={"n": ctx.visit}
    )
    ctx.record(action)
    self._die(ctx)
    report = ctx.report
    seen = None if report is None else report.artifacts.get("feedback")
    return Outcome(
      "done", {"feedback": None if seen is None else seen.read_text()}
    )

  def check(self, ctx: Context) -> Outcome:
    self._count(ctx)
    self._die(ctx)
    read = str(ctx.artifacts.get("draft").relative_to(self.root))
    if ctx.visit == 1:
      return Outcome("fail", {"read": read}, feedback={"n": "1"})
    return Outcome("pass", {"read": read}, last=ctx.visit == 3)

  def judge(self, ctx: Context) -> Outcome:
    self._count(ctx)
    self._die(ctx)
    return Outcome("easy")

  def restore(self, points: list[object]) -> None:
    self.restored.append(points)

  def machine(
    self, on_step: Callable[[Attempt], None] | None = None
  ) -> Machine:
    me = self

    class Writer(Step):
      @override
      def runners(self) -> list[object]:
        return [me]

    return Machine(
      [
        Writer("write", self.write, {"done": Goto("check")}, 5),
        Step(
          "check",
          self.check,
          {"fail": Goto("write"), "pass": Goto("judge")},
          5,
          "error",
          feedback="fix ${n}",
        ),
        Step(
          "judge",
          self.judge,
          {"easy": Goto("write", mark="too_easy", end_on_last_visit=True)},
          3,
        ),
      ],
      on_step=on_step,
    )

  def _count(self, ctx: Context) -> None:
    self.calls[ctx.state, ctx.visit] += 1

  def _die(self, ctx: Context) -> None:
    if (ctx.state, ctx.visit) in self.kill:
      self.kill.discard((ctx.state, ctx.visit))
      raise Killed


def sequence(attempt: Attempt) -> list[tuple[object, ...]]:
  return [
    (v.state, v.visit, v.round, v.outcome, v.mark, dict(v.data))
    for v in attempt.visits
  ]


def killed_and_resumed(
  root: Path, at: tuple[str, int], carried_usd: float = 0.25, step: int = -1
) -> tuple[Resumable, Attempt]:
  """Run a Resumable, kill it at a visit and resume it in a new machine.

  Args:
    root: The attempt's directory.
    at: The visit to kill.
    carried_usd: What the resume carries.
    step: Which record on_step saw to resume from: -1 for the one with the
      killed visit, -2 for the one as it began, -3 for the one before, as a
      process killed outright leaves.

  Returns:
    The Resumable, and the attempt as the resume ended it.
  """
  fake = Resumable(root)
  fake.kill.add(at)
  steps: list[dict[str, object]] = []
  machine = fake.machine(lambda attempt: steps.append(as_json(attempt)))
  with pytest.raises(Killed):
    _ = machine.run(root)
  record = Attempt.from_json(root, steps[step])
  resumed = fake.machine().resume(
    record, carried_usd=carried_usd, discarded_path="resumes/1", reason="killed"
  )
  return fake, resumed


@pytest.mark.parametrize(
  "at",
  [
    pytest.param(("judge", 2), id="after-a-last-outcome"),
    pytest.param(("check", 3), id="before-the-end-on-last-visit"),
    pytest.param(("write", 2), id="after-feedback-going-back"),
  ],
)
def test_a_killed_attempt_resumes_where_it_stopped(
  tmp_path: Path, at: tuple[str, int]
) -> None:
  reference = (
    Resumable(tmp_path / "reference").machine().run(tmp_path / "reference")
  )
  assert (reference.status, reference.kept_visit) == ("too_easy", 7)

  fake, resumed = killed_and_resumed(tmp_path / "attempt", at)

  assert sequence(resumed) == sequence(reference)
  assert (resumed.status, resumed.reason, resumed.kept_visit) == (
    reference.status,
    reference.reason,
    reference.kept_visit,
  )
  # Each visit ran once, but the one killed, which ran again under its number.
  visits = [(v.state, v.visit) for v in resumed.visits]
  assert fake.calls == {v: 2 if v == at else 1 for v in visits}
  assert resumed.cost_usd == pytest.approx(reference.cost_usd + 0.25)
  [resumption] = resumed.resumptions
  assert resumption.kept == visits.index(at)
  assert [
    (d["state"], d["visit"], d["outcome"]) for d in resumption.discarded
  ] == [(*at, "raised")]
  assert resumption.discarded[0]["path"] == "resumes/1"
  assert (resumption.carried_usd, resumption.reason) == (0.25, "killed")
  feedback = [a.kind for a in resumed.visits[1].actions]
  assert feedback == ["report"]


def test_an_interrupted_visit_is_left_out_and_run_again(tmp_path: Path) -> None:
  root = tmp_path / "attempt"
  fake = Resumable(root)
  fake.kill.add(("judge", 2))
  steps: list[dict[str, object]] = []
  with pytest.raises(Killed):
    _ = fake.machine(lambda attempt: steps.append(as_json(attempt))).run(root)
  visits = cast(list[dict[str, object]], steps[-1]["visits"])
  visits[-1]["outcome"] = "interrupted"

  resumed = fake.machine().resume(Attempt.from_json(root, steps[-1]))

  assert resumed.status == "too_easy"
  [resumption] = resumed.resumptions
  assert [(d["state"], d["outcome"]) for d in resumption.discarded] == [
    ("judge", "interrupted")
  ]


@pytest.mark.parametrize(
  ("step", "discarded"),
  [
    pytest.param(-2, [("judge", 2, None)], id="as-the-visit-began"),
    pytest.param(-3, [], id="after-the-visit-before"),
  ],
)
def test_an_attempt_killed_outright_resumes_after_its_last_whole_visit(
  tmp_path: Path, step: int, discarded: list[tuple[object, ...]]
) -> None:
  reference = (
    Resumable(tmp_path / "reference").machine().run(tmp_path / "reference")
  )
  fake, resumed = killed_and_resumed(
    tmp_path / "attempt", ("judge", 2), step=step
  )
  assert sequence(resumed) == sequence(reference)
  visits = [(v.state, v.visit) for v in resumed.visits]
  assert fake.calls == {v: 2 if v == ("judge", 2) else 1 for v in visits}
  [resumption] = resumed.resumptions
  assert resumption.kept == 7
  assert [
    (d["state"], d["visit"], d["outcome"]) for d in resumption.discarded
  ] == discarded


def test_resume_hands_each_runner_the_sessions_of_its_states(
  tmp_path: Path,
) -> None:
  fake, _ = killed_and_resumed(tmp_path, ("write", 3))
  # write 3 recorded its session before it was killed.
  assert fake.restored == [[{"n": 1}, {"n": 2}, {"n": 3}]]


def test_a_resumed_record_reads_back_with_its_resumptions(
  tmp_path: Path,
) -> None:
  _, resumed = killed_and_resumed(tmp_path, ("check", 3))
  record = as_json(resumed)
  again = Attempt.from_json(tmp_path, record)
  assert again.to_json() == record
  assert again.cost_usd == pytest.approx(resumed.cost_usd)
  resumptions = cast(list[dict[str, object]], record["resumptions"])
  assert resumptions[0]["kept"] == 6
  assert datetime.fromisoformat(cast(str, resumptions[0]["at"])).tzinfo == UTC


def test_a_resume_with_no_whole_visit_starts_over(tmp_path: Path) -> None:
  reference = (
    Resumable(tmp_path / "reference").machine().run(tmp_path / "reference")
  )
  fake, resumed = killed_and_resumed(tmp_path / "attempt", ("write", 1), 0.0)
  assert fake.calls["write", 1] == 2
  assert sequence(resumed) == sequence(reference)
  assert resumed.resumptions[0].kept == 0


def as_json(attempt: Attempt) -> dict[str, object]:
  return cast(dict[str, object], json.loads(json.dumps(attempt.to_json())))


def ended(tmp_path: Path) -> dict[str, object]:
  return as_json(Resumable(tmp_path).machine().run(tmp_path))


def visits_of(record: dict[str, object]) -> list[dict[str, object]]:
  return cast(list[dict[str, object]], record["visits"])


def cut(record: dict[str, object], keep: int) -> dict[str, object]:
  unended = {"status": None, "reason": None, "kept_visit": None}
  return record | unended | {"visits": visits_of(record)[:keep]}


def other_graph(record: dict[str, object]) -> dict[str, object]:
  graph = cast(dict[str, object], record["graph"])
  return cut(record, 3) | {"graph": graph | {"start": "check"}}


def renumbered(record: dict[str, object]) -> dict[str, object]:
  visits = visits_of(record)
  return cut(record, 3) | {"visits": [*visits[:2], visits[2] | {"visit": 3}]}


def unknown(record: dict[str, object]) -> dict[str, object]:
  return cut(record, 1) | {"visits": [visits_of(record)[0] | {"state": "x"}]}


def unchanged(record: dict[str, object]) -> dict[str, object]:
  return record


def after_the_last_visit(record: dict[str, object]) -> dict[str, object]:
  return cut(record, 8)


@pytest.mark.parametrize(
  ("change", "error"),
  [
    (unchanged, "the attempt has ended: too_easy"),
    (other_graph, "graph is not this machine's"),
    (after_the_last_visit, "judge visit 2 was its last visit"),
    (renumbered, "write visit 3 is its visit 2"),
    (unknown, "x visit 1: unknown state"),
  ],
)
def test_resume_refuses_a_record_it_cannot_go_on_with(
  tmp_path: Path,
  change: Callable[[dict[str, object]], dict[str, object]],
  error: str,
) -> None:
  record = Attempt.from_json(tmp_path, change(ended(tmp_path)))
  with pytest.raises(ResumeError, match=error):
    _ = Resumable(tmp_path).machine().resume(record)


def test_resume_refuses_a_visit_that_ended_the_attempt(tmp_path: Path) -> None:
  machine = Machine([Step("a", lambda _: Outcome("ok"), {"ok": End("done")})])
  record = machine.run(tmp_path)
  record.status = None
  with pytest.raises(
    ResumeError, match="a visit 1 ended the attempt with done"
  ):
    _ = machine.resume(record)


def test_a_record_reads_back_whole(tmp_path: Path, validator: Path) -> None:
  script = Script(tasks=["valid"], verdicts=[True], solved=[1])
  record = as_json(run(tmp_path, validator, script))
  assert Attempt.from_json(tmp_path / "attempt", record).to_json() == record


def test_a_record_from_before_logs_reads_back_with_none(tmp_path: Path) -> None:
  record = broken(
    ended(tmp_path), ["visits", 0, "actions", 0, "logs"], KeyError
  )
  attempt = Attempt.from_json(tmp_path, record)
  assert attempt.visits[0].actions[0].logs == []


def broken(
  record: dict[str, object], path: list[str | int], value: object
) -> dict[str, object]:
  record = cast(dict[str, object], json.loads(json.dumps(record)))
  where: object = record
  for key in path[:-1]:
    where = cast(dict[str | int, object], where)[key]
  last = path[-1]
  if value is KeyError:
    del cast(dict[str | int, object], where)[last]
  else:
    cast(dict[str | int, object], where)[last] = value
  return record


@pytest.mark.parametrize(
  ("path", "value", "error"),
  [
    (["visits"], KeyError, "the record: no visits"),
    (["visits", 0, "last"], KeyError, r"visits\[0\]: no last"),
    (["visits", 0, "visit"], True, r"visits\[0\]: visit is not an integer"),
    (["budget", "max_usd"], "1", "budget: max_usd is not a number"),
    (
      ["visits", 0, "actions", 0, "started_at"],
      "2026-01-02T03:04:05",
      "started_at has no time zone",
    ),
    (
      ["visits", 0, "actions", 0, "outputs", "draft"],
      "../draft",
      "is not inside the attempt",
    ),
    (["visits", 0, "actions", 0, "session"], [], "session: not an object"),
    (["resumptions"], {}, "resumptions is not a list"),
    (["visits", 0, "actions", 0, "logs"], {}, "logs is not a list"),
    (
      ["visits", 0, "actions", 0, "logs"],
      ["../log"],
      r"logs\[0\]: '../log' is not inside the attempt",
    ),
  ],
)
def test_a_malformed_record_is_a_record_error(
  tmp_path: Path, path: list[str | int], value: object, error: str
) -> None:
  record = broken(ended(tmp_path), path, value)
  with pytest.raises(RecordError, match=error):
    _ = Attempt.from_json(tmp_path, record)
