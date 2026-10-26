"""Python's check of a fix's files (core.guards).

It finds the code a fix adds that changes pytest's or unittest's
assertions or hooks, or the builtins, which tests run with (rewires). It
reads the lines added alone, as words, not as parsed code: a member set
through a name the lines do not import, or through code built at run
time, goes unseen.
"""

from __future__ import annotations

import re

SOURCES = (".py",)
"""Python's source files, which a task's copy has this module for
(bundle.guards_for)."""

WATCHED_MODULES = frozenset({"builtins", "unittest", "pytest", "_pytest"})
"""The modules whose objects tests assert or are collected with, which a
fix that sets, deletes or patches a member of them, or of a name it
imports from them, changes (rewires)."""

# What rewires reads of the lines a fix adds: their code, joined, with
# comments removed, each string literal kept only as "" or, when it is a
# dotted name, as "<that>", and whitespace collapsed to one space with
# none around a `.` or inside brackets.
_KEPT = re.compile(r"[A-Za-z_][\w.]*")
_MODULE = "|".join(sorted(WATCHED_MODULES))
_RULES = (
  re.compile(r"\bdef pytest_\w+"),
  re.compile(r"\bpytest_plugins\b"),
  re.compile(r"\bregister_assert_rewrite\b"),
  re.compile(r"(?<![\w.])_pytest\b"),
  re.compile(r"\bpytest\.hookimpl\b|\bHookimplMarker\(\"pytest\""),
  re.compile(r"\bpluginmanager\b"),
  re.compile(
    r"\bsys\.modules(?:\[|\.(?:setdefault|update|pop|__setitem__"
    + rf"|__delitem__)\(|,)\{{?\"(?:{_MODULE}|pluggy)(?:\.[\w.]+)?\""
  ),
  re.compile(
    r"\b(?:setattr|delattr|setitem|delitem|patch|patch\.object|patch\.dict"
    + rf"|patch\.multiple)\(\"(?:{_MODULE})\.[\w.]+\""
  ),
)


def _quoted(text: str, start: int, quote: str) -> int:
  # The end of the string literal whose opening quote ends at start: the
  # start of its closing quote, or, for a one-line string that has none,
  # the end of its line.
  i = start
  while i < len(text) and not text.startswith(quote, i):
    if len(quote) == 1 and text[i] == "\n":
      break
    i += 2 if text[i] == "\\" else 1
  return min(i, len(text))


def _code(text: str) -> str:
  # The code of text, its lines kept, with each comment removed and each
  # string literal as rewires reads it.
  out: list[str] = []
  i = 0
  while i < len(text):
    c = text[i]
    if c == "#":
      end = text.find("\n", i)
      i = len(text) if end < 0 else end
    elif c in "'\"":
      quote = c * 3 if text.startswith(c * 3, i) else c
      end = _quoted(text, i + len(quote), quote)
      content = text[i + len(quote) : end]
      out.append(f'"{content}"' if _KEPT.fullmatch(content) else '""')
      i = end + len(quote)
    else:
      out.append(c)
      i += 1
  return "".join(out)


def _watched(code: str) -> set[str]:
  # WATCHED_MODULES, TestCase and each name code binds to one of the
  # modules or to a name it imports from them.
  found = {*WATCHED_MODULES, "__builtins__", "TestCase"}
  for m in re.finditer(r"\bfrom\s+([\w.]+)\s+import\s+(\([^)]*|[^\n;]*)", code):
    if m[1].split(".")[0] in WATCHED_MODULES:
      for name in m[2].strip("()").split(","):
        found.update(name.split()[-1:])
  for m in re.finditer(r"(?<![\w.])import\s+([^\n;]*)", code):
    for name in m[1].split(","):
      words = name.split()
      if len(words) == 3 and words[0].split(".")[0] in WATCHED_MODULES:
        found.add(words[2])
  return {n for n in found if n.isidentifier()}


def rewires(path: str, added: str) -> str | None:
  """Return what lines a fix adds to a Python file change what tests check.

  The lines are read as code from their start, and also as if they began
  inside a triple-quoted string that the lines before them opened, so
  that code they add after closing one is read too.

  Args:
    path: The file's path.
    added: The lines added, joined by newlines.

  Returns:
    In a file of SOURCES, the code that defines a pytest hook or names
    pytest's plugins, its assertion rewriting, its internals or its
    hook markers; replaces one of WATCHED_MODULES in sys.modules; sets,
    deletes or patches a member of one of them, of unittest's TestCase,
    of `__builtins__` or of a name the lines import from one of them;
    None for none.
  """
  if not path.endswith(SOURCES):
    return None
  for start in ("", '"""', "'''"):
    lines = _code(start + added)
    code = " ".join(lines.split())
    code = re.sub(r"\s*\.\s*", ".", code)
    code = re.sub(r"([(\[{,])\s+|\s+(?=[)\]},(\[])", r"\1", code)
    for rule in _RULES:
      if m := rule.search(code):
        return m[0]
    names = "|".join(sorted(_watched(lines)))
    member = rf"(?<![\w.])(?:{names})(?:\.\w+|\[[^\]]*\])+"
    if m := re.search(
      rf"{member}\s*(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)|\bdel {member}"
      + r"|\b(?:setattr|delattr|patch\.object|patch\.multiple)\("
      + rf"(?:{names}|sys\.modules\[\"(?:{_MODULE})\"\])\b",
      code,
    ):
      return m[0]
  return None
