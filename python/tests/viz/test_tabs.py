import json
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge.viz.app import create_app
from dataforge.viz.config import ConfigError, load_site
from tests.viz.sites import make_tree, PROJECT, Tree

API = "/api/projects/example"
ATTEMPT = {"attempt": "batch-1/task-1"}

TABS = """\
deliverables:
  - {name: task, kind: files, title: Task}
tabs:
  - name: notes
    title: Notes
    versions: [notes, "notes-*"]
    file: out/notes.md
    fields:
      - {title: Verdict, file: tags.json, field: [verdict, value]}
      - {title: Outcome, file: status.json, field: [outcome]}
      - {title: Spent, file: status.json, field: [cost_usd], format: usd}
  - {name: other, title: Other, versions: ["other-*"], file: other.md}
"""

COLUMNS = """\
  - {title: Verdict, tab: notes, file: tags.json, field: [verdict, value]}
  - {title: Own, file: own.json, field: [n], format: number}
"""


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  made = make_tree(tmp_path)
  _ = made.project.write_text(PROJECT + COLUMNS + TABS)
  return made


def client(tree: Tree) -> TestClient:
  return TestClient(create_app(load_site(tree.site), tree.root / "no-static"))


def version(
  tree: Tree,
  name: str,
  tags: object = None,
  status: object = None,
  notes: str | None = "# Notes\n",
) -> Path:
  found = tree.attempt / name
  (found / "out").mkdir(parents=True)
  if notes is not None:
    _ = (found / "out" / "notes.md").write_text(notes)
  if tags is not None:
    _ = (found / "tags.json").write_text(json.dumps(tags))
  if status is not None:
    _ = (found / "status.json").write_text(json.dumps(status))
  return found


def found_(value: object) -> dict[str, object]:
  return {"read": "found", "value": value, "reason": None}


def not_run(reason: str) -> dict[str, object]:
  return {"read": "not run", "value": None, "reason": reason}


def missing(reason: str) -> dict[str, object]:
  return {"read": "missing", "value": None, "reason": reason}


def shown(tree: Tree) -> dict[str, object]:
  response = client(tree).get(f"{API}/tabs", params=ATTEMPT)
  assert response.status_code == 200
  body = cast(list[dict[str, object]], response.json())
  assert [t["name"] for t in body] == ["notes", "other"]
  return body[0]


def dirs(tab: dict[str, object]) -> list[str]:
  found = cast(list[dict[str, object]], tab["versions"])
  return [cast(str, v["dir"]) for v in found]


def test_a_project_file_s_tabs_load(tree: Tree) -> None:
  [project] = load_site(tree.site).projects

  [notes, other] = project.tabs
  assert notes.versions == ("notes", "notes-*")
  assert notes.file == ("out", "notes.md")
  assert [f.format for f in notes.fields] == ["text", "text", "usd"]
  assert other.fields == ()
  assert project.public()["tabs"] == [
    {
      "name": "notes",
      "title": "Notes",
      "versions": ["notes", "notes-*"],
      "file": "out/notes.md",
      "fields": [
        {"title": "Verdict", "format": "text"},
        {"title": "Outcome", "format": "text"},
        {"title": "Spent", "format": "usd"},
      ],
    },
    {
      "name": "other",
      "title": "Other",
      "versions": ["other-*"],
      "file": "other.md",
      "fields": [],
    },
  ]


