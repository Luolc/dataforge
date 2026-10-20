"""The viewer's FastAPI app: a read-only JSON API and the built frontend."""

from collections.abc import Awaitable, Callable
from datetime import datetime, UTC
import json
import mimetypes
import os
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from dataforge.viz.config import Project, Site, split_relative
from dataforge.viz.paths import (
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
  " img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline';"
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
    The app; every route is a GET.
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
    ("/api/projects/{p}/files", api.files),
    ("/api/{rest:path}", api.no_route),
    ("/{rest:path}", api.static),
  ]
  for path, endpoint in routes:
    app.add_api_route(path, endpoint, methods=["GET"])
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
    rows = [_row(project, attempt) for attempt in list_attempts(project)]
    rows.sort(key=lambda r: cast(str, r["modified"] or ""), reverse=True)
    return JSONResponse(rows)

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

  def no_route(self, rest: str) -> Response:
    return _error(404, f"no route /api/{rest}")

  def static(self, rest: str) -> Response:
    return _static(self._static, rest)

  def _project(self, p: str) -> Project:
    if p not in self._projects:
      raise RefusedError(404, f"no project {p!r}")
    return self._projects[p]


def _attempt(value: str | None) -> str:
  if value is None:
    raise RefusedError(400, "the attempt query parameter is missing")
  return value


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
  names = {400: "bad request", 404: "not found", 413: "too large"}
  return JSONResponse(
    {"error": names.get(status, "error"), "reason": reason}, status_code=status
  )


def _row(project: Project, attempt: str) -> dict[str, object]:
  values: list[object] = [None] * len(project.columns)
  row: dict[str, object] = {
    "id": attempt,
    "modified": None,
    "values": values,
    "reason": None,
  }
  try:
    with open_attempt(project, attempt) as fd:
      read = read_record(project, fd)
  except RefusedError as error:
    return row | {"reason": f"the record: {error.reason}"}
  row["modified"] = datetime.fromtimestamp(read.mtime, UTC).isoformat()
  try:
    record = cast(object, json.loads(read.data))
  except ValueError, RecursionError:
    return row | {"reason": "the record is not JSON"}
  for i, column in enumerate(project.columns):
    values[i] = _field(record, column.field)
  return row


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
