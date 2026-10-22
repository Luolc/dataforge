"""Run pytest with the project's directories added once its plugins are loaded.

The Pytest adapter runs this file, not a module of the package, as `python -P
pytest_main.py <plan> <pytest's arguments>`, with PYTHONPATH holding only
its entries outside the project. <plan> is a JSON object: `paths`, the
directory pytest starts in, then every entry of PYTHONPATH as an absolute
path, in its order; and `pythonpath`, PYTHONPATH as it was, or null when it
was not set, which the tests' own processes get back. So pytest, its
plugins and what they import come from the interpreter's installation and
the entries outside the project, never from a file of the project of the
same name. When pytest starts loading the project's conftest files, the
paths go back where `python -m pytest` would have had them, after the
configuration's pythonpath, which pytest itself would otherwise add before
loading its plugins. Unlike the rest of the package, it imports pytest, and
`-P` needs Python 3.11; nothing of the package imports it.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
import json
import os
import sys
from typing import cast, final

import pytest

# pytest 9 adds the configuration's pythonpath from this private method,
# before the plugins named with -p and those of installed packages are loaded.
# pytest 8.3, which has no such method, adds it in its own
# pytest_load_initial_conftests, after _Late's has begun.
_ADD = "_configure_python_path"


@final
class _Late:
  # Puts the paths on sys.path before the first conftest is loaded.

  def __init__(
    self, paths: list[str], anchor: str | None, add: Callable[..., None] | None
  ) -> None:
    self._paths = paths
    self._anchor = anchor
    self._add = add

  @pytest.hookimpl(wrapper=True)
  def pytest_load_initial_conftests(
    self, early_config: pytest.Config
  ) -> Generator[None]:
    rest = [p for p in sys.path if p not in self._paths]
    at = rest.index(self._anchor) if self._anchor in rest else len(rest)
    sys.path[:] = [*rest[:at], *self._paths, *rest[at:]]
    if self._add is not None:
      self._add(early_config)
    return (yield)


def _skip(_: pytest.Config) -> None:
  pass


def main() -> int:
  """Run pytest on the arguments after <plan>.

  Returns:
    pytest's exit status.
  """
  plan = cast("dict[str, object]", json.loads(sys.argv[1]))
  paths = cast("list[str]", plan["paths"])
  pythonpath = cast("str | None", plan["pythonpath"])
  if pythonpath is None:
    _ = os.environ.pop("PYTHONPATH", None)
  else:
    os.environ["PYTHONPATH"] = pythonpath
  args = sys.argv[2:]
  sys.argv[1:] = args
  # Where the paths go: before what came after PYTHONPATH's entries.
  anchor = next((p for p in sys.path if p not in paths), None)
  add = cast("Callable[..., None] | None", getattr(pytest.Config, _ADD, None))
  if add is not None:
    setattr(pytest.Config, _ADD, _skip)
  code = pytest.main(args, plugins=[_Late(paths, anchor, add)])
  _ = sys.stdout.flush()
  return int(code)


if __name__ == "__main__":
  sys.exit(main())
