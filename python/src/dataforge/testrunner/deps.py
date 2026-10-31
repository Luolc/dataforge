"""Where a project's dependencies come from, and build files told by name.

The dependency overrides check, of where build files take dependencies
from, and where vendored dependencies are (vendor_dirs). Neither is a
runner's nor a language's: a project of any runner may hold such a build
file or a vendored dependency, such as a native extension's, and a fix
may add one, so every task's copy has this module (bundle.COMMON) and it
imports no runner's.

It also names the files through which package managers, bundlers and
transpilers run code of the project's when it builds (PACKAGE_MANIFEST,
SCRIPT_RUNNER_FILES, BUILD_CONFIG), which a dataset tells by name when it
filters a fix's changes. They are here because every task has this module,
so a dataset's code in a task takes them from it and names none of them.
"""

from __future__ import annotations

from collections.abc import Collection
import json
from pathlib import PurePosixPath
import posixpath
import re
import tomllib
from typing import cast

DEPENDENCY_FILES: dict[str, str] = {"go": "go.mod", "cargo": "Cargo.toml"}
"""For each runner that builds its tests with the project's dependencies,
the name of the build file that says where they come from: a fix may need to
change it, so it is not one of the runner's runner_files, and a dataset
compares it with the base's through dependency_overrides."""


def dependency_file(runner: str) -> str | None:
  """Return the name of the file that says where a runner's dependencies are.

  Args:
    runner: The runner.

  Returns:
    Its DEPENDENCY_FILES entry; None when it has none.
  """
  return DEPENDENCY_FILES.get(runner)


def dependency_overrides(
  runner: str, base_text: str | None, text: str
) -> list[str]:
  """Return what a build file puts in place of a dependency, beyond its base.

  A fix that points a dependency, such as a test framework or an assertion
  library, at a copy of its own makes the tests run against that copy; a
  dataset judges such a fix as it sees fit. A dependency's new version is
  not one, since it still comes from where the base's did.

  Args:
    runner: A runner of DEPENDENCY_FILES.
    base_text: The file at the base; None when the base has none.
    text: The file as the fix leaves it.

  Returns:
    The overrides text has and base_text has not, sorted: for go, the
    replace directives of a go.mod (replacements); for cargo, the patch and
    replace entries of a Cargo.toml and its dependencies taken from a path
    or git (overrides).

  Raises:
    ValueError: When either text cannot be read, or the runner has no
      dependency file.
  """
  read = {"go": replacements, "cargo": overrides}.get(runner)
  if read is None:
    raise ValueError(f"{runner} has no dependency file")
  before: set[str] = set() if base_text is None else read(base_text)
  return sorted(read(text) - before)


def replacements(text: str) -> set[str]:
  """Return the replace directives of a go.mod.

  The file is read as golang.org/x/mod's modfile reads it: `//` comments,
  quoted paths, and a directive's block, `replace (` up to `)`, each line
  of it a directive of its own.

  Args:
    text: The go.mod.

  Returns:
    Each directive as `<path> [<version>] => <path> [<version>]`.

  Raises:
    ValueError: When a string or a block is not closed.
  """
  found: set[str] = set()
  verb: str | None = None  # the verb of the block being read
  for line in _go_mod_lines(text):
    if verb is not None:
      if line == [")"]:
        verb = None
      else:
        line = [verb, *line]
    elif len(line) == 2 and line[1] == "(":
      verb = line[0]
      continue
    if line[0] == "replace" and "=>" in line[1:]:
      found.add(" ".join(line[1:]))
  if verb is not None:
    raise ValueError(f"go.mod: the {verb} block is not closed")
  return found


_GO_PUNCT = frozenset("()[]{},")


def _go_mod_lines(text: str) -> list[list[str]]:
  # The tokens of each line that has any, quoted strings unquoted.
  lines: list[list[str]] = []
  for raw in text.splitlines():
    tokens: list[str] = []
    i = 0
    while i < len(raw):
      c = raw[i]
      if c in " \t\r":
        i += 1
      elif raw.startswith("//", i):
        break
      elif c in _GO_PUNCT:
        tokens.append(c)
        i += 1
      elif c in '"`':
        end = _go_string_end(raw, i)
        quoted = raw[i : end + 1]
        tokens.append(quoted[1:-1] if c == "`" else json.loads(quoted))
        i = end + 1
      else:
        start = i
        while (
          i < len(raw)
          and raw[i] not in " \t\r"
          and raw[i] not in _GO_PUNCT
          and not raw.startswith("//", i)
        ):
          i += 1
        tokens.append(raw[start:i])
    if tokens:
      lines.append(tokens)
  return lines


