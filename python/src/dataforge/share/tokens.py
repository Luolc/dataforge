"""The access tokens of share, in a SQLite database.

A token is "dfs_<id>_<secret>": id is 8 hex digits, the public half by which
the database finds its row, and secret 32 random bytes in base64url. The
database keeps only the SHA-256 of the secret, never the token, and a check
compares that hash in constant time. Only the token commands write the
database; the server opens it read-only.
"""

from collections.abc import Generator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import cast

DAYS = 14
"""How many days a token is valid for when its command names none."""

MOST_DAYS = 365
"""The most days a token may be valid for."""

PROJECT = re.compile(r"[a-z0-9-]+")
"""What a project's ID matches, as a whole."""

LABEL = re.compile(r"[A-Za-z0-9._-]{1,64}")
"""What a token's label matches, as a whole: a code for who holds it, not
their name."""

_TOKEN = re.compile(r"dfs_([0-9a-f]{8})_([A-Za-z0-9_-]{43})")
_TRIES = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token (
  id TEXT PRIMARY KEY,
  secret_sha256 BLOB NOT NULL,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS token_project (
  token_id TEXT NOT NULL REFERENCES token (id),
  project TEXT NOT NULL,
  PRIMARY KEY (token_id, project)
);
"""


class NoSuchTokenError(Exception):
  """A token ID the database does not hold."""


@dataclass(frozen=True, slots=True)
class Token:
  """A token as the database holds it, without its secret or hash.

  Attributes:
    id: Its public half.
    label: A code for who holds it.
    projects: The IDs of the projects it may see, sorted.
    created_at: When it was made.
    expires_at: When it stops being valid.
    revoked_at: When it was revoked; None while it is not.
  """

  id: str
  label: str
  projects: tuple[str, ...]
  created_at: datetime
  expires_at: datetime
  revoked_at: datetime | None

  def state(self, now: datetime) -> str:
    """Say whether the token is valid at a time.

    Args:
      now: The time.

    Returns:
      "revoked", "expired" or "valid".
    """
    if self.revoked_at is not None:
      return "revoked"
    return "valid" if now < self.expires_at else "expired"


def create(
  db: Path,
  projects: Sequence[str],
  label: str,
  days: int = DAYS,
  now: datetime | None = None,
) -> tuple[str, Token]:
  """Make a token, and the database first when there is none.

  A new database file is made readable and writable by its owner alone.

  Args:
    db: The database.
    projects: The IDs of the projects the token may see, at least one.
    label: A code for who holds it, matching LABEL.
    days: How many days it is valid for, from 1 to MOST_DAYS.
    now: When it is made; None for the current time.

  Returns:
    The token, which the database does not keep and nothing can show
    again, and its row.

  Raises:
    ValueError: When an argument fails its check.
    sqlite3.IntegrityError: When every new ID drawn was taken already.
  """  # noqa: DOC503
  wanted = tuple(sorted(set(projects)))
  if not wanted:
    raise ValueError("a token needs a project")
  for project in wanted:
    if not PROJECT.fullmatch(project):
      raise ValueError(f"project {project!r} is not of [a-z0-9-]")
  if not LABEL.fullmatch(label):
    raise ValueError(
      f"label {label!r} is not 1 to 64 of [A-Za-z0-9._-]: a code, not a name"
    )
  if not 1 <= days <= MOST_DAYS:
    raise ValueError(f"days {days} is not from 1 to {MOST_DAYS}")
  created = (now or datetime.now(UTC)).replace(microsecond=0)
  expires = created + timedelta(days=days)
  try:
    fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    os.close(fd)
  except FileExistsError:
    pass
  with _connect(db, "rw") as connection:
    _ = connection.executescript(_SCHEMA)
    for _ in range(_TRIES - 1):
      try:
        return _insert(connection, wanted, label, created, expires)
      except sqlite3.IntegrityError:
        continue
    return _insert(connection, wanted, label, created, expires)


def tokens(db: Path) -> list[Token]:
  """List the tokens of a database.

  Args:
    db: The database, which must exist.

  Returns:
    Every token, the newest first.
  """
  with _connect(db, "ro") as connection:
    rows = _all(connection, "SELECT id FROM token ORDER BY created_at DESC, id")
    return [_row(connection, cast(str, row[0])) for row in rows]


def revoke(db: Path, id_: str, now: datetime | None = None) -> bool:
  """Revoke a token, which is refused from then on.

  Args:
    db: The database, which must exist.
    id_: The token's ID.
    now: When it is revoked; None for the current time.

  Returns:
    True when it is revoked now; False when it was revoked before.

  Raises:
    NoSuchTokenError: When the database holds no token of that ID.
  """
  when = _text((now or datetime.now(UTC)).replace(microsecond=0))
  with _connect(db, "rw") as connection, connection:
    found = _one(connection, "SELECT revoked_at FROM token WHERE id = ?", id_)
    if found is None:
      raise NoSuchTokenError(id_)
    if found[0] is not None:
      return False
    _ = connection.execute(
      "UPDATE token SET revoked_at = ? WHERE id = ?", (when, id_)
    )
    return True


def check(db: Path, token: str, now: datetime) -> Token | None:
  """Find a token that is valid now.

  The database is opened read-only. Every reason a token is refused gives
  the same None: it is not of the form, has no row, its secret's hash
  differs, it has expired or it was revoked.

  Args:
    db: The database, which must exist.
    token: The token as a person gave it.
    now: The time.

  Returns:
    The token's row; None when it is refused.
  """
  match = _TOKEN.fullmatch(token)
  if match is None:
    return None
  id_, secret = match.groups()
  with _connect(db, "ro") as connection:
    found = _one(
      connection, "SELECT secret_sha256 FROM token WHERE id = ?", id_
    )
    if found is None:
      return None
    stored = found[0]
    if not isinstance(stored, bytes) or not hmac.compare_digest(
      _hash(secret), stored
    ):
      return None
    row = _row(connection, id_)
  return row if row.state(now) == "valid" else None


def readable(db: Path) -> None:
  """Check that the server can read a database.

  Args:
    db: The database.

  Raises:
    sqlite3.Error: When it cannot be opened read-only or lacks a table.
  """  # noqa: DOC502
  with _connect(db, "ro") as connection:
    _ = connection.execute("SELECT 1 FROM token, token_project LIMIT 1")


@contextmanager
def _connect(db: Path, mode: str) -> Generator[sqlite3.Connection]:
  uri = f"{Path(os.path.abspath(db)).as_uri()}?mode={mode}"
  with closing(sqlite3.connect(uri, uri=True)) as connection:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    yield connection


def _insert(
  connection: sqlite3.Connection,
  projects: tuple[str, ...],
  label: str,
  created: datetime,
  expires: datetime,
) -> tuple[str, Token]:
  # A new ID may be taken already, which raises sqlite3.IntegrityError.
  id_ = secrets.token_hex(4)
  secret = secrets.token_urlsafe(32)
  with connection:
    _ = connection.execute(
      "INSERT INTO token (id, secret_sha256, label, created_at, expires_at) "
      + "VALUES (?, ?, ?, ?, ?)",
      (id_, _hash(secret), label, _text(created), _text(expires)),
    )
    _ = connection.executemany(
      "INSERT INTO token_project (token_id, project) VALUES (?, ?)",
      [(id_, project) for project in projects],
    )
  return f"dfs_{id_}_{secret}", Token(
    id_, label, projects, created, expires, None
  )


def _row(connection: sqlite3.Connection, id_: str) -> Token:
  label, created, expires, revoked = cast(
    tuple[str, str, str, str | None],
    _one(
      connection,
      "SELECT label, created_at, expires_at, revoked_at FROM token "
      + "WHERE id = ?",
      id_,
    ),
  )
  rows = _all(
    connection,
    "SELECT project FROM token_project WHERE token_id = ? ORDER BY project",
    id_,
  )
  projects = tuple(cast(str, row[0]) for row in rows)
  return Token(
    id_,
    label,
    projects,
    datetime.fromisoformat(created),
    datetime.fromisoformat(expires),
    None if revoked is None else datetime.fromisoformat(revoked),
  )


def _one(
  connection: sqlite3.Connection, sql: str, *params: str
) -> tuple[object, ...] | None:
  return cast(
    tuple[object, ...] | None, connection.execute(sql, params).fetchone()
  )


def _all(
  connection: sqlite3.Connection, sql: str, *params: str
) -> list[tuple[object, ...]]:
  return cast(
    list[tuple[object, ...]], connection.execute(sql, params).fetchall()
  )


def _hash(secret: str) -> bytes:
  return hashlib.sha256(secret.encode("ascii")).digest()


def _text(when: datetime) -> str:
  return when.astimezone(UTC).isoformat()
