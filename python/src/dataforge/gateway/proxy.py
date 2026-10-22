"""The host's model proxy: a container's only way to the model.

`serve` runs it as the client of redcoast-client, so that the gateway session
is only in the launcher's environment and this process's. As it starts, it
takes the session out of its environment variables, so that nothing it starts
inherits it; `/proc` still shows the environment it began with, to the host's
processes of the same user, as it does the launcher's. It listens on a Unix
socket that a container reaches through `forward.py`, forwards only its
channel's model calls (`ROUTES`), in origin form, to the gateway with the
session's `Authorization` in place of the request's, and refuses every other
request with 403, the session unsent. It also refuses a model call whose body
asks the model's side to reach the network or run code for the container
(`screen`), and forwards the others' bodies as they came. It ends when the
process that started it ends, killed or not.

With a `Spend`, it prices each reply as it passes it on. Once the session has
cost `max_usd` or a reply could not be priced, and with a ledger once the
ledger could not be written, it refuses every later request with 403, lets the
replies under way finish and then prints `DRAINED`, on which `serve` takes the
socket over and ends the session. With a ledger, it writes each reply down
(`spend.record`).

Run as `python -I -m dataforge.gateway.proxy --channel <channel> --socket
<path> --parent <pid>` with `--pause`, `--ledger` and `--spend` as given.
"""

import argparse
from collections.abc import Generator
import contextlib
import http.client
import http.server
import json
import os
from pathlib import Path
import select
import signal
import socket
import socketserver
import sys
import threading
from typing import cast, final, override
import urllib.parse

from dataforge.gateway import channels, screen
from dataforge.gateway.channels import BILLED, Channel, DRAINED, ROUTES
from dataforge.gateway.pause import Pause
from dataforge.gateway.spend import Meter, NO_USAGE, now, price, record, Spend

# How long the proxy waits for the gateway's answer to one request.
UPSTREAM_TIMEOUT = 600
# Request headers the proxy sets itself, that carry a credential, or that
# belong to one hop only.
_REQUEST_DROP = frozenset(
  {
    "accept-encoding",
    "authorization",
    "chatgpt-account-id",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "x-api-key",
  }
)
_RESPONSE_DROP = frozenset({"connection", "keep-alive", "transfer-encoding"})
# The variables in which redcoast-client hands its client the session.
SESSION_VARIABLES = (
  "ANTHROPIC_BASE_URL",
  "CLAUDE_CODE_OAUTH_TOKEN",
  "CODEX_GATEWAY_TOKEN",
  "HTTPS_PROXY",
  "https_proxy",
)


@final
class _BadRequestError(Exception):
  def __init__(self, status: int):
    super().__init__(status)
    self.status = status


