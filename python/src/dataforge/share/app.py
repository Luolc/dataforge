"""The FastAPI app of share: a JSON API over exports, and the frontend.

Every route only reads: GETs and HEADs, but for two POSTs, which sign in
and sign out and write nothing but a cookie. Every route of a project's
data needs a valid token that names the project; any reason a token is
refused gives the same 401 and the same words.
"""

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager, ExitStack
from datetime import datetime, UTC
import json
import math
import mimetypes
import os
from pathlib import Path
import sqlite3
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from dataforge.share import tokens
from dataforge.share.config import Site
from dataforge.share.exports import Export, ExportError, open_export, open_item
from dataforge.share.tokens import Token
from dataforge.viz.paths import (
  FILE_LIMIT,
  is_dir,
  list_dir,
  open_path,
  read_regular,
  RECORD_LIMIT,
  RefusedError,
)

CSP = (
  "default-src 'self'; script-src 'self'; connect-src 'self';"
  " img-src 'self' data:; font-src 'self'; style-src 'self';"
  " object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
  " form-action 'none'"
)
"""The Content-Security-Policy of every response."""

HEADERS = {
  "Content-Security-Policy": CSP,
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
}
"""The headers every response carries."""

COOKIE = "share"
"""The cookie that carries the token after a sign-in."""

DENIED = "a valid token for this project is needed"
"""The words of every 401, whatever the reason the token was refused."""

LOGIN_LIMIT = 4096
"""The most bytes of a sign-in's body that are read."""

PLACEHOLDER = (
  "<!doctype html><meta charset=utf-8><title>share</title>"
  "<p>The frontend of this site is not built.</p>"
)
"""The page served for the frontend when the server is given none."""

_UNNAMED = {404: "no such file", 413: "the file is too large"}
_STATIC = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


