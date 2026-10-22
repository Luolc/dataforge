from collections.abc import Callable, Iterable
from contextlib import closing
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import cast, NotRequired, TypedDict

import pytest

from dataforge import cli
from dataforge.curation.actions import ResourcePool
from dataforge.formats import (
  against_base,
  changes,
  check,
  Fact,
  Fingerprint,
  Format,
  installed,
  RegistryError,
  sqlite,
  typed_dict,
  write,
)
from dataforge.formats.builtin import FORMATS
from dataforge.formats.snapshot import dumps
from dataforge.queue import cancel, Config, count, jobs, submit
from dataforge.queue import store as queue_store
from dataforge.queue.proc import leader
from dataforge.queue.scheduler import Scheduler
from dataforge.runs import Store
from dataforge.statedir import ENV

SNAPSHOTS = Path(__file__).parent


@pytest.mark.parametrize("fmt", FORMATS, ids=[one.name for one in FORMATS])
def test_each_format_gives_its_snapshot(fmt: Format):
  assert check(fmt, SNAPSHOTS) == []


@pytest.mark.parametrize("fmt", FORMATS, ids=[one.name for one in FORMATS])
def test_each_snapshot_changed_only_additively_since_main(fmt: Format):
  found = against_base(fmt, SNAPSHOTS)
  if found is None:
    pytest.skip("git finds no origin/main to compare the snapshot with")
  assert found == []


def test_a_column_no_insert_writes_fails_the_queue_tables_snapshot(
  monkeypatch: pytest.MonkeyPatch,
):
  # The snapshot check catches a real change to the queue's schema.
  schema = queue_store._SCHEMA.replace(  # pyright: ignore[reportPrivateUsage]
    "  log TEXT NOT NULL\n", "  log TEXT NOT NULL,\n  owner TEXT NOT NULL\n"
  )
  monkeypatch.setattr(queue_store, "_SCHEMA", schema)
  [fmt] = [one for one in FORMATS if one.name == "dataforge.queue.table"]
  [problem] = check(fmt, SNAPSHOTS)
  assert "a change that is not additive" in problem
  assert "added table:jobs/column:owner: owner TEXT NOT NULL" in problem
  assert "raise its version" in problem


def _schema(tmp_path: Path, sql: str) -> Fingerprint:
  path = tmp_path / f"{len(list(tmp_path.iterdir()))}.sqlite"
  with closing(sqlite3.connect(path)) as conn:
    _ = conn.executescript(sql)
  return sqlite(path)


BASE = """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,  -- a comment, which is left out
  state TEXT NOT NULL CHECK (state IN ('queued', 'done'))
) STRICT;
"""


@pytest.mark.parametrize(
  ("more", "additive"),
  [
    ("ALTER TABLE jobs ADD COLUMN note TEXT", True),
    ("ALTER TABLE jobs ADD COLUMN n INTEGER NOT NULL DEFAULT 0", True),
    ("ALTER TABLE jobs ADD COLUMN n INTEGER NOT NULL", False),
    ("CREATE TABLE pauses (queue TEXT PRIMARY KEY, at REAL NOT NULL)", True),
    ("CREATE INDEX jobs_state ON jobs (state)", True),
    ("CREATE UNIQUE INDEX jobs_state ON jobs (state)", False),
    (
      "CREATE TRIGGER t AFTER INSERT ON jobs BEGIN SELECT 1; END",
      False,
    ),
  ],
)
def test_what_a_schema_adds_is_additive_when_old_writers_go_on(
  tmp_path: Path, more: str, additive: bool
):
  found = changes(_schema(tmp_path, BASE), _schema(tmp_path, f"{BASE}{more};"))
  assert found
  assert all(change.kind == "added" for change in found)
  assert all(change.additive for change in found) is additive


@pytest.mark.parametrize(
  "other",
  [
    BASE.replace("'done'", "'done', 'paused'"),
    BASE.replace(") STRICT", ")"),
    BASE.replace("state TEXT NOT NULL", "state TEXT"),
  ],
)
def test_a_changed_definition_is_not_additive(tmp_path: Path, other: str):
  found = changes(_schema(tmp_path, BASE), _schema(tmp_path, other))
  assert [change.kind for change in found] == ["changed"]
  assert not found[0].additive


def test_comments_and_spacing_are_no_change(tmp_path: Path):
  spaced = BASE.replace("  -- a comment, which is left out", "/* other */")
  assert changes(_schema(tmp_path, BASE), _schema(tmp_path, spaced)) == []


class _Inner(TypedDict):
  start: float


class _Before(TypedDict):
  inner: _Inner
  until: float


class _Optional(_Before):
  cause: NotRequired[str | None]


class _Required(_Before):
  cause: str | None


def test_a_typed_dicts_key_is_additive_only_when_not_required():
  before = typed_dict(_Before)
  assert before["field:inner/field:start"] == Fact("float")
  [optional] = changes(before, typed_dict(_Optional))
  [required] = changes(before, typed_dict(_Required))
  assert optional.additive and optional.after == Fact("str | None", True)
  assert not required.additive


