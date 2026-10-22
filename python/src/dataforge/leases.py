"""Leases: what the processes of one machine hold of a capacity they share.

A lease is a file in a directory that holds a whole number, the amount it
holds, and on which its holder keeps an exclusive `flock` until it gives the
lease back. The kernel lets go of that lock when the holder's process ends,
killed by SIGKILL too, so a lease whose lock nobody holds is one nobody holds:
whoever reads the directory next removes it. Leases are taken, given back and
read under a lock on the directory's `.lock`, so that no process changes them
while another reads them.
"""

from collections.abc import Callable, Generator
import contextlib
import fcntl
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import final

from dataforge import stopping

# How often a take that does not fit looks again.
POLL_S = 0.2
_PREFIX = "lease-"


@final
class Lease:
  """One lease, held until `release`."""

  def __init__(self, leases: Leases, path: Path, fd: int, amount: int):
    """Hold a lease `Leases.take` wrote and locked.

    Args:
      leases: Its directory.
      path: Its file.
      fd: The file, opened and locked.
      amount: What it holds.
    """
    self.leases: Leases = leases
    self.path: Path = path
    self.amount: int = amount
    self._fd: int | None = fd
    self._lock: threading.Lock = threading.Lock()

  def release(self) -> None:
    """Give the lease back; once given back, again does nothing."""
    with self._lock:
      fd, self._fd = self._fd, None
    if fd is None:
      return
    with self.leases.locked():
      self.path.unlink(missing_ok=True)
      os.close(fd)


@final
class Leases:
  """The leases of one directory."""

  def __init__(self, path: Path):
    """Use a directory of leases, made mode 0700 when it is missing.

    Args:
      path: The directory.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    self.path: Path = path

  @contextlib.contextmanager
  def locked(self) -> Generator[None]:
    """Hold the directory's lock for the length of the block.

    Yields:
      Nothing; no other process takes, gives back or reads a lease meanwhile.
    """
    with (self.path / ".lock").open("a") as f:
      fcntl.flock(f, fcntl.LOCK_EX)
      yield

  def held(self) -> list[int]:
    """Return the amounts of the leases held now, removing the others.

    Returns:
      One amount per lease.
    """
    with self.locked():
      return self._held()

  def _held(self) -> list[int]:
    # Under the directory's lock: a lease is held while its holder's lock is.
    amounts: list[int] = []
    for path in self.path.glob(f"{_PREFIX}*"):
      try:
        f = path.open("r")
      except FileNotFoundError:
        continue
      with f:
        try:
          fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
          amounts.append(int(f.read() or 0))
          continue
      path.unlink(missing_ok=True)
    return amounts

  def take(
    self, amount: int, fits: Callable[[list[int]], bool]
  ) -> Lease | None:
    """Take a lease when `fits` says the leases held leave room for it.

    Args:
      amount: What the lease holds.
      fits: Given the amounts of the leases held, whether one more fits.

    Returns:
      The lease, or None when it does not fit.
    """
    with self.locked():
      if not fits(self._held()):
        return None
      fd, name = tempfile.mkstemp(dir=self.path, prefix=_PREFIX)
      # A file left unlocked is a lease nobody holds, which the next read
      # removes.
      with contextlib.ExitStack() as undo:
        _ = undo.callback(os.close, fd)
        _ = os.write(fd, str(amount).encode())
        fcntl.flock(fd, fcntl.LOCK_EX)
        _ = undo.pop_all()
      return Lease(self, Path(name), fd, amount)

  def wait(self, amount: int, fits: Callable[[list[int]], bool]) -> Lease:
    """Take a lease, waiting until `fits` says the leases held leave room.

    Args:
      amount: What the lease holds.
      fits: Given the amounts of the leases held, whether one more fits.

    Returns:
      The lease.

    Raises:
      stopping.Stopped: The stop of the call this thread runs was set first.
    """  # noqa: DOC502
    while (lease := self.take(amount, fits)) is None:
      stopping.check()
      time.sleep(POLL_S)
    return lease
