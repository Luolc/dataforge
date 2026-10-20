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
    'system = "S"\n[[criteria]]\nid = "a"\nseverity = "error"\nquestion = "Q"\n'
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
