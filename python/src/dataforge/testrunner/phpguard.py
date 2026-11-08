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
and then included, goes unseen. It also finds the functions a file
declares in a namespace (functions), whose names a dataset compares with
those PHP builds in (BUILTINS): one of a built-in's name declared in the
tests' namespace stands in for the built-in they call. So does a
constant, and it finds those a file's `const` statements declare in a
namespace (constants), whose names a dataset compares with those PHP
builds in (BUILTIN_CONSTANTS), and those a file's calls of define name
whole (defines), the way a project often declares its global constants.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
import re
import string
from typing import NamedTuple

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
_FUNCTION = re.compile("function", _FLAGS)
_CONST = re.compile("const", _FLAGS)
# What opens or closes a block or a parenthesis, or declares a namespace,
# a named function, a constant or a class-like body, whose named functions
# are methods and whose constants a class's: each keyword a whole word,
# not the end of a name such as `stdClass`, a variable's or a namespace's;
# `enum` only as PHP's lexer takes it for the keyword, before a name,
# since a class may be named Enum, and none before a lone `:`, a named
# argument's name, as in `class_exists(class: $x)`.
_BLOCKS = re.compile(
  rf"(?<![{_CHAR}$\\])(?:"
  + rf"namespace(?![{_CHAR}\\])"
  + rf"(?:[ \t\r\n]+(?P<ns>\\?{_LABEL}(?:\\{_LABEL})*))?"
  + r"[ \t\r\n]*(?P<end>[;{])"
  + rf"|function(?![{_CHAR}])[ \t\r\n]*&?[ \t\r\n]*(?P<name>{_LABEL})"
  + r"[ \t\r\n]*\("
  + rf"|(?P<kind>(?:class|interface|trait)(?![{_CHAR}])(?![ \t\r\n]*:(?!:))"
  + r"|enum(?=[ \t\r\n]+[A-Za-z_\x80-\U0010ffff]))"
  + rf"|(?P<const>const)(?![{_CHAR}])"
  + r")|[{}();]",
  _FLAGS,
)
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


class _Piece(NamedTuple):
  # A part of a text, from start to end: code, a closing tag, or else what
  # is not code, outside the tags or a comment or a string, which `opens`
  # starts when one does.
  start: int
  end: int
  kind: str
  opens: str = ""


def _pieces(text: str, opener: re.Pattern[str]) -> list[_Piece]:
  # The parts of text, in order.
  pieces: list[_Piece] = []
  i = 0
  while (tag := opener.search(text, i)) is not None:
    pieces.append(_Piece(i, tag.end(), "other"))
    i = tag.end()
    while (m := _TOP.search(text, i)) is not None and m[0] != "?>":
      end = _skip(text, m)
      pieces.append(_Piece(i, m.start(), "code"))
      pieces.append(_Piece(m.start(), end, "other", m[0]))
      i = end
    if m is None:
      pieces.append(_Piece(i, len(text), "code"))
      return pieces
    pieces.append(_Piece(i, m.start(), "code"))
    pieces.append(_Piece(m.start(), m.end(), "close"))
    i = m.end()
  pieces.append(_Piece(i, len(text), "other"))
  return pieces


def _code(text: str, opener: re.Pattern[str]) -> str:
  # The code of text, each other part a gap; the closing tag, which ends
  # a statement, reads as `;`.
  parts: list[str] = []
  for p in _pieces(text, opener):
    part = text[p.start : p.end]
    parts.append(
      part if p.kind == "code" else ";" if p.kind == "close" else _gap(part)
    )
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


@dataclass
class _Scope:
  # Where a scan of code stands: its namespace, its open blocks, its depth
  # of parentheses, and that at which a class-like keyword waits for its
  # body, which opens at the next `{` there, past an anonymous class's
  # arguments.
  namespace: str = ""
  blocks: list[str] = field(default_factory=list[str])
  depth: int = 0
  pending: int | None = None

  def in_class(self) -> bool:
    return bool(self.blocks) and self.blocks[-1] == "class"

  def punctuation(self, mark: str) -> None:
    if mark in "()":
      self.depth += 1 if mark == "(" else -1
    elif mark == "{":
      opens = self.pending == self.depth
      self.blocks.append("class" if opens else "block")
      self.pending = None if opens else self.pending
    elif mark == "}":
      del self.blocks[-1:]
    elif self.pending == self.depth:
      self.pending = None


def _after_member(code: str, i: int) -> bool:
  # Whether `::` or `->` comes before i, but for spaces: `X::class` names
  # a class and opens no body.
  while i and code[i - 1] in " \t\r\n":
    i -= 1
  return code.endswith(("::", "->"), 0, i)


_CONST_NAME = re.compile(rf"[ \t\r\n]*({_LABEL})")
_CONST_STOP = re.compile(r"[,;()[\]{}]")


def _imports(code: str, i: int) -> bool:
  # Whether the `const` at i imports a constant: after `use`, or inside a
  # group use's `\{`, after it or a `,`. A statement ends before a `const`
  # that declares, so no other word comes before one.
  while i and code[i - 1] in " \t\r\n":
    i -= 1
  if code.endswith(",", 0, i):
    return True
  if code.endswith("{", 0, i):
    i -= 1
    while i and code[i - 1] in " \t\r\n":
      i -= 1
    return code.endswith("\\", 0, i)
  return code[max(i - 3, 0) : i].translate(_LOWER) == "use"


def _constant_names(code: str, i: int) -> list[re.Match[str]]:
  # The names the `const` statement whose keyword ends at i declares: the
  # first, and each after a `,` outside brackets, up to its `;`.
  found: list[re.Match[str]] = []
  depth = 0
  while (m := _CONST_NAME.match(code, i)) is not None:
    found.append(m)
    i = m.end()
    while (stop := _CONST_STOP.search(code, i)) is not None:
      i = stop.end()
      if stop[0] in "([{":
        depth += 1
      elif stop[0] in ")]}":
        depth -= 1
      elif depth == 0:
        if stop[0] == ";":
          return found
        break
    else:
      return found
  return found


_Found = list[tuple[int, int, str, str]]


@dataclass
class _Lines:
  # The number of the line, from 1, of each place in code a scan asks for,
  # in order, counted on from the place before.
  code: str
  line: int = 1
  at: int = 0

  def of(self, i: int) -> int:
    self.line += self.code.count("\n", self.at, i)
    self.at = i
    return self.line


def _statement(m: re.Match[str], scope: _Scope, lines: _Lines) -> _Found:
  # The constants the `const` m matched declares: none in a class-like
  # body, after `::` or `->`, or in an import.
  code, start = lines.code, m.start()
  if scope.in_class() or _after_member(code, start) or _imports(code, start):
    return []
  first = lines.of(start)
  return [
    (first, first + code.count("\n", start, n.end(1)), scope.namespace, n[1])
    for n in _constant_names(code, m.end())
  ]


def _scan(code: str) -> tuple[_Found, _Found]:
  # The named functions and the constants code declares outside a
  # class-like body, each with its namespace.
  functions: _Found = []
  constants: _Found = []
  scope = _Scope()
  lines = _Lines(code)
  for m in _BLOCKS.finditer(code):
    if m["end"] is not None:
      scope.namespace = (m["ns"] or "").lstrip("\\").translate(_LOWER)
      if m["end"] == "{":
        scope.blocks.append("block")
    elif m["name"] is not None:
      scope.depth += 1
      if not scope.in_class():
        first = lines.of(m.start())
        last = first + code.count("\n", m.start(), m.end("name"))
        name = m["name"].translate(_LOWER)
        functions.append((first, last, scope.namespace, name))
    elif m["const"] is not None:
      constants += _statement(m, scope, lines)
    elif m["kind"] is not None:
      if not _after_member(code, m.start()):
        scope.pending = scope.depth
    else:
      scope.punctuation(m[0])
  return functions, constants


def functions(text: str) -> list[tuple[int, int, str, str]]:
  r"""Return the functions a file's PHP code declares in a namespace.

  A function declared by name outside the body of a class, an interface,
  a trait or an enum, at the top of the file or inside another function,
  a method or a block, which PHP declares in the namespace in force there
  when the code runs; not a method, nor a closure.

  Args:
    text: The file's text.

  Returns:
    For each, the numbers of the first and last line, from 1, counted at
    `\n` alone, of `function` to its name, the namespace, empty for the
    global one, and the name, both as declares writes a name; each once,
    in order.

  Raises:
    RecursionError: As declares does.
  """  # noqa: DOC502
  if _FUNCTION.search(text) is None:
    return []
  found: set[tuple[int, int, str, str]] = set()
  for opener in (_OPEN, _SHORT_OPEN):
    found.update(_scan(_code(text, opener))[0])
  return sorted(found)


# A call of define whose first argument is a quoted string alone, marked
# `\0<index of its piece>\0` in the text `_calls` reads: after an optional
# name of that argument, inside parentheses that close before the `,`.
_DEFINE = re.compile(
  rf"(?<![{_CHAR}$\\])\\?define[ \t\r\n]*\([ \t\r\n]*"
  + r"(?:constant_name[ \t\r\n]*:(?!:)[ \t\r\n]*)?"
  + r"((?:\([ \t\r\n]*)*)\x00([0-9]+)\x00((?:[ \t\r\n]*\))*)[ \t\r\n]*,",
  _FLAGS,
)
_NAME = re.compile(rf"((?:{_LABEL}\\)*)({_LABEL})")
_SINGLE = re.compile(r"\\([\\'])")
_DOUBLE = re.compile(
  r"\\(?:([\\$\"nrtvef])|([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|u\{([0-9A-Fa-f]+)\})"
)
_CHARS = {"n": "\n", "r": "\r", "t": "\t", "v": "\v", "e": "\x1b", "f": "\f"}


def _escaped(m: re.Match[str]) -> bytes:
  # The bytes an escape of a double-quoted string stands for.
  if m[1] is not None:
    return _CHARS.get(m[1], m[1]).encode()
  if m[2] is not None:
    return bytes([int(m[2], 8) % 256])
  if m[3] is not None:
    return bytes([int(m[3], 16)])
  if int(m[4], 16) > 0x10FFFF:
    return m[0].encode()
  return chr(int(m[4], 16)).encode("utf-8", "surrogatepass")


def _literal(text: str, piece: _Piece) -> str:
  # What the quoted string piece holds, its escapes read as PHP reads them:
  # a double-quoted one's into bytes, which a name holds as UTF-8.
  body = text[piece.start + 1 : piece.end - 1]
  if piece.opens == "'":
    return _SINGLE.sub(r"\1", body)
  held = bytearray()
  i = 0
  for m in _DOUBLE.finditer(body):
    held += body[i : m.start()].encode("utf-8", "surrogatepass")
    held += _escaped(m)
    i = m.end()
  held += body[i:].encode("utf-8", "surrogatepass")
  return held.decode("utf-8", "replace")


def _calls(text: str, pieces: list[_Piece]) -> tuple[str, list[int], list[int]]:
  # The code of the pieces, each comment a space, each quoted string its
  # mark and each other piece a `\1`, which no call reads past, a `\0` of
  # the code a `\1` too, so that each mark is a string's; with where each
  # piece starts in it and in text.
  parts: list[str] = []
  starts: list[int] = []
  at = 0
  for i, p in enumerate(pieces):
    if p.kind == "code":
      part = text[p.start : p.end].replace("\x00", "\x01")
    elif p.opens in ("'", '"'):
      part = f"\x00{i}\x00"
    elif p.opens in ("#", "//", "/*"):
      part = " "
    else:
      part = "\x01"
    parts.append(part)
    starts.append(at)
    at += len(part)
  return "".join(parts), starts, [p.start for p in pieces]


def _defines(text: str, opener: re.Pattern[str]) -> _Found:
  # The constants the define calls of text name by a literal string.
  found: _Found = []
  pieces = _pieces(text, opener)
  code, starts, origins = _calls(text, pieces)
  lines = _Lines(text)
  for m in _DEFINE.finditer(code):
    quoted = pieces[int(m[2])]
    if m[1].count("(") != m[3].count(")") or _after_member(code, m.start()):
      continue
    if (name := _NAME.fullmatch(_literal(text, quoted))) is None:
      continue
    piece = bisect.bisect_right(starts, m.start()) - 1
    start = origins[piece] + m.start() - starts[piece]
    first = lines.of(start)
    last = first + text.count("\n", start, quoted.end)
    namespace = name[1].rstrip("\\").translate(_LOWER)
    found.append((first, last, namespace, name[2]))
  return found


def defines(text: str) -> list[tuple[int, int, str, str]]:
  r"""Return the constants a file's PHP code declares by calling define.

  Each call of `define`, or `\define`, outside a string or a comment,
  whose first argument, first in the call, is a single quoted string with
  no variable in it, alone or in parentheses, named `constant_name:` or
  not, with comments anywhere around it; the string names the constant
  whole: `define('X', …)` declares `X` in the global namespace wherever
  the call is, `define('Ns\X', …)` `X` in `Ns`, and a leading `\` or a
  character no name has makes one no code can name. A double-quoted
  string's escapes are read as bytes, and the name as UTF-8, as a file's
  text is. A name code builds, as by `.`, a variable, a constant or a
  heredoc, only running the code tells, and one given after the value by
  its argument's name is not read; nor is a method of that name a call of
  it.

  Args:
    text: The file's text.

  Returns:
    For each, the numbers of the first and last line, from 1, counted at
    `\n` alone, of `define` to the end of the string, the namespace, as
    declares writes it, empty for the global one, and the name as written;
    each once, in order.
  """
  if re.search("define", text, _FLAGS) is None:
    return []
  found: set[tuple[int, int, str, str]] = set()
  for opener in (_OPEN, _SHORT_OPEN):
    found.update(_defines(text, opener))
  return sorted(found)


def constants(text: str) -> list[tuple[int, int, str, str]]:
  r"""Return the constants a file's PHP code declares in a namespace.

  Each name a `const` statement declares outside the body of a class, an
  interface, a trait or an enum, which PHP declares in the namespace in
  force there; not a class's constant, nor one `use const` imports, nor
  one `define` declares, which only running the code tells.

  Args:
    text: The file's text.

  Returns:
    For each, the numbers of the first and last line, from 1, counted at
    `\n` alone, of `const` to the name, the namespace, as declares writes
    it, empty for the global one, and the name as written, since PHP
    tells a constant's letter case; each once, in order.

  Raises:
    RecursionError: As declares does.
  """  # noqa: DOC502
  if _CONST.search(text) is None:
    return []
  found: set[tuple[int, int, str, str]] = set()
  for opener in (_OPEN, _SHORT_OPEN):
    found.update(_scan(_code(text, opener))[1])
  return sorted(found)


