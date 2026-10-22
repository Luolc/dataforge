"""The `[metadata]` of a Harbor task's task.toml, the part every dataset shares.

Harbor keeps `[metadata]` as it is and reads nothing in it, so a dataset puts
there what it knows of a task. dataforge gives the shape of the part every
dataset shares and leaves the rest to the dataset:

  [metadata]
  id = "..."                  the task's ID outside the dataset; optional
  language = "..."            the language of the task's project

  [metadata.tests.f2p]        the tests the task declares, counted for each
  <kind> = <count>            suite, f2p (fail to pass) and p2p (pass to
  [metadata.tests.p2p]        pass); the dataset names the kinds, and p2p's
  project = <count>           `project` counts the project's own tests

  [[metadata.principles]]     what the task asks of a change beyond the
  id = "p1"                   description, one sentence each: `type` one of
  text = "..."                TYPES and `source` where it comes from, both
  type = "best_practice"      optional, and `tests` the tests that check it
  source = "..."
  tests = ["..."]

  [metadata.instruction]      `settings`, the solver settings the task is
  settings = ["implicit"]     given out in (SETTINGS), and the dataset's keys

  [metadata.<group>]          any other table: the dataset's, kept as read

Besides `id` and `language`, the top level of `[metadata]` holds only tables.
A `[metadata]` with no `tests` table is of the format before this one, which
`parse_legacy` reads.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
import copy
from dataclasses import dataclass, field
import datetime
import hashlib
import itertools
import json
import re
import tomllib
from typing import cast, TypeGuard

SUITES = ("f2p", "p2p")
"""The suites of `tests`: fail to pass and pass to pass."""

PROJECT = "project"
"""The kind of p2p that counts the project's own tests."""

TYPES = ("best_practice", "trade_off")
"""What a principle may be: a way a change should go by default, or the
choice between two goods."""

IMPLICIT, EXPLICIT = "implicit", "explicit"
SETTINGS = (IMPLICIT, EXPLICIT)
"""The solver settings: the principles kept from the solver, or stated to it
at the head of `instruction.md` (`explicit_instruction`)."""

GUIDELINE = "Guideline for your change: "
"""What opens the explicit setting's `instruction.md` for one principle."""

GUIDELINES = "Guidelines for your change:"
"""What opens it for several, each then on a line of its own after `- `."""

GUIDELINE_RULE = "\n\n---\n\n"
"""What comes between the principles and the description."""

_OWN = ("id", "language", "tests", "principles")
_INSTRUCTION = "instruction"
_SETTINGS = "settings"
_PRINCIPLE_KEYS = ("id", "text", "type", "source", "tests")
_THEIRS = "is dataforge's, not the dataset's"
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
# What a multi-line basic string escapes: its quote, the backslash, and the
# controls TOML refuses raw there.
_MULTILINE_ESCAPED = re.compile(r'["\\\x00-\x08\x0b-\x1f\x7f]')


class MetadataError(ValueError):
  """A `[metadata]` that does not have the shape dataforge gives it.

  path says which key is wrong, as "metadata.principles[1].type".
  """

  def __init__(self, path: str, what: str) -> None:
    """Make the error.

    Args:
      path: The key that is wrong.
      what: What is wrong with it.
    """
    super().__init__(f"{path}: {what}")
    self.path: str = path


@dataclass(frozen=True, slots=True, kw_only=True)
class Principle:
  """A principle of a task.

  Attributes:
    id: Its ID, unique in the task.
    text: The principle, one sentence on one line.
    type: One of TYPES; None when it is not known.
    source: Where it comes from; None when it is not known.
    tests: The tests that check it, as the task's tests name them.
  """

  id: str
  text: str
  type: str | None = None
  source: str | None = None
  tests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Metadata:
  """A task's `[metadata]`.

  Attributes:
    id: The task's ID outside the dataset; None when it has none.
    language: The language of the task's project.
    tests: For each suite of SUITES the task has, the count of its tests of
      each kind.
    principles: The task's principles, in the file's order.
    settings: The solver settings the task is given out in, in the order of
      SETTINGS.
    extra: The dataset's own tables, as read; `instruction` holds its keys
      other than `settings`.
  """

  id: str | None
  language: str
  tests: Mapping[str, Mapping[str, int]]
  principles: tuple[Principle, ...] = ()
  settings: tuple[str, ...] = (IMPLICIT,)
  extra: Mapping[str, Mapping[str, object]] = field(
    default_factory=dict[str, Mapping[str, object]]
  )


