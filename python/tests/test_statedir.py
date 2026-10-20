from pathlib import Path

import pytest

from dataforge.statedir import ENV, state_dir


@pytest.mark.parametrize(
  ("named", "xdg", "found"),
  [
    ("named", "xdg", "named"),
    ("", "xdg", "xdg/dataforge"),
    ("", "relative", "home/.local/state/dataforge"),
    ("", "", "home/.local/state/dataforge"),
  ],
)
def test_the_state_directory_is_named_else_xdgs_else_homes(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  named: str,
  xdg: str,
  found: str,
):
  # A relative XDG_STATE_HOME counts as unset; the directory is made 0700.
  monkeypatch.setenv(ENV, str(tmp_path / named) if named else "")
  absolute = str(tmp_path / xdg) if xdg and xdg != "relative" else xdg
  monkeypatch.setenv("XDG_STATE_HOME", absolute)
  monkeypatch.setenv("HOME", str(tmp_path / "home"))
  path = state_dir()
  assert path == tmp_path / found
  assert path.stat().st_mode & 0o777 == 0o700
