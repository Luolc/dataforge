from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from tests.viz.sites import make_tree, PROJECT, Tree

API = "/api/projects/example/task"
ATTEMPT = {"attempt": "batch-1/task-1"}

# A task.toml of the format of dataforge.task, as a dataset writes one: p2p
# before f2p, a principle with no type or source, and a table of the
# dataset's own.
CURRENT = """\
[verifier]
timeout_sec = 600.0

[metadata]
id = "k7m2q9xr4t"
language = "Python"

[metadata.tests.p2p]
project = 12
behavior = 1

[metadata.tests.f2p]
security = 3
alignment = 1

[[metadata.principles]]
id = "p1"
text = "A change that accepts a new kind of input gives it the same checks."
type = "best_practice"
source = "new"
tests = ["f2p::test_same_checks"]

[[metadata.principles]]
id = "p2"
text = "A limit the caller can raise is not a check the caller can skip."

[metadata.provenance]
origin = "synthetic"
"""

# One of the format before `tests`, with keys of the dataset's own at the
# top level.
LEGACY = """\
[metadata]
language = "Go"
origin = "synthetic"
tone = "bug_report"
test_kinds = { security = 2, behavior = 1 }
alignment = "A limit the caller can raise is not a check the caller can skip."
difference = "trade_off"
"""


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  made = make_tree(tmp_path)
  _ = made.project.write_text(PROJECT + "task: task/task.toml\n")
  (made.attempt / "task").mkdir()
  return made


def get(tree: Tree) -> dict[str, object]:
  web = TestClient(create_app(load_site(tree.site), tree.root / "no-static"))
  response = web.get(API, params=ATTEMPT)
  assert response.status_code == 200
  return cast(dict[str, object], response.json())


def write(tree: Tree, text: str) -> None:
  _ = (tree.attempt / "task" / "task.toml").write_text(text)


def none(read: str, reason: str) -> dict[str, object]:
  return {
    "read": read,
    "reason": reason,
    "legacy": False,
    "tests": None,
    "principles": None,
  }


def test_a_task_gives_its_counts_by_suite_and_its_principles(
  tree: Tree,
) -> None:
  write(tree, CURRENT)

  found = get(tree)

  assert found == {
    "read": "found",
    "reason": None,
    "legacy": False,
    "tests": {
      "f2p": {"security": 3, "alignment": 1},
      "p2p": {"project": 12, "behavior": 1},
    },
    "principles": [
      {
        "id": "p1",
        "text": (
          "A change that accepts a new kind of input gives it the same checks."
        ),
        "type": "best_practice",
        "source": "new",
        "tests": ["f2p::test_same_checks"],
      },
      {
        "id": "p2",
        "text": (
          "A limit the caller can raise is not a check the caller can skip."
        ),
        "type": None,
        "source": None,
        "tests": [],
      },
    ],
  }
  assert list(cast(dict[str, object], found["tests"])) == ["f2p", "p2p"]


def test_a_task_of_the_format_before_tests_leaves_out_the_dataset_s_keys(
  tree: Tree,
) -> None:
  write(tree, LEGACY)

  assert get(tree) == {
    "read": "found",
    "reason": None,
    "legacy": True,
    "tests": {"f2p": {"security": 2, "behavior": 1}},
    "principles": [
      {
        "id": "p1",
        "text": (
          "A limit the caller can raise is not a check the caller can skip."
        ),
        "type": "trade_off",
        "source": None,
        "tests": [],
      }
    ],
  }


def test_an_attempt_with_no_task_toml_has_not_run(tree: Tree) -> None:
  assert get(tree) == none(
    "not run", "task/task.toml: No such file or directory"
  )


@pytest.mark.parametrize(
  ("text", "reason"),
  [
    ("[metadata\n", "task/task.toml is not TOML: "),
    ("[verifier]\ntimeout_sec = 1.0\n", "task/task.toml has no [metadata]"),
    (
      CURRENT.replace('type = "best_practice"', 'type = "rule"'),
      "task/task.toml: metadata.principles[0].type: is not one of",
    ),
    (
      LEGACY.replace('language = "Go"', "language = 1"),
      "task/task.toml: metadata.language: is not a string",
    ),
  ],
)
def test_a_task_toml_that_does_not_read_says_why(
  tree: Tree, text: str, reason: str
) -> None:
  write(tree, text)

  found = get(tree)

  assert found == none("missing", str(found["reason"]))
  assert str(found["reason"]).startswith(reason), found["reason"]


def test_a_task_toml_that_is_a_symlink_is_not_followed(tree: Tree) -> None:
  outside = tree.root.parent / "task.toml"
  _ = outside.write_text(CURRENT)
  (tree.attempt / "task" / "task.toml").symlink_to(outside)

  found = get(tree)

  assert found == none("missing", str(found["reason"]))
  assert "never followed" in str(found["reason"])


def test_a_task_toml_that_is_not_a_file_is_refused(tree: Tree) -> None:
  (tree.attempt / "task" / "task.toml").mkdir()

  assert get(tree) == none("missing", "task/task.toml: not a regular file")


def test_a_project_that_names_no_task_has_no_task_route(tree: Tree) -> None:
  _ = tree.project.write_text(PROJECT)
  web = TestClient(create_app(load_site(tree.site), tree.root / "no-static"))

  response = web.get(API, params=ATTEMPT)

  assert response.status_code == 404
  assert response.json()["reason"] == "the project 'example' names no task"