def _go_string_end(line: str, start: int) -> int:
  # The index of the quote that closes the string opening at start.
  quote, i = line[start], start + 1
  while i < len(line):
    if line[i] == "\\" and quote == '"':
      i += 2
    elif line[i] == quote:
      return i
    else:
      i += 1
  raise ValueError(f"go.mod: a string is not closed: {line}")


def overrides(text: str) -> set[str]:
  """Return what a Cargo.toml puts in place of a dependency's source.

  That is each entry of its `patch` and `replace` tables, and each
  dependency of a dependency table (`dependencies`, `dev-dependencies` and
  `build-dependencies`, with their old `_` spellings, those of each
  `target`, and `workspace.dependencies`) taken from a path or a git
  repository, with the keys that say which.

  Args:
    text: The Cargo.toml.

  Returns:
    Each as `<table>.<name> = <its value or keys as JSON>`.

  Raises:
    ValueError: When it is not TOML.
  """  # noqa: DOC502
  doc = tomllib.loads(text)
  found: set[str] = set()
  for key in ("patch", "replace"):
    for name, value in _leaves(
      doc.get(key), (key,), 2 if key == "patch" else 1
    ):
      found.add(f"{name} = {json.dumps(value, sort_keys=True)}")
  for table, deps in _dependency_tables(doc):
    for name, dep in deps.items():
      if not isinstance(dep, dict):
        continue
      source = {
        k: v for k, v in cast("dict[str, object]", dep).items() if k in _SOURCE
      }
      if "path" in source or "git" in source:
        line = (
          f"{'.'.join((*table, name))} = {json.dumps(source, sort_keys=True)}"
        )
        found.add(line)
  return found


_DEPENDENCIES = (
  "dependencies",
  "dev-dependencies",
  "dev_dependencies",
  "build-dependencies",
  "build_dependencies",
)
# The keys that say where a dependency comes from, besides the registry.
_SOURCE = frozenset({"path", "git", "branch", "tag", "rev"})


def _leaves(
  value: object, prefix: tuple[str, ...], depth: int
) -> list[tuple[str, object]]:
  # The entries depth tables below value, by their dotted names.
  if not isinstance(value, dict):
    return [] if value is None else [(".".join(prefix), value)]
  table = cast("dict[str, object]", value)
  if depth == 0:
    return [(".".join(prefix), table)]
  return [
    leaf
    for key, inner in table.items()
    for leaf in _leaves(inner, (*prefix, key), depth - 1)
  ]


