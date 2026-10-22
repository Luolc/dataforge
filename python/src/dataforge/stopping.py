"""Stopping the calls `fan_out` runs in threads once it is interrupted.

Only the main thread gets the SystemExit of a SIGTERM or a KeyboardInterrupt;
a call running in another thread goes on, and a child it started in a session
of its own outlives the process. So `fan_out` runs each call under a stop,
which it sets once it is interrupted. Code that starts a long-lived child or
waits in such a call checks the stop: `check` before it starts one, `wait`
while it waits for one, each raising Stopped once the stop is set, so that the
call ends its child in its own `except BaseException` or `finally` and starts
nothing more. Outside `fan_out` there is no stop, and neither ever raises.

A stop set has a deadline, BUDGET_S after it was set: each command that a
stopped call runs on its way out gets `left(timeout)` as its timeout, its own
timeout or what is left until the deadline, whichever is less, so that the
calls end within the budget whatever hangs.
"""

from collections.abc import Callable
import contextvars
import math
import subprocess
import threading
import time
from typing import final, overload

# How often `wait` looks at the stop.
POLL_S = 0.5
# How long a stop gives the commands its calls run on their way out.
BUDGET_S = 40.0


@final
class Stop:
  """The stop of the calls of one `fan_out`, and its deadline once set."""

  def __init__(self) -> None:
    """Make a stop not set."""
    self._lock: threading.Lock = threading.Lock()
    self._set: threading.Event = threading.Event()
    self.deadline: float = math.inf

  def set(self) -> None:
    """Set the stop, its deadline BUDGET_S from now; again does nothing."""
    with self._lock:
      if not self._set.is_set():
        self.deadline = time.monotonic() + BUDGET_S
        self._set.set()

  def is_set(self) -> bool:
    """Return whether the stop is set.

    Returns:
      Whether it is.
    """
    return self._set.is_set()


_stop: contextvars.ContextVar[Stop | None] = contextvars.ContextVar(
  "stop", default=None
)


@final
class Stopped(BaseException):
  """The stop of the call this thread runs is set.

  A BaseException, as SystemExit is, so that `except Exception` lets it by.
  """


def current() -> Stop | None:
  """Return the stop of the call this thread runs.

  Returns:
    It, or None outside `fan_out`'s calls.
  """
  return _stop.get()


def under[T](stop: Stop, call: Callable[[], T]) -> Callable[[], T]:
  """Bind a call to a stop, for a thread to run.

  Args:
    stop: The stop.
    call: The call.

  Returns:
    What runs the call, with `current` giving stop, once it checked the stop.
  """
  context = contextvars.copy_context()
  _ = context.run(_stop.set, stop)

  def run() -> T:
    check()
    return call()

  return lambda: context.run(run)


def stopped() -> bool:
  """Return whether the stop of the call this thread runs is set.

  Returns:
    Whether it is; False outside `fan_out`'s calls.
  """
  stop = _stop.get()
  return stop is not None and stop.is_set()


@overload
def left(timeout: float) -> float: ...
@overload
def left(timeout: None) -> float | None: ...
def left(timeout: float | None) -> float | None:
  """Return the timeout of a command of the call this thread runs.

  Args:
    timeout: The command's own timeout, or None for none.

  Returns:
    timeout while the call's stop is not set; once it is, what is left until
    its deadline, at least 0, when that is less.
  """
  stop = _stop.get()
  if stop is None or not stop.is_set():
    return timeout
  rest = max(stop.deadline - time.monotonic(), 0.0)
  return rest if timeout is None else min(timeout, rest)


def check() -> None:
  """Raise Stopped when the stop of the call this thread runs is set.

  Raises:
    Stopped: It is set.
  """
  stop = _stop.get()
  if stop is not None and stop.is_set():
    raise Stopped


def wait(
  process: subprocess.Popen[bytes],
  timeout: float | None,
  data: bytes | None = None,
) -> int:
  """Send data to a process and wait for it, as `Popen.communicate` does.

  Its output is not read: the process writes it elsewhere, to files say.

  Args:
    process: The process.
    timeout: How long to wait, or None for as long as it runs.
    data: What to write to its stdin before it is closed, if any.

  Returns:
    Its exit status.

  Raises:
    Stopped: The stop of the call this thread runs was set first; the
      process still runs.
    subprocess.TimeoutExpired: The process ran past timeout; it still runs.
  """  # noqa: DOC503
  deadline = math.inf if timeout is None else time.monotonic() + timeout
  while True:
    check()
    left = deadline - time.monotonic()
    try:
      _ = process.communicate(data, max(min(left, POLL_S), 0))
    except subprocess.TimeoutExpired:
      if time.monotonic() >= deadline:
        raise
      # communicate keeps what it has not written yet for the next call.
      data = None
      continue
    return process.returncode
