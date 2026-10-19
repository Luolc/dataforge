# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import override

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
  End,
  Env,
  Goto,
  Judge,
  Limits,
  Machine,
  Outcome,
  PassAtKState,
  ScriptSpec,
  ScriptState,
  TrialAgent,
  TrialResult,
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

  def memory_gb(self, limits: Limits) -> float:
    return limits.memory_gb

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    self.agents.append(agent)
    self.task_dirs.append(task_dir)
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


def test_pass_at_k_judges_only_the_trials_that_ran(tmp_path: Path) -> None:
  results = [result(1.0), result(0.0, "agent crashed"), result(1.0)]
  assert pass_at_k(tmp_path, results) == (
    "all_solved",
    {"solved": 2, "errors": 1, "k": 3},
  )


def test_pass_at_k_with_every_trial_errored_is_an_error(tmp_path: Path) -> None:
  results = [result(0.0, "docker daemon down")] * 3
  assert pass_at_k(tmp_path, results) == (
    "error",
    {"solved": 0, "errors": 3, "k": 3},
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
  }


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
  }
  assert seen[0].feedback == {"details": "every trial solved it"}
  kinds = [(a.kind, a.status) for a in attempt.visits[0].actions]
  assert kinds[-1] == ("judge", "ok")
  verdict = attempt.visits[0].outputs["verdict.json"]
  assert json.loads(verdict.read_text())["outcome"] == "too_easy"
