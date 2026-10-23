"""The adapter of cargo test.

A test's ID is `<source file>::<libtest name>`, the file the root of the
test's target from the project's root (src/lib.rs, src/main.rs,
src/bin/<name>.rs or tests/<name>.rs under the crate's directory, which is
the test's dir), the name as libtest gives it (`tests::adds`); a file covers
its target's tests, and a module path the tests in it. The tests run in
place, as `cargo test --offline --no-fail-fast` on their targets in the
crate's directory, on the project's own target directory and with its
environment as it is, so the project's own build compiles them.

Stable libtest writes no machine-readable report, so its text is read: the
`Running` line cargo writes before each test binary, a line for each test's
result and the binary's `test result` line, which counts them. A test's code
can write such lines itself, bypassing libtest's capture, so a binary's
results hold only when it ran once and its count is of what its lines
report: else each of its tests is an error.

It also holds Rust's checks of a fix's files (core.guards): the lines of
the base's files that hold listed tests (kept_lines), and the macros and
attributes that change what tests check (rewires).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath
import re
import sys
from typing import final, override

from .core import Call, Case, Context, rel, Runner, Spec, Test

SEP = "::"
"""What joins a test's file and name in its ID."""

_RUNNING = re.compile(r"\s*Running (?:unittests )?(\S+) \(.*\)\s*")
_DOC = re.compile(r"\s*Doc-tests \S+\s*")
_TEST = re.compile(r"test (.+?) (?:- should panic )?\.\.\. ?(.*)")
_RESULT = re.compile(r"(ok|FAILED|ignored)(?:, .*)?")
_SECTION = re.compile(r"---- (.+?) stdout ----")
_TOTAL = re.compile(r"(\d+) passed; (\d+) failed; (\d+) ignored;")
_STATUS = {"ok": "passed", "FAILED": "failed", "ignored": "skipped"}


def covers(outer: str, inner: str) -> bool:
  """Return whether an ID names a test or covers it, as a file or module.

  Args:
    outer: An ID, such as a test's.
    inner: Another, such as a case's.

  Returns:
    Whether inner is outer or inside it.
  """
  return inner == outer or inner.startswith(outer + SEP)


def target(source: str) -> list[str] | None:
  """Return the cargo test arguments that choose a source file's target.

  Args:
    source: The target's root, relative to the crate's directory.

  Returns:
    The arguments, or None for a file that is no target's root by cargo's
    default layout.
  """
  parts = PurePosixPath(source).parts
  if parts == ("src", "lib.rs"):
    return ["--lib"]
  if parts == ("src", "main.rs") or (
    parts[:2] == ("src", "bin") and _root(parts[2:])
  ):
    return ["--bins"]
  if parts[:1] == ("tests",) and _root(parts[1:]):
    return ["--test", PurePosixPath(parts[1]).stem]
  return None


def _root(rest: tuple[str, ...]) -> bool:
  # A target's root in a directory of targets: <name>.rs or <name>/main.rs.
  return (len(rest) == 1 and rest[0].endswith(".rs")) or (
    len(rest) == 2 and rest[1] == "main.rs"
  )


@final
class Cargo(Runner):
  """cargo test, its text output read.

  A binary whose results do not hold, such as one a test aborts before its
  `test result` line, is an errored case named as its file, and so is each
  of its tests; when no binary runs, as when a target does not compile,
  each listed file is one, with cargo's first error as its message. Doc
  tests are not run.
  """

  _REPORT = "cargo.txt"

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._sources: list[str] = []

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    self._sources = []
    targets: list[str] = []
    names: list[str] | None = []
    for test in tests:
      source, _, name = test.id.partition(SEP)
      path = ctx.app / source
      if ctx.dir not in path.parents or not path.is_file():
        continue
      chosen = target(path.relative_to(ctx.dir).as_posix())
      if chosen is None:
        continue
      if source not in self._sources:
        self._sources.append(source)
        if chosen[0] not in targets or chosen[0] == "--test":
          targets += chosen
      if not name:
        names = None
      elif names is not None and name not in names:
        names.append(name)
    if not self._sources:
      return []
    argv = ["cargo", "test", "--offline", "--no-fail-fast", *targets]
    # A name filters by substring, so each listed test and module runs, and
    # what else matches is left out as unlisted.
    if names:
      argv += ["--", *names]
    return [Call(argv, output=ctx.work / self._REPORT)]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    path = ctx.work / self._REPORT
    if path.is_symlink() or not path.is_file():
      return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return cargo_cases(ctx, text, self._sources)

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)


