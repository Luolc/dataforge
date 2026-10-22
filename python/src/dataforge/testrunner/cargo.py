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
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path, PurePosixPath
import re
from typing import final, override

from .adapter import Call, Context, rel, Runner
from .ctrf import Case
from .manifest import Test

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
