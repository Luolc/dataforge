# pyright: reportAny=false
# The tests read the launches' JSON.

"""The proxy and `serve` against a fake gateway and a fake launcher.

Invariant 26: where the session goes, what the proxy forwards, and that it
ends with the process that started it.
"""

import gzip
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from dataforge import stopping
from dataforge.gateway import Channel, channels, proxy, serve
from dataforge.gateway.session import holders
from tests.gateway.fakes import (
  alive,
  call,
  Launcher,
  serving,
  TOKEN,
  upstream,
)

ROUTE = {
  Channel.CLAUDE: "/v1/messages",
  Channel.CODEX: "/backend-api/codex/responses",
}
PREFIX = {
  Channel.CLAUDE: "REDCOAST_CLIENT_",
  Channel.CODEX: "REDCOAST_CLIENT_CODEX_",
}
CLIENT = {
  Channel.CLAUDE: "REDCOAST_CLIENT_CLAUDE",
  Channel.CODEX: "REDCOAST_CLIENT_PI",
}


def cmdline(pid: int) -> bytes:
  return Path(f"/proc/{pid}/cmdline").read_bytes()


@pytest.mark.parametrize("channel", list(Channel))
def test_a_session_is_only_in_the_launcher_and_the_proxy(
  launcher: Launcher,
  socket_dir: Path,
  capfd: pytest.CaptureFixture[str],
  channel: Channel,
):
  path = socket_dir / "missing" / "model.sock"
  with upstream() as (base, seen):
    launcher.write(base)
    with serve(channel, path, ledger=socket_dir / "ledger.jsonl") as found:
      assert call(found, ROUTE[channel]) == (200, b"data: one\n\ndata: two\n\n")
      lines = [cmdline(pid) for pid in holders(path)]
      assert path.stat().st_mode & 0o777 == 0o666
      assert path.parent.stat().st_mode & 0o777 == 0o700
  [launch] = launcher.launches()
  prefix = PREFIX[channel]
  # The fake launcher's Python sets LC_CTYPE in its own environment.
  _ = launch["env"].pop("LC_CTYPE", None)
  assert sorted(launch["env"]) == sorted(
    [
      "HOME",
      "PATH",
      f"{prefix}ADDRESS",
      f"{prefix}CREDENTIAL_FILE",
      CLIENT[channel],
    ]
  )
  address, credential = {
    Channel.CLAUDE: (
      "claude-gateway:1",
      f"{Path.home()}/.config/redcoast-client/machine-credential",
    ),
    Channel.CODEX: ("codex-gateway:2", f"{Path.home()}/codex-credential"),
  }[channel]
  assert launch["env"][f"{prefix}ADDRESS"] == address
  assert launch["env"][f"{prefix}CREDENTIAL_FILE"] == credential
  assert launch["env"][CLIENT[channel]] == sys.executable
  # The session went to the gateway, and nowhere else.
  [(_, _, headers, _)] = seen.requests
  assert headers["Authorization"] == f"Bearer {TOKEN}"
  assert lines
  assert not any(TOKEN.encode() in line for line in lines)
  out = capfd.readouterr()
  assert TOKEN not in out.out + out.err
  assert TOKEN not in json.dumps(dict(os.environ))
  assert not any(
    TOKEN.encode() in f.read_bytes()
    for f in socket_dir.rglob("*")
    if f.is_file()
  )


@pytest.mark.parametrize("channel", list(Channel))
def test_the_proxy_takes_the_session_out_of_the_environment_it_passes_on(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: Channel
):
  # Out of `os.environ`, and so out of what its children inherit; `/proc`
  # still shows the environment the process began with.
  monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://claude-gateway")
  monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
  monkeypatch.setenv("CODEX_GATEWAY_TOKEN", TOKEN)
  monkeypatch.setenv("HTTPS_PROXY", f"http://session:{TOKEN}@proxy:1")
  monkeypatch.setenv("https_proxy", f"http://session:{TOKEN}@proxy:1")
  _ = (tmp_path / "models.json").write_text(
    json.dumps(
      {
        "providers": {
          "openai-codex": {"baseUrl": "http://codex-gateway/backend-api"}
        }
      }
    )
  )
  monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
  base_url, token = proxy.session(channel)
  assert token == TOKEN
  assert (
    base_url
    == {
      Channel.CLAUDE: "http://claude-gateway",
      Channel.CODEX: "http://codex-gateway",
    }[channel]
  )
  assert TOKEN not in json.dumps(dict(os.environ))


@pytest.mark.parametrize("channel", list(Channel))
def test_the_proxy_forwards_only_its_channels_model_calls(
  socket_dir: Path, channel: Channel
):
  path = socket_dir / "model.sock"
  other = ROUTE[Channel.CODEX if channel is Channel.CLAUDE else Channel.CLAUDE]
  with (
    upstream() as (base, seen),
    proxy.make_server(channel, path, f"{base}/gw", TOKEN) as server,
  ):
    _ = serving(server)
    answer = call(
      path,
      f"{ROUTE[channel]}?beta=true",
      body=b'{"model": "m"}',
      headers={
        "Authorization": "Bearer placeholder",
        "X-Api-Key": "placeholder",
        "Chatgpt-Account-Id": "placeholder",
        "Anthropic-Beta": "oauth-2025-04-20",
        "Accept-Encoding": "gzip, br",
      },
    )
    refused = [
      call(path, ROUTE[channel], method="GET")[0],
      call(path, "/v1/files")[0],
      call(path, other)[0],
      call(path, f"{ROUTE[channel]}/../files")[0],
      call(path, f"http://elsewhere.invalid{ROUTE[channel]}")[0],
      call(path, f"//elsewhere.invalid{ROUTE[channel]}")[0],
      call(path, "*", method="OPTIONS")[0],
    ]
    server.shutdown()
  assert answer == (200, b"data: one\n\ndata: two\n\n")
  assert refused == [403] * 7
  [(method, url, headers, body)] = seen.requests
  assert (method, url) == ("POST", f"/gw{ROUTE[channel]}?beta=true")
  assert headers["Authorization"] == f"Bearer {TOKEN}"
  assert "X-Api-Key" not in headers
  assert "Chatgpt-Account-Id" not in headers
  assert headers["Anthropic-Beta"] == "oauth-2025-04-20"
  assert headers["Accept-Encoding"] == "identity"
  assert body == b'{"model": "m"}'


