from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import cast

import pytest

from dataforge.cli import main
from dataforge.share import app, site
from dataforge.share.site import SiteError, write_site
from tests.share.sites import export_json, write_export

ZIP = b"PK\x05\x06" + bytes(18)
"""The bytes of an empty zip archive, the synthetic download."""


@dataclass(frozen=True)
class Inputs:
  frontend: Path
  export: Path
  item: Path
  out: Path


def make_inputs(tmp_path: Path) -> Inputs:
  """Make a frontend build and an export of two items, one with a download.

  Args:
    tmp_path: The directory to make them in.

  Returns:
    Where they are, and the site's directory, not yet there.
  """
  frontend = tmp_path / "frontend"
  (frontend / "assets").mkdir(parents=True)
  _ = (frontend / "index.html").write_text("<!doctype html>\n")
  _ = (frontend / "assets" / "index-abc.js").write_text("export {};\n")
  export = tmp_path / "exports" / "example"
  item = export / "items" / "item-1"
  (item / "task" / "environment").mkdir(parents=True)
  (item / "trials" / "1").mkdir(parents=True)
  _ = (item / "task" / "instruction.md").write_text("# A task\n")
  _ = (item / "task" / "environment" / "source.tar.gz").write_bytes(
    b"\x1f\x8b synthetic"
  )
  _ = (item / "trials" / "1" / "trace.jsonl").write_text("{}\n")
  _ = (item / "review.md").write_text("Reviewed\n")
  _ = (item / "item-1-r1.zip").write_bytes(ZIP)
  record: dict[str, object] = {
    "id": "item-1",
    "revision": 1,
    "deliverables": [],
    "download": {
      "path": "item-1-r1.zip",
      "size": len(ZIP),
      "sha256": hashlib.sha256(ZIP).hexdigest(),
    },
  }
  _ = (item / "record.json").write_text(json.dumps(record))
  plain = export / "items" / "item-2"
  plain.mkdir()
  _ = (plain / "record.json").write_text('{"id": "item-2"}')
  content = export_json("example", ["item-1", "item-2"])
  content["listed_only"] = ["task/environment/source.tar.gz"]
  write_export(export, content)
  return Inputs(frontend, export, item, tmp_path / "site")


def files_under(root: Path) -> set[str]:
  return {str(p.relative_to(root)) for p in root.rglob("*") if not p.is_dir()}


GENERATED = {
  "_headers",
  "data/projects.json",
  "data/example/items.json",
  "data/example/items/item-1/tree.json",
  "data/example/items/item-2/tree.json",
}


def source_of(path: str, inputs: Inputs) -> Path:
  """Give the input that a copied file of the site is a copy of.

  Args:
    path: The file's path in the site.
    inputs: The inputs the site was written from.

  Returns:
    The input's path.
  """
  if not path.startswith("data/"):
    return inputs.frontend / path
  _, _, _, item, *rest = path.split("/")
  if rest[0] == "files":
    rest = rest[1:]
  return inputs.export / "items" / item / "/".join(rest)


def test_the_site_has_its_layout(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)

  count = write_site(inputs.frontend, inputs.out, [inputs.export])

  base = "data/example/items"
  assert files_under(inputs.out) == GENERATED | {
    "index.html",
    "assets/index-abc.js",
    f"{base}/item-1/record.json",
    f"{base}/item-1/item-1-r1.zip",
    f"{base}/item-1/files/record.json",
    f"{base}/item-1/files/review.md",
    f"{base}/item-1/files/task/instruction.md",
    f"{base}/item-1/files/trials/1/trace.jsonl",
    f"{base}/item-2/record.json",
    f"{base}/item-2/files/record.json",
  }
  assert count == len(files_under(inputs.out))
  assert json.loads((inputs.out / "data/projects.json").read_text()) == [
    {
      "id": "example",
      "name": "Example",
      "color": "#3a7bd5",
      "session": {"thinking": "collapsed"},
      "statuses": {"pass": {"label": "Pass", "tone": "good"}},
    }
  ]
  assert json.loads((inputs.out / "data/example/items.json").read_text()) == [
    {"id": "item-1", "revision": 1},
    {"id": "item-2", "revision": 1},
  ]


