"""The engine: states, transitions, caps, artifacts and the record of an attempt.

A dataset describes its process as states. Each state has a body, written by the dataset,
that does the work (usually through the actions of dataforge.statemachine.actions) and
returns an outcome; the state's transitions map each outcome to the next state or to the
end. The engine knows no state by name: the states, their transitions and their caps all
come from the dataset.
"""

import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

type Data = Mapping[str, object]
"""Data a state or an action hands on; the dataset keeps it JSON-serializable."""

type Input = str | Path
"""An action input: a str names an artifact (its latest version), a Path is used as is."""


@dataclass(frozen=True)
class Outcome:
    """What a state's body returns. With last=True the state is never entered again."""

    name: str
    data: Data = field(default_factory=dict[str, object])
    last: bool = False


@dataclass(frozen=True)
class Goto:
    """Go to a state. With report=True it gets a Report of this visit as ctx.report.

    A mark (for example "too_easy") keeps this visit: when the attempt later ends at a cap or
    a guard, the last marked visit is kept and the mark becomes the attempt's status.
    """

    state: str
    report: bool = True
    mark: str | None = None


@dataclass(frozen=True)
class End:
    """End the attempt with the given status."""

    status: str


@dataclass(frozen=True)
class State:
    """A state: its body, where each outcome leads, and how often it may be entered.

    Entering it once more than max_visits times, or after it returned last=True, ends the
    attempt: with the last mark if there was one, else with the status on_exhausted.
    """

    name: str
    body: Callable[[Context], Outcome]
    transitions: Mapping[str, Goto | End]
    max_visits: int
    on_exhausted: str = "failed"


@dataclass(frozen=True)
class Report:
    """What a state that went to another one hands it: its outcome, data and outputs."""

    from_state: str
    outcome: str
    data: Data
    artifacts: Mapping[str, Path]


@dataclass(frozen=True)
class Version:
    state: str
    visit: int
    path: Path


class Artifacts:
    """The outputs of an attempt's actions, by name, each name with its versions in order."""

    def __init__(self) -> None:
        self._versions: dict[str, list[Version]] = {}
        self._lock: threading.Lock = threading.Lock()

    def get(self, name: str) -> Path:
        """The latest version of an artifact; KeyError when there is none."""
        versions = self.history(name)
        if not versions:
            raise KeyError(name)
        return versions[-1].path

    def history(self, name: str) -> list[Version]:
        with self._lock:
            return list(self._versions.get(name, ()))

    def _add(self, name: str, version: Version) -> None:
        with self._lock:
            self._versions.setdefault(name, []).append(version)


@dataclass
class ActionRecord:
    kind: str
    status: str
    outputs: dict[str, Path]
    started_at: datetime
    ended_at: datetime
    cost_usd: float


@dataclass
class Visit:
    """One entry into a state. A body that raised has outcome "raised"."""

    state: str
    visit: int
    outcome: str | None = None
    data: Data = field(default_factory=dict[str, object])
    mark: str | None = None
    actions: list[ActionRecord] = field(default_factory=list[ActionRecord])

    @property
    def outputs(self) -> dict[str, Path]:
        return {name: path for action in self.actions for name, path in action.outputs.items()}


@dataclass
class Attempt:
    """The record of one attempt. status stays None until it ends.

    kept_visit is the index into visits of the marked visit the attempt kept, if any.
    """

    root: Path
    status: str | None = None
    reason: str | None = None
    kept_visit: int | None = None
    visits: list[Visit] = field(default_factory=list[Visit])

    @property
    def final(self) -> dict[str, Visit]:
        """The last visit of each state, counting back from the kept visit or the end."""
        end = len(self.visits) if self.kept_visit is None else self.kept_visit + 1
        return {visit.state: visit for visit in self.visits[:end]}

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "kept_visit": self.kept_visit,
            "final": {state: self._visit_json(visit) for state, visit in self.final.items()},
            "visits": [self._visit_json(visit) for visit in self.visits],
        }

    def _visit_json(self, visit: Visit) -> dict[str, object]:
        return {
            "state": visit.state,
            "visit": visit.visit,
            "outcome": visit.outcome,
            "data": dict(visit.data),
            "mark": visit.mark,
            "actions": [
                {
                    "kind": action.kind,
                    "status": action.status,
                    "outputs": {
                        name: str(path.relative_to(self.root))
                        for name, path in action.outputs.items()
                    },
                    "started_at": action.started_at.isoformat(),
                    "ended_at": action.ended_at.isoformat(),
                    "cost_usd": action.cost_usd,
                }
                for action in visit.actions
            ],
        }


type Guard = Callable[[Context, State], str | None]


