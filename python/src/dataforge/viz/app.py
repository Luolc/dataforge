"""The viewer's FastAPI app: a read-only JSON API and the built frontend."""

from collections.abc import Awaitable, Callable
from datetime import datetime, UTC
import json
import math
import mimetypes
import os
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from dataforge.viz import live, monitor
from dataforge.viz.config import Project, Site, split_relative
from dataforge.viz.paths import (
  attempt_parts,
  FILE_LIMIT,
  is_dir,
  list_attempts,
  list_dir,
  open_attempt,
  open_path,
  read_record,
  read_regular,
  RefusedError,
)

CSP = (
  "default-src 'self'; script-src 'self'; connect-src 'self';"
  " img-src 'self' data:; font-src 'self'; style-src 'self';"
  " object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
  " form-action 'none'"
)
"""The Content-Security-Policy of every response."""

_STATIC = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def create_app(site: Site, static: Path) -> FastAPI:
  """Make the app for a site.

  Args:
    site: The site, with its projects.
    static: The built frontend, the output of npm run build.

  Returns:
    The app; every route is a GET or a HEAD.
  """
  app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
  api = _Api(site, static)
  app.add_exception_handler(RefusedError, _refused)
  _ = app.middleware("http")(_headers)
  # In this order: the first route that matches a path serves it.
  routes = [
    ("/api/projects", api.projects),
    ("/api/projects/{p}/attempts", api.attempts),
    ("/api/projects/{p}/record", api.record),
    ("/api/projects/{p}/live", api.live_status),
    ("/api/projects/{p}/files", api.files),
    ("/api/projects/{p}/monitor/runs", api.monitor_runs),
    ("/api/projects/{p}/monitor/window", api.monitor_window),
    ("/api/projects/{p}/monitor/curve", api.monitor_curve),
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

  def projects(self) -> Response:
    return JSONResponse([p.public() for p in self._site.projects])

  def attempts(self, p: str) -> Response:
    project = self._project(p)
    rows = [_row(project, attempt)[0] for attempt in list_attempts(project)]
    _mark(project, rows)
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
    _mark(project, [row for row, _ in read.values()])
    row, record = read[attempt]
    running = row["live"] == live.RUNNING
    return JSONResponse(
      {
        "live": row["live"],
        "held": row["held"],
        "current": live.current(record) if running and record else None,
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
      if is_dir(fd):
        entries = [
          {"name": e.name, "type": e.type, "size": e.size} for e in list_dir(fd)
        ]
        return JSONResponse({"entries": entries})
      data = read_regular(fd, FILE_LIMIT).data
    return Response(data, media_type="application/octet-stream")

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


def _attempt(value: str | None) -> str:
  if value is None:
    raise RefusedError(400, "the attempt query parameter is missing")
  return value


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
    404: "not found",
    413: "too large",
    503: "unavailable",
  }
  return JSONResponse(
    {"error": names.get(status, "error"), "reason": reason}, status_code=status
  )


def _mark(project: Project, rows: list[dict[str, object]]) -> None:
  rows.sort(key=lambda r: cast(str, r["modified"] or ""), reverse=True)
  live.mark(project, rows)


def _item(project: Project, attempt: str) -> str:
  return "/".join(attempt.split("/")[: project.item.depth])


def _row(
  project: Project, attempt: str
) -> tuple[dict[str, object], dict[str, object] | None]:
  # A row of the listing, and the record it read, None when there is none.
  row: dict[str, object] = {
    "id": attempt,
    "item": _item(project, attempt),
    "modified": None,
    "status": None,
    "reason": None,
    "cost_usd": None,
    "started_at": None,
    "ended_at": None,
    "rounds": None,
    "values": None,
    "live": None,
    "held": None,
    "error": None,
  }
  try:
    with open_attempt(project, attempt) as fd:
      read = read_record(project, fd)
  except RefusedError as error:
    return row | {"error": f"the record: {error.reason}"}, None
  row["modified"] = datetime.fromtimestamp(read.mtime, UTC).isoformat()
  try:
    record = cast(object, json.loads(read.data))
  except ValueError, RecursionError:
    return row | {"error": "the record is not JSON"}, None
  if not isinstance(record, dict):
    return row | {"error": "the record is not a JSON object"}, None
  data = cast(dict[str, object], record)
  # The record is the file as an attempt left it: each value is checked
  # before it is used, and one of another type reads as missing.
  visits = [
    cast(dict[str, object], v)
    for v in _list(_field(data, ("visits",)))
    if isinstance(v, dict)
  ]
  actions = [
    cast(dict[str, object], a)
    for v in visits
    for a in _list(v.get("actions"))
    if isinstance(a, dict)
  ]
  status = _str(_field(data, ("status",)))
  outcome = _str(visits[-1].get("outcome")) if visits else None
  ended = live.has_ended(project, status, outcome)
  return row | {
    "status": status,
    "reason": _str(_field(data, ("reason",))),
    "cost_usd": _number(_field(data, ("budget", "cost_usd"))),
    "started_at": _str(actions[0].get("started_at")) if actions else None,
    "ended_at": _str(actions[-1].get("ended_at")) if actions else None,
    "rounds": _rounds(visits),
    "values": [_field(data, c.field) for c in project.columns],
    "live": None if ended else live.NOT_ENDED,
  }, data


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
