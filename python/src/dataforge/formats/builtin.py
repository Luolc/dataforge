"""dataforge's own formats, each with its version, declared here only.

These are what a job of one install shares with the scheduler and the jobs
of another on one machine, on one queue database and one state directory:
the queue's table, how the scheduler starts and watches a job's wrapper, the
state directory's layout with its leases, the gateway's pause files, the
store of attempts and the attempt's record in it. A version is raised for any
change of a format that is not additive (`dataforge.formats.core`), whether
its fingerprint shows the change or only what the code means by it.
"""

from collections.abc import Generator
import contextlib
from datetime import datetime, UTC
import os
from pathlib import Path
import tempfile

from dataforge.curation.actions import ResourcePool
from dataforge.curation.engine import (
  ActionRecord,
  Attempt,
  Resumption,
  TrialRecord,
  Visit,
)
from dataforge.formats.core import Fact, Fingerprint, Format
from dataforge.formats.fingerprint import json_shape, sqlite, tree, typed_dict
from dataforge.queue import store as queue_store
from dataforge.queue.config import Config, load_config
from dataforge.queue.proc import Leader
from dataforge.queue.wrapper import command, JOB_ID_ENV, QUEUE_DB_ENV
from dataforge.runs import store as runs_store
from dataforge.statedir import ENV, state_dir


def _queue_table() -> Fingerprint:
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "queue.sqlite"
    with queue_store.connect(path):
      pass
    return sqlite(path)


def _queue_wrapper() -> Fingerprint:
  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    config = Config(
      queues={"q": 1},
      max_running=1,
      min_mem_available_mib=0,
      max_load1=1.0,
      database=root / "queue.sqlite",
      logs=root / "logs",
    )
    job = queue_store.submit(config, "q", ["true"], cwd=root, memory_mib=0)
    log = Path(job.log).relative_to(config.logs.resolve()).as_posix()
  wrapper = command(Path("/queue.sqlite"), 7, 30.0)
  wrapper[0] = "<python>"
  return {
    "command": Fact(" ".join(wrapper)),
    "leader": Fact(", ".join(Leader._fields)),
    "log": Fact(log.replace(str(job.id), "<id>")),
    f"env:{JOB_ID_ENV}": Fact("the job's ID", additive=True),
    f"env:{QUEUE_DB_ENV}": Fact("the database", additive=True),
  }


@contextlib.contextmanager
def _environ(**values: str | None) -> Generator[None]:
  before = {name: os.environ.get(name) for name in values}
  try:
    _set(values)
    yield
  finally:
    _set(before)


def _set(values: dict[str, str | None]) -> None:
  for name, value in values.items():
    if value is None:
      _ = os.environ.pop(name, None)
    else:
      os.environ[name] = value


def _statedir() -> Fingerprint:
  # dataforge.cli imports no module of dataforge.gateway (invariant 30), so
  # the gateway is imported only here and in _pause.
  from dataforge.gateway import pause
  from dataforge.gateway.channels import Channel

  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    facts = {"variable": Fact(ENV)}
    with _environ(**{ENV: None, "XDG_STATE_HOME": None, "HOME": tmp}):
      facts["default"] = Fact(f"~/{state_dir().relative_to(root).as_posix()}")
    with _environ(**{ENV: None, "XDG_STATE_HOME": tmp}):
      under = state_dir().relative_to(root).as_posix()
      facts["xdg"] = Fact(f"$XDG_STATE_HOME/{under}")
    state = root / "state"
    queue_file = root / "queue.yaml"
    _ = queue_file.write_text(
      "queues: {q: 1}\nmax_running: 1\nmin_mem_available_mib: 0\nmax_load1: 1\n"
    )
    with _environ(**{ENV: str(state)}):
      config = load_config(queue_file)
      facts["queue database"] = Fact(_under(config.database, state))
      facts["queue logs"] = Fact(_under(config.logs, state))
      pool = ResourcePool(0.5)
      with pool.build(0.5), pause.pausing(pause.Backoff()) as pauses:
        for one in pauses.values():
          _ = one.remaining()
        with pause.sessions(1):
          slots = pause.slots(Channel.CLAUDE)
          lease = None if slots is None else slots.take()
          facts.update(tree(state))
          if lease is not None:
            lease.release()
    return facts


