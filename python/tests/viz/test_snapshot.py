import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from dataforge.viz.snapshot import check, SnapshotError
from tests.viz.sites import FIXTURES, make_pages_tree, PagesTree

API = "/api/projects/example/pages/steps"
GOOD = cast(
  dict[str, object], json.loads((FIXTURES / "snapshot.json").read_text())
)


@pytest.fixture
def made(tmp_path: Path) -> PagesTree:
  return make_pages_tree(tmp_path)


def client(made: PagesTree) -> TestClient:
  site = load_site(made.tree.site)
  return TestClient(create_app(site, made.tree.root / "no-static"))


def test_a_snapshot_is_served_as_the_file_holds_it(made: PagesTree) -> None:
  response = client(made).get(f"{API}/snapshot")

  assert response.status_code == 200
  assert response.headers["content-type"] == "application/json"
  assert response.content == made.snapshot.read_bytes()


def test_a_snapshot_not_written_yet_names_its_file(made: PagesTree) -> None:
  made.snapshot.unlink()

  response = client(made).get(f"{API}/snapshot")

  assert response.status_code == 404
  assert response.json() == {
    "error": "no snapshot",
    "reason": "no snapshot has been written",
    "file": "../snapshot/steps.json",
  }


def test_an_absolute_snapshot_is_named_by_its_file_name_alone(
  made: PagesTree,
) -> None:
  project = made.tree.project
  _ = project.write_text(
    project.read_text().replace("../snapshot/steps.json", str(made.snapshot))
  )
  made.snapshot.unlink()

  response = client(made).get(f"{API}/snapshot")

  assert response.json()["file"] == "steps.json"


def test_a_symlinked_or_irregular_snapshot_is_never_read(
  made: PagesTree,
) -> None:
  real = made.snapshot.with_name("real.json")
  _ = made.snapshot.rename(real)
  made.snapshot.symlink_to(real)
  web = client(made)

  linked = web.get(f"{API}/snapshot")
  made.snapshot.unlink()
  made.snapshot.mkdir()
  directory = web.get(f"{API}/snapshot")

  assert linked.status_code == 503
  assert "symlink" in linked.json()["reason"]
  assert directory.status_code == 503
  assert "not a regular file" in directory.json()["reason"]


@pytest.mark.parametrize(
  ("text", "reason"),
  [
    (b'{"version": 1, "steps": [', "the snapshot: "),
    (b'{"version": NaN}', "NaN is not JSON"),
    (b"\xff\xfe", "the snapshot: "),
    (b'{"version": 2}', "version: 2 is not 1"),
    (b"[" * 100_000 + b"]" * 100_000, "the snapshot: "),
    (
      (FIXTURES / "snapshot.json")
      .read_bytes()
      .replace(b'"value": 12,', b'"value": 1' + b"0" * 400 + b","),
      "steps[0].value: not a finite number",
    ),
    (
      (FIXTURES / "snapshot.json")
      .read_bytes()
      .replace(b'"page": "attempts"', b'"page": "steps"'),
      "steps[2].page: 'steps' is no other page",
    ),
  ],
)
def test_a_snapshot_that_is_not_json_of_the_shape_says_why(
  made: PagesTree, text: bytes, reason: str
) -> None:
  _ = made.snapshot.write_bytes(text)

  response = client(made).get(f"{API}/snapshot")

  assert response.status_code == 503
  assert reason in response.json()["reason"]


def test_a_page_that_is_no_snapshot_page_has_no_routes(
  made: PagesTree,
) -> None:
  web = client(made)

  for page in ("attempts", "nothing"):
    for route in ("snapshot", "files?root=bundles"):
      url = f"/api/projects/example/pages/{page}/{route}"
      assert web.get(url).status_code == 404, url


def test_a_valid_snapshot_passes_the_check() -> None:
  check(GOOD, {"bundles"}, {"attempts"})


def _with(path: str, value: object) -> dict[str, object]:
  # The good snapshot with the key at path, keys and indices split by "/",
  # set to value, or removed when value is _GONE.
  data = copy.deepcopy(GOOD)
  *parents, last = path.split("/")
  node: object = data
  for key in parents:
    node = _child(node, key)
  if isinstance(node, list):
    cast(list[object], node)[int(last)] = value
  elif value is _GONE:
    del cast(dict[str, object], node)[last]
  else:
    cast(dict[str, object], node)[last] = value
  return data


def _child(node: object, key: str) -> object:
  if isinstance(node, list):
    return cast(list[object], node)[int(key)]
  return cast(dict[str, object], node)[key]


_GONE = object()
_ROWS = "steps/1/entries/rows"
_COLUMNS = "steps/1/entries/columns"