def chunked(parts: list[bytes]) -> bytes:
  return b"".join(b"%x\r\n%s\r\n" % (len(p), p) for p in parts) + b"0\r\n\r\n"


def test_a_compressed_chunked_body_is_sent_whole_as_it_came(socket_dir: Path):
  path = socket_dir / "model.sock"
  body = gzip.compress(b'{"model": "gpt", "input": "' + b"x" * 5000 + b'"}')
  parts = [body[:100], body[100:]]
  with (
    upstream() as (base, seen),
    proxy.make_server(Channel.CODEX, path, base, TOKEN) as server,
  ):
    _ = serving(server)
    with socket.socket(socket.AF_UNIX) as s:
      s.connect(str(path))
      head = [
        b"POST /backend-api/codex/responses HTTP/1.1",
        b"Host: x",
        b"Transfer-Encoding: chunked",
        b"Content-Encoding: gzip",
        b"Originator: pi",
      ]
      s.sendall(b"\r\n".join(head) + b"\r\n\r\n" + chunked(parts))
      reply = s.makefile("rb").readline()
    server.shutdown()
  assert reply.startswith(b"HTTP/1.0 200 ")
  [(_, _, headers, sent)] = seen.requests
  assert sent == body
  assert headers["Content-Length"] == str(len(body))
  assert headers["Content-Encoding"] == "gzip"
  assert "Transfer-Encoding" not in headers


@pytest.mark.parametrize("framing", ["chunked", "length"])
def test_a_body_past_the_limit_is_refused_unsent(
  socket_dir: Path, monkeypatch: pytest.MonkeyPatch, framing: str
):
  monkeypatch.setattr(channels, "MAX_BODY", 10)
  path = socket_dir / "model.sock"
  with (
    upstream() as (base, seen),
    proxy.make_server(Channel.CLAUDE, path, base, TOKEN) as server,
  ):
    _ = serving(server)
    with socket.socket(socket.AF_UNIX) as s:
      s.connect(str(path))
      head = [b"POST /v1/messages HTTP/1.1", b"Host: x"]
      if framing == "chunked":
        head.append(b"Transfer-Encoding: chunked")
        body = chunked([b"12345"] * 3)
      else:
        head.append(b"Content-Length: 11")
        body = b"x" * 11
      s.sendall(b"\r\n".join(head) + b"\r\n\r\n" + body)
      reply = s.makefile("rb").readline()
    server.shutdown()
  assert reply.startswith(b"HTTP/1.0 413 ")
  assert seen.requests == []


# Runs `serve` with the fake launcher and config, says when the proxy
# listens, and waits to be killed.
PARENT = """
import sys, time
from pathlib import Path
from dataforge.gateway import Channel, serve, session
session.LAUNCHER, session.CONFIG_DIR = Path(sys.argv[1]), Path(sys.argv[2])
with serve(Channel.CLAUDE, Path(sys.argv[3])):
  print("listening", flush=True)
  time.sleep(600)
"""


def test_the_proxy_ends_when_the_process_that_started_it_is_killed(
  launcher: Launcher, socket_dir: Path
):
  path = socket_dir / "model.sock"
  config = launcher.root / "config"
  with subprocess.Popen(
    [sys.executable, "-c", PARENT, str(launcher.path), str(config), str(path)],
    stdout=subprocess.PIPE,
  ) as parent:
    try:
      assert parent.stdout is not None
      assert parent.stdout.readline() == b"listening\n"
      # The parent's own command line names the socket too.
      [pid] = [p for p in holders(path) if p != parent.pid]
      parent.kill()
      _ = parent.wait()
      deadline = time.monotonic() + 10
      while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
      assert not alive(pid)
    finally:
      parent.kill()
      for pid in holders(path):
        os.kill(pid, signal.SIGKILL)


def test_serve_raises_when_a_process_is_left_behind(
  launcher: Launcher, socket_dir: Path
):
  path = socket_dir / "model.sock"
  launcher.write("http://127.0.0.1:9", straggler=True)
  with (
    pytest.raises(RuntimeError, match="left processes"),
    serve(Channel.CLAUDE, path),
  ):
    pass
  deadline = time.monotonic() + 5
  while holders(path) and time.monotonic() < deadline:
    time.sleep(0.1)
  assert holders(path) == []


def test_a_stopped_fan_outs_session_ends_within_the_stops_budget(
  launcher: Launcher, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
):
  # A launcher that ignores SIGTERM gets END_S, or what is left of the budget.
  monkeypatch.setattr(stopping, "BUDGET_S", 2.0)
  launcher.write("http://127.0.0.1:9", deaf=True)
  path = socket_dir / "model.sock"
  stop = stopping.Stop()

  def run() -> float:
    with serve(Channel.CLAUDE, path):
      stop.set()
      return time.monotonic()

  stopped = stopping.under(stop, run)()
  assert time.monotonic() - stopped < stopping.BUDGET_S + 1
  assert holders(path) == []
