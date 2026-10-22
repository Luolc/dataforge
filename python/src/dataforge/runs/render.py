"""An attempt's record file: what its row renders to, and reading it back.

The runner writes the record file in the attempt's root at each step and
before it writes the outcome, as the dataset's Hooks.render gives it, so that
a person or a viewer reads the attempt there. Programs decide by the store;
the one read of the file is reconcile's (newer): when its holder could not
write the store, the file may hold a newer record and its machine_status.
"""

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import cast

from dataforge.runs.store import AttemptRow


def render(row: AttemptRow) -> dict[str, object]:
  """Render a row as its record file, the default of Hooks.render.

  Args:
    row: The row.

  Returns:
    The row's record with the dataset's extra fields, and over them `status`,
    the outcome once it has ended, else the row's state; `machine_status`,
    the record's own status; and `reason`, the row's once it has ended, else
    the record's.
  """
  rendered = {**(row.record or {}), **row.extra}
  ended = row.state == "ended"
  rendered["status"] = row.outcome if ended else row.state
  rendered["machine_status"] = row.machine_status
  rendered["reason"] = row.reason if ended else rendered.get("reason")
  return rendered


def write(path: Path, rendered: Mapping[str, object]) -> None:
  """Write a record file whole, so that no reader sees it half written.

  Args:
    path: The file.
    rendered: What it holds.

  Raises:
    OSError: When it cannot be written; the file is then as it was.
  """  # noqa: DOC502
  part = path.with_name(f".{path.name}.{os.getpid()}.part")
  try:
    _ = part.write_text(json.dumps(rendered, indent=2) + "\n")
    _ = part.replace(path)
  finally:
    part.unlink(missing_ok=True)


def newer(row: AttemptRow, path: Path) -> dict[str, object] | None:
  """Read from a row's record file a record newer than the row's.

  The file is newer when its record has more visits than the row's, or a
  machine_status where the row has none. Only its record and its
  machine_status are read, never its status; the record's status is taken
  from machine_status.

  Args:
    row: The row.
    path: Its record file, which is not followed when it is a symlink.

  Returns:
    The file's record, its status the file's machine_status, when it is
    newer; None when it is not, or cannot be read as a record file.
  """
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as file:
      data = cast(object, json.load(file))
  except OSError, ValueError:
    return None
  if not isinstance(data, dict):
    return None
  record = cast(dict[str, object], data)
  status = record.get("machine_status")
  visits = record.get("visits")
  if not isinstance(visits, list) or not (
    status is None or isinstance(status, str)
  ):
    return None
  record = {**record, "status": status}
  mine = row.record or {}
  more = len(cast(list[object], visits)) > len(
    cast(list[object], mine.get("visits") or [])
  )
  if more or (status is not None and row.machine_status is None):
    return record
  return None
