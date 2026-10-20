"""Whether an attempt has ended, and whether one that has not still runs.

A dataset may hold a lock on a file in an item's directory while one of the
item's attempts runs. The viewer learns whether that lock is held only from
/proc/locks, by the file's device and inode, and never takes it: an attempt
that tried to take it at that moment would find it busy and fail.
"""

import logging
import os
from pathlib import Path
import re
import stat

from dataforge.viz.config import Project
from dataforge.viz.paths import stat_lock

PROC_LOCKS = Path("/proc/locks")
"""Where the kernel lists the locks held on files."""

RUNNING = "running"
MAY_HAVE_STOPPED = "may have stopped"
NOT_ENDED = "not ended"

_log = logging.getLogger(__name__)

_FILE = re.compile(r"([0-9a-f]+):([0-9a-f]+):([0-9]+)")


def has_ended(
  project: Project, status: str | None, outcome: str | None
) -> bool:
  """Say whether an attempt has ended.

  Args:
    project: The project, which may name the status words of an attempt that
      has not ended.
    status: The record's status.
    outcome: The outcome of the record's last visit.

  Returns:
    False when the status is one of the project's not_ended; without
    not_ended, when the status is None and the last visit was not raised.
  """
  if project.not_ended is not None:
    return status not in project.not_ended
  return status is not None or outcome == "raised"


def held_locks(text: str) -> set[tuple[int, int, int]]:
  """Read the files that hold a lock from the text of /proc/locks.

  Args:
    text: The text of /proc/locks.

  Returns:
    Each locked file as (major, minor, inode); a lock that is only waited
    for, a line with "->", does not count.
  """
  held: set[tuple[int, int, int]] = set()
  for line in text.splitlines():
    fields = line.split()
    if "->" in fields:
      continue
    for field in fields:
      if found := _FILE.fullmatch(field):
        major, minor, inode = found.groups()
        held.add((int(major, 16), int(minor, 16), int(inode)))
        break
  return held


def read_held_locks() -> set[tuple[int, int, int]]:
  """Read the files that hold a lock now.

  Returns:
    Each locked file as (major, minor, inode); none when /proc/locks cannot
    be read.
  """
  try:
    return held_locks(PROC_LOCKS.read_text())
  except OSError as error:
    _log.warning("%s: %s; no lock counts as held", PROC_LOCKS, error.strerror)
    return set()


def is_held(
  project: Project, item: str, held: set[tuple[int, int, int]]
) -> bool:
  """Say whether an item's lock file is held.

  Args:
    project: The project, which names the lock file.
    item: The item.
    held: The locked files, from held_locks.

  Returns:
    True when the lock file is a regular file that one of held names.
  """
  found = stat_lock(project, item)
  if found is None or not stat.S_ISREG(found.st_mode):
    return False
  key = (os.major(found.st_dev), os.minor(found.st_dev), found.st_ino)
  return key in held


def mark(project: Project, rows: list[dict[str, object]]) -> None:
  """Set held on every row of the listing, and live on those not ended.

  Without a lock file, held stays None and live "not ended". With one, held
  says whether the row's item's lock is held, a record that cannot be read
  included, and a row not ended is "running" only when its item's lock is
  held and it is the item's newest attempt by modified, since one lock covers
  the whole item; every other is "may have stopped".

  Args:
    project: The project.
    rows: The listing, newest modified first, live "not ended" or None; a
      row whose record could not be read has an error and is no item's
      newest.
  """
  if project.lock is None:
    return
  held = read_held_locks()
  locked: dict[str, bool] = {}
  seen: set[str] = set()
  for row in rows:
    item = str(row["item"])
    if item not in locked:
      locked[item] = is_held(project, item, held)
    row["held"] = locked[item]
    if row["error"] is not None:
      continue
    newest = item not in seen
    seen.add(item)
    if row["live"] is None:
      continue
    row["live"] = RUNNING if newest and locked[item] else MAY_HAVE_STOPPED
