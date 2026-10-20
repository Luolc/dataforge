from collections.abc import Generator
from pathlib import Path
import tempfile

import pytest

from tests.gateway.fakes import Launcher


@pytest.fixture
def socket_dir() -> Generator[Path]:
  # A short path: a Unix socket's path is limited to 107 bytes.
  with tempfile.TemporaryDirectory(prefix="df-gw-test-") as d:
    yield Path(d)


@pytest.fixture
def launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Launcher:
  return Launcher(tmp_path, monkeypatch)
