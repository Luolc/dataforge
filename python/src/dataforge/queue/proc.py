"""What the scheduler reads of the machine and of its jobs, from /proc.

A job is known by its pid together with the pid's starttime (field 22 of
/proc/<pid>/stat, in clock ticks since boot), so that a pid the kernel has
given to another process since is not taken for the job.
"""

import os
from pathlib import Path
import signal
from typing import Protocol

_PROC = Path("/proc")
_PAGE = os.sysconf("SC_PAGE_SIZE")
_MIB = 1024 * 1024


class Readings(Protocol):
  """The readings admission rests on; tests give their own."""

  def mem_available_mib(self) -> int:
    """Read MemAvailable of /proc/meminfo, in MiB.

    Returns:
      The reading.
    """
    ...

  def load1(self) -> float:
    """Read the 1-minute load of /proc/loadavg.

    Returns:
      The reading.
    """
    ...

  def rss_mib(self, session: int) -> int:
    """Sum the resident memory of a session's processes, in MiB.

    Args:
      session: The session's ID, its leader's pid.

    Returns:
      The sum over the processes alive now.
    """
    ...


class ProcReadings:
  """The machine's readings, from /proc."""

  def mem_available_mib(self) -> int:
    """Read MemAvailable of /proc/meminfo, in MiB.

    Returns:
      The reading.

    Raises:
      ValueError: When /proc/meminfo has no MemAvailable.
    """
    for line in (_PROC / "meminfo").read_text().splitlines():
      name, _, value = line.partition(":")
      if name == "MemAvailable":
        return int(value.split()[0]) // 1024
    raise ValueError("/proc/meminfo has no MemAvailable")

  def load1(self) -> float:
    """Read the 1-minute load of /proc/loadavg.

    Returns:
      The reading.
    """
    return float((_PROC / "loadavg").read_text().split()[0])

  def rss_mib(self, session: int) -> int:
    """Sum the resident memory of a session's processes, in MiB.

    Args:
      session: The session's ID, its leader's pid.

    Returns:
      The sum over the processes alive now.
    """
    pages = 0
    for entry in _PROC.iterdir():
      if not entry.name.isdigit():
        continue
      fields = _stat(int(entry.name))
      if fields is not None and int(fields[3]) == session:
        pages += int(fields[21])
    return pages * _PAGE // _MIB


def starttime(pid: int) -> int | None:
  """Read the starttime of a process that has not ended.

  Args:
    pid: The process.

  Returns:
    Field 22 of its stat; None when it is gone or a zombie.
  """
  fields = _stat(pid)
  if fields is None or fields[0] in ("Z", "X"):
    return None
  return int(fields[19])


def alive(pid: int, start: int) -> bool:
  """Tell whether a process is the one with this starttime and runs still.

  Args:
    pid: The process.
    start: Its starttime when it was recorded.

  Returns:
    True when it runs still.
  """
  return starttime(pid) == start


def signal_group(pid: int, start: int, sig: signal.Signals) -> bool:
  """Send a signal to the process group a job leads, if the job runs still.

  Args:
    pid: The job's pid, the leader of its session and process group.
    start: Its starttime.
    sig: The signal.

  Returns:
    True when the signal was sent.
  """
  if not alive(pid, start):
    return False
  try:
    os.killpg(pid, sig)
  except ProcessLookupError:
    return False
  return True


def _stat(pid: int) -> list[str] | None:
  # The fields after the command's name, which may hold spaces and parentheses:
  # the process's state is the first, field 3 of proc_pid_stat(5).
  try:
    text = (_PROC / str(pid) / "stat").read_text()
  except FileNotFoundError, ProcessLookupError:
    return None
  return text[text.rindex(")") + 2 :].split()
