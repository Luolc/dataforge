"""The engine: states, transitions, caps, artifacts and an attempt's record.

A dataset describes its process as states. Each state runs (usually through the
actions of dataforge.curation.actions, or as one of the states of
dataforge.curation.states) and returns an outcome; the state's transitions map
each outcome to the next state or to the end. The engine knows no state by name:
the states, their transitions and their caps all come from the dataset.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, UTC
import fnmatch
import json
import os
from pathlib import Path
import re
import stat
import string
import threading
from typing import cast

from dataforge import stopping
from dataforge.curation.deliverables import check as check_deliverables
from dataforge.curation.deliverables import Deliverable, Source, Value
from dataforge.curation.graph import Graph, GraphEdge, GraphState

type Data = Mapping[str, object]
"""Data a state or an action hands on; the dataset keeps it serializable as
JSON."""

type Input = str | Path
"""An action input: a str names an artifact (its latest version), or else a
path inside one as "<artifact>/<path>", either one prefixed with "<state>:"
for the latest version a visit of that state wrote; a Path is used as is."""


RAISED = "raised"
"""The outcome of a visit whose state's run raised an exception."""

INTERRUPTED = "interrupted"
"""The outcome of a visit whose state's run an interrupt ended: a SystemExit,
such as a caller makes of SIGTERM, a KeyboardInterrupt or stopping.Stopped."""

ENDING = (RAISED, INTERRUPTED)
"""The outcomes of a visit that end its attempt without a transition."""

BY_VISIT = ("artifacts", "logs", "records", "summaries", "validators")
"""The directories under an attempt's root that keep a visit's files, each
under <state>/<visit>, but records, which keeps <state>/<visit>.json: its
outputs, the actions' logs (actions.log_path), the record a judge read, the
summaries (actions.summary_path) and the validators an agent got."""


class RecordError(ValueError):
  """A record that Attempt.from_json cannot read back."""


class ResumeError(Exception):
  """An attempt that Machine.resume cannot go on with."""


@dataclass(frozen=True)
class Outcome:
  """What a state's run returns.

  Attributes:
    name: The outcome's name, which the state's transitions map.
    data: Data the state hands on.
    last: With True, the state is never entered again.
    feedback: The variables of the state's feedback template; with None, the
      visit writes no feedback.
    reason: Why the attempt ends, when the outcome's transition is an End:
      the attempt's reason becomes "<state> visit <n>: <reason>".
    fault: Why the visit gave nothing to judge, one of the FAULTS words of
      dataforge.curation.actions, such as its agent run's, or None. When the
      transition is an End, "budget", or any fault once the attempt has
      spent all it may, ends the attempt as the cost cap does instead.
  """

  name: str
  data: Data = field(default_factory=dict[str, object])
  last: bool = False
  feedback: Mapping[str, str] | None = None
  reason: str | None = None
  fault: str | None = None


@dataclass(frozen=True)
class Goto:
  """Go to a state.

  Attributes:
    state: The name of the state to go to.
    report: With True, the state gets a Report of this visit as ctx.report.
    mark: A mark (for example "too_easy") keeps this visit: when the attempt
      later ends at a cap or a guard, the last marked visit is kept and the
      mark becomes the attempt's status.
    end_on_last_visit: With True, a visit after which its state cannot be
      entered again ends the attempt instead of going to the state named
      here, as entering a state once too often would; the mark is recorded
      first. The state cannot be entered again after its max_visits-th visit,
      after an outcome with last=True, or when every way back to it from the
      state named here passes a state that cannot be entered again.
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
    keep_mark: A mark (Goto.mark) whose last visit the attempt keeps, as an
      attempt that ends at a cap keeps its last marked visit, but with this
      End's status; None, or no visit with this mark, keeps none.
  """

  status: str
  keep_mark: str | None = None


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

  def runners(self) -> Sequence[object]:
    """Name the runners whose sessions this state's actions record.

    Machine.resume gives each runner that has restore(points) the session of
    every action of the record's visits of the states that name it.

    Returns:
      The runners; none here.
    """
    return ()

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


