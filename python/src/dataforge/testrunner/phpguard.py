"""PHP's check of a fix's files (core.guards).

It finds the namespaces a file's PHP code declares (declares), for a
dataset to compare with those of the project and of its tests: PHP looks
a function or a constant that code names without a namespace up in the
code's own namespace first, and in the global one only when that has
none, so that one declared in a namespace of the tests, or of a library
they run, stands in for what they call. It reads the code between PHP's
tags apart from its strings, heredocs and comments, as PHP 8's lexer
splits them, once with the short open tag `<?` on and once with it off,
not as parsed code: code built at run time, as by eval or a file written
and then included, goes unseen.
"""

from __future__ import annotations

import re
import string

SOURCES = (".php",)
"""PHP's source files, which a task's copy has this module for
(bundle.guards_for)."""

_FLAGS = re.ASCII | re.IGNORECASE
_CHAR = r"A-Za-z0-9_\x80-\U0010ffff"
_LABEL = rf"[A-Za-z_\x80-\U0010ffff][{_CHAR}]*"
_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# The open tags with the short open tag off, and with it on.
_OPEN = re.compile(r"<\?(?:=|php(?=[ \t\r\n]|\Z))", _FLAGS)
_SHORT_OPEN = re.compile(r"<\?(?:=|php(?=[ \t\r\n]|\Z))?", _FLAGS)
# What starts a comment or a string, or ends the code: at the top, and
# inside a string's `{$…}` or `${…}`, which its braces end.
_TOP = re.compile(r"\?>|#(?!\[)|//|/\*|['\"`]|<<<")
_INNER = re.compile(r"#(?!\[)|//|/\*|['\"`{}]|<<<")
_LINE_END = re.compile(r"[\r\n]|\?>")
# What ends each kind of quoted string or opens code inside it.
_STRING = {
  "'": re.compile(r"\\.|'", re.S),
  '"': re.compile(r"\\.|\{\$|\$\{|\"", re.S),
  "`": re.compile(r"\\.|\{\$|\$\{|`", re.S),
}
_HEREDOC = re.compile(
  rf"<<<[ \t]*(?:({_LABEL})|\"({_LABEL})\"|'({_LABEL})')(?:\r\n|\r|\n)"
)
_KEYWORD = re.compile("namespace", _FLAGS)
_DECLARE = re.compile(
  rf"namespace[ \t\r\n]+(\\?{_LABEL}(?:\\{_LABEL})*)" + r"[ \t\r\n]*[;{]",
  _FLAGS,
)


def _gap(text: str) -> str:
  # What stands for text that is not code: a space and its newlines.
  return " " + "\n" * text.count("\n")


def _string(text: str, i: int, quote: str) -> int:
  # The end of the string whose opening quote ends at i.
  stop = _STRING[quote]
  while (m := stop.search(text, i)) is not None:
    if m[0] == quote:
      return m.end()
    i = _inner(text, m.end()) if m[0] in ("{$", "${") else m.end()
  return len(text)


def _heredoc(text: str, h: re.Match[str]) -> int:
  # The end of the heredoc or nowdoc whose opening line h matched.
  label = h[1] or h[2] or h[3]
  end = rf"[ \t]*{re.escape(label)}(?![{_CHAR}])"
  if (m := re.compile(end).match(text, h.end())) is not None:
    return m.end()
  stops = [rf"(?:\r\n|\r|\n){end}"]
  if not h[3]:
    stops = [r"\\[^\r\n]", r"\{\$", r"\$\{", *stops]
  stop = re.compile("|".join(stops))
  i = h.end()
  while (m := stop.search(text, i)) is not None:
    if m[0] in ("{$", "${"):
      i = _inner(text, m.end())
    elif m[0].startswith("\\"):
      i = m.end()
    else:
      return m.end()
  return len(text)


def _skip(text: str, m: re.Match[str]) -> int:
  # The end of the comment or string that m starts.
  if m[0] in ("#", "//"):
    end = _LINE_END.search(text, m.end())
    return len(text) if end is None else end.start()
  if m[0] == "/*":
    end = text.find("*/", m.end())
    return len(text) if end < 0 else end + 2
  if m[0] == "<<<":
    h = _HEREDOC.match(text, m.start())
    return m.end() if h is None else _heredoc(text, h)
  return _string(text, m.end(), m[0])


def _inner(text: str, i: int) -> int:
  # The end of the code inside a string from i, after its closing brace.
  depth = 1
  while (m := _INNER.search(text, i)) is not None:
    if m[0] == "{":
      depth += 1
    elif m[0] == "}":
      depth -= 1
      if depth == 0:
        return m.end()
    i = m.end() if m[0] in "{}" else _skip(text, m)
  return len(text)


def _code(text: str, opener: re.Pattern[str]) -> str:
  # The code of text, each other part a gap; the closing tag, which ends
  # a statement, reads as `;`.
  parts: list[str] = []
  i = 0
  while (tag := opener.search(text, i)) is not None:
    parts.append(_gap(text[i : tag.end()]))
    i = tag.end()
    while (m := _TOP.search(text, i)) is not None and m[0] != "?>":
      end = _skip(text, m)
      parts += [text[i : m.start()], _gap(text[m.start() : end])]
      i = end
    if m is None:
      parts.append(text[i:])
      return "".join(parts)
    parts += [text[i : m.start()], ";"]
    i = m.end()
  parts.append(_gap(text[i:]))
  return "".join(parts)


def declares(text: str) -> list[tuple[int, int, str]]:
  r"""Return the namespaces a file's PHP code declares.

  Args:
    text: The file's text.

  Returns:
    For each declaration, `namespace <name>;` or `namespace <name> {`,
    the numbers of its first and last line, from 1, counted at `\n`
    alone, as git counts them, and the name without a leading `\`, its
    ASCII letters lowercase, as PHP compares names; each once, in order.

  Raises:
    RecursionError: When strings and the code inside them nest deeper than
      Python's recursion allows, some hundreds of levels, for which there
      is no reading: a caller takes that as a finding, not as none.
  """  # noqa: DOC502
  if _KEYWORD.search(text) is None:
    return []
  found: set[tuple[int, int, str]] = set()
  for opener in (_OPEN, _SHORT_OPEN):
    code = _code(text, opener)
    for m in _DECLARE.finditer(code):
      first = code.count("\n", 0, m.start()) + 1
      last = first + m[0].count("\n")
      found.add((first, last, m[1].lstrip("\\").translate(_LOWER)))
  return sorted(found)
