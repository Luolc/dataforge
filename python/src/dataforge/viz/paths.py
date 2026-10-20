"""Opening an attempt's directory and the paths in it.

What an attempt holds was written by agents, the code under test and
containers. A path is opened one component at a time from the project's
attempts_dir with O_NOFOLLOW, so no symlink under it is followed and no read
leaves the attempt's directory.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
import stat

from dataforge.viz.config import Project, split_relative

RECORD_LIMIT = 64 * 1024 * 1024
"""The most bytes of a record file the viewer reads."""

FILE_LIMIT = 16 * 1024 * 1024
"""The most bytes of a file the viewer serves."""

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: opening a FIFO an attempt left must not hang the request.
_ANY = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class RefusedError(Exception):
  """A request the viewer refuses, with its HTTP status and reason."""

  def __init__(self, status: int, reason: str) -> None:
    """Make the refusal.

    Args:
      status: The HTTP status, 400, 404, 413 or 503.
      reason: Why, for the person reading it.
    """
    super().__init__(reason)
    self.status: int = status
    self.reason: str = reason


@dataclass(frozen=True, slots=True)
class Entry:
  """One entry of a directory listing.

  Attributes:
    name: Its name in the directory.
    type: "file", "dir", "symlink" or "other".
    size: A file's size in bytes; None for any other type.
  """

  name: str
  type: str
  size: int | None


@dataclass(frozen=True, slots=True)
class Read:
  """A regular file's bytes and when it was last modified.

  Attributes:
    data: The bytes.
    mtime: The modification time, in seconds since the epoch.
  """

  data: bytes
  mtime: float


def attempt_parts(project: Project, attempt: str) -> tuple[str, ...]:
  """Check an attempt ID against the project's attempts pattern.

  Args:
    project: The project.
    attempt: The attempt's ID, its path relative to attempts_dir.

  Returns:
    The ID's components.

  Raises:
    RefusedError: When the ID is not a relative path that matches the pattern.
  """
  parts = split_relative(attempt)
  if (
    not parts
    or len(parts) != len(project.attempts)
    or not all(
      _matches(a, b) for a, b in zip(project.attempts, parts, strict=True)
    )
  ):
    raise RefusedError(404, f"{attempt!r} is not an attempt of {project.id}")
  return parts


def list_attempts(project: Project) -> list[str]:
  """List the IDs of the directories that match the attempts pattern.

  Args:
    project: The project.

  Returns:
    The attempt IDs, sorted.
  """
  found: list[str] = []
  with _open_attempts_dir(project) as top:
    _walk(top, project.attempts, (), found)
  return sorted(found)


@contextmanager
def open_attempt(project: Project, attempt: str) -> Generator[int]:
  """Open an attempt's directory.

  Args:
    project: The project.
    attempt: The attempt's ID.

  Yields:
    A file descriptor of the directory, closed on exit.

  Raises:
    RefusedError: When the ID is refused or its directory cannot be opened.
  """  # noqa: DOC502
  parts = attempt_parts(project, attempt)
  with _open_attempts_dir(project) as top, _open_at(top, parts, _DIR) as fd:
    yield fd


@contextmanager
def open_path(attempt: int, path: str) -> Generator[int]:
  """Open a path in an attempt's directory, a file or a directory.

  Args:
    attempt: The attempt's directory.
    path: The path relative to it, "" for the directory itself.

  Yields:
    A file descriptor, opened without following a symlink and closed on exit.

  Raises:
    RefusedError: When the path is refused or cannot be opened.
  """
  parts = split_relative(path)
  if parts is None:
    raise RefusedError(404, f"{path!r} is not a path in the attempt")
  if not parts:
    yield attempt
    return
  with _open_at(attempt, parts, _ANY) as fd:
    yield fd


def read_record(project: Project, attempt: int) -> Read:
  """Read an attempt's record file as it is written.

  Args:
    project: The project, which names the record file.
    attempt: The attempt's directory.

  Returns:
    The record's bytes.

  Raises:
    RefusedError: When the record is missing, not a regular file or too large.
  """  # noqa: DOC502
  with _open_at(attempt, project.record, _ANY) as fd:
    return read_regular(fd, RECORD_LIMIT)


def stat_lock(project: Project, item: str) -> os.stat_result | None:
  """Stat an item's lock file without following a symlink.

  Args:
    project: The project, which names the lock file.
    item: The item, an attempt ID's first item.depth components.

  Returns:
    The lock file's status; None when the project names no lock file, or
    it or a directory on its way is missing or a symlink.
  """
  parts = split_relative(item)
  if project.lock is None or not parts:
    return None
  *dirs, name = (*parts, *project.lock)
  try:
    with (
      _open_attempts_dir(project) as top,
      _open_at(top, tuple(dirs), _DIR) as fd,
    ):
      return os.stat(name, dir_fd=fd, follow_symlinks=False)
  except RefusedError, OSError:
    return None


def read_regular(fd: int, limit: int) -> Read:
  """Read a regular file of at most limit bytes.

  Args:
    fd: The open file.
    limit: The most bytes it may hold.

  Returns:
    Its bytes.

  Raises:
    RefusedError: When it is not a regular file, or holds more than limit bytes.
  """
  status = os.fstat(fd)
  if not stat.S_ISREG(status.st_mode):
    raise RefusedError(404, "not a regular file")
  if status.st_size > limit:
    raise RefusedError(413, f"{status.st_size} bytes, over {limit}")
  chunks: list[bytes] = []
  size = 0
  while chunk := os.read(fd, min(1 << 20, limit + 1 - size)):
    chunks.append(chunk)
    size += len(chunk)
    if size > limit:
      raise RefusedError(413, f"over {limit} bytes, grown while read")
  return Read(b"".join(chunks), status.st_mtime)


def list_dir(fd: int) -> list[Entry]:
  """List a directory without following any symlink in it.

  Args:
    fd: The open directory.

  Returns:
    Its entries, sorted by name; a symlink has no size and no target.
  """
  entries: list[Entry] = []
  with os.scandir(fd) as it:
    for item in it:
      if item.is_symlink():
        entries.append(Entry(item.name, "symlink", None))
      elif item.is_dir(follow_symlinks=False):
        entries.append(Entry(item.name, "dir", None))
      elif item.is_file(follow_symlinks=False):
        size = item.stat(follow_symlinks=False).st_size
        entries.append(Entry(item.name, "file", size))
      else:
        entries.append(Entry(item.name, "other", None))
  return sorted(entries, key=lambda e: e.name)


def is_dir(fd: int) -> bool:
  """Say whether an open file is a directory.

  Args:
    fd: The open file.

  Returns:
    True for a directory.
  """
  return stat.S_ISDIR(os.fstat(fd).st_mode)


def _matches(pattern: str, part: str) -> bool:
  return part == pattern or (pattern == "*" and not part.startswith("."))


@contextmanager
def _open_attempts_dir(project: Project) -> Generator[int]:
  # attempts_dir is configuration, opened as given; only what is under it
  # is opened component by component.
  try:
    fd = os.open(
      project.attempts_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    )
  except OSError as error:
    raise RefusedError(404, f"attempts_dir: {error.strerror}") from None
  try:
    yield fd
  finally:
    os.close(fd)


@contextmanager
def _open_at(base: int, parts: tuple[str, ...], last: int) -> Generator[int]:
  fd = base
  try:
    for i, part in enumerate(parts):
      flags = last if i == len(parts) - 1 else _DIR
      try:
        child = os.open(part, flags, dir_fd=fd)
      except OSError as error:
        raise RefusedError(404, _why("/".join(parts[: i + 1]), error)) from None
      if fd != base:
        os.close(fd)
      fd = child
    yield fd
  finally:
    if fd != base:
      os.close(fd)


def _why(path: str, error: OSError) -> str:
  if error.errno in (errno.ELOOP, errno.ENOTDIR):
    return f"{path}: a symlink or not a directory, which is never followed"
  return f"{path}: {error.strerror}"


def _walk(
  fd: int, pattern: tuple[str, ...], at: tuple[str, ...], found: list[str]
) -> None:
  if not pattern:
    found.append("/".join(at))
    return
  with os.scandir(fd) as it:
    names = [
      e.name
      for e in it
      if e.is_dir(follow_symlinks=False) and _matches(pattern[0], e.name)
    ]
  for name in names:
    try:
      child = os.open(name, _DIR, dir_fd=fd)
    except OSError:
      # Removed or replaced since it was listed: not an attempt now.
      continue
    try:
      _walk(child, pattern[1:], (*at, name), found)
    finally:
      os.close(child)
