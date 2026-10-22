from pathlib import Path

import pytest

from dataforge.testrunner.manifest import File, load, ManifestError, read
from dataforge.testrunner.manifest import Test as Listed

FULL = """
[[test]]
suite = "p2p"
runner = "pytest"
id = "tests/test_parse.py::test_attr"

[[test]]
suite = "f2p"
runner = "pytest"
id = "tests/test_parse.py::test_attr"
dir = "pkg"
kind = "alignment"
principle = "p1"

[[file]]
source = "f2p/test_new.py"
dest = "tests/test_new.py"

[no_tests]
extra = "the project has no such tests"
"""


def problems(
  text: str,
  principles: set[str] | None = None,
  base_files: set[str] | None = None,
) -> list[str]:
  with pytest.raises(ManifestError) as e:
    _ = load(text, principles=principles, base_files=base_files)
  return e.value.problems


def test_load_reads_every_field():
  manifest = load(FULL, principles={"p1"}, base_files={"tests/test_parse.py"})
  assert manifest.tests == (
    Listed("p2p", "pytest", "tests/test_parse.py::test_attr"),
    Listed(
      "f2p",
      "pytest",
      "tests/test_parse.py::test_attr",
      "pkg",
      "alignment",
      "p1",
    ),
  )
  assert manifest.files == (File("f2p/test_new.py", "tests/test_new.py"),)
  assert manifest.no_tests == {"extra": "the project has no such tests"}
  assert manifest.tests[1].ref == "f2p::tests/test_parse.py::test_attr"


def test_read_takes_the_manifest_of_tests(tmp_path: Path):
  _ = (tmp_path / "manifest.toml").write_text(FULL)
  assert len(read(tmp_path).tests) == 2


def test_an_empty_manifest_lists_nothing():
  manifest = load("")
  assert (manifest.tests, manifest.files, dict(manifest.no_tests)) == (
    (),
    (),
    {},
  )


def test_load_refuses_what_is_not_toml():
  assert problems("[[test]")[0].startswith("not TOML")


@pytest.mark.parametrize(
  ("table", "problem"),
  [
    ('suite = "p2p"\nrunner = "pytest"', "test 1: id is not"),
    ('suite = "P2P"\nrunner = "pytest"\nid = "a"', "is not a plain name"),
    ('suite = "p2p"\nrunner = "nose"\nid = "a"', "unknown runner 'nose'"),
    ('suite = "p2p"\nrunner = "pytest"\nid = ""', "test 1: id is not"),
    ('suite = "p2p"\nrunner = "pytest"\nid = "a"\nkind = 1', "kind is not"),
    ('suite = "p2p"\nrunner = "pytest"\nid = "a"\ndir = "../x"', "dir is not"),
    ('suite = "p2p"\nrunner = "pytest"\nid = "a"\ndir = "/x"', "dir is not"),
    (
      'suite = "p2p"\nrunner = "pytest"\nid = "a"\ntag = "x"',
      "unknown key 'tag'",
    ),
  ],
)
def test_load_checks_each_test(table: str, problem: str):
  found = problems(f"[[test]]\n{table}\n")
  assert any(problem in p for p in found), found


def test_a_suite_and_id_appear_once():
  one = '[[test]]\nsuite = "p2p"\nrunner = "pytest"\nid = "a"\n'
  assert problems(one + one) == ["test 2 (p2p::a): listed twice"]


def test_a_principle_must_be_one_of_the_tasks():
  text = (
    '[[test]]\nsuite = "f2p"\nrunner = "pytest"\nid = "a"\nprinciple = "p2"\n'
  )
  assert problems(text, principles={"p1"}) == [
    "test 1 (f2p::a): no principle 'p2'"
  ]
  assert load(text).tests[0].principle == "p2"


@pytest.mark.parametrize(
  ("files", "problem"),
  [
    ('source = "../x.py"\ndest = "x.py"', "source is not"),
    ('source = "x.py"\ndest = "a/../../x.py"', "dest is not"),
    ('source = "x.py"\ndest = "/etc/x.py"', "dest is not"),
    ('source = "x.py"\ndest = "a//x.py"', "dest is not"),
  ],
)
def test_load_checks_each_file(files: str, problem: str):
  found = problems(f"[[file]]\n{files}\n")
  assert any(problem in p for p in found), found


def test_a_dest_appears_once_and_is_no_file_of_the_base():
  text = '[[file]]\nsource = "a.py"\ndest = "t/a.py"\n'
  text += '[[file]]\nsource = "b.py"\ndest = "t/a.py"\n'
  assert problems(text) == ["file 2: dest 't/a.py' is listed twice"]
  one = '[[file]]\nsource = "a.py"\ndest = "t/a.py"\n'
  assert problems(one, base_files={"t/a.py"}) == [
    "file 1: dest 't/a.py' is a file of the base"
  ]
  assert load(one, base_files={"t/b.py"}).files[0].dest == "t/a.py"


def test_a_suite_with_no_tests_has_a_reason_and_no_test():
  test = '[[test]]\nsuite = "p2p"\nrunner = "pytest"\nid = "a"\n'
  assert problems(test + '[no_tests]\np2p = "none"\n') == [
    "no_tests: p2p has tests"
  ]
  assert problems('[no_tests]\np2p = " "\n') == [
    "no_tests: p2p gives no reason"
  ]


def test_load_refuses_an_unknown_table():
  assert problems('[[tests]]\nid = "a"\n') == ["unknown key 'tests'"]
