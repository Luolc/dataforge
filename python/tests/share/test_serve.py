from collections.abc import Generator
import contextlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import cast
import urllib.error
import urllib.request

import pytest

from dataforge.cli import main
from dataforge.share import tokens
from tests.share.sites import make_tree, Tree

WAIT = 10.0
MAIN = (
  "import sys; from dataforge.cli import main; sys.exit(main(sys.argv[1:]))"
)


def free_port() -> int:
  with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    return cast(tuple[str, int], probe.getsockname())[1]


@contextlib.contextmanager
def serving(tree: Tree, port: int, log: Path) -> Generator[str]:
  """Run dataforge share serve in another process, bound to 127.0.0.1.

  Args:
    tree: The site.
    port: The port of its listen.
    log: The file its stdout and stderr go to.

  Yields:
    The site's URL, once it answers; the process is stopped after the block,
    killed when it does not end within WAIT.
  """
  url = f"http://127.0.0.1:{port}"
  with (
    log.open("w") as out,
    subprocess.Popen(
      [
        sys.executable,
        "-c",
        MAIN,
        "share",
        "serve",
        "--config",
        str(tree.site),
      ],
      stdout=out,
      stderr=out,
    ) as process,
  ):
    try:
      deadline = time.monotonic() + WAIT
      while not answers(url):
        assert process.poll() is None, log.read_text()
        assert time.monotonic() < deadline, log.read_text()
        time.sleep(0.05)
      yield url
    finally:
      process.terminate()
      try:
        _ = process.wait(WAIT)
      except subprocess.TimeoutExpired:
        process.kill()
        _ = process.wait(WAIT)


def answers(url: str) -> bool:
  try:
    with urllib.request.urlopen(f"{url}/", timeout=1):
      return True
  except OSError:
    return False


def fetch(
  url: str, data: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str]]:
  request = urllib.request.Request(url, data=data, headers=headers or {})
  try:
    with cast(
      http.client.HTTPResponse, urllib.request.urlopen(request, timeout=WAIT)
    ) as response:
      return response.status, dict(response.headers)
  except urllib.error.HTTPError as error:
    with error:
      return error.code, dict(error.headers)


def test_no_token_and_no_request_reaches_the_log(tmp_path: Path) -> None:
  port = free_port()
  tree = make_tree(tmp_path, port)
  token, _ = tokens.create(tree.tokens, ["example"], "r1")
  secret = token.split("_", 2)[2]
  log = tmp_path / "serve.log"

  with serving(tree, port, log) as url:
    status, headers = fetch(
      f"{url}/api/login",
      json.dumps({"token": token}).encode(),
      {"content-type": "application/json"},
    )
    cookie = headers["set-cookie"].split(";")[0]
    shown = [
      fetch(f"{url}/api/projects/example/items", headers={"cookie": cookie}),
      fetch(f"{url}/api/projects/example/items?token={token}"),
      fetch(f"{url}/?token={token}"),
      fetch(
        f"{url}/api/login",
        json.dumps({"token": token + "x"}).encode(),
        {"content-type": "application/json"},
      ),
    ]
  text = log.read_text()

  assert status == 204
  assert [s for s, _ in shown] == [200, 401, 200, 401]
  assert f"Uvicorn running on {url}" in text
  assert secret not in text
  assert "/api/" not in text
  assert "token=" not in text


@pytest.mark.parametrize("host", ["0.0.0.0", '"::"', "localhost"])
def test_serve_refuses_a_wildcard_or_a_name_before_it_binds(
  tmp_path: Path, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
  tree = make_tree(tmp_path)
  _ = tree.site.write_text(tree.site.read_text().replace("127.0.0.1", host))

  status = main(["share", "serve", "--config", str(tree.site)])

  assert status == 1
  assert "listen.host" in capsys.readouterr().err


def test_serve_refuses_a_missing_token_database_or_frontend(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  tree = make_tree(tmp_path)
  config = ["share", "serve", "--config", str(tree.site)]

  no_frontend = main([*config, "--static", str(tmp_path / "dist")])
  tree.tokens.unlink()
  no_tokens = main(config)

  assert (no_frontend, no_tokens) == (1, 1)
  err = capsys.readouterr().err
  assert "no index.html" in err
  assert "make a token first" in err