@dataclass(frozen=True)
class TrialRecord:
  """What a trial action records about its trial.

  Attributes:
    id: The trial's name, as its runner gives it, else its output's name.
    path: The trial's directory.
    trace: The agent's trace, if the runner gives one.
    format: The trace's format, such as "claude-code-stream-json".
    rewards: The verifier's rewards, by name.
    exception: What ended the trial early, if anything did.
    source: The path of the trial whose output this trial judged again, such
      as one whose diff it applied, if its task names one.
  """

  id: str
  path: Path
  trace: Path | None
  format: str | None
  rewards: Mapping[str, float]
  exception: str | None
  source: Path | None = None


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
    trial: For a trial, what it recorded about it.
    session: For an agent, what its runner's checkpoint gave, if the runner
      has checkpoint; Machine.resume hands it back to the runner's restore.
    trace: For an agent, the trace of its session's first run, if the runner
      gives one inside the attempt's directory.
    trace_format: The trace's format, such as "claude-code-stream-json".
    cost_known: Whether cost_usd is all it cost: False when a run of an
      agent did not report its cost, so that cost_usd counts only the runs
      that did. A record written before it existed reads as True.
    logs: What it printed besides its outputs and trace, as files in the
      attempt's directory, in order: for an agent, each run's log its runner
      keeps there (AgentRun.log), then the engine's; for a script or a judge,
      the engine's, its stdout and stderr (actions.log_path). Empty for a
      trial and in a record written before it existed.
  """

  kind: str
  status: str
  outputs: dict[str, Path]
  started_at: datetime
  ended_at: datetime
  cost_usd: float
  summary: str | None = None
  trial: TrialRecord | None = None
  session: Data | None = None
  trace: Path | None = None
  trace_format: str | None = None
  cost_known: bool = True
  logs: list[Path] = field(default_factory=list[Path])


@dataclass
class Visit:
  """One entry into a state.

  Attributes:
    state: The state's name.
    visit: How many times the state has been entered, this time included.
    outcome: The name of the outcome; RAISED when the state's run raised,
      INTERRUPTED when an interrupt ended it, None while it runs.
    data: The outcome's data.
    mark: The mark of the transition taken, if any.
    actions: The actions the visit ran.
    round: How many times the attempt's first state had been entered when
      this visit began, this visit included.
    last: Whether the outcome had last=True.
    running: The outputs of its trials that have started and are not recorded
      yet; not part of the record's visit.
  """

  state: str
  visit: int
  outcome: str | None = None
  data: Data = field(default_factory=dict[str, object])
  mark: str | None = None
  actions: list[ActionRecord] = field(default_factory=list[ActionRecord])
  round: int = 1
  last: bool = False
  running: list[str] = field(default_factory=list[str])

  @property
  def outputs(self) -> dict[str, Path]:
    """The outputs of the visit's actions, by name."""
    return {
      name: path
      for action in self.actions
      for name, path in action.outputs.items()
    }


@dataclass(frozen=True)
class Resumption:
  """One time Machine.resume went on with an attempt.

  Attributes:
    at: When it went on.
    kept: How many visits of the record it kept: the first kept visits of the
      attempt's visits came before it.
    discarded: The visits it left out, the visits that had not ended, each as
      the record had it, with "path", where the caller keeps their outputs
      relative to the attempt's directory, and "reason", why path is None.
    carried_usd: What the attempt spent that no kept visit's actions record,
      in US dollars, as the caller gave it: what the default measure of
      spend (recorded_usd) adds to the actions, kept only as a record when
      the machine has spent_usd.
    reason: Why the attempt had stopped, as the caller gave it.
  """

  at: datetime
  kept: int
  discarded: list[dict[str, object]]
  carried_usd: float
  reason: str | None = None


@dataclass
class Attempt:
  """The record of one attempt.

  Attributes:
    root: The attempt's directory.
    status: How the attempt ended; None until it ends.
    reason: Why it ended at a cap or a guard, if it did, or the reason of
      the outcome whose End ended it (Outcome.reason), if that gave one.
    kept_visit: The index into visits of the marked visit the attempt kept, if
      any: its last marked visit when a cap or the guard ended it, or its
      last visit with the mark of the End that ended it (End.keep_mark).
    visits: Its visits, in order.
    max_cost_usd: What the attempt may spend, in US dollars; None for no cap.
    cost_usd: What the attempt spent, in US dollars, as the machine last
      measured it (measure): what the cost cap checks and the record holds.
    stopped: The reason, when the cost cap ended the attempt.
    graph: The machine's graph when the attempt began.
    deliverables: The machine's deliverables, one entry each, in their order,
      as the attempt's end resolved them, or while it runs as it would end
      then. Empty before the first step, and when a state's run raised.
    resumptions: Each time Machine.resume went on with it, in order.
  """

  root: Path
  status: str | None = None
  reason: str | None = None
  kept_visit: int | None = None
  visits: list[Visit] = field(default_factory=list[Visit])
  max_cost_usd: float | None = None
  cost_usd: float = 0.0
  stopped: str | None = None
  graph: Graph | None = None
  deliverables: list[dict[str, object]] = field(
    default_factory=list[dict[str, object]]
  )
  resumptions: list[Resumption] = field(default_factory=list[Resumption])

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
      "resumptions": [
        {
          "at": resumption.at.isoformat(),
          "kept": resumption.kept,
          "discarded": resumption.discarded,
          "carried_usd": resumption.carried_usd,
          "reason": resumption.reason,
        }
        for resumption in self.resumptions
      ],
    }

  @classmethod
  def from_json(cls, root: Path, data: Mapping[str, object]) -> Attempt:
    """Read back a record that to_json wrote.

    Args:
      root: The attempt's directory, which the record's paths are relative to.
      data: The record.

    Returns:
      The attempt.

    Raises:
      RecordError: When a field is missing or has the wrong type, or a path
        is not inside root.
    """  # noqa: DOC502
    budget = _object(_get(data, "budget", "the record"), "budget")
    graph = _get(data, "graph", "the record")
    if graph is not None:
      graph = _object(graph, "graph")
    return cls(
      root,
      status=_maybe_text(data, "status", "the record"),
      reason=_maybe_text(data, "reason", "the record"),
      kept_visit=_maybe_integer(data, "kept_visit", "the record"),
      visits=[
        _read_visit(root, _object(item, f"visits[{i}]"), f"visits[{i}]")
        for i, item in enumerate(_list(data, "visits", "the record"))
      ],
      max_cost_usd=_maybe_number(budget, "max_usd", "budget"),
      cost_usd=_number(budget, "cost_usd", "budget"),
      stopped=_maybe_text(budget, "stopped", "budget"),
      # Machine.resume compares it with its own graph before it uses it.
      graph=cast(Graph | None, graph),
      deliverables=[
        _object(item, f"deliverables[{i}]")
        for i, item in enumerate(_list(data, "deliverables", "the record"))
      ],
      resumptions=[
        _read_resumption(
          _object(item, f"resumptions[{i}]"), f"resumptions[{i}]"
        )
        for i, item in enumerate(_list(data, "resumptions", "the record"))
      ],
    )

  def _visit_json(self, visit: Visit) -> dict[str, object]:
    return {
      "state": visit.state,
      "visit": visit.visit,
      "round": visit.round,
      "outcome": visit.outcome,
      "data": dict(visit.data),
      "mark": visit.mark,
      "last": visit.last,
      "actions": [self._action_json(action) for action in visit.actions],
    }

  def _action_json(self, action: ActionRecord) -> dict[str, object]:
    trial = action.trial
    return {
      "kind": action.kind,
      "status": action.status,
      "outputs": {
        name: str(path.relative_to(self.root))
        for name, path in action.outputs.items()
      },
      "started_at": action.started_at.isoformat(),
      "ended_at": action.ended_at.isoformat(),
      "cost_usd": action.cost_usd,
      "cost_known": action.cost_known,
      "summary": action.summary,
      "logs": [str(path.relative_to(self.root)) for path in action.logs],
      "trace": _trace_json(action.trace, action.trace_format, self.root),
      "session": None if action.session is None else dict(action.session),
      "trial": None
      if trial is None
      else {
        "id": trial.id,
        "path": str(trial.path.relative_to(self.root)),
        "trace": _trace_json(trial.trace, trial.format, self.root),
        "rewards": dict(trial.rewards),
        "exception": trial.exception,
        "source": _relative(trial.source, self.root),
      },
    }