def parse_metadata(table: Mapping[str, object]) -> Metadata:
  """Read a task's `[metadata]`.

  Args:
    table: The `[metadata]`, as `tomllib` reads it.

  Returns:
    What it says.

  Raises:
    MetadataError: It does not have the shape of this module's docstring,
      or it states the explicit setting with no principle.
  """
  for key, value in table.items():
    if key not in ("id", "language", "principles") and not _is_table(value):
      raise MetadataError(f"metadata.{key}", "is not a table")
  who = table.get("id")
  if who is not None and not isinstance(who, str):
    raise MetadataError("metadata.id", "is not a string")
  language = table.get("language")
  if not isinstance(language, str):
    raise MetadataError("metadata.language", "is not a string")
  if "tests" not in table:
    raise MetadataError("metadata.tests", "is missing")
  principles = _principles(table.get("principles", []))
  instruction = cast(Mapping[str, object], table.get(_INSTRUCTION, {}))
  settings = _settings(instruction.get(_SETTINGS, [IMPLICIT]))
  if EXPLICIT in settings and not principles:
    raise MetadataError(
      f"metadata.{_INSTRUCTION}.{_SETTINGS}", f"{EXPLICIT} with no principle"
    )
  extra = {
    k: cast(Mapping[str, object], copy.deepcopy(v))
    for k, v in table.items()
    if k not in (*_OWN, _INSTRUCTION)
  }
  own = {k: v for k, v in instruction.items() if k != _SETTINGS}
  if own:
    extra[_INSTRUCTION] = copy.deepcopy(own)
  return Metadata(
    id=who,
    language=language,
    tests=_tests(table["tests"]),
    principles=principles,
    settings=settings,
    extra=extra,
  )


def parse_legacy(
  table: Mapping[str, object],
  groups: Mapping[str, str] | None,
  principle_tests: Sequence[str] = (),
) -> Metadata:
  """Read a task's `[metadata]` of the format before `tests`.

  That format kept every key at the top level: `test_kinds`, the counts of
  the f2p tests alone, and `alignment`, the one principle, with
  `difference`, what a ranking rested on, and `tags`.

  Args:
    table: The `[metadata]`, as `tomllib` reads it.
    groups: For each key of the dataset's at its top level, the dotted path
      it goes to, a key of a group, as `{"tone": "instruction.tone"}`; None
      to leave out every key of the dataset's, for a reader of the shared
      part alone.
    principle_tests: The tests that check the principle.

  Returns:
    What it says: `test_kinds` as the suite f2p, with p2p left out as not
    known; `alignment` as the principle `p1`, of the type `difference`
    names when it is one of TYPES, with no source and `principle_tests`;
    `settings` as `instruction.settings`; each key of `groups` at its path.
    `difference` and `tags` are left out.

  Raises:
    MetadataError: A key is neither the format's nor in `groups`, a path of
      `groups` is not a key of a group of the dataset's, two keys go to one
      path, `principle_tests` are given with no `alignment`, or what comes
      out fails `parse_metadata`.
  """
  new: dict[str, object] = {"tests": {}}
  for key, value in table.items():
    match key:
      case "id" | "language":
        new[key] = value
      case "test_kinds":
        new["tests"] = {"f2p": value}
      case "settings":
        _put(new, (_INSTRUCTION, _SETTINGS), value, key)
      case "alignment" | "difference" | "tags":
        pass
      case _ if groups is None:
        pass
      case _ if key in groups:
        _put(new, _group_path(groups[key], key), value, key)
      case _:
        raise MetadataError(f"metadata.{key}", "has no group to go to")
  alignment = table.get("alignment")
  if alignment is not None:
    new["principles"] = [_legacy_principle(table, alignment, principle_tests)]
  elif principle_tests:
    raise MetadataError("metadata.alignment", "is missing, but tests check it")
  return parse_metadata(new)


