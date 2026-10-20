"""A job's wrapper: it runs the command and writes how it ended.

The scheduler starts `python -m dataforge.queue.wrapper <database> <id>` as
the leader of a new session, with the job's log as its output, inside the
transaction that claims the job. The wrapper runs the command only once that
claim is committed with its own pid, waits for it, and writes its end: done
for exit status 0, failed for any other, canceled when the job's
cancellation was asked for. It outlives a scheduler that stops, so a job's
end is written also when no scheduler waits for it.
"""

from collections.abc import Sequence
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from dataforge.queue.store import connect, row, write

_END = """
UPDATE jobs SET state = ?, exit_code = ?, ended_at = ?
WHERE id = ? AND state = 'running' AND pid = ?
"""


def main(argv: Sequence[str] | None = None) -> int:
  """Run a claimed job.

  Args:
    argv: The database and the job's ID; None for the command line's.

  Returns:
    0 once the end is written, 1 when the job was not claimed for this
    process.
  """
  database, job_id = (sys.argv[1:] if argv is None else argv)[:2]
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
    if job is None or job.state != "running" or job.pid != pid:
      print(f"wrapper: job {job_id} is not claimed by {pid}", file=sys.stderr)
      return 1
  print(f"wrapper: job {job.id}: {job.argv} in {job.cwd}", file=sys.stderr)
  code: int | None = None
  if job.cancel_requested_at is None:
    try:
      child = subprocess.Popen(job.argv, cwd=job.cwd, stdin=subprocess.DEVNULL)
    except OSError as error:
      print(f"wrapper: job {job.id}: {error}", file=sys.stderr)
    else:
      if terminated:
        os.killpg(0, signal.SIGTERM)
      code = child.wait()
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


if __name__ == "__main__":
  sys.exit(main())
