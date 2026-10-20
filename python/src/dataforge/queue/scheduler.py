"""The scheduler: it starts queued jobs when they fit, and notes how they end.

Each look at the table (a tick) does three things:

- A running job whose wrapper is gone without writing its end (killed, or the
  machine restarted) is recorded canceled when its cancellation was asked
  for and failed otherwise, with no exit code.
- A running job whose cancellation is older than cancel_grace_s gets SIGKILL
  sent to its process group.
- In one BEGIN IMMEDIATE transaction, queued jobs start while their queue and
  the machine are under their caps, the 1-minute load is at most max_load1,
  and the job's memory fits. A job fits when MemAvailable, less what running
  jobs have declared but not yet grown to (their declared memory less the
  resident memory of their session, never below 0), less what this tick has
  started, leaves min_mem_available_mib once the job's memory is taken too.
  Within a queue the oldest goes first, and a queue whose oldest does not fit
  starts none this tick; across queues the oldest of their first jobs goes
  first.

A job runs as `python -m dataforge.queue.wrapper <database> <id>`, in a
session of its own: the wrapper runs the command and writes its end, so that
a job outlives the scheduler, and a scheduler started again takes the running
jobs as they are, knowing each by its wrapper's pid, starttime and boot, and
kills none. A wrapper that is gone while processes of its group run still
(it alone was killed) has its group killed, and its end is recorded once
nothing of the group runs.
"""

from collections.abc import Callable
import logging
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from dataforge.queue.config import Config
from dataforge.queue.proc import (
  alive,
  leader,
  ProcReadings,
  Readings,
  signal_group,
)
from dataforge.queue.store import connect, Job, select, write

_LOST = """
UPDATE jobs SET state = ?, ended_at = ?
WHERE id = ? AND state = 'running' AND pid = ? AND starttime = ?
"""

_UNSTARTED = """
UPDATE jobs SET state = 'failed', started_at = ?, ended_at = ? WHERE id = ?
"""

_STARTED = """
UPDATE jobs SET state = 'running', pid = ?, starttime = ?, boot_id = ?,
  started_at = ?
WHERE id = ?
"""

_log = logging.getLogger(__name__)


