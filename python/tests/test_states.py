# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Sequence
import dataclasses
import json
from pathlib import Path
import re
import sys
from typing import cast, ClassVar, override

import pytest

from dataforge.curation import (
  ActionResult,
  Actions,
  AgentRun,
  AgentSpec,
  AgentState,
  AgentTask,
  Artifacts,
  Attempt,
  CommandResult,
  Context,
  Deliverable,
  End,
  Env,
  Goto,
  Judge,
  JudgeState,
  Limits,
  Machine,
  Outcome,
  PassAtKState,
  Report,
  Review,
  RubricState,
  ScriptSpec,
  ScriptState,
  Source,
  State,
  TrialAgent,
  TrialResult,
  TrialsState,
  TrialState,
  Visit,
)


class Trials:
  """Returns the given results, one per trial, in the order of trial names.

  Args:
    results: The results, by trial name.
  """

  def __init__(self, results: dict[str, TrialResult]) -> None:
    self.results: dict[str, TrialResult] = results
    self.agents: list[TrialAgent] = []
    self.task_dirs: list[Path] = []
    self.calls: dict[str, tuple[Path, TrialAgent]] = {}

  def memory_gb(self, limits: Limits) -> float:
    return limits.memory_gb

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    self.agents.append(agent)
    self.task_dirs.append(task_dir)
    self.calls[outdir.name] = (task_dir, agent)
    return self.results[outdir.name]


def result(reward: float, exception: str | None = None) -> TrialResult:
  return TrialResult({"reward": reward}, exception, 0.0, 1.0, {})


ENDS = {
  name: End(name)
  for name in [
    "solved",
    "unsolved",
    "error",
    "all_solved",
    "none_solved",
    "in_range",
  ]
}


def outcome_of(tmp_path: Path, state: TrialState) -> tuple[str | None, object]:
  attempt = Machine([state]).run(tmp_path)
  return attempt.status, attempt.visits[0].data


def test_trial_state_outcomes(tmp_path: Path) -> None:
  for reward, exception, expected in [
    (1.0, None, "solved"),
    (0.0, None, "unsolved"),
    (0.0, "verifier timed out", "error"),
  ]:
    trials = Trials({"trial": result(reward, exception)})
    state = TrialState(
      "t",
      ENDS,
      actions=Actions(trial_runner=trials),
      task_dir=tmp_path,
      agent="oracle",
      limits=Limits(1.0),
    )
    status, _ = outcome_of(tmp_path / expected, state)
    assert status == expected
    assert trials.agents == ["oracle"]


def pass_at_k(
  tmp_path: Path, results: list[TrialResult]
) -> tuple[str | None, object]:
  trials = Trials({f"trial-{i}": r for i, r in enumerate(results)})
  state = PassAtKState(
    "p",
    ENDS,
    actions=Actions(trial_runner=trials),
    task_dir=tmp_path,
    agent="solver",
    limits=Limits(1.0),
    k=len(results),
  )
  return outcome_of(tmp_path / "a", state)


@pytest.mark.parametrize(
  ("exception", "fault"),
  [
    ("UnknownApiError: API Error: overloaded", "model"),
    ("HarborNotStarted: Harbor did not start the trial", "infra"),
    ("TimeoutError: Harbor ran past 60 s", "timeout"),
    ("VerifierTimeoutError: timed out after 900.0 seconds", "unclear"),
  ],
)
def test_pass_at_k_with_a_trial_that_cannot_be_judged_is_an_error(
  tmp_path: Path, exception: str, fault: str
) -> None:
  # The sample does not shrink to the trials that ran.
  results = [result(0.0), *[result(0.0, exception)] * 3]
  assert pass_at_k(tmp_path, results) == (
    "error",
    {"solved": 0, "errors": 3, "faults": {fault: 3}, "k": 4},
  )


def test_pass_at_k_judges_a_trial_its_agent_ended_itself(
  tmp_path: Path,
) -> None:
  results = [result(0.0, "AgentTimeoutError: ran past 3600 s"), result(1.0)]
  assert pass_at_k(tmp_path, results) == (
    "in_range",
    {"solved": 1, "errors": 0, "faults": {}, "k": 2},
  )


def test_a_subclass_decides_what_solved_means(tmp_path: Path) -> None:
  class HalfSolves(PassAtKState):
    @override
    def solved(self, result: TrialResult) -> bool:
      return result.rewards.get("reward", 0.0) >= 0.5

  trials = Trials({f"trial-{i}": result(0.5) for i in range(2)})
  state = HalfSolves(
    "p",
    ENDS,
    actions=Actions(trial_runner=trials),
    task_dir=tmp_path,
    agent="solver",
    limits=Limits(1.0),
    k=2,
  )
  assert outcome_of(tmp_path / "a", state)[0] == "all_solved"


