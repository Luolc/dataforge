"""A job's wrapper: it runs the command and writes how it ended.

The scheduler starts `python -m dataforge.queue.wrapper <database> <id>
<cancel_grace_s>` as the leader of a new session, with the job's log as its
output, inside the transaction that claims the job. The wrapper runs the
command only once that claim is committed with its own pid, waits for it, and
writes its end: done for exit status 0, failed for any other, canceled when
the job's cancellation was asked for. The command runs with the wrapper's
environment and DATAFORGE_JOB_ID, the job's ID, and DATAFORGE_QUEUE_DB, the
database's absolute path, through which a store of attempts (dataforge.runs)
knows the job an attempt runs in. It outlives a scheduler that stops, so a
job's end is written also when no scheduler waits for it.

A command still running cancel_grace_s after its cancellation was asked for
gets SIGKILL to its process group from the wrapper, so that it ends also while
no scheduler runs; the wrapper keeps the cancel_grace_s it was started with.
The wrapper leads that group and ends with it: a child of it that has left the
group for a session of its own sends the signal, and writes the end, canceled
with no exit code, once nothing of the group runs, as a scheduler does for a
wrapper that is gone.
"""

from collections.abc import Callable, Sequence
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import time

from dataforge.queue.proc import group_alive, Leader
from dataforge.queue.store import connect, row, write

_END = """
UPDATE jobs SET state = ?, exit_code = ?, ended_at = ?
WHERE id = ? AND state = 'running' AND pid = ?
"""

_POLL_S = 0.1

JOB_ID_ENV = "DATAFORGE_JOB_ID"
"""The variable that gives a command the ID of the job it runs in."""

QUEUE_DB_ENV = "DATAFORGE_QUEUE_DB"
"""The variable that gives a command its job's database, an absolute path."""


def command(database: Path, job_id: int, grace: float) -> list[str]:
  """Give the command that runs a job's wrapper, as the scheduler starts it.

  Args:
    database: The queue's database, an absolute path.
    job_id: The job.
    grace: cancel_grace_s.

  Returns:
    The command: this Python, the wrapper's module and its arguments.
  """
  return [
    sys.executable,
    "-m",
    "dataforge.queue.wrapper",
    str(database),
    str(job_id),
    str(grace),
  ]


def main(argv: Sequence[str] | None = None) -> int:
  """Run a claimed job.

  Args:
    argv: The database, the job's ID and, optionally, cancel_grace_s; None
      for the command line's. Without cancel_grace_s the wrapper sends no
      SIGKILL.

  Returns:
    0 once the end is written, 1 when the job was not claimed for this
    process.
  """
  database, job_id, *rest = sys.argv[1:] if argv is None else argv
  grace = float(rest[0]) if rest else None
  terminated = False

  def _term(_sig: int, _frame: object) -> None:
    # SIGTERM goes to the whole process group: the command gets it too, and
    # the wrapper stays to write the end.
    nonlocal terminated
    terminated = True

  _ = signal.signal(signal.SIGTERM, _term)
  path, pid = Path(database), os.getpid()
  with connect(path) as conn:
    with write(conn):
      job = row(conn, int(job_id))
    started = None if job is None else job.leader()
    if (
      job is None
      or job.state != "running"
      or started is None
      or started.pid != pid
    ):
      print(f"wrapper: job {job_id} is not claimed by {pid}", file=sys.stderr)
      return 1
  print(f"wrapper: job {job.id}: {job.argv} in {job.cwd}", file=sys.stderr)
  code: int | None = None
  if job.cancel_requested_at is None:
    try:
      env = {
        **os.environ,
        JOB_ID_ENV: str(job.id),
        QUEUE_DB_ENV: str(path.resolve()),
      }
      child = subprocess.Popen(
        job.argv, cwd=job.cwd, stdin=subprocess.DEVNULL, env=env
      )
    except OSError as error:
      print(f"wrapper: job {job.id}: {error}", file=sys.stderr)
    else:
      if terminated:
        os.killpg(0, signal.SIGTERM)
      code = _wait(child, path, job.id, grace, lambda: terminated)
      if code is None:
        print(
          f"wrapper: job {job.id}: SIGKILL after the grace period",
          file=sys.stderr,
        )
        _kill_group(started)
  with connect(path) as conn, write(conn):
    now = row(conn, job.id)
    canceled = now is not None and now.cancel_requested_at is not None
    state = "canceled" if canceled else "done" if code == 0 else "failed"
    _ = conn.execute(
      _END,
      (state, code, time.time(), job.id, pid),
    )
  print(f"wrapper: job {job.id}: {state}, exit {code}", file=sys.stderr)
  return 0


def _wait(
  child: subprocess.Popen[bytes],
  path: Path,
  job_id: int,
  grace: float | None,
  terminated: Callable[[], bool],
) -> int | None:
  # Wait for the command, or until grace has passed since the job's
  # cancellation was asked for. A cancellation sends SIGTERM once it is in
  # the row, so the wrapper reads the row only when grace has passed since
  # SIGTERM came, not while a command that SIGTERM ends is exiting, and reads
  # it again a grace period later when the read fails or finds no
  # cancellation.
  if grace is None:
    return child.wait()
  deadline: float | None = None
  wake, woken = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
  ended = os.pidfd_open(child.pid)
  previous = signal.set_wakeup_fd(woken)
  try:
    while (code := child.poll()) is None:
      if deadline is None and terminated():
        deadline = time.time() + grace
      timeout = None if deadline is None else max(0.0, deadline - time.time())
      ready, _, _ = select.select([ended, wake], [], [], timeout)
      if wake in ready:
        _ = os.read(wake, 512)
      if ready or deadline is None:
        continue
      asked = _asked(path, job_id)
      if asked is not None and time.time() >= asked + grace:
        return None
      deadline = None if asked is None else asked + grace
    return code
  finally:
    _ = signal.set_wakeup_fd(previous)
    for fd in (wake, woken, ended):
      os.close(fd)


def _asked(path: Path, job_id: int) -> float | None:
  # When the job's cancellation was asked for. A read that fails gives None:
  # the command runs on, and a wrapper that exited here would leave the
  # scheduler to take the job for lost and kill it.
  try:
    with connect(path) as conn:
      now = row(conn, job_id)
  except sqlite3.Error as error:
    print(f"wrapper: job {job_id}: {error}", file=sys.stderr)
    return None
  return None if now is None else now.cancel_requested_at


def _kill_group(started: Leader) -> None:
  # Returns only in a child that has left the group and outlived it; the
  # wrapper ends with the group.
  killer = os.fork()
  if killer == 0:
    _ = os.setsid()
    os.killpg(started.pid, signal.SIGKILL)
    while group_alive(started):
      time.sleep(_POLL_S)
    return
  _ = os.waitpid(killer, 0)
  # The child ended before it sent the signal: end the group from here, and
  # leave the end to the scheduler.
  os.killpg(0, signal.SIGKILL)


if __name__ == "__main__":
  sys.exit(main())
