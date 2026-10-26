"""JavaScript's and TypeScript's check of a fix's files (core.guards).

It finds the code a fix adds that changes the test frameworks' assertions
or globals, which tests run with (rewires). It reads the lines added alone,
as words, not as parsed code: a matcher added through a name that the
lines neither import from a framework nor share with one, or through code
built at run time, goes unseen.
"""

from __future__ import annotations

import re

SOURCES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
"""JavaScript's and TypeScript's source files, which a task's copy has this
module for (bundle.guards_for)."""

FRAMEWORK_GLOBALS = frozenset(
  {
    "expect",
    "expectAsync",
    "assert",
    "chai",
    "jasmine",
    "jest",
    "vi",
    "should",
    "it",
    "test",
    "describe",
    "suite",
    "fit",
    "xit",
    "fdescribe",
    "xdescribe",
    "beforeEach",
    "afterEach",
    "beforeAll",
    "afterAll",
    "spyOn",
    "fail",
    "pending",
  }
)
"""The globals of the test frameworks that tests declare, assert or fail
with, which a fix that sets them on the global object changes (rewires)."""

FRAMEWORK_MODULES = re.compile(
  r"(?:vitest|jest|expect|jasmine|jasmine-core|mocha|chai|should"
  + r"|@vitest/[\w.-]+|@jest/[\w.-]+)(?:/[\w./-]+)?|node:test"
)
"""The modules of the test frameworks, which a fix's file imports to reach
their assertions by a name of its own (rewires)."""

# What rewires reads of the lines a fix adds: their code, joined, with
# comments removed, each string or template literal with no substitution
# kept only as "" or, when it is a name or a module of the frameworks, as
# "<that>", `?.` and `["<name>"]` read as `.` and `.<name>`, and whitespace
# collapsed to one space.
_KEPT = re.compile(
  rf"[A-Za-z_$][\w$]*|{FRAMEWORK_MODULES.pattern}|[\w$.-]*matchers-object[\w$.-]*"
)
_REGEX_AFTER = re.compile(
  r"(?:^|[^\w$)\]}\"'`]|\b(?:return|typeof|case|do|else|in|of|new|delete"
  + r"|void|throw|yield|await))\s*$"
)
_NAMES = "|".join(sorted(FRAMEWORK_GLOBALS))
_GLOBAL = r"(?<![\w$.])(?:globalThis|global|window)"
_SET = r"\s*(?:\?\?|\|\||&&)?=(?![=>])"
_RULES = (
  re.compile(
    r"(?<![\w$.])expect\.(?:extend|addSnapshotSerializer"
    + r"|addEqualityTesters|setState|prototype)\b"
  ),
  re.compile(
    r"(?<![\w$.])(?:chai\.(?:use|Assertion)|jasmine\.[\w$]+|Assertion\."
    + r"(?:add|overwrite)(?:Method|Property|ChainableMethod))\b"
  ),
  re.compile(rf"{_GLOBAL}\.(?:{_NAMES}){_SET}"),
  re.compile(
    rf"(?<![\w$.])(?:expect|assert|chai|jasmine|jest|should)\.[\w$.]+{_SET}"
  ),
  re.compile(
    rf"\b(?:Object|Reflect)\.(?:defineProperty|set)\({_GLOBAL},\"(?:{_NAMES})\""
  ),
  re.compile(
    rf"\bObject\.(?:assign|defineProperties)\({_GLOBAL},\{{[^}}]*?"
    + rf"(?<![\w$])(?:{_NAMES})\b"
  ),
  re.compile(r"\bsetPrototypeOf\((?:expect|assert|chai|jasmine|Assertion)\b"),
  re.compile(r"\"[^\"]*matchers-object[^\"]*\""),
)
_IMPORT = re.compile(
  rf"\b(?:from|import|require) ?\(? ?\"({FRAMEWORK_MODULES.pattern})\""
)
_IMPORTED_RULE = re.compile(
  r"[\w$]*\.(?:extend|use|prototype|(?:add|overwrite)(?:Method|Property"
  + r"|ChainableMethod)|add(?:Matchers|AsyncMatchers|SnapshotSerializer"
  + r"|EqualityTesters|CustomEqualityTester))\b"
  + r"|\b(?:setPrototypeOf|defineProperty|defineProperties|__proto__)\b"
)


