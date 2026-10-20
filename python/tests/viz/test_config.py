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
  assert project.attempts_dir == tree.root
  assert project.attempts == ("*", "*")
  assert project.thinking == "hidden"
  assert [c.format for c in project.columns] == ["text", "usd", "text"]


def test_a_session_view_shows_thinking_as_the_project_file_says(
  tree: Tree,
) -> None:
  _ = tree.project.write_text(
    PROJECT.replace("statuses:", "session: {thinking: collapsed}\nstatuses:", 1)
  )

  [project] = load_site(tree.site).projects

  assert project.thinking == "collapsed"


def test_a_session_with_no_thinking_shows_it_hidden(tree: Tree) -> None:
  _ = tree.project.write_text(
    PROJECT.replace("statuses:", "session: {}\nstatuses:", 1)
  )

  [project] = load_site(tree.site).projects

  assert project.thinking == "hidden"


def test_the_monitor_database_is_relative_to_attempts_dir(tree: Tree) -> None:
  _ = tree.project.write_text(
    PROJECT + "monitor: {db: ../monitor/monitor.sqlite}\n"
  )

  [project] = load_site(tree.site).projects

  # Not checked at start: the sampler may not have written it yet.
  assert project.monitor == tree.root.parent / "monitor" / "monitor.sqlite"
  assert project.monitor_shown == "../monitor/monitor.sqlite"
  assert project.public()["monitor"] is True
  assert "monitor.sqlite" not in str(project.public())


def test_the_monitor_database_does_not_follow_the_project_file(
  tree: Tree, tmp_path: Path
) -> None:
  # A deployment copies the project file elsewhere to pin it; its relative
  # monitor.db still names the database beside attempts_dir.
  pinned = tmp_path / "pinned" / "deep" / "viz-project-0123abcd.yaml"
  pinned.parent.mkdir(parents=True)
  _ = pinned.write_text(
    PROJECT.replace("attempts_dir: attempts", f"attempts_dir: {tree.root}")
    + "monitor: {db: ../monitor/monitor.sqlite}\n"
  )
  _ = tree.site.write_text(
    f"listen: {{host: 127.0.0.1, port: 8800}}\nprojects: [{pinned}]\n"
  )

  [project] = load_site(tree.site).projects

  assert project.monitor == tree.root.parent / "monitor" / "monitor.sqlite"


def test_an_absolute_monitor_database_is_shown_by_its_name_alone(
  tree: Tree, tmp_path: Path
) -> None:
  db = tmp_path / "srv" / "monitor.sqlite"
  _ = tree.project.write_text(PROJECT + f"monitor: {{db: {db}}}\n")

  [project] = load_site(tree.site).projects

  assert project.monitor == db
  assert project.monitor_shown == "monitor.sqlite"


def test_a_project_with_no_monitor_says_so(tree: Tree) -> None:
  [project] = load_site(tree.site).projects

  assert project.monitor is None
  assert project.public()["monitor"] is False


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
    (("attempts_dir: attempts", "attempts_dir: missing"), "attempts_dir"),
    (("depth: 1", "depth: 3"), r"item\.depth"),
    (("depth: 1", "depth: 0"), r"item\.depth"),
    (("item: {depth: 1, title: Batch}\n", ""), "item: missing"),
    (("tone: good", "tone: green"), "statuses.passed.tone"),
    (("format: usd", "format: euro"), r"columns\[1\]\.format"),
    (("field: [status]", "field: status"), r"columns\[0\]\.field"),
    (("name: Example", "name: Example\nextra: 1"), "extra: unknown key"),
    (("name: Example", "name: Example\nname: Again"), "key name repeated"),
    (("name: Example", "name: 3"), "name"),
    (("statuses:", "session: {thinking: open}\nstatuses:"), "thinking"),
    (("name: Example", "name: Example\nlock: ../.lock"), "lock"),
    (("name: Example", "name: Example\nnot_ended: running"), "not_ended"),
    (("name: Example", "name: Example\nnot_ended: []"), "not_ended"),
    (("name: Example", "name: Example\ndeliverables: task"), "deliverables"),
    (
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: task, kind: files}]",
      ),
      r"deliverables\[0\]\.title: missing",
    ),
    (
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: t, kind: k, title: T, path: x}]",
      ),
      r"deliverables\[0\]\.path: unknown key",
    ),
    (
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: t, kind: k, title: T}, "
        + "{name: t, kind: k, title: U}]",
      ),
      r"deliverables\[1\]\.name: 't' repeated",
    ),
    (
      ("name: Example", "name: Example\ntrials: {diff: ../x.diff}"),
      r"trials\.diff: '\.\./x\.diff' is not",
    ),
    (
      ("name: Example", "name: Example\ntrials: {diff: /x.diff}"),
      r"trials\.diff: '/x\.diff' is not",
    ),
    (("name: Example", "name: Example\ntrials: {}"), r"trials\.diff: missing"),
    (
      ("name: Example", "name: Example\ntrials: {diff: x, glob: y}"),
      r"trials\.glob: unknown key",
    ),
    (("name: Example", "name: Example\nmonitor: {}"), r"monitor\.db: missing"),
    (("name: Example", "name: Example\nmonitor: {db: ''}"), r"monitor\.db"),
    (
      ("name: Example", "name: Example\nmonitor: {db: m.sqlite, wal: x}"),
      r"monitor\.wal: unknown key",
    ),
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


def test_attempts_dir_is_opened_as_given(tree: Tree) -> None:
  real = tree.root.parent / "real"
  _ = tree.root.rename(real)
  tree.root.symlink_to(real)

  [project] = load_site(tree.site).projects

  assert project.attempts_dir == tree.root


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

  assert project.attempts_dir == tree.root
  assert project.attempts_dir.is_absolute()
