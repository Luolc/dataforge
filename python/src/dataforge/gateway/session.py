"""One gateway session on a Unix socket: `serve`.

`serve` starts redcoast-client as the launcher, `redcoast-client claude` for
`CLAUDE` or `redcoast-client pi` for `CODEX`, with HOME, PATH and the
launcher's own variables only, and has it start this interpreter as its
client, running the proxy (`dataforge.gateway.proxy`). The launcher asks the
gateway for a session and hands it to the proxy in its environment, so that
the session is only in those two processes; nothing writes it to disk. The
session ends when the proxy does, and the proxy when this process does.

The launcher reads the machine's credential from a file and the gateway's
address from its variables; both come from the `address=` and
`credential_file=` lines of `~/.config/redcoast-client/shim.env` (`CLAUDE`) or
of `codex.env` beside it (`CODEX`), read as the claude shim reads them.
"""

from collections.abc import Generator
import contextlib
import http.server
import os
from pathlib import Path
import signal
import socket as sockets
import socketserver
import subprocess
import sys
import threading
import time
from typing import final, IO, override

from dataforge.gateway.channels import Channel, DRAINED, MAX_BODY
from dataforge.gateway.pause import (
  active,
  check_cut,
  LAUNCHER_FAILURE,
  Pause,
  QUOTA_REFUSAL,
  QuotaError,
)
from dataforge.gateway.pause import (
  slots as channel_slots,
)
from dataforge.gateway.spend import Spend

LAUNCHER = (
  Path.home() / ".local/libexec/redcoast-client/current/redcoast-client"
)
CONFIG_DIR = Path.home() / ".config/redcoast-client"
# The file of each channel's address and credential file.
CONFIG = {Channel.CLAUDE: "shim.env", Channel.CODEX: "codex.env"}
# What the launcher is told to start, and the variables it reads, by channel.
_LAUNCH = {
  Channel.CLAUDE: ("claude", "REDCOAST_CLIENT_", "REDCOAST_CLIENT_CLAUDE"),
  Channel.CODEX: ("pi", "REDCOAST_CLIENT_CODEX_", "REDCOAST_CLIENT_PI"),
}
# The proxy's module, which the launcher's client runs.
PROXY = "dataforge.gateway.proxy"
# How long the launcher gets to end its client and the session on SIGTERM.
END_S = 30.0


def read_config(path: Path) -> dict[str, str]:
  """Read a file of the claude shim's format.

  `key=value` lines, read as data: blank lines and lines that start with `#`
  are skipped, and a value may start with `$HOME`, nothing else expanded.

  Args:
    path: The file.

  Returns:
    Its keys and values.

  Raises:
    ValueError: A line has no `=` or a `$` other than a leading `$HOME`.
  """
  found: dict[str, str] = {}
  for line in path.read_text().splitlines():
    if not line or line.startswith("#"):
      continue
    key, sep, value = line.partition("=")
    if value == "$HOME" or value.startswith("$HOME/"):
      value = str(Path.home()) + value.removeprefix("$HOME")
    if not sep or "$" in key + value:
      raise ValueError(f"cannot parse {line!r} in {path}")
    found[key] = value
  return found


def gateway(channel: Channel, address: str | None = None) -> tuple[str, str]:
  """Return a channel's gateway address and the machine's credential file.

  Args:
    channel: The channel.
    address: The address to use instead of the channel's file's; the
      credential file then comes from the channel's file when it names one,
      else from `shim.env`.

  Returns:
    The address and the credential file's path.

  Raises:
    ValueError: No file names the address or the credential file.
  """
  files = [CONFIG_DIR / CONFIG[channel]]
  if address is not None and channel is not Channel.CLAUDE:
    files.append(CONFIG_DIR / CONFIG[Channel.CLAUDE])
  if address is None:
    found = read_config(files[0])
    address = found.get("address")
    credential = found.get("credential_file")
  else:
    credential = None
    for path in files:
      if path.is_file():
        credential = read_config(path).get("credential_file")
      if credential:
        break
  if not address:
    raise ValueError(f"{files[0]} gives no address")
  if not credential:
    raise ValueError(f"{' or '.join(map(str, files))} gives no credential_file")
  return address, credential


