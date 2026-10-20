"""Other processes of the machine, for what they share with the tests'."""

from collections.abc import Generator
import contextlib
import subprocess
import sys

WAIT = 10.0


@contextlib.contextmanager
def holding(code: str, *args: str) -> Generator[subprocess.Popen[str]]:
  """Run Python code in another process that holds what it took.

  Args:
    code: What the process runs: it prints `held` once it holds what it
      takes, then holds it until its stdin closes.
    *args: Its arguments.

  Yields:
    The process, once it printed `held`; killed, and its pipes closed, after
    the block.
  """
  with subprocess.Popen(
    [sys.executable, "-c", code, *args],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
  ) as process:
    try:
      assert process.stdout is not None
      assert process.stdout.readline() == "held\n"
      yield process
    finally:
      process.kill()


def kill(process: subprocess.Popen[str]) -> None:
  """Kill a process with SIGKILL and wait for it to end.

  Args:
    process: The process.
  """
  process.kill()
  _ = process.wait(WAIT)
