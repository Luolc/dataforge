"""dataforge's own states, each one action of dataforge.curation.actions.

A dataset subclasses them: it gives what to run (spec, or the trial's task_dir, agent and
limits) and may override how the result becomes an outcome.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import override

from dataforge.curation.actions import (
    HOST,
    ActionResult,
    Actions,
    AgentSpec,
    Env,
    Limits,
    ScriptSpec,
    TrialAgent,
    TrialResult,
    fan_out,
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
        super().__init__(name, transitions, max_visits, on_exhausted)
        self.actions: Actions = actions


class AgentState(_ActionState):
    """Runs one agent. Outcomes: "ok" (it finished and its validator, if any, passes) or
    "failed". A subclass implements spec."""

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
        super().__init__(name, transitions, max_visits, on_exhausted, actions=actions)
        self.env: Env = env

    def spec(self, ctx: Context) -> AgentSpec:
        _ = ctx
        raise NotImplementedError

    def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
        _ = ctx
        return Outcome(result.status)

    @override
    def run(self, ctx: Context) -> Outcome:
        return self.outcome(ctx, self.actions.agent(ctx, self.spec(ctx), self.env))


class ScriptState(_ActionState):
    """Runs one script. Outcomes: "ok" (exit 0, every output present) or "failed". A subclass
    implements spec."""

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
        super().__init__(name, transitions, max_visits, on_exhausted, actions=actions)
        self.env: Env = env

    def spec(self, ctx: Context) -> ScriptSpec:
        _ = ctx
        raise NotImplementedError

    def outcome(self, ctx: Context, result: ActionResult) -> Outcome:
        _ = ctx
        return Outcome(result.status)

    @override
    def run(self, ctx: Context) -> Outcome:
        return self.outcome(ctx, self.actions.script(ctx, self.spec(ctx), self.env))


class TrialState(_ActionState):
    """Runs one trial, recorded as the output "trial". Outcomes: "solved", "unsolved", or
    "error" when the trial reports an exception.

    task_dir may be a path or a function of the context; agent and limits are fixed, or
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
        task_dir: Path | Callable[[Context], Path],
        agent: TrialAgent,
        limits: Limits,
    ) -> None:
        super().__init__(name, transitions, max_visits, on_exhausted, actions=actions)
        self._task_dir: Path | Callable[[Context], Path] = task_dir
        self._agent: TrialAgent = agent
        self._limits: Limits = limits

    def task_dir(self, ctx: Context) -> Path:
        return self._task_dir if isinstance(self._task_dir, Path) else self._task_dir(ctx)

    def agent(self, ctx: Context) -> TrialAgent:
        _ = ctx
        return self._agent

    def limits(self, ctx: Context) -> Limits:
        _ = ctx
        return self._limits

    def solved(self, result: TrialResult) -> bool:
        """By default, a reward "reward" of at least 1 with no exception."""
        return result.exception is None and result.rewards.get("reward", 0.0) >= 1.0

    def trial(self, ctx: Context, name: str) -> TrialResult:
        return self.actions.trial(ctx, name, self.task_dir(ctx), self.agent(ctx), self.limits(ctx))

    def outcome(self, ctx: Context, result: TrialResult) -> Outcome:
        _ = ctx
        if result.exception is not None:
            return Outcome("error", {"exception": result.exception})
        return Outcome("solved" if self.solved(result) else "unsolved", dict(result.rewards))

    @override
    def run(self, ctx: Context) -> Outcome:
        return self.outcome(ctx, self.trial(ctx, "trial"))


class PassAtKState(TrialState):
    """Runs k trials at once, recorded as the outputs "trial-0" to "trial-<k-1>". Outcomes:
    "all_solved", "none_solved" or "in_range"; a trial with an exception counts as unsolved.
    Data: {"solved": n, "errors": m, "k": k}."""

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
        _ = ctx
        solved = sum(self.solved(result) for result in results)
        errors = sum(result.exception is not None for result in results)
        name = {0: "none_solved", len(results): "all_solved"}.get(solved, "in_range")
        return Outcome(name, {"solved": solved, "errors": errors, "k": len(results)})

    @override
    def run(self, ctx: Context) -> Outcome:
        def one(i: int) -> Callable[[], TrialResult]:
            return lambda: self.trial(ctx, f"trial-{i}")

        return self.outcomes(ctx, fan_out([one(i) for i in range(self.k)]))
