"""Where dataforge keeps the state the processes of one machine share."""

import os
from pathlib import Path

ENV = "DATAFORGE_STATE_DIR"
"""The variable that names the state directory, in place of the default."""


def state_dir() -> Path:
  """Return the state directory, made mode 0700 when it is missing.

  It is `DATAFORGE_STATE_DIR` when that is set and not empty, else
  `$XDG_STATE_HOME/dataforge` when `XDG_STATE_HOME` is an absolute path,
  else `~/.local/state/dataforge`.

  Returns:
    The directory.
  """
  if named := os.environ.get(ENV):
    path = Path(named)
  else:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local/state"
    path = base / "dataforge"
  path.mkdir(mode=0o700, parents=True, exist_ok=True)
  return path
