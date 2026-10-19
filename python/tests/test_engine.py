# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
from pathlib import Path
from typing import cast, override

import pytest

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
  End,
  Env,
  Goto,
  Guard,
  Limits,
  Machine,
  Outcome,
  PassAtKState,
  ScriptSpec,
  State,
  TrialAgent,
  TrialResult,
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
  assert attempt.final["pass_at_k"].data == {"solved": 1, "errors": 0, "k": K}


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
  seen: list[tuple[int, str | None]] = []
  attempt = run(
    tmp_path,
    validator,
    script,
    on_step=lambda a: seen.append((len(a.visits), a.status)),
  )
  assert seen == [(1, None), (2, None), (3, "passed")]
  assert attempt.to_json()["status"] == "passed"


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
  assert seen == ["raised"]


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
  assert [(visit.state, visit.outcome) for visit in attempts[1].visits] == [
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
  assert record["budget"] == {"max_usd": None, "cost_usd": 0, "stopped": None}
