"""Keep the processes of a run off its reports and off the harness.

`core.run` uses it when it is given an `Isolation`; a dataset's harness may
use `confine` and `seal` for its own steps too. Two parts:

- `confine` puts a command, before it starts, in a Landlock domain of its
  own that may change files only beneath the paths it is given: there it
  may write, make, remove, move and truncate files, and nowhere else.
  Reading is left alone. A file outside those paths cannot be opened for
  writing by its path, nor again through `/proc/<pid>/fd`; a descriptor
  opened before the command started still writes. A process in the domain
  also cannot reach the `/proc` files, such as the open files, of a
  process outside it, as the harness that started it is.
- `seal` kills every process descended from a root but one process and
  its ancestors, so that what a command left running cannot change what
  the command wrote once it is read. The root is made a child subreaper
  (`subreaper`), so a process that leaves its parent, by `setsid` or a
  double fork, stays its descendant.

`confine` runs in the child between fork and exec (`preexec_fn`), as
nothing may be read from disk once the child is the command's: a wrapper
started from disk would load code the command's side may have changed.
So this module loads all it needs before the fork.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import ctypes
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import time
from typing import cast

_LIBC = ctypes.CDLL(None, use_errno=True)
_CREATE_RULESET, _ADD_RULE, _RESTRICT_SELF = 444, 445, 446
# landlock_create_ruleset's flag that asks for the ABI's version.
_VERSION = 1
_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_CHILD_SUBREAPER = 36

# Landlock's rights that change files, by bit: write a file, remove a
# directory or a file, make a character device, a directory, a regular
# file, a socket, a FIFO, a block device or a link (ABI 1), move a file to
# another directory (2) and truncate one (3).
_CHANGES = sum(1 << b for b in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14))
# Those a rule on a file, not a directory, may give.
_FILE_RIGHTS = 1 << 1 | 1 << 14

MIN_ABI = 3
"""The oldest Landlock ABI `confine` uses, the first that covers every
right above, truncation among them."""

TRIES = 100
"""Looks `seal` takes, `PAUSE` seconds apart, before it gives up."""

PAUSE = 0.05

PROC = Path("/proc")
"""Where /proc is."""


class UnavailableError(OSError):
  """The kernel cannot confine a command."""


def abi() -> int:
  """Return the Landlock ABI the kernel has, 0 when it has none.

  Returns:
    The ABI's version.
  """
  found = cast("int", _LIBC.syscall(_CREATE_RULESET, None, 0, _VERSION))
  return max(found, 0)


def _call(number: int, *args: object) -> int:
  # A raw system call, its error raised.
  done = cast("int", _LIBC.syscall(number, *args))
  if done < 0:
    errno = ctypes.get_errno()
    raise OSError(errno, os.strerror(errno))
  return done


def _prctl(option: int) -> None:
  # Sets a prctl option to 1, its error raised.
  if _LIBC.prctl(option, 1, 0, 0, 0) != 0:
    errno = ctypes.get_errno()
    raise OSError(errno, os.strerror(errno))


def confine(writable: Sequence[str]) -> None:
  """Let this process and what it starts change files only beneath writable.

  Run it in the child, before it runs the command (`preexec_fn`). A path
  that is not there is left out. It sets `no_new_privs` itself, which
  Landlock needs, whether or not the container did.

  Args:
    writable: The paths beneath which files may change.

  Raises:
    UnavailableError: The kernel has no Landlock of ABI `MIN_ABI` or later.
  """  # noqa: DOC502
  version = abi()
  if version < MIN_ABI:
    raise UnavailableError(f"Landlock ABI {version}; {MIN_ABI} or later needed")
  attr = ctypes.create_string_buffer(struct.pack("Q", _CHANGES))
  ruleset = _call(_CREATE_RULESET, attr, len(attr.raw), 0)
  try:
    for path in writable:
      try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
      except OSError:
        continue
      try:
        mode = os.fstat(fd).st_mode
        given = _CHANGES if stat.S_ISDIR(mode) else _FILE_RIGHTS
        rule = ctypes.create_string_buffer(struct.pack("=Qi", given, fd))
        _ = _call(_ADD_RULE, ruleset, _PATH_BENEATH, rule, 0)
      finally:
        os.close(fd)
    _prctl(_PR_SET_NO_NEW_PRIVS)
    _ = _call(_RESTRICT_SELF, ruleset, 0)
  finally:
    os.close(ruleset)


def subreaper() -> None:
  """Make this process the parent of its orphaned descendants.

  Raises:
    OSError: The kernel refused.
  """  # noqa: DOC502
  _prctl(_PR_SET_CHILD_SUBREAPER)


def _stat(proc: Path, pid: int) -> tuple[int, str, int]:
  # A process's parent, state and start time, as /proc gives them; the
  # command name in parentheses may hold spaces and parentheses.
  fields = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
  return int(fields[1]), fields[0], int(fields[19])


def table(proc: Path = PROC) -> dict[int, tuple[int, str, int]]:
  """Return each process's parent, state and start time.

  Args:
    proc: Where /proc is.

  Returns:
    Them by PID.
  """
  found: dict[int, tuple[int, str, int]] = {}
  for entry in proc.iterdir():
    if entry.name.isdigit():
      try:
        found[int(entry.name)] = _stat(proc, int(entry.name))
      except (OSError, IndexError, ValueError):
        continue
  return found


def descendants(root: int, by_pid: dict[int, tuple[int, str, int]]) -> set[int]:
  """Return the processes under a root, by their parents.

  Args:
    root: The root's PID.
    by_pid: Each process's parent, state and start time (`table`).

  Returns:
    Their PIDs, the root's not among them.
  """
  children: dict[int, list[int]] = {}
  for pid, (ppid, _, _) in by_pid.items():
    children.setdefault(ppid, []).append(pid)
  found: set[int] = set()
  todo = [root]
  while todo:
    for child in children.get(todo.pop(), []):
      if child not in found and child != root:
        found.add(child)
        todo.append(child)
  return found


def seal(
  root: int,
  keep: int | None = None,
  *,
  proc: Path = PROC,
  kill: Callable[[int, int], None] | None = None,
) -> list[int]:
  """Kill each process under root but keep and its ancestors, until none run.

  A zombie runs nothing and is left to its parent.

  Args:
    root: The PID whose descendants are killed; it should be a child
      subreaper (`subreaper`), so that none of them leaves it.
    keep: A PID under root that stays with its ancestors, or None.
    proc: Where /proc is.
    kill: Kills a PID that started at a time, /proc's start time; the
      default does only while that PID is still that process.

  Returns:
    The PIDs killed, in order, each as often as it was.

  Raises:
    RuntimeError: Processes still ran after `TRIES` looks.
  """
  send = kill or _kill
  killed: list[int] = []
  left: list[int] = []
  for _ in range(TRIES):
    by_pid = table(proc)
    spare = {root}
    pid = keep
    while pid is not None and pid in by_pid and pid not in spare:
      spare.add(pid)
      pid = by_pid[pid][0]
    under = descendants(root, by_pid) - spare
    left = sorted(p for p in under if by_pid[p][1] not in ("Z", "X"))
    if not left:
      return killed
    for pid in left:
      send(pid, by_pid[pid][2])
      killed.append(pid)
    time.sleep(PAUSE)
  raise RuntimeError(f"processes still run: {left}")


def _kill(pid: int, start: int) -> None:
  # Kills pid while it is the process that started at start, through a
  # pidfd, which stays that process's.
  try:
    fd = os.pidfd_open(pid)
  except OSError:
    return
  try:
    if _stat(PROC, pid)[2] == start:
      signal.pidfd_send_signal(fd, signal.SIGKILL)
  except (OSError, IndexError, ValueError):
    pass
  finally:
    os.close(fd)


def reap_one(spare: int) -> bool:
  """Wait for one ended child of this process, unless it is spare.

  A `subprocess` that waits for spare keeps its status.

  Args:
    spare: A child left for another to wait for.

  Returns:
    Whether a child was waited for.
  """
  try:
    found = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
  except ChildProcessError:
    return False
  if found is None or found.si_pid == spare:
    return False
  _ = os.waitpid(found.si_pid, 0)
  return True


def wait(command: subprocess.Popen[bytes]) -> int:
  """Wait for a command, and meanwhile for each other child that ends.

  The orphans of a run that a child subreaper takes would otherwise stay
  its zombies until it waits for them, and fill the process limit.

  Args:
    command: The command, a child of this process.

  Returns:
    Its exit status, as `subprocess` gives it.
  """
  while command.poll() is None:
    if not reap_one(command.pid):
      time.sleep(PAUSE)
  return command.returncode


def reap(children: Sequence[int] = ()) -> None:
  """Wait for each child of this process that has ended, but those given.

  Call it only when no `subprocess` waits for a child of this process, or
  name that child: reaping it first would lose its status.

  Args:
    children: Children whose status another waits for, left alone.
  """
  for pid, (ppid, state, _) in table().items():
    if ppid == os.getpid() and state == "Z" and pid not in children:
      try:
        _ = os.waitpid(pid, os.WNOHANG)
      except ChildProcessError:
        continue
