import dataclasses
from datetime import datetime, UTC
import hashlib
import tomllib
from typing import cast

import pytest

from dataforge.task import (
  explicit_instruction,
  Metadata,
  metadata_toml,
  MetadataError,
  parse_legacy,
  parse_metadata,
  Principle,
  principles_sha256,
  public_toml,
  toml_text,
)

SENTENCE = (
  "A change that accepts a new kind of input gives it the same checks the"
  " old kinds get."
)
OTHER = "A limit the caller can raise is not a check the caller can skip."

# The rest of a task.toml after [metadata], as a dataset writes it, and as
# an export of a task with the metadata keys of the format before `tests`
# gave it out before dataforge.task: the multi-line literal string written
# anew as a basic one.
REST = (
  "\n[verifier]\n"
  "timeout_sec = 900.0\n"
  "\n[verifier.env]\n"
  'EVAL = "patch"\n'
  "\n[[verifier.collect]]\n"
  "command = '''\n"
  'set -eu\nmkdir -p "$out"\ngit diff --no-ext-diff \\\n  --no-color > x\n'
  "'''\n"
  "\n[environment]\n"
  'docker_image = "task:k7m2q9xr4t"\n'
  "cpus = 2\n"
)
PUBLIC_REST = (
  "\n[verifier]\n"
  "timeout_sec = 900.0\n"
  "\n[verifier.env]\n"
  'EVAL = "patch"\n'
  "\n[[verifier.collect]]\n"
  'command = """\n'
  'set -eu\nmkdir -p \\"$out\\"\n'
  "git diff --no-ext-diff \\\\\n  --no-color > x\n"
  '"""\n'
  "\n[environment]\n"
  'docker_image = "task:k7m2q9xr4t"\n'
  "cpus = 2\n"
)


def metadata(**changes: object) -> dict[str, object]:
  # A [metadata] of the format, with a group of the dataset's and a key of
  # its own in `instruction`.
  table: dict[str, object] = {
    "id": "k7m2q9xr4t",
    "language": "Python",
    "provenance": {"origin": "synthetic", "base_sha": "a" * 40},
    "instruction": {"tone": "bug_report", "settings": ["explicit", "implicit"]},
    "tests": {
      "f2p": {"vulnerability": 2, "behavior": 1},
      "p2p": {"project": 7, "behavior": 0},
    },
    "principles": [
      {
        "id": "p2",
        "text": OTHER,
        "type": "trade_off",
        "tests": ["f2p::test_limit"],
      },
      {"id": "p1", "text": SENTENCE, "source": "new", "tests": []},
    ],
  }
  return table | changes


def test_parse_metadata_reads_the_shared_part_and_keeps_the_datasets():
  found = parse_metadata(metadata())

  assert found == Metadata(
    id="k7m2q9xr4t",
    language="Python",
    tests={
      "f2p": {"vulnerability": 2, "behavior": 1},
      "p2p": {"project": 7, "behavior": 0},
    },
    principles=(
      Principle(
        id="p2", text=OTHER, type="trade_off", tests=("f2p::test_limit",)
      ),
      Principle(id="p1", text=SENTENCE, source="new"),
    ),
    settings=("implicit", "explicit"),
    extra={
      "provenance": {"origin": "synthetic", "base_sha": "a" * 40},
      "instruction": {"tone": "bug_report"},
    },
  )


def test_parse_metadata_takes_no_principles_and_implicit_by_default():
  found = parse_metadata({"language": "Go", "tests": {}})

  assert found == Metadata(id=None, language="Go", tests={})


@pytest.mark.parametrize(
  ("changes", "path"),
  [
    ({"language": 3}, "metadata.language"),
    ({"tone": "chat"}, "metadata.tone"),
    ({"tests": {"f2p": {"behavior": -1}}}, "metadata.tests.f2p.behavior"),
    ({"tests": {"f2p": {"behavior": True}}}, "metadata.tests.f2p.behavior"),
    ({"tests": {"f2p": {"behavior": "1"}}}, "metadata.tests.f2p.behavior"),
    ({"tests": {"e2e": {}}}, "metadata.tests.e2e"),
    ({"tests": {"p2p": 3}}, "metadata.tests.p2p"),
    (
      {"principles": [{"id": "p1", "text": "a"}, {"id": "p1", "text": "b"}]},
      "metadata.principles[1].id",
    ),
    (
      {"principles": [{"id": "p1", "text": "a", "type": "vulnerability"}]},
      "metadata.principles[0].type",
    ),
    (
      {"principles": [{"id": "p1", "text": "one\ntwo"}]},
      "metadata.principles[0].text",
    ),
    (
      {"principles": [{"id": "p1", "text": "a", "tests": ["t", "t"]}]},
      "metadata.principles[0].tests",
    ),
    (
      {"principles": [{"id": "p1", "text": "a", "kind": "x"}]},
      "metadata.principles[0].kind",
    ),
    ({"principles": []}, "metadata.instruction.settings"),
    ({"instruction": {"settings": []}}, "metadata.instruction.settings"),
  ],
)
def test_parse_metadata_names_the_key_that_is_wrong(
  changes: dict[str, object], path: str
):
  with pytest.raises(MetadataError) as e:
    _ = parse_metadata(metadata(**changes))
  assert e.value.path == path


