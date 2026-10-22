"""Fingerprints from code: a SQLite schema, a TypedDict, JSON, a directory.

Each gives the facts (`core.Fact`) of one kind of format, read from what the
code makes, so that a change to the code shows in them.
"""

from collections.abc import Collection, Mapping
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import types
import typing
from typing import cast

from dataforge.formats.core import Fact, Fingerprint

_CONSTRAINT = {"constraint", "primary", "unique", "check", "foreign"}
_LEASE = re.compile(r"lease-.*")
_OBJECTS = """
SELECT type, name, tbl_name, sql FROM sqlite_master
WHERE name NOT LIKE 'sqlite_%'
"""


def sqlite(path: Path) -> Fingerprint:
  """Give the facts of a SQLite database's schema.

  A table is `table:<name>`, its value what follows its definition, such as
  STRICT; each of its columns `table:<name>/column:<column>`, its value the
  column's definition, additive when an INSERT that leaves it out writes it:
  it may be NULL or has a default, and is not part of the primary key. Each
  table constraint, index and trigger is under its table too; an index is
  additive unless it is UNIQUE, a constraint or a trigger never. A view is
  `view:<name>` and additive. The comments of a definition and its spacing
  are left out.

  Args:
    path: The database.

  Returns:
    The facts.
  """
  facts: Fingerprint = {}
  uri = f"{path.resolve().as_uri()}?mode=ro"
  with closing(sqlite3.connect(uri, uri=True)) as conn:
    rows = cast(
      list[tuple[str, str, str, str | None]],
      conn.execute(_OBJECTS).fetchall(),
    )
    for kind, name, table, sql in rows:
      if sql is None:
        continue
      text = _strip_comments(sql)
      if kind == "table":
        facts.update(_table(conn, name, text))
      elif kind == "index":
        facts[f"table:{table}/index:{name}"] = Fact(
          _spaced(text), additive=not _unique(conn, table, name)
        )
      elif kind == "trigger":
        facts[f"table:{table}/trigger:{name}"] = Fact(_spaced(text))
      else:
        facts[f"{kind}:{name}"] = Fact(_spaced(text), additive=True)
  return facts


def _table(conn: sqlite3.Connection, name: str, sql: str) -> Fingerprint:
  body, options = _parenthesized(sql)
  columns = {
    cast(str, row[1]): row
    for row in cast(
      list[tuple[object, ...]],
      conn.execute("SELECT * FROM pragma_table_xinfo(?)", (name,)).fetchall(),
    )
  }
  facts = {f"table:{name}": Fact(_spaced(options), additive=True)}
  for part in _split(body):
    words = part.split(maxsplit=1)
    if words[0].lower() in _CONSTRAINT:
      facts[f"table:{name}/constraint:{part}"] = Fact("")
      continue
    column = _unquoted(words[0])
    # pragma_table_xinfo: cid, name, type, notnull, dflt_value, pk, hidden.
    info = columns[column]
    additive = info[5] == 0 and (info[3] == 0 or info[4] is not None)
    facts[f"table:{name}/column:{column}"] = Fact(part, additive=additive)
  return facts


def _unique(conn: sqlite3.Connection, table: str, index: str) -> bool:
  for row in cast(
    list[tuple[object, ...]],
    conn.execute("SELECT * FROM pragma_index_list(?)", (table,)).fetchall(),
  ):
    # pragma_index_list: seq, name, unique, origin, partial.
    if row[1] == index:
      return bool(row[2])
  return True


def _strip_comments(sql: str) -> str:
  out: list[str] = []
  i, quote = 0, ""
  while i < len(sql):
    c = sql[i]
    if quote:
      quote = "" if c == quote else quote
    elif c in "'\"`[":
      quote = "]" if c == "[" else c
    elif sql.startswith("--", i):
      end = sql.find("\n", i)
      i = len(sql) if end < 0 else end
      continue
    elif sql.startswith("/*", i):
      end = sql.find("*/", i + 2)
      i = len(sql) if end < 0 else end + 2
      out.append(" ")
      continue
    out.append(c)
    i += 1
  return "".join(out)


def _parenthesized(sql: str) -> tuple[str, str]:
  # What the first parentheses hold, and what follows them.
  start = sql.index("(")
  depth, quote = 0, ""
  for i in range(start, len(sql)):
    c = sql[i]
    if quote:
      quote = "" if c == quote else quote
    elif c in "'\"`[":
      quote = "]" if c == "[" else c
    elif c == "(":
      depth += 1
    elif c == ")":
      depth -= 1
      if depth == 0:
        return sql[start + 1 : i], sql[i + 1 :]
  raise ValueError(f"unbalanced parentheses in {sql!r}")