def test_script_state_outcome_is_the_action_status(tmp_path: Path) -> None:
  class Check(ScriptState):
    def __init__(self, argv: list[str]) -> None:
      super().__init__(
        "c", {"ok": End("ok"), "failed": End("failed")}, actions=Actions()
      )
      self.argv: list[str] = argv

    @override
    def spec(self, ctx: Context) -> ScriptSpec:
      return ScriptSpec(self.argv)

  assert Machine([Check(["true"])]).run(tmp_path / "a").status == "ok"
  assert Machine([Check(["false"])]).run(tmp_path / "b").status == "failed"


class Agent:
  """An agent runner that records its prompts and ends each run as told.

  Args:
    runs: How each start ends: its status, and the text it writes to the
      output "out".
  """

  def __init__(self, runs: list[tuple[str, str]]) -> None:
    self.runs: list[tuple[str, str]] = runs
    self.prompts: list[str] = []

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    self.prompts.append(task.prompt)
    status, text = self.runs.pop(0)
    _ = (Path(task.outdir) / "out").write_text(text)
    return AgentRun(task.outdir, status, 0.5, None, "done", "success", True)

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("no fixes here")


def checker(tmp_path: Path) -> Path:
  path = tmp_path / "check.sh"
  _ = path.write_text('#!/bin/sh\ngrep -q good "$1/out" || echo "not $2"\n')
  _ = path.write_text(path.read_text() + 'grep -q good "$1/out"\n')
  path.chmod(0o755)
  return path


def test_agent_state_renders_its_prompts_and_gives_feedback(
  tmp_path: Path,
) -> None:
  hint = tmp_path / "good"
  _ = hint.write_text("good")
  agent = Agent([("ok", "bad"), ("ok", "good")])
  spec = AgentSpec(
    "Write ${state} ${visit} ${round} for $HOME and ${other}.",
    tmp_path,
    inputs=[hint],
    outputs=["out"],
    validator=checker(tmp_path),
    max_fixes=0,
  )
  state = AgentState(
    "author",
    {"done": End("passed"), "failed": Goto("author")},
    2,
    actions=Actions(agent_runner=agent),
    spec=spec,
    report_prompt="Fix, round ${round}: ${feedback}",
    rename={"ok": "done"},
    feedback="${details}",
  )
  attempt = Machine([state]).run(tmp_path / "a")
  assert attempt.status == "passed"
  assert agent.prompts[0].startswith(
    "Write author 1 1 for $HOME and ${other}.\n"
  )
  assert agent.prompts[1].startswith(f"Fix, round 2: not {hint}\n")
  assert [visit.outcome for visit in attempt.visits] == ["failed", "done"]
  assert attempt.visits[1].data == {
    "ending": "success",
    "fixes": 0,
    "cost_usd": 0.5,
    "fault": None,
    "stop_reason": None,
    "error": None,
  }


class Refused:
  """A runner whose one run ends on a refusal it did not report a cost for."""

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    return AgentRun(
      None,
      "failed",
      None,
      None,
      "API Error: flagged",
      "success",
      False,
      fault="model",
      stop_reason="refusal",
      error="API Error: flagged",
    )

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("no fixes here")


def test_agent_state_gives_an_error_with_how_its_run_ended(
  tmp_path: Path,
) -> None:
  spec = AgentSpec("Write.", tmp_path, validator=checker(tmp_path))
  state = AgentState(
    "author",
    {"ok": End("passed"), "failed": End("failed"), "error": End("errored")},
    actions=Actions(agent_runner=Refused()),
    spec=spec,
  )
  attempt = Machine([state]).run(tmp_path / "a")
  assert attempt.status == "errored"
  assert attempt.reason == (
    "author visit 1: the agent's run ended success, model: API Error: flagged"
  )
  assert attempt.visits[0].data == {
    "ending": "success",
    "fixes": 0,
    "cost_usd": None,
    "fault": "model",
    "stop_reason": "refusal",
    "error": "API Error: flagged",
  }


class Capped:
  """A runner whose one run ends on its own spend cap."""

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    return AgentRun(
      None,
      "failed",
      1.5,
      None,
      "error_max_budget_usd",
      "error_max_budget_usd",
      False,
      fault="budget",
      error="Reached maximum budget ($1.5)",
    )

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("no fixes here")