def _format(
  name: str = "project.table",
  version: int = 1,
  facts: Fingerprint | None = None,
) -> Format:
  given = {"a": Fact("1")} if facts is None else facts
  return Format(name, version, lambda: dict(given), "a test's")


def registered() -> list[Format]:
  return [_format()]


def not_formats() -> list[str]:
  return ["project.table"]


def _points(
  *values: str,
) -> Callable[[], Iterable[importlib.metadata.EntryPoint]]:
  return lambda: [
    importlib.metadata.EntryPoint(f"p{i}", value, "dataforge.formats")
    for i, value in enumerate(values)
  ]


def test_a_project_registers_its_formats_through_an_entry_point():
  found = installed(FORMATS, _points(f"{__name__}:registered"))
  names = [one.name for one in found]
  assert "project.table" in names
  assert "dataforge.queue.table" in names
  assert names == sorted(names)


@pytest.mark.parametrize(
  ("value", "said"),
  [
    ("tests.formats.no_such_module:registered", "ModuleNotFoundError"),
    (f"{__name__}:not_formats", "not a Format"),
  ],
)
def test_an_entry_point_that_gives_no_formats_fails_the_registry(
  value: str, said: str
):
  with pytest.raises(RegistryError, match=said):
    _ = installed(FORMATS, _points(value))


@pytest.mark.parametrize(
  ("formats", "said"),
  [
    ([_format(), _format()], "registered twice"),
    ([_format(version=0)], "below 1"),
    ([_format(name="Table")], "not a dotted"),
  ],
)
def test_a_bad_or_doubled_format_fails_the_registry(
  formats: list[Format], said: str
):
  with pytest.raises(RegistryError, match=said):
    _ = installed(formats, _points())


def test_list_json_of_one_install_compares_equal_to_itself(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  assert cli.main(["formats", "list", "--json"]) == 0
  listed = cast(dict[str, dict[str, int]], json.loads(capsys.readouterr().out))
  assert listed["formats"]["dataforge.queue.table"] >= 1
  old, new = tmp_path / "old.json", tmp_path / "new.json"
  _ = old.write_text(json.dumps(listed))
  _ = new.write_text(json.dumps(listed))
  assert cli.main(["formats", "compare", str(old), str(new)]) == 0
  assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
  ("old", "new", "said"),
  [
    (
      {"formats": {"a.b": 1}},
      {"formats": {"a.b": 2}},
      "a.b: version 1 in the old install, 2 in the new",
    ),
    (
      {"formats": {"a.b": 1}},
      {"formats": {"a.b": 1, "c.d": 1}},
      "c.d: only in the new install, version 1",
    ),
    (
      {"formats": {"a.b": 1, "c.d": 1}},
      {"formats": {"a.b": 1}},
      "c.d: only in the old install, version 1",
    ),
    ("usage: dataforge [-h]", {"formats": {"a.b": 1}}, "old install"),
    ({"formats": {}}, {"formats": {"a.b": 1}}, "not what"),
    ({"formats": {"a.b": "1"}}, {"formats": {"a.b": 1}}, "not an integer"),
  ],
)
def test_compare_refuses_any_difference_or_a_list_it_cannot_read(
  tmp_path: Path,
  capsys: pytest.CaptureFixture[str],
  old: object,
  new: object,
  said: str,
):
  files: list[str] = []
  for name, data in (("old", old), ("new", new)):
    file = tmp_path / f"{name}.json"
    _ = file.write_text(data if isinstance(data, str) else json.dumps(data))
    files.append(str(file))
  assert cli.main(["formats", "compare", *files]) == 1
  assert said in capsys.readouterr().err