def test_tree_json_lists_every_directory_and_marks_what_is_only_listed(
  tmp_path: Path,
) -> None:
  inputs = make_inputs(tmp_path)

  _ = write_site(inputs.frontend, inputs.out, [inputs.export])

  tree = cast(
    dict[str, object],
    json.loads(
      (inputs.out / "data/example/items/item-1/tree.json").read_text()
    ),
  )
  record_size = (inputs.item / "record.json").stat().st_size
  assert sorted(tree) == [
    "",
    "task",
    "task/environment",
    "trials",
    "trials/1",
  ]
  assert tree[""] == [
    {"name": "item-1-r1.zip", "type": "file", "size": 22, "listed_only": True},
    {"name": "record.json", "type": "file", "size": record_size},
    {"name": "review.md", "type": "file", "size": 9},
    {"name": "task", "type": "dir", "size": None},
    {"name": "trials", "type": "dir", "size": None},
  ]
  assert tree["task/environment"] == [
    {
      "name": "source.tar.gz",
      "type": "file",
      "size": 12,
      "listed_only": True,
    }
  ]


def test_a_file_only_listed_is_in_no_file_of_the_site_but_tree_json(
  tmp_path: Path,
) -> None:
  inputs = make_inputs(tmp_path)
  secret = b"\x1f\x8b synthetic"

  _ = write_site(inputs.frontend, inputs.out, [inputs.export])

  holding = [
    path
    for path in files_under(inputs.out)
    if secret in (inputs.out / path).read_bytes()
  ]
  assert holding == []
  assert not any("source.tar.gz" in p for p in files_under(inputs.out))


def test_every_file_but_the_generated_is_a_copy_of_an_input(
  tmp_path: Path,
) -> None:
  inputs = make_inputs(tmp_path)

  _ = write_site(inputs.frontend, inputs.out, [inputs.export])

  copied = files_under(inputs.out) - GENERATED
  assert len(copied) == 10
  for path in copied:
    assert (inputs.out / path).read_bytes() == source_of(
      path, inputs
    ).read_bytes(), path


def test_the_headers_keep_the_site_out_of_search_and_data_out_of_caches(
  tmp_path: Path,
) -> None:
  inputs = make_inputs(tmp_path)

  _ = write_site(inputs.frontend, inputs.out, [inputs.export])

  headers = (inputs.out / "_headers").read_text()
  assert "  X-Robots-Tag: noindex\n" in headers
  assert "/data/*\n  Cache-Control: no-cache\n" in headers
  assert f"  Content-Security-Policy: {app.CSP}\n" in headers


def test_an_export_without_a_download_or_listed_only_is_written_whole(
  tmp_path: Path,
) -> None:
  inputs = make_inputs(tmp_path)
  record = cast(
    dict[str, dict[str, object]],
    json.loads((inputs.item / "record.json").read_text()),
  )
  del record["download"]
  _ = (inputs.item / "record.json").write_text(json.dumps(record))
  write_export(inputs.export, export_json("example", ["item-1", "item-2"]))

  _ = write_site(inputs.frontend, inputs.out, [inputs.export])

  files = inputs.out / "data/example/items/item-1/files"
  assert (files / "task/environment/source.tar.gz").exists()
  assert (files / "item-1-r1.zip").exists()
  tree = (inputs.out / "data/example/items/item-1/tree.json").read_text()
  assert "listed_only" not in tree


def assert_refused(inputs: Inputs, words: str) -> None:
  with pytest.raises(SiteError, match=words):
    _ = write_site(inputs.frontend, inputs.out, [inputs.export])
  # Nothing is left: no site, and no directory it was written in.
  assert sorted(p.name for p in inputs.out.parent.iterdir()) == [
    "exports",
    "frontend",
  ]


def test_a_file_of_25_mib_is_written_and_one_byte_more_is_refused(
  tmp_path: Path,
) -> None:
  fits = make_inputs(tmp_path / "fits")
  with (fits.item / "trials" / "1" / "big.bin").open("wb") as file:
    _ = file.truncate(25 * 1024 * 1024)
  _ = write_site(fits.frontend, fits.out, [fits.export])
  big = fits.out / "data/example/items/item-1/files/trials/1/big.bin"
  assert big.stat().st_size == 25 * 1024 * 1024
  inputs = make_inputs(tmp_path / "over")
  with (inputs.item / "trials" / "1" / "big.bin").open("wb") as file:
    _ = file.truncate(25 * 1024 * 1024 + 1)

  assert_refused(inputs, r"trials/1/big.bin: 26214401 bytes, over 26214400")


def test_a_file_only_listed_may_be_over_25_mib(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)
  source = inputs.item / "task" / "environment" / "source.tar.gz"
  with source.open("wb") as file:
    _ = file.truncate(25 * 1024 * 1024 + 1)

  _ = write_site(inputs.frontend, inputs.out, [inputs.export])

  assert inputs.out.is_dir()