def _quoted(text: str, start: int, quote: str) -> int:
  # The end of the string literal whose quote is at start - 1: its closing
  # quote, or the end of its line when it has none.
  i = start
  while i < len(text) and text[i] not in (quote, "\n"):
    i += 2 if text[i] == "\\" else 1
  return min(i, len(text))


def _template(text: str, start: int) -> int:
  # The end of a template literal's text from start: its closing backtick
  # or the `${` of a substitution.
  i = start
  while i < len(text) and text[i] != "`" and not text.startswith("${", i):
    i += 2 if text[i] == "\\" else 1
  return min(i, len(text))


def _regex(text: str, start: int) -> int:
  # The end of the regular expression literal whose `/` is at start - 1:
  # its closing `/`, or the end of its line when it has none.
  i = start
  klass = False
  while i < len(text) and text[i] != "\n":
    if text[i] == "\\":
      i += 1
    elif text[i] in "[]":
      klass = text[i] == "["
    elif text[i] == "/" and not klass:
      break
    i += 1
  return min(i, len(text))


def _kept(content: str) -> str:
  # A string literal's content as _code gives it.
  return f'"{content}"' if _KEPT.fullmatch(content) else '""'


def _code(text: str) -> str:
  # The code of text, as rewires reads it.
  out: list[str] = []
  # The depth of braces at which each open substitution of a template
  # literal started.
  subs: list[int] = []
  depth = i = 0
  while i < len(text):
    c = text[i]
    if text.startswith("//", i):
      end = text.find("\n", i)
      i = len(text) if end < 0 else end
    elif text.startswith("/*", i):
      end = text.find("*/", i + 2)
      i = len(text) if end < 0 else end + 2
      out.append(" ")
    elif c in "'\"":
      end = _quoted(text, i + 1, c)
      out.append(_kept(text[i + 1 : end]))
      i = end + 1
    elif c == "`" or (c == "}" and subs and subs[-1] == depth):
      if c == "}":
        _ = subs.pop()
      end = _template(text, i + 1)
      whole = c == "`" and text.startswith("`", end)
      out.append(_kept(text[i + 1 : end]) if whole else '""')
      if text.startswith("${", end):
        subs.append(depth)
        end += 1
      i = end + 1
    elif c == "/" and _REGEX_AFTER.search("".join(out[-20:])):
      out.append("/ /")
      i = _regex(text, i + 1) + 1
    else:
      depth += {"{": 1, "}": -1}.get(c, 0)
      out.append(c)
      i += 1
  code = " ".join("".join(out).split())
  code = re.sub(r"\s*\[\s*\"([A-Za-z_$][\w$]*)\"\s*\]", r".\1", code)
  code = re.sub(r"\s*\??\.\s*(?=[A-Za-z_$\"])", ".", code)
  return re.sub(r"([(\[{,])\s+|\s+(?=[)\]},(])", r"\1", code)


def rewires(path: str, added: str) -> str | None:
  """Return what lines a fix adds to a JS or TS file change what tests check.

  The lines are read as code from their start, and also as if they began
  inside a template literal or a comment that the lines before them
  opened, so that code they add after closing one is read too.

  Args:
    path: The file's path.
    added: The lines added, joined by newlines.

  Returns:
    In a file of SOURCES, the code that adds a matcher to a framework's
    `expect` or changes one, sets one of FRAMEWORK_GLOBALS on the global
    object, sets a member of a framework's assertions, uses chai's or
    Jasmine's own API, or changes an assertion's prototype or a
    framework's store of matchers; or that, in lines that import one of
    FRAMEWORK_MODULES, extends, uses or changes the prototype of any name,
    with the module; None for none.
  """
  if not path.endswith(SOURCES):
    return None
  for start in ("", "`", "/*"):
    code = _code(start + added)
    for rule in _RULES:
      if m := rule.search(code):
        return m[0]
    if (imported := _IMPORT.search(code)) and (
      m := _IMPORTED_RULE.search(code)
    ):
      return f'{m[0]}, with "{imported[1]}" imported'
  return None
