"""dataforge's own states, each one action of dataforge.curation.actions.

A dataset subclasses them: it gives what to run (spec, or the trial's task_dir,
agent and limits) and may override how the result becomes an outcome.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import override

from dataforge.curation.actions import (
  ActionResult,
  Actions,
  AgentSpec,
  Env,
  fan_out,
  HOST,
  Limits,
  ScriptSpec,
  TrialAgent,
  TrialResult,
)
from dataforge.curation.engine import Context, End, Goto, Outcome, State


class _ActionState(State):
  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
  ) -> None:
    """Make a state that runs actions.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
    """
    super().__init__(name, transitions, max_visits, on_exhausted)
    self.actions: Actions = actions


class AgentState(_ActionState):
  """Runs one agent; a subclass implements spec.

  Outcomes: "ok" (it finished and its validator, if any, passes) or "failed".
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    env: Env = HOST,
  ) -> None:
    """Make the state.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      env: Where the action runs.
    """
    super().__init__(
      name, transitions, max_visits, on_exhausted, actions=actions
    )
    self.env: Env = env

  def spec(self, ctx: Context) -> AgentSpec:
    """Return what the agent runs; a subclass implements it.

    Args:
      ctx: The visit's context.

    Returns:
      The agent's spec.

    Raises:
      NotImplementedError: Always, here.
    """
    _ = ctx
    raise NotImplementedError

  def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
    """Turn the action's result into the visit's outcome.

    Args:
      ctx: The visit's context.
      result: The action's result.

    Returns:
      The outcome.
    """
    _ = ctx
    return Outcome(result.status)

  @override
  def run(self, ctx: Context) -> Outcome:
    return self.outcome(ctx, self.actions.agent(ctx, self.spec(ctx), self.env))


class ScriptState(_ActionState):
  """Runs one script; a subclass implements spec.

  Outcomes: "ok" (exit 0, every output present) or "failed".
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    env: Env = HOST,
  ) -> None:
    """Make the state.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      env: Where the action runs.
    """
    super().__init__(
      name, transitions, max_visits, on_exhausted, actions=actions
    )
    self.env: Env = env

  def spec(self, ctx: Context) -> ScriptSpec:
    """Return what the script runs; a subclass implements it.

    Args:
      ctx: The visit's context.

    Returns:
      The script's spec.

    Raises:
      NotImplementedError: Always, here.
    """
    _ = ctx
    raise NotImplementedError

  def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
    """Turn the action's result into the visit's outcome.

    Args:
      ctx: The visit's context.
      result: The action's result.

    Returns:
      The outcome.
    """
    _ = ctx
    return Outcome(result.status)

  @override
  def run(self, ctx: Context) -> Outcome:
    return self.outcome(ctx, self.actions.script(ctx, self.spec(ctx), self.env))


class TrialState(_ActionState):
  """Runs one trial, recorded as the output "trial".

  Outcomes: "solved", "unsolved", or "error" when the trial reports an
  exception.

  task_dir may be a path or a function of the context; agent and limits are
  fixed, or overridden as methods.
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    task_dir: Path | Callable[[Context], Path],
    agent: TrialAgent,
    limits: Limits,
  ) -> None:
    """Make a state that runs one trial.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      task_dir: The task's directory, or a function of the context that
        gives it.
      agent: The agent of the trial.
      limits: The limits of the trial.
    """
    super().__init__(
      name, transitions, max_visits, on_exhausted, actions=actions
    )
    self._task_dir: Path | Callable[[Context], Path] = task_dir
    self._agent: TrialAgent = agent
    self._limits: Limits = limits

  def task_dir(self, ctx: Context) -> Path:
    """Return the task's directory.

    Args:
      ctx: The visit's context.

    Returns:
      The directory.
    """
    return (
      self._task_dir
      if isinstance(self._task_dir, Path)
      else self._task_dir(ctx)
    )

  def agent(self, ctx: Context) -> TrialAgent:
    """Return the agent of the trial.

    Args:
      ctx: The visit's context.

    Returns:
      The agent.
    """
    _ = ctx
    return self._agent

  def limits(self, ctx: Context) -> Limits:
    """Return the limits of the trial.

    Args:
      ctx: The visit's context.

    Returns:
      The limits.
    """
    _ = ctx
    return self._limits

  def solved(self, result: TrialResult) -> bool:
    """Judge whether a trial solved the task.

    By default, a reward "reward" of at least 1 with no exception.

    Args:
      result: The trial's result.

    Returns:
      Whether it solved the task.
    """
    return result.exception is None and result.rewards.get("reward", 0.0) >= 1.0

  def trial(self, ctx: Context, name: str) -> TrialResult:
    """Run one trial.

    Args:
      ctx: The visit's context.
      name: The name of the trial's output.

    Returns:
      The trial's result.
    """
    return self.actions.trial(
      ctx, name, self.task_dir(ctx), self.agent(ctx), self.limits(ctx)
    )

  def outcome(self, ctx: Context, result: TrialResult) -> Outcome:
    """Turn the trial's result into the visit's outcome.

    Args:
      ctx: The visit's context.
      result: The trial's result.

    Returns:
      The outcome.
    """
    _ = ctx
    if result.exception is not None:
      return Outcome("error", {"exception": result.exception})
    return Outcome(
      "solved" if self.solved(result) else "unsolved", dict(result.rewards)
    )

  @override
  def run(self, ctx: Context) -> Outcome:
    return self.outcome(ctx, self.trial(ctx, "trial"))


class PassAtKState(TrialState):
  """Runs k trials at once, recorded as the outputs "trial-0" to "trial-<k-1>".

  Outcomes: "all_solved", "none_solved" or "in_range", judged on the trials that
  ran (no exception), so a failure of the trial machinery never reads as a hard
  task; "error" when none ran. Data: {"solved": n, "errors": m, "k": k}.
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    task_dir: Path | Callable[[Context], Path],
    agent: TrialAgent,
    limits: Limits,
    k: int,
  ) -> None:
    """Make a state that runs k trials at once.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      task_dir: The task's directory, or a function of the context that
        gives it.
      agent: The agent of each trial.
      limits: The limits of each trial.
      k: How many trials run.
    """
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      actions=actions,
      task_dir=task_dir,
      agent=agent,
      limits=limits,
    )
    self.k: int = k

  def outcomes(self, ctx: Context, results: Sequence[TrialResult]) -> Outcome:
    """Turn the results of the k trials into the visit's outcome.

    Args:
      ctx: The visit's context.
      results: The trials' results.

    Returns:
      The outcome.
    """
    _ = ctx
    ran = [result for result in results if result.exception is None]
    solved = sum(self.solved(result) for result in ran)
    data = {
      "solved": solved,
      "errors": len(results) - len(ran),
      "k": len(results),
    }
    if not ran:
      return Outcome("error", data)
    name = {0: "none_solved", len(ran): "all_solved"}.get(solved, "in_range")
    return Outcome(name, data)

  @override
  def run(self, ctx: Context) -> Outcome:
    def one(i: int) -> Callable[[], TrialResult]:
      return lambda: self.trial(ctx, f"trial-{i}")

    return self.outcomes(ctx, fan_out([one(i) for i in range(self.k)]))
