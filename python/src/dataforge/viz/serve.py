"""The dataforge viz serve command."""

from pathlib import Path
import sys

import uvicorn

from dataforge.viz.app import create_app
from dataforge.viz.config import ConfigError, load_site


def serve(config: Path, static: Path) -> int:
  """Start the viewer and serve until it is stopped.

  It binds only the site file's listen.host and listen.port.

  Args:
    config: The site file.
    static: The built frontend, the output of npm run build in web/.

  Returns:
    The exit status: 0 once stopped, 1 when a file fails its check.
  """
  try:
    site = load_site(config)
  except ConfigError as error:
    print(f"dataforge viz serve: {error}", file=sys.stderr)
    return 1
  if not (static / "index.html").is_file():
    print(
      f"dataforge viz serve: {static}: no index.html; build web/ first",
      file=sys.stderr,
    )
    return 1
  uvicorn.run(
    create_app(site, static),
    host=site.listen.host,
    port=site.listen.port,
    proxy_headers=False,
    server_header=False,
  )
  return 0
