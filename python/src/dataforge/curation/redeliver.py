"""Resolve again the deliverables of attempts that have ended.

A record keeps the deliverables its machine declared while the attempt ran.
After a dataset changes what it declares, redeliver gives the record of an
attempt that has ended the deliverables the declaration gives now, resolved
from the same visits and outputs (Machine.deliverables_of), and leaves every
other key's value as it was. It looks first and writes only when asked;
before it writes, it keeps the record's bytes in a backup file beside it,
which restores the record when moved back in its place.

An attempt has ended as the viewer decides (design-viz.md): its status is not
one of not_ended, or, without not_ended, its status is set or its last visit
raised. A record whose last visit has no outcome has not ended in any case:
the engine writes one only while a visit runs.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import stat
from typing import cast, Literal

from dataforge.curation.engine import Attempt, Machine, RecordError

type Action = Literal["skip", "same", "change", "wrote", "error"]


@dataclass(frozen=True)
class Result:
  """What redeliver found, or did, for one record.

  Attributes:
    record: The record file.
    action: "skip" for an attempt that has not ended, "same" when its
      deliverables would not change, "change" when they would and nothing
      was written, "wrote" when they were written, "error" when the record
      could not be read or written.
    why: Why it was skipped or failed; "" else.
    old: The deliverables the record holds.
    new: The deliverables the declaration gives now.
    backup: The copy of the record made before writing it, if one was.
  """

  record: Path
  action: Action
  why: str = ""
  old: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
  new: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
  backup: Path | None = None


def redeliver(
  machine: Machine,
  record: Path,
  *,
  not_ended: Collection[str] | None = None,
  write: bool = False,
  now: datetime | None = None,
) -> Result:
  """Resolve a record's deliverables again, and write them when asked.

  The record's directory is the attempt's root, which its paths are
  relative to. When it writes, it first copies the record's bytes to
  "<record>.<UTC time>.bak" beside it, then replaces the record in one
  rename, after checking that it has not changed since it was read.

  Args:
    machine: The machine as the dataset declares it now.
    record: The record file.
    not_ended: The status words of an attempt that has not ended, as the
      viewer's project file names them; None for none.
    write: Write the record when its deliverables change.
    now: The time that names the backup; None for now.

  Returns:
    What it found or did.
  """
  try:
    raw = _read(record)
    data = cast(object, json.loads(raw))
  except (OSError, ValueError) as error:
    return Result(record, "error", f"cannot read it: {error}")
  if not isinstance(data, dict):
    return Result(record, "error", "the record is not a JSON object")
  data = cast(dict[str, object], data)
  try:
    attempt = Attempt.from_json(record.parent, data)
  except RecordError as error:
    return Result(record, "error", f"not a record: {error}")
  why = _not_ended(data.get("status"), attempt, not_ended)
  if why is not None:
    return Result(record, "skip", why)
  old, new = attempt.deliverables, machine.deliverables_of(attempt)
  if old == new:
    return Result(record, "same", old=old, new=new)
  if not write:
    return Result(record, "change", old=old, new=new)
  stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
  backup = record.with_name(f"{record.name}.{stamp}.bak")
  try:
    if _read(record) != raw:
      why = "it changed while it was read; nothing written"
      return Result(record, "error", why, old, new)
    mode = stat.S_IMODE(os.stat(record).st_mode)
    _write_new(backup, raw, mode)
    temporary = record.with_name(f".{record.name}.{stamp}.tmp")
    _write_new(temporary, _dump(data | {"deliverables": new}, raw), mode)
    os.replace(temporary, record)
  except OSError as error:
    return Result(record, "error", f"cannot write it: {error}", old, new)
  return Result(record, "wrote", old=old, new=new, backup=backup)


def describe(result: Result) -> list[str]:
  """Say in lines what redeliver found or did for one record.

  Args:
    result: What it found or did.

  Returns:
    A first line with the action and the record, then one line for each
    deliverable that changes: "+" added, "-" removed, "~" changed.
  """
  head = f"{result.action} {result.record}"
  if result.why:
    head += f": {result.why}"
  if result.backup is not None:
    head += f" (backup {result.backup})"
  lines = [head]
  if result.action not in ("change", "wrote"):
    return lines
  old = {str(entry["name"]): entry for entry in result.old}
  new = {str(entry["name"]): entry for entry in result.new}
  for name, entry in old.items():
    if name not in new:
      lines.append(f"  - {name}: {_brief(entry)}")
  for name, entry in new.items():
    before = old.get(name)
    if before is None:
      lines.append(f"  + {name}: {_brief(entry)}")
    elif before != entry:
      was, now = _brief(before), _brief(entry)
      change = now if was == now else f"{was} -> {now}"
      lines.append(f"  ~ {name}: {change}")
  return lines


def _not_ended(
  status: object, attempt: Attempt, words: Collection[str] | None
) -> str | None:
  if attempt.visits and attempt.visits[-1].outcome is None:
    return "its last visit has no outcome, so it has not ended"
  if words is not None:
    if status in words:
      return f"its status {status!r} is a word of an attempt not ended"
    return None
  if status is None and not (
    attempt.visits and attempt.visits[-1].outcome == "raised"
  ):
    return "its status is null and its last visit did not raise"
  return None


def _brief(entry: dict[str, object]) -> str:
  where = entry.get("path")
  if where is None:
    where = f"not reached ({entry.get('reason')})"
  text = f"{entry.get('kind')} {entry.get('title')!r}, {where}"
  if entry.get("kind") == "trials":
    value = cast(dict[str, object] | None, entry.get("value"))
    shown = None if value is None else value.get("value")
    text += f", {'-' if shown is None else shown} / {entry.get('k')}"
  return text


def _read(path: Path) -> bytes:
  # The record is the dataset's own file: never one a symlink points to.
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  with os.fdopen(fd, "rb") as file:
    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
      raise OSError(f"{path}: not a regular file")
    return file.read()


def _write_new(path: Path, data: bytes, mode: int) -> None:
  fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
  with os.fdopen(fd, "wb") as file:
    _ = file.write(data)
    file.flush()
    os.fsync(file.fileno())


def _dump(data: dict[str, object], raw: bytes) -> bytes:
  # As the record was written: its indent, ASCII escapes and final newline.
  lines = raw.split(b"\n", 2)
  second = lines[1] if len(lines) > 1 else b""
  indent = len(second) - len(second.lstrip(b" ")) if second.strip() else None
  text = json.dumps(data, indent=indent, ensure_ascii=raw.isascii())
  return text.encode() + (b"\n" if raw.endswith(b"\n") else b"")