def _under(path: Path, root: Path) -> str:
  return path.relative_to(root).as_posix()


def _pause() -> Fingerprint:
  from dataforge.gateway import pause
  from dataforge.gateway.channels import Channel

  # The pause file's keys, of each channel (`pause/<channel>.json`).
  # The TypedDict stays private to the module that writes the file.
  facts = typed_dict(pause._State)  # pyright: ignore[reportPrivateUsage]
  facts["channels"] = Fact(", ".join(c.value for c in Channel))
  return facts


def _runs_store() -> Fingerprint:
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "runs.sqlite"
    runs_store.Store(path).create()
    return sqlite(path)


def _record() -> Fingerprint:
  root = Path("/attempt")
  at = datetime(2026, 1, 1, tzinfo=UTC)
  action = ActionRecord(
    kind="trial",
    status="ok",
    outputs={"out": root / "out"},
    started_at=at,
    ended_at=at,
    cost_usd=0.5,
    summary="summary",
    trial=TrialRecord(
      id="trial",
      path=root / "trial",
      trace=root / "trial/trace",
      format="format",
      rewards={"reward": 1.0},
      exception="exception",
    ),
    session={"session": "id"},
    trace=root / "trace",
    trace_format="format",
  )
  visit = Visit(
    state="state",
    visit=1,
    outcome="outcome",
    data={"key": "value"},
    mark="mark",
    actions=[action],
  )
  attempt = Attempt(
    root,
    status="status",
    reason="reason",
    kept_visit=0,
    visits=[visit],
    max_cost_usd=1.0,
    cost_usd=0.5,
    stopped="stopped",
    graph={"states": []},  # pyright: ignore[reportArgumentType]
    deliverables=[{"name": "deliverable"}],
    resumptions=[
      Resumption(
        at=at,
        kept=1,
        discarded=[{"path": None}],
        carried_usd=0.5,
        reason="reason",
      )
    ],
  )
  maps = ("final",)
  opaque = {"graph", "deliverables"} | {
    path
    for prefix in ("visits/[]", "final/*")
    for path in (
      f"{prefix}/data",
      f"{prefix}/actions/[]/outputs",
      f"{prefix}/actions/[]/session",
      f"{prefix}/actions/[]/trial/rewards",
    )
  }
  opaque.add("resumptions/[]/discarded")
  return json_shape(attempt.to_json(), maps=maps, opaque=opaque)


FORMATS: tuple[Format, ...] = (
  Format(
    "dataforge.queue.table",
    1,
    _queue_table,
    "the queue's SQLite tables (dataforge.queue.store)",
  ),
  Format(
    "dataforge.queue.wrapper",
    1,
    _queue_wrapper,
    (
      "how the scheduler starts and knows a job's wrapper, the job's log and"
      " the variables a command gets (dataforge.queue.wrapper, .proc)"
    ),
  ),
  Format(
    "dataforge.statedir",
    1,
    _statedir,
    (
      "the state directory, its layout and its leases (dataforge.statedir,"
      " .leases, .queue.config, .curation.actions, .gateway.pause)"
    ),
  ),
  Format(
    "dataforge.gateway.pause",
    1,
    _pause,
    "a channel's pause file, pause/<channel>.json (dataforge.gateway.pause)",
    # A process of the version before rewrites the whole file and reads
    # backoff as Backoff(**...), which a key it does not know fails.
    additive=False,
  ),
  Format(
    "dataforge.runs.store",
    runs_store.SCHEMA_MAJOR,
    _runs_store,
    (
      "a store of attempts, its SQLite tables; its version is SCHEMA_MAJOR"
      " (dataforge.runs.store)"
    ),
  ),
  Format(
    "dataforge.curation.record",
    1,
    _record,
    (
      "an attempt's record, Attempt.to_json, in the store's rows and the"
      " record file (dataforge.curation.engine)"
    ),
    # Attempt.from_json of a later version may need a key that a writer of
    # the version before leaves out.
    additive=False,
  ),
)
"""dataforge's formats."""