def test_agent_state_whose_run_hit_its_own_cap_ends_the_attempt_with_budget(
  tmp_path: Path,
) -> None:
  spec = AgentSpec("Write.", tmp_path, validator=checker(tmp_path))
  state = AgentState(
    "author",
    {"ok": End("passed"), "failed": End("failed"), "error": End("errored")},
    actions=Actions(agent_runner=Capped()),
    spec=spec,
  )
  attempt = Machine([state]).run(tmp_path / "a")
  assert (attempt.status, attempt.visits[0].outcome) == ("budget", "error")
  assert attempt.reason == (
    "author visit 1: the agent's run ended error_max_budget_usd, budget:"
    " Reached maximum budget ($1.5)"
  )


def agent_outcome(
  tmp_path: Path,
  result: ActionResult,
  last_if_unresumable: bool = False,
) -> Outcome:
  state = AgentState(
    "author",
    {},
    actions=Actions(),
    last_if_unresumable=last_if_unresumable,
  )
  ctx = Context(Attempt(tmp_path), Artifacts(), Visit("author", 1), None)
  return state.outcome(ctx, result)


def run_of(status: str = "ok", resumable: bool = True) -> AgentRun:
  return AgentRun(None, status, 0.25, None, "", "the end", resumable)


def test_agent_state_tells_ok_failed_and_error_apart(tmp_path: Path) -> None:
  failing = CommandResult(1, "the validator says no")
  passing = CommandResult(0, "")
  cases = [
    (ActionResult("ok", {}, "", run=run_of(), validation=passing), "ok", None),
    (
      ActionResult("failed", {}, "log", run=run_of(), validation=failing),
      "failed",
      {"details": "the validator says no"},
    ),
    (
      ActionResult("failed", {}, "missing outputs: out", run=run_of()),
      "failed",
      {"details": "missing outputs: out"},
    ),
    (ActionResult("failed", {}, "", run=run_of("failed")), "error", None),
    (ActionResult("failed", {}, ""), "error", None),
  ]
  for result, name, feedback in cases:
    outcome = agent_outcome(tmp_path, result)
    assert (outcome.name, outcome.feedback, outcome.last) == (
      name,
      feedback,
      False,
    )


def test_last_if_unresumable_closes_the_state(tmp_path: Path) -> None:
  for resumable, flag, last in [
    (False, True, True),
    (True, True, False),
    (False, False, False),
  ]:
    for status in ["ok", "failed"]:
      result = ActionResult(status, {}, "", run=run_of(resumable=resumable))
      assert agent_outcome(tmp_path, result, flag).last is last


class Writer:
  """An agent runner that writes the files it is told on each call.

  Args:
    calls: What each call, a start or a resume in turn, writes into the
      outdir, by file name.
  """

  def __init__(self, calls: list[dict[str, str]]) -> None:
    self.calls: list[dict[str, str]] = calls
    self.prompts: list[tuple[str, str]] = []

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    return self._write("start", task.outdir, task.prompt)

  def resume(self, session: object, prompt: str) -> AgentRun:
    assert isinstance(session, str)
    return self._write("resume", session, prompt)

  def _write(self, kind: str, outdir: str, prompt: str) -> AgentRun:
    self.prompts.append((kind, prompt))
    for name, text in self.calls.pop(0).items():
      _ = (Path(outdir) / name).write_text(text)
    return AgentRun(outdir, "ok", 0.0, None, "done")


def run_reporting(
  tmp_path: Path,
  calls: list[dict[str, str]],
  visits: int,
  validator: Path | None = None,
) -> tuple[Attempt, Writer]:
  """Run an agent that goes back to itself with a report, visits times.

  Args:
    tmp_path: The test's directory.
    calls: What each call of the agent writes.
    visits: How many visits the attempt has.
    validator: The agent's validator, if any.

  Returns:
    The attempt and the runner.
  """
  writer = Writer(calls)
  state = AgentState(
    "author",
    {
      "ok": Goto("author", end_on_last_visit=True),
      "failed": End("failed"),
      "error": End("error"),
    },
    visits,
    on_exhausted="done",
    actions=Actions(agent_runner=writer),
    spec=AgentSpec(
      "Write.", tmp_path, outputs=["out"], validator=validator, max_fixes=1
    ),
    report_outputs=["changes.md"],
    feedback="${details}",
  )
  attempt = Machine([state]).run(tmp_path / "a")
  assert not writer.calls
  return attempt, writer


def kinds(writer: Writer) -> list[str]:
  return [kind for kind, _ in writer.prompts]


def test_report_outputs_are_not_required_without_a_report(
  tmp_path: Path,
) -> None:
  attempt, writer = run_reporting(tmp_path, [{"out": "1"}], 1)
  assert attempt.status == "done"
  assert [visit.outcome for visit in attempt.visits] == ["ok"]
  assert sorted(attempt.visits[0].outputs) == ["out"]
  assert kinds(writer) == ["start"]
  assert "changes.md" not in writer.prompts[0][1]


