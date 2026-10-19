# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from pathlib import Path
from typing import override

from dataforge.curation import (
  Actions,
  Context,
  End,
  Limits,
  Machine,
  PassAtKState,
  ScriptSpec,
  ScriptState,
  TrialAgent,
  TrialResult,
  TrialState,
)


class Trials:
  """Returns the given results, one per trial, in the order of trial names.

  Args:
    results: The results, by trial name.
  """

  def __init__(self, results: dict[str, TrialResult]) -> None:
    self.results: dict[str, TrialResult] = results
    self.agents: list[TrialAgent] = []

  def memory_gb(self, limits: Limits) -> float:
    return limits.memory_gb

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    self.agents.append(agent)
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
