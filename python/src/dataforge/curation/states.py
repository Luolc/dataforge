"""dataforge's own states, each one action of dataforge.curation.actions.

A dataset configures them, giving what to run (spec, or the trial's task_dir,
agent and limits) and renaming their outcomes, or subclasses them to override
how the result becomes an outcome.
"""

from collections.abc import Callable, Mapping, Sequence
import dataclasses
from pathlib import Path
import string
from typing import override

from dataforge.curation.actions import (
  ActionResult,
  Actions,
  AgentSpec,
  Env,
  fan_out,
  HOST,
  Judge,
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
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    """Make a state that runs actions.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.
    """
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self.actions: Actions = actions
    self.rename: Mapping[str, str] = dict(rename or {})

  def renamed(self, outcome: Outcome) -> Outcome:
    """Give an outcome the name rename gives it, if any.

    Args:
      outcome: The outcome.

    Returns:
      The outcome under its new name.
    """
    name = self.rename.get(outcome.name, outcome.name)
    return dataclasses.replace(outcome, name=name)


class AgentState(_ActionState):
  """Runs one agent, on the spec it is given or the one a subclass makes.

  Outcomes: "ok" (the run ended well and its outputs, answer and validator,
  if any, pass), "failed" (they do not) or "error" (the run itself failed,
  so there is nothing to judge). Data: {"ending", "fixes", "cost_usd"}. On
  "failed" the outcome's feedback is {"details": the validator's last output,
  or the action's log when the validator passed or there is none}.
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
    spec: AgentSpec | None = None,
    report_prompt: str | None = None,
    last_if_unresumable: bool = False,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
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
      spec: What the agent runs. Its prompt is a string.Template that sees
        ${state}, ${visit}, ${round} and ${feedback}, the text of the
        feedback the state's report holds ("" without one); placeholders it
        does not know and a lone $ are left as they are.
      report_prompt: The prompt, a template as above, in place of the spec's
        when the visit got a report.
      last_if_unresumable: With True, an outcome after which the agent's
        session cannot go on has last=True.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.
    """
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      actions=actions,
      rename=rename,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self.env: Env = env
    self._spec: AgentSpec | None = spec
    self.report_prompt: str | None = report_prompt
    self.last_if_unresumable: bool = last_if_unresumable

  def spec(self, ctx: Context) -> AgentSpec:
    """Return what the agent runs: the spec given, its prompt rendered.

    Args:
      ctx: The visit's context.

    Returns:
      The agent's spec.

    Raises:
      NotImplementedError: When the state was given no spec.
    """
    if self._spec is None:
      raise NotImplementedError(f"{self.name}: no spec given or made")
    prompt = self._spec.prompt
    if ctx.report is not None and self.report_prompt is not None:
      prompt = self.report_prompt
    return dataclasses.replace(self._spec, prompt=_render(prompt, ctx))

  def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
    """Turn the action's result into the visit's outcome.

    Args:
      ctx: The visit's context.
      result: The action's result.

    Returns:
      The outcome.
    """
    _ = ctx
    run = result.run
    data = {
      "ending": None if run is None else run.ending,
      "fixes": result.fixes,
      "cost_usd": result.cost_usd,
    }
    last = self.last_if_unresumable and run is not None and not run.resumable
    if run is None or run.status != "ok":
      return Outcome("error", data, last)
    if result.ok:
      return Outcome("ok", data, last)
    check = result.validation
    details = check.log if check is not None and check.exit_code else result.log
    return Outcome("failed", data, last, {"details": details})

  @override
  def run(self, ctx: Context) -> Outcome:
    result = self.actions.agent(ctx, self.spec(ctx), self.env)
    return self.renamed(self.outcome(ctx, result))


class ScriptState(_ActionState):
  """Runs one script, on the spec it is given or the one a subclass makes.

  Outcomes: "ok" (exit 0, every output present) or "failed", whose feedback is
  {"details": the script's log}.
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
    spec: ScriptSpec | None = None,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
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
      spec: What the script runs.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.
    """
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      actions=actions,
      rename=rename,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self.env: Env = env
    self._spec: ScriptSpec | None = spec

  def spec(self, ctx: Context) -> ScriptSpec:
    """Return what the script runs: the spec given.

    Args:
      ctx: The visit's context.

    Returns:
      The script's spec.

    Raises:
      NotImplementedError: When the state was given no spec.
    """
    _ = ctx
    if self._spec is None:
      raise NotImplementedError(f"{self.name}: no spec given or made")
    return self._spec

  def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
    """Turn the action's result into the visit's outcome.

    Args:
      ctx: The visit's context.
      result: The action's result.

    Returns:
      The outcome.
    """
    _ = ctx
    if result.ok:
      return Outcome("ok")
    return Outcome("failed", feedback={"details": result.log})

  @override
  def run(self, ctx: Context) -> Outcome:
    result = self.actions.script(ctx, self.spec(ctx), self.env)
    return self.renamed(self.outcome(ctx, result))


class TrialState(_ActionState):
  """Runs one trial, recorded as the output "trial".

  Outcomes: "solved", "unsolved", or "error" when the trial reports an
  exception.

  task_dir may be a path, an artifact (or a path inside one, as an Input
  names it) or a function of the context; agent and limits are fixed, or
  overridden as methods.
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    task_dir: Path | str | Callable[[Context], Path],
    agent: TrialAgent,
    limits: Limits,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    """Make a state that runs one trial.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      task_dir: The task's directory, the artifact that is, or a function of
        the context that gives it.
      agent: The agent of the trial.
      limits: The limits of the trial.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.
    """
    super().__init__(
      name,
      transitions,
      max_visits,
      on_exhausted,
      actions=actions,
      rename=rename,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self._task_dir: Path | str | Callable[[Context], Path] = task_dir
    self._agent: TrialAgent = agent
    self._limits: Limits = limits

  def task_dir(self, ctx: Context) -> Path:
    """Return the task's directory.

    Args:
      ctx: The visit's context.

    Returns:
      The directory.
    """
    task_dir = self._task_dir
    if isinstance(task_dir, Path):
      return task_dir
    if isinstance(task_dir, str):
      return ctx.resolve([task_dir])[task_dir]
    return task_dir(ctx)

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
    return self.renamed(self.outcome(ctx, self.trial(ctx, "trial")))


class PassAtKState(TrialState):
  """Runs k trials at once, recorded as the outputs "trial-0" to "trial-<k-1>".

  Outcomes: "all_solved", "none_solved" or "in_range", judged on the trials that
  ran (no exception), so a failure of the trial machinery never reads as a hard
  task; "error" when none ran. Data: {"solved": n, "errors": m, "k": k}. With a
  judge, the judge decides instead, given the k trials' outputs as inputs.
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    task_dir: Path | str | Callable[[Context], Path],
    agent: TrialAgent,
    limits: Limits,
    k: int,
    judge: Judge | None = None,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    """Make a state that runs k trials at once.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      task_dir: The task's directory, the artifact that is, or a function of
        the context that gives it.
      agent: The agent of each trial.
      limits: The limits of each trial.
      k: How many trials run.
      judge: What decides the outcome in place of counting, if anything.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.
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
      rename=rename,
      feedback=feedback,
      max_cost_usd=max_cost_usd,
    )
    self.k: int = k
    self.judge: Judge | None = judge

  def outcomes(self, ctx: Context, results: Sequence[TrialResult]) -> Outcome:
    """Turn the results of the k trials into the visit's outcome.

    Args:
      ctx: The visit's context.
      results: The trials' results.

    Returns:
      The outcome.
    """
    if self.judge is not None:
      names = [f"trial-{i}" for i in range(len(results))]
      verdict = self.actions.judge(ctx, self.judge, names)
      return Outcome(verdict.outcome, verdict.data, feedback=verdict.feedback)
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

    results = fan_out([one(i) for i in range(self.k)])
    return self.renamed(self.outcomes(ctx, results))


def _render(template: str, ctx: Context) -> str:
  report = ctx.report
  path = None if report is None else report.artifacts.get("feedback")
  return string.Template(template).safe_substitute(
    state=ctx.state,
    visit=ctx.visit,
    round=ctx.round,
    feedback="" if path is None else path.read_text(),
  )