class Scheduler:
  """Starts and watches the jobs of one database."""

  def __init__(
    self,
    config: Config,
    readings: Readings | None = None,
    clock: Callable[[], float] = time.time,
  ) -> None:
    """Make a scheduler; it does nothing until tick() or serve().

    Args:
      config: The queue file.
      readings: The machine's readings; None for /proc's.
      clock: Seconds since the epoch.
    """
    self.config: Config = config
    self.readings: Readings = readings or ProcReadings()
    self.clock: Callable[[], float] = clock
    self._children: list[subprocess.Popen[bytes]] = []
    self._waiting: set[int] = set()

  def tick(self) -> None:
    """Look at the table once: settle the ended jobs, then start what fits."""
    # A wrapper this process started stays a zombie until it is waited for.
    self._children = [c for c in self._children if c.poll() is None]
    with connect(self.config.database) as conn:
      for job in select(conn, "state = 'running'"):
        self._settle(conn, job)
      with write(conn):
        self._admit(conn)

  def serve(self, stop: threading.Event) -> None:
    """Tick every poll_s until stop is set.

    Args:
      stop: Set to stop; the running jobs go on.
    """
    while True:
      try:
        self.tick()
      except sqlite3.Error, OSError, ValueError:
        # A locked database or an unreadable /proc file passes; the next
        # tick tries again.
        _log.exception("tick failed")
      if stop.wait(self.config.poll_s):
        return

  def _settle(self, conn: sqlite3.Connection, job: Job) -> None:
    started = job.leader()
    if started is None:
      return
    if not alive(started):
      # The wrapper alone may have been killed: end what is left of its
      # group, and record the end once nothing of it runs.
      if signal_group(started, signal.SIGKILL):
        _log.warning("job %d: its wrapper is gone; killing its group", job.id)
        return
      state = "failed" if job.cancel_requested_at is None else "canceled"
      with write(conn):
        # The wrapper writes its end before it exits; a row it wrote is
        # no longer running, and this changes nothing.
        ended = conn.execute(
          _LOST,
          (state, self.clock(), job.id, started.pid, started.start),
        ).rowcount
      if ended:
        _log.warning("job %d: its wrapper is gone; %s", job.id, state)
      return
    asked = job.cancel_requested_at
    if (
      asked is not None
      and self.clock() - asked >= self.config.cancel_grace_s
      and signal_group(started, signal.SIGKILL)
    ):
      _log.warning("job %d: SIGKILL after the grace period", job.id)

  def _admit(self, conn: sqlite3.Connection) -> None:
    running = select(conn, "state = 'running'")
    total = len(running)
    if total >= self.config.max_running:
      return
    load = self.readings.load1()
    if load > self.config.max_load1:
      return
    room = self.readings.mem_available_mib() - sum(
      max(0, job.memory_mib - self.readings.rss_mib(job.pid))
      for job in running
      if job.pid is not None
    )
    counts = dict.fromkeys(self.config.queues, 0)
    for job in running:
      counts[job.queue] = counts.get(job.queue, 0) + 1
    queued = select(conn, "state = 'queued'")
    blocked: set[str] = set()
    for job in queued:
      if total >= self.config.max_running:
        return
      cap = self.config.queues.get(job.queue)
      if cap is None or job.queue in blocked or counts[job.queue] >= cap:
        continue
      if room - job.memory_mib < self.config.min_mem_available_mib:
        blocked.add(job.queue)
        if job.id not in self._waiting:
          self._waiting.add(job.id)
          _log.info(
            "job %d: waits in %s for %d MiB, %d MiB free above the floor",
            job.id,
            job.queue,
            job.memory_mib,
            room - self.config.min_mem_available_mib,
          )
        continue
      self._waiting.discard(job.id)
      self._start(conn, job)
      room -= job.memory_mib
      counts[job.queue] += 1
      total += 1

  def _start(self, conn: sqlite3.Connection, job: Job) -> None:
    # Called inside the claiming transaction: the wrapper waits for its
    # commit before it runs the command, and runs nothing when the claim
    # was rolled back.
    log = Path(job.log)
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    wrapper = [
      sys.executable,
      "-m",
      "dataforge.queue.wrapper",
      str(self.config.database.resolve()),
      str(job.id),
    ]
    now = self.clock()
    try:
      fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
      with open(fd, "ab") as out:
        child = subprocess.Popen(
          wrapper,
          stdin=subprocess.DEVNULL,
          stdout=out,
          stderr=out,
          start_new_session=True,
          cwd="/",
        )
    except OSError as error:
      _log.error("job %d: cannot start its wrapper: %s", job.id, error)
      _ = conn.execute(_UNSTARTED, (now, now, job.id))
      return
    self._children.append(child)
    started = leader(child.pid)
    if started is None:
      _log.error("job %d: its wrapper ended before its claim", job.id)
      _ = conn.execute(_UNSTARTED, (now, now, job.id))
      return
    _ = conn.execute(_STARTED, (*started, now, job.id))
    _log.info("job %d: started in %s, pid %d", job.id, job.queue, child.pid)


def serve(config: Config) -> int:
  """Run the scheduler until SIGTERM or SIGINT.

  Args:
    config: The queue file.

  Returns:
    The exit status, 0.
  """
  stop = threading.Event()

  def _stop(_sig: int, _frame: object) -> None:
    stop.set()

  for sig in (signal.SIGTERM, signal.SIGINT):
    _ = signal.signal(sig, _stop)
  _log.info("serving %s, pid %d", config.database, os.getpid())
  Scheduler(config).serve(stop)
  return 0
