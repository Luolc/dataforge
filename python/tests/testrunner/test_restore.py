import os
from pathlib import Path
import subprocess

import pytest

from dataforge.testrunner import globs
from dataforge.testrunner.manifest import load
from dataforge.testrunner.restore import globs as restore_globs
from dataforge.testrunner.restore import remove, restore
from tests.testrunner.projects import commit, GIT_ENV, write


@pytest.mark.parametrize(
  ("glob", "path", "matched"),
  [
    ("spec/**", "spec", True),
    ("spec/**", "spec/a/b_spec.rb", True),
    ("spec/*", "spec/a/b_spec.rb", False),
    ("spec/*_spec.rb", "spec/a_spec.rb", True),
    ("**/conftest.py", "conftest.py", True),
    ("**/conftest.py", "a/b/conftest.py", True),
    ("**/conftest.py", "a/b/conftest.pyc", False),
    ("*.rb", "a/b.rb", False),
    ("*", ".rspec", True),
    ("a/**/z", "a/z", True),
    ("a/**/z", "a/b/c/z", True),
    ("a/**/z", "a/b/c/y", False),
    ("t[12].py", "t1.py", True),
  ],
)
def test_a_glob_matches_within_components_and_double_star_across(
  glob: str, path: str, matched: bool
):
  assert globs.matches(path, [glob]) is matched


def test_escape_matches_the_path_alone():
  path = "tests/test_[a]*.py"
  assert globs.matches(path, [globs.escape(path)])
  assert not globs.matches("tests/test_a.py", [globs.escape(path)])


BASE = {
  "spec/a_spec.rb": "base a\n",
  "spec/helper.rb": "base helper\n",
  "lib/code.rb": "base code\n",
  ".gitignore": "vendor/\n",
}


def test_restore_checks_matches_back_and_deletes_new_ones(tmp_path: Path):
  app = tmp_path / "app"
  base = commit(app, BASE)
  write(
    app,
    {
      "spec/a_spec.rb": "changed\n",
      "spec/new_spec.rb": "planted\n",
      "lib/code.rb": "the fix\n",
      "vendor/spec/x_spec.rb": "installed\n",
    },
  )
  (app / "spec/helper.rb").unlink()
  (app / "spec/helper.rb").symlink_to(app / "lib/code.rb")
  done = restore(app, base, ["spec/**", "**/x_spec.rb"])
  assert done == ["spec/a_spec.rb", "spec/helper.rb", "spec/new_spec.rb"]
  assert (app / "spec/a_spec.rb").read_text() == "base a\n"
  assert (app / "spec/helper.rb").read_text() == "base helper\n"
  assert not (app / "spec/helper.rb").is_symlink()
  assert not (app / "spec/new_spec.rb").exists()
  assert (app / "lib/code.rb").read_text() == "the fix\n"
  assert (app / "vendor/spec/x_spec.rb").exists()
  assert restore(app, base, ["spec/**"]) == []


def test_restore_writes_a_file_through_no_link_on_its_way(tmp_path: Path):
  app = tmp_path / "app"
  base = commit(app, BASE)
  outside = tmp_path / "outside"
  outside.mkdir()
  for name in os.listdir(app / "spec"):
    (app / "spec" / name).unlink()
  (app / "spec").rmdir()
  (app / "spec").symlink_to(outside)
  _ = restore(app, base, ["spec/a_spec.rb"])
  assert not (app / "spec").is_symlink()
  assert (app / "spec/a_spec.rb").read_text() == "base a\n"
  assert not os.listdir(outside)


def test_restore_runs_no_filter_or_hook_of_the_project(tmp_path: Path):
  app = tmp_path / "app"
  base = commit(app, BASE)
  marker = tmp_path / "ran"
  write(app, {".gitattributes": "*.rb filter=evil\n", "spec/a_spec.rb": "x\n"})
  for key in ("filter.evil.smudge", "filter.evil.clean", "core.fsmonitor"):
    _ = subprocess.run(
      ["git", "-C", str(app), "config", key, f"touch {marker}; cat"],
      check=True,
      env=GIT_ENV,
    )
  write(app, {".git/hooks/post-checkout": f"#!/bin/sh\ntouch {marker}\n"})
  (app / ".git/hooks/post-checkout").chmod(0o755)
  assert restore(app, base, ["spec/**"]) == ["spec/a_spec.rb"]
  assert (app / "spec/a_spec.rb").read_text() == "base a\n"
  assert not marker.exists()


def test_globs_joins_restore_and_the_runners_files():
  manifest = load(
    """
[restore]
paths = ["spec/**"]

[[test]]
suite = "p2p"
runner = "pytest"
id = "tests/test_[x].py::test_a"
"""
  )
  found = restore_globs(manifest, {"tests/test_[x].py"})
  assert found[0] == "spec/**"
  assert "**/conftest.py" in found
  assert found[-1] == "tests/test_[[]x].py"


def test_remove_deletes_matches_through_no_link(tmp_path: Path):
  app = tmp_path / "app"
  outside = tmp_path / "outside"
  write(outside, {"r.xml": ""})
  write(app, {"build/results/a.xml": "", "build/keep.txt": "", "b.xml": ""})
  (app / "build/results/link").symlink_to(outside)
  assert remove(app, ["build/results/**", "*.xml", "gone/**"]) == [
    "b.xml",
    "build/results",
  ]
  assert (app / "build/keep.txt").exists()
  assert (outside / "r.xml").exists()
