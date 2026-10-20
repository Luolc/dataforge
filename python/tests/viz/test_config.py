from pathlib import Path

import pytest
import uvicorn

from dataforge.viz.config import ConfigError, load_site
from dataforge.viz.serve import serve
from tests.viz.sites import make_tree, PROJECT, Tree


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  return make_tree(tmp_path)


def test_a_valid_site_loads(tree: Tree) -> None:
  site = load_site(tree.site)

  assert (site.listen.host, site.listen.port) == ("127.0.0.1", 8800)
  [project] = site.projects
  assert project.id == "example"
  assert project.root == tree.root
  assert project.attempts == ("*", "*")
  assert project.thinking == "collapsed"
  assert [c.format for c in project.columns] == ["text", "usd", "text"]


def test_the_server_binds_only_the_configured_address(
  tree: Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  static = tmp_path / "dist"
  static.mkdir()
  _ = (static / "index.html").write_text("<!doctype html>")
  calls: list[dict[str, object]] = []

  def run(app: object, **kwargs: object) -> None:
    del app
    calls.append(kwargs)

  monkeypatch.setattr(uvicorn, "run", run)

  assert serve(tree.site, static) == 0
  [call] = calls
  assert (call["host"], call["port"]) == ("127.0.0.1", 8800)


@pytest.mark.parametrize(
  "host", ["0.0.0.0", "::", "localhost", "viewer.example.org", ""]
)
def test_a_wildcard_or_a_name_is_refused(tree: Tree, host: str) -> None:
  _ = tree.site.write_text(
    f"listen: {{host: '{host}', port: 8800}}\n"
    + "projects: [project/viz-project.yaml]\n"
  )

  with pytest.raises(ConfigError, match=r"listen\.host"):
    _ = load_site(tree.site)


@pytest.mark.parametrize(
  ("change", "key"),
  [
    (("id: example", "id: Example"), "id"),
    (('color: "#e5a54b"', "color: red"), "color"),
    (("record: record.json", "record: ../record.json"), "record"),
    (('attempts: "*/*"', 'attempts: "batch-*/*"'), "attempts"),
    (('attempts: "*/*"', 'attempts: "/*"'), "attempts"),
    (("root: attempts", "root: missing"), "root"),
    (("tone: good", "tone: green"), "statuses.passed.tone"),
    (("format: usd", "format: euro"), r"columns\[1\]\.format"),
    (("field: [status]", "field: status"), r"columns\[0\]\.field"),
    (("name: Example", "name: Example\nextra: 1"), "extra: unknown key"),
    (("name: Example", "name: Example\nname: Again"), "key name repeated"),
    (("name: Example", "name: 3"), "name"),
    (("statuses:", "session: {thinking: open}\nstatuses:"), "thinking"),
  ],
)
def test_a_project_file_that_breaks_a_rule_is_refused(
  tree: Tree, change: tuple[str, str], key: str
) -> None:
  old, new = change
  assert old in PROJECT
  _ = tree.project.write_text(PROJECT.replace(old, new, 1))

  with pytest.raises(ConfigError, match=key) as error:
    _ = load_site(tree.site)
  assert str(tree.project) in str(error.value)


def test_a_symlinked_root_is_refused(tree: Tree) -> None:
  real = tree.root.parent / "real"
  _ = tree.root.rename(real)
  tree.root.symlink_to(real)

  with pytest.raises(ConfigError, match="root"):
    _ = load_site(tree.site)


def test_a_repeated_project_id_is_refused(tree: Tree) -> None:
  _ = tree.site.write_text(
    "listen: {host: 127.0.0.1, port: 8800}\n"
    + "projects: [project/viz-project.yaml, project/viz-project.yaml]\n"
  )

  with pytest.raises(ConfigError, match="id example is repeated"):
    _ = load_site(tree.site)


def test_paths_are_relative_to_their_file(
  tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.chdir(tree.site.parent)

  [project] = load_site(Path("site.yaml")).projects

  assert project.root == tree.root
  assert project.root.is_absolute()
