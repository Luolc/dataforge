"""The viewer's FastAPI app: a JSON API and the built frontend.

Every route only reads, but for two that write to the job queue and nothing
else: one puts a data item's action in its queue, one cancels a job.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, UTC
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import sqlite3
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from dataforge import queue
from dataforge.viz import actions, live, monitor, runs, snapshot, tabs
from dataforge.viz.config import (
  Action,
  case_value,
  Page,
  Project,
  Site,
  split_relative,
  Tab,
)
from dataforge.viz.paths import (
  attempt_parts,
  FILE_LIMIT,
  is_dir,
  list_attempts,
  list_dir,
  open_attempt,
  open_path,
  open_root,
  read_record,
  read_regular,
  read_snapshot,
  RefusedError,
)
from dataforge.viz.task import read_task

CSP = (
  "default-src 'self'; script-src 'self'; connect-src 'self';"
  " img-src 'self' data:; font-src 'self'; style-src 'self';"
  " object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
  " form-action 'none'"
)
"""The Content-Security-Policy of every response."""

WRITE_HEADER = "X-Viz-Write"
"""The header a write must carry, as 1: a page of another origin cannot send
it without the browser first asking, which the viewer never allows."""

ENDED_LIMIT = 50
"""How many of the jobs that have ended the queue route lists, unless the
request asks for more."""

ENDED_MAX = 5000
"""The most jobs that have ended the queue route lists, whatever is asked."""

_STATIC = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def create_app(site: Site, static: Path) -> FastAPI:
  """Make the app for a site.

  Args:
    site: The site, with its projects.
    static: The built frontend, the output of npm run build.

  Returns:
    The app; every route is a GET or a HEAD, but the two writes, POSTs.
  """
  app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
  api = _Api(site, static)
  app.add_exception_handler(RefusedError, _refused)
  _ = app.middleware("http")(_headers)
  app.add_api_route(
    "/api/projects/{p}/actions/{name}", api.submit, methods=["POST"]
  )
  app.add_api_route(
    "/api/queue/jobs/{job}/cancel", api.cancel, methods=["POST"]
  )
  # In this order: the first route that matches a path serves it.
  routes = [
    ("/api/projects", api.projects),
    ("/api/projects/{p}/attempts", api.attempts),
    ("/api/projects/{p}/record", api.record),
    ("/api/projects/{p}/live", api.live_status),
    ("/api/projects/{p}/files", api.files),
    ("/api/projects/{p}/tabs", api.tabs),
    ("/api/projects/{p}/task", api.task),
    ("/api/projects/{p}/actions", api.actions),
    ("/api/projects/{p}/monitor/runs", api.monitor_runs),
    ("/api/projects/{p}/monitor/window", api.monitor_window),
    ("/api/projects/{p}/monitor/curve", api.monitor_curve),
    ("/api/projects/{p}/pages/{name}/snapshot", api.snapshot),
    ("/api/projects/{p}/pages/{name}/files", api.page_files),
    ("/api/queue", api.queue_jobs),
    ("/api/{rest:path}", api.no_route),
    ("/{rest:path}", api.static),
  ]
  for path, endpoint in routes:
    app.add_api_route(path, endpoint, methods=["GET", "HEAD"])
  return app


class _Api:
  def __init__(self, site: Site, static: Path) -> None:
    self._site: Site = site
    self._static: Path = static
    self._projects: dict[str, Project] = {p.id: p for p in site.projects}
    # A write is taken only from a page the viewer served, as the browser
    # names it, at the one address the server binds.
    host = str(ipaddress.ip_address(site.listen.host))
    if ":" in host:
      host = f"[{host}]"
    port = site.listen.port
    # A browser leaves out the port when it is http's own.
    self._host: str = host if port == 80 else f"{host}:{port}"
    self._origin: str = f"http://{self._host}"

  def projects(self) -> Response:
    return JSONResponse([p.public() for p in self._site.projects])

  def attempts(self, p: str) -> Response:
    project = self._project(p)
    read = [_row(project, attempt) for attempt in list_attempts(project)]
    rows = [row for row, _, _ in read]
    _mark(project, rows, _touched(read))
    self._stopped(project, rows)
    self._mark_queued(project, rows)
    unstarted = [
      row
      for row in rows
      if row["live"] == live.RUNNING and row["started_at"] is None
    ]
    if unstarted:
      starts = self._job_starts(project)
      for row in unstarted:
        row["started_at"] = starts.get(str(row["item"]))
    return JSONResponse(rows)

  def live_status(self, p: str, attempt: str | None = None) -> Response:
    project = self._project(p)
    attempt = _attempt(attempt)
    _ = attempt_parts(project, attempt)
    # live as the listing gives it, from the attempts of its item alone.
    item = _item(project, attempt)
    read = {
      a: _row(project, a)
      for a in list_attempts(project)
      if _item(project, a) == item
    }
    if attempt not in read:
      raise RefusedError(404, f"no attempt {attempt!r}")
    rows = [row for row, _, _ in read.values()]
    _mark(project, rows, _touched(list(read.values())))
    self._stopped(project, rows)
    row, record, _ = read[attempt]
    running = row["live"] == live.RUNNING
    current = live.current(record) if running and record else None
    if current is not None and current["since"] is None:
      current["since"] = self._job_starts(project).get(item)
    return JSONResponse(
      {
        "live": row["live"],
        "held": row["held"],
        "holder": row["holder"],
        "stopped": row["stopped"],
        "current": current,
        "state": row["state"],
        "status": row["status"],
        "reason": row["reason"],
        "note": row["note"],
      }
    )

  def record(self, p: str, attempt: str | None = None) -> Response:
    project = self._project(p)
    with open_attempt(project, _attempt(attempt)) as fd:
      data = read_record(project, fd).data
    return Response(data, media_type="application/json")

  def files(
    self, p: str, attempt: str | None = None, path: str = ""
  ) -> Response:
    project = self._project(p)
    with (
      open_attempt(project, _attempt(attempt)) as root,
      open_path(root, path) as fd,
    ):
      return _file(fd)

  def snapshot(self, p: str, name: str) -> Response:
    project = self._project(p)
    page = _snapshot_page(project, name)
    try:
      data = read_snapshot(page)
    except RefusedError as refused:
      if not refused.absent:
        raise
      # Not an error to show but a state of its own, as a monitor database
      # not written yet is.
      return JSONResponse(
        {
          "error": "no snapshot",
          "reason": refused.reason,
          "file": page.file_shown,
        },
        status_code=404,
      )
    try:
      parsed = cast(object, json.loads(data, parse_constant=_no_constant))
      others = [q.name for q in project.pages if q.name not in (None, name)]
      snapshot.check(parsed, list(page.roots), others)
    except (ValueError, snapshot.SnapshotError) as error:
      # UnicodeDecodeError and json's errors are ValueErrors.
      raise RefusedError(503, f"the snapshot: {error}") from None
    return Response(data, media_type="application/json")

  def page_files(
    self, p: str, name: str, root: str | None = None, path: str = ""
  ) -> Response:
    page = _snapshot_page(self._project(p), name)
    with (
      open_root(page, _required("root", root)) as top,
      open_path(top, path, f"the root {root}") as fd,
    ):
      return _file(fd)

  def tabs(self, p: str, attempt: str | None = None) -> Response:
    project = self._project(p)
    attempt = _attempt(attempt)
    find = self._queued(project) if any(t.job for t in project.tabs) else None
    with open_attempt(project, attempt) as root:
      return JSONResponse(
        [
          {
            "name": tab.name,
            "queued": None
            if tab.job is None or find is None
            else find(tab.job, attempt),
            "versions": [
              {
                "dir": version,
                "title": None
                if tab.version_title is None
                else tabs.version_title(tab.version_title, root, version),
                "parts": [
                  {
                    "name": part.name,
                    "fields": tabs.fields(part, root, version),
                  }
                  for part in tab.parts
                ],
              }
              for version in tabs.versions(tab, root)
            ],
          }
          for tab in project.tabs
        ]
      )

  def task(self, p: str, attempt: str | None = None) -> Response:
    project = self._project(p)
    if project.task is None:
      raise RefusedError(404, f"the project {p!r} names no task")
    with open_attempt(project, _attempt(attempt)) as root:
      return JSONResponse(read_task(project, project.task, root))

  def actions(self, p: str, item: str | None = None) -> Response:
    project = self._project(p)
    if not project.actions:
      return JSONResponse({"newest": None, "actions": []})
    item = _required("item", item)
    actions.check_item(project, item)
    rows = _item_rows(project, item)
    jobs = self._open_jobs()
    found = None if project.runs is None else runs.read(project.runs, item)
    newest = actions.newest_attempt(rows, found)
    return JSONResponse(
      {
        "newest": newest,
        "actions": [
          {
            "name": a.name,
            "label": a.label,
            "queue": a.queue,
            "reason": actions.unavailable(project, a, item, rows, jobs, found),
            "params": [param.public() for param in a.params],
          }
          for a in project.actions
        ],
      }
    )

  def submit(self, request: Request, p: str, name: str) -> Response:
    self._writable(request)
    project = self._project(p)
    action = self._action(project, name)
    _only(request, {"item", *(param.name for param in action.params)})
    item = _required("item", request.query_params.get("item"))
    actions.check_item(project, item)
    args = actions.param_args(action, request.query_params)
    rows = _item_rows(project, item)
    config = self._config()
    found = None if project.runs is None else runs.read(project.runs, item)
    reason = actions.unavailable(
      project, action, item, rows, self._open_jobs(), found
    )
    if reason is not None:
      raise RefusedError(409, reason)
    try:
      job = actions.submit(config, project, action, item, args)
    except queue.DuplicateError as duplicate:
      return JSONResponse(
        {
          "error": "conflict",
          "reason": f"Job {duplicate.job.id} holds this item's key.",
          "job": actions.public(duplicate.job),
        },
        status_code=409,
      )
    except ValueError:
      # Its text may name the action's directory, which no page shows.
      raise RefusedError(503, "the queue refused the job") from None
    except (sqlite3.Error, OSError) as error:
      raise RefusedError(503, _queue_failure(error)) from None
    return JSONResponse({"job": actions.public(job)}, status_code=201)

  def cancel(self, request: Request, job: str) -> Response:
    self._writable(request)
    _only(request, set())
    config = self._config()
    try:
      found = queue.cancel(config, _integer("job", job))
    except queue.NoSuchJobError:
      raise RefusedError(404, f"no job {job}") from None
    except (sqlite3.Error, OSError) as error:
      raise RefusedError(503, _queue_failure(error)) from None
    return JSONResponse({"job": actions.public(found)})

  def queue_jobs(self, ended: str | None = None) -> Response:
    limit = ENDED_LIMIT if ended is None else _integer("ended", ended)
    limit = min(max(limit, ENDED_LIMIT), ENDED_MAX)
    config = self._config()
    try:
      pauses = {pause.queue: pause.to_json() for pause in queue.pauses(config)}
      open_jobs = queue.jobs(config, states=actions.OPEN)
      # The jobs that ended last, not those queued last: a long job queued
      # early may end after many short ones.
      finished = queue.jobs(
        config, states=actions.ENDED, by="ended_at", limit=limit
      )
      ended_count = queue.count(config, states=actions.ENDED)
    except FileNotFoundError:
      # No job was ever submitted: submitting one makes the database.
      open_jobs, finished, ended_count, pauses = [], [], 0, {}
    except (sqlite3.Error, OSError) as error:
      raise RefusedError(503, _queue_failure(error)) from None
    return JSONResponse(
      {
        "queues": [
          {
            "name": name,
            "limit": limit,
            "running": sum(
              j.queue == name and j.state == "running" for j in open_jobs
            ),
            "queued": sum(
              j.queue == name and j.state == "queued" for j in open_jobs
            ),
            "paused": pauses.get(name),
          }
          for name, limit in config.queues.items()
        ],
        "max_running": config.max_running,
        "ended_limit": limit,
        "ended": ended_count,
        "jobs": [actions.public(j) for j in open_jobs + finished],
      }
    )

  def monitor_runs(self, p: str) -> Response:
    return self._monitor(p, monitor.runs)

  def monitor_window(
    self,
    p: str,
    run: str | None = None,
    t0: str | None = None,
    t1: str | None = None,
  ) -> Response:
    return self._monitor(
      p,
      lambda db: monitor.window(
        db,
        _integer("run", run),
        None if t0 is None else _seconds("t0", t0),
        None if t1 is None else _seconds("t1", t1),
      ),
    )

  def monitor_curve(
    self,
    p: str,
    run: str | None = None,
    t0: str | None = None,
    t1: str | None = None,
    attempt: str | None = None,
    container: str | None = None,
  ) -> Response:
    return self._monitor(
      p,
      lambda db: monitor.curve(
        db,
        _integer("run", run),
        _seconds("t0", t0),
        _seconds("t1", t1),
        attempt,
        container,
      ),
    )

  def no_route(self, rest: str) -> Response:
    return _error(404, f"no route /api/{rest}")

  def static(self, rest: str) -> Response:
    return _static(self._static, rest)

  def _project(self, p: str) -> Project:
    if p not in self._projects:
      raise RefusedError(404, f"no project {p!r}")
    return self._projects[p]

  def _action(self, project: Project, name: str) -> Action:
    found = next((a for a in project.actions if a.name == name), None)
    if found is None:
      raise RefusedError(404, f"no action {name!r} in {project.id}")
    return found

  def _config(self) -> queue.Config:
    if self._site.queue is None:
      raise RefusedError(404, "the site names no queue")
    return self._site.queue

  def _job_starts(self, project: Project) -> dict[str, str]:
    # When the running jobs of a project's items started, by item, as the
    # record has no start before an action of the attempt has ended; empty
    # when the site names no queue or it cannot be read. Labels are the
    # job's own, so a job queued without them gives no start.
    if self._site.queue is None:
      return {}
    try:
      jobs = queue.jobs(self._site.queue, states={"running"})
    except sqlite3.Error, OSError:
      return {}
    return {
      job.labels["item"]: datetime.fromtimestamp(
        job.started_at, UTC
      ).isoformat()
      for job in reversed(jobs)
      if job.labels.get("project") == project.id
      and "item" in job.labels
      and job.started_at is not None
    }

  def _queued(self, project: Project) -> actions.Queued:
    # Each of the project's jobs of an action and an item, and its queue's
    # pause, read once for the request.
    config = self._site.queue
    if config is None:
      return actions.unread("the site names no queue")
    try:
      return actions.queued(project, queue.jobs(config), queue.pauses(config))
    except FileNotFoundError:
      # No job was ever submitted, and no queue was ever paused.
      return actions.queued(project, [], [])
    except (sqlite3.Error, OSError) as error:
      return actions.unread(_queue_failure(error))

  def _mark_queued(
    self, project: Project, rows: list[dict[str, object]]
  ) -> None:
    # Beside each column of a tab that names its job, where the attempt has
    # no version of the tab, that job; null beside every other column.
    jobs = [_tab(project, c.tab) for c in project.columns]
    find: actions.Queued | None = None
    for row in rows:
      reads = cast(list[str | None] | None, row["reads"])
      if reads is None:
        continue
      attempt = cast(str, row["id"])
      found: list[dict[str, object] | None] = []
      for tab, read in zip(jobs, reads, strict=True):
        if (
          tab is None
          or tab.job is None
          or read != tabs.NOT_RUN
          or _has_version(project, tab, attempt)
        ):
          found.append(None)
          continue
        find = find or self._queued(project)
        found.append(find(tab.job, attempt))
      row["queued"] = found

  def _stopped(self, project: Project, rows: list[dict[str, object]]) -> None:
    # Which job ran each attempt that may have stopped, its item's lock free,
    # once it has ended; nothing with runs, whose rows say how each stands,
    # nor when the site names no queue or it cannot be read, and the queue
    # is read only when some row may have stopped so.
    if (
      project.runs is not None
      or self._site.queue is None
      or not any(
        row["live"] == live.MAY_HAVE_STOPPED and row["held"] is False
        for row in rows
      )
    ):
      return
    try:
      ended = queue.jobs(self._site.queue, states=actions.ENDED)
    except sqlite3.Error, OSError:
      return
    live.stopped(project, rows, ended)

  def _open_jobs(self) -> list[queue.Job]:
    try:
      return queue.jobs(self._config(), states=actions.OPEN)
    except FileNotFoundError:
      return []
    except (sqlite3.Error, OSError) as error:
      raise RefusedError(503, _queue_failure(error)) from None

  def _writable(self, request: Request) -> None:
    # Against a page of another site (CSRF) and one that a name of its own
    # points at the viewer's address (DNS rebinding): the header makes the
    # browser ask first across origins, and Host and Origin must both name
    # the address the server binds.
    headers = request.headers
    if headers.getlist(WRITE_HEADER) != ["1"]:
      raise RefusedError(403, f"a write needs the header {WRITE_HEADER}: 1")
    if headers.getlist("host") != [self._host]:
      raise RefusedError(403, f"a write is taken only at {self._host}")
    if headers.getlist("origin") != [self._origin]:
      raise RefusedError(403, f"a write is taken only from {self._origin}")
    if headers.get("sec-fetch-site", "same-origin") != "same-origin":
      raise RefusedError(403, "a write is taken only from the same origin")
    if "transfer-encoding" in headers or headers.get(
      "content-length", "0"
    ) not in ("", "0"):
      raise RefusedError(400, "a write takes no body")

  def _monitor(self, p: str, read: Callable[[Path], object]) -> Response:
    project = self._project(p)
    if project.monitor is None:
      raise RefusedError(404, f"the project {p!r} names no monitor database")
    try:
      return JSONResponse(read(project.monitor))
    except monitor.MissingError as missing:
      # Not an error to show but a state of its own: the page tells how to
      # start, naming the database as the project file does.
      return JSONResponse(
        {
          "error": "no database",
          "reason": missing.reason,
          "db": project.monitor_shown,
        },
        status_code=404,
      )


def _only(request: Request, params: set[str]) -> None:
  names = [name for name, _ in request.query_params.multi_items()]
  if any(n not in params for n in names) or len(names) != len(set(names)):
    allowed = ", ".join(sorted(params)) or "none"
    raise RefusedError(400, f"a write takes its parameters once: {allowed}")


def _file(fd: int) -> Response:
  # A directory's listing, or a regular file's bytes, never to be run.
  if is_dir(fd):
    entries = [
      {"name": e.name, "type": e.type, "size": e.size} for e in list_dir(fd)
    ]
    return JSONResponse({"entries": entries})
  data = read_regular(fd, FILE_LIMIT).data
  return Response(data, media_type="application/octet-stream")


def _snapshot_page(project: Project, name: str) -> Page:
  page = project.page(name)
  if page is None or page.kind != "snapshot":
    raise RefusedError(404, f"no snapshot page {name!r} in {project.id}")
  return page


def _no_constant(name: str) -> object:
  raise ValueError(f"{name} is not JSON")


def _attempt(value: str | None) -> str:
  return _required("attempt", value)


def _required(name: str, value: str | None) -> str:
  if value is None:
    raise RefusedError(400, f"the {name} query parameter is missing")
  return value


def _queue_failure(error: sqlite3.Error | OSError) -> str:
  # An OSError's text may name a path; only its reason is shown.
  if isinstance(error, OSError):
    return f"the queue cannot be read: {error.strerror or type(error).__name__}"
  return f"the queue cannot be read: {error}"


def _integer(name: str, value: str | None) -> int:
  if value is None or not value.isascii() or not value.isdigit():
    raise RefusedError(400, f"{name} is not a whole number")
  return int(value)


def _seconds(name: str, value: str | None) -> float:
  try:
    seconds = float(value or "")
  except ValueError:
    seconds = math.nan
  if not math.isfinite(seconds):
    raise RefusedError(400, f"{name} is not a number of seconds")
  return seconds


async def _refused(request: Request, error: Exception) -> Response:
  del request
  refusal = cast(RefusedError, error)
  return _error(refusal.status, refusal.reason)


async def _headers(
  request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
  response = await call_next(request)
  response.headers["Content-Security-Policy"] = CSP
  response.headers["X-Content-Type-Options"] = "nosniff"
  return response


def _error(status: int, reason: str) -> Response:
  names = {
    400: "bad request",
    403: "forbidden",
    404: "not found",
    409: "conflict",
    413: "too large",
    503: "unavailable",
  }
  return JSONResponse(
    {"error": names.get(status, "error"), "reason": reason}, status_code=status
  )


_Read = tuple[dict[str, object], dict[str, object] | None, float | None]


def _touched(read: list[_Read]) -> dict[str, float]:
  # When each attempt that may run last wrote, by ID, from _row.
  return {str(row["id"]): t for row, _, t in read if t is not None}


def _mark(
  project: Project, rows: list[dict[str, object]], touched: dict[str, float]
) -> None:
  rows.sort(key=lambda r: cast(str, r["modified"] or ""), reverse=True)
  if project.runs is not None:
    runs.mark(project, rows)
  else:
    live.mark(project, rows, touched)


def _item_rows(project: Project, item: str) -> list[dict[str, object]]:
  # The listing's rows of an item's attempts, newest first, marked.
  read = [_row(project, a) for a in list_attempts(project)]
  read = [r for r in read if r[0]["item"] == item]
  if not read:
    raise RefusedError(404, f"no attempt of item {item!r}")
  rows = [row for row, _, _ in read]
  _mark(project, rows, _touched(read))
  return rows


def _has_version(project: Project, tab: Tab, attempt: str) -> bool:
  # A column's file not there may be in a version not finished yet.
  try:
    with open_attempt(project, attempt) as fd:
      return bool(tabs.versions(tab, fd))
  except RefusedError:
    return False


def _tab(project: Project, name: str | None) -> Tab | None:
  return next((t for t in project.tabs if t.name == name), None)


def _item(project: Project, attempt: str) -> str:
  return "/".join(attempt.split("/")[: project.item.depth])


def _row(project: Project, attempt: str) -> _Read:
  # A row of the listing, the record it read, None when there is none, and,
  # for an attempt its item's lock may be held for, when it last wrote: the
  # record's mtime for one that has not ended, its directory's for one that
  # has no record yet, as one that has just started; else None.
  row: dict[str, object] = {
    "id": attempt,
    "item": _item(project, attempt),
    "modified": None,
    "status": None,
    "reason": None,
    "cost_usd": None,
    "cost_from": None,
    "started_at": None,
    "ended_at": None,
    "rounds": None,
    "values": None,
    "reads": None,
    "queued": None,
    "live": None,
    "held": None,
    "holder": None,
    "stopped": None,
    "state": None,
    "note": None,
    "progress_at": None,
    "error": None,
  }
  made: float | None = None
  try:
    with open_attempt(project, attempt) as fd:
      made = os.fstat(fd).st_mtime
      read = read_record(project, fd)
      from_files = [
        None if c.file is None else tabs.column_value(project, c, fd)
        for c in project.columns
      ]
  except RefusedError as error:
    # An attempt that has just started may have written no record yet.
    fresh = made if error.absent else None
    if error.absent and project.runs is not None:
      # The store decides: `runs.mark` shows the row, or says there is none.
      return row, None, fresh
    return row | {"error": f"the record: {error.reason}"}, None, fresh
  row["modified"] = datetime.fromtimestamp(read.mtime, UTC).isoformat()
  try:
    record = cast(object, json.loads(read.data))
  except ValueError, RecursionError:
    return row | {"error": "the record is not JSON"}, None, None
  if not isinstance(record, dict):
    return row | {"error": "the record is not a JSON object"}, None, None
  data = cast(dict[str, object], record)
  # The record is the file as an attempt left it: each value is checked
  # before it is used, and one of another type reads as missing.
  visits = [
    cast(dict[str, object], v)
    for v in _list(_field(data, ("visits",)))
    if isinstance(v, dict)
  ]
  actions = [a for v in visits for a in _actions(v)]
  starts = [_visit_time(v, 0, "started_at") for v in visits]
  ends = [_visit_time(v, -1, "ended_at") for v in visits]
  status = _str(_field(data, ("status",)))
  outcome = _str(visits[-1].get("outcome")) if visits else None
  ended = live.has_ended(project, status, outcome)
  cost, cost_from = _cost(data, actions)
  return (
    row
    | {
      "status": status,
      "reason": _str(_field(data, ("reason",))),
      "cost_usd": cost,
      "cost_from": cost_from,
      "started_at": next((t for t in starts if t is not None), None),
      "ended_at": next((t for t in reversed(ends) if t is not None), None),
      "rounds": _rounds(visits),
      "values": [
        case_value(data, c.cases)
        if c.cases
        else _field(data, c.field)
        if found is None
        else found["value"]
        for c, found in zip(project.columns, from_files, strict=True)
      ],
      "reads": [
        None if found is None else found["read"] for found in from_files
      ],
      "live": None if ended else live.NOT_ENDED,
    },
    data,
    None if ended else read.mtime,
  )


def _actions(visit: dict[str, object]) -> list[dict[str, object]]:
  return [
    cast(dict[str, object], a)
    for a in _list(visit.get("actions"))
    if isinstance(a, dict)
  ]


def _visit_time(visit: dict[str, object], which: int, key: str) -> str | None:
  # A visit's start or end: its first or last action's, else its own, which a
  # visit that raised before any action has alone.
  actions = _actions(visit)
  return _str((actions[which] if actions else visit).get(key))


def _cost(
  data: dict[str, object], actions: list[dict[str, object]]
) -> tuple[float | None, str | None]:
  # A record with no budget costs what its actions cost, as its page says.
  budget = _number(_field(data, ("budget", "cost_usd")))
  if budget is not None:
    return budget, "budget"
  costs = [c for a in actions if (c := _number(a.get("cost_usd"))) is not None]
  return (sum(costs), "runs") if costs else (None, None)


def _rounds(visits: list[dict[str, object]]) -> list[dict[str, object]]:
  last: dict[int, dict[str, object]] = {}
  for visit in visits:
    round_ = visit.get("round")
    if isinstance(round_, int) and not isinstance(round_, bool):
      last[round_] = {
        "round": round_,
        "state": _str(visit.get("state")),
        "outcome": _str(visit.get("outcome")),
      }
  return [last[r] for r in sorted(last)]


def _list(value: object) -> list[object]:
  return cast(list[object], value) if isinstance(value, list) else []


def _str(value: object) -> str | None:
  return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
  if isinstance(value, bool) or not isinstance(value, int | float):
    return None
  return value


def _field(record: object, field: tuple[str, ...]) -> object:
  current = record
  for key in field:
    if not isinstance(current, dict) or key not in current:
      return None
    current = cast(dict[str, object], current)[key]
  return current


def _static(static: Path, rest: str) -> Response:
  # The built frontend is the operator's own, but a request names the path:
  # the same rules as an attempt's keep it inside the directory.
  parts = split_relative(rest)
  if parts:
    found = _static_file(static, parts)
    if found is not None:
      kind = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
      return Response(found, media_type=kind)
  index = _static_file(static, ("index.html",))
  if index is None:
    return _error(404, "the frontend is not built")
  return Response(index, media_type="text/html")


def _static_file(static: Path, parts: tuple[str, ...]) -> bytes | None:
  try:
    fd = os.open(static, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
  except OSError:
    return None
  try:
    for part in parts:
      child = os.open(part, _STATIC, dir_fd=fd)
      os.close(fd)
      fd = child
    return read_regular(fd, FILE_LIMIT).data
  except OSError, RefusedError:
    return None
  finally:
    os.close(fd)