def cargo_cases(ctx: Context, text: str, sources: list[str]) -> list[Case]:
  """Return the cases of cargo test's output.

  Args:
    ctx: Where cargo ran.
    text: Its standard output and error, together.
    sources: The files of the listed tests, for when no binary ran.

  Returns:
    A case for each test a binary reported, in its order.
  """
  binaries: list[_Binary] = []
  binary: _Binary | None = None
  for line in text.splitlines():
    running = _RUNNING.fullmatch(line)
    if running or _DOC.fullmatch(line):
      binary = None
      if running:
        source = Path(running.group(1))
        binary = _Binary(
          rel(ctx, source if source.is_absolute() else ctx.dir / source)
        )
        binaries.append(binary)
      continue
    if binary is not None:
      binary.read(line)
  seen = [b.source for b in binaries]
  cases = [c for b in binaries for c in b.end(seen.count(b.source) > 1)]
  if not binaries:
    error = next(
      (x.strip() for x in text.splitlines() if x.startswith("error")), None
    )
    cases += [
      Case(s, "failed", error=True, message=error, file=s) for s in sources
    ]
  return cases


@final
class _Binary:
  # What one test binary reported.

  def __init__(self, source: str) -> None:
    self.source: str = source
    self.results: dict[str, str] = {}
    self.twice: set[str] = set()
    self.started: list[str] = []
    self.output: dict[str, list[str]] = {}
    self.section: str | None = None
    self.counted: dict[str, int] = dict.fromkeys(_STATUS.values(), 0)
    self.totals: list[dict[str, int]] = []

  def read(self, line: str) -> None:
    section = _SECTION.fullmatch(line)
    if section:
      name = section.group(1)
      self.section = name
      self.output[name] = []
      return
    if line == "failures:" or line.startswith("test result: "):
      self.section = None
      total = _TOTAL.search(line)
      if line.startswith("test result: "):
        self.totals.append(
          dict(zip(_STATUS.values(), map(int, total.groups()), strict=True))
          if total
          else {}
        )
      return
    if self.section is not None:
      self.output[self.section].append(line)
      return
    test = _TEST.fullmatch(line)
    if test is None:
      return
    name, rest = test.group(1), test.group(2)
    result = _RESULT.fullmatch(rest)
    if name not in self.started:
      self.started.append(name)
    if result is None:
      return
    if name in self.results:
      self.twice.add(name)
    self.results[name] = _STATUS[result.group(1)]
    self.counted[self.results[name]] += 1

  def end(self, again: bool) -> list[Case]:
    # Unless the binary ran once and its one `test result` line counts what
    # its lines report, a line may be forged or missing, and none holds.
    whole = None
    if again:
      whole = "a test binary of its file ran twice"
    elif not self.totals:
      whole = "its test binary ended with no result"
    elif self.totals != [self.counted]:
      whole = "its results do not match its binary's count of them"
    cases: list[Case] = []
    for name in self.started:
      trace = "\n".join(self.output.get(name, [])).strip() or None
      status = self.results.get(name)
      error = whole
      if name in self.twice:
        error = "it was reported twice"
      elif status is None:
        error = "it did not end"
      cases.append(
        Case(
          f"{self.source}{SEP}{name}",
          "failed" if error else status or "failed",
          error=error is not None,
          message=error or (_message(trace) if status == "failed" else None),
          trace=trace,
          file=self.source,
        )
      )
    if whole:
      cases.append(
        Case(self.source, "failed", error=True, message=whole, file=self.source)
      )
    return cases


def _message(trace: str | None) -> str | None:
  # The panic's message, past the line that says where it panicked.
  lines = [x.strip() for x in (trace or "").splitlines() if x.strip()]
  for line in lines:
    if not line.startswith(("thread '", "note: ")):
      return line
  return lines[0] if lines else None


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the base's file of a cargo test that is a test target of its own.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    Its ID's file when that is under the crate's tests/ and in files; a
    source file that holds unit tests is not restored, since a fix may
    change it too.
  """
  source = test.id.split(SEP, 1)[0]
  under = "" if test.dir == "." else test.dir + "/"
  if source.startswith(under + "tests/") and source in files:
    return [source]
  return []


SOURCES = (".rs",)
"""Rust's source files, which a task's copy has this module for
(bundle.guards_for)."""

PLAIN_SOURCES = SOURCES
"""Rust's tests are never in a file whose directory or name alone makes it
a test's (core.is_test_file)."""

WHOLE = (0, sys.maxsize)
"""kept_lines' span of every line of a file, and of a line put before the
first."""

