"""A fake dataset for dataforge.runs: a one-state machine and its hooks.

Each hook appends a line to calls.log in the hooks' base directory, which
processes the tests fork share, so that a test counts the calls of each.
"""

# pyright: reportUnusedParameter=false
# The fakes implement Hooks and take parameters they do not need.

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
from typing import override

from dataforge.curation import Attempt, Context, End, Machine, Outcome, State
from dataforge.runs import AttemptRow, Hooks, OnStep, Store

KEPT = "kept"
"""What finalize keeps in an attempt's root, written whole or not at all."""

WHOLE = "every file of the attempt\n"


class Work(State):
  """The one state: it ends the attempt passed, unless told otherwise.

  With `sigterm`, it sends this process SIGTERM on its first run, so that the
  attempt is stopped while the state runs.
  """

  def __init__(self, base: Path, *, sigterm: bool = False) -> None:
    """Make the state.

    Args:
      base: The hooks' base directory.
      sigterm: Whether its first run sends this process SIGTERM.
    """
    super().__init__("work", {"done": End("passed")})
    self.base: Path = base
    self.sigterm: bool = sigterm

  @override
  def run(self, ctx: Context) -> Outcome:
    if self.sigterm and once(self.base, "sigterm"):
      os.kill(os.getpid(), signal.SIGTERM)
    return Outcome("done")


def once(base: Path, name: str) -> bool:
  """Tell whether this is the first time a name is asked for.

  Args:
    base: Where the marks are kept.
    name: The name.

  Returns:
    True the first time, in any process.
  """
  try:
    fd = os.open(base / f"once-{name}", os.O_CREAT | os.O_EXCL, 0o600)
  except FileExistsError:
    return False
  os.close(fd)
  return True


@dataclass
class FakeHooks(Hooks):
  """Hooks whose behaviour a test sets.

  Attributes:
    base: Where the attempts' directories, calls.log and the marks are.
    store: The store, which on_stop reads.
    sigterm: Whether the state sends SIGTERM on its first run.
    stop_in: A hook that raises SystemExit on its first call, in any process.
    fail_in: A hook that raises OSError on its first call, in any process.
    problem: What resumable says.
    take_over_problem: What take_over says.
    left: What cleanup says is left.
    cleanup_raises: What cleanup raises.
    extras: The row's extra that each call of set_up and take_over in this
      process got, as (hook, extra).
  """

  base: Path
  store: Store
  sigterm: bool = False
  stop_in: str | None = None
  fail_in: str | None = None
  problem: str | None = None
  take_over_problem: str | None = None
  left: Mapping[str, object] = field(default_factory=dict[str, object])
  cleanup_raises: BaseException | None = None
  extras: list[tuple[str, dict[str, object]]] = field(
    default_factory=list[tuple[str, dict[str, object]]]
  )

  def calls(self) -> list[str]:
    """Read calls.log.

    Returns:
      A line per call, `<hook> <attempt ID>`, and for on_stop the row's
      state in the store as it ran.
    """
    log = self.base / "calls.log"
    return log.read_text().splitlines() if log.exists() else []

  def hook_calls(self, hook: str) -> list[str]:
    """Read the calls of one hook.

    Args:
      hook: The hook.

    Returns:
      Its lines of calls.log.
    """
    return [line for line in self.calls() if line.split()[0] == hook]

  @override
  def root(self, attempt_id: str) -> Path:
    return self.base / "attempts" / attempt_id

  @override
  def set_up(
    self, row: AttemptRow, on_step: OnStep, stack: ExitStack
  ) -> Callable[[], Attempt]:
    self._log("set_up", row)
    self.extras.append(("set_up", row.extra))
    root = Path(row.root)
    root.mkdir(parents=True, exist_ok=True)
    _ = stack.enter_context(self._seen(row))
    machine = Machine([Work(self.base, sigterm=self.sigterm)], on_step=on_step)
    record = row.attempt()
    if record is None:
      return lambda: machine.run(root)
    return lambda: machine.resume(record)

  @override
  def resumable(self, row: AttemptRow) -> str | None:
    self._log("resumable", row)
    return self.problem

  @override
  def take_over(self, row: AttemptRow) -> str | None:
    self._log("take_over", row)
    self.extras.append(("take_over", row.extra))
    return self.take_over_problem

  @override
  def outcome(
    self, record: Attempt | None, error: Exception | None
  ) -> tuple[str, str | None]:
    if error is not None:
      return "error", f"{type(error).__name__}: {error}"
    if record is None or record.status is None:
      return "error", "no status"
    return record.status, record.reason

  @override
  def finalize(self, row: AttemptRow, record: Attempt | None) -> None:
    self._log("finalize", row)
    root = Path(row.root)
    root.mkdir(parents=True, exist_ok=True)
    if (root / KEPT).exists():
      return
    part = root / f"{KEPT}.part"
    _ = part.write_text(WHOLE[:5])
    self._stop("finalize")
    _ = part.write_text(WHOLE)
    _ = part.replace(root / KEPT)

  @override
  def on_ended(self, row: AttemptRow) -> None:
    self._log("on_ended", row)
    self._stop("on_ended")

  @override
  def on_stop(self, row: AttemptRow) -> None:
    now = self.store.get(row.id)
    self._log("on_stop", row, "none" if now is None else now.state)

  @override
  def cleanup(self, row: AttemptRow) -> Mapping[str, object]:
    self._log("cleanup", row)
    if self.cleanup_raises is not None:
      raise self.cleanup_raises
    return self.left

  @override
  def meter(self, row: AttemptRow) -> float | None:
    return 1.5

  @contextmanager
  def _seen(self, row: AttemptRow) -> Generator[None]:
    # Records how the stack was left: with the stop's exception or none.
    try:
      yield
    except BaseException as stop:
      self._log("stack", row, type(stop).__name__)
      raise
    self._log("stack", row, "none")

  def _stop(self, hook: str) -> None:
    if self.stop_in == hook and once(self.base, f"stop-{hook}"):
      raise SystemExit(f"stopped in {hook}")
    if self.fail_in == hook and once(self.base, f"fail-{hook}"):
      raise OSError(f"failed in {hook}")

  def _log(self, hook: str, row: AttemptRow, *more: str) -> None:
    line = " ".join((hook, row.id, *more))
    with (self.base / "calls.log").open("a") as log:
      _ = log.write(line + "\n")


HOOKS = FakeHooks(Path(), Store(Path("runs.sqlite")))
"""Hooks for the command's --hooks, whose base and store a test sets."""
