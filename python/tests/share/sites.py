"""A synthetic site of two exports for the tests of share."""

from dataclasses import dataclass
from datetime import datetime, UTC
import json
from pathlib import Path

from dataforge.share import tokens

NOW = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
"""The time the tests' clock gives."""

RECORD = {
  "id": "item-1",
  "revision": 2,
  "deliverables": [
    {"name": "task", "kind": "files", "title": "Task", "path": "task"},
    {"name": "notes", "kind": "report", "title": "Notes", "path": "notes.md"},
  ],
}


def export_json(project: str, items: list[str]) -> dict[str, object]:
  """Make the content of an export.json.

  Args:
    project: The project's ID.
    items: The IDs of its items, each at revision 1.

  Returns:
    The content, as JSON data.
  """
  return {
    "format": 1,
    "project": {"id": project, "name": project.title(), "color": "#3a7bd5"},
    "session": {"thinking": "collapsed"},
    "statuses": {"pass": {"label": "Pass", "tone": "good"}},
    "items": [{"id": i, "revision": 1} for i in items],
  }


@dataclass(frozen=True)
class Tree:
  site: Path
  tokens: Path
  export: Path
  item: Path
  outside: Path


def make_tree(tmp_path: Path, port: int = 8810) -> Tree:
  """Make a site of two exports, example and other, and a file outside.

  Args:
    tmp_path: The directory to make it in.
    port: The port of the site's listen.

  Returns:
    Where its files are; the token database has no token yet.
  """
  outside = tmp_path / "outside.txt"
  _ = outside.write_text("a host file\n")
  export = tmp_path / "exports" / "example"
  item = export / "items" / "item-1"
  (item / "task").mkdir(parents=True)
  _ = (item / "record.json").write_text(json.dumps(RECORD))
  _ = (item / "task" / "README.md").write_text("# A task\n")
  _ = (item / "notes.md").write_text("Notes\n")
  (export / "items" / "item-2").mkdir()
  write_export(export, export_json("example", ["item-1", "item-2"]))
  other = tmp_path / "exports" / "other"
  (other / "items" / "item-9").mkdir(parents=True)
  write_export(other, export_json("other", ["item-9"]))
  db = tmp_path / "tokens.sqlite"
  _, _ = tokens.create(db, ["unused"], "setup", now=NOW)
  site = tmp_path / "site.yaml"
  _ = site.write_text(
    f"listen: {{host: 127.0.0.1, port: {port}}}\n"
    + "tokens: tokens.sqlite\n"
    + "projects: [exports/example, exports/other]\n"
  )
  return Tree(site, db, export, item, outside)


def write_export(directory: Path, content: dict[str, object]) -> None:
  """Write an export directory's export.json.

  Args:
    directory: The export directory.
    content: What export.json holds.
  """
  _ = (directory / "export.json").write_text(json.dumps(content))