def test_a_missing_report_output_resumes_the_session(tmp_path: Path) -> None:
  calls = [{"out": "1"}, {"out": "2"}, {"changes.md": "fixed out"}]
  attempt, writer = run_reporting(tmp_path, calls, 2)
  assert attempt.status == "done"
  assert [visit.outcome for visit in attempt.visits] == ["ok", "ok"]
  assert kinds(writer) == ["start", "start", "resume"]
  assert "anew this time" in writer.prompts[1][1]
  assert "changes.md is missing" in writer.prompts[2][1]
  assert attempt.visits[1].data["fixes"] == 1
  outputs = attempt.visits[1].outputs
  assert outputs["changes.md"].read_text() == "fixed out"


def test_an_unchanged_report_output_resumes_the_session(
  tmp_path: Path,
) -> None:
  calls = [
    {"out": "1"},
    {"out": "2", "changes.md": "fixed out"},
    {"out": "3", "changes.md": "fixed out"},
    {"changes.md": "fixed out again"},
  ]
  attempt, writer = run_reporting(tmp_path, calls, 3)
  assert attempt.status == "done"
  assert kinds(writer) == ["start", "start", "start", "resume"]
  assert "changes.md is unchanged from its last version" in writer.prompts[3][1]
  assert attempt.visits[2].data["fixes"] == 1


def test_a_rewritten_report_output_passes_without_a_resume(
  tmp_path: Path,
) -> None:
  calls = [
    {"out": "1"},
    {"out": "2", "changes.md": "fixed out"},
    {"out": "3", "changes.md": "fixed out again"},
  ]
  attempt, writer = run_reporting(tmp_path, calls, 3)
  assert attempt.status == "done"
  assert kinds(writer) == ["start"] * 3
  assert [visit.data["fixes"] for visit in attempt.visits] == [0, 0, 0]


def test_a_report_output_still_not_written_fails_the_visit(
  tmp_path: Path,
) -> None:
  writer = Writer([{"out": "1"}, {"out": "2"}])
  state = AgentState(
    "author",
    {},
    actions=Actions(agent_runner=writer),
    spec=AgentSpec("Write.", tmp_path, outputs=["out"], max_fixes=1),
    report_outputs=["changes.md"],
  )
  report = Report("check", "fail", {}, {})
  ctx = Context(Attempt(tmp_path), Artifacts(), Visit("author", 2), report)
  outcome = state.run(ctx)
  assert kinds(writer) == ["start", "resume"]
  assert outcome.name == "failed"
  assert outcome.feedback is not None
  assert "outputs not written anew after 1 fixes: changes.md is missing" in str(
    outcome.feedback["details"]
  )


def test_report_outputs_and_the_validator_share_max_fixes(
  tmp_path: Path,
) -> None:
  calls = [{"out": "good"}, {"out": "bad"}, {"changes.md": "fixed out"}]
  attempt, writer = run_reporting(tmp_path, calls, 2, checker(tmp_path))
  assert attempt.status == "failed"
  assert kinds(writer) == ["start", "start", "resume"]
  assert "changes.md is missing" in writer.prompts[2][1]
  assert attempt.visits[1].data["fixes"] == 1


def test_script_state_runs_the_spec_it_is_given(tmp_path: Path) -> None:
  for argv, status in [
    (["true"], "passed"),
    (["sh", "-c", "echo broke; exit 3"], "reported"),
  ]:
    state = ScriptState(
      "s",
      {"ok": End("passed"), "fail": Goto("r")},
      actions=Actions(),
      spec=ScriptSpec(argv),
      rename={"failed": "fail"},
      feedback="${details}",
    )
    end = ScriptState(
      "r", {"ok": End("reported")}, actions=Actions(), spec=ScriptSpec(["true"])
    )
    attempt = Machine([state, end]).run(tmp_path / status)
    assert attempt.status == status
  assert attempt.visits[0].outputs["feedback"].read_text() == "broke\n"


WRITE_TASK = ScriptSpec(
  ["sh", "-c", 'mkdir -p "$DATAFORGE_OUTPUTS/task/env"'], outputs=["task"]
)


def test_a_trial_state_takes_an_artifact_or_a_path_in_it(
  tmp_path: Path,
) -> None:
  for task_dir, expected in [("task", "task"), ("task/env", "task/env")]:
    trials = Trials({"trial": result(1.0)})
    trial = TrialState(
      "t",
      {"done": End("passed")},
      actions=Actions(trial_runner=trials),
      task_dir=task_dir,
      agent="oracle",
      limits=Limits(1.0),
      rename={"solved": "done"},
    )
    root = tmp_path / expected.replace("/", "-")
    write = ScriptState(
      "w", {"ok": Goto("t")}, actions=Actions(), spec=WRITE_TASK
    )
    states = [write, trial]
    assert Machine(states).run(root).status == "passed"
    assert trials.task_dirs == [root / "artifacts/w/1/task" / expected[5:]]