def test_parse_metadata_refuses_a_table_with_no_tests():
  table = metadata()
  del table["tests"]
  with pytest.raises(MetadataError) as e:
    _ = parse_metadata(table)
  assert e.value.path == "metadata.tests"


def test_metadata_toml_writes_the_dataforge_keys_first_and_the_rest_sorted():
  text = metadata_toml(parse_metadata(metadata()))

  assert text == (
    "[metadata]\n"
    'id = "k7m2q9xr4t"\n'
    'language = "Python"\n'
    "\n[metadata.tests]\n"
    "\n[metadata.tests.f2p]\n"
    "behavior = 1\n"
    "vulnerability = 2\n"
    "\n[metadata.tests.p2p]\n"
    "behavior = 0\n"
    "project = 7\n"
    "\n[[metadata.principles]]\n"
    'id = "p2"\n'
    f'text = "{OTHER}"\n'
    'type = "trade_off"\n'
    'tests = ["f2p::test_limit"]\n'
    "\n[[metadata.principles]]\n"
    'id = "p1"\n'
    f'text = "{SENTENCE}"\n'
    'source = "new"\n'
    "tests = []\n"
    "\n[metadata.instruction]\n"
    'settings = ["implicit", "explicit"]\n'
    'tone = "bug_report"\n'
    "\n[metadata.provenance]\n"
    f'base_sha = "{"a" * 40}"\n'
    'origin = "synthetic"\n'
  )
  read = cast(dict[str, object], tomllib.loads(text)["metadata"])
  assert parse_metadata(read) == parse_metadata(metadata())


def test_metadata_toml_gives_equal_metadata_the_same_bytes():
  # The same tables with their keys in other orders.
  other = dict(reversed(metadata().items()))
  other["provenance"] = {"base_sha": "a" * 40, "origin": "synthetic"}
  other["tests"] = {
    "p2p": {"behavior": 0, "project": 7},
    "f2p": {"behavior": 1, "vulnerability": 2},
  }

  assert metadata_toml(parse_metadata(other)) == metadata_toml(
    parse_metadata(metadata())
  )


@pytest.mark.parametrize(
  "tests",
  [{}, {"f2p": {}}, {"f2p": {"vulnerability": 0}, "p2p": {"project": 0}}],
)
def test_metadata_toml_keeps_tests_when_nothing_is_counted(
  tests: dict[str, dict[str, int]],
):
  # The format is told apart from the one before it by `tests` alone.
  meta = Metadata(id=None, language="Rust", tests=tests)

  table = cast(
    dict[str, object], tomllib.loads(metadata_toml(meta))["metadata"]
  )

  assert table["tests"] == tests
  assert parse_metadata(table) == meta


def test_metadata_toml_refuses_a_key_of_dataforges_in_the_datasets_tables():
  meta = Metadata(id=None, language="Go", tests={}, extra={"tests": {}})
  with pytest.raises(MetadataError) as e:
    _ = metadata_toml(meta)
  assert e.value.path == "metadata.tests"