def test_a_site_of_more_files_than_the_limit_is_refused(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  inputs = make_inputs(tmp_path / "fits")
  monkeypatch.setattr(site, "FILES_LIMIT", 15)
  _ = write_site(inputs.frontend, inputs.out, [inputs.export])
  assert len(files_under(inputs.out)) == 15
  inputs = make_inputs(tmp_path / "over")
  monkeypatch.setattr(site, "FILES_LIMIT", 14)

  assert_refused(inputs, "more than the 14 files a site may hold")


def test_the_limits_are_those_of_cloudflare_pages() -> None:
  assert (site.FILE_LIMIT, site.FILES_LIMIT) == (26_214_400, 20_000)


@pytest.mark.parametrize(
  ("link", "words"),
  [
    ("task/notes.md", "item-1/task/notes.md: a symlink"),
    ("task/dir", "item-1/task/dir: a symlink"),
  ],
)
def test_a_symlink_in_an_item_is_refused(
  tmp_path: Path, link: str, words: str
) -> None:
  inputs = make_inputs(tmp_path)
  (inputs.item / link).symlink_to(tmp_path / "exports")

  assert_refused(inputs, words)


def test_an_item_that_is_a_symlink_is_refused(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)
  plain = inputs.export / "items" / "item-2"
  os.rename(plain, inputs.export / "item-2")
  plain.symlink_to(inputs.export / "item-2")

  assert_refused(inputs, "no such item")


def test_a_fifo_in_an_item_is_refused(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)
  os.mkfifo(inputs.item / "task" / "pipe")

  assert_refused(inputs, "item-1/task/pipe: a symlink or not a regular file")


def test_a_symlink_in_the_frontend_is_refused(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)
  (inputs.frontend / "assets" / "x.js").symlink_to(inputs.item / "review.md")

  assert_refused(inputs, "assets/x.js: a symlink")


@pytest.mark.parametrize("name", ["404.html", "_redirects", "data"])
def test_a_frontend_with_a_name_the_site_keeps_is_refused(
  tmp_path: Path, name: str
) -> None:
  inputs = make_inputs(tmp_path)
  _ = (inputs.frontend / name).write_text("x\n")

  assert_refused(inputs, f"has {name} at its top")


@pytest.mark.parametrize(
  ("change", "words"),
  [
    ({"size": 23}, "22 bytes, not the 23 record.json gives"),
    ({"sha256": "0" * 64}, "not the SHA-256 record.json gives"),
    ({"path": "../item-2/record.json"}, "download.path: not a plain name"),
    ({"path": "tree.json"}, "download.path: not a plain name"),
    ({"path": "gone.zip"}, "gone.zip: No such file"),
  ],
)
def test_a_download_that_is_not_what_record_json_says_is_refused(
  tmp_path: Path, change: dict[str, object], words: str
) -> None:
  inputs = make_inputs(tmp_path)
  record = cast(
    dict[str, dict[str, object]],
    json.loads((inputs.item / "record.json").read_text()),
  )
  record["download"] |= change
  _ = (inputs.item / "record.json").write_text(json.dumps(record))

  assert_refused(inputs, words)


@pytest.mark.parametrize(
  ("listed", "words"),
  [
    (
      ["../item-2/record.json"],
      "listed_only.0.: '../item-2/record.json' is not",
    ),
    (["task"], "item-1/task: listed_only names a directory"),
  ],
)
def test_a_listed_only_that_is_no_file_of_an_item_is_refused(
  tmp_path: Path, listed: list[str], words: str
) -> None:
  inputs = make_inputs(tmp_path)
  content = export_json("example", ["item-1", "item-2"])
  content["listed_only"] = listed
  write_export(inputs.export, content)

  assert_refused(inputs, words)


def test_a_project_given_twice_is_refused(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)

  with pytest.raises(SiteError, match="project example is given twice"):
    _ = write_site(inputs.frontend, inputs.out, [inputs.export] * 2)
  assert not inputs.out.exists()


def test_a_site_that_is_there_is_not_written_over(tmp_path: Path) -> None:
  inputs = make_inputs(tmp_path)
  inputs.out.mkdir()

  with pytest.raises(SiteError, match="already there"):
    _ = write_site(inputs.frontend, inputs.out, [inputs.export])
  assert list(inputs.out.iterdir()) == []


def test_the_command_writes_the_site_or_says_why_not(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  inputs = make_inputs(tmp_path)
  args = ["share", "site", "--frontend", str(inputs.frontend)]
  args += ["--out", str(inputs.out), str(inputs.export)]

  assert main(args) == 0
  assert main(args) == 1
  assert "already there" in capsys.readouterr().err