def test_compare_of_a_missing_list_refuses(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  new = tmp_path / "new.json"
  _ = new.write_text(json.dumps({"formats": {"a.b": 1}}))
  missing = str(tmp_path / "old.json")
  assert cli.main(["formats", "compare", missing, str(new)]) == 1
  assert "old install" in capsys.readouterr().err


def test_write_takes_an_additive_change_and_refuses_another_until_raised(
  tmp_path: Path,
):
  base = {"t": Fact("STRICT", additive=True), "t/c": Fact("c TEXT")}
  assert write(_format(facts=base), tmp_path) == []
  grown = {**base, "t/d": Fact("d TEXT", additive=True)}
  assert write(_format(facts=grown), tmp_path) == []
  assert check(_format(facts=grown), tmp_path) == []
  changed = {**grown, "t/c": Fact("c INTEGER")}
  [refused] = write(_format(facts=changed), tmp_path)
  assert "raise its version" in refused
  [stale] = check(_format(facts=changed), tmp_path)
  assert "a change that is not additive" in stale
  assert write(_format(version=2, facts=changed), tmp_path) == []
  assert check(_format(version=2, facts=changed), tmp_path) == []


def _git(repo: Path, *args: str) -> None:
  _ = subprocess.run(
    ["git", "-C", str(repo), *args], check=True, capture_output=True
  )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
  _git(tmp_path, "init", "-q", "-b", "main")
  _git(tmp_path, "config", "user.email", "test@example.com")
  _git(tmp_path, "config", "user.name", "test")
  assert write(_format(facts={"t/c": Fact("c TEXT")}), tmp_path) == []
  _git(tmp_path, "add", ".")
  _git(tmp_path, "commit", "-q", "-m", "base")
  _git(tmp_path, "switch", "-q", "-c", "change")
  return tmp_path


@pytest.mark.parametrize(
  ("version", "facts", "problem"),
  [
    (1, {"t/c": Fact("c TEXT"), "t/d": Fact("d", additive=True)}, None),
    (1, {"t/c": Fact("c INTEGER")}, "not additive"),
    (2, {"t/c": Fact("c INTEGER")}, None),
  ],
)
def test_against_base_takes_only_an_additive_change_at_one_version(
  repo: Path, version: int, facts: Fingerprint, problem: str | None
):
  # The snapshot is written by hand, as write would refuse to.
  fmt = _format(version=version, facts=facts)
  file = repo / "project.table.json"
  _ = file.write_text(dumps(fmt, facts))
  found = against_base(fmt, repo, base="main")
  if problem is None:
    assert found == []
  else:
    assert found is not None and len(found) == 1 and problem in found[0]


def test_against_base_refuses_a_version_that_goes_down(repo: Path):
  _git(repo, "switch", "-q", "main")
  fmt = _format(version=2)
  _ = (repo / "project.table.json").write_text(dumps(fmt, fmt.fingerprint()))
  _git(repo, "commit", "-q", "-am", "version 2")
  _git(repo, "switch", "-q", "-c", "down")
  fmt = _format(version=1)
  _ = (repo / "project.table.json").write_text(dumps(fmt, fmt.fingerprint()))
  [problem] = against_base(fmt, repo, base="main") or ["none"]
  assert "below the 2" in problem


def test_against_base_is_none_without_the_branch(repo: Path):
  assert against_base(_format(), repo, base="origin/main") is None


def test_a_format_whose_readers_fail_on_new_keys_takes_no_change_at_one_version(
  tmp_path: Path,
):
  base = {"field:a": Fact("float")}
  strict = Format("project.file", 1, lambda: dict(base), "a test's", False)
  assert write(strict, tmp_path) == []
  grown = {**base, "field:b": Fact("str", additive=True)}
  strict = Format("project.file", 1, lambda: dict(grown), "a test's", False)
  [refused] = write(strict, tmp_path)
  assert "readers fail on what they do not know" in refused
  [stale] = check(strict, tmp_path)
  assert "raise its version" in stale


class _Readings:
  def mem_available_mib(self) -> int:
    return 1 << 20

  def load1(self) -> float:
    return 0.0

  def rss_mib(self, session: int) -> int:
    _ = session
    return 0


def _grow(path: Path, sql: str) -> None:
  with closing(sqlite3.connect(path)) as conn:
    _ = conn.executescript(sql)


def test_the_queue_reads_and_writes_a_table_grown_additively(tmp_path: Path):
  # What an install of a later version adds at the same version: a column
  # that may be NULL, a table, an index.
  config = Config(
    queues={"q": 1},
    max_running=1,
    min_mem_available_mib=0,
    max_load1=1.0,
    database=tmp_path / "queue.sqlite",
    logs=tmp_path / "logs",
  )
  with queue_store.connect(config.database):
    pass
  _grow(
    config.database,
    """
    ALTER TABLE jobs ADD COLUMN note TEXT;
    CREATE TABLE pauses (queue TEXT PRIMARY KEY, at REAL NOT NULL) STRICT;
    CREATE INDEX jobs_note ON jobs (note);
    """,
  )
  job = submit(config, "q", ["true"], cwd=tmp_path, memory_mib=0)
  assert [one.id for one in jobs(config)] == [job.id]
  assert count(config, states=["queued"]) == 1
  assert cancel(config, job.id).state == "canceled"
  Scheduler(config, _Readings()).tick()
  assert [one.state for one in jobs(config)] == ["canceled"]


def test_the_store_of_attempts_reads_and_writes_a_schema_grown_additively(
  tmp_path: Path,
):
  store = Store(tmp_path / "runs.sqlite")
  store.create()
  _grow(
    store.path,
    """
    ALTER TABLE attempt ADD COLUMN note TEXT;
    ALTER TABLE event ADD COLUMN note TEXT;
    CREATE TABLE other (id TEXT PRIMARY KEY) STRICT;
    """,
  )
  holder = leader(os.getpid())
  assert holder is not None
  assert store.insert("item/1", "item", tmp_path, holder, None)
  [row] = store.rows()
  assert (row.id, row.state) == ("item/1", "running")
  assert [event.to_state for event in store.events("item/1")] == ["running"]


def test_the_state_directory_grown_by_new_directories_counts_as_before(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  # A new directory, at the top or in one of the leases', is not a lease.
  state = tmp_path / "state"
  for path in ("other", "memory/other", "builds/other"):
    (state / path).mkdir(parents=True)
  monkeypatch.setenv(ENV, str(state))
  pool = ResourcePool(0.5)
  with pool.build(0.25):
    assert pool.reserved_gb == 0.25
  assert pool.reserved_gb == 0