def _dependency_tables(
  doc: dict[str, object],
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
  # Each dependency table, by its path, with its entries.
  found: list[tuple[tuple[str, ...], dict[str, object]]] = []
  scopes: list[tuple[tuple[str, ...], object]] = [((), doc)]
  targets = doc.get("target")
  if isinstance(targets, dict):
    scopes += [
      (("target", cfg), inner)
      for cfg, inner in cast("dict[str, object]", targets).items()
    ]
  for prefix, scope in scopes:
    if not isinstance(scope, dict):
      continue
    for key in _DEPENDENCIES:
      deps = cast("dict[str, object]", scope).get(key)
      if isinstance(deps, dict):
        found.append(((*prefix, key), cast("dict[str, object]", deps)))
  workspace = doc.get("workspace")
  if isinstance(workspace, dict):
    deps = cast("dict[str, object]", workspace).get("dependencies")
    if isinstance(deps, dict):
      found.append(
        (("workspace", "dependencies"), cast("dict[str, object]", deps))
      )
  return found


VENDOR = "vendor"
"""The directory, beside a file of VENDOR_OWNERS or in the dir of a test
whose runner loads it (core.vendor_test_dirs), that dependencies are loaded
from."""

VENDOR_OWNERS = frozenset({"go.mod", "composer.json"})
"""The build files whose directory's VENDOR go and Composer load the
dependencies from."""

CARGO_CHECKSUM = ".cargo-checksum.json"
"""The file that marks a crate `cargo vendor` wrote."""


def vendored(
  path: str, owners: Collection[str], crates: Collection[str] = ()
) -> bool:
  """Return whether a path is a vendored dependency's, or under one.

  Args:
    path: The path, relative to the project.
    owners: The directories, from the project's root ("" for it), whose
      VENDOR holds dependencies (vendor_dirs).
    crates: The directories of crates `cargo vendor` wrote.

  Returns:
    Whether it is the VENDOR of a directory of owners, a link among them,
    or under it, or under a directory of crates.
  """
  parts = PurePosixPath(path).parts
  if any(
    part == VENDOR and "/".join(parts[:i]) in owners
    for i, part in enumerate(parts)
  ):
    return True
  return any(path.startswith(c + "/") for c in crates)


def vendor_dirs(
  paths: Collection[str], dirs: Collection[str] = ()
) -> tuple[set[str], set[str]]:
  """Return where vendored dependencies are among a project's paths.

  Args:
    paths: The paths of the project's files, those a fix adds among them.
    dirs: The dir of each test whose runner loads its VENDOR
      (core.vendor_test_dirs).

  Returns:
    The directories that hold a file of VENDOR_OWNERS, with dirs, and
    those below the root that hold a CARGO_CHECKSUM.
  """
  owners = {"" if d == "." else d for d in map(posixpath.normpath, dirs)}
  crates: set[str] = set()
  for path in paths:
    p = PurePosixPath(path)
    parent = "" if str(p.parent) == "." else str(p.parent)
    if p.name in VENDOR_OWNERS:
      owners.add(parent)
    elif p.name == CARGO_CHECKSUM and parent:
      crates.add(parent)
  return owners, crates


PACKAGE_MANIFEST = "package.json"
"""The manifest of a Node.js package, whose `scripts` npm, pnpm and yarn
run by name: a build that runs a package's script runs that script and,
under npm and pnpm, its `pre<name>` and `post<name>` around it. A fix may
need to change the manifest's dependencies, so it is not one of a runner's
runner_files (core.runner_files), and a dataset compares only its `scripts`
with the base's."""

SCRIPT_RUNNER_FILES = frozenset(
  {
    ".npmrc",
    ".pnpmfile.cjs",
    "pnpm-workspace.yaml",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnp.cjs",
    ".pnp.loader.mjs",
  }
)
"""The files npm, pnpm or yarn read by themselves, by their names, when they
run a package's script: `.npmrc`, whose `script-shell` names the shell
every script runs in, `node-options` the options of each `node` it starts
and `pnpmfile` a file pnpm loads; `.pnpmfile.cjs`, which pnpm loads on
every command, `pnpm run` among them; `pnpm-workspace.yaml`, which says
whose scripts a recursive run runs; `.yarnrc` and `.yarnrc.yml`, whose
`yarn-path` and `yarnPath` name the yarn that runs; and `.pnp.cjs` and
`.pnp.loader.mjs`, Plug'n'Play's loaders, which yarn under Plug'n'Play adds
to the `NODE_OPTIONS` of each script it runs, and pnpm a `.pnp.cjs` it finds
in the workspace's or the package's directory."""

BUILD_CONFIG = re.compile(
  r"(?:tsdown|tsup|vite|rollup|rolldown|webpack|rspack|esbuild|babel"
  + r"|postcss|build)\.config\.[cm]?[jt]s|babel\.config\.json"
  + r"|\.babelrc(?:\.(?:json|[cm]?[jt]s))?|\.postcssrc(?:\..+)?"
)
"""The names, matched whole (fullmatch on a file's name, never its path),
of the configuration files a JavaScript bundler or transpiler loads by
itself as code or as a list of plugins, which can be files of the
project's: tsdown, tsup, Vite, Rollup, Rolldown, webpack, Rspack, esbuild,
Babel, PostCSS and unbuild (`build.config.*`). `.babelrc` also takes the
TypeScript extensions Babel reads, `.ts` and `.mts`, which it loads only from
a path it is given."""