# What kept_lines reads of Rust source with its comments and literals
# blanked: a raw string's opening, an attribute's, a `cfg` attribute's
# predicate, the visibility before `mod` and the module itself, inline (`{`)
# or in a file of its own (`;`).
_RAW = re.compile(r'(?:b|c)?r(#*)"')
_ATTR = re.compile(r"#\s*\[")
_CFG = re.compile(r"\s*cfg\s*\((.*)\)\s*\Z", re.DOTALL)
_CFG_TEST = re.compile(r"\btest\b")
_NOT = re.compile(r"\bnot\b")
_VIS = re.compile(r"pub\s*(?:\([^()]*\))?\s*")
_MOD = re.compile(r"mod\s+(?:r#)?[A-Za-z_]\w*\s*([{;])")

TEST_MACROS = frozenset(
  {
    "assert",
    "assert_eq",
    "assert_ne",
    "debug_assert",
    "debug_assert_eq",
    "debug_assert_ne",
    "panic",
    "unreachable",
    "todo",
    "unimplemented",
    "matches",
  }
)
"""std's macros that tests assert or fail with, which a `macro_rules!` of
the same name in the crate shadows (rewires)."""

# What rewires reads of the lines a fix adds, joined, with their comments
# removed and their whitespace collapsed to one space: a `macro_rules!` and
# its name, and an attribute, `#[...]` or `#![...]`, `cfg_attr`'s included,
# that sets `path`, which loads a module from another file.
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_MACRO_RULES = re.compile(r"macro_rules! ?(?:r#)?(\w+)")
_PATH_ATTR = re.compile(r"#!? ?\[[^\]]*\bpath ?=")


def _word_before(text: str, i: int) -> bool:
  # Whether the character before `i` continues a word, as in `fn r"`.
  return i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")


def _skipped(text: str, i: int) -> int:
  # Where the comment or literal of Rust that starts at `i` ends, or `i`
  # when none does.
  n = len(text)
  if text.startswith("//", i):
    j = text.find("\n", i)
    return n if j < 0 else j
  if text.startswith("/*", i):
    depth, j = 1, i + 2
    while j < n and depth:
      step = text[j : j + 2]
      depth += (step == "/*") - (step == "*/")
      j += 2 if step in ("/*", "*/") else 1
    return j
  if _word_before(text, i):
    return i
  if raw := _RAW.match(text, i):
    end = '"' + raw[1]
    j = text.find(end, raw.end())
    return n if j < 0 else j + len(end)
  quote = '"' if text.startswith('"', i) or text[i] in "bc" else "'"
  if text.startswith('"', i) or (
    text[i] in "bc" and text.startswith('"', i + 1)
  ):
    j = text.index('"', i) + 1
  elif text.startswith("'", i) and (
    text.startswith("\\", i + 1) or text.startswith("'", i + 2)
  ):
    # A character literal, not a lifetime such as 'a.
    j = i + 1
  else:
    return i
  while j < n and text[j] != quote:
    j += 2 if text[j] == "\\" else 1
  return min(j + 1, n)


def masked(text: str) -> str:
  """Return Rust source with its comments and literals blanked, lines kept.

  Args:
    text: The source.

  Returns:
    It with each character of a comment or literal but a newline made a
    space.
  """
  out: list[str] = []
  i = 0
  while i < len(text):
    j = _skipped(text, i)
    if j == i:
      out.append(text[i])
      i += 1
    else:
      out.append("".join(c if c == "\n" else " " for c in text[i:j]))
      i = j
  return "".join(out)


def _close(code: str, start: int, open_: str, close: str) -> int:
  # The index of the bracket that closes the one at `start`, or -1.
  depth = 0
  for j in range(start, len(code)):
    if code[j] == open_:
      depth += 1
    elif code[j] == close:
      depth -= 1
      if not depth:
        return j
  return -1


def test_modules(text: str) -> list[tuple[int, int]]:
  """Return the lines of each inline module of Rust source built for tests.

  A module is built for tests when a `cfg` attribute on it names `test`
  and not `not`: `#[cfg(test)]`, `#[cfg(all(test, unix))]`.

  Args:
    text: The source.

  Returns:
    For each such `mod <name> { ... }`, the line of its `cfg` attribute and
    that of its closing brace, from 1.
  """
  code = masked(text)
  found: list[tuple[int, int]] = []
  for m in _ATTR.finditer(code):
    end = _close(code, m.end() - 1, "[", "]")
    cfg = _CFG.match(code, m.end(), end) if end > 0 else None
    if cfg is None or not _CFG_TEST.search(cfg[1]) or _NOT.search(cfg[1]):
      continue
    j = end + 1
    while True:
      j += len(code[j:]) - len(code[j:].lstrip())
      if not _ATTR.match(code, j):
        break
      j = _close(code, code.index("[", j), "[", "]") + 1
      if not j:
        break
    vis = _VIS.match(code, j)
    mod = _MOD.match(code, vis.end() if vis else j)
    if mod is None or mod[1] != "{":
      continue
    close = _close(code, mod.end() - 1, "{", "}")
    if close > 0:
      first = code.count("\n", 0, m.start()) + 1
      found.append((first, code.count("\n", 0, close) + 1))
  return found