def metadata_table(meta: Metadata) -> dict[str, object]:
  """Return a task's `[metadata]` as `tomllib` would read it.

  Equal metadata gives the same table, in the same order: dataforge's keys
  in the order of this module's docstring, then the dataset's sorted.

  Args:
    meta: The metadata.

  Returns:
    The table: `settings` always, `principles` only when there are some.

  Raises:
    MetadataError: `extra` holds one of dataforge's keys, or the table
      fails `parse_metadata`.
  """
  for key in meta.extra:
    if key in _OWN:
      raise MetadataError(f"metadata.{key}", _THEIRS)
  given = dict(meta.extra.get(_INSTRUCTION, {}))
  if _SETTINGS in given:
    raise MetadataError(f"metadata.{_INSTRUCTION}.{_SETTINGS}", _THEIRS)
  table: dict[str, object] = {} if meta.id is None else {"id": meta.id}
  table["language"] = meta.language
  suites = [s for s in SUITES if s in meta.tests]
  suites += sorted(set(meta.tests) - set(SUITES))
  table["tests"] = {s: _sorted(meta.tests[s]) for s in suites}
  if meta.principles:
    table["principles"] = [_principle_table(p) for p in meta.principles]
  table[_INSTRUCTION] = {_SETTINGS: list(meta.settings)} | _sorted(given)
  for key in sorted(set(meta.extra) - {_INSTRUCTION}):
    table[key] = _sorted(meta.extra[key])
  _ = parse_metadata(table)
  return table


def metadata_toml(meta: Metadata) -> str:
  """Return a task's `[metadata]` as task.toml has it.

  Args:
    meta: The metadata.

  Returns:
    The `[metadata]` table and the tables under it, the same text for
    equal metadata (`metadata_table`).

  Raises:
    MetadataError: As `metadata_table` does.
    ValueError: A value of the dataset's TOML cannot hold.
  """  # noqa: DOC502
  return toml_text({"metadata": metadata_table(meta)})


def public_toml(text: str, allow: Iterable[str]) -> str:
  """Return a task's task.toml with only the metadata it may give out.

  `[metadata]` keeps only what the allow-list names, so that a key added
  later stays inside unless the allow-list names it too; every other value
  reads the same. The file is written anew, without comments.

  Args:
    text: The task's task.toml.
    allow: Dotted paths of keys in `[metadata]`, as `tests` or `tests.f2p`;
      a path keeps the whole value under it. An array of tables, as
      `principles`, is kept whole or not at all, and a path the file does
      not have keeps nothing.

  Returns:
    The file's text.

  Raises:
    MetadataError: `[metadata]` fails `parse_metadata`.
    ValueError: The text is not TOML, a path has an empty key, or the file
      written does not read back as intended.
  """  # noqa: DOC503
  doc = tomllib.loads(text)
  if "metadata" not in doc:
    return toml_text(doc)
  meta = cast(object, doc["metadata"])
  if not _is_table(meta):
    raise MetadataError("metadata", "is not a table")
  _ = parse_metadata(meta)
  paths = [_dotted(p) for p in allow]
  return toml_text(doc | {"metadata": _kept(meta, paths)})


def explicit_instruction(
  description: bytes, principles: Sequence[Principle]
) -> bytes:
  """Return a task's `instruction.md` in the explicit setting.

  Every principle is stated, in the order of their IDs: the setting gives
  the set whole, never part of it.

  Args:
    description: The task's `instruction.md` as the implicit setting has
      it, the description alone.
    principles: The task's principles.

  Returns:
    For one principle, GUIDELINE and the principle; for several,
    GUIDELINES and a line `- <principle>` for each. Then GUIDELINE_RULE,
    the description without the whitespace around it and a line end.

  Raises:
    ValueError: There is no principle, two share an ID, or one is not one
      line.
  """
  texts = _texts(principles)
  if not texts:
    raise ValueError("no principle to state")
  if len(texts) == 1:
    head = f"{GUIDELINE}{texts[0]}"
  else:
    head = "\n- ".join((GUIDELINES, *texts))
  return f"{head}{GUIDELINE_RULE}".encode() + description.strip() + b"\n"


def principles_sha256(principles: Sequence[Principle]) -> str | None:
  """Return the SHA-256 of a task's principles, which changes with any of them.

  Args:
    principles: The task's principles.

  Returns:
    The SHA-256 of their texts' UTF-8, in the order of their IDs, joined
    by a line end, which no text holds: for one principle, of its text
    alone. None when there are none.

  Raises:
    ValueError: Two share an ID, or one is not one line.
  """  # noqa: DOC502
  texts = _texts(principles)
  if not texts:
    return None
  return hashlib.sha256("\n".join(texts).encode()).hexdigest()


def toml_text(doc: Mapping[str, object]) -> str:
  """Return TOML that reads back as a document.

  A string with a line end is a multi-line string, so that the lines of a
  script or of base64 stay lines for whoever reads the file, the scanner
  among them; every key is written, quoted when it is not bare.

  Args:
    doc: The document, as `tomllib` reads one.

  Returns:
    The text.

  Raises:
    ValueError: A value TOML cannot hold, or a text that `tomllib` does not
      read back as `doc`.
  """
  text = "".join(_table(doc, ())).lstrip("\n")
  if tomllib.loads(text) != doc:
    raise ValueError("the TOML written does not read back the same")
  return text