@pytest.mark.parametrize(
  ("path", "value", "reason"),
  [
    ("version", True, r"version: not an integer"),
    ("generated_at", _GONE, r"generated_at: missing"),
    ("generated_at", "2026-03-03T11:15:00", r"generated_at: not an ISO"),
    ("generated_at", "2026-03-03T021050Z", r"generated_at: not an ISO"),
    ("generated_at", "2026-13-03T11:15:00Z", r"generated_at: not an ISO"),
    ("extra", 1, r"^extra: unknown key"),
    ("statuses/partial/tone", "orange", r"statuses\.partial\.tone: not one"),
    ("statuses/partial/label", "", r"statuses\.partial\.label: not a non"),
    ("steps", [], r"steps: empty"),
    ("steps/0/name", "Gather", r"steps\[0\]\.name: 'Gather' is not of"),
    ("steps/1/name", "gather", r"steps\[1\]\.name: 'gather' is repeated"),
    ("steps/0/value", "12", r"steps\[0\]\.value: not a finite number"),
    ("steps/0/value", float("inf"), r"steps\[0\]\.value: not a finite"),
    ("steps/0/of", 0, r"steps\[0\]\.of: not above 0"),
    ("steps/0/unit", None, r"steps\[0\]\.unit: not a non-empty string"),
    ("steps/0/page", "elsewhere", r"steps\[0\]\.page: 'elsewhere' is no"),
    ("steps/1/page", "attempts", r"steps\[1\]: both page and entries"),
    ("steps/1/entries/extra", 1, r"entries\.extra: unknown key"),
    (f"{_COLUMNS}/0/format", "colour", r"columns\[0\]\.format: not one of"),
    (f"{_COLUMNS}/1/filter", True, r"columns\[1\]\.filter: only for text"),
    (f"{_COLUMNS}/0/filter", "yes", r"columns\[0\]\.filter: not a boolean"),
    (f"{_COLUMNS}/1/name", "state", r"columns\[1\]\.name: 'state' is repeat"),
    (f"{_COLUMNS}/1/name", "Files", r"columns\[1\]\.name: 'Files' is not"),
    (f"{_ROWS}/1/id", "batch-1", r"rows\[1\]\.id: 'batch-1' is repeated"),
    (f"{_ROWS}/1/values/other", "x", r"rows\[1\]\.values\.other: no such"),
    (f"{_ROWS}/0/values/files", "2", r"values\.files: not a finite number"),
    (f"{_ROWS}/0/values/built", "today", r"values\.built: not an ISO"),
    (f"{_ROWS}/0/values/state", 1, r"values\.state: not a string"),
    (f"{_ROWS}/0/dir/root", "elsewhere", r"dir\.root: 'elsewhere' is not"),
    (f"{_ROWS}/0/dir/path", "../batch-2", r"dir\.path: not a relative path"),
    (f"{_ROWS}/0/dir/path", "/batch-1", r"dir\.path: not a relative path"),
    (f"{_ROWS}/0/dir/open", "a//b", r"dir\.open: not a relative path"),
    (f"{_ROWS}/0/dir/path", _GONE, r"dir\.path: missing"),
  ],
)
def test_a_snapshot_not_of_the_shape_names_its_first_wrong_key(
  path: str, value: object, reason: str
) -> None:
  with pytest.raises(SnapshotError, match=reason):
    check(_with(path, value), {"bundles"}, {"attempts"})


def test_the_check_loads_without_the_viewer_s_extra() -> None:
  code = (
    "import sys, dataforge.viz.snapshot;"
    + " sys.exit('fastapi' in sys.modules or 'uvicorn' in sys.modules)"
  )

  done = subprocess.run([sys.executable, "-c", code], check=False, timeout=60)

  assert done.returncode == 0


def test_a_root_s_files_are_listed_and_served_as_bytes(
  made: PagesTree,
) -> None:
  web = client(made)

  listing = web.get(f"{API}/files", params={"root": "bundles", "path": ""})
  folder = web.get(
    f"{API}/files", params={"root": "bundles", "path": "batch-1"}
  )
  file = web.get(
    f"{API}/files", params={"root": "bundles", "path": "batch-1/summary.md"}
  )

  assert listing.json() == {
    "entries": [{"name": "batch-1", "type": "dir", "size": None}]
  }
  assert folder.json() == {
    "entries": [{"name": "summary.md", "type": "file", "size": 10}]
  }
  assert file.headers["content-type"] == "application/octet-stream"
  assert file.content == b"# Batch 1\n"


@pytest.mark.parametrize(
  "path",
  [
    "../snapshot/steps.json",
    "batch-1/../../snapshot/steps.json",
    "/etc/hostname",
    "batch-1//summary.md",
    "batch-1/./summary.md",
  ],
)
def test_a_path_out_of_a_root_is_refused(made: PagesTree, path: str) -> None:
  response = client(made).get(
    f"{API}/files", params={"root": "bundles", "path": path}
  )

  assert response.status_code == 404
  assert "is not a path in the root bundles" in response.json()["reason"]


def test_a_symlink_under_a_root_is_listed_and_never_opened(
  made: PagesTree,
) -> None:
  outside = made.tree.root.parent.parent / "outside.txt"
  (made.bundles / "batch-1" / "leak.txt").symlink_to(outside)
  (made.bundles / "up").symlink_to(made.tree.root.parent.parent)
  web = client(made)
  query = {"root": "bundles"}

  listing = web.get(f"{API}/files", params=query | {"path": "batch-1"})
  for path in ("batch-1/leak.txt", "up", "up/outside.txt"):
    response = web.get(f"{API}/files", params=query | {"path": path})
    assert response.status_code == 404, path
    assert "a host file" not in response.text

  assert {
    "name": "leak.txt",
    "type": "symlink",
    "size": None,
  } in listing.json()["entries"]


def test_a_root_the_page_does_not_name_is_refused(made: PagesTree) -> None:
  web = client(made)

  unnamed = web.get(f"{API}/files", params={"root": "other", "path": ""})
  missing = web.get(f"{API}/files", params={"path": ""})

  assert unnamed.status_code == 404
  assert "has no root 'other'" in unnamed.json()["reason"]
  assert missing.status_code == 400


def test_a_root_that_is_not_there_is_refused(made: PagesTree) -> None:
  _ = made.bundles.rename(made.bundles.with_name("gone"))

  response = client(made).get(
    f"{API}/files", params={"root": "bundles", "path": ""}
  )

  assert response.status_code == 404
  reason = cast(dict[str, str], response.json())["reason"]
  assert reason.startswith("root bundles: ")