# The functions PHP 8.3.6 builds in with no namespace, as
# get_defined_functions()["internal"] lists them on Ubuntu 24.04 with
# php8.3-cli and every other php8.3-* package of the release but cgi, fpm,
# phpdbg and dev, and apcu and gmagick, which conflict with yac and
# imagick; of those ds fails to load (read 2026-11-08): a superset of
# those a project's PHP can have, so that what the tests call is among
# them. To update it, install the same in a container of that release's
# image, run `php -r 'foreach (get_defined_functions()["internal"] as $f)
# echo $f, "\n";'`, and put here those without a `\`, sorted, wrapped at 78
# columns.
_BUILTIN_NAMES = """
_ abs acos acosh addcslashes addslashes array_change_key_case array_chunk
array_column array_combine array_count_values array_diff array_diff_assoc
array_diff_key array_diff_uassoc array_diff_ukey array_fill array_fill_keys
array_filter array_flip array_intersect array_intersect_assoc
array_intersect_key array_intersect_uassoc array_intersect_ukey array_is_list
array_key_exists array_key_first array_key_last array_keys array_map
array_merge array_merge_recursive array_multisort array_pad array_pop
array_product array_push array_rand array_reduce array_replace
array_replace_recursive array_reverse array_search array_shift array_slice
array_splice array_sum array_udiff array_udiff_assoc array_udiff_uassoc
array_uintersect array_uintersect_assoc array_uintersect_uassoc array_unique
array_unshift array_values array_walk array_walk_recursive arsort asin asinh
asort assert assert_options atan atan2 atanh base64_decode base64_encode
base_convert basename bcadd bccomp bcdiv bcmod bcmul bcpow bcpowmod bcscale
bcsqrt bcsub bin2hex bind_textdomain_codeset bindec bindtextdomain boolval
bzclose bzcompress bzdecompress bzerrno bzerror bzerrstr bzflush bzopen bzread
bzwrite cal_days_in_month cal_from_jd cal_info cal_to_jd call_user_func
call_user_func_array ceil chdir checkdate checkdnsrr chgrp chmod chop chown
chr chroot chunk_split class_alias class_exists class_implements class_parents
class_uses clearstatcache cli_get_process_title cli_set_process_title closedir
closelog collator_asort collator_compare collator_create
collator_get_attribute collator_get_error_code collator_get_error_message
collator_get_locale collator_get_sort_key collator_get_strength
collator_set_attribute collator_set_strength collator_sort
collator_sort_with_sort_keys compact connection_aborted connection_status
constant convert_uudecode convert_uuencode copy cos cosh count count_chars
crc32 crypt ctype_alnum ctype_alpha ctype_cntrl ctype_digit ctype_graph
ctype_lower ctype_print ctype_punct ctype_space ctype_upper ctype_xdigit
curl_close curl_copy_handle curl_errno curl_error curl_escape curl_exec
curl_file_create curl_getinfo curl_init curl_multi_add_handle curl_multi_close
curl_multi_errno curl_multi_exec curl_multi_getcontent curl_multi_info_read
curl_multi_init curl_multi_remove_handle curl_multi_select curl_multi_setopt
curl_multi_strerror curl_pause curl_reset curl_setopt curl_setopt_array
curl_share_close curl_share_errno curl_share_init curl_share_setopt
curl_share_strerror curl_strerror curl_unescape curl_upkeep curl_version
current date date_add date_create date_create_from_format
date_create_immutable date_create_immutable_from_format date_date_set
date_default_timezone_get date_default_timezone_set date_diff date_format
date_get_last_errors date_interval_create_from_date_string
date_interval_format date_isodate_set date_modify date_offset_get date_parse
date_parse_from_format date_sub date_sun_info date_sunrise date_sunset
date_time_set date_timestamp_get date_timestamp_set date_timezone_get
date_timezone_set datefmt_create datefmt_format datefmt_format_object
datefmt_get_calendar datefmt_get_calendar_object datefmt_get_datetype
datefmt_get_error_code datefmt_get_error_message datefmt_get_locale
datefmt_get_pattern datefmt_get_timetype datefmt_get_timezone
datefmt_get_timezone_id datefmt_is_lenient datefmt_localtime datefmt_parse
datefmt_set_calendar datefmt_set_lenient datefmt_set_pattern
datefmt_set_timezone dba_close dba_delete dba_exists dba_fetch dba_firstkey
dba_handlers dba_insert dba_key_split dba_list dba_nextkey dba_open
dba_optimize dba_popen dba_replace dba_sync dcgettext dcngettext
debug_backtrace debug_print_backtrace debug_zval_dump decbin dechex decoct
define defined deflate_add deflate_init deg2rad dgettext dir dirname
disk_free_space disk_total_space diskfreespace dl dngettext dns_check_record
dns_get_mx dns_get_record dom_import_simplexml doubleval easter_date
easter_days enchant_broker_describe enchant_broker_dict_exists
enchant_broker_free enchant_broker_free_dict enchant_broker_get_dict_path
enchant_broker_get_error enchant_broker_init enchant_broker_list_dicts
enchant_broker_request_dict enchant_broker_request_pwl_dict
enchant_broker_set_dict_path enchant_broker_set_ordering enchant_dict_add
enchant_dict_add_to_personal enchant_dict_add_to_session enchant_dict_check
enchant_dict_describe enchant_dict_get_error enchant_dict_is_added
enchant_dict_is_in_session enchant_dict_quick_check
enchant_dict_store_replacement enchant_dict_suggest end enum_exists
error_clear_last error_get_last error_log error_reporting escapeshellarg
escapeshellcmd exec exif_imagetype exif_read_data exif_tagname exif_thumbnail
exp explode expm1 extension_loaded extract face_count face_detect fclose
fdatasync fdiv feof fflush fgetc fgetcsv fgets file file_exists
file_get_contents file_put_contents fileatime filectime filegroup fileinode
filemtime fileowner fileperms filesize filetype filter_has_var filter_id
filter_input filter_input_array filter_list filter_var filter_var_array
finfo_buffer finfo_close finfo_file finfo_open finfo_set_flags floatval flock
floor flush fmod fnmatch fopen forward_static_call forward_static_call_array
fpassthru fprintf fputcsv fputs fread frenchtojd fscanf fseek fsockopen fstat
fsync ftell ftok ftp_alloc ftp_append ftp_cdup ftp_chdir ftp_chmod ftp_close
ftp_connect ftp_delete ftp_exec ftp_fget ftp_fput ftp_get ftp_get_option
ftp_login ftp_mdtm ftp_mkdir ftp_mlsd ftp_nb_continue ftp_nb_fget ftp_nb_fput
ftp_nb_get ftp_nb_put ftp_nlist ftp_pasv ftp_put ftp_pwd ftp_quit ftp_raw
ftp_rawlist ftp_rename ftp_rmdir ftp_set_option ftp_site ftp_size
ftp_ssl_connect ftp_systype ftruncate func_get_arg func_get_args func_num_args
function_exists fwrite gc_collect_cycles gc_disable gc_enable gc_enabled
gc_mem_caches gc_status gd_info gearman_bugreport gearman_client_add_options
gearman_client_add_server gearman_client_add_servers gearman_client_add_task
gearman_client_add_task_background gearman_client_add_task_high
gearman_client_add_task_high_background gearman_client_add_task_low
gearman_client_add_task_low_background gearman_client_add_task_status
gearman_client_clear_callbacks gearman_client_context gearman_client_create
gearman_client_do_background gearman_client_do_high
gearman_client_do_high_background gearman_client_do_job_handle
gearman_client_do_low gearman_client_do_low_background
gearman_client_do_normal gearman_client_do_status
gearman_client_enable_exception_handler gearman_client_error
gearman_client_get_errno gearman_client_job_status
gearman_client_job_status_by_unique_key gearman_client_options
gearman_client_ping gearman_client_remove_options gearman_client_return_code
gearman_client_run_tasks gearman_client_set_complete_callback
gearman_client_set_context gearman_client_set_created_callback
gearman_client_set_data_callback gearman_client_set_exception_callback
gearman_client_set_fail_callback gearman_client_set_options
gearman_client_set_status_callback gearman_client_set_timeout
gearman_client_set_warning_callback gearman_client_set_workload_callback
gearman_client_timeout gearman_client_wait gearman_job_function_name
gearman_job_handle gearman_job_return_code gearman_job_send_complete
gearman_job_send_data gearman_job_send_exception gearman_job_send_fail
gearman_job_send_status gearman_job_send_warning gearman_job_set_return
gearman_job_unique gearman_job_workload gearman_job_workload_size
gearman_task_data gearman_task_data_size gearman_task_denominator
gearman_task_function_name gearman_task_is_known gearman_task_is_running
gearman_task_job_handle gearman_task_numerator gearman_task_recv_data
gearman_task_return_code gearman_task_send_workload gearman_task_unique
gearman_verbose_name gearman_version gearman_worker_add_function
gearman_worker_add_options gearman_worker_add_server
gearman_worker_add_servers gearman_worker_create gearman_worker_errno
gearman_worker_error gearman_worker_grab_job gearman_worker_options
gearman_worker_ping gearman_worker_register gearman_worker_remove_options
gearman_worker_return_code gearman_worker_set_id gearman_worker_set_options
gearman_worker_set_timeout gearman_worker_timeout gearman_worker_unregister
gearman_worker_unregister_all gearman_worker_wait gearman_worker_work
get_browser get_called_class get_cfg_var get_class get_class_methods
get_class_vars get_current_user get_debug_type get_declared_classes
get_declared_interfaces get_declared_traits get_defined_constants
get_defined_functions get_defined_vars get_extension_funcs get_headers
get_html_translation_table get_include_path get_included_files
get_loaded_extensions get_mangled_object_vars get_meta_tags get_object_vars
get_parent_class get_required_files get_resource_id get_resource_type
get_resources getcwd getdate getenv gethostbyaddr gethostbyname gethostbynamel
gethostname getimagesize getimagesizefromstring getlastmod getmxrr getmygid
getmyinode getmypid getmyuid getopt getprotobyname getprotobynumber getrandmax
getrusage getservbyname getservbyport gettext gettimeofday gettype glob gmdate
gmmktime gmp_abs gmp_add gmp_and gmp_binomial gmp_clrbit gmp_cmp gmp_com
gmp_div gmp_div_q gmp_div_qr gmp_div_r gmp_divexact gmp_export gmp_fact
gmp_gcd gmp_gcdext gmp_hamdist gmp_import gmp_init gmp_intval gmp_invert
gmp_jacobi gmp_kronecker gmp_lcm gmp_legendre gmp_mod gmp_mul gmp_neg
gmp_nextprime gmp_or gmp_perfect_power gmp_perfect_square gmp_popcount gmp_pow
gmp_powm gmp_prob_prime gmp_random_bits gmp_random_range gmp_random_seed
gmp_root gmp_rootrem gmp_scan0 gmp_scan1 gmp_setbit gmp_sign gmp_sqrt
gmp_sqrtrem gmp_strval gmp_sub gmp_testbit gmp_xor gmstrftime
gnupg_adddecryptkey gnupg_addencryptkey gnupg_addsignkey
gnupg_cleardecryptkeys gnupg_clearencryptkeys gnupg_clearsignkeys
gnupg_decrypt gnupg_decryptverify gnupg_deletekey gnupg_encrypt
gnupg_encryptsign gnupg_export gnupg_getengineinfo gnupg_geterror
gnupg_geterrorinfo gnupg_getprotocol gnupg_gettrustlist gnupg_import
gnupg_init gnupg_keyinfo gnupg_listsignatures gnupg_setarmor
gnupg_seterrormode gnupg_setsignmode gnupg_sign gnupg_verify grapheme_extract
grapheme_stripos grapheme_stristr grapheme_strlen grapheme_strpos
grapheme_strripos grapheme_strrpos grapheme_strstr grapheme_substr
gregoriantojd gzclose gzcompress gzdecode gzdeflate gzencode gzeof gzfile
gzgetc gzgets gzinflate gzopen gzpassthru gzputs gzread gzrewind gzseek gztell
gzuncompress gzwrite hash hash_algos hash_copy hash_equals hash_file
hash_final hash_hkdf hash_hmac hash_hmac_algos hash_hmac_file hash_init
hash_pbkdf2 hash_update hash_update_file hash_update_stream header
header_register_callback header_remove headers_list headers_sent hebrev
hex2bin hexdec highlight_file highlight_string hrtime html_entity_decode
htmlentities htmlspecialchars htmlspecialchars_decode http_build_query
http_response_code hypot iconv iconv_get_encoding iconv_mime_decode
iconv_mime_decode_headers iconv_mime_encode iconv_set_encoding iconv_strlen
iconv_strpos iconv_strrpos iconv_substr idate idn_to_ascii idn_to_utf8
igbinary_serialize igbinary_unserialize ignore_user_abort
image_type_to_extension image_type_to_mime_type imageaffine
imageaffinematrixconcat imageaffinematrixget imagealphablending imageantialias
imagearc imagebmp imagechar imagecharup imagecolorallocate
imagecolorallocatealpha imagecolorat imagecolorclosest imagecolorclosestalpha
imagecolorclosesthwb imagecolordeallocate imagecolorexact imagecolorexactalpha
imagecolormatch imagecolorresolve imagecolorresolvealpha imagecolorset
imagecolorsforindex imagecolorstotal imagecolortransparent imageconvolution
imagecopy imagecopymerge imagecopymergegray imagecopyresampled
imagecopyresized imagecreate imagecreatefrombmp imagecreatefromgd
imagecreatefromgd2 imagecreatefromgd2part imagecreatefromgif
imagecreatefromjpeg imagecreatefrompng imagecreatefromstring
imagecreatefromtga imagecreatefromwbmp imagecreatefromwebp imagecreatefromxbm
imagecreatefromxpm imagecreatetruecolor imagecrop imagecropauto
imagedashedline imagedestroy imageellipse imagefill imagefilledarc
imagefilledellipse imagefilledpolygon imagefilledrectangle imagefilltoborder
imagefilter imageflip imagefontheight imagefontwidth imageftbbox imagefttext
imagegammacorrect imagegd imagegd2 imagegetclip imagegetinterpolation imagegif
imageinterlace imageistruecolor imagejpeg imagelayereffect imageline
imageloadfont imageopenpolygon imagepalettecopy imagepalettetotruecolor
imagepng imagepolygon imagerectangle imageresolution imagerotate
imagesavealpha imagescale imagesetbrush imagesetclip imagesetinterpolation
imagesetpixel imagesetstyle imagesetthickness imagesettile imagestring
imagestringup imagesx imagesy imagetruecolortopalette imagettfbbox
imagettftext imagetypes imagewbmp imagewebp imagexbm imap_8bit imap_alerts
imap_append imap_base64 imap_binary imap_body imap_bodystruct imap_check
imap_clearflag_full imap_close imap_create imap_createmailbox imap_delete
imap_deletemailbox imap_errors imap_expunge imap_fetch_overview imap_fetchbody
imap_fetchheader imap_fetchmime imap_fetchstructure imap_fetchtext imap_gc
imap_get_quota imap_get_quotaroot imap_getacl imap_getmailboxes
imap_getsubscribed imap_headerinfo imap_headers imap_is_open imap_last_error
imap_list imap_listmailbox imap_listscan imap_listsubscribed imap_lsub
imap_mail imap_mail_compose imap_mail_copy imap_mail_move imap_mailboxmsginfo
imap_mime_header_decode imap_msgno imap_mutf7_to_utf8 imap_num_msg
imap_num_recent imap_open imap_ping imap_qprint imap_rename imap_renamemailbox
imap_reopen imap_rfc822_parse_adrlist imap_rfc822_parse_headers
imap_rfc822_write_address imap_savebody imap_scan imap_scanmailbox imap_search
imap_set_quota imap_setacl imap_setflag_full imap_sort imap_status
imap_subscribe imap_thread imap_timeout imap_uid imap_undelete
imap_unsubscribe imap_utf7_decode imap_utf7_encode imap_utf8
imap_utf8_to_mutf7 implode in_array inet_ntop inet_pton inflate_add
inflate_get_read_len inflate_get_status inflate_init ini_alter ini_get
ini_get_all ini_parse_quantity ini_restore ini_set intdiv interface_exists
intl_error_name intl_get_error_code intl_get_error_message intl_is_failure
intlcal_add intlcal_after intlcal_before intlcal_clear intlcal_create_instance
intlcal_equals intlcal_field_difference intlcal_from_date_time intlcal_get
intlcal_get_actual_maximum intlcal_get_actual_minimum
intlcal_get_available_locales intlcal_get_day_of_week_type
intlcal_get_error_code intlcal_get_error_message intlcal_get_first_day_of_week
intlcal_get_greatest_minimum intlcal_get_keyword_values_for_locale
intlcal_get_least_maximum intlcal_get_locale intlcal_get_maximum
intlcal_get_minimal_days_in_first_week intlcal_get_minimum intlcal_get_now
intlcal_get_repeated_wall_time_option intlcal_get_skipped_wall_time_option
intlcal_get_time intlcal_get_time_zone intlcal_get_type
intlcal_get_weekend_transition intlcal_in_daylight_time
intlcal_is_equivalent_to intlcal_is_lenient intlcal_is_set intlcal_is_weekend
intlcal_roll intlcal_set intlcal_set_first_day_of_week intlcal_set_lenient
intlcal_set_minimal_days_in_first_week intlcal_set_repeated_wall_time_option
intlcal_set_skipped_wall_time_option intlcal_set_time intlcal_set_time_zone
intlcal_to_date_time intlgregcal_create_instance
intlgregcal_get_gregorian_change intlgregcal_is_leap_year
intlgregcal_set_gregorian_change intltz_count_equivalent_ids
intltz_create_default intltz_create_enumeration intltz_create_time_zone
intltz_create_time_zone_id_enumeration intltz_from_date_time_zone
intltz_get_canonical_id intltz_get_display_name intltz_get_dst_savings
intltz_get_equivalent_id intltz_get_error_code intltz_get_error_message
intltz_get_gmt intltz_get_id intltz_get_id_for_windows_id intltz_get_offset
intltz_get_raw_offset intltz_get_region intltz_get_tz_data_version
intltz_get_unknown intltz_get_windows_id intltz_has_same_rules
intltz_to_date_time_zone intltz_use_daylight_time intval ip2long iptcembed
iptcparse is_a is_array is_bool is_callable is_countable is_dir is_double
is_executable is_file is_finite is_float is_infinite is_int is_integer
is_iterable is_link is_long is_nan is_null is_numeric is_object is_readable
is_resource is_scalar is_soap_fault is_string is_subclass_of is_uploaded_file
is_writable is_writeable iterator_apply iterator_count iterator_to_array
jddayofweek jdmonthname jdtofrench jdtogregorian jdtojewish jdtojulian
jdtounix jewishtojd join json_decode json_encode json_last_error
json_last_error_msg json_validate juliantojd key key_exists krsort ksort
lcfirst lcg_value lchgrp lchown ldap_add ldap_add_ext ldap_bind ldap_bind_ext
ldap_close ldap_compare ldap_connect ldap_count_entries ldap_count_references
ldap_delete ldap_delete_ext ldap_dn2ufn ldap_err2str ldap_errno ldap_error
ldap_escape ldap_exop ldap_exop_passwd ldap_exop_refresh ldap_exop_sync
ldap_exop_whoami ldap_explode_dn ldap_first_attribute ldap_first_entry
ldap_first_reference ldap_free_result ldap_get_attributes ldap_get_dn
ldap_get_entries ldap_get_option ldap_get_values ldap_get_values_len ldap_list
ldap_mod_add ldap_mod_add_ext ldap_mod_del ldap_mod_del_ext ldap_mod_replace
ldap_mod_replace_ext ldap_modify ldap_modify_batch ldap_next_attribute
ldap_next_entry ldap_next_reference ldap_parse_exop ldap_parse_reference
ldap_parse_result ldap_read ldap_rename ldap_rename_ext ldap_sasl_bind
ldap_search ldap_set_option ldap_set_rebind_proc ldap_start_tls ldap_unbind
levenshtein libxml_clear_errors libxml_disable_entity_loader libxml_get_errors
libxml_get_external_entity_loader libxml_get_last_error
libxml_set_external_entity_loader libxml_set_streams_context
libxml_use_internal_errors link linkinfo locale_accept_from_http
locale_canonicalize locale_compose locale_filter_matches
locale_get_all_variants locale_get_default locale_get_display_language
locale_get_display_name locale_get_display_region locale_get_display_script
locale_get_display_variant locale_get_keywords locale_get_primary_language
locale_get_region locale_get_script locale_lookup locale_parse
locale_set_default localeconv localtime log log10 log1p long2ip lstat ltrim
mail mailparse_determine_best_xfer_encoding mailparse_msg_create
mailparse_msg_extract_part mailparse_msg_extract_part_file
mailparse_msg_extract_whole_part_file mailparse_msg_free
mailparse_msg_get_part mailparse_msg_get_part_data mailparse_msg_get_structure
mailparse_msg_parse mailparse_msg_parse_file mailparse_rfc822_parse_addresses
mailparse_stream_encode mailparse_test mailparse_uudecode_all max
mb_check_encoding mb_chr mb_convert_case mb_convert_encoding mb_convert_kana
mb_convert_variables mb_decode_mimeheader mb_decode_numericentity
mb_detect_encoding mb_detect_order mb_encode_mimeheader
mb_encode_numericentity mb_encoding_aliases mb_ereg mb_ereg_match
mb_ereg_replace mb_ereg_replace_callback mb_ereg_search mb_ereg_search_getpos
mb_ereg_search_getregs mb_ereg_search_init mb_ereg_search_pos
mb_ereg_search_regs mb_ereg_search_setpos mb_eregi mb_eregi_replace
mb_get_info mb_http_input mb_http_output mb_internal_encoding mb_language
mb_list_encodings mb_ord mb_output_handler mb_parse_str mb_preferred_mime_name
mb_regex_encoding mb_regex_set_options mb_scrub mb_send_mail mb_split
mb_str_pad mb_str_split mb_strcut mb_strimwidth mb_stripos mb_stristr
mb_strlen mb_strpos mb_strrchr mb_strrichr mb_strripos mb_strrpos mb_strstr
mb_strtolower mb_strtoupper mb_strwidth mb_substitute_character mb_substr
mb_substr_count mcrypt_create_iv mcrypt_decrypt mcrypt_enc_get_algorithms_name
mcrypt_enc_get_block_size mcrypt_enc_get_iv_size mcrypt_enc_get_key_size
mcrypt_enc_get_modes_name mcrypt_enc_get_supported_key_sizes
mcrypt_enc_is_block_algorithm mcrypt_enc_is_block_algorithm_mode
mcrypt_enc_is_block_mode mcrypt_enc_self_test mcrypt_encrypt mcrypt_generic
mcrypt_generic_deinit mcrypt_generic_init mcrypt_get_block_size
mcrypt_get_cipher_name mcrypt_get_iv_size mcrypt_get_key_size
mcrypt_list_algorithms mcrypt_list_modes mcrypt_module_close
mcrypt_module_get_algo_block_size mcrypt_module_get_algo_key_size
mcrypt_module_get_supported_key_sizes mcrypt_module_is_block_algorithm
mcrypt_module_is_block_algorithm_mode mcrypt_module_is_block_mode
mcrypt_module_open mcrypt_module_self_test md5 md5_file mdecrypt_generic
memcache_add memcache_add_server memcache_append memcache_cas memcache_close
memcache_connect memcache_debug memcache_decrement memcache_delete
memcache_flush memcache_get memcache_get_extended_stats
memcache_get_server_status memcache_get_stats memcache_get_version
memcache_increment memcache_pconnect memcache_prepend memcache_replace
memcache_set memcache_set_compress_threshold memcache_set_failure_callback
memcache_set_sasl_auth_data memcache_set_server_params memory_get_peak_usage
memory_get_usage memory_reset_peak_usage metaphone method_exists mhash
mhash_count mhash_get_block_size mhash_get_hash_name mhash_keygen_s2k
microtime mime_content_type min mkdir mktime move_uploaded_file msg_get_queue
msg_queue_exists msg_receive msg_remove_queue msg_send msg_set_queue
msg_stat_queue msgfmt_create msgfmt_format msgfmt_format_message
msgfmt_get_error_code msgfmt_get_error_message msgfmt_get_locale
msgfmt_get_pattern msgfmt_parse msgfmt_parse_message msgfmt_set_pattern
msgpack_pack msgpack_serialize msgpack_unpack msgpack_unserialize
mt_getrandmax mt_rand mt_srand mysqli_affected_rows mysqli_autocommit
mysqli_begin_transaction mysqli_change_user mysqli_character_set_name
mysqli_close mysqli_commit mysqli_connect mysqli_connect_errno
mysqli_connect_error mysqli_data_seek mysqli_debug mysqli_dump_debug_info
mysqli_errno mysqli_error mysqli_error_list mysqli_escape_string
mysqli_execute mysqli_execute_query mysqli_fetch_all mysqli_fetch_array
mysqli_fetch_assoc mysqli_fetch_column mysqli_fetch_field
mysqli_fetch_field_direct mysqli_fetch_fields mysqli_fetch_lengths
mysqli_fetch_object mysqli_fetch_row mysqli_field_count mysqli_field_seek
mysqli_field_tell mysqli_free_result mysqli_get_charset mysqli_get_client_info
mysqli_get_client_stats mysqli_get_client_version mysqli_get_connection_stats
mysqli_get_host_info mysqli_get_links_stats mysqli_get_proto_info
mysqli_get_server_info mysqli_get_server_version mysqli_get_warnings
mysqli_info mysqli_init mysqli_insert_id mysqli_kill mysqli_more_results
mysqli_multi_query mysqli_next_result mysqli_num_fields mysqli_num_rows
mysqli_options mysqli_ping mysqli_poll mysqli_prepare mysqli_query
mysqli_real_connect mysqli_real_escape_string mysqli_real_query
mysqli_reap_async_query mysqli_refresh mysqli_release_savepoint mysqli_report
mysqli_rollback mysqli_savepoint mysqli_select_db mysqli_set_charset
mysqli_set_opt mysqli_sqlstate mysqli_ssl_set mysqli_stat
mysqli_stmt_affected_rows mysqli_stmt_attr_get mysqli_stmt_attr_set
mysqli_stmt_bind_param mysqli_stmt_bind_result mysqli_stmt_close
mysqli_stmt_data_seek mysqli_stmt_errno mysqli_stmt_error
mysqli_stmt_error_list mysqli_stmt_execute mysqli_stmt_fetch
mysqli_stmt_field_count mysqli_stmt_free_result mysqli_stmt_get_result
mysqli_stmt_get_warnings mysqli_stmt_init mysqli_stmt_insert_id
mysqli_stmt_more_results mysqli_stmt_next_result mysqli_stmt_num_rows
mysqli_stmt_param_count mysqli_stmt_prepare mysqli_stmt_reset
mysqli_stmt_result_metadata mysqli_stmt_send_long_data mysqli_stmt_sqlstate
mysqli_stmt_store_result mysqli_store_result mysqli_thread_id
mysqli_thread_safe mysqli_use_result mysqli_warning_count natcasesort natsort
net_get_interfaces next ngettext nl2br nl_langinfo
normalizer_get_raw_decomposition normalizer_is_normalized normalizer_normalize
number_format numfmt_create numfmt_format numfmt_format_currency
numfmt_get_attribute numfmt_get_error_code numfmt_get_error_message
numfmt_get_locale numfmt_get_pattern numfmt_get_symbol
numfmt_get_text_attribute numfmt_parse numfmt_parse_currency
numfmt_set_attribute numfmt_set_pattern numfmt_set_symbol
numfmt_set_text_attribute oauth_get_sbs oauth_urlencode ob_clean ob_end_clean
ob_end_flush ob_flush ob_get_clean ob_get_contents ob_get_flush ob_get_length
ob_get_level ob_get_status ob_gzhandler ob_implicit_flush ob_list_handlers
ob_start octdec odbc_autocommit odbc_binmode odbc_close odbc_close_all
odbc_columnprivileges odbc_columns odbc_commit odbc_connect
odbc_connection_string_is_quoted odbc_connection_string_quote
odbc_connection_string_should_quote odbc_cursor odbc_data_source odbc_do
odbc_error odbc_errormsg odbc_exec odbc_execute odbc_fetch_array
odbc_fetch_into odbc_fetch_object odbc_fetch_row odbc_field_len
odbc_field_name odbc_field_num odbc_field_precision odbc_field_scale
odbc_field_type odbc_foreignkeys odbc_free_result odbc_gettypeinfo
odbc_longreadlen odbc_next_result odbc_num_fields odbc_num_rows odbc_pconnect
odbc_prepare odbc_primarykeys odbc_procedurecolumns odbc_procedures
odbc_result odbc_result_all odbc_rollback odbc_setoption odbc_specialcolumns
odbc_statistics odbc_tableprivileges odbc_tables opcache_compile_file
opcache_get_configuration opcache_get_status opcache_invalidate
opcache_is_script_cached opcache_reset opendir openlog
openssl_cipher_iv_length openssl_cipher_key_length openssl_cms_decrypt
openssl_cms_encrypt openssl_cms_read openssl_cms_sign openssl_cms_verify
openssl_csr_export openssl_csr_export_to_file openssl_csr_get_public_key
openssl_csr_get_subject openssl_csr_new openssl_csr_sign openssl_decrypt
openssl_dh_compute_key openssl_digest openssl_encrypt openssl_error_string
openssl_free_key openssl_get_cert_locations openssl_get_cipher_methods
openssl_get_curve_names openssl_get_md_methods openssl_get_privatekey
openssl_get_publickey openssl_open openssl_pbkdf2 openssl_pkcs12_export
openssl_pkcs12_export_to_file openssl_pkcs12_read openssl_pkcs7_decrypt
openssl_pkcs7_encrypt openssl_pkcs7_read openssl_pkcs7_sign
openssl_pkcs7_verify openssl_pkey_derive openssl_pkey_export
openssl_pkey_export_to_file openssl_pkey_free openssl_pkey_get_details
openssl_pkey_get_private openssl_pkey_get_public openssl_pkey_new
openssl_private_decrypt openssl_private_encrypt openssl_public_decrypt
openssl_public_encrypt openssl_random_pseudo_bytes openssl_seal openssl_sign
openssl_spki_export openssl_spki_export_challenge openssl_spki_new
openssl_spki_verify openssl_verify openssl_x509_check_private_key
openssl_x509_checkpurpose openssl_x509_export openssl_x509_export_to_file
openssl_x509_fingerprint openssl_x509_free openssl_x509_parse
openssl_x509_read openssl_x509_verify ord output_add_rewrite_var
output_reset_rewrite_vars pack parse_ini_file parse_ini_string parse_str
parse_url passthru password_algos password_get_info password_hash
password_needs_rehash password_verify pathinfo pclose pcntl_alarm
pcntl_async_signals pcntl_errno pcntl_exec pcntl_fork pcntl_get_last_error
pcntl_getpriority pcntl_setpriority pcntl_signal pcntl_signal_dispatch
pcntl_signal_get_handler pcntl_sigprocmask pcntl_sigtimedwait
pcntl_sigwaitinfo pcntl_strerror pcntl_unshare pcntl_wait pcntl_waitpid
pcntl_wexitstatus pcntl_wifcontinued pcntl_wifexited pcntl_wifsignaled
pcntl_wifstopped pcntl_wstopsig pcntl_wtermsig pdo_drivers pfsockopen
pg_affected_rows pg_cancel_query pg_client_encoding pg_clientencoding pg_close
pg_cmdtuples pg_connect pg_connect_poll pg_connection_busy pg_connection_reset
pg_connection_status pg_consume_input pg_convert pg_copy_from pg_copy_to
pg_dbname pg_delete pg_end_copy pg_errormessage pg_escape_bytea
pg_escape_identifier pg_escape_literal pg_escape_string pg_exec pg_execute
pg_fetch_all pg_fetch_all_columns pg_fetch_array pg_fetch_assoc
pg_fetch_object pg_fetch_result pg_fetch_row pg_field_is_null pg_field_name
pg_field_num pg_field_prtlen pg_field_size pg_field_table pg_field_type
pg_field_type_oid pg_fieldisnull pg_fieldname pg_fieldnum pg_fieldprtlen
pg_fieldsize pg_fieldtype pg_flush pg_free_result pg_freeresult pg_get_notify
pg_get_pid pg_get_result pg_getlastoid pg_host pg_insert pg_last_error
pg_last_notice pg_last_oid pg_lo_close pg_lo_create pg_lo_export pg_lo_import
pg_lo_open pg_lo_read pg_lo_read_all pg_lo_seek pg_lo_tell pg_lo_truncate
pg_lo_unlink pg_lo_write pg_loclose pg_locreate pg_loexport pg_loimport
pg_loopen pg_loread pg_loreadall pg_lounlink pg_lowrite pg_meta_data
pg_num_fields pg_num_rows pg_numfields pg_numrows pg_options
pg_parameter_status pg_pconnect pg_ping pg_port pg_prepare pg_put_line
pg_query pg_query_params pg_result pg_result_error pg_result_error_field
pg_result_seek pg_result_status pg_select pg_send_execute pg_send_prepare
pg_send_query pg_send_query_params pg_set_client_encoding
pg_set_error_context_visibility pg_set_error_verbosity pg_setclientencoding
pg_socket pg_trace pg_transaction_status pg_tty pg_unescape_bytea pg_untrace
pg_update pg_version php_ini_loaded_file php_ini_scanned_files php_sapi_name
php_strip_whitespace php_uname phpcredits phpinfo phpversion pi pinba_flush
pinba_get_data pinba_get_info pinba_hostname_set pinba_request_time_set
pinba_reset pinba_schema_set pinba_script_name_set pinba_server_name_set
pinba_tag_delete pinba_tag_get pinba_tag_set pinba_tags_get pinba_timer_add
pinba_timer_data_merge pinba_timer_data_replace pinba_timer_delete
pinba_timer_get_info pinba_timer_start pinba_timer_stop pinba_timer_tags_merge
pinba_timer_tags_replace pinba_timers_get pinba_timers_stop popen pos
posix_access posix_ctermid posix_eaccess posix_errno posix_fpathconf
posix_get_last_error posix_getcwd posix_getegid posix_geteuid posix_getgid
posix_getgrgid posix_getgrnam posix_getgroups posix_getlogin posix_getpgid
posix_getpgrp posix_getpid posix_getppid posix_getpwnam posix_getpwuid
posix_getrlimit posix_getsid posix_getuid posix_initgroups posix_isatty
posix_kill posix_mkfifo posix_mknod posix_pathconf posix_setegid posix_seteuid
posix_setgid posix_setpgid posix_setrlimit posix_setsid posix_setuid
posix_strerror posix_sysconf posix_times posix_ttyname posix_uname pow
preg_filter preg_grep preg_last_error preg_last_error_msg preg_match
preg_match_all preg_quote preg_replace preg_replace_callback
preg_replace_callback_array preg_split prev print_r printf proc_close
proc_get_status proc_nice proc_open proc_terminate property_exists
ps_add_bookmark ps_add_kerning ps_add_launchlink ps_add_ligature
ps_add_locallink ps_add_note ps_add_pdflink ps_add_weblink ps_arc ps_arcn
ps_begin_font ps_begin_glyph ps_begin_page ps_begin_pattern ps_begin_template
ps_circle ps_clip ps_close ps_close_image ps_closepath ps_closepath_stroke
ps_continue_text ps_curveto ps_delete ps_end_font ps_end_glyph ps_end_page
ps_end_pattern ps_end_template ps_fill ps_fill_stroke ps_findfont
ps_get_buffer ps_get_parameter ps_get_value ps_glyph_list ps_glyph_show
ps_glyph_width ps_hyphenate ps_include_file ps_lineto ps_makespotcolor
ps_moveto ps_new ps_open_file ps_open_image ps_open_image_file
ps_open_memory_image ps_place_image ps_rect ps_restore ps_rotate ps_save
ps_scale ps_set_border_color ps_set_border_dash ps_set_border_style
ps_set_info ps_set_parameter ps_set_text_pos ps_set_value ps_setcolor
ps_setdash ps_setflat ps_setfont ps_setlinecap ps_setlinejoin ps_setlinewidth
ps_setmiterlimit ps_setoverprintmode ps_setpolydash ps_shading
ps_shading_pattern ps_shfill ps_show ps_show2 ps_show_boxed ps_show_xy
ps_show_xy2 ps_string_geometry ps_stringwidth ps_stroke ps_symbol
ps_symbol_name ps_symbol_width ps_translate pspell_add_to_personal
pspell_add_to_session pspell_check pspell_clear_session pspell_config_create
pspell_config_data_dir pspell_config_dict_dir pspell_config_ignore
pspell_config_mode pspell_config_personal pspell_config_repl
pspell_config_runtogether pspell_config_save_repl pspell_new pspell_new_config
pspell_new_personal pspell_save_wordlist pspell_store_replacement
pspell_suggest putenv quoted_printable_decode quoted_printable_encode
quotemeta rad2deg rand random_bytes random_int range rawurldecode rawurlencode
readdir readfile readgzfile readline readline_add_history
readline_callback_handler_install readline_callback_handler_remove
readline_callback_read_char readline_clear_history
readline_completion_function readline_info readline_list_history
readline_on_new_line readline_read_history readline_redisplay
readline_write_history readlink realpath realpath_cache_get
realpath_cache_size register_shutdown_function register_tick_function rename
reset resourcebundle_count resourcebundle_create resourcebundle_get
resourcebundle_get_error_code resourcebundle_get_error_message
resourcebundle_locales restore_error_handler restore_exception_handler rewind
rewinddir rmdir round rrd_create rrd_error rrd_fetch rrd_first rrd_graph
rrd_info rrd_last rrd_lastupdate rrd_restore rrd_tune rrd_update rrd_version
rrd_xport rrdc_disconnect rsort rtrim scandir sem_acquire sem_get sem_release
sem_remove serialize session_abort session_cache_expire session_cache_limiter
session_commit session_create_id session_decode session_destroy session_encode
session_gc session_get_cookie_params session_id session_module_name
session_name session_regenerate_id session_register_shutdown session_reset
session_save_path session_set_cookie_params session_set_save_handler
session_start session_status session_unset session_write_close
set_error_handler set_exception_handler set_file_buffer set_include_path
set_time_limit setcookie setlocale setrawcookie settype sha1 sha1_file
shell_exec shm_attach shm_detach shm_get_var shm_has_var shm_put_var
shm_remove shm_remove_var shmop_close shmop_delete shmop_open shmop_read
shmop_size shmop_write show_source shuffle similar_text simplexml_import_dom
simplexml_load_file simplexml_load_string sin sinh sizeof sleep
smbclient_chmod smbclient_client_protocols smbclient_close smbclient_closedir
smbclient_creat smbclient_fstat smbclient_fstatvfs smbclient_ftruncate
smbclient_getxattr smbclient_library_version smbclient_listxattr
smbclient_lseek smbclient_mkdir smbclient_open smbclient_opendir
smbclient_option_get smbclient_option_set smbclient_read smbclient_readdir
smbclient_removexattr smbclient_rename smbclient_rmdir smbclient_setxattr
smbclient_stat smbclient_state_errno smbclient_state_free smbclient_state_init
smbclient_state_new smbclient_statvfs smbclient_unlink smbclient_utimes
smbclient_version smbclient_write snmp2_get snmp2_getnext snmp2_real_walk
snmp2_set snmp2_walk snmp3_get snmp3_getnext snmp3_real_walk snmp3_set
snmp3_walk snmp_get_quick_print snmp_get_valueretrieval snmp_read_mib
snmp_set_enum_print snmp_set_oid_numeric_print snmp_set_oid_output_format
snmp_set_quick_print snmp_set_valueretrieval snmpget snmpgetnext snmprealwalk
snmpset snmpwalk snmpwalkoid socket_accept socket_addrinfo_bind
socket_addrinfo_connect socket_addrinfo_explain socket_addrinfo_lookup
socket_atmark socket_bind socket_clear_error socket_close socket_cmsg_space
socket_connect socket_create socket_create_listen socket_create_pair
socket_export_stream socket_get_option socket_get_status socket_getopt
socket_getpeername socket_getsockname socket_import_stream socket_last_error
socket_listen socket_read socket_recv socket_recvfrom socket_recvmsg
socket_select socket_send socket_sendmsg socket_sendto socket_set_block
socket_set_blocking socket_set_nonblock socket_set_option socket_set_timeout
socket_setopt socket_shutdown socket_strerror socket_write sodium_add
sodium_base642bin sodium_bin2base64 sodium_bin2hex sodium_compare
sodium_crypto_aead_aes256gcm_decrypt sodium_crypto_aead_aes256gcm_encrypt
sodium_crypto_aead_aes256gcm_is_available sodium_crypto_aead_aes256gcm_keygen
sodium_crypto_aead_chacha20poly1305_decrypt
sodium_crypto_aead_chacha20poly1305_encrypt
sodium_crypto_aead_chacha20poly1305_ietf_decrypt
sodium_crypto_aead_chacha20poly1305_ietf_encrypt
sodium_crypto_aead_chacha20poly1305_ietf_keygen
sodium_crypto_aead_chacha20poly1305_keygen
sodium_crypto_aead_xchacha20poly1305_ietf_decrypt
sodium_crypto_aead_xchacha20poly1305_ietf_encrypt
sodium_crypto_aead_xchacha20poly1305_ietf_keygen sodium_crypto_auth
sodium_crypto_auth_keygen sodium_crypto_auth_verify sodium_crypto_box
sodium_crypto_box_keypair
sodium_crypto_box_keypair_from_secretkey_and_publickey sodium_crypto_box_open
sodium_crypto_box_publickey sodium_crypto_box_publickey_from_secretkey
sodium_crypto_box_seal sodium_crypto_box_seal_open sodium_crypto_box_secretkey
sodium_crypto_box_seed_keypair sodium_crypto_core_ristretto255_add
sodium_crypto_core_ristretto255_from_hash
sodium_crypto_core_ristretto255_is_valid_point
sodium_crypto_core_ristretto255_random
sodium_crypto_core_ristretto255_scalar_add
sodium_crypto_core_ristretto255_scalar_complement
sodium_crypto_core_ristretto255_scalar_invert
sodium_crypto_core_ristretto255_scalar_mul
sodium_crypto_core_ristretto255_scalar_negate
sodium_crypto_core_ristretto255_scalar_random
sodium_crypto_core_ristretto255_scalar_reduce
sodium_crypto_core_ristretto255_scalar_sub sodium_crypto_core_ristretto255_sub
sodium_crypto_generichash sodium_crypto_generichash_final
sodium_crypto_generichash_init sodium_crypto_generichash_keygen
sodium_crypto_generichash_update sodium_crypto_kdf_derive_from_key
sodium_crypto_kdf_keygen sodium_crypto_kx_client_session_keys
sodium_crypto_kx_keypair sodium_crypto_kx_publickey sodium_crypto_kx_secretkey
sodium_crypto_kx_seed_keypair sodium_crypto_kx_server_session_keys
sodium_crypto_pwhash sodium_crypto_pwhash_scryptsalsa208sha256
sodium_crypto_pwhash_scryptsalsa208sha256_str
sodium_crypto_pwhash_scryptsalsa208sha256_str_verify sodium_crypto_pwhash_str
sodium_crypto_pwhash_str_needs_rehash sodium_crypto_pwhash_str_verify
sodium_crypto_scalarmult sodium_crypto_scalarmult_base
sodium_crypto_scalarmult_ristretto255
sodium_crypto_scalarmult_ristretto255_base sodium_crypto_secretbox
sodium_crypto_secretbox_keygen sodium_crypto_secretbox_open
sodium_crypto_secretstream_xchacha20poly1305_init_pull
sodium_crypto_secretstream_xchacha20poly1305_init_push
sodium_crypto_secretstream_xchacha20poly1305_keygen
sodium_crypto_secretstream_xchacha20poly1305_pull
sodium_crypto_secretstream_xchacha20poly1305_push
sodium_crypto_secretstream_xchacha20poly1305_rekey sodium_crypto_shorthash
sodium_crypto_shorthash_keygen sodium_crypto_sign sodium_crypto_sign_detached
sodium_crypto_sign_ed25519_pk_to_curve25519
sodium_crypto_sign_ed25519_sk_to_curve25519 sodium_crypto_sign_keypair
sodium_crypto_sign_keypair_from_secretkey_and_publickey
sodium_crypto_sign_open sodium_crypto_sign_publickey
sodium_crypto_sign_publickey_from_secretkey sodium_crypto_sign_secretkey
sodium_crypto_sign_seed_keypair sodium_crypto_sign_verify_detached
sodium_crypto_stream sodium_crypto_stream_keygen
sodium_crypto_stream_xchacha20 sodium_crypto_stream_xchacha20_keygen
sodium_crypto_stream_xchacha20_xor sodium_crypto_stream_xchacha20_xor_ic
sodium_crypto_stream_xor sodium_hex2bin sodium_increment sodium_memcmp
sodium_memzero sodium_pad sodium_unpad solr_get_version sort soundex
spl_autoload spl_autoload_call spl_autoload_extensions spl_autoload_functions
spl_autoload_register spl_autoload_unregister spl_classes spl_object_hash
spl_object_id sprintf sqrt srand sscanf ssh2_auth_agent
ssh2_auth_hostbased_file ssh2_auth_none ssh2_auth_password
ssh2_auth_pubkey_file ssh2_connect ssh2_disconnect ssh2_exec ssh2_fetch_stream
ssh2_fingerprint ssh2_forward_accept ssh2_forward_listen
ssh2_methods_negotiated ssh2_poll ssh2_publickey_add ssh2_publickey_init
ssh2_publickey_list ssh2_publickey_remove ssh2_scp_recv ssh2_scp_send
ssh2_send_eof ssh2_sftp ssh2_sftp_chmod ssh2_sftp_lstat ssh2_sftp_mkdir
ssh2_sftp_readlink ssh2_sftp_realpath ssh2_sftp_rename ssh2_sftp_rmdir
ssh2_sftp_stat ssh2_sftp_symlink ssh2_sftp_unlink ssh2_shell ssh2_tunnel stat
stomp_abort stomp_ack stomp_begin stomp_close stomp_commit stomp_connect
stomp_connect_error stomp_error stomp_get_read_timeout stomp_get_session_id
stomp_has_frame stomp_nack stomp_read_frame stomp_send stomp_set_read_timeout
stomp_subscribe stomp_unsubscribe stomp_version str_contains str_decrement
str_ends_with str_getcsv str_increment str_ireplace str_pad str_repeat
str_replace str_rot13 str_shuffle str_split str_starts_with str_word_count
strcasecmp strchr strcmp strcoll strcspn stream_bucket_append
stream_bucket_make_writeable stream_bucket_new stream_bucket_prepend
stream_context_create stream_context_get_default stream_context_get_options
stream_context_get_params stream_context_set_default stream_context_set_option
stream_context_set_options stream_context_set_params stream_copy_to_stream
stream_filter_append stream_filter_prepend stream_filter_register
stream_filter_remove stream_get_contents stream_get_filters stream_get_line
stream_get_meta_data stream_get_transports stream_get_wrappers stream_is_local
stream_isatty stream_register_wrapper stream_resolve_include_path
stream_select stream_set_blocking stream_set_chunk_size stream_set_read_buffer
stream_set_timeout stream_set_write_buffer stream_socket_accept
stream_socket_client stream_socket_enable_crypto stream_socket_get_name
stream_socket_pair stream_socket_recvfrom stream_socket_sendto
stream_socket_server stream_socket_shutdown stream_supports_lock
stream_wrapper_register stream_wrapper_restore stream_wrapper_unregister
strftime strip_tags stripcslashes stripos stripslashes stristr strlen
strnatcasecmp strnatcmp strncasecmp strncmp strpbrk strpos strptime strrchr
strrev strripos strrpos strspn strstr strtok strtolower strtotime strtoupper
strtr strval substr substr_compare substr_count substr_replace symlink
sys_get_temp_dir sys_getloadavg syslog system tan tanh tempnam textdomain
tideways_xhprof_disable tideways_xhprof_enable tidy_access_count
tidy_clean_repair tidy_config_count tidy_diagnose tidy_error_count
tidy_get_body tidy_get_config tidy_get_error_buffer tidy_get_head
tidy_get_html tidy_get_html_ver tidy_get_opt_doc tidy_get_output
tidy_get_release tidy_get_root tidy_get_status tidy_getopt tidy_is_xhtml
tidy_is_xml tidy_parse_file tidy_parse_string tidy_repair_file
tidy_repair_string tidy_warning_count time time_nanosleep time_sleep_until
timezone_abbreviations_list timezone_identifiers_list timezone_location_get
timezone_name_from_abbr timezone_name_get timezone_offset_get timezone_open
timezone_transitions_get timezone_version_get tmpfile token_get_all token_name
touch trait_exists transliterator_create transliterator_create_from_rules
transliterator_create_inverse transliterator_get_error_code
transliterator_get_error_message transliterator_list_ids
transliterator_transliterate trigger_error trim uasort ucfirst ucwords uksort
umask uniqid unixtojd unlink unpack unregister_tick_function unserialize
uopz_add_function uopz_allow_exit uopz_call_user_func
uopz_call_user_func_array uopz_del_function uopz_flags uopz_get_exit_status
uopz_get_hook uopz_get_mock uopz_get_property uopz_get_return uopz_get_static
uopz_redefine uopz_set_hook uopz_set_mock uopz_set_property uopz_set_return
uopz_set_static uopz_undefine uopz_unset_hook uopz_unset_mock
uopz_unset_return uploadprogress_get_contents uploadprogress_get_info
urldecode urlencode use_soap_error_handler user_error usleep usort utf8_decode
utf8_encode uuid_compare uuid_create uuid_generate_md5 uuid_generate_sha1
uuid_is_null uuid_is_valid uuid_mac uuid_parse uuid_time uuid_type
uuid_unparse uuid_variant var_dump var_export version_compare vfprintf vprintf
vsprintf wordwrap xdebug_break xdebug_call_class xdebug_call_file
xdebug_call_function xdebug_call_line xdebug_code_coverage_started
xdebug_connect_to_client xdebug_debug_zval xdebug_debug_zval_stdout
xdebug_dump_superglobals xdebug_get_code_coverage xdebug_get_collected_errors
xdebug_get_function_count xdebug_get_function_stack xdebug_get_gc_run_count
xdebug_get_gc_total_collected_roots xdebug_get_gcstats_filename
xdebug_get_headers xdebug_get_monitored_functions xdebug_get_profiler_filename
xdebug_get_stack_depth xdebug_get_tracefile_name xdebug_info
xdebug_is_debugger_active xdebug_memory_usage xdebug_notify
xdebug_peak_memory_usage xdebug_print_function_stack xdebug_set_filter
xdebug_start_code_coverage xdebug_start_error_collection
xdebug_start_function_monitor xdebug_start_gcstats xdebug_start_trace
xdebug_stop_code_coverage xdebug_stop_error_collection
xdebug_stop_function_monitor xdebug_stop_gcstats xdebug_stop_trace
xdebug_time_index xdebug_var_dump xml_error_string xml_get_current_byte_index
xml_get_current_column_number xml_get_current_line_number xml_get_error_code
xml_parse xml_parse_into_struct xml_parser_create xml_parser_create_ns
xml_parser_free xml_parser_get_option xml_parser_set_option
xml_set_character_data_handler xml_set_default_handler xml_set_element_handler
xml_set_end_namespace_decl_handler xml_set_external_entity_ref_handler
xml_set_notation_decl_handler xml_set_object
xml_set_processing_instruction_handler xml_set_start_namespace_decl_handler
xml_set_unparsed_entity_decl_handler xmlrpc_decode xmlrpc_decode_request
xmlrpc_encode xmlrpc_encode_request xmlrpc_get_type xmlrpc_is_fault
xmlrpc_parse_method_descriptions xmlrpc_server_add_introspection_data
xmlrpc_server_call_method xmlrpc_server_create xmlrpc_server_destroy
xmlrpc_server_register_introspection_callback xmlrpc_server_register_method
xmlrpc_set_type xmlwriter_end_attribute xmlwriter_end_cdata
xmlwriter_end_comment xmlwriter_end_document xmlwriter_end_dtd
xmlwriter_end_dtd_attlist xmlwriter_end_dtd_element xmlwriter_end_dtd_entity
xmlwriter_end_element xmlwriter_end_pi xmlwriter_flush
xmlwriter_full_end_element xmlwriter_open_memory xmlwriter_open_uri
xmlwriter_output_memory xmlwriter_set_indent xmlwriter_set_indent_string
xmlwriter_start_attribute xmlwriter_start_attribute_ns xmlwriter_start_cdata
xmlwriter_start_comment xmlwriter_start_document xmlwriter_start_dtd
xmlwriter_start_dtd_attlist xmlwriter_start_dtd_element
xmlwriter_start_dtd_entity xmlwriter_start_element xmlwriter_start_element_ns
xmlwriter_start_pi xmlwriter_text xmlwriter_write_attribute
xmlwriter_write_attribute_ns xmlwriter_write_cdata xmlwriter_write_comment
xmlwriter_write_dtd xmlwriter_write_dtd_attlist xmlwriter_write_dtd_element
xmlwriter_write_dtd_entity xmlwriter_write_element xmlwriter_write_element_ns
xmlwriter_write_pi xmlwriter_write_raw yaml_emit yaml_emit_file yaml_parse
yaml_parse_file yaml_parse_url zend_version zip_close zip_entry_close
zip_entry_compressedsize zip_entry_compressionmethod zip_entry_filesize
zip_entry_name zip_entry_open zip_entry_read zip_open zip_read zlib_decode
zlib_encode zlib_get_coding_type
"""
BUILTINS = frozenset(_BUILTIN_NAMES.split())

