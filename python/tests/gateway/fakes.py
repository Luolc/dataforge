# pyright: reportAny=false, reportExplicitAny=false
# The fakes read and write JSON of their own.

"""Fakes for the gateway's tests: a gateway upstream and a launcher."""

from collections.abc import Callable, Generator
import contextlib
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import sys
import tarfile
import threading
import time
from typing import Any, final, override

import pytest

from dataforge.gateway import Client, session

TOKEN = "session-token-of-the-fake-gateway"


@final
class Seen:
  # What the upstream got: each request's method, path, headers and body.

  def __init__(self):
    self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
    self.lock = threading.Lock()


type Respond = Callable[[BaseHTTPRequestHandler, int], None]


def stream(*events: bytes) -> Respond:
  """Return a responder that streams events in a 200.

  Args:
    *events: The body's parts, each written and flushed in turn.

  Returns:
    The responder.
  """

  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    del n
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    for event in events:
      _ = handler.wfile.write(event)
      handler.wfile.flush()

  return respond


REPLY = stream(b"data: one\n\n", b"data: two\n\n")


@contextlib.contextmanager
def upstream(respond: Respond = REPLY) -> Generator[tuple[str, Seen]]:
  """Run a gateway stand-in that records each request and answers it.

  Args:
    respond: Answers a request, given its handler and its number from 1.

  Yields:
    Its base URL and what it got.
  """
  seen = Seen()

  @final
  class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
      body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
      with seen.lock:
        seen.requests.append(
          (self.command, self.path, dict(self.headers.items()), body)
        )
        n = len(seen.requests)
      respond(self, n)

    @override
    def log_message(self, format: str, *args: Any):
      pass

  httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
  threading.Thread(target=httpd.serve_forever, daemon=True).start()
  try:
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
  finally:
    httpd.shutdown()
    httpd.server_close()


@final
class UnixConnection(http.client.HTTPConnection):
  # An HTTP connection over a Unix socket.

  def __init__(self, path: Path, timeout: float = 30):
    super().__init__("localhost", timeout=timeout)
    self.unix = path

  @override
  def connect(self):
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(self.timeout)
    sock.connect(str(self.unix))
    self.sock = sock


def call(
  path: Path,
  url: str,
  body: bytes = b"{}",
  headers: dict[str, str] | None = None,
  method: str = "POST",
) -> tuple[int, bytes]:
  """Make one request on a socket.

  Args:
    path: The socket.
    url: The request's path.
    body: Its body.
    headers: Its headers.
    method: Its method.

  Returns:
    The answer's status and body.
  """
  connection = UnixConnection(path)
  try:
    connection.request(method, url, body=body, headers=headers or {})
    response = connection.getresponse()
    return response.status, response.read()
  finally:
    connection.close()


def serving(server: Any) -> threading.Thread:
  """Serve a proxy made with make_server in a thread.

  Args:
    server: The proxy.

  Returns:
    The thread.
  """
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  return thread


# The launcher's part, as redcoast-client plays it: it writes each launch to
# the log, refuses the first `refuse` launches by printing `line` and exiting
# with `code`, and then runs its client with the session in its environment,
# in its own process. With `straggler`, it first starts a process that names
# the socket on its command line and ignores SIGTERM; with `deaf`, it ignores
# SIGTERM itself and runs its client as a child.
LAUNCHER = """\
#!{python}
import json, os, pathlib, signal, subprocess, sys, time
cfg = json.loads({cfg!r})
log = pathlib.Path(cfg["log"])
with log.open("a") as f:
    f.write(json.dumps({{"argv": sys.argv, "env": dict(os.environ),
                        "at": time.time()}}) + "\\n")
if len(log.read_text().splitlines()) <= cfg["refuse"]:
    sys.stderr.write(cfg["line"] + "\\n")
    sys.exit(cfg["code"])
client, args = sys.argv[1], sys.argv[2:]
env = dict(os.environ)
proxy = "http://session:" + cfg["token"] + "@forward-proxy:1"
env.update(HTTPS_PROXY=proxy, https_proxy=proxy)
if client == "claude":
    env.update(ANTHROPIC_BASE_URL=cfg["upstream"],
               CLAUDE_CODE_OAUTH_TOKEN=cfg["token"])
    exe = env["REDCOAST_CLIENT_CLAUDE"]
else:
    d = pathlib.Path(cfg["pi_dir"])
    d.mkdir(exist_ok=True)
    provider = {{"baseUrl": cfg["upstream"] + "/backend-api",
                "apiKey": "${{CODEX_GATEWAY_TOKEN}}"}}
    (d / "models.json").write_text(
        json.dumps({{"providers": {{"openai-codex": provider}}}}))
    env.update(PI_CODING_AGENT_DIR=str(d), PI_OFFLINE="1",
               CODEX_GATEWAY_TOKEN=cfg["token"])
    exe = env["REDCOAST_CLIENT_PI"]
if cfg["straggler"]:
    socket = args[args.index("--socket") + 1]
    subprocess.Popen([sys.executable, "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        " time.sleep(600)", socket], start_new_session=True,
        stderr=subprocess.DEVNULL)
if cfg["deaf"]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.exit(subprocess.call([exe, *args], env=env))
os.execve(exe, [exe, *args], env)
"""
QUOTA = " ".join(
  [
    "redcoast: local gateway quota exhausted, every account is over its hard",
    "threshold or rate-limited",
  ]
)


