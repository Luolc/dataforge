"""A SIGTERM to the test's own process, as a queue's cancel sends a job's."""

from collections.abc import Callable, Generator
import contextlib
import os
import signal
import threading
import time

WAIT = 10.0


@contextlib.contextmanager
def sigterm_once(
  ready: Callable[[], bool],
  interrupt: Callable[[], BaseException] = lambda: SystemExit(143),
) -> Generator[list[float]]:
  """Send this process SIGTERM once ready says so, while the block runs.

  The SIGTERM raises what interrupt makes in the main thread, wherever it is,
  as a command that wants its `finally` blocks run on SIGTERM has it do:
  SystemExit(143) by default, or KeyboardInterrupt.

  Args:
    ready: Asked every 50 ms, for at most WAIT seconds.
    interrupt: Makes what the SIGTERM raises.

  Yields:
    A list that gets the `time.monotonic()` at which the SIGTERM was sent.
  """
  sent: list[float] = []
  done = threading.Event()

  def send() -> None:
    deadline = time.monotonic() + WAIT
    while not ready():
      if done.is_set() or time.monotonic() > deadline:
        return
      time.sleep(0.05)
    sent.append(time.monotonic())
    os.kill(os.getpid(), signal.SIGTERM)

  def handler(*_: object) -> None:
    raise interrupt()

  previous = signal.signal(signal.SIGTERM, handler)
  thread = threading.Thread(target=send)
  thread.start()
  try:
    yield sent
  finally:
    done.set()
    thread.join()
    _ = signal.signal(signal.SIGTERM, previous)