JUDGE = """\
import json, os, pathlib, sys
record = json.loads(pathlib.Path(os.environ["DATAFORGE_RECORD"]).read_text())
verdict = {
  "outcome": "too_easy",
  "data": {
    "args": [pathlib.Path(arg).name for arg in sys.argv[1:]],
    "env": [
      os.environ[f"DATAFORGE_{key}"] for key in ("STATE", "VISIT", "ROUND")
    ],
    "root": os.environ["DATAFORGE_ROOT"],
    "visits": len(record["visits"]),
    "actions": [action["kind"] for action in record["visits"][-1]["actions"]],
  },
  "feedback": {"details": "every trial solved it"},
}
out = pathlib.Path(os.environ["DATAFORGE_OUTPUTS"])
(out / "verdict.json").write_text(json.dumps(verdict))
"""


def test_pass_at_k_with_a_judge_takes_its_verdict(tmp_path: Path) -> None:
  script = tmp_path / "judge.py"
  _ = script.write_text(JUDGE)
  trials = Trials({f"trial-{i}": result(1.0) for i in range(2)})
  seen: list[Outcome] = []

  class Seen(PassAtKState):
    @override
    def outcomes(self, ctx: Context, results: Sequence[TrialResult]) -> Outcome:
      seen.append(super().outcomes(ctx, results))
      return seen[-1]

  state = Seen(
    "p",
    {"easy": End("too_easy")},
    actions=Actions(trial_runner=trials),
    task_dir=tmp_path,
    agent="solver",
    limits=Limits(1.0),
    k=2,
    judge=Judge(ScriptSpec([sys.executable, "-I", str(script)])),
    rename={"too_easy": "easy"},
    max_cost_usd=2.0,
  )
  assert state.max_cost_usd == 2.0
  root = tmp_path / "a"
  attempt = Machine([state]).run(root)
  assert attempt.status == "too_easy"
  assert attempt.visits[0].data == {
    "args": ["trial-0", "trial-1"],
    "env": ["p", "1", "1"],
    "root": str(root),
    "visits": 1,
    "actions": ["trial", "trial"],
  }
  assert seen[0].feedback == {"details": "every trial solved it"}
  kinds = [(a.kind, a.status) for a in attempt.visits[0].actions]
  assert kinds[-1] == ("judge", "ok")
  verdict = attempt.visits[0].outputs["verdict.json"]
  assert json.loads(verdict.read_text())["outcome"] == "too_easy"


def test_trials_state_runs_each_agent_its_number_of_times(
  tmp_path: Path,
) -> None:
  names = ["nop-0", "nop-1", "solver-0"]
  for crash, status in [(None, "passed"), ("docker died", "errored")]:
    results = {name: result(0.0) for name in names}
    results["solver-0"] = result(1.0, crash)
    trials = Trials(results)
    state = TrialsState(
      "t",
      {"done": End("passed"), "error": End("errored")},
      actions=Actions(trial_runner=trials),
      task_dir=tmp_path,
      agents={"nop": 2, "solver": 1},
      limits=Limits(1.0),
      configs={"solver": {"name": "claude-code"}},
      rename={"ok": "done"},
    )
    attempt = Machine([state]).run(tmp_path / status)
    assert attempt.status == status
    assert trials.calls == {
      "nop-0": (tmp_path, "nop"),
      "nop-1": (tmp_path, "nop"),
      "solver-0": (tmp_path, {"name": "claude-code"}),
    }
    assert attempt.visits[0].data["solver-0"] == {
      "rewards": {"reward": 1.0},
      "exception": crash,
    }
    assert sorted(attempt.visits[0].outputs) == names


def test_trials_state_runs_on_each_task_of_a_directory(tmp_path: Path) -> None:
  make = ScriptSpec(
    ["sh", "-c", 'cd "$DATAFORGE_OUTPUTS" && mkdir -p tasks/a tasks/b'],
    outputs=["tasks"],
  )
  for runs, names in [
    (1, ["regrade-a", "regrade-b"]),
    (2, ["regrade-a-0", "regrade-a-1", "regrade-b-0", "regrade-b-1"]),
  ]:
    trials = Trials({name: result(1.0) for name in names})
    write = ScriptState("w", {"ok": Goto("t")}, actions=Actions(), spec=make)
    state = TrialsState(
      "t",
      {"ok": End("passed")},
      actions=Actions(trial_runner=trials),
      task_dir="tasks",
      agents={"regrade": runs},
      limits=Limits(1.0),
      configs={"regrade": "oracle"},
      each_task=True,
    )
    root = tmp_path / str(runs)
    assert Machine([write, state]).run(root).status == "passed"
    tasks = root / "artifacts/w/1/tasks"
    assert trials.calls == {
      name: (tasks / name.split("-")[1], "oracle") for name in names
    }