@contextlib.contextmanager
def serve(
  channel: Channel,
  socket: Path,
  *,
  address: str | None = None,
  spend: Spend | None = None,
  ledger: Path | None = None,
  timeout: float = 60.0,
) -> Generator[Path]:
  """Serve one gateway session on a Unix socket for the length of the block.

  The socket is made anew with mode 0666, so that a container's user without
  CAP_DAC_OVERRIDE can connect; its directory, made mode 0700 when it is
  missing and never removed, keeps the host's other users out. The proxy runs
  in a session of its own, out of reach of a Ctrl-C at the terminal; the
  launcher's and the proxy's stderr are copied to this process's line by line.

  Inside `sessions`, it first waits for a free slot of its channel. Inside
  `pausing`, it shares its channel's pause: it starts once no pause is under
  way, and when the gateway refuses the session for its quota, the refusal
  pauses the channel and the proxy starts again once the pause is over.

  With `spend`, once the cap ends the session, this process answers every
  request on the socket with 403 until the block ends; for a moment while it
  takes the socket over, a caller cannot connect instead.

  Args:
    channel: The channel.
    socket: Where the proxy listens.
    address: The gateway's address, instead of the channel's file's.
    spend: The session's cap, or None.
    ledger: Where the proxy writes down each model call's reply, or None.
    timeout: How long to wait for the socket.

  Yields:
    The socket, once it accepts connections.

  Raises:
    QuotaError: The gateway refused the session for its quota outside
      `pausing`; the pause stopped; or the block's calls were paused `CUT_S`
      or longer and the pause is still under way as it ends.
    RuntimeError: The proxy stopped before it listened, did not listen in
      time, or left a process behind.
  """  # noqa: DOC502
  slots = channel_slots(channel)
  if slots is not None:
    _ = slots.acquire()
  try:
    with _serving(channel, socket, address, spend, ledger, timeout) as path:
      yield path
  finally:
    if slots is not None:
      slots.release()


@contextlib.contextmanager
def _serving(
  channel: Channel,
  socket: Path,
  address: str | None,
  spend: Spend | None,
  ledger: Path | None,
  timeout: float,
) -> Generator[Path]:
  if not socket.parent.exists():
    socket.parent.mkdir(mode=0o700, parents=True)
  pause = active(channel)
  launch = _Launch(channel, socket, address, spend, ledger, pause)
  launch.start(timeout)
  before = 0.0 if pause is None else pause.paused()
  try:
    yield socket
    if pause is not None:
      check_cut(pause, pause.paused() - before)
  finally:
    launch.end()


@final
class _Launch:
  # The launcher and its proxy, started again after each quota refusal the
  # pause takes, and what answers once the cap ended the session.

  def __init__(
    self,
    channel: Channel,
    socket: Path,
    address: str | None,
    spend: Spend | None,
    ledger: Path | None,
    pause: Pause | None,
  ):
    self.channel = channel
    self.socket = socket
    self.address = address
    self.spend = spend
    self.ledger = ledger
    self.pause = pause
    self.process: subprocess.Popen[bytes] | None = None
    self.stderr: _Stderr | None = None
    self._refuser: _Refuser | None = None
    self._ended = threading.Event()
    self._watch: threading.Thread | None = None

  def start(self, timeout: float) -> None:
    while True:
      if self.pause is not None:
        _ = self.pause.wait()
        if (why := self.pause.stopped()) is not None:
          raise QuotaError(why)
      process, stderr = self._launch()
      self.process, self.stderr = process, stderr
      try:
        listening = _listening(process, self.socket, timeout)
      except BaseException:
        self.end()
        raise
      if listening:
        self._watch = threading.Thread(target=self._watching, daemon=True)
        self._watch.start()
        return
      self.end()
      refusal = (
        stderr.refusal if process.returncode == LAUNCHER_FAILURE else None
      )
      if refusal is None:
        raise RuntimeError(f"model proxy exited with {process.returncode}")
      if self.pause is None:
        raise QuotaError(refusal)
      if not self.pause.refused(refusal):
        raise QuotaError(self.pause.stopped() or refusal)
      self._ended.clear()

  def _launch(self) -> tuple[subprocess.Popen[bytes], _Stderr]:
    address, credential = gateway(self.channel, self.address)
    client, prefix, executable = _LAUNCH[self.channel]
    env = {
      "HOME": str(Path.home()),
      "PATH": "/usr/local/bin:/usr/bin:/bin",
      f"{prefix}ADDRESS": address,
      f"{prefix}CREDENTIAL_FILE": credential,
      executable: sys.executable,
    }
    # The launcher runs the client with the arguments after its name: this
    # interpreter, running the proxy.
    args = [
      str(LAUNCHER), client,
      "-I", "-m", PROXY,
      "--channel", self.channel.value,
      "--socket", str(self.socket),
      "--parent", str(os.getpid()),
    ]  # fmt: skip
    if self.pause is not None:
      args += ["--pause", str(self.pause.path)]
    if self.ledger is not None:
      args += ["--ledger", str(self.ledger.resolve())]
    if self.spend is not None:
      args += ["--spend", self.spend.to_json()]
    process = subprocess.Popen(
      args,
      env=env,
      cwd=self.socket.parent,
      stdin=subprocess.DEVNULL,
      stderr=subprocess.PIPE,
      start_new_session=True,
    )
    assert process.stderr is not None
    return process, _Stderr(process.stderr)

  def _watching(self) -> None:
    # Once the cap came and no reply is under way, this process takes the
    # socket over, so that what comes next is refused, and ends the launcher,
    # and with it the session. While it takes the socket over, for a moment,
    # a caller cannot connect instead of getting 403; the cap stays closed.
    assert self.stderr is not None
    while not self._ended.is_set():
      if self.stderr.drained.wait(0.1):
        self._refuser = _Refuser(self.socket)
        self._stop()
        return

  def end(self) -> None:
    # Ends the launcher and its proxy, waits for their last lines of stderr,
    # so that they come before what follows them, and kills what they left.
    self._ended.set()
    if self._watch is not None:
      self._watch.join()
      self._watch = None
    if self._refuser is not None:
      self._refuser.close()
      self._refuser = None
    if self.process is None:
      return
    self._stop()
    if self.stderr is not None:
      self.stderr.join(5)
    self.socket.unlink(missing_ok=True)
    if left := holders(self.socket):
      for pid in left:
        with contextlib.suppress(ProcessLookupError):
          os.kill(pid, signal.SIGKILL)
      raise RuntimeError(f"model proxy left processes {left}, killed")

  def _stop(self) -> None:
    process = self.process
    if process is None or process.poll() is not None:
      return
    # The launcher passes SIGTERM to its client and then ends the session.
    process.send_signal(signal.SIGTERM)
    try:
      _ = process.wait(timeout=END_S)
    except subprocess.TimeoutExpired:
      os.killpg(process.pid, signal.SIGKILL)
      _ = process.wait()


