"""The dataforge share serve command."""

from pathlib import Path
import sqlite3
import sys

import uvicorn

from dataforge.share import tokens
from dataforge.share.app import create_app
from dataforge.share.config import ConfigError, load_site


def serve(config: Path, static: Path | None) -> int:
  """Start the site and serve until it is stopped.

  It binds only the site file's listen.host and listen.port, and logs no
  request: a request may carry a token, in its cookie or where a person
  pasted it.

  Args:
    config: The site file.
    static: The built frontend; None for a page that says it is not built.

  Returns:
    The exit status: 0 once stopped, 1 when a file fails its check.
  """
  try:
    site = load_site(config)
  except ConfigError as error:
    print(f"dataforge share serve: {error}", file=sys.stderr)
    return 1
  try:
    tokens.readable(site.tokens)
  except sqlite3.Error as error:
    print(
      f"dataforge share serve: {site.tokens}: {error}; make a token first",
      file=sys.stderr,
    )
    return 1
  if static is not None and not (static / "index.html").is_file():
    print(
      f"dataforge share serve: {static}: no index.html; build the frontend",
      file=sys.stderr,
    )
    return 1
  uvicorn.run(
    create_app(site, static),
    host=site.listen.host,
    port=site.listen.port,
    access_log=False,
    proxy_headers=False,
    server_header=False,
  )
  return 0