class Context:
    """What a state's body sees: where it is, the report it got and the artifacts so far.

    Actions record themselves through claim, resolve and record; they may run concurrently.
    """

    def __init__(
        self, attempt: Attempt, artifacts: Artifacts, record: Visit, report: Report | None
    ) -> None:
        self._attempt: Attempt = attempt
        self._artifacts: Artifacts = artifacts
        self._record: Visit = record
        self._report: Report | None = report
        self._lock: threading.Lock = threading.Lock()
        self._claimed: set[str] = set()
        self._actions: int = 0

    @property
    def state(self) -> str:
        return self._record.state

    @property
    def visit(self) -> int:
        """How many times the state has been entered, this time included."""
        return self._record.visit

    @property
    def report(self) -> Report | None:
        return self._report

    @property
    def artifacts(self) -> Artifacts:
        return self._artifacts

    @property
    def attempt(self) -> Attempt:
        return self._attempt

    @property
    def outdir(self) -> Path:
        """Where this visit's actions write their outputs, one name per output."""
        return self._attempt.root / "artifacts" / self.state / str(self.visit)

    def claim(self, outputs: Sequence[str]) -> int:
        """Reserve output names for an action, create outdir, and number the action from 1.

        A name claimed twice in one visit is a ValueError.
        """
        with self._lock:
            if self._claimed.intersection(outputs) or len(set(outputs)) != len(outputs):
                raise ValueError(f"{self.state} visit {self.visit}: an output is named twice")
            self._claimed.update(outputs)
            self._actions += 1
            index = self._actions
        self.outdir.mkdir(parents=True, exist_ok=True)
        return index

    def resolve(self, inputs: Sequence[Input]) -> dict[str, Path]:
        """Input paths by name: an artifact's latest version, or a path by its file name."""
        return {
            item.name if isinstance(item, Path) else item: (
                item if isinstance(item, Path) else self._artifacts.get(item)
            )
            for item in inputs
        }

    def record(self, action: ActionRecord) -> None:
        """Add an action to this visit and its outputs to the artifacts."""
        with self._lock:
            self._record.actions.append(action)
        for name, path in action.outputs.items():
            self._artifacts._add(name, Version(self.state, self.visit, path))  # pyright: ignore[reportPrivateUsage]


def run_attempt(
    states: Sequence[State],
    root: Path,
    *,
    guard: Guard | None = None,
    on_step: Callable[[Attempt], None] | None = None,
) -> Attempt:
    """Run one attempt from the first state until an End, a cap or the guard ends it.

    root is the attempt's directory; outputs go to root/artifacts/<state>/<visit>/<name>.
    guard runs before each state is entered; a reason it returns ends the attempt. on_step
    gets the attempt after each visit and when it ends. A body's exception is recorded and
    raised again unchanged. Several attempts may run at once, each with its own root.
    """
    by_name = _check_states(states)
    attempt = Attempt(root)
    artifacts = Artifacts()

    def step() -> None:
        if on_step is not None:
            on_step(attempt)

    visits: Counter[str] = Counter()
    closed: set[str] = set()
    state, report = states[0], None
    while True:
        visit = visits[state.name] + 1
        if visit > state.max_visits or state.name in closed:
            why = "after its last visit" if state.name in closed else "over max_visits"
            _stop(attempt, state.on_exhausted, f"{state.name} visit {visit}: entered {why}")
            step()
            return attempt
        record = Visit(state.name, visit)
        ctx = Context(attempt, artifacts, record, report)
        if guard is not None and (why := guard(ctx, state)) is not None:
            _stop(attempt, "budget", f"{state.name} visit {visit}: {why}")
            step()
            return attempt
        visits[state.name] = visit
        attempt.visits.append(record)
        try:
            outcome = state.body(ctx)
        except BaseException as error:
            record.outcome = "raised"
            record.data = {"error": f"{type(error).__name__}: {error}"}
            step()
            raise
        record.outcome, record.data = outcome.name, outcome.data
        if outcome.last:
            closed.add(state.name)
        transition = state.transitions.get(outcome.name)
        if transition is None:
            raise ValueError(f"{state.name} has no transition for outcome {outcome.name!r}")
        if isinstance(transition, End):
            attempt.status = transition.status
            step()
            return attempt
        record.mark = transition.mark
        report = None
        if transition.report:
            report = Report(state.name, outcome.name, outcome.data, record.outputs)
        state = by_name[transition.state]
        step()


def _check_states(states: Sequence[State]) -> dict[str, State]:
    by_name = {state.name: state for state in states}
    if not states or len(by_name) != len(states):
        raise ValueError("states must be a non-empty sequence with unique names")
    for state in states:
        if state.max_visits < 1:
            raise ValueError(f"{state.name}: max_visits must be at least 1")
        for outcome, transition in state.transitions.items():
            if isinstance(transition, Goto) and transition.state not in by_name:
                raise ValueError(f"{state.name}: {outcome} goes to unknown {transition.state}")
    return by_name


def _stop(attempt: Attempt, status: str, reason: str) -> None:
    marked = [i for i, visit in enumerate(attempt.visits) if visit.mark is not None]
    if marked:
        attempt.kept_visit = marked[-1]
        attempt.status = attempt.visits[marked[-1]].mark
    else:
        attempt.status = status
    attempt.reason = reason