def _tests(value: object) -> dict[str, dict[str, int]]:
  if not _is_table(value):
    raise MetadataError("metadata.tests", "is not a table")
  found: dict[str, dict[str, int]] = {}
  for suite, counts in value.items():
    path = f"metadata.tests.{suite}"
    if suite not in SUITES:
      raise MetadataError(path, f"is not a suite, one of {', '.join(SUITES)}")
    if not _is_table(counts):
      raise MetadataError(path, "is not a table of counts")
    for kind, n in counts.items():
      if type(n) is not int or n < 0:
        raise MetadataError(f"{path}.{kind}", "is not a count")
    found[suite] = cast(dict[str, int], dict(counts))
  return found


def _principles(value: object) -> tuple[Principle, ...]:
  if not _tables(value) and value != []:
    raise MetadataError("metadata.principles", "is not an array of tables")
  found: list[Principle] = []
  for i, each in enumerate(cast(list[dict[str, object]], value)):
    at = f"metadata.principles[{i}]"
    for key in each:
      if key not in _PRINCIPLE_KEYS:
        raise MetadataError(f"{at}.{key}", "is not a key of a principle")
    found.append(_principle(each, at))
    if found[-1].id in (p.id for p in found[:-1]):
      raise MetadataError(f"{at}.id", f"repeats {found[-1].id}")
  return tuple(found)


def _principle(table: Mapping[str, object], at: str) -> Principle:
  who, text = table.get("id"), table.get("text")
  if not isinstance(who, str) or not who:
    raise MetadataError(f"{at}.id", "is not a string that is not empty")
  if not isinstance(text, str) or not text.strip():
    raise MetadataError(f"{at}.text", "is not a string that is not blank")
  if not _one_line(text):
    raise MetadataError(f"{at}.text", "is not one line")
  kind, source = table.get("type"), table.get("source")
  if kind is not None and kind not in TYPES:
    raise MetadataError(f"{at}.type", f"is not one of {', '.join(TYPES)}")
  if source is not None and not isinstance(source, str):
    raise MetadataError(f"{at}.source", "is not a string")
  tests = table.get("tests", [])
  if not _strings(tests) or not all(tests) or len(set(tests)) != len(tests):
    raise MetadataError(
      f"{at}.tests", "is not a list of strings, none empty or repeated"
    )
  return Principle(
    id=who,
    text=text,
    type=cast(str | None, kind),
    source=source,
    tests=tuple(tests),
  )


def _settings(value: object) -> tuple[str, ...]:
  if (
    not _strings(value)
    or not value
    or not all(s in SETTINGS for s in value)
    or len(set(value)) != len(value)
  ):
    raise MetadataError(
      f"metadata.{_INSTRUCTION}.{_SETTINGS}",
      f"is not a list of {', '.join(SETTINGS)} without repeats",
    )
  return tuple(s for s in SETTINGS if s in value)


def _legacy_principle(
  table: Mapping[str, object], alignment: object, tests: Sequence[str]
) -> dict[str, object]:
  principle: dict[str, object] = {"id": "p1", "text": alignment}
  if table.get("difference") in TYPES:
    principle["type"] = table["difference"]
  principle["tests"] = list(tests)
  return principle


def _principle_table(p: Principle) -> dict[str, object]:
  table: dict[str, object] = {"id": p.id, "text": p.text}
  if p.type is not None:
    table["type"] = p.type
  if p.source is not None:
    table["source"] = p.source
  table["tests"] = list(p.tests)
  return table


def _texts(principles: Sequence[Principle]) -> list[str]:
  # The texts in the order of the IDs, which must not repeat, or two orders
  # of the same principles would read differently.
  ordered = sorted(principles, key=lambda p: p.id)
  for a, b in itertools.pairwise(ordered):
    if a.id == b.id:
      raise ValueError(f"two principles have the ID {a.id}")
  for p in ordered:
    if not _one_line(p.text):
      raise ValueError(f"principle {p.id} is not one line")
  return [p.text for p in ordered]


def _one_line(text: str) -> bool:
  return "\n" not in text and "\r" not in text