@pytest.mark.parametrize(
  ("change", "key"),
  [
    (("versions: [notes, ", "versions: [a/notes, "), r"tabs\[0\]\.versions"),
    (("versions: [notes, ", "versions: [.., "), r"tabs\[0\]\.versions"),
    (("versions: [notes, ", "versions: [/notes, "), r"tabs\[0\]\.versions"),
    (("versions: [notes, ", "versions: ['', "), r"tabs\[0\]\.versions"),
    (('versions: [notes, "notes-*"]', "versions: []"), r"tabs\[0\]\.versions"),
    (('versions: [notes, "notes-*"]', "versions: notes"), r"tabs\[0\]\.vers"),
    (("file: out/notes.md", "file: ../notes.md"), r"tabs\[0\]\.file"),
    (("file: out/notes.md", "file: /notes.md"), r"tabs\[0\]\.file"),
    (("file: out/notes.md", "file: out/../x.md"), r"tabs\[0\]\.file"),
    (("Verdict, file: tags.json", "Verdict, file: ../t.json"), r"fields\[0\]"),
    (("Verdict, file: tags.json", "Verdict, file: /tags.json"), r"fields\[0\]"),
    (("field: [outcome]", "field: []"), r"fields\[1\]\.field"),
    (
      ("[cost_usd], format: usd}", "[c], format: euro}"),
      r"fields\[2\]\.format",
    ),
    (("[cost_usd], format: usd}", "[c], path: x}"), r"fields\[2\]\.path: unkn"),
    (("name: notes", "name: Notes"), r"tabs\[0\]\.name"),
    (("name: notes", "name: runs"), r"tabs\[0\]\.name: 'runs'"),
    (("name: notes", "name: files"), r"tabs\[0\]\.name: 'files'"),
    (("name: other", "name: notes"), r"tabs\[1\]\.name: 'notes' is repeated"),
    (("    file: out/notes.md\n", ""), r"tabs\[0\]\.file: missing"),
    ((TABS[TABS.index("tabs:") :], "tabs: []\n"), "tabs: not a non-empty list"),
    (("tab: notes, file: tags.json", "tab: none, file: x"), r"columns\[3\]"),
    (("tab: notes, file: tags.json", "tab: notes"), r"columns\[3\]\.file"),
    (("file: own.json", "file: ../own.json"), r"columns\[4\]\.file"),
    (("file: own.json", "file: /own.json"), r"columns\[4\]\.file"),
  ],
)
def test_a_tab_that_breaks_a_rule_is_refused(
  tree: Tree, change: tuple[str, str], key: str
) -> None:
  old, new = change
  text = tree.project.read_text()
  assert old in text
  _ = tree.project.write_text(text.replace(old, new, 1))

  with pytest.raises(ConfigError, match=key):
    _ = load_site(tree.site)


def test_versions_come_in_the_patterns_order_last_name_first(
  tree: Tree,
) -> None:
  for name in ("notes-run9", "notes-run10", "notes-eval2", "notes", "x-1"):
    _ = version(tree, name)

  assert dirs(shown(tree)) == [
    "notes",
    "notes-run10",
    "notes-run9",
    "notes-eval2",
  ]


def test_a_symlink_a_file_or_a_dot_name_is_no_version(tree: Tree) -> None:
  outside = tree.root.parent.parent / "elsewhere"
  outside.mkdir()
  _ = (outside / "out").mkdir()
  _ = (outside / "out" / "notes.md").write_text("a host file\n")
  (tree.attempt / "notes-link").symlink_to(outside)
  _ = (tree.attempt / "notes-file").write_text("not a directory\n")
  (tree.attempt / ".notes-hidden").mkdir()
  _ = version(tree, "notes-1")

  assert dirs(shown(tree)) == ["notes-1"]
  response = client(tree).get(
    f"{API}/files", params=ATTEMPT | {"path": "notes-link/out/notes.md"}
  )
  assert response.status_code == 404


def test_an_attempt_with_no_version_has_the_tab_empty(tree: Tree) -> None:
  assert shown(tree)["versions"] == []


def test_a_version_with_no_markdown_yet_is_listed(tree: Tree) -> None:
  _ = version(tree, "notes", notes=None)

  assert dirs(shown(tree)) == ["notes"]
  response = client(tree).get(
    f"{API}/files", params=ATTEMPT | {"path": "notes/out/notes.md"}
  )
  assert response.status_code == 404