SOLVER = "artifacts/solve/1/trial-0/task__0"


def regrade_tasks(
  source: str, *, deliverables: bool = False, link: bool = False
) -> tuple[Machine, Trials]:
  """Make a machine that writes tasks a and b, then regrades each.

  Args:
    source: What task a's source.json holds; task b has none.
    deliverables: Whether the machine lists the regrade's trials as a
      deliverable.
    link: Whether task a's source.json is a symlink to a file that holds
      source.

  Returns:
    The machine and its trial runner.
  """
  target = "other.json" if link else "source.json"
  command = f"printf '%s' \"$1\" > tasks/a/{target}"
  if link:
    command += " && ln -s other.json tasks/a/source.json"
  make = ScriptSpec(
    [
      "sh",
      "-c",
      f'cd "$DATAFORGE_OUTPUTS" && mkdir -p tasks/a tasks/b && {command}',
      "sh",
      source,
    ],
    outputs=["tasks"],
  )
  trials = Trials({"regrade-a": result(1.0), "regrade-b": result(0.0)})
  write = ScriptState("w", {"ok": Goto("t")}, actions=Actions(), spec=make)
  state = TrialsState(
    "t",
    {"ok": End("passed")},
    actions=Actions(trial_runner=trials),
    task_dir="tasks",
    agents={"regrade": 1},
    limits=Limits(1.0),
    configs={"regrade": "oracle"},
    each_task=True,
  )
  regrade = {"regrade": Deliverable("trials", "Regrade", Source("t"))}
  machine = Machine(
    [write, state], deliverables=regrade if deliverables else None
  )
  return machine, trials


def test_trials_state_records_the_source_a_task_names(tmp_path: Path) -> None:
  machine, _ = regrade_tasks(json.dumps({"trial": SOLVER}), deliverables=True)
  root = tmp_path / "attempt"
  attempt = machine.run(root)
  assert attempt.status == "passed"
  sources = {
    next(iter(action.outputs)): action.trial and action.trial.source
    for action in attempt.visits[1].actions
  }
  assert sources == {"regrade-a": root / SOLVER, "regrade-b": None}
  record = attempt.to_json()
  [entry] = cast(
    list[dict[str, list[dict[str, object]]]], record["deliverables"]
  )
  assert [(t["output"], t["source"]) for t in entry["trials"]] == [
    ("regrade-a", SOLVER),
    ("regrade-b", None),
  ]
  assert Attempt.from_json(root, record).to_json() == record


@pytest.mark.parametrize(
  ("source", "error"),
  [
    (json.dumps({"trial": "/etc"}), "is not inside the attempt"),
    (json.dumps({"trial": "../elsewhere"}), "is not inside the attempt"),
    (json.dumps({"trial": ""}), "is not inside the attempt"),
    (json.dumps({"trial": 1}), "trial is not a string"),
    (json.dumps({"trial": SOLVER, "by": "x"}), 'not {"trial": <path>}'),
    (json.dumps([SOLVER]), 'not {"trial": <path>}'),
    ("{", "Expecting property name"),
  ],
)
def test_trials_state_runs_no_trial_when_a_source_is_wrong(
  tmp_path: Path, source: str, error: str
) -> None:
  machine, trials = regrade_tasks(source)
  with pytest.raises(ValueError, match=re.escape(error)):
    _ = machine.run(tmp_path / "attempt")
  assert trials.calls == {}


def test_trials_state_reads_no_source_through_a_symlink(tmp_path: Path) -> None:
  machine, trials = regrade_tasks(json.dumps({"trial": SOLVER}), link=True)
  with pytest.raises(ValueError, match=r"source\.json: .*symbolic links"):
    _ = machine.run(tmp_path / "attempt")
  assert trials.calls == {}


def test_trials_state_with_a_judge_takes_its_verdict(tmp_path: Path) -> None:
  script = tmp_path / "judge.py"
  _ = script.write_text(JUDGE)
  names = ["nop-0", "solver-0", "solver-1"]
  trials = Trials({name: result(0.0, "docker died") for name in names})
  state = TrialsState(
    "t",
    {"easy": End("too_easy")},
    actions=Actions(trial_runner=trials),
    task_dir=tmp_path,
    agents={"nop": 1, "solver": 2},
    limits=Limits(1.0),
    judge=Judge(ScriptSpec([sys.executable, "-I", str(script)])),
    rename={"too_easy": "easy"},
  )
  attempt = Machine([state]).run(tmp_path / "a")
  assert attempt.status == "too_easy"
  assert attempt.visits[0].data["args"] == names
  assert attempt.visits[0].data["actions"] == ["trial"] * 3
  kinds = [(a.kind, a.status) for a in attempt.visits[0].actions]
  assert kinds[-1] == ("judge", "ok")


