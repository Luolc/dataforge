import json

import pytest

from dataforge.share.exports import ExportError, parse
from tests.share.sites import export_json

COLUMNS = [
  {
    "title": "Kind",
    "labels": {"plain": "Plain"},
    "hints": {"plain": "Made with no principle."},
    "cases": ["plain", "tightened"],
    "filter": True,
  },
  {
    "title": "Language",
    "description": "The project's language.",
    "none": "No language was found.",
  },
]


def with_columns(**more: object) -> dict[str, object]:
  """Make an export.json of one item, item-1, with the two COLUMNS.

  Args:
    **more: Keys to add to export.json, or to put in place of its own.

  Returns:
    The content, as JSON data.
  """
  data = export_json("example", ["item-1"])
  data["columns"] = COLUMNS
  data["items"] = [
    {
      "id": "item-1",
      "revision": 1,
      "values": ["plain", None],
      "reads": [None, "not run"],
    }
  ]
  return {**data, **more}


def test_columns_trials_and_deliverables_reach_the_page() -> None:
  data = with_columns(
    trials={"diff": "diff.patch", "tests": "ctrf.json", "kinds": {}},
    deliverables=[
      {"name": "pass_at_k", "kind": "trials", "title": "Pass@k"},
      {
        "name": "rubrics",
        "kind": "report",
        "title": "Reviews",
        "default": {"field": ["settings"], "equals": ["explicit"]},
      },
    ],
    kinds={"report": {"title": "Reviews"}},
  )

  export = parse(json.dumps(data).encode())

  public = export.public()
  assert public["columns"] == [
    {
      "title": "Kind",
      "format": "text",
      "labels": {"plain": "Plain"},
      "description": None,
      "hints": {"plain": "Made with no principle."},
      "none": None,
      "cases": ["plain", "tightened"],
      "filter": True,
    },
    {
      "title": "Language",
      "format": "text",
      "labels": {},
      "description": "The project's language.",
      "hints": {},
      "none": "No language was found.",
      "cases": [],
      "filter": False,
    },
  ]
  assert public["trials"] == {
    "diff": "diff.patch",
    "tests": "ctrf.json",
    "kinds": {},
  }
  assert public["deliverables"] == [
    {
      "name": "pass_at_k",
      "kind": "trials",
      "title": "Pass@k",
      "no_trace": None,
      "count": None,
      "default": False,
    },
    {
      "name": "rubrics",
      "kind": "report",
      "title": "Reviews",
      "no_trace": None,
      "count": None,
      "default": {"field": ["settings"], "equals": ["explicit"]},
    },
  ]
  assert public["kinds"] == {"report": {"title": "Reviews"}}
  assert [i.public() for i in export.items] == [
    {
      "id": "item-1",
      "revision": 1,
      "values": ["plain", None],
      "reads": [None, "not run"],
    }
  ]


def test_an_export_without_them_has_none() -> None:
  export = parse(json.dumps(export_json("example", ["item-1"])).encode())

  public = export.public()
  assert (public["columns"], public["trials"]) == ([], None)
  assert (public["deliverables"], public["kinds"]) == ([], {})
  assert export.items[0].public() == {
    "id": "item-1",
    "revision": 1,
    "values": None,
    "reads": None,
  }


@pytest.mark.parametrize(
  ("key", "row", "error"),
  [
    ("values", ["plain"], "items[0].values: 1 values for 2 columns"),
    ("values", ["plain", None, 3], "items[0].values: 3 values for 2 columns"),
    ("reads", [None], "items[0].reads: 1 values for 2 columns"),
    ("reads", [None, "read"], "items[0].reads[1]: not null or one of"),
  ],
)
def test_a_row_that_does_not_fit_the_columns_is_refused(
  key: str, row: list[object], error: str
) -> None:
  data = with_columns()
  item = {"id": "item-1", "revision": 1, key: row}

  with pytest.raises(ExportError, match=error.replace("[", r"\[")):
    _ = parse(json.dumps({**data, "items": [item]}).encode())


@pytest.mark.parametrize("path", ["/diff.patch", "../diff.patch", ""])
def test_a_trial_path_that_is_not_relative_is_refused(path: str) -> None:
  data = with_columns(trials={"diff": path})

  with pytest.raises(ExportError, match=r"^trials\.diff: "):
    _ = parse(json.dumps(data).encode())
