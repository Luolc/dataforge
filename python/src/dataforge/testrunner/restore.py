"""Checking a project's files back to its base, and clearing a run's reports.

restore writes each file of the base that a glob matches from the base's
git objects, and deletes each file that a glob matches, the base lacks and
git does not ignore. It runs no `git checkout`: a filter a `.gitattributes`
names, or a hook, which the tests' side may have written, would run there.
Each git command runs with only the repository's own configuration, with
its hooks and fsmonitor off and replace refs not read.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess

from . import globs as glob
from .globs import matches
from .manifest import Manifest
from .runners import restore_paths, RUNNER_FILES

__all__ = ["globs", "matches", "remove", "restore"]

_GIT = (
  "git",
  "--no-replace-objects",
  "-c",
  "core.hooksPath=/dev/null",
  "-c",
  "core.fsmonitor=false",
)
_MODES = {"100644": 0o644, "100755": 0o755}
_LINK = "120000"


def globs(manifest: Manifest, base_files: Collection[str]) -> list[str]:
  """Return the globs of what a manifest's tests need at the base.

  Args:
    manifest: The manifest.
    base_files: The paths, relative to the project, of the base's files.

  Returns:
    Its [restore] paths, then for each runner its tests name, the files of
    RUNNER_FILES anywhere in the project, then the files restore_paths
    gives for its tests, each once.
  """
  found = list(manifest.restore)
  runners = dict.fromkeys(t.runner for t in manifest.tests if t.runner)
  for runner in runners:
    found += [f"{glob.ANY}/{p}" for p in RUNNER_FILES[runner]]
  for test in manifest.tests:
    if test.runner:
      found += [glob.escape(p) for p in restore_paths(test, base_files)]
  return list(dict.fromkeys(found))


def restore(app: Path, base: str, globs: Iterable[str]) -> list[str]:
  """Check back to the base the files some globs match.

  Args:
    app: The project, a git repository with the base commit.
    base: The base commit.
    globs: The globs.

  Returns:
    The paths written or deleted, sorted; a file that is already as at the
    base is not written. A git command that fails raises
    subprocess.CalledProcessError.
  """
  chosen = list(globs)
  entries = _tree(app, base)
  done: list[str] = []
  for path in _git(
    app, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
  ):
    if path not in entries and matches(path, chosen):
      target = app / path
      if os.path.lexists(target) and not _is_dir(target):
        target.unlink()
        done.append(path)
  wanted = {
    p: e
    for p, e in entries.items()
    if (e.mode in _MODES or e.mode == _LINK) and matches(p, chosen)
  }
  blobs = _blobs(app, [e.sha for e in wanted.values()])
  for path, entry in wanted.items():
    if _write(app, path, entry.mode, blobs[entry.sha]):
      done.append(path)
  return sorted(done)


def remove(app: Path, globs: Iterable[str]) -> list[str]:
  """Delete what some globs match in a project, a link not followed.

  Args:
    app: The project.
    globs: The globs.

  Returns:
    The paths deleted, sorted; a directory is deleted with what it holds.
    A `.git` directory is never looked into or deleted.
  """
  found = {p for one in globs for p in _matched(app, one)}
  done: list[str] = []
  for path in sorted(found):
    target = app / path
    if not os.path.lexists(target):
      continue
    if _is_dir(target):
      shutil.rmtree(target)
    else:
      target.unlink()
    done.append(path)
  return done


def _matched(app: Path, one: str) -> set[str]:
  # The paths a glob matches, walked from the directory its leading
  # components name, through no link and not into a .git.
  start = app
  for part in glob.literal_prefix(one):
    if not _is_dir(start):
      return set()
    start = start / part
  if not os.path.lexists(start):
    return set()
  found: set[str] = set()
  if matches(_rel(app, start), [one]):
    found.add(_rel(app, start))
  if not _is_dir(start):
    return found
  for root, dirs, files in os.walk(start):
    dirs[:] = [d for d in dirs if d != ".git"]
    for name in [*dirs, *files]:
      path = _rel(app, Path(root, name))
      if matches(path, [one]):
        found.add(path)
  return found


@dataclass(frozen=True, slots=True)
class _Entry:
  mode: str
  sha: str


def _tree(app: Path, base: str) -> dict[str, _Entry]:
  entries: dict[str, _Entry] = {}
  for line in _git(app, "ls-tree", "-r", "-z", "--full-tree", base):
    meta, _, path = line.partition("\t")
    mode, _, sha = meta.split(" ")
    entries[path] = _Entry(mode, sha)
  return entries


def _blobs(app: Path, shas: list[str]) -> dict[str, bytes]:
  if not shas:
    return {}
  unique = list(dict.fromkeys(shas))
  done = subprocess.run(
    [*_GIT, "-C", str(app), "cat-file", "--batch"],
    input="".join(f"{s}\n" for s in unique).encode(),
    capture_output=True,
    check=True,
    env=_env(),
  )
  out, at = done.stdout, 0
  found: dict[str, bytes] = {}
  for sha in unique:
    end = out.index(b"\n", at)
    header = out[at:end].split(b" ")
    if len(header) != 3 or header[0].decode() != sha:
      raise ValueError(f"git cat-file gave no blob for {sha}")
    size = int(header[2])
    found[sha] = out[end + 1 : end + 1 + size]
    at = end + 1 + size + 1
  return found


def _write(app: Path, path: str, mode: str, data: bytes) -> bool:
  target = app / path
  if _same(target, mode, data):
    return False
  here = app
  for part in path.split("/")[:-1]:
    here = here / part
    if not _is_dir(here):
      if os.path.lexists(here):
        here.unlink()
      here.mkdir()
  if _is_dir(target):
    shutil.rmtree(target)
  elif os.path.lexists(target):
    target.unlink()
  if mode == _LINK:
    os.symlink(os.fsdecode(data), target)
    return True
  with target.open("xb") as f:
    _ = f.write(data)
  target.chmod(_MODES[mode])
  return True


def _same(target: Path, mode: str, data: bytes) -> bool:
  try:
    st = os.lstat(target)
  except FileNotFoundError:
    return False
  if mode == _LINK:
    return stat.S_ISLNK(st.st_mode) and os.readlink(target) == os.fsdecode(data)
  if not stat.S_ISREG(st.st_mode) or st.st_size != len(data):
    return False
  executable = bool(st.st_mode & 0o100)
  return executable == (mode == "100755") and target.read_bytes() == data


def _is_dir(path: Path) -> bool:
  return path.is_dir() and not path.is_symlink()


def _rel(app: Path, path: Path) -> str:
  return path.relative_to(app).as_posix()


def _git(app: Path, *args: str) -> list[str]:
  done = subprocess.run(
    [*_GIT, "-C", str(app), *args],
    capture_output=True,
    check=True,
    env=_env(),
  )
  return [p for p in os.fsdecode(done.stdout).split("\0") if p]


def _env() -> dict[str, str]:
  return {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
  }