# The constants PHP 8.3.6 builds in with no namespace, as
# get_defined_constants() lists them in the same install as BUILTINS (read
# 2026-11-08), but `TRUE`, `FALSE` and `NULL`, which PHP never looks up in
# a namespace. To update it, run `php -r 'foreach (get_defined_constants()
# as $k => $v) echo $k, "\n";'` there, and put here those without a `\`
# but those three, sorted, wrapped at 78 columns.
_BUILTIN_CONSTANT_NAMES = """
ABDAY_1 ABDAY_2 ABDAY_3 ABDAY_4 ABDAY_5 ABDAY_6 ABDAY_7 ABMON_1 ABMON_10
ABMON_11 ABMON_12 ABMON_2 ABMON_3 ABMON_4 ABMON_5 ABMON_6 ABMON_7 ABMON_8
ABMON_9 AF_INET AF_INET6 AF_UNIX AI_ADDRCONFIG AI_ALL AI_CANONIDN AI_CANONNAME
AI_IDN AI_NUMERICHOST AI_NUMERICSERV AI_PASSIVE AI_V4MAPPED ALT_DIGITS
AMQP_AUTOACK AMQP_AUTODELETE AMQP_DURABLE AMQP_EXCLUSIVE AMQP_EX_TYPE_DIRECT
AMQP_EX_TYPE_FANOUT AMQP_EX_TYPE_HEADERS AMQP_EX_TYPE_TOPIC AMQP_IFEMPTY
AMQP_IFUNUSED AMQP_IMMEDIATE AMQP_INTERNAL AMQP_JUST_CONSUME AMQP_MANDATORY
AMQP_MULTIPLE AMQP_NOLOCAL AMQP_NOPARAM AMQP_NOWAIT
AMQP_OS_SOCKET_TIMEOUT_ERRNO AMQP_PASSIVE AMQP_REQUEUE
AMQP_SASL_METHOD_EXTERNAL AMQP_SASL_METHOD_PLAIN AM_STR APACHE_MAP
ARRAY_FILTER_USE_BOTH ARRAY_FILTER_USE_KEY ASSERT_ACTIVE ASSERT_BAIL
ASSERT_CALLBACK ASSERT_EXCEPTION ASSERT_WARNING BUS_ADRALN BUS_ADRERR
BUS_OBJERR CAL_DOW_DAYNO CAL_DOW_LONG CAL_DOW_SHORT
CAL_EASTER_ALWAYS_GREGORIAN CAL_EASTER_ALWAYS_JULIAN CAL_EASTER_DEFAULT
CAL_EASTER_ROMAN CAL_FRENCH CAL_GREGORIAN CAL_JEWISH CAL_JEWISH_ADD_ALAFIM
CAL_JEWISH_ADD_ALAFIM_GERESH CAL_JEWISH_ADD_GERESHAYIM CAL_JULIAN
CAL_MONTH_FRENCH CAL_MONTH_GREGORIAN_LONG CAL_MONTH_GREGORIAN_SHORT
CAL_MONTH_JEWISH CAL_MONTH_JULIAN_LONG CAL_MONTH_JULIAN_SHORT CAL_NUM_CALS
CASE_LOWER CASE_UPPER CHAR_MAX CLD_CONTINUED CLD_DUMPED CLD_EXITED CLD_KILLED
CLD_STOPPED CLD_TRAPPED CLONE_NEWCGROUP CLONE_NEWIPC CLONE_NEWNET CLONE_NEWNS
CLONE_NEWPID CLONE_NEWUSER CLONE_NEWUTS CL_EXPUNGE CODESET CONNECTION_ABORTED
CONNECTION_NORMAL CONNECTION_TIMEOUT COUNT_NORMAL COUNT_RECURSIVE CP_MOVE
CP_UID CREDITS_ALL CREDITS_DOCS CREDITS_FULLPAGE CREDITS_GENERAL CREDITS_GROUP
CREDITS_MODULES CREDITS_QA CREDITS_SAPI CRNCYSTR CRYPT_BLOWFISH CRYPT_EXT_DES
CRYPT_MD5 CRYPT_SALT_LENGTH CRYPT_SHA256 CRYPT_SHA512 CRYPT_STD_DES
CURLALTSVC_H1 CURLALTSVC_H2 CURLALTSVC_H3 CURLALTSVC_READONLYFILE CURLAUTH_ANY
CURLAUTH_ANYSAFE CURLAUTH_AWS_SIGV4 CURLAUTH_BASIC CURLAUTH_BEARER
CURLAUTH_DIGEST CURLAUTH_DIGEST_IE CURLAUTH_GSSAPI CURLAUTH_GSSNEGOTIATE
CURLAUTH_NEGOTIATE CURLAUTH_NONE CURLAUTH_NTLM CURLAUTH_NTLM_WB CURLAUTH_ONLY
CURLE_ABORTED_BY_CALLBACK CURLE_BAD_CALLING_ORDER CURLE_BAD_CONTENT_ENCODING
CURLE_BAD_DOWNLOAD_RESUME CURLE_BAD_FUNCTION_ARGUMENT
CURLE_BAD_PASSWORD_ENTERED CURLE_COULDNT_CONNECT CURLE_COULDNT_RESOLVE_HOST
CURLE_COULDNT_RESOLVE_PROXY CURLE_FAILED_INIT CURLE_FILESIZE_EXCEEDED
CURLE_FILE_COULDNT_READ_FILE CURLE_FTP_ACCESS_DENIED
CURLE_FTP_BAD_DOWNLOAD_RESUME CURLE_FTP_CANT_GET_HOST CURLE_FTP_CANT_RECONNECT
CURLE_FTP_COULDNT_GET_SIZE CURLE_FTP_COULDNT_RETR_FILE
CURLE_FTP_COULDNT_SET_ASCII CURLE_FTP_COULDNT_SET_BINARY
CURLE_FTP_COULDNT_STOR_FILE CURLE_FTP_COULDNT_USE_REST CURLE_FTP_PARTIAL_FILE
CURLE_FTP_PORT_FAILED CURLE_FTP_QUOTE_ERROR CURLE_FTP_SSL_FAILED
CURLE_FTP_USER_PASSWORD_INCORRECT CURLE_FTP_WEIRD_227_FORMAT
CURLE_FTP_WEIRD_PASS_REPLY CURLE_FTP_WEIRD_PASV_REPLY
CURLE_FTP_WEIRD_SERVER_REPLY CURLE_FTP_WEIRD_USER_REPLY CURLE_FTP_WRITE_ERROR
CURLE_FUNCTION_NOT_FOUND CURLE_GOT_NOTHING CURLE_HTTP_NOT_FOUND
CURLE_HTTP_PORT_FAILED CURLE_HTTP_POST_ERROR CURLE_HTTP_RANGE_ERROR
CURLE_HTTP_RETURNED_ERROR CURLE_LDAP_CANNOT_BIND CURLE_LDAP_INVALID_URL
CURLE_LDAP_SEARCH_FAILED CURLE_LIBRARY_NOT_FOUND CURLE_MALFORMAT_USER
CURLE_OBSOLETE CURLE_OK CURLE_OPERATION_TIMEDOUT CURLE_OPERATION_TIMEOUTED
CURLE_OUT_OF_MEMORY CURLE_PARTIAL_FILE CURLE_PROXY CURLE_READ_ERROR
CURLE_RECV_ERROR CURLE_SEND_ERROR CURLE_SHARE_IN_USE CURLE_SSH
CURLE_SSL_CACERT CURLE_SSL_CACERT_BADFILE CURLE_SSL_CERTPROBLEM
CURLE_SSL_CIPHER CURLE_SSL_CONNECT_ERROR CURLE_SSL_ENGINE_NOTFOUND
CURLE_SSL_ENGINE_SETFAILED CURLE_SSL_PEER_CERTIFICATE
CURLE_SSL_PINNEDPUBKEYNOTMATCH CURLE_TELNET_OPTION_SYNTAX
CURLE_TOO_MANY_REDIRECTS CURLE_UNKNOWN_TELNET_OPTION
CURLE_UNSUPPORTED_PROTOCOL CURLE_URL_MALFORMAT CURLE_URL_MALFORMAT_USER
CURLE_WEIRD_SERVER_REPLY CURLE_WRITE_ERROR CURLFTPAUTH_DEFAULT CURLFTPAUTH_SSL
CURLFTPAUTH_TLS CURLFTPMETHOD_DEFAULT CURLFTPMETHOD_MULTICWD
CURLFTPMETHOD_NOCWD CURLFTPMETHOD_SINGLECWD CURLFTPSSL_ALL
CURLFTPSSL_CCC_ACTIVE CURLFTPSSL_CCC_NONE CURLFTPSSL_CCC_PASSIVE
CURLFTPSSL_CONTROL CURLFTPSSL_NONE CURLFTPSSL_TRY CURLFTP_CREATE_DIR
CURLFTP_CREATE_DIR_NONE CURLFTP_CREATE_DIR_RETRY CURLGSSAPI_DELEGATION_FLAG
CURLGSSAPI_DELEGATION_POLICY_FLAG CURLHEADER_SEPARATE CURLHEADER_UNIFIED
CURLHSTS_ENABLE CURLHSTS_READONLYFILE CURLINFO_APPCONNECT_TIME
CURLINFO_APPCONNECT_TIME_T CURLINFO_CAINFO CURLINFO_CAPATH CURLINFO_CERTINFO
CURLINFO_CONDITION_UNMET CURLINFO_CONNECT_TIME CURLINFO_CONNECT_TIME_T
CURLINFO_CONTENT_LENGTH_DOWNLOAD CURLINFO_CONTENT_LENGTH_DOWNLOAD_T
CURLINFO_CONTENT_LENGTH_UPLOAD CURLINFO_CONTENT_LENGTH_UPLOAD_T
CURLINFO_CONTENT_TYPE CURLINFO_COOKIELIST CURLINFO_EFFECTIVE_METHOD
CURLINFO_EFFECTIVE_URL CURLINFO_FILETIME CURLINFO_FILETIME_T
CURLINFO_FTP_ENTRY_PATH CURLINFO_HEADER_OUT CURLINFO_HEADER_SIZE
CURLINFO_HTTPAUTH_AVAIL CURLINFO_HTTP_CODE CURLINFO_HTTP_CONNECTCODE
CURLINFO_HTTP_VERSION CURLINFO_LASTONE CURLINFO_LOCAL_IP CURLINFO_LOCAL_PORT
CURLINFO_NAMELOOKUP_TIME CURLINFO_NAMELOOKUP_TIME_T CURLINFO_NUM_CONNECTS
CURLINFO_OS_ERRNO CURLINFO_PRETRANSFER_TIME CURLINFO_PRETRANSFER_TIME_T
CURLINFO_PRIMARY_IP CURLINFO_PRIMARY_PORT CURLINFO_PRIVATE CURLINFO_PROTOCOL
CURLINFO_PROXYAUTH_AVAIL CURLINFO_PROXY_ERROR CURLINFO_PROXY_SSL_VERIFYRESULT
CURLINFO_REDIRECT_COUNT CURLINFO_REDIRECT_TIME CURLINFO_REDIRECT_TIME_T
CURLINFO_REDIRECT_URL CURLINFO_REFERER CURLINFO_REQUEST_SIZE
CURLINFO_RESPONSE_CODE CURLINFO_RETRY_AFTER CURLINFO_RTSP_CLIENT_CSEQ
CURLINFO_RTSP_CSEQ_RECV CURLINFO_RTSP_SERVER_CSEQ CURLINFO_RTSP_SESSION_ID
CURLINFO_SCHEME CURLINFO_SIZE_DOWNLOAD CURLINFO_SIZE_DOWNLOAD_T
CURLINFO_SIZE_UPLOAD CURLINFO_SIZE_UPLOAD_T CURLINFO_SPEED_DOWNLOAD
CURLINFO_SPEED_DOWNLOAD_T CURLINFO_SPEED_UPLOAD CURLINFO_SPEED_UPLOAD_T
CURLINFO_SSL_ENGINES CURLINFO_SSL_VERIFYRESULT CURLINFO_STARTTRANSFER_TIME
CURLINFO_STARTTRANSFER_TIME_T CURLINFO_TOTAL_TIME CURLINFO_TOTAL_TIME_T
CURLKHMATCH_LAST CURLKHMATCH_MISMATCH CURLKHMATCH_MISSING CURLKHMATCH_OK
CURLMIMEOPT_FORMESCAPE CURLMOPT_CHUNK_LENGTH_PENALTY_SIZE
CURLMOPT_CONTENT_LENGTH_PENALTY_SIZE CURLMOPT_MAXCONNECTS
CURLMOPT_MAX_CONCURRENT_STREAMS CURLMOPT_MAX_HOST_CONNECTIONS
CURLMOPT_MAX_PIPELINE_LENGTH CURLMOPT_MAX_TOTAL_CONNECTIONS
CURLMOPT_PIPELINING CURLMOPT_PUSHFUNCTION CURLMSG_DONE CURLM_ADDED_ALREADY
CURLM_BAD_EASY_HANDLE CURLM_BAD_HANDLE CURLM_CALL_MULTI_PERFORM
CURLM_INTERNAL_ERROR CURLM_OK CURLM_OUT_OF_MEMORY CURLOPT_ABSTRACT_UNIX_SOCKET
CURLOPT_ACCEPTTIMEOUT_MS CURLOPT_ACCEPT_ENCODING CURLOPT_ADDRESS_SCOPE
CURLOPT_ALTSVC CURLOPT_ALTSVC_CTRL CURLOPT_APPEND CURLOPT_AUTOREFERER
CURLOPT_AWS_SIGV4 CURLOPT_BINARYTRANSFER CURLOPT_BUFFERSIZE CURLOPT_CAINFO
CURLOPT_CAINFO_BLOB CURLOPT_CAPATH CURLOPT_CA_CACHE_TIMEOUT CURLOPT_CERTINFO
CURLOPT_CONNECTTIMEOUT CURLOPT_CONNECTTIMEOUT_MS CURLOPT_CONNECT_ONLY
CURLOPT_CONNECT_TO CURLOPT_COOKIE CURLOPT_COOKIEFILE CURLOPT_COOKIEJAR
CURLOPT_COOKIELIST CURLOPT_COOKIESESSION CURLOPT_CRLF CURLOPT_CRLFILE
CURLOPT_CUSTOMREQUEST CURLOPT_DEFAULT_PROTOCOL CURLOPT_DIRLISTONLY
CURLOPT_DISALLOW_USERNAME_IN_URL CURLOPT_DNS_CACHE_TIMEOUT
CURLOPT_DNS_INTERFACE CURLOPT_DNS_LOCAL_IP4 CURLOPT_DNS_LOCAL_IP6
CURLOPT_DNS_SERVERS CURLOPT_DNS_SHUFFLE_ADDRESSES CURLOPT_DNS_USE_GLOBAL_CACHE
CURLOPT_DOH_SSL_VERIFYHOST CURLOPT_DOH_SSL_VERIFYPEER
CURLOPT_DOH_SSL_VERIFYSTATUS CURLOPT_DOH_URL CURLOPT_EGDSOCKET
CURLOPT_ENCODING CURLOPT_EXPECT_100_TIMEOUT_MS CURLOPT_FAILONERROR
CURLOPT_FILE CURLOPT_FILETIME CURLOPT_FNMATCH_FUNCTION CURLOPT_FOLLOWLOCATION
CURLOPT_FORBID_REUSE CURLOPT_FRESH_CONNECT CURLOPT_FTPAPPEND
CURLOPT_FTPLISTONLY CURLOPT_FTPPORT CURLOPT_FTPSSLAUTH CURLOPT_FTP_ACCOUNT
CURLOPT_FTP_ALTERNATIVE_TO_USER CURLOPT_FTP_CREATE_MISSING_DIRS
CURLOPT_FTP_FILEMETHOD CURLOPT_FTP_RESPONSE_TIMEOUT CURLOPT_FTP_SKIP_PASV_IP
CURLOPT_FTP_SSL CURLOPT_FTP_SSL_CCC CURLOPT_FTP_USE_EPRT CURLOPT_FTP_USE_EPSV
CURLOPT_FTP_USE_PRET CURLOPT_GSSAPI_DELEGATION
CURLOPT_HAPPY_EYEBALLS_TIMEOUT_MS CURLOPT_HAPROXYPROTOCOL CURLOPT_HEADER
CURLOPT_HEADERFUNCTION CURLOPT_HEADEROPT CURLOPT_HSTS CURLOPT_HSTS_CTRL
CURLOPT_HTTP09_ALLOWED CURLOPT_HTTP200ALIASES CURLOPT_HTTPAUTH CURLOPT_HTTPGET
CURLOPT_HTTPHEADER CURLOPT_HTTPPROXYTUNNEL CURLOPT_HTTP_CONTENT_DECODING
CURLOPT_HTTP_TRANSFER_DECODING CURLOPT_HTTP_VERSION
CURLOPT_IGNORE_CONTENT_LENGTH CURLOPT_INFILE CURLOPT_INFILESIZE
CURLOPT_INTERFACE CURLOPT_IPRESOLVE CURLOPT_ISSUERCERT CURLOPT_ISSUERCERT_BLOB
CURLOPT_KEEP_SENDING_ON_ERROR CURLOPT_KEYPASSWD CURLOPT_KRB4LEVEL
CURLOPT_KRBLEVEL CURLOPT_LOCALPORT CURLOPT_LOCALPORTRANGE
CURLOPT_LOGIN_OPTIONS CURLOPT_LOW_SPEED_LIMIT CURLOPT_LOW_SPEED_TIME
CURLOPT_MAIL_AUTH CURLOPT_MAIL_FROM CURLOPT_MAIL_RCPT
CURLOPT_MAIL_RCPT_ALLLOWFAILS CURLOPT_MAXAGE_CONN CURLOPT_MAXCONNECTS
CURLOPT_MAXFILESIZE CURLOPT_MAXFILESIZE_LARGE CURLOPT_MAXLIFETIME_CONN
CURLOPT_MAXREDIRS CURLOPT_MAX_RECV_SPEED_LARGE CURLOPT_MAX_SEND_SPEED_LARGE
CURLOPT_MIME_OPTIONS CURLOPT_NETRC CURLOPT_NETRC_FILE
CURLOPT_NEW_DIRECTORY_PERMS CURLOPT_NEW_FILE_PERMS CURLOPT_NOBODY
CURLOPT_NOPROGRESS CURLOPT_NOPROXY CURLOPT_NOSIGNAL CURLOPT_PASSWORD
CURLOPT_PATH_AS_IS CURLOPT_PINNEDPUBLICKEY CURLOPT_PIPEWAIT CURLOPT_PORT
CURLOPT_POST CURLOPT_POSTFIELDS CURLOPT_POSTQUOTE CURLOPT_POSTREDIR
CURLOPT_PREQUOTE CURLOPT_PRE_PROXY CURLOPT_PRIVATE CURLOPT_PROGRESSFUNCTION
CURLOPT_PROTOCOLS CURLOPT_PROTOCOLS_STR CURLOPT_PROXY CURLOPT_PROXYAUTH
CURLOPT_PROXYHEADER CURLOPT_PROXYPASSWORD CURLOPT_PROXYPORT CURLOPT_PROXYTYPE
CURLOPT_PROXYUSERNAME CURLOPT_PROXYUSERPWD CURLOPT_PROXY_CAINFO
CURLOPT_PROXY_CAINFO_BLOB CURLOPT_PROXY_CAPATH CURLOPT_PROXY_CRLFILE
CURLOPT_PROXY_ISSUERCERT CURLOPT_PROXY_ISSUERCERT_BLOB CURLOPT_PROXY_KEYPASSWD
CURLOPT_PROXY_PINNEDPUBLICKEY CURLOPT_PROXY_SERVICE_NAME CURLOPT_PROXY_SSLCERT
CURLOPT_PROXY_SSLCERTTYPE CURLOPT_PROXY_SSLCERT_BLOB CURLOPT_PROXY_SSLKEY
CURLOPT_PROXY_SSLKEYTYPE CURLOPT_PROXY_SSLKEY_BLOB CURLOPT_PROXY_SSLVERSION
CURLOPT_PROXY_SSL_CIPHER_LIST CURLOPT_PROXY_SSL_OPTIONS
CURLOPT_PROXY_SSL_VERIFYHOST CURLOPT_PROXY_SSL_VERIFYPEER
CURLOPT_PROXY_TLS13_CIPHERS CURLOPT_PROXY_TLSAUTH_PASSWORD
CURLOPT_PROXY_TLSAUTH_TYPE CURLOPT_PROXY_TLSAUTH_USERNAME
CURLOPT_PROXY_TRANSFER_MODE CURLOPT_PUT CURLOPT_QUICK_EXIT CURLOPT_QUOTE
CURLOPT_RANDOM_FILE CURLOPT_RANGE CURLOPT_READDATA CURLOPT_READFUNCTION
CURLOPT_REDIR_PROTOCOLS CURLOPT_REDIR_PROTOCOLS_STR CURLOPT_REFERER
CURLOPT_REQUEST_TARGET CURLOPT_RESOLVE CURLOPT_RESUME_FROM
CURLOPT_RETURNTRANSFER CURLOPT_RTSP_CLIENT_CSEQ CURLOPT_RTSP_REQUEST
CURLOPT_RTSP_SERVER_CSEQ CURLOPT_RTSP_SESSION_ID CURLOPT_RTSP_STREAM_URI
CURLOPT_RTSP_TRANSPORT CURLOPT_SAFE_UPLOAD CURLOPT_SASL_AUTHZID
CURLOPT_SASL_IR CURLOPT_SERVICE_NAME CURLOPT_SHARE CURLOPT_SOCKS5_AUTH
CURLOPT_SOCKS5_GSSAPI_NEC CURLOPT_SOCKS5_GSSAPI_SERVICE CURLOPT_SSH_AUTH_TYPES
CURLOPT_SSH_COMPRESSION CURLOPT_SSH_HOSTKEYFUNCTION
CURLOPT_SSH_HOST_PUBLIC_KEY_MD5 CURLOPT_SSH_HOST_PUBLIC_KEY_SHA256
CURLOPT_SSH_KNOWNHOSTS CURLOPT_SSH_PRIVATE_KEYFILE CURLOPT_SSH_PUBLIC_KEYFILE
CURLOPT_SSLCERT CURLOPT_SSLCERTPASSWD CURLOPT_SSLCERTTYPE CURLOPT_SSLCERT_BLOB
CURLOPT_SSLENGINE CURLOPT_SSLENGINE_DEFAULT CURLOPT_SSLKEY
CURLOPT_SSLKEYPASSWD CURLOPT_SSLKEYTYPE CURLOPT_SSLKEY_BLOB CURLOPT_SSLVERSION
CURLOPT_SSL_CIPHER_LIST CURLOPT_SSL_EC_CURVES CURLOPT_SSL_ENABLE_ALPN
CURLOPT_SSL_ENABLE_NPN CURLOPT_SSL_FALSESTART CURLOPT_SSL_OPTIONS
CURLOPT_SSL_SESSIONID_CACHE CURLOPT_SSL_VERIFYHOST CURLOPT_SSL_VERIFYPEER
CURLOPT_SSL_VERIFYSTATUS CURLOPT_STDERR CURLOPT_STREAM_WEIGHT
CURLOPT_SUPPRESS_CONNECT_HEADERS CURLOPT_TCP_FASTOPEN CURLOPT_TCP_KEEPALIVE
CURLOPT_TCP_KEEPIDLE CURLOPT_TCP_KEEPINTVL CURLOPT_TCP_NODELAY
CURLOPT_TELNETOPTIONS CURLOPT_TFTP_BLKSIZE CURLOPT_TFTP_NO_OPTIONS
CURLOPT_TIMECONDITION CURLOPT_TIMEOUT CURLOPT_TIMEOUT_MS CURLOPT_TIMEVALUE
CURLOPT_TIMEVALUE_LARGE CURLOPT_TLS13_CIPHERS CURLOPT_TLSAUTH_PASSWORD
CURLOPT_TLSAUTH_TYPE CURLOPT_TLSAUTH_USERNAME CURLOPT_TRANSFERTEXT
CURLOPT_TRANSFER_ENCODING CURLOPT_UNIX_SOCKET_PATH CURLOPT_UNRESTRICTED_AUTH
CURLOPT_UPKEEP_INTERVAL_MS CURLOPT_UPLOAD CURLOPT_UPLOAD_BUFFERSIZE
CURLOPT_URL CURLOPT_USERAGENT CURLOPT_USERNAME CURLOPT_USERPWD CURLOPT_USE_SSL
CURLOPT_VERBOSE CURLOPT_WILDCARDMATCH CURLOPT_WRITEFUNCTION
CURLOPT_WRITEHEADER CURLOPT_WS_OPTIONS CURLOPT_XFERINFOFUNCTION
CURLOPT_XOAUTH2_BEARER CURLPAUSE_ALL CURLPAUSE_CONT CURLPAUSE_RECV
CURLPAUSE_RECV_CONT CURLPAUSE_SEND CURLPAUSE_SEND_CONT CURLPIPE_HTTP1
CURLPIPE_MULTIPLEX CURLPIPE_NOTHING CURLPROTO_ALL CURLPROTO_DICT
CURLPROTO_FILE CURLPROTO_FTP CURLPROTO_FTPS CURLPROTO_GOPHER CURLPROTO_HTTP
CURLPROTO_HTTPS CURLPROTO_IMAP CURLPROTO_IMAPS CURLPROTO_LDAP CURLPROTO_LDAPS
CURLPROTO_MQTT CURLPROTO_POP3 CURLPROTO_POP3S CURLPROTO_RTMP CURLPROTO_RTMPE
CURLPROTO_RTMPS CURLPROTO_RTMPT CURLPROTO_RTMPTE CURLPROTO_RTMPTS
CURLPROTO_RTSP CURLPROTO_SCP CURLPROTO_SFTP CURLPROTO_SMB CURLPROTO_SMBS
CURLPROTO_SMTP CURLPROTO_SMTPS CURLPROTO_TELNET CURLPROTO_TFTP CURLPROXY_HTTP
CURLPROXY_HTTPS CURLPROXY_HTTP_1_0 CURLPROXY_SOCKS4 CURLPROXY_SOCKS4A
CURLPROXY_SOCKS5 CURLPROXY_SOCKS5_HOSTNAME CURLPX_BAD_ADDRESS_TYPE
CURLPX_BAD_VERSION CURLPX_CLOSED CURLPX_GSSAPI CURLPX_GSSAPI_PERMSG
CURLPX_GSSAPI_PROTECTION CURLPX_IDENTD CURLPX_IDENTD_DIFFER
CURLPX_LONG_HOSTNAME CURLPX_LONG_PASSWD CURLPX_LONG_USER CURLPX_NO_AUTH
CURLPX_OK CURLPX_RECV_ADDRESS CURLPX_RECV_AUTH CURLPX_RECV_CONNECT
CURLPX_RECV_REQACK CURLPX_REPLY_ADDRESS_TYPE_NOT_SUPPORTED
CURLPX_REPLY_COMMAND_NOT_SUPPORTED CURLPX_REPLY_CONNECTION_REFUSED
CURLPX_REPLY_GENERAL_SERVER_FAILURE CURLPX_REPLY_HOST_UNREACHABLE
CURLPX_REPLY_NETWORK_UNREACHABLE CURLPX_REPLY_NOT_ALLOWED
CURLPX_REPLY_TTL_EXPIRED CURLPX_REPLY_UNASSIGNED CURLPX_REQUEST_FAILED
CURLPX_RESOLVE_HOST CURLPX_SEND_AUTH CURLPX_SEND_CONNECT CURLPX_SEND_REQUEST
CURLPX_UNKNOWN_FAIL CURLPX_UNKNOWN_MODE CURLPX_USER_REJECTED CURLSHOPT_NONE
CURLSHOPT_SHARE CURLSHOPT_UNSHARE CURLSSH_AUTH_AGENT CURLSSH_AUTH_ANY
CURLSSH_AUTH_DEFAULT CURLSSH_AUTH_GSSAPI CURLSSH_AUTH_HOST
CURLSSH_AUTH_KEYBOARD CURLSSH_AUTH_NONE CURLSSH_AUTH_PASSWORD
CURLSSH_AUTH_PUBLICKEY CURLSSLOPT_ALLOW_BEAST CURLSSLOPT_AUTO_CLIENT_CERT
CURLSSLOPT_NATIVE_CA CURLSSLOPT_NO_PARTIALCHAIN CURLSSLOPT_NO_REVOKE
CURLSSLOPT_REVOKE_BEST_EFFORT CURLUSESSL_ALL CURLUSESSL_CONTROL
CURLUSESSL_NONE CURLUSESSL_TRY CURLVERSION_NOW CURLWS_RAW_MODE
CURL_FNMATCHFUNC_FAIL CURL_FNMATCHFUNC_MATCH CURL_FNMATCHFUNC_NOMATCH
CURL_HTTP_VERSION_1_0 CURL_HTTP_VERSION_1_1 CURL_HTTP_VERSION_2
CURL_HTTP_VERSION_2TLS CURL_HTTP_VERSION_2_0
CURL_HTTP_VERSION_2_PRIOR_KNOWLEDGE CURL_HTTP_VERSION_NONE CURL_IPRESOLVE_V4
CURL_IPRESOLVE_V6 CURL_IPRESOLVE_WHATEVER CURL_LOCK_DATA_CONNECT
CURL_LOCK_DATA_COOKIE CURL_LOCK_DATA_DNS CURL_LOCK_DATA_PSL
CURL_LOCK_DATA_SSL_SESSION CURL_MAX_READ_SIZE CURL_NETRC_IGNORED
CURL_NETRC_OPTIONAL CURL_NETRC_REQUIRED CURL_PUSH_DENY CURL_PUSH_OK
CURL_READFUNC_PAUSE CURL_REDIR_POST_301 CURL_REDIR_POST_302
CURL_REDIR_POST_303 CURL_REDIR_POST_ALL CURL_RTSPREQ_ANNOUNCE
CURL_RTSPREQ_DESCRIBE CURL_RTSPREQ_GET_PARAMETER CURL_RTSPREQ_OPTIONS
CURL_RTSPREQ_PAUSE CURL_RTSPREQ_PLAY CURL_RTSPREQ_RECEIVE CURL_RTSPREQ_RECORD
CURL_RTSPREQ_SETUP CURL_RTSPREQ_SET_PARAMETER CURL_RTSPREQ_TEARDOWN
CURL_SSLVERSION_DEFAULT CURL_SSLVERSION_MAX_DEFAULT CURL_SSLVERSION_MAX_NONE
CURL_SSLVERSION_MAX_TLSv1_0 CURL_SSLVERSION_MAX_TLSv1_1
CURL_SSLVERSION_MAX_TLSv1_2 CURL_SSLVERSION_MAX_TLSv1_3 CURL_SSLVERSION_SSLv2
CURL_SSLVERSION_SSLv3 CURL_SSLVERSION_TLSv1 CURL_SSLVERSION_TLSv1_0
CURL_SSLVERSION_TLSv1_1 CURL_SSLVERSION_TLSv1_2 CURL_SSLVERSION_TLSv1_3
CURL_TIMECOND_IFMODSINCE CURL_TIMECOND_IFUNMODSINCE CURL_TIMECOND_LASTMOD
CURL_TIMECOND_NONE CURL_TLSAUTH_SRP CURL_VERSION_ALTSVC CURL_VERSION_ASYNCHDNS
CURL_VERSION_BROTLI CURL_VERSION_CONV CURL_VERSION_CURLDEBUG
CURL_VERSION_DEBUG CURL_VERSION_GSASL CURL_VERSION_GSSAPI
CURL_VERSION_GSSNEGOTIATE CURL_VERSION_HSTS CURL_VERSION_HTTP2
CURL_VERSION_HTTP3 CURL_VERSION_HTTPS_PROXY CURL_VERSION_IDN CURL_VERSION_IPV6
CURL_VERSION_KERBEROS4 CURL_VERSION_KERBEROS5 CURL_VERSION_LARGEFILE
CURL_VERSION_LIBZ CURL_VERSION_MULTI_SSL CURL_VERSION_NTLM
CURL_VERSION_NTLM_WB CURL_VERSION_PSL CURL_VERSION_SPNEGO CURL_VERSION_SSL
CURL_VERSION_SSPI CURL_VERSION_TLSAUTH_SRP CURL_VERSION_UNICODE
CURL_VERSION_UNIX_SOCKETS CURL_VERSION_ZSTD CURL_WRITEFUNC_PAUSE
CURRENCY_SYMBOL DATE_ATOM DATE_COOKIE DATE_ISO8601 DATE_ISO8601_EXPANDED
DATE_RFC1036 DATE_RFC1123 DATE_RFC2822 DATE_RFC3339 DATE_RFC3339_EXTENDED
DATE_RFC7231 DATE_RFC822 DATE_RFC850 DATE_RSS DATE_W3C DAY_1 DAY_2 DAY_3 DAY_4
DAY_5 DAY_6 DAY_7 DBA_LMDB_NO_SUB_DIR DBA_LMDB_USE_SUB_DIR
DEBUG_BACKTRACE_IGNORE_ARGS DEBUG_BACKTRACE_PROVIDE_OBJECT DECIMAL_POINT
DEFAULT_INCLUDE_PATH DIRECTORY_SEPARATOR DNS_A DNS_A6 DNS_AAAA DNS_ALL DNS_ANY
DNS_CAA DNS_CNAME DNS_HINFO DNS_MX DNS_NAPTR DNS_NS DNS_PTR DNS_SOA DNS_SRV
DNS_TXT DOMSTRING_SIZE_ERR DOM_HIERARCHY_REQUEST_ERR DOM_INDEX_SIZE_ERR
DOM_INUSE_ATTRIBUTE_ERR DOM_INVALID_ACCESS_ERR DOM_INVALID_CHARACTER_ERR
DOM_INVALID_MODIFICATION_ERR DOM_INVALID_STATE_ERR DOM_NAMESPACE_ERR
DOM_NOT_FOUND_ERR DOM_NOT_SUPPORTED_ERR DOM_NO_DATA_ALLOWED_ERR
DOM_NO_MODIFICATION_ALLOWED_ERR DOM_PHP_ERR DOM_SYNTAX_ERR DOM_VALIDATION_ERR
DOM_WRONG_DOCUMENT_ERR D_FMT D_T_FMT ENC7BIT ENC8BIT ENCBASE64 ENCBINARY
ENCHANT_ISPELL ENCHANT_MYSPELL ENCOTHER ENCQUOTEDPRINTABLE ENT_COMPAT
ENT_DISALLOWED ENT_HTML401 ENT_HTML5 ENT_IGNORE ENT_NOQUOTES ENT_QUOTES
ENT_SUBSTITUTE ENT_XHTML ENT_XML1 ERA ERA_D_FMT ERA_D_T_FMT ERA_T_FMT ERA_YEAR
EXIF_USE_MBSTRING EXTR_IF_EXISTS EXTR_OVERWRITE EXTR_PREFIX_ALL
EXTR_PREFIX_IF_EXISTS EXTR_PREFIX_INVALID EXTR_PREFIX_SAME EXTR_REFS EXTR_SKIP
E_ALL E_COMPILE_ERROR E_COMPILE_WARNING E_CORE_ERROR E_CORE_WARNING
E_DEPRECATED E_ERROR E_NOTICE E_PARSE E_RECOVERABLE_ERROR E_STRICT
E_USER_DEPRECATED E_USER_ERROR E_USER_NOTICE E_USER_WARNING E_WARNING
FILEINFO_APPLE FILEINFO_CONTINUE FILEINFO_DEVICES FILEINFO_EXTENSION
FILEINFO_MIME FILEINFO_MIME_ENCODING FILEINFO_MIME_TYPE FILEINFO_NONE
FILEINFO_PRESERVE_ATIME FILEINFO_RAW FILEINFO_SYMLINK FILE_APPEND FILE_BINARY
FILE_IGNORE_NEW_LINES FILE_NO_DEFAULT_CONTEXT FILE_SKIP_EMPTY_LINES FILE_TEXT
FILE_USE_INCLUDE_PATH FILTER_CALLBACK FILTER_DEFAULT
FILTER_FLAG_ALLOW_FRACTION FILTER_FLAG_ALLOW_HEX FILTER_FLAG_ALLOW_OCTAL
FILTER_FLAG_ALLOW_SCIENTIFIC FILTER_FLAG_ALLOW_THOUSAND
FILTER_FLAG_EMAIL_UNICODE FILTER_FLAG_EMPTY_STRING_NULL FILTER_FLAG_ENCODE_AMP
FILTER_FLAG_ENCODE_HIGH FILTER_FLAG_ENCODE_LOW FILTER_FLAG_GLOBAL_RANGE
FILTER_FLAG_HOSTNAME FILTER_FLAG_IPV4 FILTER_FLAG_IPV6 FILTER_FLAG_NONE
FILTER_FLAG_NO_ENCODE_QUOTES FILTER_FLAG_NO_PRIV_RANGE
FILTER_FLAG_NO_RES_RANGE FILTER_FLAG_PATH_REQUIRED FILTER_FLAG_QUERY_REQUIRED
FILTER_FLAG_STRIP_BACKTICK FILTER_FLAG_STRIP_HIGH FILTER_FLAG_STRIP_LOW
FILTER_FORCE_ARRAY FILTER_NULL_ON_FAILURE FILTER_REQUIRE_ARRAY
FILTER_REQUIRE_SCALAR FILTER_SANITIZE_ADD_SLASHES FILTER_SANITIZE_EMAIL
FILTER_SANITIZE_ENCODED FILTER_SANITIZE_FULL_SPECIAL_CHARS
FILTER_SANITIZE_NUMBER_FLOAT FILTER_SANITIZE_NUMBER_INT
FILTER_SANITIZE_SPECIAL_CHARS FILTER_SANITIZE_STRING FILTER_SANITIZE_STRIPPED
FILTER_SANITIZE_URL FILTER_UNSAFE_RAW FILTER_VALIDATE_BOOL
FILTER_VALIDATE_BOOLEAN FILTER_VALIDATE_DOMAIN FILTER_VALIDATE_EMAIL
FILTER_VALIDATE_FLOAT FILTER_VALIDATE_INT FILTER_VALIDATE_IP
FILTER_VALIDATE_MAC FILTER_VALIDATE_REGEXP FILTER_VALIDATE_URL FNM_CASEFOLD
FNM_NOESCAPE FNM_PATHNAME FNM_PERIOD FORCE_DEFLATE FORCE_GZIP FPE_FLTDIV
FPE_FLTINV FPE_FLTOVF FPE_FLTRES FPE_FLTSUB FPE_FLTUND FPE_INTDIV FPE_INTOVF
FRAC_DIGITS FTP_ASCII FTP_AUTORESUME FTP_AUTOSEEK FTP_BINARY FTP_FAILED
FTP_FINISHED FTP_IMAGE FTP_MOREDATA FTP_TEXT FTP_TIMEOUT_SEC
FTP_USEPASVADDRESS FT_INTERNAL FT_NOT FT_PEEK FT_PREFETCHTEXT FT_UID
GD_BUNDLED GD_EXTRA_VERSION GD_MAJOR_VERSION GD_MINOR_VERSION
GD_RELEASE_VERSION GD_VERSION GEARMAN_ARGS_BUFFER_SIZE
GEARMAN_CLIENT_ALLOCATED GEARMAN_CLIENT_FREE_TASKS GEARMAN_CLIENT_NON_BLOCKING
GEARMAN_CLIENT_NO_NEW GEARMAN_CLIENT_STATE_IDLE GEARMAN_CLIENT_STATE_NEW
GEARMAN_CLIENT_STATE_PACKET GEARMAN_CLIENT_STATE_SUBMIT
GEARMAN_CLIENT_TASK_IN_USE GEARMAN_CLIENT_UNBUFFERED_RESULT
GEARMAN_COMMAND_ALL_YOURS GEARMAN_COMMAND_CANT_DO GEARMAN_COMMAND_CAN_DO
GEARMAN_COMMAND_CAN_DO_TIMEOUT GEARMAN_COMMAND_ECHO_REQ
GEARMAN_COMMAND_ECHO_RES GEARMAN_COMMAND_ERROR GEARMAN_COMMAND_GET_STATUS
GEARMAN_COMMAND_GRAB_JOB GEARMAN_COMMAND_GRAB_JOB_UNIQ
GEARMAN_COMMAND_JOB_ASSIGN GEARMAN_COMMAND_JOB_ASSIGN_UNIQ
GEARMAN_COMMAND_JOB_CREATED GEARMAN_COMMAND_MAX GEARMAN_COMMAND_NOOP
GEARMAN_COMMAND_NO_JOB GEARMAN_COMMAND_OPTION_REQ GEARMAN_COMMAND_OPTION_RES
GEARMAN_COMMAND_PRE_SLEEP GEARMAN_COMMAND_RESET_ABILITIES
GEARMAN_COMMAND_SET_CLIENT_ID GEARMAN_COMMAND_STATUS_RES
GEARMAN_COMMAND_SUBMIT_JOB GEARMAN_COMMAND_SUBMIT_JOB_BG
GEARMAN_COMMAND_SUBMIT_JOB_EPOCH GEARMAN_COMMAND_SUBMIT_JOB_HIGH
GEARMAN_COMMAND_SUBMIT_JOB_HIGH_BG GEARMAN_COMMAND_SUBMIT_JOB_LOW
GEARMAN_COMMAND_SUBMIT_JOB_LOW_BG GEARMAN_COMMAND_SUBMIT_JOB_SCHED
GEARMAN_COMMAND_TEXT GEARMAN_COMMAND_UNUSED GEARMAN_COMMAND_WORK_COMPLETE
GEARMAN_COMMAND_WORK_DATA GEARMAN_COMMAND_WORK_EXCEPTION
GEARMAN_COMMAND_WORK_FAIL GEARMAN_COMMAND_WORK_STATUS
GEARMAN_COMMAND_WORK_WARNING GEARMAN_CON_CLOSE_AFTER_FLUSH
GEARMAN_CON_EXTERNAL_FD GEARMAN_CON_IGNORE_LOST_CONNECTION
GEARMAN_CON_PACKET_IN_USE GEARMAN_CON_READY GEARMAN_CON_RECV_STATE_READ_DATA
GEARMAN_CON_SEND_STATE_NONE GEARMAN_COULD_NOT_CONNECT GEARMAN_DATA_TOO_LARGE
GEARMAN_DEFAULT_SOCKET_RECV_SIZE GEARMAN_DEFAULT_SOCKET_SEND_SIZE
GEARMAN_DEFAULT_SOCKET_TIMEOUT GEARMAN_DEFAULT_TCP_HOST
GEARMAN_DEFAULT_TCP_PORT GEARMAN_DONT_TRACK_PACKETS
GEARMAN_ECHO_DATA_CORRUPTION GEARMAN_ERRNO GEARMAN_EVENT GEARMAN_FLUSH_DATA
GEARMAN_GETADDRINFO GEARMAN_IGNORE_PACKET GEARMAN_INVALID_COMMAND
GEARMAN_INVALID_FUNCTION_NAME GEARMAN_INVALID_MAGIC GEARMAN_INVALID_PACKET
GEARMAN_INVALID_WORKER_FUNCTION GEARMAN_IO_WAIT GEARMAN_JOB_EXISTS
GEARMAN_JOB_HANDLE_SIZE GEARMAN_JOB_PRIORITY_HIGH GEARMAN_JOB_PRIORITY_LOW
GEARMAN_JOB_PRIORITY_MAX GEARMAN_JOB_PRIORITY_NORMAL GEARMAN_JOB_QUEUE_FULL
GEARMAN_LOST_CONNECTION GEARMAN_MAX_COMMAND_ARGS GEARMAN_MAX_ERROR_SIZE
GEARMAN_MAX_RETURN GEARMAN_MEMORY_ALLOCATION_FAILURE GEARMAN_NEED_WORKLOAD_FN
GEARMAN_NON_BLOCKING GEARMAN_NOT_CONNECTED GEARMAN_NOT_FLUSHING
GEARMAN_NO_ACTIVE_FDS GEARMAN_NO_JOBS GEARMAN_NO_REGISTERED_FUNCTIONS
GEARMAN_NO_SERVERS GEARMAN_OPTION_SIZE GEARMAN_PACKET_HEADER_SIZE
GEARMAN_PAUSE GEARMAN_PIPE_EOF GEARMAN_PTHREAD GEARMAN_QUEUE_ERROR
GEARMAN_RECV_BUFFER_SIZE GEARMAN_RECV_IN_PROGRESS GEARMAN_SEND_BUFFER_SIZE
GEARMAN_SEND_BUFFER_TOO_SMALL GEARMAN_SEND_IN_PROGRESS GEARMAN_SERVER_ERROR
GEARMAN_SHUTDOWN GEARMAN_SHUTDOWN_GRACEFUL GEARMAN_SUCCESS
GEARMAN_TASK_STATE_COMPLETE GEARMAN_TASK_STATE_CREATED GEARMAN_TASK_STATE_DATA
GEARMAN_TASK_STATE_EXCEPTION GEARMAN_TASK_STATE_FAIL
GEARMAN_TASK_STATE_FINISHED GEARMAN_TASK_STATE_NEW GEARMAN_TASK_STATE_STATUS
GEARMAN_TASK_STATE_SUBMIT GEARMAN_TASK_STATE_WARNING GEARMAN_TASK_STATE_WORK
GEARMAN_TASK_STATE_WORKLOAD GEARMAN_TIMEOUT GEARMAN_TOO_MANY_ARGS
GEARMAN_UNEXPECTED_PACKET GEARMAN_UNIQUE_SIZE GEARMAN_UNKNOWN_OPTION
GEARMAN_UNKNOWN_STATE GEARMAN_VERBOSE_CRAZY GEARMAN_VERBOSE_DEBUG
GEARMAN_VERBOSE_ERROR GEARMAN_VERBOSE_FATAL GEARMAN_VERBOSE_INFO
GEARMAN_VERBOSE_MAX GEARMAN_WORKER_ALLOCATED GEARMAN_WORKER_CHANGE
GEARMAN_WORKER_GRAB_JOB_IN_USE GEARMAN_WORKER_GRAB_UNIQ
GEARMAN_WORKER_NON_BLOCKING GEARMAN_WORKER_PACKET_INIT
GEARMAN_WORKER_PRE_SLEEP_IN_USE GEARMAN_WORKER_STATE_CONNECT
GEARMAN_WORKER_STATE_FUNCTION_SEND GEARMAN_WORKER_STATE_GRAB_JOB_RECV
GEARMAN_WORKER_STATE_GRAB_JOB_SEND GEARMAN_WORKER_STATE_PRE_SLEEP
GEARMAN_WORKER_STATE_START GEARMAN_WORKER_TIMEOUT_RETURN
GEARMAN_WORKER_WAIT_TIMEOUT GEARMAN_WORKER_WORK_JOB_IN_USE GEARMAN_WORK_DATA
GEARMAN_WORK_ERROR GEARMAN_WORK_EXCEPTION GEARMAN_WORK_FAIL
GEARMAN_WORK_STATUS GEARMAN_WORK_WARNING GLOB_AVAILABLE_FLAGS GLOB_BRACE
GLOB_ERR GLOB_MARK GLOB_NOCHECK GLOB_NOESCAPE GLOB_NOSORT GLOB_ONLYDIR
GMP_BIG_ENDIAN GMP_LITTLE_ENDIAN GMP_LSW_FIRST GMP_MSW_FIRST GMP_NATIVE_ENDIAN
GMP_ROUND_MINUSINF GMP_ROUND_PLUSINF GMP_ROUND_ZERO GMP_VERSION
GNUPG_ERROR_EXCEPTION GNUPG_ERROR_SILENT GNUPG_ERROR_WARNING
GNUPG_GPGME_VERSION GNUPG_PK_DSA GNUPG_PK_ECC GNUPG_PK_ECDH GNUPG_PK_ECDSA
GNUPG_PK_EDDSA GNUPG_PK_ELG GNUPG_PK_ELG_E GNUPG_PK_RSA GNUPG_PK_RSA_E
GNUPG_PK_RSA_S GNUPG_PROTOCOL_CMS GNUPG_PROTOCOL_OpenPGP
GNUPG_SIGSUM_BAD_POLICY GNUPG_SIGSUM_CRL_MISSING GNUPG_SIGSUM_CRL_TOO_OLD
GNUPG_SIGSUM_GREEN GNUPG_SIGSUM_KEY_EXPIRED GNUPG_SIGSUM_KEY_MISSING
GNUPG_SIGSUM_KEY_REVOKED GNUPG_SIGSUM_RED GNUPG_SIGSUM_SIG_EXPIRED
GNUPG_SIGSUM_SYS_ERROR GNUPG_SIGSUM_VALID GNUPG_SIG_MODE_CLEAR
GNUPG_SIG_MODE_DETACH GNUPG_SIG_MODE_NORMAL GNUPG_VALIDITY_FULL
GNUPG_VALIDITY_MARGINAL GNUPG_VALIDITY_NEVER GNUPG_VALIDITY_ULTIMATE
GNUPG_VALIDITY_UNDEFINED GNUPG_VALIDITY_UNKNOWN GRAPHEME_EXTR_COUNT
GRAPHEME_EXTR_MAXBYTES GRAPHEME_EXTR_MAXCHARS GROUPING HASH_HMAC HTML_ENTITIES
HTML_SPECIALCHARS ICONV_IMPL ICONV_MIME_DECODE_CONTINUE_ON_ERROR
ICONV_MIME_DECODE_STRICT ICONV_VERSION IDNA_ALLOW_UNASSIGNED IDNA_CHECK_BIDI
IDNA_CHECK_CONTEXTJ IDNA_DEFAULT IDNA_ERROR_BIDI IDNA_ERROR_CONTEXTJ
IDNA_ERROR_DISALLOWED IDNA_ERROR_DOMAIN_NAME_TOO_LONG IDNA_ERROR_EMPTY_LABEL
IDNA_ERROR_HYPHEN_3_4 IDNA_ERROR_INVALID_ACE_LABEL IDNA_ERROR_LABEL_HAS_DOT
IDNA_ERROR_LABEL_TOO_LONG IDNA_ERROR_LEADING_COMBINING_MARK
IDNA_ERROR_LEADING_HYPHEN IDNA_ERROR_PUNYCODE IDNA_ERROR_TRAILING_HYPHEN
IDNA_NONTRANSITIONAL_TO_ASCII IDNA_NONTRANSITIONAL_TO_UNICODE
IDNA_USE_STD3_RULES ILL_BADSTK ILL_COPROC ILL_ILLADR ILL_ILLOPC ILL_ILLOPN
ILL_ILLTRP ILL_PRVOPC ILL_PRVREG IMAGETYPE_AVIF IMAGETYPE_BMP IMAGETYPE_COUNT
IMAGETYPE_GIF IMAGETYPE_ICO IMAGETYPE_IFF IMAGETYPE_JB2 IMAGETYPE_JP2
IMAGETYPE_JPC IMAGETYPE_JPEG IMAGETYPE_JPEG2000 IMAGETYPE_JPX IMAGETYPE_PNG
IMAGETYPE_PSD IMAGETYPE_SWC IMAGETYPE_SWF IMAGETYPE_TIFF_II IMAGETYPE_TIFF_MM
IMAGETYPE_UNKNOWN IMAGETYPE_WBMP IMAGETYPE_WEBP IMAGETYPE_XBM
IMAP_CLOSETIMEOUT IMAP_GC_ELT IMAP_GC_ENV IMAP_GC_TEXTS IMAP_OPENTIMEOUT
IMAP_READTIMEOUT IMAP_WRITETIMEOUT IMG_AFFINE_ROTATE IMG_AFFINE_SCALE
IMG_AFFINE_SHEAR_HORIZONTAL IMG_AFFINE_SHEAR_VERTICAL IMG_AFFINE_TRANSLATE
IMG_ARC_CHORD IMG_ARC_EDGED IMG_ARC_NOFILL IMG_ARC_PIE IMG_ARC_ROUNDED
IMG_AVIF IMG_BELL IMG_BESSEL IMG_BICUBIC IMG_BICUBIC_FIXED IMG_BILINEAR_FIXED
IMG_BLACKMAN IMG_BMP IMG_BOX IMG_BSPLINE IMG_CATMULLROM IMG_COLOR_BRUSHED
IMG_COLOR_STYLED IMG_COLOR_STYLEDBRUSHED IMG_COLOR_TILED IMG_COLOR_TRANSPARENT
IMG_CROP_BLACK IMG_CROP_DEFAULT IMG_CROP_SIDES IMG_CROP_THRESHOLD
IMG_CROP_TRANSPARENT IMG_CROP_WHITE IMG_EFFECT_ALPHABLEND IMG_EFFECT_MULTIPLY
IMG_EFFECT_NORMAL IMG_EFFECT_OVERLAY IMG_EFFECT_REPLACE IMG_FILTER_BRIGHTNESS
IMG_FILTER_COLORIZE IMG_FILTER_CONTRAST IMG_FILTER_EDGEDETECT
IMG_FILTER_EMBOSS IMG_FILTER_GAUSSIAN_BLUR IMG_FILTER_GRAYSCALE
IMG_FILTER_MEAN_REMOVAL IMG_FILTER_NEGATE IMG_FILTER_PIXELATE
IMG_FILTER_SCATTER IMG_FILTER_SELECTIVE_BLUR IMG_FILTER_SMOOTH IMG_FLIP_BOTH
IMG_FLIP_HORIZONTAL IMG_FLIP_VERTICAL IMG_GAUSSIAN IMG_GD2_COMPRESSED
IMG_GD2_RAW IMG_GENERALIZED_CUBIC IMG_GIF IMG_HAMMING IMG_HANNING IMG_HERMITE
IMG_JPEG IMG_JPG IMG_MITCHELL IMG_NEAREST_NEIGHBOUR IMG_PNG IMG_POWER
IMG_QUADRATIC IMG_SINC IMG_TGA IMG_TRIANGLE IMG_WBMP IMG_WEBP
IMG_WEBP_LOSSLESS IMG_WEIGHTED4 IMG_XPM INF INFO_ALL INFO_CONFIGURATION
INFO_CREDITS INFO_ENVIRONMENT INFO_GENERAL INFO_LICENSE INFO_MODULES
INFO_VARIABLES INI_ALL INI_PERDIR INI_SCANNER_NORMAL INI_SCANNER_RAW
INI_SCANNER_TYPED INI_SYSTEM INI_USER INPUT_COOKIE INPUT_ENV INPUT_GET
INPUT_POST INPUT_SERVER INTL_ICU_DATA_VERSION INTL_ICU_VERSION
INTL_IDNA_VARIANT_UTS46 INTL_MAX_LOCALE_LEN INT_CURR_SYMBOL INT_FRAC_DIGITS
IPPROTO_IP IPPROTO_IPV6 IPV6_HOPLIMIT IPV6_MULTICAST_HOPS IPV6_MULTICAST_IF
IPV6_MULTICAST_LOOP IPV6_PKTINFO IPV6_RECVHOPLIMIT IPV6_RECVPKTINFO
IPV6_RECVTCLASS IPV6_TCLASS IPV6_UNICAST_HOPS IPV6_V6ONLY
IP_BIND_ADDRESS_NO_PORT IP_MTU_DISCOVER IP_MULTICAST_IF IP_MULTICAST_LOOP
IP_MULTICAST_TTL IP_PMTUDISC_DO IP_PMTUDISC_DONT IP_PMTUDISC_INTERFACE
IP_PMTUDISC_OMIT IP_PMTUDISC_PROBE IP_PMTUDISC_WANT JSON_BIGINT_AS_STRING
JSON_ERROR_CTRL_CHAR JSON_ERROR_DEPTH JSON_ERROR_INF_OR_NAN
JSON_ERROR_INVALID_PROPERTY_NAME JSON_ERROR_NONE JSON_ERROR_NON_BACKED_ENUM
JSON_ERROR_RECURSION JSON_ERROR_STATE_MISMATCH JSON_ERROR_SYNTAX
JSON_ERROR_UNSUPPORTED_TYPE JSON_ERROR_UTF16 JSON_ERROR_UTF8 JSON_FORCE_OBJECT
JSON_HEX_AMP JSON_HEX_APOS JSON_HEX_QUOT JSON_HEX_TAG JSON_INVALID_UTF8_IGNORE
JSON_INVALID_UTF8_SUBSTITUTE JSON_NUMERIC_CHECK JSON_OBJECT_AS_ARRAY
JSON_PARTIAL_OUTPUT_ON_ERROR JSON_PRESERVE_ZERO_FRACTION JSON_PRETTY_PRINT
JSON_THROW_ON_ERROR JSON_UNESCAPED_LINE_TERMINATORS JSON_UNESCAPED_SLASHES
JSON_UNESCAPED_UNICODE LATT_HASCHILDREN LATT_HASNOCHILDREN LATT_MARKED
LATT_NOINFERIORS LATT_NOSELECT LATT_REFERRAL LATT_UNMARKED LC_ALL LC_COLLATE
LC_CTYPE LC_MESSAGES LC_MONETARY LC_NUMERIC LC_TIME LDAP_CONTROL_ASSERT
LDAP_CONTROL_AUTHZID_REQUEST LDAP_CONTROL_AUTHZID_RESPONSE
LDAP_CONTROL_DONTUSECOPY LDAP_CONTROL_MANAGEDSAIT LDAP_CONTROL_PAGEDRESULTS
LDAP_CONTROL_PASSWORDPOLICYREQUEST LDAP_CONTROL_PASSWORDPOLICYRESPONSE
LDAP_CONTROL_POST_READ LDAP_CONTROL_PRE_READ LDAP_CONTROL_PROXY_AUTHZ
LDAP_CONTROL_SORTREQUEST LDAP_CONTROL_SORTRESPONSE LDAP_CONTROL_SUBENTRIES
LDAP_CONTROL_SYNC LDAP_CONTROL_SYNC_DONE LDAP_CONTROL_SYNC_STATE
LDAP_CONTROL_VALUESRETURNFILTER LDAP_CONTROL_VLVREQUEST
LDAP_CONTROL_VLVRESPONSE LDAP_CONTROL_X_DOMAIN_SCOPE
LDAP_CONTROL_X_EXTENDED_DN LDAP_CONTROL_X_INCREMENTAL_VALUES
LDAP_CONTROL_X_PERMISSIVE_MODIFY LDAP_CONTROL_X_SEARCH_OPTIONS
LDAP_CONTROL_X_TREE_DELETE LDAP_DEREF_ALWAYS LDAP_DEREF_FINDING
LDAP_DEREF_NEVER LDAP_DEREF_SEARCHING LDAP_ESCAPE_DN LDAP_ESCAPE_FILTER
LDAP_EXOP_MODIFY_PASSWD LDAP_EXOP_REFRESH LDAP_EXOP_START_TLS LDAP_EXOP_TURN
LDAP_EXOP_WHO_AM_I LDAP_MODIFY_BATCH_ADD LDAP_MODIFY_BATCH_ATTRIB
LDAP_MODIFY_BATCH_MODTYPE LDAP_MODIFY_BATCH_REMOVE
LDAP_MODIFY_BATCH_REMOVE_ALL LDAP_MODIFY_BATCH_REPLACE
LDAP_MODIFY_BATCH_VALUES LDAP_OPT_CLIENT_CONTROLS LDAP_OPT_DEBUG_LEVEL
LDAP_OPT_DEREF LDAP_OPT_DIAGNOSTIC_MESSAGE LDAP_OPT_ERROR_NUMBER
LDAP_OPT_ERROR_STRING LDAP_OPT_HOST_NAME LDAP_OPT_MATCHED_DN
LDAP_OPT_NETWORK_TIMEOUT LDAP_OPT_PROTOCOL_VERSION LDAP_OPT_REFERRALS
LDAP_OPT_RESTART LDAP_OPT_SERVER_CONTROLS LDAP_OPT_SIZELIMIT
LDAP_OPT_TIMELIMIT LDAP_OPT_TIMEOUT LDAP_OPT_X_KEEPALIVE_IDLE
LDAP_OPT_X_KEEPALIVE_INTERVAL LDAP_OPT_X_KEEPALIVE_PROBES
LDAP_OPT_X_SASL_AUTHCID LDAP_OPT_X_SASL_AUTHZID LDAP_OPT_X_SASL_MECH
LDAP_OPT_X_SASL_NOCANON LDAP_OPT_X_SASL_REALM LDAP_OPT_X_SASL_USERNAME
LDAP_OPT_X_TLS_ALLOW LDAP_OPT_X_TLS_CACERTDIR LDAP_OPT_X_TLS_CACERTFILE
LDAP_OPT_X_TLS_CERTFILE LDAP_OPT_X_TLS_CIPHER_SUITE LDAP_OPT_X_TLS_CRLCHECK
LDAP_OPT_X_TLS_CRLFILE LDAP_OPT_X_TLS_CRL_ALL LDAP_OPT_X_TLS_CRL_NONE
LDAP_OPT_X_TLS_CRL_PEER LDAP_OPT_X_TLS_DEMAND LDAP_OPT_X_TLS_DHFILE
LDAP_OPT_X_TLS_HARD LDAP_OPT_X_TLS_KEYFILE LDAP_OPT_X_TLS_NEVER
LDAP_OPT_X_TLS_PACKAGE LDAP_OPT_X_TLS_PROTOCOL_MIN
LDAP_OPT_X_TLS_PROTOCOL_SSL2 LDAP_OPT_X_TLS_PROTOCOL_SSL3
LDAP_OPT_X_TLS_PROTOCOL_TLS1_0 LDAP_OPT_X_TLS_PROTOCOL_TLS1_1
LDAP_OPT_X_TLS_PROTOCOL_TLS1_2 LDAP_OPT_X_TLS_RANDOM_FILE
LDAP_OPT_X_TLS_REQUIRE_CERT LDAP_OPT_X_TLS_TRY LIBENCHANT_VERSION
LIBEXSLT_DOTTED_VERSION LIBEXSLT_VERSION LIBXML_BIGLINES LIBXML_COMPACT
LIBXML_DOTTED_VERSION LIBXML_DTDATTR LIBXML_DTDLOAD LIBXML_DTDVALID
LIBXML_ERR_ERROR LIBXML_ERR_FATAL LIBXML_ERR_NONE LIBXML_ERR_WARNING
LIBXML_HTML_NODEFDTD LIBXML_HTML_NOIMPLIED LIBXML_LOADED_VERSION
LIBXML_NOBLANKS LIBXML_NOCDATA LIBXML_NOEMPTYTAG LIBXML_NOENT LIBXML_NOERROR
LIBXML_NONET LIBXML_NOWARNING LIBXML_NOXMLDECL LIBXML_NSCLEAN LIBXML_PARSEHUGE
LIBXML_PEDANTIC LIBXML_SCHEMA_CREATE LIBXML_VERSION LIBXML_XINCLUDE
LIBXSLT_DOTTED_VERSION LIBXSLT_VERSION LOCK_EX LOCK_NB LOCK_SH LOCK_UN
LOG_ALERT LOG_AUTH LOG_AUTHPRIV LOG_CONS LOG_CRIT LOG_CRON LOG_DAEMON
LOG_DEBUG LOG_EMERG LOG_ERR LOG_INFO LOG_KERN LOG_LOCAL0 LOG_LOCAL1 LOG_LOCAL2
LOG_LOCAL3 LOG_LOCAL4 LOG_LOCAL5 LOG_LOCAL6 LOG_LOCAL7 LOG_LPR LOG_MAIL
LOG_NDELAY LOG_NEWS LOG_NOTICE LOG_NOWAIT LOG_ODELAY LOG_PERROR LOG_PID
LOG_SYSLOG LOG_USER LOG_UUCP LOG_WARNING MAILPARSE_EXTRACT_OUTPUT
MAILPARSE_EXTRACT_RETURN MAILPARSE_EXTRACT_STREAM MB_CASE_FOLD
MB_CASE_FOLD_SIMPLE MB_CASE_LOWER MB_CASE_LOWER_SIMPLE MB_CASE_TITLE
MB_CASE_TITLE_SIMPLE MB_CASE_UPPER MB_CASE_UPPER_SIMPLE MB_ONIGURUMA_VERSION
MCAST_BLOCK_SOURCE MCAST_JOIN_GROUP MCAST_JOIN_SOURCE_GROUP MCAST_LEAVE_GROUP
MCAST_LEAVE_SOURCE_GROUP MCAST_UNBLOCK_SOURCE MCRYPT_3DES MCRYPT_ARCFOUR
MCRYPT_ARCFOUR_IV MCRYPT_BLOWFISH MCRYPT_BLOWFISH_COMPAT MCRYPT_CAST_128
MCRYPT_CAST_256 MCRYPT_CRYPT MCRYPT_DECRYPT MCRYPT_DES MCRYPT_DEV_RANDOM
MCRYPT_DEV_URANDOM MCRYPT_ENCRYPT MCRYPT_ENIGNA MCRYPT_GOST MCRYPT_IDEA
MCRYPT_LOKI97 MCRYPT_MARS MCRYPT_MODE_CBC MCRYPT_MODE_CFB MCRYPT_MODE_ECB
MCRYPT_MODE_NOFB MCRYPT_MODE_OFB MCRYPT_MODE_STREAM MCRYPT_PANAMA MCRYPT_RAND
MCRYPT_RC2 MCRYPT_RC6 MCRYPT_RIJNDAEL_128 MCRYPT_RIJNDAEL_192
MCRYPT_RIJNDAEL_256 MCRYPT_SAFER128 MCRYPT_SAFER64 MCRYPT_SAFERPLUS
MCRYPT_SERPENT MCRYPT_SKIPJACK MCRYPT_THREEWAY MCRYPT_TRIPLEDES MCRYPT_TWOFISH
MCRYPT_WAKE MCRYPT_XTEA MEMCACHE_COMPRESSED MEMCACHE_HAVE_SESSION
MEMCACHE_USER1 MEMCACHE_USER2 MEMCACHE_USER3 MEMCACHE_USER4
MESSAGEPACK_OPT_PHPONLY MHASH_ADLER32 MHASH_CRC32 MHASH_CRC32B MHASH_CRC32C
MHASH_FNV132 MHASH_FNV164 MHASH_FNV1A32 MHASH_FNV1A64 MHASH_GOST
MHASH_HAVAL128 MHASH_HAVAL160 MHASH_HAVAL192 MHASH_HAVAL224 MHASH_HAVAL256
MHASH_JOAAT MHASH_MD2 MHASH_MD4 MHASH_MD5 MHASH_MURMUR3A MHASH_MURMUR3C
MHASH_MURMUR3F MHASH_RIPEMD128 MHASH_RIPEMD160 MHASH_RIPEMD256 MHASH_RIPEMD320
MHASH_SHA1 MHASH_SHA224 MHASH_SHA256 MHASH_SHA384 MHASH_SHA512 MHASH_SNEFRU256
MHASH_TIGER MHASH_TIGER128 MHASH_TIGER160 MHASH_WHIRLPOOL MHASH_XXH128
MHASH_XXH3 MHASH_XXH32 MHASH_XXH64 MONGODB_STABILITY MONGODB_VERSION MON_1
MON_10 MON_11 MON_12 MON_2 MON_3 MON_4 MON_5 MON_6 MON_7 MON_8 MON_9
MON_DECIMAL_POINT MON_GROUPING MON_THOUSANDS_SEP MSG_CMSG_CLOEXEC MSG_CONFIRM
MSG_CTRUNC MSG_DONTROUTE MSG_DONTWAIT MSG_EAGAIN MSG_ENOMSG MSG_EOF MSG_EOR
MSG_ERRQUEUE MSG_EXCEPT MSG_IPC_NOWAIT MSG_MORE MSG_NOERROR MSG_NOSIGNAL
MSG_OOB MSG_PEEK MSG_TRUNC MSG_WAITALL MSG_WAITFORONE MSG_ZEROCOPY
MT_RAND_MT19937 MT_RAND_PHP MYSQLI_ASSOC MYSQLI_ASYNC
MYSQLI_AUTO_INCREMENT_FLAG MYSQLI_BINARY_FLAG MYSQLI_BLOB_FLAG MYSQLI_BOTH
MYSQLI_CLIENT_CAN_HANDLE_EXPIRED_PASSWORDS MYSQLI_CLIENT_COMPRESS
MYSQLI_CLIENT_FOUND_ROWS MYSQLI_CLIENT_IGNORE_SPACE MYSQLI_CLIENT_INTERACTIVE
MYSQLI_CLIENT_NO_SCHEMA MYSQLI_CLIENT_SSL
MYSQLI_CLIENT_SSL_DONT_VERIFY_SERVER_CERT MYSQLI_CLIENT_SSL_VERIFY_SERVER_CERT
MYSQLI_CURSOR_TYPE_FOR_UPDATE MYSQLI_CURSOR_TYPE_NO_CURSOR
MYSQLI_CURSOR_TYPE_READ_ONLY MYSQLI_CURSOR_TYPE_SCROLLABLE
MYSQLI_DATA_TRUNCATED MYSQLI_DEBUG_TRACE_ENABLED MYSQLI_ENUM_FLAG
MYSQLI_GROUP_FLAG MYSQLI_INIT_COMMAND MYSQLI_IS_MARIADB
MYSQLI_MULTIPLE_KEY_FLAG MYSQLI_NOT_NULL_FLAG MYSQLI_NO_DATA
MYSQLI_NO_DEFAULT_VALUE_FLAG MYSQLI_NUM MYSQLI_NUM_FLAG
MYSQLI_ON_UPDATE_NOW_FLAG MYSQLI_OPT_CAN_HANDLE_EXPIRED_PASSWORDS
MYSQLI_OPT_CONNECT_TIMEOUT MYSQLI_OPT_INT_AND_FLOAT_NATIVE
MYSQLI_OPT_LOAD_DATA_LOCAL_DIR MYSQLI_OPT_LOCAL_INFILE
MYSQLI_OPT_NET_CMD_BUFFER_SIZE MYSQLI_OPT_NET_READ_BUFFER_SIZE
MYSQLI_OPT_READ_TIMEOUT MYSQLI_OPT_SSL_VERIFY_SERVER_CERT MYSQLI_PART_KEY_FLAG
MYSQLI_PRI_KEY_FLAG MYSQLI_READ_DEFAULT_FILE MYSQLI_READ_DEFAULT_GROUP
MYSQLI_REFRESH_BACKUP_LOG MYSQLI_REFRESH_GRANT MYSQLI_REFRESH_HOSTS
MYSQLI_REFRESH_LOG MYSQLI_REFRESH_MASTER MYSQLI_REFRESH_REPLICA
MYSQLI_REFRESH_SLAVE MYSQLI_REFRESH_STATUS MYSQLI_REFRESH_TABLES
MYSQLI_REFRESH_THREADS MYSQLI_REPORT_ALL MYSQLI_REPORT_ERROR
MYSQLI_REPORT_INDEX MYSQLI_REPORT_OFF MYSQLI_REPORT_STRICT
MYSQLI_SERVER_PS_OUT_PARAMS MYSQLI_SERVER_PUBLIC_KEY
MYSQLI_SERVER_QUERY_NO_GOOD_INDEX_USED MYSQLI_SERVER_QUERY_NO_INDEX_USED
MYSQLI_SERVER_QUERY_WAS_SLOW MYSQLI_SET_CHARSET_DIR MYSQLI_SET_CHARSET_NAME
MYSQLI_SET_FLAG MYSQLI_STMT_ATTR_CURSOR_TYPE MYSQLI_STMT_ATTR_PREFETCH_ROWS
MYSQLI_STMT_ATTR_UPDATE_MAX_LENGTH MYSQLI_STORE_RESULT
MYSQLI_STORE_RESULT_COPY_DATA MYSQLI_TIMESTAMP_FLAG MYSQLI_TRANS_COR_AND_CHAIN
MYSQLI_TRANS_COR_AND_NO_CHAIN MYSQLI_TRANS_COR_NO_RELEASE
MYSQLI_TRANS_COR_RELEASE MYSQLI_TRANS_START_READ_ONLY
MYSQLI_TRANS_START_READ_WRITE MYSQLI_TRANS_START_WITH_CONSISTENT_SNAPSHOT
MYSQLI_TYPE_BIT MYSQLI_TYPE_BLOB MYSQLI_TYPE_CHAR MYSQLI_TYPE_DATE
MYSQLI_TYPE_DATETIME MYSQLI_TYPE_DECIMAL MYSQLI_TYPE_DOUBLE MYSQLI_TYPE_ENUM
MYSQLI_TYPE_FLOAT MYSQLI_TYPE_GEOMETRY MYSQLI_TYPE_INT24 MYSQLI_TYPE_INTERVAL
MYSQLI_TYPE_JSON MYSQLI_TYPE_LONG MYSQLI_TYPE_LONGLONG MYSQLI_TYPE_LONG_BLOB
MYSQLI_TYPE_MEDIUM_BLOB MYSQLI_TYPE_NEWDATE MYSQLI_TYPE_NEWDECIMAL
MYSQLI_TYPE_NULL MYSQLI_TYPE_SET MYSQLI_TYPE_SHORT MYSQLI_TYPE_STRING
MYSQLI_TYPE_TIME MYSQLI_TYPE_TIMESTAMP MYSQLI_TYPE_TINY MYSQLI_TYPE_TINY_BLOB
MYSQLI_TYPE_VAR_STRING MYSQLI_TYPE_YEAR MYSQLI_UNIQUE_KEY_FLAG
MYSQLI_UNSIGNED_FLAG MYSQLI_USE_RESULT MYSQLI_ZEROFILL_FLAG M_1_PI M_2_PI
M_2_SQRTPI M_E M_EULER M_LN10 M_LN2 M_LNPI M_LOG10E M_LOG2E M_PI M_PI_2 M_PI_4
M_SQRT1_2 M_SQRT2 M_SQRT3 M_SQRTPI NAN NEGATIVE_SIGN NIL NOEXPR NOSTR
N_CS_PRECEDES N_SEP_BY_SPACE N_SIGN_POSN OAUTH_AUTH_TYPE_AUTHORIZATION
OAUTH_AUTH_TYPE_FORM OAUTH_AUTH_TYPE_NONE OAUTH_AUTH_TYPE_URI OAUTH_BAD_NONCE
OAUTH_BAD_TIMESTAMP OAUTH_CONSUMER_KEY_REFUSED OAUTH_CONSUMER_KEY_UNKNOWN
OAUTH_HTTP_METHOD_DELETE OAUTH_HTTP_METHOD_GET OAUTH_HTTP_METHOD_HEAD
OAUTH_HTTP_METHOD_POST OAUTH_HTTP_METHOD_PUT OAUTH_INVALID_SIGNATURE OAUTH_OK
OAUTH_PARAMETER_ABSENT OAUTH_REQENGINE_STREAMS OAUTH_SIGNATURE_METHOD_REJECTED
OAUTH_SIG_METHOD_HMACSHA1 OAUTH_SIG_METHOD_HMACSHA256
OAUTH_SIG_METHOD_PLAINTEXT OAUTH_SIG_METHOD_RSASHA1 OAUTH_SSLCHECK_BOTH
OAUTH_SSLCHECK_HOST OAUTH_SSLCHECK_NONE OAUTH_SSLCHECK_PEER
OAUTH_TOKEN_EXPIRED OAUTH_TOKEN_REJECTED OAUTH_TOKEN_REVOKED OAUTH_TOKEN_USED
OAUTH_VERIFIER_INVALID ODBC_BINMODE_CONVERT ODBC_BINMODE_PASSTHRU
ODBC_BINMODE_RETURN ODBC_TYPE OPENSSL_ALGO_MD4 OPENSSL_ALGO_MD5
OPENSSL_ALGO_RMD160 OPENSSL_ALGO_SHA1 OPENSSL_ALGO_SHA224 OPENSSL_ALGO_SHA256
OPENSSL_ALGO_SHA384 OPENSSL_ALGO_SHA512 OPENSSL_CIPHER_3DES
OPENSSL_CIPHER_AES_128_CBC OPENSSL_CIPHER_AES_192_CBC
OPENSSL_CIPHER_AES_256_CBC OPENSSL_CIPHER_DES OPENSSL_CIPHER_RC2_128
OPENSSL_CIPHER_RC2_40 OPENSSL_CIPHER_RC2_64 OPENSSL_CMS_BINARY
OPENSSL_CMS_DETACHED OPENSSL_CMS_NOATTR OPENSSL_CMS_NOCERTS
OPENSSL_CMS_NOINTERN OPENSSL_CMS_NOSIGS OPENSSL_CMS_NOVERIFY
OPENSSL_CMS_OLDMIMETYPE OPENSSL_CMS_TEXT OPENSSL_DEFAULT_STREAM_CIPHERS
OPENSSL_DONT_ZERO_PAD_KEY OPENSSL_ENCODING_DER OPENSSL_ENCODING_PEM
OPENSSL_ENCODING_SMIME OPENSSL_KEYTYPE_DH OPENSSL_KEYTYPE_DSA
OPENSSL_KEYTYPE_EC OPENSSL_KEYTYPE_RSA OPENSSL_NO_PADDING
OPENSSL_PKCS1_OAEP_PADDING OPENSSL_PKCS1_PADDING OPENSSL_RAW_DATA
OPENSSL_TLSEXT_SERVER_NAME OPENSSL_VERSION_NUMBER OPENSSL_VERSION_TEXT
OPENSSL_ZERO_PADDING OP_ANONYMOUS OP_DEBUG OP_EXPUNGE OP_HALFOPEN OP_PROTOTYPE
OP_READONLY OP_SECURE OP_SHORTCACHE OP_SILENT PASSWORD_ARGON2I
PASSWORD_ARGON2ID PASSWORD_ARGON2_DEFAULT_MEMORY_COST
PASSWORD_ARGON2_DEFAULT_THREADS PASSWORD_ARGON2_DEFAULT_TIME_COST
PASSWORD_ARGON2_PROVIDER PASSWORD_BCRYPT PASSWORD_BCRYPT_DEFAULT_COST
PASSWORD_DEFAULT PATHINFO_ALL PATHINFO_BASENAME PATHINFO_DIRNAME
PATHINFO_EXTENSION PATHINFO_FILENAME PATH_SEPARATOR PCNTL_E2BIG PCNTL_EACCES
PCNTL_EAGAIN PCNTL_ECHILD PCNTL_EFAULT PCNTL_EINTR PCNTL_EINVAL PCNTL_EIO
PCNTL_EISDIR PCNTL_ELIBBAD PCNTL_ELOOP PCNTL_EMFILE PCNTL_ENAMETOOLONG
PCNTL_ENFILE PCNTL_ENOENT PCNTL_ENOEXEC PCNTL_ENOMEM PCNTL_ENOSPC
PCNTL_ENOTDIR PCNTL_EPERM PCNTL_ESRCH PCNTL_ETXTBSY PCNTL_EUSERS
PCRE_JIT_SUPPORT PCRE_VERSION PCRE_VERSION_MAJOR PCRE_VERSION_MINOR
PDO_ODBC_TYPE PEAR_EXTENSION_DIR PEAR_INSTALL_DIR PGSQL_ASSOC
PGSQL_BAD_RESPONSE PGSQL_BOTH PGSQL_COMMAND_OK PGSQL_CONNECTION_AUTH_OK
PGSQL_CONNECTION_AWAITING_RESPONSE PGSQL_CONNECTION_BAD PGSQL_CONNECTION_MADE
PGSQL_CONNECTION_OK PGSQL_CONNECTION_SETENV PGSQL_CONNECTION_STARTED
PGSQL_CONNECT_ASYNC PGSQL_CONNECT_FORCE_NEW PGSQL_CONV_FORCE_NULL
PGSQL_CONV_IGNORE_DEFAULT PGSQL_CONV_IGNORE_NOT_NULL PGSQL_COPY_IN
PGSQL_COPY_OUT PGSQL_DIAG_COLUMN_NAME PGSQL_DIAG_CONSTRAINT_NAME
PGSQL_DIAG_CONTEXT PGSQL_DIAG_DATATYPE_NAME PGSQL_DIAG_INTERNAL_POSITION
PGSQL_DIAG_INTERNAL_QUERY PGSQL_DIAG_MESSAGE_DETAIL PGSQL_DIAG_MESSAGE_HINT
PGSQL_DIAG_MESSAGE_PRIMARY PGSQL_DIAG_SCHEMA_NAME PGSQL_DIAG_SEVERITY
PGSQL_DIAG_SEVERITY_NONLOCALIZED PGSQL_DIAG_SOURCE_FILE
PGSQL_DIAG_SOURCE_FUNCTION PGSQL_DIAG_SOURCE_LINE PGSQL_DIAG_SQLSTATE
PGSQL_DIAG_STATEMENT_POSITION PGSQL_DIAG_TABLE_NAME PGSQL_DML_ASYNC
PGSQL_DML_ESCAPE PGSQL_DML_EXEC PGSQL_DML_NO_CONV PGSQL_DML_STRING
PGSQL_EMPTY_QUERY PGSQL_ERRORS_DEFAULT PGSQL_ERRORS_SQLSTATE
PGSQL_ERRORS_TERSE PGSQL_ERRORS_VERBOSE PGSQL_FATAL_ERROR PGSQL_LIBPQ_VERSION
PGSQL_LIBPQ_VERSION_STR PGSQL_NONFATAL_ERROR PGSQL_NOTICE_ALL
PGSQL_NOTICE_CLEAR PGSQL_NOTICE_LAST PGSQL_NUM PGSQL_POLLING_ACTIVE
PGSQL_POLLING_FAILED PGSQL_POLLING_OK PGSQL_POLLING_READING
PGSQL_POLLING_WRITING PGSQL_SEEK_CUR PGSQL_SEEK_END PGSQL_SEEK_SET
PGSQL_SHOW_CONTEXT_ALWAYS PGSQL_SHOW_CONTEXT_ERRORS PGSQL_SHOW_CONTEXT_NEVER
PGSQL_STATUS_LONG PGSQL_STATUS_STRING PGSQL_TRACE_REGRESS_MODE
PGSQL_TRANSACTION_ACTIVE PGSQL_TRANSACTION_IDLE PGSQL_TRANSACTION_INERROR
PGSQL_TRANSACTION_INTRANS PGSQL_TRANSACTION_UNKNOWN PGSQL_TUPLES_OK
PHP_AMQP_MAX_CHANNELS PHP_BINARY PHP_BINARY_READ PHP_BINDIR
PHP_CLI_PROCESS_TITLE PHP_CONFIG_FILE_PATH PHP_CONFIG_FILE_SCAN_DIR
PHP_DATADIR PHP_DEBUG PHP_EOL PHP_EXTENSION_DIR PHP_EXTRA_VERSION
PHP_FD_SETSIZE PHP_FLOAT_DIG PHP_FLOAT_EPSILON PHP_FLOAT_MAX PHP_FLOAT_MIN
PHP_INT_MAX PHP_INT_MIN PHP_INT_SIZE PHP_LIBDIR PHP_LOCALSTATEDIR
PHP_MAJOR_VERSION PHP_MANDIR PHP_MAXPATHLEN PHP_MINOR_VERSION PHP_NORMAL_READ
PHP_OS PHP_OS_FAMILY PHP_OUTPUT_HANDLER_CLEAN PHP_OUTPUT_HANDLER_CLEANABLE
PHP_OUTPUT_HANDLER_CONT PHP_OUTPUT_HANDLER_DISABLED PHP_OUTPUT_HANDLER_END
PHP_OUTPUT_HANDLER_FINAL PHP_OUTPUT_HANDLER_FLUSH PHP_OUTPUT_HANDLER_FLUSHABLE
PHP_OUTPUT_HANDLER_REMOVABLE PHP_OUTPUT_HANDLER_START
PHP_OUTPUT_HANDLER_STARTED PHP_OUTPUT_HANDLER_STDFLAGS
PHP_OUTPUT_HANDLER_WRITE PHP_PREFIX PHP_QUERY_RFC1738 PHP_QUERY_RFC3986
PHP_RELEASE_VERSION PHP_ROUND_HALF_DOWN PHP_ROUND_HALF_EVEN PHP_ROUND_HALF_ODD
PHP_ROUND_HALF_UP PHP_SAPI PHP_SESSION_ACTIVE PHP_SESSION_DISABLED
PHP_SESSION_NONE PHP_SHLIB_SUFFIX PHP_SYSCONFDIR PHP_URL_FRAGMENT PHP_URL_HOST
PHP_URL_PASS PHP_URL_PATH PHP_URL_PORT PHP_URL_QUERY PHP_URL_SCHEME
PHP_URL_USER PHP_VERSION PHP_VERSION_ID PHP_ZTS PINBA_AUTO_FLUSH
PINBA_FLUSH_ONLY_STOPPED_TIMERS PINBA_FLUSH_RESET_DATA
PINBA_ONLY_RUNNING_TIMERS PINBA_ONLY_STOPPED_TIMERS PKCS7_BINARY
PKCS7_DETACHED PKCS7_NOATTR PKCS7_NOCERTS PKCS7_NOCHAIN PKCS7_NOINTERN
PKCS7_NOOLDMIMETYPE PKCS7_NOSIGS PKCS7_NOVERIFY PKCS7_TEXT PM_STR
PNG_ALL_FILTERS PNG_FILTER_AVG PNG_FILTER_NONE PNG_FILTER_PAETH PNG_FILTER_SUB
PNG_FILTER_UP PNG_NO_FILTER POLL_ERR POLL_HUP POLL_IN POLL_MSG POLL_OUT
POLL_PRI POSITIVE_SIGN POSIX_F_OK POSIX_PC_ALLOC_SIZE_MIN
POSIX_PC_CHOWN_RESTRICTED POSIX_PC_LINK_MAX POSIX_PC_MAX_CANON
POSIX_PC_MAX_INPUT POSIX_PC_NAME_MAX POSIX_PC_NO_TRUNC POSIX_PC_PATH_MAX
POSIX_PC_PIPE_BUF POSIX_PC_SYMLINK_MAX POSIX_RLIMIT_AS POSIX_RLIMIT_CORE
POSIX_RLIMIT_CPU POSIX_RLIMIT_DATA POSIX_RLIMIT_FSIZE POSIX_RLIMIT_INFINITY
POSIX_RLIMIT_LOCKS POSIX_RLIMIT_MEMLOCK POSIX_RLIMIT_MSGQUEUE
POSIX_RLIMIT_NICE POSIX_RLIMIT_NOFILE POSIX_RLIMIT_NPROC POSIX_RLIMIT_RSS
POSIX_RLIMIT_RTPRIO POSIX_RLIMIT_RTTIME POSIX_RLIMIT_SIGPENDING
POSIX_RLIMIT_STACK POSIX_R_OK POSIX_SC_ARG_MAX POSIX_SC_NPROCESSORS_CONF
POSIX_SC_NPROCESSORS_ONLN POSIX_SC_PAGESIZE POSIX_S_IFBLK POSIX_S_IFCHR
POSIX_S_IFIFO POSIX_S_IFREG POSIX_S_IFSOCK POSIX_W_OK POSIX_X_OK
PREG_BACKTRACK_LIMIT_ERROR PREG_BAD_UTF8_ERROR PREG_BAD_UTF8_OFFSET_ERROR
PREG_GREP_INVERT PREG_INTERNAL_ERROR PREG_JIT_STACKLIMIT_ERROR PREG_NO_ERROR
PREG_OFFSET_CAPTURE PREG_PATTERN_ORDER PREG_RECURSION_LIMIT_ERROR
PREG_SET_ORDER PREG_SPLIT_DELIM_CAPTURE PREG_SPLIT_NO_EMPTY
PREG_SPLIT_OFFSET_CAPTURE PREG_UNMATCHED_AS_NULL PRIO_PGRP PRIO_PROCESS
PRIO_USER PSFS_ERR_FATAL PSFS_FEED_ME PSFS_FLAG_FLUSH_CLOSE
PSFS_FLAG_FLUSH_INC PSFS_FLAG_NORMAL PSFS_PASS_ON PSPELL_BAD_SPELLERS
PSPELL_FAST PSPELL_NORMAL PSPELL_RUN_TOGETHER PS_LINECAP_BUTT PS_LINECAP_ROUND
PS_LINECAP_SQUARED PS_LINEJOIN_BEVEL PS_LINEJOIN_MITER PS_LINEJOIN_ROUND
P_CS_PRECEDES P_SEP_BY_SPACE P_SIGN_POSN RADIXCHAR READLINE_LIB SASS_FLAVOR
SA_ALL SA_MESSAGES SA_RECENT SA_UIDNEXT SA_UIDVALIDITY SA_UNSEEN
SCANDIR_SORT_ASCENDING SCANDIR_SORT_DESCENDING SCANDIR_SORT_NONE
SCM_CREDENTIALS SCM_RIGHTS SEEK_CUR SEEK_END SEEK_SET SEGV_ACCERR SEGV_MAPERR
SE_FREE SE_NOPREFETCH SE_UID SIGABRT SIGALRM SIGBABY SIGBUS SIGCHLD SIGCLD
SIGCONT SIGFPE SIGHUP SIGILL SIGINT SIGIO SIGIOT SIGKILL SIGPIPE SIGPOLL
SIGPROF SIGPWR SIGQUIT SIGRTMAX SIGRTMIN SIGSEGV SIGSTKFLT SIGSTOP SIGSYS
SIGTERM SIGTRAP SIGTSTP SIGTTIN SIGTTOU SIGURG SIGUSR1 SIGUSR2 SIGVTALRM
SIGWINCH SIGXCPU SIGXFSZ SIG_BLOCK SIG_DFL SIG_ERR SIG_IGN SIG_SETMASK
SIG_UNBLOCK SI_ASYNCIO SI_KERNEL SI_MESGQ SI_QUEUE SI_SIGIO SI_TIMER SI_TKILL
SI_USER SKF_AD_ALU_XOR_X SKF_AD_CPU SKF_AD_HATYPE SKF_AD_IFINDEX SKF_AD_MARK
SKF_AD_MAX SKF_AD_NLATTR SKF_AD_NLATTR_NEST SKF_AD_OFF SKF_AD_PAY_OFFSET
SKF_AD_PKTTYPE SKF_AD_PROTOCOL SKF_AD_QUEUE SKF_AD_RANDOM SKF_AD_RXHASH
SKF_AD_VLAN_TAG SKF_AD_VLAN_TAG_PRESENT SKF_AD_VLAN_TPID
SMBCLIENT_ENCRYPTLEVEL_NONE SMBCLIENT_ENCRYPTLEVEL_REQUEST
SMBCLIENT_ENCRYPTLEVEL_REQUIRE SMBCLIENT_OPT_AUTO_ANONYMOUS_LOGIN
SMBCLIENT_OPT_BROWSE_MAX_LMB_COUNT SMBCLIENT_OPT_CASE_SENSITIVE
SMBCLIENT_OPT_ENCRYPT_LEVEL SMBCLIENT_OPT_FALLBACK_AFTER_KERBEROS
SMBCLIENT_OPT_NETBIOS_NAME SMBCLIENT_OPT_OPEN_SHAREMODE SMBCLIENT_OPT_PORT
SMBCLIENT_OPT_TIMEOUT SMBCLIENT_OPT_URLENCODE_READDIR_ENTRIES
SMBCLIENT_OPT_USER SMBCLIENT_OPT_USE_CCACHE SMBCLIENT_OPT_USE_KERBEROS
SMBCLIENT_OPT_USE_NT_HASH SMBCLIENT_OPT_WORKGROUP SMBCLIENT_SHAREMODE_DENY_ALL
SMBCLIENT_SHAREMODE_DENY_DOS SMBCLIENT_SHAREMODE_DENY_FCB
SMBCLIENT_SHAREMODE_DENY_NONE SMBCLIENT_SHAREMODE_DENY_READ
SMBCLIENT_SHAREMODE_DENY_WRITE SMBCLIENT_VFS_CASE_INSENSITIVE
SMBCLIENT_VFS_DFS SMBCLIENT_VFS_NO_UNIXCIFS SMBCLIENT_VFS_RDONLY
SMBCLIENT_XATTR_CREATE SMBCLIENT_XATTR_REPLACE SNMP_BIT_STR SNMP_COUNTER
SNMP_COUNTER64 SNMP_INTEGER SNMP_IPADDRESS SNMP_NULL SNMP_OBJECT_ID
SNMP_OCTET_STR SNMP_OID_OUTPUT_FULL SNMP_OID_OUTPUT_MODULE
SNMP_OID_OUTPUT_NONE SNMP_OID_OUTPUT_NUMERIC SNMP_OID_OUTPUT_SUFFIX
SNMP_OID_OUTPUT_UCD SNMP_OPAQUE SNMP_TIMETICKS SNMP_UINTEGER SNMP_UNSIGNED
SNMP_VALUE_LIBRARY SNMP_VALUE_OBJECT SNMP_VALUE_PLAIN SOAP_1_1 SOAP_1_2
SOAP_ACTOR_NEXT SOAP_ACTOR_NONE SOAP_ACTOR_UNLIMATERECEIVER
SOAP_AUTHENTICATION_BASIC SOAP_AUTHENTICATION_DIGEST SOAP_COMPRESSION_ACCEPT
SOAP_COMPRESSION_DEFLATE SOAP_COMPRESSION_GZIP SOAP_DOCUMENT SOAP_ENCODED
SOAP_ENC_ARRAY SOAP_ENC_OBJECT SOAP_FUNCTIONS_ALL SOAP_LITERAL
SOAP_PERSISTENCE_REQUEST SOAP_PERSISTENCE_SESSION SOAP_RPC
SOAP_SINGLE_ELEMENT_ARRAYS SOAP_SSL_METHOD_SSLv2 SOAP_SSL_METHOD_SSLv23
SOAP_SSL_METHOD_SSLv3 SOAP_SSL_METHOD_TLS SOAP_USE_XSI_ARRAY_TYPE
SOAP_WAIT_ONE_WAY_CALLS SOCKET_E2BIG SOCKET_EACCES SOCKET_EADDRINUSE
SOCKET_EADDRNOTAVAIL SOCKET_EADV SOCKET_EAFNOSUPPORT SOCKET_EAGAIN
SOCKET_EALREADY SOCKET_EBADE SOCKET_EBADF SOCKET_EBADFD SOCKET_EBADMSG
SOCKET_EBADR SOCKET_EBADRQC SOCKET_EBADSLT SOCKET_EBUSY SOCKET_ECHRNG
SOCKET_ECOMM SOCKET_ECONNABORTED SOCKET_ECONNREFUSED SOCKET_ECONNRESET
SOCKET_EDESTADDRREQ SOCKET_EDQUOT SOCKET_EEXIST SOCKET_EFAULT SOCKET_EHOSTDOWN
SOCKET_EHOSTUNREACH SOCKET_EIDRM SOCKET_EINPROGRESS SOCKET_EINTR SOCKET_EINVAL
SOCKET_EIO SOCKET_EISCONN SOCKET_EISDIR SOCKET_EISNAM SOCKET_EL2HLT
SOCKET_EL2NSYNC SOCKET_EL3HLT SOCKET_EL3RST SOCKET_ELNRNG SOCKET_ELOOP
SOCKET_EMEDIUMTYPE SOCKET_EMFILE SOCKET_EMLINK SOCKET_EMSGSIZE
SOCKET_EMULTIHOP SOCKET_ENAMETOOLONG SOCKET_ENETDOWN SOCKET_ENETRESET
SOCKET_ENETUNREACH SOCKET_ENFILE SOCKET_ENOANO SOCKET_ENOBUFS SOCKET_ENOCSI
SOCKET_ENODATA SOCKET_ENODEV SOCKET_ENOENT SOCKET_ENOLCK SOCKET_ENOLINK
SOCKET_ENOMEDIUM SOCKET_ENOMEM SOCKET_ENOMSG SOCKET_ENONET SOCKET_ENOPROTOOPT
SOCKET_ENOSPC SOCKET_ENOSR SOCKET_ENOSTR SOCKET_ENOSYS SOCKET_ENOTBLK
SOCKET_ENOTCONN SOCKET_ENOTDIR SOCKET_ENOTEMPTY SOCKET_ENOTSOCK SOCKET_ENOTTY
SOCKET_ENOTUNIQ SOCKET_ENXIO SOCKET_EOPNOTSUPP SOCKET_EPERM
SOCKET_EPFNOSUPPORT SOCKET_EPIPE SOCKET_EPROTO SOCKET_EPROTONOSUPPORT
SOCKET_EPROTOTYPE SOCKET_EREMCHG SOCKET_EREMOTE SOCKET_EREMOTEIO
SOCKET_ERESTART SOCKET_EROFS SOCKET_ESHUTDOWN SOCKET_ESOCKTNOSUPPORT
SOCKET_ESPIPE SOCKET_ESRMNT SOCKET_ESTRPIPE SOCKET_ETIME SOCKET_ETIMEDOUT
SOCKET_ETOOMANYREFS SOCKET_EUNATCH SOCKET_EUSERS SOCKET_EWOULDBLOCK
SOCKET_EXDEV SOCKET_EXFULL SOCK_DGRAM SOCK_RAW SOCK_RDM SOCK_SEQPACKET
SOCK_STREAM SODIUM_BASE64_VARIANT_ORIGINAL
SODIUM_BASE64_VARIANT_ORIGINAL_NO_PADDING SODIUM_BASE64_VARIANT_URLSAFE
SODIUM_BASE64_VARIANT_URLSAFE_NO_PADDING SODIUM_CRYPTO_AEAD_AES256GCM_ABYTES
SODIUM_CRYPTO_AEAD_AES256GCM_KEYBYTES SODIUM_CRYPTO_AEAD_AES256GCM_NPUBBYTES
SODIUM_CRYPTO_AEAD_AES256GCM_NSECBYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_ABYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_IETF_ABYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_IETF_KEYBYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_IETF_NPUBBYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_IETF_NSECBYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_KEYBYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_NPUBBYTES
SODIUM_CRYPTO_AEAD_CHACHA20POLY1305_NSECBYTES
SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_ABYTES
SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_KEYBYTES
SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_NPUBBYTES
SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_NSECBYTES SODIUM_CRYPTO_AUTH_BYTES
SODIUM_CRYPTO_AUTH_KEYBYTES SODIUM_CRYPTO_BOX_KEYPAIRBYTES
SODIUM_CRYPTO_BOX_MACBYTES SODIUM_CRYPTO_BOX_NONCEBYTES
SODIUM_CRYPTO_BOX_PUBLICKEYBYTES SODIUM_CRYPTO_BOX_SEALBYTES
SODIUM_CRYPTO_BOX_SECRETKEYBYTES SODIUM_CRYPTO_BOX_SEEDBYTES
SODIUM_CRYPTO_CORE_RISTRETTO255_BYTES
SODIUM_CRYPTO_CORE_RISTRETTO255_HASHBYTES
SODIUM_CRYPTO_CORE_RISTRETTO255_NONREDUCEDSCALARBYTES
SODIUM_CRYPTO_CORE_RISTRETTO255_SCALARBYTES SODIUM_CRYPTO_GENERICHASH_BYTES
SODIUM_CRYPTO_GENERICHASH_BYTES_MAX SODIUM_CRYPTO_GENERICHASH_BYTES_MIN
SODIUM_CRYPTO_GENERICHASH_KEYBYTES SODIUM_CRYPTO_GENERICHASH_KEYBYTES_MAX
SODIUM_CRYPTO_GENERICHASH_KEYBYTES_MIN SODIUM_CRYPTO_KDF_BYTES_MAX
SODIUM_CRYPTO_KDF_BYTES_MIN SODIUM_CRYPTO_KDF_CONTEXTBYTES
SODIUM_CRYPTO_KDF_KEYBYTES SODIUM_CRYPTO_KX_KEYPAIRBYTES
SODIUM_CRYPTO_KX_PUBLICKEYBYTES SODIUM_CRYPTO_KX_SECRETKEYBYTES
SODIUM_CRYPTO_KX_SEEDBYTES SODIUM_CRYPTO_KX_SESSIONKEYBYTES
SODIUM_CRYPTO_PWHASH_ALG_ARGON2I13 SODIUM_CRYPTO_PWHASH_ALG_ARGON2ID13
SODIUM_CRYPTO_PWHASH_ALG_DEFAULT SODIUM_CRYPTO_PWHASH_MEMLIMIT_INTERACTIVE
SODIUM_CRYPTO_PWHASH_MEMLIMIT_MODERATE SODIUM_CRYPTO_PWHASH_MEMLIMIT_SENSITIVE
SODIUM_CRYPTO_PWHASH_OPSLIMIT_INTERACTIVE
SODIUM_CRYPTO_PWHASH_OPSLIMIT_MODERATE SODIUM_CRYPTO_PWHASH_OPSLIMIT_SENSITIVE
SODIUM_CRYPTO_PWHASH_SALTBYTES
SODIUM_CRYPTO_PWHASH_SCRYPTSALSA208SHA256_MEMLIMIT_INTERACTIVE
SODIUM_CRYPTO_PWHASH_SCRYPTSALSA208SHA256_MEMLIMIT_SENSITIVE
SODIUM_CRYPTO_PWHASH_SCRYPTSALSA208SHA256_OPSLIMIT_INTERACTIVE
SODIUM_CRYPTO_PWHASH_SCRYPTSALSA208SHA256_OPSLIMIT_SENSITIVE
SODIUM_CRYPTO_PWHASH_SCRYPTSALSA208SHA256_SALTBYTES
SODIUM_CRYPTO_PWHASH_SCRYPTSALSA208SHA256_STRPREFIX
SODIUM_CRYPTO_PWHASH_STRPREFIX SODIUM_CRYPTO_SCALARMULT_BYTES
SODIUM_CRYPTO_SCALARMULT_RISTRETTO255_BYTES
SODIUM_CRYPTO_SCALARMULT_RISTRETTO255_SCALARBYTES
SODIUM_CRYPTO_SCALARMULT_SCALARBYTES SODIUM_CRYPTO_SECRETBOX_KEYBYTES
SODIUM_CRYPTO_SECRETBOX_MACBYTES SODIUM_CRYPTO_SECRETBOX_NONCEBYTES
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_ABYTES
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_HEADERBYTES
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_KEYBYTES
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_MESSAGEBYTES_MAX
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_FINAL
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_MESSAGE
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_PUSH
SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_REKEY
SODIUM_CRYPTO_SHORTHASH_BYTES SODIUM_CRYPTO_SHORTHASH_KEYBYTES
SODIUM_CRYPTO_SIGN_BYTES SODIUM_CRYPTO_SIGN_KEYPAIRBYTES
SODIUM_CRYPTO_SIGN_PUBLICKEYBYTES SODIUM_CRYPTO_SIGN_SECRETKEYBYTES
SODIUM_CRYPTO_SIGN_SEEDBYTES SODIUM_CRYPTO_STREAM_KEYBYTES
SODIUM_CRYPTO_STREAM_NONCEBYTES SODIUM_CRYPTO_STREAM_XCHACHA20_KEYBYTES
SODIUM_CRYPTO_STREAM_XCHACHA20_NONCEBYTES SODIUM_LIBRARY_MAJOR_VERSION
SODIUM_LIBRARY_MINOR_VERSION SODIUM_LIBRARY_VERSION SOLR_EXTENSION_VERSION
SOLR_MAJOR_VERSION SOLR_MINOR_VERSION SOLR_PATCH_VERSION SOL_SOCKET SOL_TCP
SOL_UDP SOL_UDPLITE SOMAXCONN SORTARRIVAL SORTCC SORTDATE SORTFROM SORTSIZE
SORTSUBJECT SORTTO SORT_ASC SORT_DESC SORT_FLAG_CASE SORT_LOCALE_STRING
SORT_NATURAL SORT_NUMERIC SORT_REGULAR SORT_STRING SO_ATTACH_REUSEPORT_CBPF
SO_BINDTODEVICE SO_BPF_EXTENSIONS SO_BROADCAST SO_DEBUG SO_DETACH_BPF
SO_DETACH_FILTER SO_DONTROUTE SO_ERROR SO_FREE SO_INCOMING_CPU SO_KEEPALIVE
SO_LINGER SO_MARK SO_MEMINFO SO_NOSERVER SO_OOBINLINE SO_PASSCRED SO_RCVBUF
SO_RCVLOWAT SO_RCVTIMEO SO_REUSEADDR SO_REUSEPORT SO_SNDBUF SO_SNDLOWAT
SO_SNDTIMEO SO_TYPE SO_ZEROCOPY SQLITE3_ASSOC SQLITE3_BLOB SQLITE3_BOTH
SQLITE3_DETERMINISTIC SQLITE3_FLOAT SQLITE3_INTEGER SQLITE3_NULL SQLITE3_NUM
SQLITE3_OPEN_CREATE SQLITE3_OPEN_READONLY SQLITE3_OPEN_READWRITE SQLITE3_TEXT
SQL_BEST_ROWID SQL_BIGINT SQL_BINARY SQL_BIT SQL_CHAR SQL_CONCURRENCY
SQL_CONCUR_LOCK SQL_CONCUR_READ_ONLY SQL_CONCUR_ROWVER SQL_CONCUR_VALUES
SQL_CURSOR_DYNAMIC SQL_CURSOR_FORWARD_ONLY SQL_CURSOR_KEYSET_DRIVEN
SQL_CURSOR_STATIC SQL_CURSOR_TYPE SQL_CUR_USE_DRIVER SQL_CUR_USE_IF_NEEDED
SQL_CUR_USE_ODBC SQL_DATE SQL_DECIMAL SQL_DOUBLE SQL_ENSURE SQL_FETCH_FIRST
SQL_FETCH_NEXT SQL_FLOAT SQL_INDEX_ALL SQL_INDEX_UNIQUE SQL_INTEGER
SQL_KEYSET_SIZE SQL_LONGVARBINARY SQL_LONGVARCHAR SQL_NO_NULLS SQL_NULLABLE
SQL_NUMERIC SQL_ODBC_CURSORS SQL_QUICK SQL_REAL SQL_ROWVER SQL_SCOPE_CURROW
SQL_SCOPE_SESSION SQL_SCOPE_TRANSACTION SQL_SMALLINT SQL_TIME SQL_TIMESTAMP
SQL_TINYINT SQL_TYPE_DATE SQL_TYPE_TIME SQL_TYPE_TIMESTAMP SQL_VARBINARY
SQL_VARCHAR SQL_WCHAR SQL_WLONGVARCHAR SQL_WVARCHAR SSH2_DEFAULT_TERMINAL
SSH2_DEFAULT_TERM_HEIGHT SSH2_DEFAULT_TERM_UNIT SSH2_DEFAULT_TERM_WIDTH
SSH2_FINGERPRINT_HEX SSH2_FINGERPRINT_MD5 SSH2_FINGERPRINT_RAW
SSH2_FINGERPRINT_SHA1 SSH2_POLLERR SSH2_POLLEXT SSH2_POLLHUP SSH2_POLLIN
SSH2_POLLNVAL SSH2_POLLOUT SSH2_POLL_CHANNEL_CLOSED SSH2_POLL_LISTENER_CLOSED
SSH2_POLL_SESSION_CLOSED SSH2_STREAM_STDERR SSH2_STREAM_STDIO
SSH2_TERM_UNIT_CHARS SSH2_TERM_UNIT_PIXELS STDERR STDIN STDOUT
STREAM_BUFFER_FULL STREAM_BUFFER_LINE STREAM_BUFFER_NONE STREAM_CAST_AS_STREAM
STREAM_CAST_FOR_SELECT STREAM_CLIENT_ASYNC_CONNECT STREAM_CLIENT_CONNECT
STREAM_CLIENT_PERSISTENT STREAM_CRYPTO_METHOD_ANY_CLIENT
STREAM_CRYPTO_METHOD_ANY_SERVER STREAM_CRYPTO_METHOD_SSLv23_CLIENT
STREAM_CRYPTO_METHOD_SSLv23_SERVER STREAM_CRYPTO_METHOD_SSLv2_CLIENT
STREAM_CRYPTO_METHOD_SSLv2_SERVER STREAM_CRYPTO_METHOD_SSLv3_CLIENT
STREAM_CRYPTO_METHOD_SSLv3_SERVER STREAM_CRYPTO_METHOD_TLS_CLIENT
STREAM_CRYPTO_METHOD_TLS_SERVER STREAM_CRYPTO_METHOD_TLSv1_0_CLIENT
STREAM_CRYPTO_METHOD_TLSv1_0_SERVER STREAM_CRYPTO_METHOD_TLSv1_1_CLIENT
STREAM_CRYPTO_METHOD_TLSv1_1_SERVER STREAM_CRYPTO_METHOD_TLSv1_2_CLIENT
STREAM_CRYPTO_METHOD_TLSv1_2_SERVER STREAM_CRYPTO_METHOD_TLSv1_3_CLIENT
STREAM_CRYPTO_METHOD_TLSv1_3_SERVER STREAM_CRYPTO_PROTO_SSLv3
STREAM_CRYPTO_PROTO_TLSv1_0 STREAM_CRYPTO_PROTO_TLSv1_1
STREAM_CRYPTO_PROTO_TLSv1_2 STREAM_CRYPTO_PROTO_TLSv1_3 STREAM_FILTER_ALL
STREAM_FILTER_READ STREAM_FILTER_WRITE STREAM_IGNORE_URL STREAM_IPPROTO_ICMP
STREAM_IPPROTO_IP STREAM_IPPROTO_RAW STREAM_IPPROTO_TCP STREAM_IPPROTO_UDP
STREAM_IS_URL STREAM_META_ACCESS STREAM_META_GROUP STREAM_META_GROUP_NAME
STREAM_META_OWNER STREAM_META_OWNER_NAME STREAM_META_TOUCH
STREAM_MKDIR_RECURSIVE STREAM_MUST_SEEK STREAM_NOTIFY_AUTH_REQUIRED
STREAM_NOTIFY_AUTH_RESULT STREAM_NOTIFY_COMPLETED STREAM_NOTIFY_CONNECT
STREAM_NOTIFY_FAILURE STREAM_NOTIFY_FILE_SIZE_IS STREAM_NOTIFY_MIME_TYPE_IS
STREAM_NOTIFY_PROGRESS STREAM_NOTIFY_REDIRECTED STREAM_NOTIFY_RESOLVE
STREAM_NOTIFY_SEVERITY_ERR STREAM_NOTIFY_SEVERITY_INFO
STREAM_NOTIFY_SEVERITY_WARN STREAM_OOB STREAM_OPTION_BLOCKING
STREAM_OPTION_READ_BUFFER STREAM_OPTION_READ_TIMEOUT
STREAM_OPTION_WRITE_BUFFER STREAM_PEEK STREAM_PF_INET STREAM_PF_INET6
STREAM_PF_UNIX STREAM_REPORT_ERRORS STREAM_SERVER_BIND STREAM_SERVER_LISTEN
STREAM_SHUT_RD STREAM_SHUT_RDWR STREAM_SHUT_WR STREAM_SOCK_DGRAM
STREAM_SOCK_RAW STREAM_SOCK_RDM STREAM_SOCK_SEQPACKET STREAM_SOCK_STREAM
STREAM_URL_STAT_LINK STREAM_URL_STAT_QUIET STREAM_USE_PATH STR_PAD_BOTH
STR_PAD_LEFT STR_PAD_RIGHT ST_SET ST_SILENT ST_UID SUNFUNCS_RET_DOUBLE
SUNFUNCS_RET_STRING SUNFUNCS_RET_TIMESTAMP TCP_CONGESTION TCP_DEFER_ACCEPT
TCP_KEEPCNT TCP_KEEPIDLE TCP_KEEPINTVL TCP_NODELAY TCP_NOTSENT_LOWAT
TCP_QUICKACK TCP_REPAIR THOUSANDS_SEP THOUSEP TIDEWAYS_XHPROF_FLAGS_CPU
TIDEWAYS_XHPROF_FLAGS_MEMORY TIDEWAYS_XHPROF_FLAGS_MEMORY_ALLOC
TIDEWAYS_XHPROF_FLAGS_MEMORY_ALLOC_AS_MU TIDEWAYS_XHPROF_FLAGS_MEMORY_MU
TIDEWAYS_XHPROF_FLAGS_MEMORY_PMU TIDEWAYS_XHPROF_FLAGS_NO_BUILTINS
TIDY_NODETYPE_ASP TIDY_NODETYPE_CDATA TIDY_NODETYPE_COMMENT
TIDY_NODETYPE_DOCTYPE TIDY_NODETYPE_END TIDY_NODETYPE_JSTE TIDY_NODETYPE_PHP
TIDY_NODETYPE_PROCINS TIDY_NODETYPE_ROOT TIDY_NODETYPE_SECTION
TIDY_NODETYPE_START TIDY_NODETYPE_STARTEND TIDY_NODETYPE_TEXT
TIDY_NODETYPE_XMLDECL TIDY_TAG_A TIDY_TAG_ABBR TIDY_TAG_ACRONYM
TIDY_TAG_ADDRESS TIDY_TAG_ALIGN TIDY_TAG_APPLET TIDY_TAG_AREA TIDY_TAG_ARTICLE
TIDY_TAG_ASIDE TIDY_TAG_AUDIO TIDY_TAG_B TIDY_TAG_BASE TIDY_TAG_BASEFONT
TIDY_TAG_BDI TIDY_TAG_BDO TIDY_TAG_BGSOUND TIDY_TAG_BIG TIDY_TAG_BLINK
TIDY_TAG_BLOCKQUOTE TIDY_TAG_BODY TIDY_TAG_BR TIDY_TAG_BUTTON TIDY_TAG_CANVAS
TIDY_TAG_CAPTION TIDY_TAG_CENTER TIDY_TAG_CITE TIDY_TAG_CODE TIDY_TAG_COL
TIDY_TAG_COLGROUP TIDY_TAG_COMMAND TIDY_TAG_COMMENT TIDY_TAG_DATALIST
TIDY_TAG_DD TIDY_TAG_DEL TIDY_TAG_DETAILS TIDY_TAG_DFN TIDY_TAG_DIALOG
TIDY_TAG_DIR TIDY_TAG_DIV TIDY_TAG_DL TIDY_TAG_DT TIDY_TAG_EM TIDY_TAG_EMBED
TIDY_TAG_FIELDSET TIDY_TAG_FIGCAPTION TIDY_TAG_FIGURE TIDY_TAG_FONT
TIDY_TAG_FOOTER TIDY_TAG_FORM TIDY_TAG_FRAME TIDY_TAG_FRAMESET TIDY_TAG_H1
TIDY_TAG_H2 TIDY_TAG_H3 TIDY_TAG_H4 TIDY_TAG_H5 TIDY_TAG_H6 TIDY_TAG_HEAD
TIDY_TAG_HEADER TIDY_TAG_HGROUP TIDY_TAG_HR TIDY_TAG_HTML TIDY_TAG_I
TIDY_TAG_IFRAME TIDY_TAG_ILAYER TIDY_TAG_IMG TIDY_TAG_INPUT TIDY_TAG_INS
TIDY_TAG_ISINDEX TIDY_TAG_KBD TIDY_TAG_KEYGEN TIDY_TAG_LABEL TIDY_TAG_LAYER
TIDY_TAG_LEGEND TIDY_TAG_LI TIDY_TAG_LINK TIDY_TAG_LISTING TIDY_TAG_MAIN
TIDY_TAG_MAP TIDY_TAG_MARK TIDY_TAG_MARQUEE TIDY_TAG_MENU TIDY_TAG_MENUITEM
TIDY_TAG_META TIDY_TAG_METER TIDY_TAG_MULTICOL TIDY_TAG_NAV TIDY_TAG_NOBR
TIDY_TAG_NOEMBED TIDY_TAG_NOFRAMES TIDY_TAG_NOLAYER TIDY_TAG_NOSAVE
TIDY_TAG_NOSCRIPT TIDY_TAG_OBJECT TIDY_TAG_OL TIDY_TAG_OPTGROUP
TIDY_TAG_OPTION TIDY_TAG_OUTPUT TIDY_TAG_P TIDY_TAG_PARAM TIDY_TAG_PLAINTEXT
TIDY_TAG_PRE TIDY_TAG_PROGRESS TIDY_TAG_Q TIDY_TAG_RB TIDY_TAG_RBC TIDY_TAG_RP
TIDY_TAG_RT TIDY_TAG_RTC TIDY_TAG_RUBY TIDY_TAG_S TIDY_TAG_SAMP
TIDY_TAG_SCRIPT TIDY_TAG_SECTION TIDY_TAG_SELECT TIDY_TAG_SERVER
TIDY_TAG_SERVLET TIDY_TAG_SMALL TIDY_TAG_SOURCE TIDY_TAG_SPACER TIDY_TAG_SPAN
TIDY_TAG_STRIKE TIDY_TAG_STRONG TIDY_TAG_STYLE TIDY_TAG_SUB TIDY_TAG_SUMMARY
TIDY_TAG_SUP TIDY_TAG_TABLE TIDY_TAG_TBODY TIDY_TAG_TD TIDY_TAG_TEMPLATE
TIDY_TAG_TEXTAREA TIDY_TAG_TFOOT TIDY_TAG_TH TIDY_TAG_THEAD TIDY_TAG_TIME
TIDY_TAG_TITLE TIDY_TAG_TR TIDY_TAG_TRACK TIDY_TAG_TT TIDY_TAG_U TIDY_TAG_UL
TIDY_TAG_UNKNOWN TIDY_TAG_VAR TIDY_TAG_VIDEO TIDY_TAG_WBR TIDY_TAG_XMP
TOKEN_PARSE TRAP_BRKPT TRAP_TRACE TYPEAPPLICATION TYPEAUDIO TYPEIMAGE
TYPEMESSAGE TYPEMODEL TYPEMULTIPART TYPEOTHER TYPETEXT TYPEVIDEO T_ABSTRACT
T_AMPERSAND_FOLLOWED_BY_VAR_OR_VARARG
T_AMPERSAND_NOT_FOLLOWED_BY_VAR_OR_VARARG T_AND_EQUAL T_ARRAY T_ARRAY_CAST
T_AS T_ATTRIBUTE T_BAD_CHARACTER T_BOOLEAN_AND T_BOOLEAN_OR T_BOOL_CAST
T_BREAK T_CALLABLE T_CASE T_CATCH T_CLASS T_CLASS_C T_CLONE T_CLOSE_TAG
T_COALESCE T_COALESCE_EQUAL T_COMMENT T_CONCAT_EQUAL T_CONST
T_CONSTANT_ENCAPSED_STRING T_CONTINUE T_CURLY_OPEN T_DEC T_DECLARE T_DEFAULT
T_DIR T_DIV_EQUAL T_DNUMBER T_DO T_DOC_COMMENT T_DOLLAR_OPEN_CURLY_BRACES
T_DOUBLE_ARROW T_DOUBLE_CAST T_DOUBLE_COLON T_ECHO T_ELLIPSIS T_ELSE T_ELSEIF
T_EMPTY T_ENCAPSED_AND_WHITESPACE T_ENDDECLARE T_ENDFOR T_ENDFOREACH T_ENDIF
T_ENDSWITCH T_ENDWHILE T_END_HEREDOC T_ENUM T_EVAL T_EXIT T_EXTENDS T_FILE
T_FINAL T_FINALLY T_FMT T_FMT_AMPM T_FN T_FOR T_FOREACH T_FUNCTION T_FUNC_C
T_GLOBAL T_GOTO T_HALT_COMPILER T_IF T_IMPLEMENTS T_INC T_INCLUDE
T_INCLUDE_ONCE T_INLINE_HTML T_INSTANCEOF T_INSTEADOF T_INTERFACE T_INT_CAST
T_ISSET T_IS_EQUAL T_IS_GREATER_OR_EQUAL T_IS_IDENTICAL T_IS_NOT_EQUAL
T_IS_NOT_IDENTICAL T_IS_SMALLER_OR_EQUAL T_LINE T_LIST T_LNUMBER T_LOGICAL_AND
T_LOGICAL_OR T_LOGICAL_XOR T_MATCH T_METHOD_C T_MINUS_EQUAL T_MOD_EQUAL
T_MUL_EQUAL T_NAMESPACE T_NAME_FULLY_QUALIFIED T_NAME_QUALIFIED
T_NAME_RELATIVE T_NEW T_NS_C T_NS_SEPARATOR T_NULLSAFE_OBJECT_OPERATOR
T_NUM_STRING T_OBJECT_CAST T_OBJECT_OPERATOR T_OPEN_TAG T_OPEN_TAG_WITH_ECHO
T_OR_EQUAL T_PAAMAYIM_NEKUDOTAYIM T_PLUS_EQUAL T_POW T_POW_EQUAL T_PRINT
T_PRIVATE T_PROTECTED T_PUBLIC T_READONLY T_REQUIRE T_REQUIRE_ONCE T_RETURN
T_SL T_SL_EQUAL T_SPACESHIP T_SR T_SR_EQUAL T_START_HEREDOC T_STATIC T_STRING
T_STRING_CAST T_STRING_VARNAME T_SWITCH T_THROW T_TRAIT T_TRAIT_C T_TRY
T_UNSET T_UNSET_CAST T_USE T_VAR T_VARIABLE T_WHILE T_WHITESPACE T_XOR_EQUAL
T_YIELD T_YIELD_FROM ULOC_ACTUAL_LOCALE ULOC_VALID_LOCALE UNKNOWN_TYPE
UPLOAD_ERR_CANT_WRITE UPLOAD_ERR_EXTENSION UPLOAD_ERR_FORM_SIZE
UPLOAD_ERR_INI_SIZE UPLOAD_ERR_NO_FILE UPLOAD_ERR_NO_TMP_DIR UPLOAD_ERR_OK
UPLOAD_ERR_PARTIAL UUID_TYPE_DCE UUID_TYPE_DEFAULT UUID_TYPE_INVALID
UUID_TYPE_MD5 UUID_TYPE_NAME UUID_TYPE_NULL UUID_TYPE_RANDOM
UUID_TYPE_SECURITY UUID_TYPE_SHA1 UUID_TYPE_TIME UUID_VARIANT_DCE
UUID_VARIANT_MICROSOFT UUID_VARIANT_NCS UUID_VARIANT_OTHER
U_AMBIGUOUS_ALIAS_WARNING U_BAD_VARIABLE_DEFINITION U_BRK_ASSIGN_ERROR
U_BRK_ERROR_LIMIT U_BRK_ERROR_START U_BRK_HEX_DIGITS_EXPECTED U_BRK_INIT_ERROR
U_BRK_INTERNAL_ERROR U_BRK_MALFORMED_RULE_TAG U_BRK_MISMATCHED_PAREN
U_BRK_NEW_LINE_IN_QUOTED_STRING U_BRK_RULE_EMPTY_SET U_BRK_RULE_SYNTAX
U_BRK_SEMICOLON_EXPECTED U_BRK_UNCLOSED_SET U_BRK_UNDEFINED_VARIABLE
U_BRK_UNRECOGNIZED_OPTION U_BRK_VARIABLE_REDFINITION U_BUFFER_OVERFLOW_ERROR
U_CE_NOT_FOUND_ERROR U_COLLATOR_VERSION_MISMATCH U_DIFFERENT_UCA_VERSION
U_ENUM_OUT_OF_SYNC_ERROR U_ERROR_LIMIT U_ERROR_WARNING_LIMIT
U_ERROR_WARNING_START U_FILE_ACCESS_ERROR U_FMT_PARSE_ERROR_LIMIT
U_FMT_PARSE_ERROR_START U_IDNA_ACE_PREFIX_ERROR U_IDNA_CHECK_BIDI_ERROR
U_IDNA_DOMAIN_NAME_TOO_LONG_ERROR U_IDNA_ERROR_LIMIT U_IDNA_ERROR_START
U_IDNA_LABEL_TOO_LONG_ERROR U_IDNA_PROHIBITED_ERROR
U_IDNA_STD3_ASCII_RULES_ERROR U_IDNA_UNASSIGNED_ERROR
U_IDNA_VERIFICATION_ERROR U_IDNA_ZERO_LENGTH_LABEL_ERROR
U_ILLEGAL_ARGUMENT_ERROR U_ILLEGAL_CHARACTER U_ILLEGAL_CHAR_FOUND
U_ILLEGAL_CHAR_IN_SEGMENT U_ILLEGAL_ESCAPE_SEQUENCE U_ILLEGAL_PAD_POSITION
U_INDEX_OUTOFBOUNDS_ERROR U_INTERNAL_PROGRAM_ERROR
U_INTERNAL_TRANSLITERATOR_ERROR U_INVALID_CHAR_FOUND U_INVALID_FORMAT_ERROR
U_INVALID_FUNCTION U_INVALID_ID U_INVALID_PROPERTY_PATTERN
U_INVALID_RBT_SYNTAX U_INVALID_STATE_ERROR U_INVALID_TABLE_FILE
U_INVALID_TABLE_FORMAT U_INVARIANT_CONVERSION_ERROR
U_MALFORMED_EXPONENTIAL_PATTERN U_MALFORMED_PRAGMA U_MALFORMED_RULE
U_MALFORMED_SET U_MALFORMED_SYMBOL_REFERENCE U_MALFORMED_UNICODE_ESCAPE
U_MALFORMED_VARIABLE_DEFINITION U_MALFORMED_VARIABLE_REFERENCE
U_MEMORY_ALLOCATION_ERROR U_MESSAGE_PARSE_ERROR
U_MISMATCHED_SEGMENT_DELIMITERS U_MISPLACED_ANCHOR_START
U_MISPLACED_COMPOUND_FILTER U_MISPLACED_CURSOR_OFFSET U_MISPLACED_QUANTIFIER
U_MISSING_OPERATOR U_MISSING_RESOURCE_ERROR U_MISSING_SEGMENT_CLOSE
U_MULTIPLE_ANTE_CONTEXTS U_MULTIPLE_COMPOUND_FILTERS U_MULTIPLE_CURSORS
U_MULTIPLE_DECIMAL_SEPARATORS U_MULTIPLE_DECIMAL_SEPERATORS
U_MULTIPLE_EXPONENTIAL_SYMBOLS U_MULTIPLE_PAD_SPECIFIERS
U_MULTIPLE_PERCENT_SYMBOLS U_MULTIPLE_PERMILL_SYMBOLS U_MULTIPLE_POST_CONTEXTS
U_NO_SPACE_AVAILABLE U_NO_WRITE_PERMISSION U_PARSE_ERROR U_PARSE_ERROR_LIMIT
U_PARSE_ERROR_START U_PATTERN_SYNTAX_ERROR U_PRIMARY_TOO_LONG_ERROR
U_REGEX_BAD_ESCAPE_SEQUENCE U_REGEX_BAD_INTERVAL U_REGEX_ERROR_LIMIT
U_REGEX_ERROR_START U_REGEX_INTERNAL_ERROR U_REGEX_INVALID_BACK_REF
U_REGEX_INVALID_FLAG U_REGEX_INVALID_STATE U_REGEX_LOOK_BEHIND_LIMIT
U_REGEX_MAX_LT_MIN U_REGEX_MISMATCHED_PAREN U_REGEX_NUMBER_TOO_BIG
U_REGEX_PROPERTY_SYNTAX U_REGEX_RULE_SYNTAX U_REGEX_SET_CONTAINS_STRING
U_REGEX_UNIMPLEMENTED U_RESOURCE_TYPE_MISMATCH U_RULE_MASK_ERROR
U_SAFECLONE_ALLOCATED_WARNING U_SORT_KEY_TOO_SHORT_WARNING
U_STANDARD_ERROR_LIMIT U_STATE_OLD_WARNING U_STATE_TOO_OLD_ERROR
U_STRINGPREP_CHECK_BIDI_ERROR U_STRINGPREP_PROHIBITED_ERROR
U_STRINGPREP_UNASSIGNED_ERROR U_STRING_NOT_TERMINATED_WARNING
U_TOO_MANY_ALIASES_ERROR U_TRAILING_BACKSLASH U_TRUNCATED_CHAR_FOUND
U_UNCLOSED_SEGMENT U_UNDEFINED_SEGMENT_REFERENCE U_UNDEFINED_VARIABLE
U_UNEXPECTED_TOKEN U_UNMATCHED_BRACES U_UNQUOTED_SPECIAL
U_UNSUPPORTED_ATTRIBUTE U_UNSUPPORTED_ERROR U_UNSUPPORTED_ESCAPE_SEQUENCE
U_UNSUPPORTED_PROPERTY U_UNTERMINATED_QUOTE U_USELESS_COLLATOR_ERROR
U_USING_DEFAULT_WARNING U_USING_FALLBACK_WARNING U_VARIABLE_RANGE_EXHAUSTED
U_VARIABLE_RANGE_OVERLAP U_ZERO_ERROR WCONTINUED WNOHANG WSDL_CACHE_BOTH
WSDL_CACHE_DISK WSDL_CACHE_MEMORY WSDL_CACHE_NONE WUNTRACED X509_PURPOSE_ANY
X509_PURPOSE_CRL_SIGN X509_PURPOSE_NS_SSL_SERVER X509_PURPOSE_SMIME_ENCRYPT
X509_PURPOSE_SMIME_SIGN X509_PURPOSE_SSL_CLIENT X509_PURPOSE_SSL_SERVER
XDEBUG_CC_BRANCH_CHECK XDEBUG_CC_DEAD_CODE XDEBUG_CC_UNUSED
XDEBUG_FILTER_CODE_COVERAGE XDEBUG_FILTER_NONE XDEBUG_FILTER_STACK
XDEBUG_FILTER_TRACING XDEBUG_NAMESPACE_EXCLUDE XDEBUG_NAMESPACE_INCLUDE
XDEBUG_PATH_EXCLUDE XDEBUG_PATH_INCLUDE XDEBUG_STACK_NO_DESC
XDEBUG_TRACE_APPEND XDEBUG_TRACE_COMPUTERIZED XDEBUG_TRACE_HTML
XDEBUG_TRACE_NAKED_FILENAME XML_ATTRIBUTE_CDATA XML_ATTRIBUTE_DECL_NODE
XML_ATTRIBUTE_ENTITY XML_ATTRIBUTE_ENUMERATION XML_ATTRIBUTE_ID
XML_ATTRIBUTE_IDREF XML_ATTRIBUTE_IDREFS XML_ATTRIBUTE_NMTOKEN
XML_ATTRIBUTE_NMTOKENS XML_ATTRIBUTE_NODE XML_ATTRIBUTE_NOTATION
XML_CDATA_SECTION_NODE XML_COMMENT_NODE XML_DOCUMENT_FRAG_NODE
XML_DOCUMENT_NODE XML_DOCUMENT_TYPE_NODE XML_DTD_NODE XML_ELEMENT_DECL_NODE
XML_ELEMENT_NODE XML_ENTITY_DECL_NODE XML_ENTITY_NODE XML_ENTITY_REF_NODE
XML_ERROR_ASYNC_ENTITY XML_ERROR_ATTRIBUTE_EXTERNAL_ENTITY_REF
XML_ERROR_BAD_CHAR_REF XML_ERROR_BINARY_ENTITY_REF
XML_ERROR_DUPLICATE_ATTRIBUTE XML_ERROR_EXTERNAL_ENTITY_HANDLING
XML_ERROR_INCORRECT_ENCODING XML_ERROR_INVALID_TOKEN
XML_ERROR_JUNK_AFTER_DOC_ELEMENT XML_ERROR_MISPLACED_XML_PI XML_ERROR_NONE
XML_ERROR_NO_ELEMENTS XML_ERROR_NO_MEMORY XML_ERROR_PARAM_ENTITY_REF
XML_ERROR_PARTIAL_CHAR XML_ERROR_RECURSIVE_ENTITY_REF XML_ERROR_SYNTAX
XML_ERROR_TAG_MISMATCH XML_ERROR_UNCLOSED_CDATA_SECTION
XML_ERROR_UNCLOSED_TOKEN XML_ERROR_UNDEFINED_ENTITY XML_ERROR_UNKNOWN_ENCODING
XML_HTML_DOCUMENT_NODE XML_LOCAL_NAMESPACE XML_NAMESPACE_DECL_NODE
XML_NOTATION_NODE XML_OPTION_CASE_FOLDING XML_OPTION_SKIP_TAGSTART
XML_OPTION_SKIP_WHITE XML_OPTION_TARGET_ENCODING XML_PI_NODE XML_SAX_IMPL
XML_TEXT_NODE XSD_1999_NAMESPACE XSD_1999_TIMEINSTANT XSD_ANYTYPE XSD_ANYURI
XSD_ANYXML XSD_BASE64BINARY XSD_BOOLEAN XSD_BYTE XSD_DATE XSD_DATETIME
XSD_DECIMAL XSD_DOUBLE XSD_DURATION XSD_ENTITIES XSD_ENTITY XSD_FLOAT XSD_GDAY
XSD_GMONTH XSD_GMONTHDAY XSD_GYEAR XSD_GYEARMONTH XSD_HEXBINARY XSD_ID
XSD_IDREF XSD_IDREFS XSD_INT XSD_INTEGER XSD_LANGUAGE XSD_LONG XSD_NAME
XSD_NAMESPACE XSD_NCNAME XSD_NEGATIVEINTEGER XSD_NMTOKEN XSD_NMTOKENS
XSD_NONNEGATIVEINTEGER XSD_NONPOSITIVEINTEGER XSD_NORMALIZEDSTRING
XSD_NOTATION XSD_POSITIVEINTEGER XSD_QNAME XSD_SHORT XSD_STRING XSD_TIME
XSD_TOKEN XSD_UNSIGNEDBYTE XSD_UNSIGNEDINT XSD_UNSIGNEDLONG XSD_UNSIGNEDSHORT
XSL_CLONE_ALWAYS XSL_CLONE_AUTO XSL_CLONE_NEVER XSL_SECPREF_CREATE_DIRECTORY
XSL_SECPREF_DEFAULT XSL_SECPREF_NONE XSL_SECPREF_READ_FILE
XSL_SECPREF_READ_NETWORK XSL_SECPREF_WRITE_FILE XSL_SECPREF_WRITE_NETWORK
YAC_MAX_KEY_LEN YAC_MAX_RAW_COMPRESSED_LEN YAC_MAX_VALUE_RAW_LEN
YAC_SERIALIZER YAC_SERIALIZER_PHP YAC_VERSION YAML_ANY_BREAK YAML_ANY_ENCODING
YAML_ANY_SCALAR_STYLE YAML_BINARY_TAG YAML_BOOL_TAG YAML_CRLN_BREAK
YAML_CR_BREAK YAML_DOUBLE_QUOTED_SCALAR_STYLE YAML_FLOAT_TAG
YAML_FOLDED_SCALAR_STYLE YAML_INT_TAG YAML_LITERAL_SCALAR_STYLE YAML_LN_BREAK
YAML_MAP_TAG YAML_MERGE_TAG YAML_NULL_TAG YAML_PHP_TAG YAML_PLAIN_SCALAR_STYLE
YAML_SEQ_TAG YAML_SINGLE_QUOTED_SCALAR_STYLE YAML_STR_TAG YAML_TIMESTAMP_TAG
YAML_UTF16BE_ENCODING YAML_UTF16LE_ENCODING YAML_UTF8_ENCODING YESEXPR YESSTR
ZEND_ACC_ABSTRACT ZEND_ACC_FINAL ZEND_ACC_PPP_MASK ZEND_ACC_PRIVATE
ZEND_ACC_PROTECTED ZEND_ACC_PUBLIC ZEND_ACC_STATIC ZEND_DEBUG_BUILD
ZEND_THREAD_SAFE ZLIB_BLOCK ZLIB_BUF_ERROR ZLIB_DATA_ERROR
ZLIB_DEFAULT_STRATEGY ZLIB_ENCODING_DEFLATE ZLIB_ENCODING_GZIP
ZLIB_ENCODING_RAW ZLIB_ERRNO ZLIB_FILTERED ZLIB_FINISH ZLIB_FIXED
ZLIB_FULL_FLUSH ZLIB_HUFFMAN_ONLY ZLIB_MEM_ERROR ZLIB_NEED_DICT ZLIB_NO_FLUSH
ZLIB_OK ZLIB_PARTIAL_FLUSH ZLIB_RLE ZLIB_STREAM_END ZLIB_STREAM_ERROR
ZLIB_SYNC_FLUSH ZLIB_VERNUM ZLIB_VERSION ZLIB_VERSION_ERROR
"""
BUILTIN_CONSTANTS = frozenset(_BUILTIN_CONSTANT_NAMES.split())
"""The names of the constants PHP builds in with no namespace, as written:
PHP tells a constant's letter case."""