def _group_path(path: str, key: str) -> tuple[str, ...]:
  keys = _dotted(path)
  if len(keys) < 2 or keys[0] in _OWN or keys == (_INSTRUCTION, _SETTINGS):
    raise MetadataError(
      f"metadata.{key}", f"goes to {path}, not a key of a group of its own"
    )
  return keys


def _put(
  table: dict[str, object], keys: tuple[str, ...], value: object, key: str
) -> None:
  # Set the value at a path of tables, which it makes as it goes; `key` is
  # the legacy key it comes from.
  for k in keys[:-1]:
    inner = table.setdefault(k, {})
    if not _is_table(inner):
      raise MetadataError(f"metadata.{key}", f"goes under {k}, not a table")
    table = inner
  if keys[-1] in table:
    raise MetadataError(f"metadata.{key}", "goes where another key went")
  table[keys[-1]] = value


def _dotted(path: str) -> tuple[str, ...]:
  keys = tuple(path.split("."))
  if not all(keys):
    raise ValueError(f"{path!r} is not a dotted path of keys")
  return keys


def _kept(
  table: Mapping[str, object], paths: Sequence[tuple[str, ...]]
) -> dict[str, object]:
  # What of a table the paths keep, in the table's order.
  kept: dict[str, object] = {}
  for key, value in table.items():
    under = [p[1:] for p in paths if p[0] == key]
    if not under:
      continue
    if () in under:
      kept[key] = value
    elif _is_table(value) and (inner := _kept(value, under)):
      kept[key] = inner
  return kept


def _sorted(value: object) -> dict[str, object]:
  # A table with its keys sorted, its tables' too, at every depth.
  def sort(v: object) -> object:
    if _is_table(v):
      return {k: sort(v[k]) for k in sorted(v)}
    if isinstance(v, list):
      return [sort(each) for each in cast(list[object], v)]
    return v

  return cast(dict[str, object], sort(value))


def _is_table(value: object) -> TypeGuard[dict[str, object]]:
  return isinstance(value, dict)


def _strings(value: object) -> TypeGuard[list[str]]:
  return isinstance(value, list) and all(
    isinstance(v, str) for v in cast(list[object], value)
  )


def _table(table: Mapping[str, object], path: tuple[str, ...]) -> Iterator[str]:
  # A table's own keys, then its tables and arrays of tables, each under its
  # header.
  nested: list[tuple[str, Mapping[str, object], bool]] = []
  for key, value in table.items():
    if _is_table(value):
      nested.append((key, value, False))
    elif _tables(value):
      nested += [(key, each, True) for each in value]
    else:
      yield f"{_key(key)} = {_value(value)}\n"
  for key, each, in_array in nested:
    inner = (*path, key)
    dotted = ".".join(map(_key, inner))
    yield f"\n[[{dotted}]]\n" if in_array else f"\n[{dotted}]\n"
    yield from _table(each, inner)


def _tables(value: object) -> TypeGuard[list[dict[str, object]]]:
  # An array of tables, which TOML writes as `[[...]]`.
  if not isinstance(value, list):
    return False
  items = cast(list[object], value)
  return bool(items) and all(_is_table(v) for v in items)


def _key(key: str) -> str:
  return key if _BARE_KEY.fullmatch(key) else _basic_string(key)


def _value(value: object) -> str:
  match value:
    case bool():
      return "true" if value else "false"
    case int() | float():
      return repr(value)
    case str():
      return _string(value)
    case datetime.date() | datetime.time():
      return value.isoformat()
    case list():
      items = cast(list[object], value)
      return f"[{', '.join(_value(v) for v in items)}]"
    case dict():
      pairs = ", ".join(
        f"{_key(k)} = {_value(v)}"
        for k, v in cast(dict[str, object], value).items()
      )
      return f"{{ {pairs} }}" if pairs else "{}"
    case _:
      raise ValueError(f"TOML holds no {type(value).__name__}")


def _string(value: str) -> str:
  if "\n" not in value:
    return _basic_string(value)
  body = _MULTILINE_ESCAPED.sub(
    lambda m: "\\" + m[0] if m[0] in '"\\' else f"\\u{ord(m[0]):04x}", value
  )
  # TOML drops a line end right after the opening quotes.
  return f'"""\n{body}"""'


def _basic_string(value: str) -> str:
  # A TOML basic string, as JSON writes one with the text left as it is: it
  # escapes `"`, `\` and the controls below U+0020, and TOML also refuses
  # DEL raw. JSON's `\u` escapes of a character past U+FFFF would be a
  # surrogate pair, which TOML refuses.
  return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