def test_public_toml_reads_as_the_export_before_with_tests_for_test_kinds():
  # Before dataforge.task an export gave out this task's [metadata] as
  #   id, language and [metadata.test_kinds] vulnerability = 2, behavior = 1;
  # the same task now has the counts under [metadata.tests.f2p].
  text = (
    'schema_version = "1.4"\n'
    "\n[metadata]\n"
    'id = "k7m2q9xr4t"\n'
    'language = "Python"\n'
    "\n[metadata.provenance]\n"
    'origin = "synthetic"\n'
    "\n[metadata.instruction]\n"
    'tone = "bug_report"\n'
    'settings = ["implicit", "explicit"]\n'
    "\n[metadata.tests.f2p]\n"
    "vulnerability = 2\n"
    "behavior = 1\n"
    "\n[[metadata.principles]]\n"
    'id = "p1"\n'
    f'text = "{SENTENCE}"\n'
  ) + REST

  found = public_toml(text, ["id", "language", "tests"])

  assert (
    found
    == (
      'schema_version = "1.4"\n'
      "\n[metadata]\n"
      'id = "k7m2q9xr4t"\n'
      'language = "Python"\n'
      "\n[metadata.tests]\n"
      "\n[metadata.tests.f2p]\n"
      "vulnerability = 2\n"
      "behavior = 1\n"
    )
    + PUBLIC_REST
  )


def test_public_toml_keeps_only_the_paths_the_allow_list_names():
  doc = {"schema_version": "1.4", "metadata": metadata(later={"x": "y"})}
  text = toml_text(doc)

  found = tomllib.loads(
    public_toml(text, ["language", "tests.f2p", "provenance.origin", "none.x"])
  )

  assert found == {
    "schema_version": "1.4",
    "metadata": {
      "language": "Python",
      "tests": {"f2p": {"vulnerability": 2, "behavior": 1}},
      "provenance": {"origin": "synthetic"},
    },
  }


def test_public_toml_keeps_a_file_without_metadata_as_read():
  assert (
    public_toml('a = 1\n\n[b]\nc = "d"\n', ["id"]) == 'a = 1\n\n[b]\nc = "d"\n'
  )


@pytest.mark.parametrize(
  "text",
  [
    "[metadata\n",
    "metadata = 1\n",
    '[metadata]\nlanguage = "Python"\n',
    '[metadata]\nlanguage = "Python"\n[metadata.tests.f2p]\nbehavior = "1"\n',
  ],
)
def test_public_toml_refuses_what_it_cannot_give_out_as_it_is(text: str):
  with pytest.raises(ValueError):
    _ = public_toml(text, ["id", "language", "tests"])


def test_explicit_instruction_of_one_principle_is_the_guideline_line():
  found = explicit_instruction(
    b"\n  Fix the parser.\n\n", [Principle(id="p1", text=SENTENCE)]
  )

  assert found == (
    b"Guideline for your change: A change that accepts a new kind of input"
    b" gives it the same checks the old kinds get.\n\n---\n\nFix the parser.\n"
  )


def test_explicit_instruction_lists_several_principles_by_their_ids():
  principles = [
    Principle(id="p2", text=OTHER),
    Principle(id="p1", text=SENTENCE),
  ]

  found = explicit_instruction(b"Fix the parser.\n", principles)

  assert (
    found
    == (
      f"Guidelines for your change:\n- {SENTENCE}\n- {OTHER}\n\n---\n\n"
      "Fix the parser.\n"
    ).encode()
  )


def test_explicit_instruction_refuses_no_principle():
  with pytest.raises(ValueError):
    _ = explicit_instruction(b"Fix the parser.\n", [])


def test_principles_sha256_of_one_principle_is_that_of_its_text():
  found = principles_sha256([Principle(id="p1", text=SENTENCE)])

  assert found == (
    "0622df056502f31aa78f0afa4238eb774f66021ccc47279e59eec899c10278c0"
  )


def test_principles_sha256_of_several_joins_their_texts_by_their_ids():
  one = [Principle(id="p2", text=OTHER), Principle(id="p1", text=SENTENCE)]

  found = principles_sha256(one)

  assert found == hashlib.sha256(f"{SENTENCE}\n{OTHER}".encode()).hexdigest()
  assert found == principles_sha256(one[::-1])
  assert principles_sha256([]) is None


@pytest.mark.parametrize(
  "principles",
  [
    [Principle(id="p1", text="a\nb")],
    [Principle(id="p1", text="a"), Principle(id="p1", text="b")],
  ],
)
def test_principles_sha256_refuses_what_could_hash_as_another_set(
  principles: list[Principle],
):
  # One principle "a\nb" would hash as the two "a" and "b".
  with pytest.raises(ValueError):
    _ = principles_sha256(principles)


