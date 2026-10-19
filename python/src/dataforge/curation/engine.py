"""The engine: states, transitions, caps, artifacts and an attempt's record.

A dataset describes its process as states. Each state runs (usually through the
actions of dataforge.curation.actions, or as one of the states of
dataforge.curation.states) and returns an outcome; the state's transitions map
each outcome to the next state or to the end. The engine knows no state by name:
the states, their transitions and their caps all come from the dataset.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import threading

type Data = Mapping[str, object]
"""Data a state or an action hands on; the dataset keeps it serializable as
JSON."""

type Input = str | Path
"""An action input: a str names an artifact (its latest version), a Path is
used as is."""


@dataclass(frozen=True)
class Outcome:
  """What a state's run returns.

  Attributes:
    name: The outcome's name, which the state's transitions map.
    data: Data the state hands on.
    last: With True, the state is never entered again.
  """

  name: str
  data: Data = field(default_factory=dict[str, object])
  last: bool = False


@dataclass(frozen=True)
class Goto:
  """Go to a state.

  Attributes:
    state: The name of the state to go to.
    report: With True, the state gets a Report of this visit as ctx.report.
    mark: A mark (for example "too_easy") keeps this visit: when the attempt
      later ends at a cap or a guard, the last marked visit is kept and the
      mark becomes the attempt's status.
  """

  state: str
  report: bool = True
  mark: str | None = None


@dataclass(frozen=True)
class End:
  """End the attempt with the given status.

  Attributes:
    status: The attempt's status.
  """

  status: str


class State:
  """A state: what it does (run), where each outcome leads, how often it runs.

  Subclass it and implement run. Entering a state once more than max_visits
  times, or after it returned last=True, ends the attempt: with the last mark if
  there was one, else with the status on_exhausted.
  """

  def __init__(
    self,
    name: str,
    transitions: Mapping[str, Goto | End],
    max_visits: int = 1,
    on_exhausted: str = "failed",
  ) -> None:
    """Make a state.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
    """
    self.name: str = name
    self.transitions: Mapping[str, Goto | End] = transitions
    self.max_visits: int = max_visits
    self.on_exhausted: str = on_exhausted

  def run(self, ctx: Context) -> Outcome:
    """Run one visit of the state; a subclass implements it.

    Args:
      ctx: What the visit sees.

    Returns:
      The visit's outcome.

    Raises:
      NotImplementedError: Always, here.
    """
    _ = ctx
    raise NotImplementedError


@dataclass(frozen=True)
class Report:
  """What a state that went to another one hands it.

  Attributes:
    from_state: The name of the state that went on.
    outcome: The name of its outcome.
    data: The outcome's data.
    artifacts: The outputs of its visit, by name.
  """

  from_state: str
  outcome: str
  data: Data
  artifacts: Mapping[str, Path]


@dataclass(frozen=True)
class Version:
  """One version of an artifact.

  Attributes:
    state: The state whose visit wrote it.
    visit: That visit's number.
    path: Where it is.
  """

  state: str
  visit: int
  path: Path


class Artifacts:
  """The outputs of an attempt's actions, by name, each with its versions.

  The versions of a name are in the order they were added.
  """

  def __init__(self) -> None:
    """Make an empty set of artifacts."""
    self._versions: dict[str, list[Version]] = {}
    self._lock: threading.Lock = threading.Lock()

  def get(self, name: str) -> Path:
    """Return the latest version of an artifact.

    Args:
      name: The artifact's name.

    Returns:
      The path of its latest version.

    Raises:
      KeyError: When it has no version.
    """
    versions = self.history(name)
    if not versions:
      raise KeyError(name)
    return versions[-1].path

  def history(self, name: str) -> list[Version]:
    """Return the versions of an artifact, oldest first.

    Args:
      name: The artifact's name.

    Returns:
      Its versions; empty when it has none.
    """
    with self._lock:
      return list(self._versions.get(name, ()))

  def _add(self, name: str, version: Version) -> None:
    with self._lock:
      self._versions.setdefault(name, []).append(version)


@dataclass
class ActionRecord:
  """The record of one action of a visit.

  Attributes:
    kind: What the action was, such as "agent", "script" or "trial".
    status: How it ended.
    outputs: Its outputs, by name.
    started_at: When it started.
    ended_at: When it ended.
    cost_usd: What it cost, in US dollars.
  """

  kind: str
  status: str
  outputs: dict[str, Path]
  started_at: datetime
  ended_at: datetime
  cost_usd: float


@dataclass
class Visit:
  """One entry into a state.

  Attributes:
    state: The state's name.
    visit: How many times the state has been entered, this time included.
    outcome: The name of the outcome; "raised" when the state's run raised,
      None while it runs.
    data: The outcome's data.
    mark: The mark of the transition taken, if any.
    actions: The actions the visit ran.
  """

  state: str
  visit: int
  outcome: str | None = None
  data: Data = field(default_factory=dict[str, object])
  mark: str | None = None
  actions: list[ActionRecord] = field(default_factory=list[ActionRecord])

  @property
  def outputs(self) -> dict[str, Path]:
    """The outputs of the visit's actions, by name."""
    return {
      name: path
      for action in self.actions
      for name, path in action.outputs.items()
    }


