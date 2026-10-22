"""What the scheduler reads of the machine and of its jobs, from /proc.

The scheduler and a job's wrapper also find here the processes below the
wrapper, and signal them.

A job is known by its wrapper, the leader of its session and process group:
the wrapper's pid, its starttime (field 22 of /proc/<pid>/stat, in clock
ticks since boot) and the boot's ID, so that a pid the kernel has given to
another process since, in this boot or a later one, is not taken for the job.
"""

from collections.abc import Iterable
import os
from pathlib import Path
import signal
from typing import NamedTuple, Protocol

_PROC = Path("/proc")
_PAGE = os.sysconf("SC_PAGE_SIZE")
_MIB = 1024 * 1024
_LOOKS = 10


class Leader(NamedTuple):
  """A job's wrapper, as the scheduler recorded it when it started.

  Attributes:
    pid: Its pid, also its process group's and its session's ID.
    start: Its starttime.
    boot: The ID of the boot it ran in.
  """

  pid: int
  start: int
  boot: str


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


def boot_id() -> str:
  """Read the ID of this boot.

  Returns:
    /proc/sys/kernel/random/boot_id.
  """
  return (_PROC / "sys/kernel/random/boot_id").read_text().strip()


def leader(pid: int) -> Leader | None:
  """Record a process as a job's leader.

  Args:
    pid: The process.

  Returns:
    The leader; None when the process is gone or a zombie.
  """
  start = starttime(pid)
  return None if start is None else Leader(pid, start, boot_id())


def alive(job: Leader) -> bool:
  """Tell whether a job's leader runs still.

  Args:
    job: The leader.

  Returns:
    True when the process with its pid is the one it recorded.
  """
  return job.boot == boot_id() and starttime(job.pid) == job.start


def group_alive(job: Leader) -> bool:
  """Tell whether any process of a job's process group runs still.

  The kernel gives no process a pid that is still a process group's ID, so a
  group whose leader is gone keeps its ID until its last process ends. Its
  processes started no earlier than the leader did.

  Args:
    job: The leader.

  Returns:
    True when the leader or another process of its group runs.
  """
  if job.boot != boot_id():
    return False
  if alive(job):
    return True
  if starttime(job.pid) is not None:
    # The pid is another process's now, so the group has ended.
    return False
  for entry in _PROC.iterdir():
    if not entry.name.isdigit():
      continue
    fields = _stat(int(entry.name))
    if (
      fields is not None
      and fields[0] not in ("Z", "X")
      and int(fields[2]) == job.pid
      and int(fields[19]) >= job.start
    ):
      return True
  return False


def signal_group(job: Leader, sig: signal.Signals) -> bool:
  """Send a signal to a job's process group, if any process of it runs.

  Args:
    job: The leader.
    sig: The signal.

  Returns:
    True when the signal was sent.
  """
  if not group_alive(job):
    return False
  try:
    os.killpg(job.pid, sig)
  except ProcessLookupError:
    return False
  return True


class Process(NamedTuple):
  """A process, known by its pid and starttime.

  Attributes:
    pid: Its pid.
    start: Its starttime.
  """

  pid: int
  start: int


def descendants(root: int) -> dict[Process, int]:
  """Find the processes below one, by the parent /proc gives each.

  A process whose parent ends goes to the nearest subreaper above it, or to
  init: it is below root only while root or a process below it adopts it.

  Args:
    root: The process.

  Returns:
    Each process below root that has not ended, with its process group's
    ID.
  """
  children: dict[int, list[tuple[Process, int]]] = {}
  for entry in _PROC.iterdir():
    if not entry.name.isdigit():
      continue
    fields = _stat(int(entry.name))
    if fields is None or fields[0] in ("Z", "X"):
      continue
    process = Process(int(entry.name), int(fields[19]))
    children.setdefault(int(fields[1]), []).append((process, int(fields[2])))
  found: dict[Process, int] = {}
  parents = [root]
  while parents:
    for process, group in children.pop(parents.pop(), []):
      found[process] = group
      parents.append(process.pid)
  return found


def send(processes: Iterable[Process], sig: signal.Signals) -> set[Process]:
  """Send a signal to each of some processes that has not ended.

  Each gets it through a pidfd opened while its pid and starttime were its
  own, so that a process given its pid since gets nothing.

  Args:
    processes: The processes.
    sig: The signal.

  Returns:
    Those that the signal may not be sent to, such as one of another user.
  """
  refused: set[Process] = set()
  for process in processes:
    try:
      fd = os.pidfd_open(process.pid)
    except ProcessLookupError:
      continue
    try:
      if starttime(process.pid) == process.start:
        signal.pidfd_send_signal(fd, sig)
    except ProcessLookupError:
      pass
    except PermissionError:
      refused.add(process)
    finally:
      os.close(fd)
  return refused


def kill_below(root: int) -> set[Process]:
  """Send SIGKILL to every process below one.

  It looks again for what the processes it sent SIGKILL forked before it
  reached them, until a look finds none it has not sent it, or for at most
  _LOOKS looks; a process forks nothing once SIGKILL is pending.

  Args:
    root: The process.

  Returns:
    Those that SIGKILL may not be sent to.
  """
  sent: set[Process] = set()
  refused: set[Process] = set()
  for _ in range(_LOOKS):
    new = [p for p in descendants(root) if p not in sent]
    if not new:
      break
    refused |= send(new, signal.SIGKILL)
    sent.update(new)
  return refused


def kill_job(job: Leader) -> bool:
  """Send SIGKILL to a job: every process below its wrapper, then its group.

  A wrapper that runs is stopped first, so that it neither writes the job's
  end before its group is killed nor ends before the processes it adopted.

  Args:
    job: The leader.

  Returns:
    True when SIGKILL was sent to the group.
  """
  try:
    if alive(job):
      _ = send([Process(job.pid, job.start)], signal.SIGSTOP)
      _ = kill_below(job.pid)
  finally:
    sent = signal_group(job, signal.SIGKILL)
  return sent


def _stat(pid: int) -> list[str] | None:
  # The fields after the command's name, which may hold spaces and parentheses:
  # the process's state is the first, field 3 of proc_pid_stat(5).
  try:
    text = (_PROC / str(pid) / "stat").read_text()
  except FileNotFoundError, ProcessLookupError:
    return None
  return text[text.rindex(")") + 2 :].split()