# A [metadata] of the format before `tests`, as a dataset wrote it.
LEGACY: dict[str, object] = {
  "tone": "bug_report",
  "id": "k7m2q9xr4t",
  "origin": "synthetic",
  "cwe": ["CWE-22"],
  "language": "Python",
  "base_sha": "a" * 40,
  "test_kinds": {"vulnerability": 2, "behavior": 1},
  "alignment": SENTENCE,
  "difference": "best_practice",
  "settings": ["implicit", "explicit"],
  "tags": ["draft"],
}
GROUPS: dict[str, str] = {
  "tone": "instruction.tone",
  "origin": "provenance.origin",
  "cwe": "provenance.cwe",
  "base_sha": "provenance.base_sha",
}


def test_parse_legacy_maps_the_old_keys_to_the_format():
  found = parse_legacy(LEGACY, GROUPS, ["f2p::test_same_checks"])

  assert found == Metadata(
    id="k7m2q9xr4t",
    language="Python",
    tests={"f2p": {"vulnerability": 2, "behavior": 1}},
    principles=(
      Principle(
        id="p1",
        text=SENTENCE,
        type="best_practice",
        tests=("f2p::test_same_checks",),
      ),
    ),
    settings=("implicit", "explicit"),
    extra={
      "instruction": {"tone": "bug_report"},
      "provenance": {
        "origin": "synthetic",
        "cwe": ["CWE-22"],
        "base_sha": "a" * 40,
      },
    },
  )


@pytest.mark.parametrize("difference", ["vulnerability", "none", None])
def test_parse_legacy_takes_a_type_only_from_a_difference_of_types(
  difference: str | None,
):
  table = {k: v for k, v in LEGACY.items() if k != "difference"}
  if difference is not None:
    table["difference"] = difference

  found = parse_legacy(table, GROUPS)

  assert found.principles == (Principle(id="p1", text=SENTENCE),)


def test_parse_legacy_of_an_early_task_has_no_principles_and_empty_tests():
  table: dict[str, object] = {
    "language": "C",
    "cwe": [],
    "base_sha": "b" * 40,
    "tone": "x",
  }

  found = parse_legacy(table, GROUPS)

  assert found == Metadata(
    id=None,
    language="C",
    tests={},
    extra={
      "instruction": {"tone": "x"},
      "provenance": {"cwe": [], "base_sha": "b" * 40},
    },
  )


def test_parse_legacy_without_groups_leaves_out_the_dataset_s_keys():
  found = parse_legacy(LEGACY, None, ["f2p::test_same_checks"])

  grouped = parse_legacy(LEGACY, GROUPS, ["f2p::test_same_checks"])
  assert found == dataclasses.replace(grouped, extra={})


@pytest.mark.parametrize(
  ("table", "groups", "tests", "path"),
  [
    (
      LEGACY,
      {k: v for k, v in GROUPS.items() if k != "cwe"},
      (),
      "metadata.cwe",
    ),
    (LEGACY, GROUPS | {"cwe": "tests.cwe"}, [], "metadata.cwe"),
    (LEGACY, GROUPS | {"cwe": "cwe"}, [], "metadata.cwe"),
    (LEGACY, GROUPS | {"cwe": "instruction.settings"}, [], "metadata.cwe"),
    (LEGACY, GROUPS | {"cwe": "provenance.origin"}, [], "metadata.cwe"),
    ({"language": "C"}, GROUPS, ["f2p::t"], "metadata.alignment"),
  ],
)
def test_parse_legacy_refuses_what_it_cannot_place(
  table: dict[str, object],
  groups: dict[str, str],
  tests: list[str],
  path: str,
):
  with pytest.raises(MetadataError) as e:
    _ = parse_legacy(table, groups, tests)
  assert e.value.path == path


def test_toml_text_reads_back_and_keeps_keys_and_lines():
  doc: dict[str, object] = {
    "top": 'a "quoted" \\ line\nand \t\r\x01\x7f another \U0001f512\n',
    "a key": "quoted",
    "table": {
      "dotted.key": [1, 2.5, True, [], [{"inline": "x"}, 3]],
      "when": datetime(2026, 10, 21, 1, 2, 3, tzinfo=UTC),
      "empty": {},
    },
    "runs": [{"n": 1, "sub": {"x": "y"}}, {"n": 2}],
  }

  text = toml_text(doc)

  assert tomllib.loads(text) == doc
  assert '\n"a key" = ' in text
  assert '\n"dotted.key" = ' in text
  assert 'a \\"quoted\\" \\\\ line\nand ' in text


@pytest.mark.parametrize("value", [None, float("nan")])
def test_toml_text_refuses_what_does_not_read_back(value: object):
  with pytest.raises(ValueError):
    _ = toml_text({"key": value})