def test_fields_are_read_from_the_version_shown(tree: Tree) -> None:
  _ = version(
    tree,
    "notes",
    tags={"verdict": {"value": None, "confidence": "low"}},
    status={"cost_usd": 1.5},
  )
  _ = version(tree, "notes-1", tags={"verdict": {"value": "n/a"}}, status=[])
  _ = version(tree, "notes-2", tags={"verdict": "plain"})
  bad = version(tree, "notes-3", status={"outcome": "ok"})
  _ = (bad / "tags.json").write_text('{"verdict": {"value": NaN}}')
  (version(tree, "notes-4") / "status.json").symlink_to(bad / "status.json")
  _ = (version(tree, "notes-5") / "tags.json").write_text("{")

  found = {
    cast(str, v["dir"]): v["fields"]
    for v in cast(list[dict[str, object]], shown(tree)["versions"])
  }

  absent = "No such file or directory"
  link = "a symlink or not a directory, which is never followed"
  assert found == {
    "notes": [
      found_(None),
      missing("notes/status.json has no outcome"),
      found_(1.5),
    ],
    "notes-1": [
      found_("n/a"),
      missing("notes-1/status.json has no outcome"),
      missing("notes-1/status.json has no cost_usd"),
    ],
    "notes-2": [
      missing("notes-2/tags.json has no verdict.value"),
      not_run(f"notes-2/status.json: {absent}"),
      not_run(f"notes-2/status.json: {absent}"),
    ],
    "notes-3": [
      missing("notes-3/tags.json is not JSON"),
      found_("ok"),
      missing("notes-3/status.json has no cost_usd"),
    ],
    "notes-4": [
      not_run(f"notes-4/tags.json: {absent}"),
      missing(f"notes-4/status.json: {link}"),
      missing(f"notes-4/status.json: {link}"),
    ],
    "notes-5": [
      missing("notes-5/tags.json is not JSON"),
      not_run(f"notes-5/status.json: {absent}"),
      not_run(f"notes-5/status.json: {absent}"),
    ],
  }


def test_the_tabs_route_reads_only_an_attempt(tree: Tree) -> None:
  web = client(tree)

  assert web.get(f"{API}/tabs", params={"attempt": "../.."}).status_code == 404
  assert web.get(f"{API}/tabs").status_code == 400


def test_a_column_reads_its_file_from_the_tab_s_first_version(
  tree: Tree,
) -> None:
  _ = version(tree, "notes-1", tags={"verdict": {"value": "older"}})
  _ = version(tree, "notes-2", tags={"verdict": {"value": "newer"}})
  _ = (tree.attempt / "own.json").write_text('{"n": 7}')

  [row] = cast(
    list[dict[str, object]], client(tree).get(f"{API}/attempts").json()
  )

  assert cast(list[object], row["values"])[3:] == ["newer", 7]
  assert row["reads"] == [None, None, None, "found", "found"]


def test_a_column_s_null_is_not_its_file_not_there(tree: Tree) -> None:
  _ = version(tree, "notes", tags={"verdict": {"value": None}})
  _ = (tree.attempt / "own.json").write_text('{"m": 7}')

  [row] = cast(
    list[dict[str, object]], client(tree).get(f"{API}/attempts").json()
  )

  assert row["values"] == ["passed", 0.42, None, None, None]
  assert row["reads"] == [None, None, None, "found", "missing"]


def test_a_column_with_no_file_or_version_is_not_run(tree: Tree) -> None:
  [row] = cast(
    list[dict[str, object]], client(tree).get(f"{API}/attempts").json()
  )

  assert row["error"] is None
  assert row["values"] == ["passed", 0.42, None, None, None]
  assert row["reads"] == [None, None, None, "not run", "not run"]
  _ = version(tree, "notes")

  [row] = cast(
    list[dict[str, object]], client(tree).get(f"{API}/attempts").json()
  )

  assert row["reads"] == [None, None, None, "not run", "not run"]
