from collections.abc import Callable, Mapping
import multiprocessing
import os
from pathlib import Path
import subprocess
from typing import cast

import pytest

from dataforge.queue.proc import Leader, leader
from dataforge.queue.store import connect
from dataforge.runs import JOB_ID_ENV, QUEUE_DB_ENV, Store
from tests.processes import WAIT
from tests.runs.fake import FakeHooks


@pytest.fixture(autouse=True)
def no_job(monkeypatch: pytest.MonkeyPatch) -> None:
  # The tests may run inside a queue job, whose variables are not theirs.
  monkeypatch.delenv(JOB_ID_ENV, raising=False)
  monkeypatch.delenv(QUEUE_DB_ENV, raising=False)


@pytest.fixture
def store(tmp_path: Path) -> Store:
  return Store(tmp_path / "db" / "runs.sqlite")


@pytest.fixture
def hooks(tmp_path: Path, store: Store) -> FakeHooks:
  return FakeHooks(tmp_path, store)


@pytest.fixture
def me() -> Leader:
  found = leader(os.getpid())
  assert found is not None
  return found


@pytest.fixture
def gone() -> Leader:
  # A process that ran and is gone, as a holder.
  with subprocess.Popen(["sleep", "60"]) as process:
    found = leader(process.pid)
    process.kill()
    _ = process.wait(WAIT)
  assert found is not None
  return found


def at_once(*targets: Callable[[], object]) -> list[int]:
  """Run each target in a process of its own, forked from this one.

  Args:
    *targets: What each process runs; a test's monkeypatches hold in them.

  Returns:
    Each process's exit status: 0 when its target returned, 1 when it
    raised, the code of a SystemExit.

  Raises:
    AssertionError: When a process did not end in WAIT seconds.
  """
  context = multiprocessing.get_context("fork")
  processes = [context.Process(target=target) for target in targets]
  for process in processes:
    process.start()
  for process in processes:
    process.join(WAIT)
  codes: list[int] = []
  for process in processes:
    if process.exitcode is None:
      process.kill()
      process.join(WAIT)
      raise AssertionError(f"process {process.pid} did not end in {WAIT} s")
    codes.append(process.exitcode)
    process.close()
  return codes


@pytest.fixture
def barrier() -> Callable[[], None]:
  # Two processes forked after it wait at it for each other.
  shared = multiprocessing.get_context("fork").Barrier(2)

  def wait() -> None:
    _ = shared.wait(WAIT)

  return wait


def queue_db(path: Path, jobs: dict[int, bool]) -> Path:
  """Make a queue database with jobs whose cancellation was asked for or not.

  Args:
    path: The database.
    jobs: Each job's ID and whether a person asked to cancel it.

  Returns:
    The database.
  """
  with connect(path) as conn:
    for job, canceled in jobs.items():
      _ = conn.execute(
        """
        INSERT INTO jobs (id, queue, argv, cwd, memory_mib, labels, state,
          queued_at, cancel_requested_at, log)
        VALUES (?, 'q', '[]', '/', 0, '{}', 'running', 0, ?, '')
        """,
        (job, 1.0 if canceled else None),
      )
  return path


def after[**P, R](
  wait: Callable[[], None], method: Callable[P, R]
) -> Callable[P, R]:
  """Make a method wait at a barrier before it runs.

  Args:
    wait: The barrier's wait.
    method: The method.

  Returns:
    The method that waits first.
  """

  def waiting(*args: P.args, **kwargs: P.kwargs) -> R:
    wait()
    return method(*args, **kwargs)

  return waiting


def failing[**P, R](
  errors: list[Exception], method: Callable[P, R]
) -> Callable[P, R]:
  """Make a method raise errors before it runs, one per call.

  Args:
    errors: What its first calls raise, from the back.
    method: The method.

  Returns:
    The method that fails first.
  """

  def failing(*args: P.args, **kwargs: P.kwargs) -> R:
    if errors:
      raise errors.pop()
    return method(*args, **kwargs)

  return failing


def visits(record: Mapping[str, object] | None) -> list[object]:
  """Give a record's visits.

  Args:
    record: The record.

  Returns:
    Its visits.
  """
  assert record is not None
  return cast(list[object], record["visits"])