def named_module(text: str, name: str) -> list[tuple[int, int]]:
  """Return the lines of each inline module of Rust source of a name.

  Args:
    text: The source.
    name: The module's name.

  Returns:
    For each `mod <name> { ... }`, at any depth, the line of `mod` and that
    of its closing brace, from 1.
  """
  code = masked(text)
  found: list[tuple[int, int]] = []
  pattern = rf"\bmod\s+(?:r#)?{re.escape(name)}\s*\{{"
  for m in re.finditer(pattern, code):
    close = _close(code, m.end() - 1, "{", "}")
    if close > 0:
      first = code.count("\n", 0, m.start()) + 1
      found.append((first, code.count("\n", 0, close) + 1))
  return found


def test_source(app: Path, test_id: str) -> tuple[str, str | None] | None:
  """Return the file a cargo test is in, from its ID's target root.

  Its module path, its libtest name without its own name, is followed as
  Rust finds a module's file: a module `m` of a target's root or of a
  `mod.rs` is `m.rs` or `m/mod.rs` beside it, and of another file `f.rs` it
  is under `f/`. The first module with no such file is inline in the file
  reached.

  Args:
    app: The project at the base.
    test_id: The test's ID, `<target root>::<libtest name>`.

  Returns:
    The file's path from the project's root, and the name of the inline
    module of it the test is in, or None for its top; None when the root
    is not a file of `app`.
  """
  root, _, name = test_id.partition(SEP)
  path = PurePosixPath(root)
  if not (app / path).is_file():
    return None
  for module in name.split(SEP)[:-1]:
    beside = str(path) == root or path.name == "mod.rs"
    parent = path.parent if beside else path.with_suffix("")
    files = (parent / f"{module}.rs", parent / module / "mod.rs")
    found = next((f for f in files if (app / f).is_file()), None)
    if found is None:
      return str(path), module
    path = found
  return str(path), None


def kept_lines(
  app: Path, tests: Sequence[Test]
) -> dict[str, list[tuple[int, int]]]:
  """Return the lines of the base's Rust files that hold listed cargo tests.

  Those of the files that listed cargo tests are in and that restore_paths
  does not give, since they may hold the repair too (test_source): a
  file's inline modules built for tests (test_modules) and the inline
  module a test is in, built for tests or not (named_module), and all of a
  file that is not a target's root when a test is at its top, as in a file
  a `#[cfg(test)] mod <name>;` loads (WHOLE).

  Args:
    app: The project at the base.
    tests: The manifest's tests.

  Returns:
    For each such file, by its path from the project's root, its spans of
    lines, from 1.
  """
  found: dict[str, list[tuple[int, int]]] = {}
  for t in tests:
    root = t.id.split(SEP, 1)[0]
    if t.runner != "cargo" or restore_paths(t, {root}):
      continue
    source = test_source(app, t.id)
    if source is None or (app / source[0]).is_symlink():
      continue
    path, module = source
    spans = found.setdefault(path, [])
    text = (app / path).read_text(errors="replace")
    if module is None and path != root:
      spans.append(WHOLE)
    named = named_module(text, module) if module else []
    for span in [*test_modules(text), *named]:
      if span not in spans:
        spans.append(span)
  return found


def rewires(path: str, added: str) -> str | None:
  """Return what lines a fix adds to a Rust file change what tests check.

  Args:
    path: The file's path.
    added: The lines added, joined by newlines.

  Returns:
    In a file of SOURCES, `macro_rules! <name>` for a macro that shadows
    one of TEST_MACROS, or `#[path]`; None for neither.
  """
  if not path.endswith(SOURCES):
    return None
  text = " ".join(_COMMENT.sub(" ", added).split())
  for m in _MACRO_RULES.finditer(text):
    if m[1] in TEST_MACROS:
      return f"macro_rules! {m[1]}"
  return "#[path]" if _PATH_ATTR.search(text) else None


SPECS = {
  "cargo": Spec(
    Cargo,
    runner_files=(".cargo/config.toml", ".cargo/config"),
    id_sep=SEP,
    restore_paths=restore_paths,
  )
}
"""cargo's Spec."""