LOG_JUDGE = """\
import json, os, pathlib, sys
log = pathlib.Path(sys.argv[1]).read_text().strip()
feedback = {"details": log}
verdict = {"outcome": "fail", "data": {"log": log}, "feedback": feedback}
out = pathlib.Path(os.environ["DATAFORGE_OUTPUTS"])
(out / "verdict.json").write_text(json.dumps(verdict))
"""


def test_a_judge_state_takes_its_verdict_on_earlier_outputs(
  tmp_path: Path,
) -> None:
  make = ScriptSpec(
    ["sh", "-c", 'echo broken > "$DATAFORGE_OUTPUTS/build.log"'],
    outputs=["build.log"],
  )
  judge = tmp_path / "judge.py"
  _ = judge.write_text(LOG_JUDGE)
  states = [
    ScriptState("build", {"ok": Goto("judge")}, actions=Actions(), spec=make),
    JudgeState(
      "judge",
      {"rejected": Goto("end")},
      actions=Actions(),
      judge=Judge(
        ScriptSpec([sys.executable, "-I", str(judge)], inputs=["build.log"])
      ),
      rename={"fail": "rejected"},
      feedback="The build: ${details}",
    ),
    ScriptState(
      "end",
      {"ok": End("sent back")},
      actions=Actions(),
      spec=ScriptSpec(["true"]),
    ),
  ]
  attempt = Machine(states).run(tmp_path / "a")
  assert attempt.status == "sent back"
  assert attempt.visits[1].data == {"log": "broken"}
  feedback = attempt.visits[1].outputs["feedback"]
  assert feedback.read_text() == "The build: broken"


TIED_JUDGE = """\
import json, os, pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
verdict = {
  "outcome": "tied" if "same" in text else "done",
  "data": {"args": [pathlib.Path(arg).name for arg in sys.argv[1:]]},
  "feedback": {"details": text},
}
out = pathlib.Path(os.environ["DATAFORGE_OUTPUTS"])
(out / "verdict.json").write_text(json.dumps(verdict))
"""