def _split(body: str) -> list[str]:
  # The parts that commas outside parentheses and quotes divide.
  parts: list[str] = []
  depth, quote, begin = 0, "", 0
  for i, c in enumerate(body):
    if quote:
      quote = "" if c == quote else quote
    elif c in "'\"`[":
      quote = "]" if c == "[" else c
    elif c == "(":
      depth += 1
    elif c == ")":
      depth -= 1
    elif c == "," and depth == 0:
      parts.append(body[begin:i])
      begin = i + 1
  parts.append(body[begin:])
  return [_spaced(part) for part in parts if part.strip()]


def _spaced(text: str) -> str:
  return " ".join(text.split())


def _unquoted(name: str) -> str:
  if name[:1] in '"`[' and len(name) > 1:
    return name[1:-1]
  return name


def typed_dict(cls: type, prefix: str = "") -> Fingerprint:
  """Give the facts of a TypedDict, the keys of a JSON object.

  Each key is `field:<name>`, its value the key's type; a key that is not
  required is additive. A TypedDict inside it gives its keys under its own.

  Args:
    cls: The TypedDict.
    prefix: What goes before each key.

  Returns:
    The facts.
  """
  facts: Fingerprint = {}
  optional = cast(
    frozenset[str], getattr(cls, "__optional_keys__", frozenset[str]())
  )
  hints = cast(dict[str, object], typing.get_type_hints(cls))
  for name, hint in hints.items():
    key = f"{prefix}field:{name}"
    if typing.is_typeddict(hint):
      facts[key] = Fact("object", additive=name in optional)
      facts.update(typed_dict(cast(type, hint), f"{key}/"))
    else:
      facts[key] = Fact(_type(hint), additive=name in optional)
  return facts


def _type(hint: object) -> str:
  if isinstance(hint, type) and not isinstance(hint, types.GenericAlias):
    return hint.__name__
  return str(hint).replace("typing.", "")


def json_shape(
  value: object,
  *,
  maps: Collection[str] = (),
  opaque: Collection[str] = (),
) -> Fingerprint:
  """Give the facts of a JSON value as code writes it: each key and its type.

  A key of an object is its path, such as `budget/cost_usd`; the items of a
  list are `[]`, of which the first gives the shape. None of them is additive:
  a reader may need a key that a writer of the version before leaves out.

  Args:
    value: A value the code wrote, with every key it writes; one that is
      null gives only that it is there.
    maps: The paths of objects whose keys are data, not names, such as a
      mapping by state: their values are `*`, of which the first gives the
      shape.
    opaque: The paths whose values are the writer's data, whose shape is not
      the format's.

  Returns:
    The facts.
  """
  facts: Fingerprint = {}
  _shape(value, "", maps, opaque, facts)
  return facts


def _shape(
  value: object,
  path: str,
  maps: Collection[str],
  opaque: Collection[str],
  facts: Fingerprint,
) -> None:
  if path:
    facts[path] = Fact(_json_type(value))
  if path in opaque:
    return
  below = f"{path}/" if path else ""
  if isinstance(value, Mapping):
    items = cast(Mapping[str, object], value)
    if path in maps:
      if items:
        _shape(next(iter(items.values())), f"{below}*", maps, opaque, facts)
      return
    for key, item in items.items():
      _shape(item, f"{below}{key}", maps, opaque, facts)
  elif isinstance(value, list) and value:
    _shape(cast(list[object], value)[0], f"{below}[]", maps, opaque, facts)


def _json_type(value: object) -> str:
  if value is None:
    return "null"
  if isinstance(value, bool):
    return "bool"
  if isinstance(value, int):
    return "int"
  if isinstance(value, float):
    return "float"
  if isinstance(value, str):
    return "str"
  if isinstance(value, list):
    return "list"
  return "object"


def tree(root: Path) -> Fingerprint:
  """Give the facts of a directory's layout, as code left it.

  Each directory is its path, additive; each file its path. A lease
  (`dataforge.leases`), whose name is random, is `lease-*`, its value the
  amount it holds; another file's value is `file`.

  Args:
    root: The directory.

  Returns:
    The facts.
  """
  facts: Fingerprint = {}
  for top, dirs, files in os.walk(root):
    here = Path(top).relative_to(root)
    for name in dirs:
      facts[(here / name).as_posix()] = Fact("directory", additive=True)
    for name in files:
      if _LEASE.fullmatch(name):
        amount = (Path(top) / name).read_text()
        facts[(here / "lease-*").as_posix()] = Fact(f"holds {amount}")
      else:
        facts[(here / name).as_posix()] = Fact("file")
  return facts
