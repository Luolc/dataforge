"""What a dataset gives run_attempt and reconcile: its hooks and its words."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path

from dataforge.curation.engine import Attempt
from dataforge.runs.render import render
from dataforge.runs.store import AttemptRow, Event

type OnStep = Callable[[Attempt], None]


class Hooks(ABC):
  """A dataset's hooks; subclass it and give the abstract ones.

  finalize, on_ended and cleanup may run more than once for one attempt, in
  more than one process, and must do the same each time: reconcile calls them
  again for an attempt whose holder died in them.

  Attributes:
    given_up: The dataset's word for an interrupted attempt that no one goes
      on with, which a newer attempt of its item ends.
    record: The name of the record file in an attempt's root.
  """

  given_up: str = "given_up"
  record: str = "record.json"

  @abstractmethod
  def root(self, attempt_id: str) -> Path:
    """Give the directory of a new attempt.

    Args:
      attempt_id: Its ID, `<item>/<stamp>`.

    Returns:
      The directory, an absolute path.
    """

  @abstractmethod
  def set_up(
    self, row: AttemptRow, on_step: OnStep, stack: ExitStack
  ) -> Callable[[], Attempt]:
    """Set up an attempt, a new one or one that goes on.

    Args:
      row: Its row, running under this process; its record is None for a new
        attempt, else the record to go on from.
      on_step: The runner's on_step, which the machine is to get, and which
        writes the row and the record file.
      stack: What it sets up and the runner removes after the attempt ends,
        such as its containers; when SIGTERM or SIGINT stops the attempt, the
        stack is left with that SystemExit or KeyboardInterrupt, which a
        context manager in it may take to keep what a later attempt goes on
        with.

    Returns:
      What runs the attempt: Machine.run on its root for a new one,
      Machine.resume of its record for one that goes on.
    """

  @abstractmethod
  def resumable(self, row: AttemptRow) -> str | None:
    """Tell why an interrupted attempt may not go on.

    Args:
      row: Its row.

    Returns:
      The reason, or None when it may go on.
    """

  @abstractmethod
  def take_over(self, row: AttemptRow) -> str | None:
    """Take over what an interrupted attempt left, before it goes on.

    It runs only once the row is running under this process.

    Args:
      row: Its row.

    Returns:
      Why it could not, or None when it did.
    """

  @abstractmethod
  def outcome(
    self, record: Attempt | None, error: Exception | None
  ) -> tuple[str, str | None]:
    """Give the dataset's word for how an attempt ended, and why.

    Args:
      record: Its record as last written; None when it has none.
      error: What a state or set_up raised, if anything did.

    Returns:
      The word and the reason; the row takes the record's own reason, the
      machine's, over it when the record has one.
    """

  @abstractmethod
  def finalize(self, row: AttemptRow, record: Attempt | None) -> None:
    """Keep what an ended attempt keeps, before its outcome is written.

    Args:
      row: Its row, running under this process.
      record: Its record.
    """

  @abstractmethod
  def on_ended(self, row: AttemptRow) -> None:
    """Write what the dataset derives from an ended row.

    Args:
      row: The row, ended.
    """

  def on_stop(self, row: AttemptRow) -> None:
    """Stop what runs for an attempt that SIGTERM or SIGINT stopped.

    It runs once the row is interrupted, before the stack set_up was given
    is left. By default it does nothing.

    Args:
      row: Its row, interrupted.
    """
    _ = row

  def cleanup(self, row: AttemptRow) -> Mapping[str, object]:
    """Remove what is left of an ended attempt, after on_ended.

    By default it removes nothing.

    Args:
      row: Its row, ended.

    Returns:
      What is left, by kind, such as counts; what it leaves or raises changes
      no outcome.
    """
    _ = row
    return {}

  def meter(self, row: AttemptRow) -> float | None:
    """Read what an interrupted attempt has spent, from the dataset's ledger.

    By default there is no ledger.

    Args:
      row: Its row.

    Returns:
      What it spent, in US dollars; None to keep the row's cost_usd.
    """
    _ = row
    return None

  def render(
    self, row: AttemptRow, events: list[Event]
  ) -> Mapping[str, object]:
    """Render a row as its record file.

    By default dataforge.runs.render, which takes no events.

    Args:
      row: The row.
      events: Its events, such as its cleanup's.

    Returns:
      What the record file holds. It must keep the record's keys, and the
      record's status as machine_status, for reconcile to read the record
      back from them.
    """
    _ = events
    return render(row)