class Asks(AgentState):
  """An agent state that gives "asked" for a good run that asks for more.

  Attributes:
    OUTCOMES: The outcomes it gives, before rename and without a judge.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset(
    {"ok", "failed", "error", "asked"}
  )

  @override
  def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
    found = super().outcome(ctx, result)
    if found.name == "ok" and "ask" in (ctx.outdir / "out").read_text():
      return dataclasses.replace(found, name="asked")
    return found


def judged_agent(
  tmp_path: Path, cls: type[AgentState], runs: list[tuple[str, str]]
) -> Attempt:
  """Run an agent state with a judge, back to itself until it is tied.

  Args:
    tmp_path: The test's directory.
    cls: The state's class.
    runs: How each run of the agent ends, as Agent takes them.

  Returns:
    The attempt.
  """
  script = tmp_path / "judge.py"
  _ = script.write_text(TIED_JUDGE)
  again = Goto("tighten", report=False)
  state = cls(
    "tighten",
    {
      "failed": again,
      "asked": again,
      "done": Goto("tighten"),
      "tied": End("too_easy"),
      "error": End("error"),
    },
    len(runs),
    actions=Actions(agent_runner=Agent(runs)),
    spec=AgentSpec(
      "Tighten.",
      tmp_path,
      outputs=["out"],
      validator=checker(tmp_path),
      max_fixes=0,
    ),
    judge=Judge(
      ScriptSpec([sys.executable, "-I", str(script)]), ["done", "tied"]
    ),
    feedback="The judge read: ${details}",
  )
  return Machine([state]).run(tmp_path / "a")


def test_an_agent_state_with_a_judge_takes_its_verdict_after_ok(
  tmp_path: Path,
) -> None:
  runs = [("ok", "bad"), ("ok", "good"), ("ok", "good, same")]
  attempt = judged_agent(tmp_path, AgentState, runs)
  assert attempt.status == "too_easy"
  assert [visit.outcome for visit in attempt.visits] == [
    "failed",
    "done",
    "tied",
  ]
  assert [[a.kind for a in visit.actions] for visit in attempt.visits] == [
    ["agent"],
    ["agent", "judge", "report"],
    ["agent", "judge"],
  ]
  assert attempt.visits[2].data == {
    "ending": "success",
    "fixes": 0,
    "cost_usd": 0.5,
    "fault": None,
    "stop_reason": None,
    "error": None,
    "args": ["out"],
  }
  feedback = attempt.visits[1].outputs["feedback"]
  assert feedback.read_text() == "The judge read: good"
  state = AgentState(
    "s",
    {},
    actions=Actions(),
    judge=Judge(ScriptSpec(["judge"]), ["done", "tied"]),
    rename={"tied": "same"},
  )
  assert state.possible_outcomes() == {"failed", "error", "done", "same"}
  any_ = AgentState("s", {}, actions=Actions(), judge=Judge(ScriptSpec(["j"])))
  assert any_.possible_outcomes() is None


def test_a_subclass_of_agent_state_takes_a_judge_after_its_own_ok(
  tmp_path: Path,
) -> None:
  runs = [("ok", "good, ask"), ("ok", "good, same")]
  attempt = judged_agent(tmp_path, Asks, runs)
  assert attempt.status == "too_easy"
  assert [visit.outcome for visit in attempt.visits] == ["asked", "tied"]
  assert [[a.kind for a in visit.actions] for visit in attempt.visits] == [
    ["agent"],
    ["agent", "judge"],
  ]
  state = Asks(
    "s", {}, actions=Actions(), judge=Judge(ScriptSpec(["j"]), ["done"])
  )
  assert state.possible_outcomes() == {"failed", "error", "asked", "done"}


def test_each_state_names_the_outcomes_it_gives() -> None:
  assert {
    cls.__name__: cls.OUTCOMES
    for cls in [
      AgentState,
      ScriptState,
      TrialState,
      PassAtKState,
      TrialsState,
      JudgeState,
      RubricState,
    ]
  } == {
    "AgentState": {"ok", "failed", "error"},
    "ScriptState": {"ok", "failed"},
    "TrialState": {"solved", "unsolved", "error"},
    "PassAtKState": {"all_solved", "none_solved", "in_range", "error"},
    "TrialsState": {"ok", "error"},
    "JudgeState": None,
    "RubricState": {"pass", "fail", "error"},
  }


def test_each_state_names_the_outcomes_it_may_give_after_rename(
  tmp_path: Path,
) -> None:
  actions = Actions()
  spec = ScriptSpec(["judge"])
  limits = Limits(1)
  rubric = tmp_path / "rubric.toml"
  _ = rubric.write_text(
    'system = "S"\n[[criteria]]\nid = "a"\nquestion = "Q"\n'
  )
  states = {
    "agent": AgentState("s", {}, actions=actions, rename={"failed": "invalid"}),
    "script": ScriptState("s", {}, actions=actions),
    "trial": TrialState(
      "s", {}, actions=actions, task_dir="t", agent="nop", limits=limits
    ),
    "pass_at_k": PassAtKState(
      "s", {}, actions=actions, task_dir="t", agent="nop", limits=limits, k=2
    ),
    "pass_at_k_judged": PassAtKState(
      "s",
      {},
      actions=actions,
      task_dir="t",
      agent="nop",
      limits=limits,
      k=2,
      judge=Judge(spec, ["fine", "bad"]),
      rename={"bad": "worse"},
    ),
    "pass_at_k_judged_any": PassAtKState(
      "s",
      {},
      actions=actions,
      task_dir="t",
      agent="nop",
      limits=limits,
      k=2,
      judge=Judge(spec),
    ),
    "trials": TrialsState(
      "s", {}, actions=actions, task_dir="t", agents={"nop": 1}, limits=limits
    ),
    "trials_judged": TrialsState(
      "s",
      {},
      actions=actions,
      task_dir="t",
      agents={"nop": 1},
      limits=limits,
      judge=Judge(spec, ["pass", "fail"]),
      rename={"fail": "worse"},
    ),
    "judge": JudgeState(
      "s", {}, actions=actions, judge=Judge(spec, ["pass", "fail"])
    ),
    "judge_any": JudgeState("s", {}, actions=actions, judge=Judge(spec)),
    "rubric": RubricState(
      "s",
      {},
      actions=actions,
      reviews=[Review("r", rubric, {})],
      rename={"error": "fail"},
    ),
    "state": State("s", {}),
  }
  assert {name: s.possible_outcomes() for name, s in states.items()} == {
    "agent": {"ok", "invalid", "error"},
    "script": {"ok", "failed"},
    "trial": {"solved", "unsolved", "error"},
    "pass_at_k": {"all_solved", "none_solved", "in_range", "error"},
    "pass_at_k_judged": {"fine", "worse"},
    "pass_at_k_judged_any": None,
    "trials": {"ok", "error"},
    "trials_judged": {"pass", "worse"},
    "judge": {"pass", "fail"},
    "judge_any": None,
    "rubric": {"pass", "fail"},
    "state": None,
  }
