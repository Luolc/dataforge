from pathlib import Path

import pytest

from dataforge.testrunner.manifest import File, load, ManifestError, read, Run
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


GROUPS = """
[[run]]
name = "rspec"
dir = "ruby"
run = ["sh", "run.sh"]
parse = "junit"
outputs = ["build/**"]

[[run]]
name = "own"
run = ["sh", "run.sh"]
parse = ["python3", "parse.py"]

[[run]]
name = "unit"
preset = "pytest"
dir = "py"

[restore]
paths = ["spec/**", ".rspec"]

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::adds"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::canary"
expect = "failed"

[[test]]
suite = "f2p"
run = "unit"
id = "t.py::test_a"
"""


def test_load_reads_run_groups_restore_and_expect():
  manifest = load(GROUPS)
  assert manifest.runs == (
    Run("rspec", "ruby", None, ("sh", "run.sh"), "junit", ("build/**",)),
    Run("own", ".", None, ("sh", "run.sh"), ("python3", "parse.py")),
    Run("unit", "py", "pytest"),
  )
  assert manifest.restore == ("spec/**", ".rspec")
  assert manifest.tests == (
    Listed("p2p", None, "spec/a_spec.rb::adds", "ruby", run="rspec"),
    Listed(
      "p2p",
      None,
      "spec/a_spec.rb::canary",
      "ruby",
      run="rspec",
      expect="failed",
    ),
    Listed("f2p", "pytest", "t.py::test_a", "py", run="unit"),
  )


@pytest.mark.parametrize(
  ("tables", "problem"),
  [
    (
      '[[run]]\nname = "a"\nrun = ["sh"]\n',
      "run 1 (a): parse, if not a format,",
    ),
    (
      '[[run]]\nname = "a"\nrun = []\nparse = "junit"\n',
      "run 1 (a): run is not",
    ),
    ('[[run]]\nname = "a"\npreset = "rspec"\n', "unknown preset 'rspec'"),
    (
      '[[run]]\nname = "a"\npreset = "go"\nparse = "junit"\n',
      "a preset has no run, parse or outputs",
    ),
    (
      '[[run]]\nname = "a"\nrun = ["x"]\nparse = "junit"\noutputs = ["../r"]\n',
      "outputs: '../r' is not a glob inside the project",
    ),
    (
      '[[run]]\nname = "a b"\npreset = "go"\n',
      "name 'a b' is not a plain name",
    ),
    (
      2 * '[[run]]\nname = "a"\npreset = "go"\n',
      "run 2 (a): named twice",
    ),
    ('[restore]\npaths = ["a/**b"]\n', "restore: paths: 'a/**b' is not a glob"),
    ('[restore]\npaths = ["sub/.git"]\n', "'sub/.git' is not a glob"),
    ("[restore]\nmore = 1\n", "restore: unknown key 'more'"),
  ],
)
def test_load_checks_each_run_group_and_restore(tables: str, problem: str):
  assert any(problem in p for p in problems(tables))


@pytest.mark.parametrize(
  ("test", "problem"),
  [
    ('run = "gone"', "no run 'gone'"),
    ('run = "a"\nrunner = "go"', "runner and dir are its run's"),
    ('run = "a"\ndir = "x"', "runner and dir are its run's"),
    ('run = "a"\nexpect = "passed"', "expect is not 'failed'"),
  ],
)
def test_load_checks_a_tests_run_and_expect(test: str, problem: str):
  group = '[[run]]\nname = "a"\npreset = "go"\n'
  text = f'{group}[[test]]\nsuite = "p2p"\nid = "t"\n{test}\n'
  assert any(problem in p for p in problems(text))


def test_an_id_appears_once_in_a_group_with_its_own_command():
  group = '[[run]]\nname = "a"\nrun = ["x"]\nparse = "ctrf"\n'
  test = '[[test]]\nsuite = "{}"\nrun = "a"\nid = "t"\n'
  text = group + test.format("p2p") + test.format("f2p")
  assert problems(text) == [
    "test 2 (f2p::t): its id is listed twice in run 'a'"
  ]
  preset = '[[run]]\nname = "a"\npreset = "go"\n'
  assert len(load(preset + test.format("p2p") + test.format("f2p")).tests) == 2
