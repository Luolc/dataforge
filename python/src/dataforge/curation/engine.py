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
from datetime import datetime, UTC
from pathlib import Path
import string
import threading

from dataforge.curation.deliverables import check, Deliverable, Source
from dataforge.curation.graph import Graph, GraphEdge, GraphState

type Data = Mapping[str, object]
"""Data a state or an action hands on; the dataset keeps it serializable as
JSON."""

type Input = str | Path
"""An action input: a str names an artifact (its latest version), or else a
path inside one as "<artifact>/<path>", either one prefixed with "<state>:"
for the latest version a visit of that state wrote; a Path is used as is."""


@dataclass(frozen=True)
class Outcome:
  """What a state's run returns.

  Attributes:
    name: The outcome's name, which the state's transitions map.
    data: Data the state hands on.
    last: With True, the state is never entered again.
    feedback: The variables of the state's feedback template; with None, the
      visit writes no feedback.
  """

  name: str
  data: Data = field(default_factory=dict[str, object])
  last: bool = False
  feedback: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Goto:
  """Go to a state.

  Attributes:
    state: The name of the state to go to.
    report: With True, the state gets a Report of this visit as ctx.report.
    mark: A mark (for example "too_easy") keeps this visit: when the attempt
      later ends at a cap or a guard, the last marked visit is kept and the
      mark becomes the attempt's status.
    end_on_last_visit: With True, a visit after which the state cannot be
      entered again (its max_visits-th, or one whose outcome has last=True)
      ends the attempt instead of going to the state, as entering the state
      once too often would; the mark is recorded first.
  """

  state: str
  report: bool = True
  mark: str | None = None
  end_on_last_visit: bool = False


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
    *,
    feedback: str | None = None,
    max_cost_usd: float = 0.0,
  ) -> None:
    """Make a state.

    Args:
      name: The state's name, unique among a machine's states.
      transitions: Where each outcome leads.
      max_visits: How many times the state may be entered.
      on_exhausted: The attempt's status when it is entered once too often
        and no visit was marked.
      feedback: A string.Template the machine renders into the visit's
        feedback.md when the visit goes on through a Goto with report=True
        and its outcome gives feedback; it sees ${state}, ${visit} and
        ${round} besides the outcome's variables, which those three override.
      max_cost_usd: The most one visit may spend, in US dollars, which the
        machine's cost cap counts before the state is entered.
    """
    self.name: str = name
    self.transitions: Mapping[str, Goto | End] = transitions
    self.max_visits: int = max_visits
    self.on_exhausted: str = on_exhausted
    self.feedback: str | None = feedback
    self.max_cost_usd: float = max_cost_usd

  def possible_outcomes(self) -> frozenset[str] | None:
    """Name the outcomes this state's run may give, if it can say.

    Returns:
      The outcomes' names, or None when the state does not say, as here.
    """
    return None

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
    summary: The Markdown summary a script or a judge wrote to
      DATAFORGE_SUMMARY, if it wrote one.
  """

  kind: str
  status: str
  outputs: dict[str, Path]
  started_at: datetime
  ended_at: datetime
  cost_usd: float
  summary: str | None = None


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
    round: How many times the attempt's first state had been entered when
      this visit began, this visit included.
  """

  state: str
  visit: int
  outcome: str | None = None
  data: Data = field(default_factory=dict[str, object])
  mark: str | None = None
  actions: list[ActionRecord] = field(default_factory=list[ActionRecord])
  round: int = 1

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
    max_cost_usd: What the attempt may spend, in US dollars; None for no cap.
    stopped: The reason, when the cost cap ended the attempt.
    graph: The machine's graph when the attempt began.
    deliverables: The machine's deliverables as the attempt's end resolved
      them, one entry each, in their order. Empty until it ends, and when a
      state's run raised.
  """

  root: Path
  status: str | None = None
  reason: str | None = None
  kept_visit: int | None = None
  visits: list[Visit] = field(default_factory=list[Visit])
  max_cost_usd: float | None = None
  stopped: str | None = None
  graph: Graph | None = None
  deliverables: list[dict[str, object]] = field(
    default_factory=list[dict[str, object]]
  )

  @property
  def cost_usd(self) -> float:
    """What the recorded actions cost, in US dollars."""
    return sum(
      (action.cost_usd for visit in self.visits for action in visit.actions),
      0.0,
    )

  @property
  def round(self) -> int:
    """The round of the last visit; 0 before the first."""
    return self.visits[-1].round if self.visits else 0

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
      "round": self.round,
      "budget": {
        "max_usd": self.max_cost_usd,
        "cost_usd": self.cost_usd,
        "stopped": self.stopped,
      },
      "final": {
        state: self._visit_json(visit) for state, visit in self.final.items()
      },
      "visits": [self._visit_json(visit) for visit in self.visits],
      "deliverables": self.deliverables,
      "graph": self.graph,
    }

  def _visit_json(self, visit: Visit) -> dict[str, object]:
    return {
      "state": visit.state,
      "visit": visit.visit,
      "round": visit.round,
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
          "summary": action.summary,
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
  def round(self) -> int:
    """How many times the attempt's first state has been entered."""
    return self._record.round

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

  def resolve(
    self, inputs: Sequence[Input], optional: Sequence[Input] = ()
  ) -> dict[str, Path]:
    """Return input paths by name.

    Args:
      inputs: The inputs of an action.
      optional: More inputs, each left out when its artifact has no version
        or its path does not exist.

    Returns:
      For a name, the artifact's latest version, or when no artifact has the
      name and it is "[<state>:]<artifact>[/<path>]", that path in the latest
      version (of that state's visits), by the str; for a path, the path by
      its file name. An artifact of inputs with no such version raises
      KeyError.
    """
    paths = {_name(item): self._path(item) for item in inputs}
    for item in optional:
      try:
        path = self._path(item)
      except KeyError:
        continue
      if path.exists():
        paths[_name(item)] = path
    return paths

  def _path(self, item: Input) -> Path:
    if isinstance(item, Path):
      return item
    if self._artifacts.history(item):
      return self._artifacts.get(item)
    state, colon, rest = item.partition(":")
    artifact, _, inside = (rest if colon else item).partition("/")
    versions = [
      version
      for version in self._artifacts.history(artifact)
      if not colon or version.state == state
    ]
    if not versions:
      raise KeyError(item)
    path = versions[-1].path
    return path / inside if inside else path

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

  Before each state is entered, the cost cap and then guard may block it,
  which ends the attempt. on_step gets the attempt after each visit and when it
  ends. A subclass may override either method instead of passing it. One
  machine may run several attempts at once, each with its own root.
  """

  def __init__(
    self,
    states: Sequence[State],
    *,
    guard: Guard | None = None,
    on_step: Callable[[Attempt], None] | None = None,
    max_cost_usd: float | None = None,
    deliverables: Mapping[str, Deliverable] | None = None,
  ) -> None:
    """Make a machine.

    Args:
      states: The states; the first is the start.
      guard: The guard, if any.
      on_step: Gets the attempt after each visit and when it ends, if given.
      max_cost_usd: What one attempt may spend, in US dollars; None for no
        cap. A state is not entered when what the attempt's actions cost so
        far and the state's max_cost_usd together would pass it.
      deliverables: What an attempt delivers, by name, in the order its
        record lists them; each attempt's end resolves them.

    Raises:
      ValueError: When a deliverable is not well formed or names an unknown
        state.
    """  # noqa: DOC502
    self.states: list[State] = list(states)
    self._by_name: dict[str, State] = _check_states(self.states)
    self.deliverables: dict[str, Deliverable] = dict(deliverables or {})
    check(self.deliverables, set(self._by_name))
    self._guard: Guard | None = guard
    self._on_step: Callable[[Attempt], None] | None = on_step
    self.max_cost_usd: float | None = max_cost_usd

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

  def graph(self) -> Graph:
    """Return the states and their transitions as data.

    It reads only each state's name, class, caps and transitions: it runs
    nothing. The result is plain data that json.dumps takes as is, and that
    dataforge.curation.mermaid renders.

    Returns:
      The graph; see Graph for its fields.
    """
    states: list[GraphState] = [
      {
        "name": state.name,
        "kind": type(state).__name__,
        "max_visits": state.max_visits,
        "on_exhausted": state.on_exhausted,
        "max_cost_usd": state.max_cost_usd,
      }
      for state in self.states
    ]
    edges = [
      _edge(state.name, outcome, transition)
      for state in self.states
      for outcome, transition in state.transitions.items()
    ]
    return {"start": self.states[0].name, "states": states, "edges": edges}

  def run(self, root: Path) -> Attempt:
    """Run one attempt until an End, a cap or the guard ends it.

    An exception from a state's run is recorded and raised again unchanged.
    A ValueError says that a state has no transition for its outcome, or that
    its feedback template needs a variable the outcome does not give.

    Args:
      root: The attempt's directory; outputs go to
        root/artifacts/<state>/<visit>/<name>.

    Returns:
      The attempt's record.

    Raises:
      BaseException: Whatever a state's run raised, unchanged.
    """
    attempt = Attempt(root, max_cost_usd=self.max_cost_usd, graph=self.graph())
    artifacts = Artifacts()
    visits: Counter[str] = Counter()
    closed: set[str] = set()
    first = self.states[0].name
    state, report = self.states[0], None
    while True:
      visit = visits[state.name] + 1
      round_ = visit if state.name == first else visits[first]
      record = Visit(state.name, visit, round=round_)
      ctx = Context(attempt, artifacts, record, report)
      if (blocked := self._blocked(ctx, state, closed)) is not None:
        _stop(attempt, *blocked)
        self._resolve(attempt)
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
      step = self._go(ctx, record, state, outcome, state.name in closed)
      if step is None:
        self._resolve(attempt)
      self.on_step(attempt)
      if step is None:
        return attempt
      state, report = step

  def _resolve(self, attempt: Attempt) -> None:
    attempt.deliverables = [
      _entry(name, item, attempt) for name, item in self.deliverables.items()
    ]

  def _blocked(
    self, ctx: Context, state: State, closed: set[str]
  ) -> tuple[str, str] | None:
    where = f"{state.name} visit {ctx.visit}"
    if ctx.visit > state.max_visits or state.name in closed:
      why = (
        "after its last visit" if state.name in closed else "over max_visits"
      )
      return state.on_exhausted, f"{where}: entered {why}"
    attempt, cap = ctx.attempt, self.max_cost_usd
    if cap is not None and attempt.cost_usd + state.max_cost_usd > cap:
      attempt.stopped = (
        f"{where}: ${attempt.cost_usd:.2f} spent and up to"
        f" ${state.max_cost_usd:.2f} more would pass the ${cap:.2f} the"
        " attempt may spend"
      )
      return "budget", attempt.stopped
    if (why := self.guard(ctx, state)) is not None:
      return "budget", f"{where}: {why}"
    return None

  def _go(
    self,
    ctx: Context,
    record: Visit,
    state: State,
    outcome: Outcome,
    closed: bool,
  ) -> tuple[State, Report | None] | None:
    """Take the transition of a visit's outcome.

    Args:
      ctx: The visit's context.
      record: The visit's record.
      state: The state.
      outcome: The visit's outcome.
      closed: Whether the state may not be entered again.

    Returns:
      The next state and its report, or None when the attempt ended.

    Raises:
      ValueError: When the state has no transition for the outcome, or its
        feedback template needs a variable the outcome does not give.
    """
    attempt = ctx.attempt
    transition = state.transitions.get(outcome.name)
    if transition is None:
      raise ValueError(
        f"{state.name} has no transition for outcome {outcome.name!r}"
      )
    if isinstance(transition, End):
      attempt.status = transition.status
      return None
    record.mark = transition.mark
    last = closed or ctx.visit == state.max_visits
    if transition.end_on_last_visit and last:
      why = f"its last visit, not going to {transition.state}"
      _stop(
        attempt, state.on_exhausted, f"{state.name} visit {ctx.visit}: {why}"
      )
      return None
    report = None
    if transition.report:
      _feedback(ctx, state, outcome)
      report = Report(state.name, outcome.name, outcome.data, record.outputs)
    return self._by_name[transition.state], report


def _edge(state: str, outcome: str, transition: Goto | End) -> GraphEdge:
  if isinstance(transition, End):
    return {
      "from": state,
      "outcome": outcome,
      "to": None,
      "end": transition.status,
      "report": False,
      "mark": None,
      "end_on_last_visit": False,
    }
  return {
    "from": state,
    "outcome": outcome,
    "to": transition.state,
    "end": None,
    "report": transition.report,
    "mark": transition.mark,
    "end_on_last_visit": transition.end_on_last_visit,
  }


def _entry(name: str, item: Deliverable, attempt: Attempt) -> dict[str, object]:
  entry: dict[str, object] = {"name": name, "kind": item.kind}
  entry["title"] = item.title
  entry.update(_source(item.source, attempt))
  if item.kind == "report":
    table = item.table
    entry["table"] = None if table is None else _source(table, attempt)
  return entry


def _source(source: Source, attempt: Attempt) -> dict[str, object]:
  found: dict[str, object] = {"state": source.state, "output": source.output}
  index, why = _deliverable_visit(source, attempt)
  if index is None:
    unreached = {"visit": None, "visit_index": None, "path": None}
    return found | unreached | {"reason": why}
  visit = attempt.visits[index]
  found |= {"visit": visit.visit, "visit_index": index}
  path = visit.outputs.get(source.output)
  if path is None:
    why = f"{source.state} visit {visit.visit} has no output {source.output}"
    return found | {"path": None, "reason": why}
  return found | {"path": str(path.relative_to(attempt.root)), "reason": None}


def _deliverable_visit(
  source: Source, attempt: Attempt
) -> tuple[int | None, str]:
  indices = [
    i for i, visit in enumerate(attempt.visits) if visit.state == source.state
  ]
  if not indices:
    return None, f"{source.state} was never visited"
  if source.visit == "last":
    return indices[-1], ""
  if source.visit == "kept":
    end = (
      len(attempt.visits) if attempt.kept_visit is None else attempt.kept_visit
    )
    kept = [i for i in indices if i <= end]
    if not kept:
      return None, f"{source.state} has no visit up to the kept one"
    return kept[-1], ""
  found = [i for i in indices if attempt.visits[i].visit == source.visit]
  if not found:
    return None, f"{source.state} has no visit {source.visit}"
  return found[0], ""


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


def _feedback(ctx: Context, state: State, outcome: Outcome) -> None:
  if state.feedback is None or outcome.feedback is None:
    return
  started = datetime.now(UTC)
  try:
    text = string.Template(state.feedback).substitute(
      outcome.feedback, state=ctx.state, visit=ctx.visit, round=ctx.round
    )
  except (KeyError, ValueError) as error:
    raise ValueError(
      f"{ctx.state} visit {ctx.visit}: its feedback template: {error!r}"
    ) from error
  _ = ctx.claim(["feedback"])
  path = ctx.outdir / "feedback.md"
  _ = path.write_text(text)
  ctx.record(
    ActionRecord(
      "report", "ok", {"feedback": path}, started, datetime.now(UTC), 0.0
    )
  )


def _name(item: Input) -> str:
  return item.name if isinstance(item, Path) else item
