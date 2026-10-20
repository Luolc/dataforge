from pathlib import Path

import pytest

from dataforge.statedir import ENV


@pytest.fixture(autouse=True)
def state(
  tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
  # Each test's own state directory, which the processes it starts inherit,
  # so that no test shares the machine's.
  path = tmp_path_factory.mktemp("state")
  monkeypatch.setenv(ENV, str(path))
  return path