@dataclass
class Attempt:
  """The record of one attempt.

  Attributes:
    root: The attempt's directory.
    status: How the attempt ended; None until it ends.
    reason: Why it ended at a cap or a guard, if it did.
    kept_visit: The index into visits of the marked visit the attempt kept, if
      any.
    visits: Its visits, in order.
  """

  root: Path
  status: str | None = None
  reason: str | None = None
  kept_visit: int | None = None
  visits: list[Visit] = field(default_factory=list[Visit])

  @property
  def final(self) -> dict[str, Visit]:
    """The last visit of each state, up to the kept visit or the end."""
    end = len(self.visits) if self.kept_visit is None else self.kept_visit + 1
    return {visit.state: visit for visit in self.visits[:end]}

  def to_json(self) -> dict[str, object]:
    """Return the record as JSON data, with paths relative to root.

    Returns:
      The record.
    """
    return {
      "status": self.status,
      "reason": self.reason,
      "kept_visit": self.kept_visit,
      "final": {
        state: self._visit_json(visit) for state, visit in self.final.items()
      },
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
  """What a running state sees: where it is, its report and the artifacts.

  Actions record themselves through claim, resolve and record; they may run
  concurrently.
  """

  def __init__(
    self,
    attempt: Attempt,
    artifacts: Artifacts,
    record: Visit,
    report: Report | None,
  ) -> None:
    """Make the context of one visit.

    Args:
      attempt: The attempt the visit is part of.
      artifacts: The attempt's artifacts so far.
      record: The visit's record.
      report: The report the state got, if any.
    """
    self._attempt: Attempt = attempt
    self._artifacts: Artifacts = artifacts
    self._record: Visit = record
    self._report: Report | None = report
    self._lock: threading.Lock = threading.Lock()
    self._claimed: set[str] = set()
    self._actions: int = 0

  @property
  def state(self) -> str:
    """The name of the running state."""
    return self._record.state

  @property
  def visit(self) -> int:
    """How many times the state has been entered, this time included."""
    return self._record.visit

  @property
  def report(self) -> Report | None:
    """The report the state got, if any."""
    return self._report

  @property
  def artifacts(self) -> Artifacts:
    """The attempt's artifacts so far."""
    return self._artifacts

  @property
  def attempt(self) -> Attempt:
    """The record of the attempt."""
    return self._attempt

  @property
  def outdir(self) -> Path:
    """Where this visit's actions write their outputs, one name per output."""
    return self._attempt.root / "artifacts" / self.state / str(self.visit)

  def claim(self, outputs: Sequence[str]) -> int:
    """Reserve output names for an action, create outdir, number the action.

    Args:
      outputs: The names of the action's outputs.

    Returns:
      The action's number in the visit, from 1.

    Raises:
      ValueError: When a name is claimed twice in one visit.
    """
    with self._lock:
      if self._claimed.intersection(outputs) or len(set(outputs)) != len(
        outputs
      ):
        raise ValueError(
          f"{self.state} visit {self.visit}: an output is named twice"
        )
      self._claimed.update(outputs)
      self._actions += 1
      index = self._actions
    self.outdir.mkdir(parents=True, exist_ok=True)
    return index

  def resolve(self, inputs: Sequence[Input]) -> dict[str, Path]:
    """Return input paths by name.

    Args:
      inputs: The inputs of an action.

    Returns:
      For a name, the artifact's latest version; for a path, the path by its
      file name.
    """
    return {
      item.name if isinstance(item, Path) else item: (
        item if isinstance(item, Path) else self._artifacts.get(item)
      )
      for item in inputs
    }

  def record(self, action: ActionRecord) -> None:
    """Add an action to this visit and its outputs to the artifacts.

    Args:
      action: The action's record.
    """
    with self._lock:
      self._record.actions.append(action)
    for name, path in action.outputs.items():
      self._artifacts._add(name, Version(self.state, self.visit, path))  # pyright: ignore[reportPrivateUsage]


class Machine:
  """Runs attempts over a list of states; the first state is the start.

  guard runs before each state is entered; a reason it returns ends the attempt.
  on_step gets the attempt after each visit and when it ends. A subclass may
  override either method instead of passing it. One machine may run several
  attempts at once, each with its own root.
  """

  def __init__(
    self,
    states: Sequence[State],
    *,
    guard: Guard | None = None,
    on_step: Callable[[Attempt], None] | None = None,
  ) -> None:
    """Make a machine.

    Args:
      states: The states; the first is the start.
      guard: The guard, if any.
      on_step: Gets the attempt after each visit and when it ends, if given.
    """
    self.states: list[State] = list(states)
    self._by_name: dict[str, State] = _check_states(self.states)
    self._guard: Guard | None = guard
    self._on_step: Callable[[Attempt], None] | None = on_step

  def guard(self, ctx: Context, state: State) -> str | None:
    """Check a state before it is entered.

    Args:
      ctx: The context the visit would have.
      state: The state.

    Returns:
      A reason that ends the attempt, or None to enter the state.
    """
    return None if self._guard is None else self._guard(ctx, state)

  def on_step(self, attempt: Attempt) -> None:
    """Get the attempt after each visit and when it ends.

    Args:
      attempt: The attempt.
    """
    if self._on_step is not None:
      self._on_step(attempt)

  def run(self, root: Path) -> Attempt:
    """Run one attempt until an End, a cap or the guard ends it.

    An exception from a state's run is recorded and raised again unchanged.

    Args:
      root: The attempt's directory; outputs go to
        root/artifacts/<state>/<visit>/<name>.

    Returns:
      The attempt's record.

    Raises:
      BaseException: Whatever a state's run raised, unchanged.
      ValueError: When a state has no transition for its outcome.
    """
    attempt = Attempt(root)
    artifacts = Artifacts()
    visits: Counter[str] = Counter()
    closed: set[str] = set()
    state, report = self.states[0], None
    while True:
      visit = visits[state.name] + 1
      if visit > state.max_visits or state.name in closed:
        why = (
          "after its last visit" if state.name in closed else "over max_visits"
        )
        _stop(
          attempt,
          state.on_exhausted,
          f"{state.name} visit {visit}: entered {why}",
        )
        self.on_step(attempt)
        return attempt
      record = Visit(state.name, visit)
      ctx = Context(attempt, artifacts, record, report)
      if (why := self.guard(ctx, state)) is not None:
        _stop(attempt, "budget", f"{state.name} visit {visit}: {why}")
        self.on_step(attempt)
        return attempt
      visits[state.name] = visit
      attempt.visits.append(record)
      try:
        outcome = state.run(ctx)
      except BaseException as error:
        record.outcome = "raised"
        record.data = {"error": f"{type(error).__name__}: {error}"}
        self.on_step(attempt)
        raise
      record.outcome, record.data = outcome.name, outcome.data
      if outcome.last:
        closed.add(state.name)
      transition = state.transitions.get(outcome.name)
      if transition is None:
        raise ValueError(
          f"{state.name} has no transition for outcome {outcome.name!r}"
        )
      if isinstance(transition, End):
        attempt.status = transition.status
        self.on_step(attempt)
        return attempt
      record.mark = transition.mark
      report = None
      if transition.report:
        report = Report(state.name, outcome.name, outcome.data, record.outputs)
      state = self._by_name[transition.state]
      self.on_step(attempt)


def _check_states(states: Sequence[State]) -> dict[str, State]:
  by_name = {state.name: state for state in states}
  if not states or len(by_name) != len(states):
    raise ValueError("states must be a non-empty sequence with unique names")
  for state in states:
    if state.max_visits < 1:
      raise ValueError(f"{state.name}: max_visits must be at least 1")
    for outcome, transition in state.transitions.items():
      if isinstance(transition, Goto) and transition.state not in by_name:
        raise ValueError(
          f"{state.name}: {outcome} goes to unknown {transition.state}"
        )
  return by_name


def _stop(attempt: Attempt, status: str, reason: str) -> None:
  marked = [
    i for i, visit in enumerate(attempt.visits) if visit.mark is not None
  ]
  if marked:
    attempt.kept_visit = marked[-1]
    attempt.status = attempt.visits[marked[-1]].mark
  else:
    attempt.status = status
  attempt.reason = reason