def create_app(
  site: Site,
  static: Path | None,
  clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
  """Make the app for a site.

  Args:
    site: The site.
    static: The built frontend; None to serve PLACEHOLDER in its place.
    clock: The current time, against which a token's expiry is checked.

  Returns:
    The app; every route is a GET or a HEAD, but the sign-in and the
    sign-out, POSTs.
  """
  app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
  api = _Api(site, static, clock)
  app.add_exception_handler(RefusedError, _refused)
  # Its response is made outside the middleware that adds HEADERS.
  app.add_exception_handler(Exception, _failed)
  _ = app.middleware("http")(_headers)
  app.add_api_route("/api/login", api.login, methods=["POST"])
  app.add_api_route("/api/logout", api.logout, methods=["POST"])
  # In this order: the first route that matches a path serves it.
  routes = [
    ("/api/projects", api.projects),
    ("/api/projects/{p}/items", api.items),
    ("/api/projects/{p}/record", api.record),
    ("/api/projects/{p}/files", api.files),
    ("/api/{rest:path}", api.no_route),
    ("/{rest:path}", api.static),
  ]
  for path, endpoint in routes:
    app.add_api_route(path, endpoint, methods=["GET", "HEAD"])
  return app


class _Api:
  def __init__(
    self, site: Site, static: Path | None, clock: Callable[[], datetime]
  ) -> None:
    self._site: Site = site
    self._static: Path | None = static
    self._clock: Callable[[], datetime] = clock

  async def login(self, request: Request) -> Response:
    # A JSON body, which a page of another origin cannot send without the
    # CORS preflight the server never allows.
    kind = request.headers.get("content-type", "").split(";")[0].strip()
    if kind.lower() != "application/json" or not _same_site(request):
      return _denied()
    body = bytearray()
    async for chunk in request.stream():
      body += chunk
      if len(body) > LOGIN_LIMIT:
        return _denied()
    try:
      value = cast(object, json.loads(bytes(body)))
    except ValueError, RecursionError:
      return _denied()
    given = (
      cast(dict[str, object], value).get("token")
      if isinstance(value, dict)
      else None
    )
    if not isinstance(given, str):
      return _denied()
    now = self._clock()
    # SQLite blocks: off the event loop, as the other routes run.
    found = await run_in_threadpool(self._check, given, now)
    if found is None:
      return _denied()
    response = Response(status_code=204)
    left = math.floor((found.expires_at - now).total_seconds())
    response.set_cookie(
      COOKIE,
      given,
      max_age=max(left, 1),
      path="/",
      httponly=True,
      samesite="strict",
    )
    return response

  def logout(self) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
    return response

  def projects(self, request: Request) -> Response:
    token = self._token(request)
    if token is None:
      raise RefusedError(401, DENIED)
    shown: list[dict[str, object]] = []
    for directory in self._site.projects:
      try:
        with open_export(directory) as (_, export):
          if export.id in token.projects:
            shown.append(export.public())
      except RefusedError, ExportError:
        # One export that cannot be read hides only itself.
        continue
    return JSONResponse(shown)

  def items(self, request: Request, p: str) -> Response:
    with self._export(request, p) as (_, export):
      return JSONResponse([i.public() for i in export.items])

  def record(
    self, request: Request, p: str, attempt: str | None = None
  ) -> Response:
    with (
      self._export(request, p) as (top, export),
      open_item(top, export, _required("attempt", attempt)) as item,
      _unnamed(),
      open_path(item, "record.json") as fd,
    ):
      data = read_regular(fd, RECORD_LIMIT).data
    return Response(data, media_type="application/json")

  def files(
    self, request: Request, p: str, attempt: str | None = None, path: str = ""
  ) -> Response:
    with (
      self._export(request, p) as (top, export),
      open_item(top, export, _required("attempt", attempt)) as item,
      _unnamed(),
      open_path(item, path) as fd,
    ):
      if is_dir(fd):
        entries = [
          {"name": e.name, "type": e.type, "size": e.size} for e in list_dir(fd)
        ]
        return JSONResponse({"entries": entries})
      data = read_regular(fd, FILE_LIMIT).data
    return Response(data, media_type="application/octet-stream")

  def no_route(self, rest: str) -> Response:
    # Not repeated: a path may hold a token a person pasted.
    del rest
    return _error(404, "no such route")

  def static(self, rest: str) -> Response:
    if self._static is None:
      return HTMLResponse(PLACEHOLDER)
    if rest:
      found = _static_file(self._static, rest)
      if found is not None:
        name = rest.rsplit("/", 1)[-1]
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(found, media_type=kind)
    index = _static_file(self._static, "index.html")
    if index is None:
      return _error(404, "the frontend is not built")
    return Response(index, media_type="text/html")

  @contextmanager
  def _export(self, request: Request, p: str) -> Generator[tuple[int, Export]]:
    # The token is checked before anything else, so that a project the token
    # does not name reads as one that does not exist.
    token = self._token(request)
    if token is None or p not in token.projects:
      raise RefusedError(401, DENIED)
    unread = False
    for directory in self._site.projects:
      with ExitStack() as stack:
        try:
          top, export = stack.enter_context(open_export(directory))
        except RefusedError, ExportError:
          unread = True
          continue
        if export.id == p:
          yield top, export
          return
    if unread:
      raise RefusedError(503, "an export cannot be read")
    raise RefusedError(404, "no such project")

  def _token(self, request: Request) -> Token | None:
    given = request.cookies.get(COOKIE)
    return None if given is None else self._check(given, self._clock())

  def _check(self, given: str, now: datetime) -> Token | None:
    try:
      return tokens.check(self._site.tokens, given, now)
    except sqlite3.Error:
      raise RefusedError(503, "the tokens cannot be read") from None


@contextmanager
def _unnamed() -> Generator[None]:
  # dataforge.viz.paths names the path in its refusals, and a path may hold
  # what a person pasted: share gives each refusal without it.
  try:
    yield
  except RefusedError as error:
    reason = _UNNAMED.get(error.status, "refused")
    raise RefusedError(error.status, reason) from None


def _same_site(request: Request) -> bool:
  return request.headers.get("sec-fetch-site", "same-origin") == "same-origin"


def _required(name: str, value: str | None) -> str:
  if value is None:
    raise RefusedError(400, f"the {name} query parameter is missing")
  return value


def _static_file(static: Path, path: str) -> bytes | None:
  # The built frontend is the operator's own, but a request names the path:
  # the same rules as an export's keep it inside the directory.
  try:
    top = os.open(static, _STATIC)
  except OSError:
    return None
  try:
    with open_path(top, path) as fd:
      return read_regular(fd, FILE_LIMIT).data
  except OSError, RefusedError:
    return None
  finally:
    os.close(top)


def _denied() -> Response:
  return _error(401, DENIED)


async def _refused(request: Request, error: Exception) -> Response:
  del request
  refusal = cast(RefusedError, error)
  return _error(refusal.status, refusal.reason)


async def _failed(request: Request, error: Exception) -> Response:
  del request, error
  response = _error(500, "the server failed")
  response.headers.update(HEADERS)
  return response


async def _headers(
  request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
  response = await call_next(request)
  response.headers.update(HEADERS)
  return response


def _error(status: int, reason: str) -> Response:
  names = {
    400: "bad request",
    401: "unauthorized",
    404: "not found",
    413: "too large",
    500: "server error",
    503: "unavailable",
  }
  return JSONResponse(
    {"error": names.get(status, "error"), "reason": reason}, status_code=status
  )