@final
class _Handler(http.server.BaseHTTPRequestHandler):
  server: _Server  # pyright: ignore[reportIncompatibleVariableOverride]

  def _forward(self):
    try:
      body = self._body()
    except _BadRequestError as e:
      self.close_connection = True
      self.send_error(e.status)
      return
    # The body is read even for a refusal: the connection closes after it, and
    # a caller still sending the body would get a broken pipe instead.
    # Only a target in origin form: an absolute or `//host` one would reach
    # the gateway as it came.
    target = urllib.parse.urlsplit(self.path)
    route = (self.command, target.path)
    if (
      not self.path.startswith("/")
      or target.netloc
      or route not in ROUTES[self.server.channel]
    ):
      self.send_error(403)
      return
    channel = self.server.channel
    # All of the header's lines, as the gateway may read them.
    encoding = ",".join(self.headers.get_all("Content-Encoding", []))
    if (refused := screen.refusal(channel, encoding, body)) is not None:
      self._refuse(refused)
      return
    with self.server.flight() as allowed:
      if not allowed:
        self.send_error(403, "the session's spend is capped")
        return
      self._send(body)

  def _refuse(self, refused: screen.Refusal):
    # The tool type or field only: the body is the caller's.
    _ = sys.stderr.write(f"gateway: refused a model call: {refused.why}\n")
    data = screen.answer(self.server.channel, refused)
    self.close_connection = True
    self.send_response(refused.status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Connection", "close")
    self.end_headers()
    _ = self.wfile.write(data)

  def _body(self) -> bytes:
    # The request's body, read whole, chunked or not.
    if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
      return self._chunked()
    try:
      length = int(self.headers.get("Content-Length") or 0)
    except ValueError:
      raise _BadRequestError(400) from None
    if length < 0:
      raise _BadRequestError(400)
    if length > channels.MAX_BODY:
      raise _BadRequestError(413)
    return self.rfile.read(length)

  def _chunked(self) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
      line = self.rfile.readline(1024)
      try:
        length = int(line.split(b";")[0].strip(), 16)
      except ValueError:
        raise _BadRequestError(400) from None
      if length < 0:
        raise _BadRequestError(400)
      if length == 0:
        break
      size += length
      if size > channels.MAX_BODY:
        raise _BadRequestError(413)
      chunk = self.rfile.read(length)
      if len(chunk) < length or self.rfile.readline(1024) != b"\r\n":
        raise _BadRequestError(400)
      chunks.append(chunk)
    # The trailer, which ends with an empty line.
    while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
      pass
    return b"".join(chunks)

  def _send(self, body: bytes):
    server = self.server
    headers = {
      k: v for k, v in self.headers.items() if k.lower() not in _REQUEST_DROP
    }
    headers["Authorization"] = f"Bearer {server.token}"
    headers["Accept-Encoding"] = "identity"
    headers["Content-Length"] = str(len(body))
    up = server.upstream
    while True:
      if server.pause is not None and not server.pause.wait(self._gone):
        return
      connection = (
        http.client.HTTPSConnection
        if up.scheme == "https"
        else http.client.HTTPConnection
      )(up.netloc, timeout=UPSTREAM_TIMEOUT)
      try:
        connection.request(
          self.command, up.path.rstrip("/") + self.path, body, headers
        )
        response = connection.getresponse()
        if server.pause is not None and response.status == 429:
          if server.pause.limited(_retry_after(response)):
            continue
        elif server.pause is not None:
          server.pause.answered()
        self._reply(response)
        return
      finally:
        connection.close()

  def _gone(self) -> bool:
    # Whether the caller closed the connection: it sends nothing more while it
    # waits for the answer, so a readable socket with no data is closed.
    connection = cast(socket.socket, self.connection)
    readable, _, _ = select.select([connection], [], [], 0)
    try:
      return bool(readable) and not connection.recv(1, socket.MSG_PEEK)
    except OSError:
      return True

  def _reply(self, response: http.client.HTTPResponse):
    server = self.server
    meter = server.meter(self.path, response)
    try:
      self.send_response(response.status, response.reason)
      for k, v in response.getheaders():
        if k.lower() not in _RESPONSE_DROP:
          self.send_header(k, v)
      self.end_headers()
      while chunk := response.read1(65536):
        if meter is not None:
          meter.feed(chunk)
        _ = self.wfile.write(chunk)
        self.wfile.flush()
    finally:
      if meter is not None:
        server.charge(meter)

  do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _forward  # noqa: N815
  do_OPTIONS = do_TRACE = do_CONNECT = _forward  # noqa: N815

  @override
  def address_string(self) -> str:
    return "socket"

  @override
  def log_message(self, format: str, *args: object):
    # The request line and status only; headers carry the session.
    _ = sys.stderr.write(f"gateway: {format % args}\n")


def _retry_after(response: http.client.HTTPResponse) -> float | None:
  # The seconds of a `Retry-After`; its date form is taken as none.
  try:
    return float(response.getheader("Retry-After") or "")
  except ValueError:
    return None


@final
class _Server(socketserver.ThreadingUnixStreamServer):
  daemon_threads = True

  def __init__(
    self,
    channel: Channel,
    path: Path,
    base_url: str,
    token: str,
    pause: Pause | None,
    ledger: Path | None,
    spend: Spend | None,
  ):
    self.channel = channel
    self.upstream = urllib.parse.urlsplit(base_url)
    self.token = token
    self.pause = pause
    self.ledger = ledger
    self.spend = spend
    self.spent = 0.0
    self.capped: str | None = None
    self._flying = 0
    self._lock = threading.Lock()
    path.unlink(missing_ok=True)
    super().__init__(str(path), _Handler)
    # A container's user has no CAP_DAC_OVERRIDE, so the socket is open to
    # every user; its directory keeps the host's other users out.
    path.chmod(0o666)

  @contextlib.contextmanager
  def flight(self) -> Generator[bool]:
    # Whether a request may go to the gateway, counted while it does; once
    # the cap has come and the last one ends, the proxy says so.
    with self._lock:
      allowed = self.capped is None
      if allowed:
        self._flying += 1
    try:
      yield allowed
    finally:
      if allowed:
        with self._lock:
          self._flying -= 1
          done = self.capped is not None and self._flying == 0
        if done:
          _ = sys.stderr.write(f"{DRAINED}\n")

  def meter(
    self, path: str, response: http.client.HTTPResponse
  ) -> Meter | None:
    # What reads a reply for the cap and the ledger: a model call's 200.
    if self.spend is None and self.ledger is None:
      return None
    if urllib.parse.urlsplit(path).path != BILLED[self.channel]:
      return None
    if response.status != 200:
      return None
    return Meter(
      self.channel,
      response.getheader("Content-Type"),
      response.getheader("Content-Encoding", "identity"),
    )

  def charge(self, meter: Meter) -> None:
    # Prices a reply that ended, caps the session when it is due and then
    # writes the reply down. The cap fails closed: a reply that cannot be
    # read, and a ledger that cannot be written, cap the session too, so that
    # no reply goes uncounted.
    try:
      meter.end()
      usd, reason = price(meter, self.spend)
    except Exception as e:  # noqa: BLE001
      usd, reason = None, f"the reply could not be read: {e!r}"
    if reason == NO_USAGE:
      # Only the shape of the reply: its Content-Type, event types, counts,
      # the length and top-level keys of its first line, how it was read and
      # the protocol field that line opens with.
      kinds = ",".join(meter.kinds) or "none"
      keys = ",".join(meter.first_keys) if meter.first_keys else "none"
      ctype = "none" if meter.content_type is None else repr(meter.content_type)
      _ = sys.stderr.write(
        f"gateway: {NO_USAGE}: {meter.size} bytes, Content-Type {ctype}, "
        + f"events {kinds}, "
        + f"data lines {meter.data_lines}, first line {meter.first_len} "
        + f"bytes, keys {keys}, "
        + f"read as {meter.mode}, first field {meter.field!r}\n"
      )
    capped = self._settle(usd, reason)
    if self.ledger is None:
      return
    line: dict[str, object] = {
      "at": now(),
      "channel": self.channel.value,
      "model": meter.model,
      "usage": meter.usage,
      "cost_usd": usd,
      "reason": reason,
    }
    try:
      record(self.ledger, line)
      if capped is not None:
        record(
          self.ledger,
          {"at": now(), "channel": self.channel.value, "capped": capped},
        )
    except OSError as e:
      _ = self._cap(f"the ledger could not be written: {e!r}")

  def _settle(self, usd: float | None, reason: str | None) -> str | None:
    # Adds a reply's cost to the session's; returns why it capped the session
    # when it did.
    if self.spend is None:
      return None
    with self._lock:
      if self.capped is not None:
        return None
      if usd is not None:
        self.spent += usd
        if self.spent < self.spend.max_usd:
          return None
    if usd is None:
      return self._cap(f"a reply it could not price: {reason}")
    return self._cap(f"spent {self.spent:.4f} USD of {self.spend.max_usd:.4f}")

  def _cap(self, why: str) -> str | None:
    # Caps the session, unless it was already; returns why when it caps it.
    with self._lock:
      if self.capped is not None:
        return None
      self.capped = why
    _ = sys.stderr.write(f"gateway: the cap ends the session: {why}\n")
    return why


def make_server(
  channel: Channel,
  path: Path,
  base_url: str,
  token: str,
  *,
  pause: Pause | None = None,
  ledger: Path | None = None,
  spend: Spend | None = None,
) -> _Server:
  """Bind the proxy to a Unix socket.

  Args:
    channel: Whose requests it forwards.
    path: The socket, made anew; its directory must exist.
    base_url: The gateway, where requests go.
    token: The session, which replaces each request's `Authorization`.
    pause: The pause a 429 starts and every request waits out, or None to
      pass each answer on as it comes.
    ledger: Where each model call's reply is written down, or None.
    spend: The session's cap, or None.

  Returns:
    The server, not yet serving.
  """
  return _Server(channel, path, base_url, token, pause, ledger, spend)


def session(channel: Channel) -> tuple[str, str]:
  """Take the gateway session out of this process's environment.

  Args:
    channel: Whose session it is.

  Returns:
    The gateway's base URL and the session's token.

  Raises:
    SystemExit: The environment holds no session.
  """
  found = {name: os.environ.pop(name, None) for name in SESSION_VARIABLES}
  if channel is Channel.CLAUDE:
    base_url, token = (
      found["ANTHROPIC_BASE_URL"],
      found["CLAUDE_CODE_OAUTH_TOKEN"],
    )
  else:
    # redcoast-client names the gateway in Pi's models.json, not in a
    # variable; the base URL there ends in /backend-api, which the requests'
    # paths start with.
    models = Path(os.environ["PI_CODING_AGENT_DIR"]) / "models.json"
    found_models = cast(
      dict[str, dict[str, dict[str, str]]], json.loads(models.read_text())
    )
    provider = found_models["providers"]["openai-codex"]
    base_url = provider["baseUrl"].removesuffix("/backend-api")
    token = found["CODEX_GATEWAY_TOKEN"]
  if not base_url or not token:
    raise SystemExit(f"no {channel.value} session in the environment")
  return base_url, token


def _exit(signum: int, frame: object) -> None:
  del signum, frame
  sys.exit(0)


def _end_with(pidfd: int):
  # A process's pidfd turns readable once the process has exited.
  _ = select.select([pidfd], [], [])
  os.kill(os.getpid(), signal.SIGTERM)


def main(argv: list[str] | None = None) -> None:
  """Serve on the session in this process's environment until SIGTERM.

  SIGTERM also comes once the process `--parent` names has exited.

  Args:
    argv: The arguments, else this process's.

  """
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--channel", type=Channel, required=True)
  _ = parser.add_argument("--socket", type=Path, required=True)
  _ = parser.add_argument(
    "--parent", type=int, required=True, help="the PID to end with"
  )
  _ = parser.add_argument("--pause", type=Path, help="a shared pause's file")
  _ = parser.add_argument("--ledger", type=Path, help="the ledger")
  _ = parser.add_argument("--spend", help="the cap, as Spend.to_json gives it")
  args = parser.parse_args(argv)
  channel = cast(Channel, args.channel)
  pause = cast(Path | None, args.pause)
  spend = cast(str | None, args.spend)
  base_url, token = session(channel)
  _ = signal.signal(signal.SIGTERM, _exit)
  # Raises, and so ends the proxy, when the parent has exited already.
  parent = os.pidfd_open(cast(int, args.parent))
  threading.Thread(target=_end_with, args=(parent,), daemon=True).start()
  with make_server(
    channel,
    cast(Path, args.socket),
    base_url,
    token,
    pause=None if pause is None else Pause(pause),
    ledger=cast(Path | None, args.ledger),
    spend=None if spend is None else Spend.from_json(spend),
  ) as server:
    server.serve_forever()


if __name__ == "__main__":
  main()