@final
class _Stderr(threading.Thread):
  # Copies a launcher's stderr, which its proxy shares, to this process's line
  # by line, and keeps the quota refusal it finds.

  def __init__(self, stream: IO[bytes]):
    super().__init__(daemon=True)
    self.stream = stream
    self.refusal: str | None = None
    self.drained = threading.Event()
    self.start()

  @override
  def run(self):
    with self.stream:
      for line in self.stream:
        text = line.decode(errors="replace")
        _ = sys.stderr.write(text)
        if text.startswith(QUOTA_REFUSAL):
          self.refusal = text.strip()
        elif text.startswith(DRAINED):
          self.drained.set()


@final
class _Refusing(http.server.BaseHTTPRequestHandler):
  # Refuses every request with 403, its body read first, as the proxy does.

  def _refuse(self):
    with contextlib.suppress(ValueError, OSError):
      length = int(self.headers.get("Content-Length") or 0)
      _ = self.rfile.read(min(max(length, 0), MAX_BODY))
    self.send_error(403, "the session's spend is capped")

  do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _refuse  # noqa: N815
  do_OPTIONS = do_TRACE = do_CONNECT = _refuse  # noqa: N815

  @override
  def address_string(self) -> str:
    return "socket"

  @override
  def log_message(self, format: str, *args: object):
    _ = sys.stderr.write(f"gateway: {format % args}\n")


@final
class _Refuser:
  # Answers on a socket once the cap ended its session, in a thread of this
  # process, which holds no session.

  def __init__(self, path: Path):
    # Bound under another name and renamed over the old socket, so that the
    # path is never without a socket that answers.
    new = path.with_name(f"{path.name}.refuser")
    new.unlink(missing_ok=True)
    self.server = socketserver.ThreadingUnixStreamServer(str(new), _Refusing)
    self.server.daemon_threads = True
    new.chmod(0o666)
    _ = new.rename(path)
    self._thread = threading.Thread(
      target=self.server.serve_forever, daemon=True
    )
    self._thread.start()

  def close(self) -> None:
    self.server.shutdown()
    self._thread.join()
    self.server.server_close()


def holders(socket: Path) -> list[int]:
  """Return the processes whose command line names a socket.

  Args:
    socket: The socket.

  Returns:
    Their PIDs, this process's left out: the launcher and the proxy.
  """
  pids: list[int] = []
  for entry in Path("/proc").iterdir():
    if not entry.name.isdigit() or int(entry.name) == os.getpid():
      continue
    with contextlib.suppress(OSError):
      args = (entry / "cmdline").read_bytes().split(b"\0")
      if os.fsencode(socket) in args:
        pids.append(int(entry.name))
  return pids


def _connectable(path: Path) -> bool:
  with sockets.socket(sockets.AF_UNIX) as s:
    try:
      s.connect(str(path))
    except OSError:
      return False
  return True


def _listening(
  process: subprocess.Popen[bytes], socket: Path, timeout: float
) -> bool:
  # True once the proxy listens, False once the launcher exited first.
  deadline = time.monotonic() + timeout
  while not _connectable(socket):
    if process.poll() is not None:
      return False
    if time.monotonic() > deadline:
      raise RuntimeError(f"model proxy not listening after {timeout}s")
    time.sleep(0.2)
  return True
