"""dataforge's own states, each running actions of dataforge.curation.actions.

A dataset configures them, giving what to run (spec, or the trial's task_dir,
agent and limits) and renaming their outcomes, or subclasses them to override
how the result becomes an outcome.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
import dataclasses
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import re
import string
from typing import cast, ClassVar, override

from dataforge.curation.actions import (
  ActionResult,
  Actions,
  AgentRun,
  AgentSpec,
  BUDGET,
  Env,
  fan_out,
  HOST,
  Judge,
  Limits,
  ScriptSpec,
  TrialAgent,
  TrialResult,
  Verdict,
)
from dataforge.curation.engine import (
  ActionRecord,
  Context,
  End,
  Goto,
  Input,
  Outcome,
  read_json,
  State,
)
from dataforge.curation.faults import trial_fault, trial_judged
from dataforge.curation.rubric import (
  block_text,
  Call,
  CALL_SUFFIX,
  judge_review,
  Judgment,
  notes_text,
  others_text,
  rereview_prompt,
  Review,
  review_prompt,
  Rubric,
  RUBRIC_REPORT,
  rubric_report,
  RUBRIC_TABLE,
  rubric_table,
  VERDICTS_SCHEMA,
)


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

  def given_outcomes(self) -> frozenset[str] | None:
    """Name the outcomes this state's outcome method gives, before rename.

    Returns:
      The class's OUTCOMES; a state whose judge decides overrides it.
    """
    return getattr(type(self), "OUTCOMES", None)

  @override
  def possible_outcomes(self) -> frozenset[str] | None:
    """Name the outcomes this state may give: given_outcomes, renamed.

    Returns:
      The outcomes' names, or None when they are not known.
    """
    given = self.given_outcomes()
    if given is None:
      return None
    return frozenset(self.rename.get(name, name) for name in given)

  @override
  def runners(self) -> Sequence[object]:
    """Name the agent runner of the state's actions, if they have one.

    Returns:
      The runners.
    """
    runner = self.actions.agent_runner
    return () if runner is None else (runner,)

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

  Outcomes: "ok" (the run ended well and its outputs, answer and validator, if
  any, pass), "failed" (they do not) or "error" (the run itself failed, so
  there is nothing to judge: an API error, a refusal, a timeout or the
  session's own spend cap, say; its reason gives the run's ending, fault and
  error, and its fault is the run's, Outcome.fault). Data: {"ending", "fixes",
  "cost_usd", "fault", "stop_reason", "error"}: the last run's ending, its
  fault, the model's stop reason and the error it ended on, as AgentRun has
  them, and cost_usd None when a run did not report its cost. On "failed" the
  outcome's feedback is {"details": the validator's last output, or the
  action's log when the validator passed or there is none}. With a judge,
  the judge runs on the host after an "ok", given the outputs of the agent's
  spec as inputs, and its verdict is the visit's outcome in place of "ok":
  its data is the run's, updated with the verdict's, and its feedback the
  verdict's.

  Attributes:
    OUTCOMES: The outcomes it gives, before rename and without a judge.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset(
    {"ok", "failed", "error"}
  )

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
    report_outputs: Sequence[str] = (),
    last_if_unresumable: bool = False,
    judge: Judge | None = None,
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
      report_outputs: Outputs that a visit which got a report must write
        anew: they join the spec's outputs, and each must differ from its
        last version, as AgentSpec.fresh_outputs says.
      last_if_unresumable: With True, an outcome after which the agent's
        session cannot go on has last=True.
      judge: What decides the outcome in place of "ok", if anything.
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
    self.report_outputs: tuple[str, ...] = tuple(report_outputs)
    self.last_if_unresumable: bool = last_if_unresumable
    self.judge: Judge | None = judge

  @override
  def given_outcomes(self) -> frozenset[str] | None:
    given = super().given_outcomes()
    if self.judge is None or given is None:
      return given
    judged = _judge_outcomes(self.judge)
    return None if judged is None else (given - {"ok"}) | judged

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
    spec = self._spec
    prompt = spec.prompt
    if ctx.report is not None and self.report_prompt is not None:
      prompt = self.report_prompt
    if ctx.report is not None and self.report_outputs:
      added = [name for name in self.report_outputs if name not in spec.outputs]
      spec = dataclasses.replace(
        spec,
        outputs=(*spec.outputs, *added),
        fresh_outputs=self.report_outputs,
      )
    return dataclasses.replace(spec, prompt=_render(prompt, ctx))

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
      "cost_usd": result.cost_usd if result.cost_known else None,
      "fault": None if run is None else run.fault,
      "stop_reason": None if run is None else run.stop_reason,
      "error": None if run is None else run.error,
    }
    last = self.last_if_unresumable and run is not None and not run.resumable
    if run is None or run.status != "ok":
      fault = None if run is None else run.fault
      return Outcome("error", data, last, reason=_why(run), fault=fault)
    if result.ok:
      return Outcome("ok", data, last)
    check = result.validation
    details = check.log if check is not None and check.exit_code else result.log
    return Outcome("failed", data, last, {"details": details})

  @override
  def run(self, ctx: Context) -> Outcome:
    spec = self.spec(ctx)
    result = self.actions.agent(ctx, spec, self.env)
    outcome = self.outcome(ctx, result)
    if self.judge is not None and outcome.name == "ok":
      verdict = self.actions.judge(ctx, self.judge, spec.outputs)
      outcome = dataclasses.replace(
        outcome,
        name=verdict.outcome,
        data={**outcome.data, **verdict.data},
        feedback=verdict.feedback,
      )
    return self.renamed(outcome)


class ScriptState(_ActionState):
  """Runs one script, on the spec it is given or the one a subclass makes.

  Outcomes: "ok" (exit 0, every output present) or "failed", whose feedback is
  {"details": the script's log}.

  Attributes:
    OUTCOMES: The outcomes it gives, before rename.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset({"ok", "failed"})

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

  Attributes:
    OUTCOMES: The outcomes it gives, before rename.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset(
    {"solved", "unsolved", "error"}
  )

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
    return _task_dir(ctx, self._task_dir)

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

  def trial(
    self, ctx: Context, name: str, replaces: str | None = None
  ) -> TrialResult:
    """Run one trial.

    Args:
      ctx: The visit's context.
      name: The name of the trial's output.
      replaces: The output of the trial of this visit whose place it takes,
        if it takes one's.

    Returns:
      The trial's result.
    """
    return self.actions.trial(
      ctx,
      name,
      self.task_dir(ctx),
      self.agent(ctx),
      self.limits(ctx),
      replaces=replaces,
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

  Outcomes: "all_solved", "none_solved" or "in_range", judged on all k
  trials; "error" when any of them cannot be judged
  (dataforge.curation.faults.trial_judged), such as one that the model's API,
  our timeout or the machine ended, so that neither a failure of the trial
  machinery nor a smaller sample decides how hard the task reads. Data:
  {"solved": n, "errors": m, "faults": {fault: count}, "k": k}, where errors
  counts the trials that cannot be judged and faults those of them by
  `trial_fault`, "unclear" for those with none. With a judge, the judge
  decides instead, given the outputs of the visit's trials as inputs, in the
  order they started.

  With retry, a script (Actions.retry), the trial of each of the k slots
  may run again in the same visit, in its place: after the k trials, and
  after each time trials ran again, while there is room for one more trial
  (room: the visit's reserve bounds what trials run again spend, and
  max_retries how many run), the script, given the outputs of the visit's
  trials so far as inputs, in the order they started, chooses the slots
  that run again, at once and started in the order of slots, until it
  chooses none.
  The n-th run again of slot i is recorded as "trial-<i>-r<n>"
  (pass_at_k_slot), whose record names the trial it replaces
  (TrialRecord.replaces); the last trial of each slot is the one the
  outcome counts, and the judge, if any, gets the room left as
  DATAFORGE_RETRY_ROOM.

  With resume, from the state's second visit on, the first trial of each
  slot goes on with the session of the last trial of the same slot in the
  visit before: an agent's configuration gets "resume", which names that
  trial's output and the cost its record gives, and, with
  resume_instruction, an input whose path is the file the session gets as
  its next message, when that file exists (HarborRunner, `resumption`). A
  slot the visit before ran no trial in, and a trial run again, start anew.

  Attributes:
    OUTCOMES: The outcomes it gives, before rename; with a judge, the
      judge's.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset(
    {"all_solved", "none_solved", "in_range", "error"}
  )

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
    retry: ScriptSpec | None = None,
    max_retries: int = 0,
    resume: bool = False,
    resume_instruction: Input | None = None,
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
      retry: What chooses the slots whose trial runs again, if any do; it
        names no outputs.
      max_retries: With a retry, the most trials one visit runs again.
      resume: Whether each slot's trial goes on with the session of the
        slot's last trial in the visit before.
      resume_instruction: With resume, the input whose path is the file
        that a session gone on with gets as its next message, if any.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.

    Raises:
      ValueError: When retry names outputs or builds, or there is a retry
        and no max_cost_usd or max_retries to bound it.
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
    if retry is not None and (
      retry.outputs or retry.optional_outputs or retry.build
    ):
      raise ValueError(f"{name}: a retry names no outputs and builds nothing")
    if retry is not None and max_cost_usd <= 0:
      raise ValueError(f"{name}: a retry needs a max_cost_usd to bound it")
    if retry is not None and max_retries < 1:
      raise ValueError(f"{name}: a retry needs a max_retries to bound it")
    self.k: int = k
    self.judge: Judge | None = judge
    self.retry: ScriptSpec | None = retry
    self.max_retries: int = max_retries
    self.resume: bool = resume
    self.resume_instruction: Input | None = resume_instruction

  def resumed(self, ctx: Context, name: str) -> TrialAgent:
    """Return the agent of a slot's first trial, with what it resumes.

    Args:
      ctx: The visit's context.
      name: The trial's output.

    Returns:
      The state's agent; with resume, from the second visit on, with
      "resume" for the slot's last trial in the visit before, when there is
      one.
    """
    agent = self.agent(ctx)
    slot = pass_at_k_slot(name)
    if not self.resume or isinstance(agent, str) or slot is None:
      return agent
    before = [
      v
      for v in ctx.attempt.visits
      if v.state == ctx.state and v.visit == ctx.visit - 1
    ]
    found: tuple[int, ActionRecord, Path] | None = None
    for action in before[-1].actions if before else ():
      for output, path in action.outputs.items():
        at = pass_at_k_slot(output) if action.kind == "trial" else None
        if (
          at is not None
          and at[0] == slot[0]
          and (found is None or at[1] > found[0])
        ):
          found = (at[1], action, path)
    if found is None:
      return agent
    _, action, path = found
    resume: dict[str, object] = {
      "trial": str(ctx.attempt.root / path),
      "cost_usd": action.cost_usd,
    }
    if self.resume_instruction is not None:
      given = ctx.resolve([], [self.resume_instruction]).values()
      if (instruction := next(iter(given), None)) is not None:
        resume["instruction"] = str(instruction)
    return {**agent, "resume": resume}

  @override
  def given_outcomes(self) -> frozenset[str] | None:
    if self.judge is not None:
      return _judge_outcomes(self.judge)
    return self.OUTCOMES

  def room(self, spent: float, retried: int) -> int:
    """Count the trials the visit still has room to run again.

    Each trial has a share of max_cost_usd, divided by k; the room is how
    many shares fit in what is left of it, and no more than what is left of
    max_retries. The share bounds what a trial run again spends only when
    max_cost_usd is at least k times each trial's own spend cap.

    Args:
      spent: What the visit has spent, in US dollars.
      retried: How many trials the visit has run again.

    Returns:
      The number of trials, 0 when none fits.
    """
    share = self.max_cost_usd / self.k
    shares = int((self.max_cost_usd - spent) / share + 1e-9)
    return max(min(shares, self.max_retries - retried), 0)

  def outcomes(
    self,
    ctx: Context,
    results: Sequence[TrialResult],
    tried: Sequence[str] | None = None,
    room: int | None = None,
  ) -> Outcome:
    """Turn the results of the k trials into the visit's outcome.

    Args:
      ctx: The visit's context.
      results: The result of each slot's last trial, in the order of slots.
      tried: The outputs of the visit's trials, in the order they started;
        None for "trial-0" to "trial-<k-1>".
      room: The trials the visit still has room to run again, given to the
        judge, with a retry.

    Returns:
      The outcome.
    """
    if self.judge is not None:
      names = (
        list(tried)
        if tried is not None
        else [pass_at_k_name(i) for i in range(len(results))]
      )
      variables = None if room is None else {"DATAFORGE_RETRY_ROOM": str(room)}
      return _judged(self.actions.judge(ctx, self.judge, names, variables))
    stopped = [r.exception for r in results if not trial_judged(r.exception)]
    solved = sum(self.solved(result) for result in results)
    faults = Counter(trial_fault(e) or "unclear" for e in stopped)
    data = {
      "solved": solved,
      "errors": len(stopped),
      "faults": dict(sorted(faults.items())),
      "k": len(results),
    }
    if stopped:
      return Outcome("error", data)
    name = {0: "none_solved", len(results): "all_solved"}.get(
      solved, "in_range"
    )
    return Outcome(name, data)

  @override
  def run(self, ctx: Context) -> Outcome:
    # What the visit spent: what its trials report, or more when the
    # machine's measure of the attempt's spend has grown by more, as it
    # does for a trial that reported no cost.
    start = ctx.attempt.cost_usd

    def one(name: str) -> Callable[[], TrialResult]:
      if not self.resume:
        return lambda: self.trial(ctx, name)
      return lambda: self.actions.trial(
        ctx, name, self.task_dir(ctx), self.resumed(ctx, name), self.limits(ctx)
      )

    def again(name: str, replaces: str) -> Callable[[], TrialResult]:
      return lambda: self.trial(ctx, name, replaces)

    names = [pass_at_k_name(i) for i in range(self.k)]
    results = fan_out([one(name) for name in names])
    if self.retry is None:
      # As a subclass written before retry calls and overrides them.
      return self.renamed(self.outcomes(ctx, results))
    tried, runs = list(names), [0] * self.k
    reported = sum(result.cost_usd for result in results)
    calls = 0
    while True:
      spent = max(reported, ctx.attempt.cost_usd - start)
      room = self.room(spent, len(tried) - self.k)
      if room < 1:
        break
      calls += 1
      slots = self.actions.retry(
        ctx, self.retry, tried, k=self.k, room=room, call=calls
      )
      if not slots:
        break
      chosen: list[tuple[int, str]] = []
      for slot in sorted(slots):
        runs[slot] += 1
        chosen.append((slot, pass_at_k_name(slot, runs[slot])))
      found = fan_out([again(new, names[slot]) for slot, new in chosen])
      for (slot, new), result in zip(chosen, found, strict=True):
        names[slot], results[slot] = new, result
        tried.append(new)
        reported += result.cost_usd
    return self.renamed(self.outcomes(ctx, results, tried, room))


_SLOT = re.compile(r"trial-(0|[1-9][0-9]*)(?:-r([1-9][0-9]*))?")


def pass_at_k_name(slot: int, run: int = 0) -> str:
  """Name the output of a PassAtKState's trial.

  Args:
    slot: Its slot, from 0.
    run: How many times the slot has run again with it, 0 for its first
      trial.

  Returns:
    "trial-<slot>", or "trial-<slot>-r<run>" for a run again.
  """
  return f"trial-{slot}" if run == 0 else f"trial-{slot}-r{run}"


def pass_at_k_slot(name: str) -> tuple[int, int] | None:
  """Read the slot and the run of a PassAtKState's trial from its output.

  Args:
    name: The output's name.

  Returns:
    The slot, from 0, and the run, 0 for the slot's first trial and n for
    its n-th run again (pass_at_k_name); None for a name it does not give.
  """
  match = _SLOT.fullmatch(name)
  if match is None:
    return None
  return int(match[1]), 0 if match[2] is None else int(match[2])


class TrialsState(_ActionState):
  """Runs trials of several agents, each a given number of times, at once.

  Outputs: "<name>-<i>" for the i-th trial of the agent named name, from 0.
  With each_task, task_dir is a directory of tasks and every agent runs on
  each of its subdirectories: "<name>-<subdir>", or "<name>-<subdir>-<i>"
  when the agent runs more than once. A subdirectory that holds SOURCE names
  the trial whose output its task judges again, and its trials record it as
  their source.

  Outcomes: "ok" when no trial reports an exception, else "error". Data: each
  trial's {"rewards", "exception"}, by output name. With a judge, the judge
  decides instead, given the trials' outputs as inputs, in the order of trials.

  Attributes:
    OUTCOMES: The outcomes it gives, before rename; with a judge, the
      judge's.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset({"ok", "error"})

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    task_dir: Path | str | Callable[[Context], Path],
    agents: Mapping[str, int],
    limits: Limits,
    configs: Mapping[str, TrialAgent] | None = None,
    each_task: bool = False,
    judge: Judge | None = None,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    """Make a state that runs trials of several agents at once.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      task_dir: The task's directory, the artifact that is, or a function of
        the context that gives it; with each_task, the directory of tasks.
      agents: How many trials each agent runs, by its name.
      limits: The limits of each trial.
      configs: The agent each name runs, for a name that is not itself the
        agent (such as "nop" or "oracle").
      each_task: Whether every agent runs on each subdirectory of task_dir.
      judge: What decides the outcome in place of looking for exceptions, if
        anything.
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
    self.agents: Mapping[str, int] = dict(agents)
    self.limits: Limits = limits
    self.configs: Mapping[str, TrialAgent] = dict(configs or {})
    self.each_task: bool = each_task
    self.judge: Judge | None = judge

  @override
  def given_outcomes(self) -> frozenset[str] | None:
    if self.judge is not None:
      return _judge_outcomes(self.judge)
    return self.OUTCOMES

  def trials(self, ctx: Context) -> list[tuple[str, Path, TrialAgent]]:
    """Return the trials of a visit: output name, task directory and agent.

    Args:
      ctx: The visit's context.

    Returns:
      The trials, in the order of agents, then of tasks, then of runs.
    """
    root = _task_dir(ctx, self._task_dir)
    tasks = [(None, root)]
    if self.each_task:
      tasks = [(p.name, p) for p in sorted(root.iterdir()) if p.is_dir()]
    found: list[tuple[str, Path, TrialAgent]] = []
    for name, n in self.agents.items():
      agent = self.configs.get(name, name)
      for task, path in tasks:
        for i in range(n):
          parts = [name] if task is None else [name, task]
          if task is None or n > 1:
            parts.append(str(i))
          found.append(("-".join(parts), path, agent))
    return found

  @override
  def run(self, ctx: Context) -> Outcome:
    trials = self.trials(ctx)
    # Every source is read before any trial starts, so that a task that
    # names its source wrongly costs nothing.
    sources = {
      task_dir: _source(ctx.attempt.root, task_dir) if self.each_task else None
      for _, task_dir, _ in trials
    }

    def one(
      name: str, task_dir: Path, agent: TrialAgent
    ) -> Callable[[], TrialResult]:
      return lambda: self.actions.trial(
        ctx, name, task_dir, agent, self.limits, sources[task_dir]
      )

    results = fan_out([one(*trial) for trial in trials])
    if self.judge is not None:
      names = [name for name, _, _ in trials]
      return self.renamed(_judged(self.actions.judge(ctx, self.judge, names)))
    data = {
      name: {"rewards": dict(result.rewards), "exception": result.exception}
      for (name, _, _), result in zip(trials, results, strict=True)
    }
    errored = any(result.exception is not None for result in results)
    return self.renamed(Outcome("error" if errored else "ok", data))


class JudgeState(_ActionState):
  """Runs a judge (see Judge) and takes its verdict as the outcome.

  Its inputs, such as the outputs of an earlier state, are its spec's.

  Attributes:
    OUTCOMES: None: the judge decides, and Judge.outcomes may name what it
      gives.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = None

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    judge: Judge,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    """Make a state that runs a judge.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      judge: The judge.
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
    self.judge: Judge = judge

  @override
  def given_outcomes(self) -> frozenset[str] | None:
    return _judge_outcomes(self.judge)

  @override
  def run(self, ctx: Context) -> Outcome:
    return self.renamed(_judged(self.actions.judge(ctx, self.judge)))


class RubricState(_ActionState):
  """Runs each of its reviews at once, each an agent with no tools.

  Outcomes: "pass" when every review passes; "fail" when one fails, with
  feedback {"details": the findings of each failed review, in order, under
  "### <name>"}; "error", with that feedback, where a review whose agent run
  failed, such as on a refusal, a timeout or no structured output, gives
  "<name> returned no verdicts (<how it ended>).", if one did, so there is
  nothing to judge (judge_review), and those findings as its reason; its fault
  (Outcome.fault) is "budget" when each such review hit its own spend cap,
  else the first other fault of their runs. A review whose run failed is run
  once more in the same visit, as retries chooses, and the reviews that gave
  verdicts are not; the retry's answer is the call's. Data: each review's
  {"ending", "cost_usd", "passed", "failed", "verdicts", "fault", "error",
  "retried"}, by name. Every visit, whatever its outcome, also writes the
  outputs RUBRIC_TABLE (rubric_table), RUBRIC_REPORT (rubric_report), which
  gives the finding of each review that gave no verdicts, and, for each
  review, "<name>" CALL_SUFFIX, its Call, recorded as an action of kind
  "report".

  Each visit calls each review once. A call compares its blocks with those
  of the last call of the review in this state that gave verdicts, one for
  each criterion of its rubric (Call.judged), which rereview_prompt calls
  the last review. With resume, each review's agent
  has the session key "<state>/<review>" and a call after such a one is a
  re-review: its prompt is the whole prompt with what rereview_prompt adds,
  and its resume prompt what rereview_prompt gives alone. With
  reuse_unchanged, a call whose blocks are those of the last call of the
  review that ran, when that call gave verdicts, runs nothing: it gives that
  call's answer again, at no cost, and no session moves on; but when that
  call did not pass (Call.passed) and a note has a version written since
  it, the call runs, so the reviewer reads what the author answered. Each
  call gives the review's notes written since the last review, or all for a
  first call.
  With cross_criteria, the whole prompt of each review also gives the
  criteria of the state's other reviews (others_text); a resume prompt does
  not, since the session has them from its whole prompt, and they are not a
  block, so they do not keep a call from reuse.

  Attributes:
    OUTCOMES: The outcomes it gives, before rename.
  """

  OUTCOMES: ClassVar[frozenset[str] | None] = frozenset(
    {"pass", "fail", "error"}
  )

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
    *,
    actions: Actions,
    reviews: Sequence[Review],
    env: Env = HOST,
    workdir: Path | None = None,
    rename: Mapping[str, str] | None = None,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
    resume: bool = False,
    reuse_unchanged: bool = False,
    cross_criteria: bool = False,
  ) -> None:
    """Make the state; each rubric is read here.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      actions: What runs the state's actions.
      reviews: The reviews.
      env: Where the agents run.
      workdir: The agents' working directory; None for the visit's output
        directory, which suits the host only.
      rename: A new name for each outcome that is to have one, by its name.
      feedback: The state's feedback template.
      max_cost_usd: The most one visit may spend, in US dollars.
      resume: Whether a review goes on with its session and re-reviews.
      reuse_unchanged: Whether a review whose blocks did not change gives
        its last answer again instead of running.
      cross_criteria: Whether a review's prompt gives the criteria of the
        other reviews.

    Raises:
      ValueError: When two reviews have one name, or a rubric is not one.
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
    self.reviews: tuple[Review, ...] = tuple(reviews)
    if len({review.name for review in self.reviews}) != len(self.reviews):
      raise ValueError(f"{name}: two reviews have one name")
    self.rubrics: dict[str, Rubric] = {
      review.name: Rubric.load(review.rubric) for review in self.reviews
    }
    self.env: Env = env
    self.workdir: Path | None = workdir
    self.resume: bool = resume
    self.reuse_unchanged: bool = reuse_unchanged
    self.cross_criteria: bool = cross_criteria

  def calls(self, ctx: Context, review: Review) -> list[Call]:
    """Return the earlier calls of a review in this state.

    Args:
      ctx: The visit's context.
      review: The review.

    Returns:
      Its calls, oldest first.
    """
    return [
      Call.load(version.path)
      for version in ctx.artifacts.history(review.name + CALL_SUFFIX)
      if version.state == ctx.state
    ]

  def call(
    self, ctx: Context, review: Review, workdir: Path
  ) -> Callable[[], tuple[Call, ActionResult]]:
    """Prepare this visit's call of a review.

    Args:
      ctx: The visit's context.
      review: The review.
      workdir: The agent's working directory.

    Returns:
      What makes the call and gives it, with the result of its agent.
    """
    calls = self.calls(ctx, review)
    ran = [call for call in calls if call.reused is None]
    rubric = self.rubrics[review.name]
    judged = [call for call in ran if call.judged(rubric)]
    last = judged[-1] if judged else None
    blocks = {
      tag: block_text(next(iter(ctx.resolve([item]).values())))
      for tag, item in review.inputs.items()
    }
    written = {
      tag: ctx.artifacts.history(name) for tag, name in review.notes.items()
    }
    call = Call(
      len(calls) + 1,
      blocks,
      {tag: len(versions) for tag, versions in written.items()},
      None,
      None,
      changed=(
        None
        if last is None
        else tuple(
          t for t, text in blocks.items() if last.blocks.get(t) != text
        )
      ),
    )
    if (
      self.reuse_unchanged
      and last is not None
      and ran[-1] is last
      and not call.changed
      and (
        last.passed(rubric)
        or all(n <= last.notes.get(t, 0) for t, n in call.notes.items())
      )
    ):
      reused = dataclasses.replace(
        call, verdicts=last.verdicts, ending=last.ending, reused=last.call
      )
      return lambda: (reused, _reused(last))
    seen: Mapping[str, int] = {} if last is None else last.notes
    notes = notes_text(
      {tag: versions[seen.get(tag, 0) :] for tag, versions in written.items()}
    )
    others = (
      others_text(
        {r.name: self.rubrics[r.name] for r in self.reviews if r is not review}
      )
      if self.cross_criteria
      else ""
    )
    if last is None or not self.resume:
      spec = self._spec(
        review, workdir, review_prompt(rubric, blocks, notes, others=others)
      )
    else:
      spec = self._spec(
        review,
        workdir,
        review_prompt(
          rubric,
          blocks,
          notes,
          rereview_prompt(last, blocks, notes, whole=True),
          others,
        ),
        rereview_prompt(
          last,
          blocks,
          notes,
          whole=False,
          later=None if ran[-1] is last else ran[-1].blocks,
        ),
      )
    return lambda: self._run(ctx, spec, call)

  def _spec(
    self,
    review: Review,
    workdir: Path,
    prompt: str,
    resume_prompt: str | None = None,
  ) -> AgentSpec:
    return AgentSpec(
      prompt,
      workdir,
      output_schema=VERDICTS_SCHEMA,
      tools=(),
      name=review.name,
      session=f"{self.name}/{review.name}" if self.resume else None,
      resume_prompt=resume_prompt,
    )

  def _run(
    self, ctx: Context, spec: AgentSpec, call: Call
  ) -> tuple[Call, ActionResult]:
    result = self.actions.agent(ctx, spec, self.env)
    run = result.run
    ran = dataclasses.replace(
      call,
      verdicts=result.value,
      ending=None if run is None else run.ending,
      resumed=run is not None and run.resumed,
    )
    return ran, result

  def retries(self, results: Sequence[ActionResult]) -> list[int]:
    """Choose which reviews that gave no verdicts run again in this visit.

    A review whose run hit its own spend cap (the fault `budget`) is not
    retried, since a retry would hit the same cap on the same prompt. Each
    review has a share of the visit's reserve, max_cost_usd divided by the
    number of reviews. The other reviews whose run failed are retried in order
    while what the visit has spent, a run that did not report its cost
    counted as a share, and a share for each retry, stay within
    max_cost_usd; with no reserve (0), each is retried. The share bounds
    what a retry spends only when max_cost_usd is at least the number of
    reviews times each review's spend cap.

    Args:
      results: The first run of each review, in order.

    Returns:
      The indexes of the reviews to retry.
    """
    failed = [
      i
      for i, result in enumerate(results)
      if _errored(result) and not _capped(result)
    ]
    if not self.max_cost_usd:
      return failed
    share = self.max_cost_usd / len(self.reviews)
    spent = sum(r.cost_usd if r.cost_known else share for r in results)
    room = int((self.max_cost_usd - spent) / share + 1e-9)
    return failed[: max(room, 0)]

  @override
  def run(self, ctx: Context) -> Outcome:
    workdir = ctx.outdir if self.workdir is None else self.workdir
    calls = [self.call(ctx, review, workdir) for review in self.reviews]
    ended = fan_out(calls)
    first = [result for _, result in ended]
    retried = self.retries(first)
    again = fan_out([calls[i] for i in retried])
    ended = list(ended)
    for i, found in zip(retried, again, strict=True):
      ended[i] = found

    def unretried(result: ActionResult) -> str | None:
      if not _errored(result):
        return None
      if _capped(result):
        return "it hit its own spend cap, which a retry would hit again"
      return "the visit's reserve has no room for it"

    judged = [
      dataclasses.replace(
        judge_review(
          review.name,
          self.rubrics[review.name],
          result,
          earlier=first[i] if i in retried else None,
          unretried=unretried(result),
        ),
        call=call.call,
        resumed=call.resumed,
        reused=call.reused,
        changed=call.changed,
      )
      for i, (review, (call, result)) in enumerate(
        zip(self.reviews, ended, strict=True)
      )
    ]
    calls = {
      review.name: call
      for review, (call, _) in zip(self.reviews, ended, strict=True)
    }
    _write_rubric(ctx, judged, calls, self.rubrics)
    data = {judgment.name: judgment.data for judgment in judged}
    failed = [j for j in judged if j.errored or not j.passed]
    if not failed:
      return self.renamed(Outcome("pass", data))
    details = "\n\n".join(f"### {j.name}\n\n{j.findings}" for j in failed)
    if errored := [j for j in failed if j.errored]:
      why = " ".join(j.findings for j in errored)
      return self.renamed(
        Outcome(
          "error",
          data,
          feedback={"details": details},
          reason=why,
          fault=_fault(errored),
        )
      )
    return self.renamed(Outcome("fail", data, feedback={"details": details}))


def _why(run: AgentRun | None) -> str:
  # Why an agent's run gave nothing to judge, as an attempt's reason.
  if run is None:
    return "the agent did not run"
  why = f"the agent's run ended {run.ending}"
  if run.fault is not None:
    why += f", {run.fault}"
  return why if run.error is None else f"{why}: {run.error}"


def _fault(errored: Sequence[Judgment]) -> str | None:
  # The fault of reviews that gave nothing to judge: `budget` when each hit
  # its own cap, else the first other fault one of them has.
  faults = [judgment.data["fault"] for judgment in errored]
  if all(fault == BUDGET for fault in faults):
    return BUDGET
  return next((f for f in faults if isinstance(f, str) and f != BUDGET), None)


def _errored(result: ActionResult) -> bool:
  return result.run is None or result.run.status != "ok"


def _capped(result: ActionResult) -> bool:
  return result.run is not None and result.run.fault == BUDGET


def _reused(call: Call) -> ActionResult:
  answer = json.dumps(call.verdicts)
  run = AgentRun(None, "ok", 0.0, None, answer, call.ending)
  return ActionResult(
    "ok", {}, f"reused call {call.call}", call.verdicts, 0.0, run
  )


def _write_rubric(
  ctx: Context,
  judged: Sequence[Judgment],
  calls: Mapping[str, Call],
  rubrics: Mapping[str, Rubric],
) -> None:
  started = datetime.now(UTC)
  names = {name + CALL_SUFFIX: call for name, call in calls.items()}
  _ = ctx.claim([RUBRIC_TABLE, RUBRIC_REPORT, *names])
  table, report = ctx.outdir / RUBRIC_TABLE, ctx.outdir / RUBRIC_REPORT
  _ = table.write_text(json.dumps(rubric_table(judged), indent=2) + "\n")
  _ = report.write_text(rubric_report(judged, rubrics))
  outputs = {RUBRIC_TABLE: table, RUBRIC_REPORT: report}
  for name, call in names.items():
    outputs[name] = ctx.outdir / name
    _ = outputs[name].write_text(json.dumps(call.to_json(), indent=2) + "\n")
  ctx.record(
    ActionRecord("report", "ok", outputs, started, datetime.now(UTC), 0.0)
  )


SOURCE = "source.json"
"""The file of a task, in a TrialsState's directory of tasks, that names the
trial whose output the task judges again: {"trial": <path>}, the path of the
trial's directory relative to the attempt's, as its record gives it."""


def _source(root: Path, task_dir: Path) -> Path | None:
  file = task_dir / SOURCE
  if not os.path.lexists(file):
    return None
  try:
    data = read_json(file)
  except (OSError, ValueError) as error:
    raise ValueError(f"{file}: {error}") from error
  if not isinstance(data, dict) or list(cast(dict[str, object], data)) != [
    "trial"
  ]:
    raise ValueError(f'{file}: not {{"trial": <path>}}')
  value = cast(dict[str, object], data)["trial"]
  if not isinstance(value, str):
    raise ValueError(f"{file}: trial is not a string")
  path = Path(value)
  if not path.parts or path.is_absolute() or ".." in path.parts:
    raise ValueError(f"{file}: {value!r} is not inside the attempt")
  return root / path


def _task_dir(
  ctx: Context, task_dir: Path | str | Callable[[Context], Path]
) -> Path:
  if isinstance(task_dir, Path):
    return task_dir
  if isinstance(task_dir, str):
    return next(iter(ctx.resolve([task_dir]).values()))
  return task_dir(ctx)


def _judge_outcomes(judge: Judge) -> frozenset[str] | None:
  return frozenset(judge.outcomes) if judge.outcomes else None


def _judged(verdict: Verdict) -> Outcome:
  return Outcome(verdict.outcome, verdict.data, feedback=verdict.feedback)


def _render(template: str, ctx: Context) -> str:
  report = ctx.report
  path = None if report is None else report.artifacts.get("feedback")
  return string.Template(template).safe_substitute(
    state=ctx.state,
    visit=ctx.visit,
    round=ctx.round,
    feedback="" if path is None else path.read_text(),
  )