type Guard = Callable[[Context, State], str | None]


def recorded_usd(attempt: Attempt) -> float:
  """Return what an attempt's recorded actions and resumptions cost.

  It is the measure of spend of a machine without spent_usd.

  Args:
    attempt: The attempt.

  Returns:
    The cost_usd of every action of its visits and the carried_usd of each
    resumption, together, in US dollars.
  """
  return sum(
    (a.cost_usd for visit in attempt.visits for a in visit.actions),
    sum((r.carried_usd for r in attempt.resumptions), 0.0),
  )


def measure(
  attempt: Attempt, spent_usd: Callable[[Attempt], float] | None = None
) -> None:
  """Set what an attempt spent, as a machine measures it.

  A caller that closes the record of an attempt whose process stopped
  measures it again this way, with the machine's spent_usd.

  Args:
    attempt: The attempt, whose cost_usd it sets.
    spent_usd: The machine's measure of spend; None for recorded_usd.
  """
  attempt.cost_usd = (spent_usd or recorded_usd)(attempt)


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
    *,
    changed: Callable[[], None] | None = None,
  ) -> None:
    """Make the context of one visit.

    Args:
      attempt: The attempt the visit is part of.
      artifacts: The attempt's artifacts so far.
      record: The visit's record.
      report: The report the state got, if any.
      changed: Called after each claim and each record, one call at a time,
        while no other action can change the visit, if given.
    """
    self._attempt: Attempt = attempt
    self._artifacts: Artifacts = artifacts
    self._record: Visit = record
    self._report: Report | None = report
    self._changed: Callable[[], None] | None = changed
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

  def claim(self, outputs: Sequence[str], *, trial: bool = False) -> int:
    """Reserve output names for an action, create outdir, number the action.

    Args:
      outputs: The names of the action's outputs.
      trial: Whether the action is a trial, which the visit then lists as
        running until it is recorded.

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
    with self._lock:
      if trial:
        self._record.running.extend(outputs)
      if self._changed is not None:
        self._changed()
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
      running = self._record.running
      running[:] = [name for name in running if name not in action.outputs]
      if self._changed is not None:
        self._changed()
    for name, path in action.outputs.items():
      self._artifacts._add(name, Version(self.state, self.visit, path))  # pyright: ignore[reportPrivateUsage]


class Machine:
  """Runs attempts over a list of states; the first state is the start.

  Before each state is entered, the cost cap and then guard may block it, which
  ends the attempt; so does a spend cap that cut a visit whose outcome would
  end it (Outcome.fault). on_step gets the attempt when a visit begins, after
  each claim and record of its actions, after the visit and when the attempt
  ends, one call at a time. A subclass may override either method instead of
  passing it. One machine may run several attempts at once, each with its own
  root.
  """

  def __init__(
    self,
    states: Sequence[State],
    *,
    guard: Guard | None = None,
    on_step: Callable[[Attempt], None] | None = None,
    max_cost_usd: float | None = None,
    deliverables: Mapping[str, Deliverable] | None = None,
    spent_usd: Callable[[Attempt], float] | None = None,
  ) -> None:
    """Make a machine.

    Args:
      states: The states; the first is the start.
      guard: The guard, if any.
      on_step: Gets the attempt at each step (see Machine), if given.
      max_cost_usd: What one attempt may spend, in US dollars; None for no
        cap. A state is not entered when what the attempt spent so far
        (Attempt.cost_usd) and the state's max_cost_usd together would pass
        it.
      deliverables: What an attempt delivers, by name, in the order its
        record lists them; each step resolves them as the attempt would end
        then, and its end as it ends.
      spent_usd: What an attempt has spent so far, in US dollars, such as
        the sum of the ledger its model proxies write; it must count every
        model call the attempt starts, since nothing else is added to it.
        Read before each state's cost cap and before each on_step. None
        for recorded_usd, what the recorded actions report.

    Raises:
      ValueError: When a deliverable is not well formed or names an unknown
        state.
    """  # noqa: DOC502
    self.states: list[State] = list(states)
    self._by_name: dict[str, State] = _check_states(self.states)
    self.deliverables: dict[str, Deliverable] = dict(deliverables or {})
    check_deliverables(self.deliverables, set(self._by_name))
    self._guard: Guard | None = guard
    self._on_step: Callable[[Attempt], None] | None = on_step
    self.max_cost_usd: float | None = max_cost_usd
    self.spent_usd: Callable[[Attempt], float] | None = spent_usd

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
    """Get the attempt at each step (see Machine).

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

  def deliverables_of(self, attempt: Attempt) -> list[dict[str, object]]:
    """Resolve this machine's deliverables in an attempt that has ended.

    The result is what the attempt's end would have recorded had the machine
    declared these deliverables then: [] when its last visit raised or was
    interrupted (ENDING). It reads
    the record and the outputs the record names, and runs nothing.

    Args:
      attempt: The attempt, as Attempt.from_json reads its record back.

    Returns:
      The deliverables, one entry each, in their order.

    Raises:
      ValueError: When the attempt's last visit has no outcome, as while it
        runs.
    """
    if attempt.visits:
      last = attempt.visits[-1].outcome
      if last is None:
        raise ValueError("its last visit has no outcome, so it has not ended")
      if last in ENDING:
        return []
    return [
      _entry(name, item, attempt) for name, item in self.deliverables.items()
    ]

  def run(self, root: Path) -> Attempt:
    """Run one attempt until an End, a cap or the guard ends it.

    An exception from a state's run is recorded, as INTERRUPTED for an
    interrupt and RAISED for any other, and raised again unchanged.
    A ValueError says that a state has no transition for its outcome, or that
    its feedback template needs a variable the outcome does not give.

    Args:
      root: The attempt's directory; outputs go to
        root/artifacts/<state>/<visit>/<name>.

    Returns:
      The attempt's record.

    Raises:
      BaseException: Whatever a state's run raised, unchanged.
    """  # noqa: DOC502
    attempt = Attempt(root, max_cost_usd=self.max_cost_usd, graph=self.graph())
    return self._loop(
      attempt, Artifacts(), Counter(), set(), self.states[0], None
    )

  def resume(
    self,
    record: Attempt,
    *,
    carried_usd: float = 0.0,
    discarded_path: str | None = None,
    reason: str | None = None,
  ) -> Attempt:
    """Go on with an attempt from its last whole visit, as run would have.

    It leaves out the visits at the record's end whose outcome is None or one
    of ENDING, rebuilds from the others how often each state was entered,
    which states are closed and the artifacts, and takes the last kept
    visit's transition again, without writing its feedback again: the record
    has it. With no visit kept, it starts at the first state. Then it runs on
    as run does, adding a Resumption to the attempt. Before that, each runner
    a state names (State.runners) that has restore(points) gets the session
    of each action of the record's visits of those states, the left-out
    visits included, in order: what a session did is done.

    The cap is this machine's max_cost_usd. The attempt's directory is the
    record's root; the caller moves the left-out visits' files, under each of
    BY_VISIT, out of the way if it keeps them, since the visits run again
    under the same numbers and write there again.

    Args:
      record: The attempt, as Attempt.from_json reads it back.
      carried_usd: What the attempt spent that no kept visit's actions record,
        in US dollars, such as what the left-out visits spent: added to them
        by recorded_usd, only recorded when the machine has spent_usd.
      discarded_path: Where the caller keeps the left-out visits' outputs,
        relative to the attempt's directory, if it does.
      reason: Why the attempt stopped, if the caller knows.

    Returns:
      The attempt's record.

    Raises:
      ResumeError: When the record's status is set, its graph is not this
        machine's, a visit names an unknown state or is numbered out of order,
        or the last kept visit has no transition to go on with: it has no
        transition for its outcome, its transition is an End, or its Goto
        with end_on_last_visit ended the attempt.
      BaseException: Whatever a state's run raised, unchanged.
    """  # noqa: DOC503
    if record.status is not None:
      raise ResumeError(f"the attempt has ended: {record.status}")
    if record.graph != self.graph():
      raise ResumeError("the record's graph is not this machine's")
    whole = len(record.visits)
    while whole and record.visits[whole - 1].outcome in (None, *ENDING):
      whole -= 1
    visits, closed, artifacts = self._rebuild(record.visits, whole)
    why = None if discarded_path is not None else "the caller kept no copy"
    resumption = Resumption(
      datetime.now(UTC),
      whole,
      [
        record._visit_json(visit) | {"path": discarded_path, "reason": why}  # pyright: ignore[reportPrivateUsage]
        for visit in record.visits[whole:]
      ],
      carried_usd,
      reason,
    )
    attempt = dataclasses.replace(
      record,
      visits=record.visits[:whole],
      max_cost_usd=self.max_cost_usd,
      resumptions=[*record.resumptions, resumption],
    )
    step: tuple[State, Report | None] = (self.states[0], None)
    if whole:
      step = self._again(attempt.visits[-1], visits, closed)
    self._restore(record.visits)
    return self._loop(attempt, artifacts, visits, closed, *step)

  def _rebuild(
    self, record: Sequence[Visit], whole: int
  ) -> tuple[Counter[str], set[str], Artifacts]:
    """Rebuild a loop's state from the visits a resume keeps.

    Args:
      record: The record's visits.
      whole: How many of them it keeps.

    Returns:
      How many times each state was entered, the closed states and the
      artifacts.

    Raises:
      ResumeError: When a visit names an unknown state, or a kept one is
        numbered out of order.
    """
    for visit in record:
      if visit.state not in self._by_name:
        raise ResumeError(f"{visit.state} visit {visit.visit}: unknown state")
    visits: Counter[str] = Counter()
    closed: set[str] = set()
    artifacts = Artifacts()
    for visit in record[:whole]:
      visits[visit.state] += 1
      if visit.visit != visits[visit.state]:
        why = f"is its visit {visits[visit.state]} in the record"
        raise ResumeError(f"{visit.state} visit {visit.visit} {why}")
      if visit.last:
        closed.add(visit.state)
      for action in visit.actions:
        for name, path in action.outputs.items():
          artifacts._add(name, Version(visit.state, visit.visit, path))  # pyright: ignore[reportPrivateUsage]
    return visits, closed, artifacts

  def _again(
    self, last: Visit, visits: Counter[str], closed: set[str]
  ) -> tuple[State, Report | None]:
    """Take the transition of the last kept visit again, writing nothing.

    Args:
      last: The visit.
      visits: How many times each state was entered, this visit included.
      closed: The states an outcome with last=True closed.

    Returns:
      The next state and its report.

    Raises:
      ResumeError: When the visit has no transition to go on with.
    """
    where = f"{last.state} visit {last.visit}"
    transition = self._by_name[last.state].transitions.get(str(last.outcome))
    if transition is None:
      raise ResumeError(f"{where}: no transition for {last.outcome!r}")
    if isinstance(transition, End):
      raise ResumeError(f"{where} ended the attempt with {transition.status}")
    if transition.end_on_last_visit and not self._reaches(
      transition.state, last.state, visits, closed
    ):
      raise ResumeError(f"{where} was its last visit and ended the attempt")
    report = None
    if transition.report:
      report = Report(last.state, str(last.outcome), last.data, last.outputs)
    return self._by_name[transition.state], report

  def _restore(self, visits: Sequence[Visit]) -> None:
    points: dict[int, tuple[object, list[Data]]] = {}
    for state in self.states:
      for runner in state.runners():
        _ = points.setdefault(id(runner), (runner, []))
    for visit in visits:
      for runner in self._by_name[visit.state].runners():
        points[id(runner)][1].extend(
          action.session
          for action in visit.actions
          if action.session is not None
        )
    for runner, sessions in points.values():
      restore = getattr(runner, "restore", None)
      if callable(restore):
        _ = restore(sessions)

  def _loop(
    self,
    attempt: Attempt,
    artifacts: Artifacts,
    visits: Counter[str],
    closed: set[str],
    state: State,
    report: Report | None,
  ) -> Attempt:
    first = self.states[0].name

    # A visit's actions call it one at a time (Context); the loop calls it
    # only while none runs.
    def step(*, resolve: bool = True) -> None:
      if resolve:
        self._resolve(attempt)
      measure(attempt, self.spent_usd)
      self.on_step(attempt)

    while True:
      visit = visits[state.name] + 1
      round_ = visit if state.name == first else visits[first]
      record = Visit(state.name, visit, round=round_)
      ctx = Context(attempt, artifacts, record, report, changed=step)
      if (blocked := self._blocked(ctx, state, closed)) is not None:
        _stop(attempt, *blocked)
        step()
        return attempt
      visits[state.name] = visit
      attempt.visits.append(record)
      try:
        step()
      except BaseException:
        _ = attempt.visits.pop()
        raise
      try:
        outcome = state.run(ctx)
      except BaseException as error:
        interrupted = isinstance(
          error, (SystemExit, KeyboardInterrupt, stopping.Stopped)
        )
        record.outcome = INTERRUPTED if interrupted else RAISED
        record.data = {"error": f"{type(error).__name__}: {error}"}
        attempt.deliverables = []
        step(resolve=False)
        raise
      record.outcome, record.data = outcome.name, outcome.data
      record.last = outcome.last
      if outcome.last:
        closed.add(state.name)
      next_ = self._go(ctx, record, state, outcome, visits, closed)
      step()
      if next_ is None:
        return attempt
      state, report = next_

  def _resolve(self, attempt: Attempt) -> None:
    # While a visit runs, deliverables are taken from the visits before it,
    # as an attempt that ended now would leave them, but trials, which list
    # those of the running visit that have started.
    running = bool(attempt.visits) and attempt.visits[-1].outcome is None
    whole = (
      dataclasses.replace(attempt, visits=attempt.visits[:-1])
      if running
      else attempt
    )
    attempt.deliverables = [
      _entry(name, item, attempt if item.kind == "trials" else whole)
      for name, item in self.deliverables.items()
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
    measure(attempt, self.spent_usd)
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

  def _capped(self, attempt: Attempt, outcome: Outcome) -> str | None:
    # Why a spend cap cut a visit that gave nothing to judge, if one did: a
    # cap of its own, or the attempt's, all of which it has spent; a model
    # proxy that caps the attempt's sessions refuses them then, whatever a
    # session makes of the refusal.
    if outcome.fault is None:
      return None
    said = outcome.reason or f"the fault {outcome.fault}"
    if outcome.fault == "budget":
      return said
    cap = self.max_cost_usd
    if cap is None:
      return None
    measure(attempt, self.spent_usd)
    if attempt.cost_usd < cap:
      return None
    return (
      f"${attempt.cost_usd:.2f} spent of the ${cap:.2f} the attempt may"
      f" spend; {said}"
    )

  def _reaches(
    self, start: str, goal: str, visits: Counter[str], closed: set[str]
  ) -> bool:
    """Whether goal can be entered again by going on from start.

    Args:
      start: The state the attempt would go to.
      goal: The state to enter again.
      visits: How many times each state was entered.
      closed: The states an outcome with last=True closed.

    Returns:
      True when some chain of Gotos leads from start to goal through states
      that can each be entered once more, start and goal included.
    """
    seen: set[str] = set()
    todo = [start]
    while todo:
      name = todo.pop()
      state = self._by_name[name]
      if name in seen or name in closed or visits[name] >= state.max_visits:
        continue
      if name == goal:
        return True
      seen.add(name)
      todo.extend(
        t.state for t in state.transitions.values() if isinstance(t, Goto)
      )
    return False

  def _go(
    self,
    ctx: Context,
    record: Visit,
    state: State,
    outcome: Outcome,
    visits: Counter[str],
    closed: set[str],
  ) -> tuple[State, Report | None] | None:
    """Take the transition of a visit's outcome.

    Args:
      ctx: The visit's context.
      record: The visit's record.
      state: The state.
      outcome: The visit's outcome.
      visits: How many times each state was entered, this visit included.
      closed: The states an outcome with last=True closed.

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
      where = f"{state.name} visit {ctx.visit}"
      if (why := self._capped(attempt, outcome)) is not None:
        _stop(attempt, "budget", f"{where}: {why}")
        return None
      attempt.status = transition.status
      if transition.keep_mark is not None:
        marked = [
          i
          for i, visit in enumerate(attempt.visits)
          if visit.mark == transition.keep_mark
        ]
        if marked:
          attempt.kept_visit = marked[-1]
      if outcome.reason is not None:
        attempt.reason = f"{where}: {outcome.reason}"
      return None
    record.mark = transition.mark
    if transition.end_on_last_visit and not self._reaches(
      transition.state, state.name, visits, closed
    ):
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
    edge: GraphEdge = {
      "from": state,
      "outcome": outcome,
      "to": None,
      "end": transition.status,
      "report": False,
      "mark": None,
      "end_on_last_visit": False,
    }
    if transition.keep_mark is not None:
      edge["keep_mark"] = transition.keep_mark
    return edge
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
  if item.kind == "trials":
    index = entry["visit_index"]
    visit = attempt.visits[index] if isinstance(index, int) else None
    trials = [] if visit is None else _trials(visit, attempt.root, item.match)
    entry["match"] = item.match
    entry["k"] = len(trials)
    entry["value"] = None if item.value is None else _value(item.value, visit)
    entry["trials"] = trials
  return entry


def _source(source: Source, attempt: Attempt) -> dict[str, object]:
  # A source of several states names the state of the visit it found, or
  # its first state when it found none.
  state = source.states[0]
  found: dict[str, object] = {"state": state, "output": source.output}
  index, why = _deliverable_visit(source, attempt)
  if index is None:
    unreached = {"visit": None, "visit_index": None, "path": None}
    return found | unreached | {"reason": why}
  visit = attempt.visits[index]
  found |= {"state": visit.state, "visit": visit.visit, "visit_index": index}
  if source.output is None:
    path = attempt.root / "artifacts" / visit.state / str(visit.visit)
    return found | {"path": str(path.relative_to(attempt.root)), "reason": None}
  path = visit.outputs.get(source.output)
  if path is None:
    why = f"{visit.state} visit {visit.visit} has no output {source.output}"
    return found | {"path": None, "reason": why}
  return found | {"path": str(path.relative_to(attempt.root)), "reason": None}


def _deliverable_visit(
  source: Source, attempt: Attempt
) -> tuple[int | None, str]:
  several = isinstance(source.state, tuple)
  named = " or ".join(source.states)
  indices = [
    i for i, visit in enumerate(attempt.visits) if visit.state in source.states
  ]
  if not indices:
    return None, f"{named} was never visited"
  if source.visit == "kept":
    end = (
      len(attempt.visits) if attempt.kept_visit is None else attempt.kept_visit
    )
    indices = [i for i in indices if i <= end]
    if not indices:
      return None, f"{named} has no visit up to the kept one"
  if several:
    output = cast(str, source.output)
    indices = [i for i in indices if output in attempt.visits[i].outputs]
    if not indices:
      upto = " up to the kept one" if source.visit == "kept" else ""
      return None, f"no visit of {named}{upto} has the output {output}"
  if source.visit in ("kept", "last"):
    return indices[-1], ""
  found = [i for i in indices if attempt.visits[i].visit == source.visit]
  if not found:
    return None, f"{source.state} has no visit {source.visit}"
  return found[0], ""


def _trials(
  visit: Visit, root: Path, match: str | None
) -> list[dict[str, object]]:
  def listed(output: str) -> bool:
    return match is None or fnmatch.fnmatchcase(output, match)

  trials: list[tuple[tuple[str, int], dict[str, object]]] = []
  for action in visit.actions:
    trial = action.trial
    if trial is None:
      continue
    output = next(iter(action.outputs))
    if not listed(output):
      continue
    trace = _trace_json(trial.trace, trial.format, root)
    trials.append(
      (
        _order(output),
        {
          "id": trial.id,
          "output": output,
          "path": str(trial.path.relative_to(root)),
          "trace": trace,
          "source": _relative(trial.source, root),
          "result": {
            "reward": trial.rewards.get("reward"),
            "rewards": dict(trial.rewards),
            "exception": trial.exception,
          },
        },
      )
    )
  for output in filter(listed, visit.running):
    path = root / "artifacts" / visit.state / str(visit.visit) / output
    trials.append(
      (
        _order(output),
        {
          "id": None,
          "output": output,
          "path": str(path.relative_to(root)),
          "trace": None,
          "source": None,
          "result": None,
        },
      )
    )
  return [entry for _, entry in sorted(trials, key=lambda pair: pair[0])]


def _trace_json(
  trace: Path | None, format_: str | None, root: Path
) -> dict[str, object] | None:
  if trace is None:
    return None
  return {"path": str(trace.relative_to(root)), "format": format_}


def _relative(path: Path | None, root: Path) -> str | None:
  return None if path is None else str(path.relative_to(root))


def _order(output: str) -> tuple[str, int]:
  # "trial-10" comes after "trial-9".
  match = re.fullmatch(r"(.*)-(\d+)", output)
  if match is None:
    return output, -1
  return match[1], int(match[2])


def _value(value: Value, visit: Visit | None) -> dict[str, object]:
  found: dict[str, object] = {
    "output": value.output,
    "field": list(value.field),
  }
  if visit is None:
    return found | {"value": None, "reason": "the state was not reached"}
  current: object = visit.data
  if value.output is not None:
    path = visit.outputs.get(value.output)
    if path is None:
      why = f"{visit.state} visit {visit.visit} has no output {value.output}"
      return found | {"value": None, "reason": why}
    try:
      current = read_json(path)
    except RecursionError:
      return found | {
        "value": None,
        "reason": f"{value.output}: nested too deep",
      }
    except (OSError, ValueError) as error:
      return found | {"value": None, "reason": f"{value.output}: {error}"}
  where = value.output or "the outcome data"
  for key in value.field:
    if not isinstance(current, dict) or key not in current:
      return found | {"value": None, "reason": f"{where} has no key {key}"}
    current = cast(dict[str, object], current)[key]
  return found | {"value": current, "reason": None}


VALUE_LIMIT = 1024 * 1024
"""The most bytes of a JSON output a deliverable's value is read from."""


def read_json(path: Path) -> object:
  """Read a JSON file an action wrote, never through a symlink.

  Args:
    path: The file.

  Returns:
    What it holds.

  Raises:
    OSError: When it cannot be opened, as a symlink cannot.
    ValueError: When it is not a regular file, is over VALUE_LIMIT bytes or
      does not hold JSON.
  """  # noqa: DOC503
  # An output may come from a container running untrusted code: never follow
  # a symlink, which could bring a host file into the record.
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  with os.fdopen(fd, "rb") as file:
    status = os.fstat(file.fileno())
    if not stat.S_ISREG(status.st_mode):
      raise ValueError("not a regular file")
    if status.st_size > VALUE_LIMIT:
      raise ValueError(f"over {VALUE_LIMIT} bytes")
    data: bytes = file.read(VALUE_LIMIT)
    return cast("object", json.loads(data))


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


def _read_resumption(data: Mapping[str, object], where: str) -> Resumption:
  return Resumption(
    _time(data, "at", where),
    _integer(data, "kept", where),
    [
      _object(item, f"{where}.discarded[{i}]")
      for i, item in enumerate(_list(data, "discarded", where))
    ],
    _number(data, "carried_usd", where),
    _maybe_text(data, "reason", where),
  )


def _read_visit(root: Path, data: Mapping[str, object], where: str) -> Visit:
  return Visit(
    _text(data, "state", where),
    _integer(data, "visit", where),
    outcome=_maybe_text(data, "outcome", where),
    data=_object(_get(data, "data", where), f"{where}.data"),
    mark=_maybe_text(data, "mark", where),
    actions=[
      _read_action(
        root, _object(item, f"{where}.actions[{i}]"), f"{where}.actions[{i}]"
      )
      for i, item in enumerate(_list(data, "actions", where))
    ],
    round=_integer(data, "round", where),
    last=_flag(data, "last", where),
  )


def _read_action(
  root: Path, data: Mapping[str, object], where: str
) -> ActionRecord:
  outputs = _object(_get(data, "outputs", where), f"{where}.outputs")
  trace = _read_trace(root, data, where)
  session = _get(data, "session", where)
  trial = _get(data, "trial", where)
  return ActionRecord(
    _text(data, "kind", where),
    _text(data, "status", where),
    {
      name: _inside(root, path, f"{where}.outputs.{name}")
      for name, path in outputs.items()
    },
    _time(data, "started_at", where),
    _time(data, "ended_at", where),
    _number(data, "cost_usd", where),
    _maybe_text(data, "summary", where),
    trial=None
    if trial is None
    else _read_trial(root, _object(trial, f"{where}.trial"), f"{where}.trial"),
    session=None if session is None else _object(session, f"{where}.session"),
    trace=None if trace is None else trace[0],
    trace_format=None if trace is None else trace[1],
    cost_known="cost_known" not in data or _flag(data, "cost_known", where),
    logs=[
      _inside(root, path, f"{where}.logs[{i}]")
      for i, path in enumerate(
        _list(data, "logs", where) if "logs" in data else []
      )
    ],
  )


def _read_trial(
  root: Path, data: Mapping[str, object], where: str
) -> TrialRecord:
  rewards = _object(_get(data, "rewards", where), f"{where}.rewards")
  trace = _read_trace(root, data, where)
  return TrialRecord(
    _text(data, "id", where),
    _inside(root, _get(data, "path", where), f"{where}.path"),
    None if trace is None else trace[0],
    None if trace is None else trace[1],
    {name: _number(rewards, name, f"{where}.rewards") for name in rewards},
    _maybe_text(data, "exception", where),
    # A record written before a trial had a source has no key.
    None
    if data.get("source") is None
    else _inside(root, data["source"], f"{where}.source"),
  )


def _read_trace(
  root: Path, data: Mapping[str, object], where: str
) -> tuple[Path, str | None] | None:
  trace = _get(data, "trace", where)
  if trace is None:
    return None
  trace = _object(trace, f"{where}.trace")
  path = _inside(root, _get(trace, "path", f"{where}.trace"), f"{where}.trace")
  return path, _maybe_text(trace, "format", f"{where}.trace")


def _get(data: Mapping[str, object], key: str, where: str) -> object:
  if key not in data:
    raise RecordError(f"{where}: no {key}")
  return data[key]


def _object(value: object, where: str) -> dict[str, object]:
  if not isinstance(value, dict):
    raise RecordError(f"{where}: not an object")
  return cast(dict[str, object], value)


def _list(data: Mapping[str, object], key: str, where: str) -> list[object]:
  value = _get(data, key, where)
  if not isinstance(value, list):
    raise RecordError(f"{where}: {key} is not a list")
  return cast(list[object], value)


def _maybe_text(data: Mapping[str, object], key: str, where: str) -> str | None:
  value = _get(data, key, where)
  return None if value is None else _text(data, key, where)


def _text(data: Mapping[str, object], key: str, where: str) -> str:
  value = _get(data, key, where)
  if not isinstance(value, str):
    raise RecordError(f"{where}: {key} is not a string")
  return value


def _maybe_integer(
  data: Mapping[str, object], key: str, where: str
) -> int | None:
  value = _get(data, key, where)
  return None if value is None else _integer(data, key, where)


def _integer(data: Mapping[str, object], key: str, where: str) -> int:
  value = _get(data, key, where)
  if isinstance(value, bool) or not isinstance(value, int):
    raise RecordError(f"{where}: {key} is not an integer")
  return value


def _maybe_number(
  data: Mapping[str, object], key: str, where: str
) -> float | None:
  value = _get(data, key, where)
  return None if value is None else _number(data, key, where)


def _number(data: Mapping[str, object], key: str, where: str) -> float:
  value = _get(data, key, where)
  if isinstance(value, bool) or not isinstance(value, int | float):
    raise RecordError(f"{where}: {key} is not a number")
  return float(value)


def _flag(data: Mapping[str, object], key: str, where: str) -> bool:
  value = _get(data, key, where)
  if not isinstance(value, bool):
    raise RecordError(f"{where}: {key} is not a boolean")
  return value


def _time(data: Mapping[str, object], key: str, where: str) -> datetime:
  text = _text(data, key, where)
  try:
    value = datetime.fromisoformat(text)
  except ValueError as error:
    raise RecordError(f"{where}: {key}: {error}") from error
  if value.tzinfo is None:
    raise RecordError(f"{where}: {key} has no time zone")
  return value


def _inside(root: Path, value: object, where: str) -> Path:
  # A record may have been edited by hand: a path that leaves root would make
  # a later state read or a deliverable name a file outside the attempt.
  if not isinstance(value, str):
    raise RecordError(f"{where}: not a string")
  path = Path(value)
  if path.is_absolute() or ".." in path.parts:
    raise RecordError(f"{where}: {value!r} is not inside the attempt")
  return root / path
