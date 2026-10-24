from collections.abc import Generator
import http.server
import os
from pathlib import Path
import shutil
import struct
import tempfile
import threading
from typing import override

import pytest

from dataforge.cli import main
from dataforge.viz import screenshot

# A synthetic viewer: a page that applies the template kept in localStorage
# as the real one does, shows "Loading…" until its content lands, then a tab
# bar and, for the Report tab, a column of two panels, the shown one marked
# aria-current="page".
PAGE = (Path(__file__).parent / "fixtures" / "viewer-page.html").read_text()

CHROME_MISSING = not Path(screenshot.CHROME).exists() and not os.environ.get(
  "CI"
)
pytestmark = pytest.mark.skipif(
  CHROME_MISSING, reason="no Google Chrome on this machine"
)


def serve(applied: str) -> Generator[str]:
  body = PAGE.replace("__APPLIED__", applied).encode()

  class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
      self.send_response(200)
      self.send_header("Content-Type", "text/html; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      _ = self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:
      pass

  server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
  thread = threading.Thread(target=server.serve_forever)
  thread.start()
  try:
    yield f"http://127.0.0.1:{server.server_port}"
  finally:
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def viewer() -> Generator[str]:
  # Applies the template the reader chose.
  yield from serve("saved")


@pytest.fixture
def stubborn() -> Generator[str]:
  # Applies the light template whatever the reader chose.
  yield from serve('"ac"')


def size(png: bytes) -> tuple[int, int]:
  assert png[:8] == b"\x89PNG\r\n\x1a\n"
  width, height = struct.unpack(">II", png[16:24])
  return width, height


def run(base: str, out: Path, *extra: str, theme: str = "dark") -> int:
  return main(
    [
      "viz",
      "screenshot",
      "--url",
      base,
      "--project",
      "example",
      "--attempt",
      "a",
      "--theme",
      theme,
      "--out",
      str(out),
      *extra,
    ]
  )


def left(root: Path) -> list[str]:
  # The processes whose command line names a directory under root: the
  # Chrome this test started, whose profile is in a directory made there.
  found: list[str] = []
  for entry in Path("/proc").iterdir():
    if entry.name.isdigit() and entry.name != str(os.getpid()):
      try:
        line = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
      except OSError:
        continue
      if str(root) in line:
        found.append(line)
  return found


@pytest.fixture(autouse=True)
def own_tmp(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
  # Chrome's profile is made under a directory of the test's, so that what is
  # left of it can be counted; short, since Chrome puts a socket in it.
  root = Path(tempfile.mkdtemp(prefix="shot-"))
  monkeypatch.setenv("TMPDIR", str(root))
  monkeypatch.setattr("tempfile.tempdir", None)
  try:
    yield root
  finally:
    shutil.rmtree(root, ignore_errors=True)


def test_a_shot_of_a_panel_in_each_theme_is_a_png_of_the_width(
  viewer: str, tmp_path: Path, own_tmp: Path, capfd: pytest.CaptureFixture[str]
) -> None:
  dark = tmp_path / "dark.png"
  light = tmp_path / "light.png"
  panel = ("--tab", "report", "--panel", "details", "--width", "900")

  assert run(viewer, dark, *panel, "--height", "500") == 0
  assert run(viewer, light, *panel, "--height", "500", theme="light") == 0

  assert size(dark.read_bytes()) == (900, 500)
  assert size(light.read_bytes()) == (900, 500)
  assert dark.read_bytes() != light.read_bytes()
  assert str(dark) in capfd.readouterr().out
  assert left(own_tmp) == []


def test_a_tab_without_a_panel_is_shot_with_its_first_panel(
  viewer: str, tmp_path: Path
) -> None:
  out = tmp_path / "runs.png"

  assert run(viewer, out, theme="light") == 0
  assert out.exists()


def test_a_theme_the_page_did_not_apply_fails_and_writes_nothing(
  stubborn: str,
  tmp_path: Path,
  capfd: pytest.CaptureFixture[str],
  own_tmp: Path,
) -> None:
  out = tmp_path / "dark.png"

  assert run(stubborn, out) == 1

  assert "applied the template 'ac', not 'dark'" in capfd.readouterr().err
  assert not out.exists()
  assert left(own_tmp) == []


def test_a_panel_that_does_not_exist_fails_and_writes_nothing(
  viewer: str, tmp_path: Path, capfd: pytest.CaptureFixture[str], own_tmp: Path
) -> None:
  out = tmp_path / "missing.png"

  assert run(viewer, out, "--tab", "report", "--panel", "nope") == 1

  assert "no panel 'nope'" in capfd.readouterr().err
  assert not out.exists()
  assert left(own_tmp) == []


def test_a_tab_that_does_not_exist_fails_and_writes_nothing(
  viewer: str, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
  out = tmp_path / "missing.png"

  assert run(viewer, out, "--tab", "nope") == 1

  assert "no tab 'nope'" in capfd.readouterr().err
  assert not out.exists()


def test_a_viewer_that_cannot_be_reached_fails(
  tmp_path: Path, capfd: pytest.CaptureFixture[str], own_tmp: Path
) -> None:
  out = tmp_path / "none.png"

  assert run("http://127.0.0.1:1", out) == 1

  assert "dataforge viz screenshot:" in capfd.readouterr().err
  assert not out.exists()
  assert left(own_tmp) == []


def test_a_width_that_is_not_positive_is_refused(tmp_path: Path) -> None:
  assert run("http://127.0.0.1:1", tmp_path / "x.png", "--width", "0") == 2