@final
class Launcher:
  # A fake redcoast-client and the machine's gateway config, in place.

  def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch):
    self.root = root
    self.path = root / "redcoast-client"
    self.log = root / "launches.jsonl"
    config = root / "config"
    config.mkdir()
    shim = [
      "# the claude shim's config",
      "address=claude-gateway:1",
      "credential_file=$HOME/.config/redcoast-client/machine-credential",
      "claude=$HOME/.local/bin/claude",
      "prefix=",
    ]
    _ = (config / "shim.env").write_text("\n".join(shim) + "\n")
    _ = (config / "codex.env").write_text(
      "address=codex-gateway:2\ncredential_file=$HOME/codex-credential\n"
    )
    monkeypatch.setattr(session, "LAUNCHER", self.path)
    monkeypatch.setattr(session, "CONFIG_DIR", config)
    self.write("http://127.0.0.1:9")

  def write(
    self,
    upstream: str,
    *,
    refuse: int = 0,
    line: str = QUOTA,
    code: int = 3,
    straggler: bool = False,
    deaf: bool = False,
  ) -> None:
    """Write the launcher.

    Args:
      upstream: The gateway it gives its client.
      refuse: How many launches it refuses.
      line: What it prints when it refuses.
      code: Its exit status when it refuses.
      straggler: Whether it leaves a process behind.
      deaf: Whether it ignores SIGTERM.
    """
    cfg = {
      "log": str(self.log),
      "upstream": upstream,
      "token": TOKEN,
      "pi_dir": str(self.root / "pi"),
      "refuse": refuse,
      "line": line,
      "code": code,
      "straggler": straggler,
      "deaf": deaf,
    }
    _ = self.path.write_text(
      LAUNCHER.format(python=sys.executable, cfg=json.dumps(cfg))
    )
    self.path.chmod(0o755)

  def launches(self) -> list[dict[str, Any]]:
    """Return each launch's argv, environment and time.

    Returns:
      The launches, in order.
    """
    if not self.log.exists():
      return []
    return [json.loads(line) for line in self.log.read_text().splitlines()]


def alive(pid: int) -> bool:
  """Return whether a process exists and is not a zombie.

  Args:
    pid: The process.

  Returns:
    Whether it runs.
  """
  try:
    stat = Path(f"/proc/{pid}/stat").read_text()
  except OSError:
    return False
  return stat.rsplit(")", 1)[1].split()[0] != "Z"


def wait_for(found: Callable[[], bool], timeout: float = 10) -> bool:
  """Wait until something holds.

  Args:
    found: Whether it holds.
    timeout: How long to wait, in seconds.

  Returns:
    Whether it held in time.
  """
  deadline = time.monotonic() + timeout
  while not found():
    if time.monotonic() > deadline:
      return False
    time.sleep(0.05)
  return True


def _add(
  tar: tarfile.TarFile, info: tarfile.TarInfo, data: bytes = b""
) -> None:
  info.size = len(data)
  tar.addfile(info, io.BytesIO(data) if data else None)


def file(
  name: str, data: bytes, mode: int = 0o644
) -> tuple[tarfile.TarInfo, bytes]:
  info = tarfile.TarInfo(name)
  info.mode = mode
  return info, data


def tarball(
  path: Path,
  members: list[tuple[tarfile.TarInfo, bytes]],
  version: str = "1.0",
  program: str = "pi/pi",
) -> Client:
  with tarfile.open(path, "w:gz") as tar:
    for info, data in members:
      _add(tar, info, data)
  digest = hashlib.sha256(path.read_bytes()).hexdigest()
  return Client(path, version, digest, program)


def directory(name: str) -> tuple[tarfile.TarInfo, bytes]:
  info = tarfile.TarInfo(name)
  info.type = tarfile.DIRTYPE
  info.mode = 0o755
  return info, b""


def package(tmp_path: Path) -> Client:
  return tarball(
    tmp_path / "pi.tar.gz",
    [
      directory("pi"),
      file("pi/pi", b"#!/bin/sh\necho 1.0\n", 0o755),
      directory("pi/assets"),
      file("pi/assets/theme.json", b"{}"),
    ],
  )


def executable(tmp_path: Path) -> Client:
  path = tmp_path / "claude"
  _ = path.write_text("#!/bin/sh\necho '1.0 (Claude Code)'\n")
  return Client(path, "1.0", hashlib.sha256(path.read_bytes()).hexdigest())
