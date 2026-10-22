from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn

from dataforge.viz.config import ConfigError, load_site
from dataforge.viz.serve import serve
from tests.viz.sites import (
  make_queue_tree,
  make_tree,
  PARAMS,
  PROJECT,
  QUEUE,
  Tree,
  with_params,
)


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
    (("name: Example", "name: Example\nresumable: []"), "resumable"),
    (
      ("name: Example", "name: Example\nresumable: [canceled]"),
      "resumable.*'canceled'",
    ),
    (
      (
        "name: Example",
        "name: Example\nresumable: [passed]\nnot_ended: [passed]",
      ),
      "resumable.*not_ended",
    ),
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
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: t, kind: k, title: T, "
        + "default: 1}]",
      ),
      r"deliverables\[0\]\.default: not true or false",
    ),
    (
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: t, kind: k, title: T, "
        + "default: true}, {name: u, kind: k, title: U, default: true}]",
      ),
      r"deliverables\[1\]\.default: another deliverable of kind 'k'",
    ),
    (
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: t, kind: k, title: T}]\n"
        + "kinds: {report: {title: Reviews}}",
      ),
      r"kinds\.report: no deliverable of this kind",
    ),
    (
      (
        "name: Example",
        "name: Example\ndeliverables: [{name: t, kind: k, title: T}]\n"
        + "kinds: {k: {label: K}}",
      ),
      r"kinds\.k\.label: unknown key",
    ),
    (
      ("name: Example", "name: Example\ntrials: {diff: ../x.diff}"),
      r"trials\.diff: '\.\./x\.diff' is not",
    ),
    (
      ("name: Example", "name: Example\ntrials: {diff: /x.diff}"),
      r"trials\.diff: '/x\.diff' is not",
    ),
    (
      ("name: Example", "name: Example\ntrials: {}"),
      "trials: names neither diff nor tests",
    ),
    (
      ("name: Example", "name: Example\ntrials: {tests: ../ctrf.json}"),
      r"trials\.tests: '\.\./ctrf\.json' is not",
    ),
    (
      (
        "name: Example",
        "name: Example\ntrials: {diff: x, kinds: {a: {label: A}}}",
      ),
      r"trials\.kinds: without trials\.tests",
    ),
    (
      (
        "name: Example",
        "name: Example\ntrials: {tests: x, kinds: {a: {title: A}}}",
      ),
      r"trials\.kinds\.a\.title: unknown key",
    ),
    (
      ("name: Example", "name: Example\ntrials: {diff: x, glob: y}"),
      r"trials\.glob: unknown key",
    ),
    (
      ("name: Example", "name: Example\ndefinition: ../definition.json"),
      r"definition: '\.\./definition\.json' is not",
    ),
    (
      ("name: Example", "name: Example\ndefinition: /definition.json"),
      r"definition: '/definition\.json' is not",
    ),
    (
      ("name: Example", "name: Example\ndefinition: [d.json]"),
      "definition: not a non-empty string",
    ),
    (
      ("name: Example", "name: Example\ntask: ../task.toml"),
      r"task: '\.\./task\.toml' is not",
    ),
    (
      ("name: Example", "name: Example\ntask: [task.toml]"),
      "task: not a non-empty string",
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


def test_a_site_with_a_queue_loads_its_actions(tmp_path: Path) -> None:
  made = make_queue_tree(tmp_path)

  site = load_site(made.tree.site)

  assert site.queue is not None
  assert site.queue.queues == {"fast": 2, "slow": 1}
  [project] = site.projects
  again, go_on = project.actions
  assert again.argv == ("run-item", "--item", "{item}", "--note=from {item}")
  assert (again.queue, again.cwd, again.memory_mib) == ("fast", made.work, 2048)
  assert (again.key, again.when, go_on.when) == (
    "example:{item}",
    "idle",
    "stopped",
  )
  assert project.public()["actions"] == [
    {"name": "again", "label": "Run again", "queue": "fast"},
    {"name": "go-on", "label": "Go on", "queue": "slow"},
  ]


@pytest.mark.parametrize(
  ("old", "new", "key"),
  [
    ("name: again", "name: Again", r"actions\[0\]\.name: 'Again' is not"),
    ("name: go-on", "name: again", r"actions\[1\]\.name: 'again' repeated"),
    ("label: Go on", "label: ''", r"actions\[1\]\.label: not a non-empty"),
    (
      "    when: idle\n",
      "    when: idle\n    shell: true\n",
      r"shell: unknown",
    ),
    ("    when: idle\n", "", None),
    ("argv: [run-item, --go-on", 'argv: ["{item}", --go-on', "the command is"),
    ('"{item}"]', '"{other}"]', r"actions\[1\]\.argv: .* brace"),
    ('"{item}"]', '"{item}}"]', r"actions\[1\]\.argv: .* brace"),
    ('argv: [run-item, --go-on, "{item}"]', "argv: []", "non-empty list"),
    ("argv: [run-item, --go-on", "argv: [run-item, 3", r"argv: not a non-"),
    (
      'key: "example:{item}"\n    when: s',
      'key: "example"\n    when: s',
      "holds no",
    ),
    ("memory_mib: 1024", "memory_mib: 0", r"memory_mib: 0 is not from 1"),
    ("memory_mib: 1024", "memory_mib: true", r"memory_mib: not an integer"),
    ("when: stopped", "when: always", r"when: 'always' is not one of"),
    ("lock: .lock\n", "", r"when: idle needs the project's lock"),
    ('pattern: "batch-[0-9]+"', 'pattern: "batch-["', r"item\.pattern: "),
    (', pattern: "batch-[0-9]+"', "", r"item\.pattern: missing"),
  ],
)
def test_an_action_that_breaks_a_rule_is_refused(
  tmp_path: Path, old: str, new: str, key: str | None
) -> None:
  made = make_queue_tree(tmp_path)
  text = made.tree.project.read_text()
  assert old in text
  _ = made.tree.project.write_text(text.replace(old, new, 1))

  if key is None:
    # A control: the change alone, an action with no when, loads.
    _ = load_site(made.tree.site)
    return
  with pytest.raises(ConfigError, match=key) as error:
    _ = load_site(made.tree.site)
  assert str(made.tree.project) in str(error.value)


def test_an_empty_list_of_actions_is_refused(tmp_path: Path) -> None:
  made = make_queue_tree(tmp_path)
  text = made.tree.project.read_text()
  _ = made.tree.project.write_text(
    text[: text.index("actions:")] + "actions: []\n"
  )

  with pytest.raises(ConfigError, match=r"actions: not a non-empty list"):
    _ = load_site(made.tree.site)


@pytest.mark.parametrize(
  "cwd", ["work", "{tmp}/missing", "{tmp}/queue/queue.yaml"]
)
def test_an_action_runs_only_in_an_absolute_directory(
  tmp_path: Path, cwd: str
) -> None:
  made = make_queue_tree(tmp_path)
  text = made.tree.project.read_text()
  _ = made.tree.project.write_text(
    text.replace(str(made.work), cwd.replace("{tmp}", str(tmp_path)))
  )

  with pytest.raises(ConfigError, match=r"cwd: .* is not an absolute dir"):
    _ = load_site(made.tree.site)


@pytest.mark.parametrize(
  ("change", "key"),
  [
    (("queue: {config: queue/queue.yaml}\n", ""), "queue: missing, and"),
    (("queue/queue.yaml", "queue/none.yaml"), r"queue\.config: "),
    (("queue: {config: q", "queue: {config: q, db: x, config2: q"), "unknown"),
  ],
)
def test_a_site_whose_queue_does_not_fit_its_actions_is_refused(
  tmp_path: Path, change: tuple[str, str], key: str
) -> None:
  made = make_queue_tree(tmp_path)
  old, new = change
  text = made.tree.site.read_text()
  assert old in text
  _ = made.tree.site.write_text(text.replace(old, new, 1))

  with pytest.raises(ConfigError, match=key) as error:
    _ = load_site(made.tree.site)
  assert str(made.tree.site) in str(error.value)


def test_an_action_s_queue_must_be_in_the_queue_file(tmp_path: Path) -> None:
  made = make_queue_tree(tmp_path)
  _ = (made.queue / "queue.yaml").write_text(
    QUEUE.replace("{fast: 2, slow: 1}", "{fast: 2}")
  )

  with pytest.raises(ConfigError, match=r"actions\[1\]\.queue: 'slow' is"):
    _ = load_site(made.tree.site)


@pytest.mark.parametrize("id_", ["queue", "monitor"])
def test_a_project_may_not_take_a_page_s_name(tree: Tree, id_: str) -> None:
  _ = tree.project.write_text(PROJECT.replace("id: example", f"id: {id_}"))

  with pytest.raises(ConfigError, match="is the name of a page"):
    _ = load_site(tree.site)


def test_an_action_loads_its_parameters(tmp_path: Path) -> None:
  made = make_queue_tree(tmp_path)
  with_params(made)

  [project] = load_site(made.tree.site).projects
  budget, tries = project.actions[0].params

  assert (budget.name, budget.label, budget.flag) == (
    "budget",
    "Budget",
    "--max-usd",
  )
  assert (budget.min, budget.max, budget.default) == (1, 150, 75)
  assert (budget.format, tries.format) == ("usd", "number")
  assert (tries.min, tries.max, tries.default) == (-2, Decimal("0.5"), 0)
  assert project.actions[1].params == ()
  # What every page reads names no parameter: only the item's actions do.
  assert "params" not in str(project.public())


BUDGET = (
  "{name: budget, label: Budget, flag: --max-usd, min: 1, max: 150,"
  + " default: 75}"
)


@pytest.mark.parametrize(
  ("old", "new", "key"),
  [
    ("name: budget", "name: Budget", r"params\[0\]\.name: 'Budget' is not"),
    ("name: budget", "name: item", r"params\[0\]\.name: 'item' is not"),
    ("name: tries", "name: budget", r"params\[1\]\.name: 'budget' repeated"),
    ("label: Tries", "label: ''", r"params\[1\]\.label: not a non-empty"),
    ("flag: --max-usd", "flag: -m", r"params\[0\]\.flag: '-m' is not"),
    ("flag: --max-usd", "flag: max-usd", r"flag: 'max-usd' is not"),
    ("flag: --max-usd", "flag: --max usd", r"flag: '--max usd' is not"),
    ("flag: --max-usd", "flag: --Max", r"flag: '--Max' is not"),
    ("flag: --max-usd", "flag: --max={item}", r"flag: '--max={item}' is not"),
    ("flag: --tries", "flag: --max-usd", r"params\[1\]\.flag: .* already in"),
    ("flag: --max-usd", "flag: --item", r"params\[0\]\.flag: .* already in"),
    ("flag: --max-usd", "flag: --note", r"params\[0\]\.flag: .* already in"),
    ("default: 75", "default: 151", r"params\[0\]: not min <= default <="),
    ("default: 75", "default: 0.5", r"params\[0\]: not min <= default <="),
    ("max: 150", "max: 0", r"params\[0\]: not min <= default <= max"),
    ("min: 1", 'min: "1"', r"params\[0\]\.min: not a number"),
    ("min: 1", "min: true", r"params\[0\]\.min: not a number"),
    ("max: 150", "max: .inf", r"params\[0\]\.max: inf is not a decimal"),
    ("max: 150", "max: .nan", r"params\[0\]\.max: nan is not a decimal"),
    ("max: 150", "max: 1.0e+30", r"params\[0\]\.max: .* at most 15"),
    (
      "default: 75",
      "default: 123456789012345678901",
      r"default: .* at most 15",
    ),
    ("min: 1", "min: 0.0000001", r"params\[0\]\.min: .* 6 decimal places"),
    ("format: usd", "format: eur", r"format: 'eur' is not one of"),
    ("        format: usd\n", "        shell: true\n", r"shell: unknown"),
    ("        default: 75\n", "", r"params\[0\]\.default: missing"),
    ("        format: usd\n", "", None),
  ],
)
def test_a_parameter_that_breaks_a_rule_is_refused(
  tmp_path: Path, old: str, new: str, key: str | None
) -> None:
  made = make_queue_tree(tmp_path)
  assert old in PARAMS
  with_params(made, PARAMS.replace(old, new, 1))

  if key is None:
    # A control: the change alone, a parameter with no format, loads.
    _ = load_site(made.tree.site)
    return
  with pytest.raises(ConfigError, match=key) as error:
    _ = load_site(made.tree.site)
  assert str(made.tree.project) in str(error.value)


@pytest.mark.parametrize(
  ("params", "key"),
  [
    ("    params: []\n", r"actions\[0\]\.params: not a non-empty list"),
    (f"    params: {BUDGET}\n", r"actions\[0\]\.params: not a non-empty list"),
    (f"    params: [{BUDGET}]\n", None),
  ],
)
def test_parameters_are_a_non_empty_list(
  tmp_path: Path, params: str, key: str | None
) -> None:
  made = make_queue_tree(tmp_path)
  with_params(made, params)

  if key is None:
    _ = load_site(made.tree.site)
    return
  with pytest.raises(ConfigError, match=key):
    _ = load_site(made.tree.site)
