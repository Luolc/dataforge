# pyright: reportAny=false
# The tests read the launches' JSON.

"""The proxy and `serve` against a fake gateway and a fake launcher.

Invariant 26: where the session goes, what the proxy forwards, and that it
ends with the process that started it.
"""

from compression import zstd
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

from dataforge.gateway import Channel, channels, proxy, screen, serve
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


# Request bodies the pinned clients sent a fake upstream on 2026-10-22, their
# prose, IDs and model replaced by placeholders: Claude Code 2.1.292's turn
# after a tool call and the call its WebSearch makes, Codex CLI 0.159.3's with
# its tools in `additional_tools` and at the top level, the latter with its
# default web search, and Pi 1.2.0's.
REQUESTS = Path(__file__).with_name("requests")


def recorded(name: str) -> bytes:
  return (REQUESTS / f"{name}.json").read_bytes()


def claude(**fields: object) -> bytes:
  body: dict[str, object] = {"model": "m", "max_tokens": 1, "messages": []}
  return json.dumps(body | fields).encode()


def codex(**fields: object) -> bytes:
  return json.dumps({"model": "m", "input": [], **fields}).encode()


def user(*content: object) -> list[object]:
  return [{"role": "user", "content": list(content)}]


def image(url: str) -> dict[str, object]:
  return {"type": "input_image", "image_url": url}


def encoded(sent: bytes, encoding: str | None) -> bytes:
  if encoding is None:
    return sent
  return {"gzip": gzip.compress, "zstd": zstd.compress}[encoding](sent)


def proxied(
  channel: Channel, url: str, sent: bytes, encoding: str | None, socket: Path
) -> tuple[int, bytes, list[bytes]]:
  # The proxy's answer to one call, and the bodies the upstream got.
  headers = {} if encoding is None else {"Content-Encoding": encoding}
  with (
    upstream() as (base, seen),
    proxy.make_server(channel, socket, base, TOKEN) as server,
  ):
    _ = serving(server)
    status, answer = call(socket, url, body=sent, headers=headers)
    server.shutdown()
  return status, answer, [body for *_, body in seen.requests]


@pytest.mark.parametrize(
  ("channel", "url", "sent", "encoding"),
  [
    pytest.param(
      Channel.CLAUDE,
      "/v1/messages",
      recorded("claude-code"),
      None,
      id="claude-code",
    ),
    pytest.param(
      Channel.CLAUDE,
      "/v1/messages/count_tokens",
      recorded("claude-code"),
      None,
      id="claude-code-count-tokens",
    ),
    pytest.param(
      Channel.CODEX, ROUTE[Channel.CODEX], recorded("codex"), None, id="codex"
    ),
    pytest.param(
      Channel.CODEX,
      ROUTE[Channel.CODEX],
      recorded("codex-tool-search"),
      None,
      id="codex-tool-search",
    ),
    # Pi compresses its calls with zstd.
    pytest.param(
      Channel.CODEX,
      ROUTE[Channel.CODEX],
      zstd.compress(recorded("pi")),
      "zstd",
      id="pi",
    ),
  ],
)
def test_the_clients_model_calls_go_as_they_came(
  socket_dir: Path,
  channel: Channel,
  url: str,
  sent: bytes,
  encoding: str | None,
):
  path = socket_dir / "model.sock"
  status, _, got = proxied(channel, url, sent, encoding, path)
  assert status == 200
  assert got == [sent]


@pytest.mark.parametrize(
  ("channel", "name", "why"),
  [
    (
      Channel.CLAUDE,
      "claude-code-web-search",
      "tool type 'web_search_20250305'",
    ),
    (Channel.CODEX, "codex-web-search", "tool type 'web_search'"),
  ],
)
@pytest.mark.parametrize("encoding", [None, "gzip", "zstd"])
def test_a_call_that_asks_the_model_side_to_reach_out_is_refused_unsent(
  socket_dir: Path,
  capfd: pytest.CaptureFixture[str],
  channel: Channel,
  name: str,
  why: str,
  encoding: str | None,
):
  path = socket_dir / "model.sock"
  sent = encoded(recorded(name), encoding)
  status, answer, got = proxied(channel, ROUTE[channel], sent, encoding, path)
  assert status == 400
  error = json.loads(answer)["error"]
  assert error["type"] == "invalid_request_error"
  assert error["message"].startswith(f"the model proxy refuses {why}: ")
  assert got == []
  # The proxy names what it refused, and nothing else of the body.
  err = capfd.readouterr().err
  assert f"gateway: refused a model call: {why}\n" in err
  assert "placeholder" not in err


@pytest.mark.parametrize(
  ("channel", "sent", "why"),
  [
    pytest.param(
      Channel.CLAUDE,
      claude(tools=[{"type": "web_fetch_20260318", "name": "web_fetch"}]),
      "tool type 'web_fetch_20260318'",
      id="web-fetch",
    ),
    pytest.param(
      Channel.CLAUDE,
      claude(tools=[{"type": "code_execution_20260521", "name": "c"}]),
      "tool type 'code_execution_20260521'",
      id="code-execution",
    ),
    pytest.param(
      Channel.CLAUDE,
      claude(tools=[{"type": ["custom"], "name": "c"}]),
      "tool type ['custom']",
      id="type-not-a-string",
    ),
    pytest.param(
      Channel.CLAUDE,
      claude(mcp_servers=[{"type": "url", "url": "https://x", "name": "x"}]),
      "field 'mcp_servers'",
      id="mcp-servers",
    ),
    pytest.param(
      Channel.CLAUDE, claude(container="c"), "field 'container'", id="container"
    ),
    pytest.param(
      Channel.CLAUDE,
      claude(Tools=[{"type": "web_search_20250305", "name": "web_search"}]),
      "tool type 'web_search_20250305'",
      id="key-in-another-case",
    ),
    pytest.param(
      Channel.CLAUDE,
      claude(tools=["web_search"]),
      "tool 'web_search'",
      id="tool-not-an-object",
    ),
    pytest.param(
      Channel.CODEX,
      codex(prompt={"id": "pmpt_x"}),
      "field 'prompt'",
      id="stored-prompt",
    ),
    pytest.param(
      Channel.CLAUDE,
      claude(
        messages=user(
          {
            "type": "tool_result",
            "tool_use_id": "t",
            "content": [
              {"type": "image", "source": {"type": "url", "url": "https://x"}}
            ],
          }
        )
      ),
      "source type 'url'",
      id="image-url",
    ),
    pytest.param(
      Channel.CODEX,
      codex(
        input=[
          {
            "type": "additional_tools",
            "tools": [
              {
                "type": "namespace",
                "name": "n",
                "tools": [{"type": "code_interpreter", "container": "auto"}],
              }
            ],
          }
        ]
      ),
      "tool type 'code_interpreter'",
      id="code-interpreter-in-a-namespace",
    ),
    pytest.param(
      Channel.CODEX,
      codex(tools=[{"type": "mcp", "server_url": "https://x"}]),
      "tool type 'mcp'",
      id="mcp",
    ),
    pytest.param(
      Channel.CODEX,
      codex(tools=[{"type": "tool_search", "execution": "server"}]),
      "tool type 'tool_search' that the client does not execute",
      id="hosted-tool-search",
    ),
    pytest.param(
      Channel.CODEX,
      codex(input=user(image("https://x/a.png"))),
      "field 'image_url' with a URL other than data:",
      id="input-image-url",
    ),
    pytest.param(
      Channel.CODEX,
      codex(input=user({"type": "input_file", "file_url": "https://x/a.pdf"})),
      "field 'file_url' with a URL other than data:",
      id="input-file-url",
    ),
  ],
)
def test_the_proxy_refuses_what_has_the_model_side_reach_out(
  channel: Channel, sent: bytes, why: str
):
  assert screen.refusal(channel, None, sent) == screen.Refusal(400, why, True)


@pytest.mark.parametrize(
  ("channel", "sent"),
  [
    pytest.param(
      Channel.CLAUDE,
      claude(
        messages=user(
          {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": ""},
          },
          {
            "type": "tool_use",
            "id": "t",
            "name": "c",
            "input": {"tools": [{"type": "web_search"}]},
          },
        ),
        tools=[
          {"type": "custom", "name": "c", "input_schema": {"type": "object"}}
        ],
      ),
      id="claude",
    ),
    pytest.param(
      Channel.CODEX,
      codex(input=user(image("data:image/png;base64,"))),
      id="codex",
    ),
  ],
)
def test_the_proxy_passes_what_stays_with_the_client(
  channel: Channel, sent: bytes
):
  # A base64 image, a data: URL, and a tool call's input, which is the
  # model's own.
  assert screen.refusal(channel, None, sent) is None


@pytest.mark.parametrize(
  ("encoding", "sent", "status", "why"),
  [
    ("br", b"{}", 415, "Content-Encoding 'br'"),
    ("gzip", b"{}", 400, "a body that is not gzip"),
    ("zstd", zstd.compress(b"{}")[:-2], 400, "a truncated body"),
    ("gzip", gzip.compress(b"{}" + b" " * 999), 413, "a body past 64 bytes"),
    (None, b"{", 400, "a body that is not UTF-8 JSON"),
    (None, b'"tools"', 400, "a body that is not a JSON object"),
    (
      None,
      b'{"tools": [{"type": "web_search"}], "tools": []}',
      400,
      "a JSON object with a duplicate key",
    ),
    (
      None,
      b'{"tools": [], "Tools": [{"type": "web_search"}]}',
      400,
      "a JSON object with a duplicate key",
    ),
  ],
)
def test_a_body_the_proxy_cannot_read_is_refused(
  monkeypatch: pytest.MonkeyPatch,
  encoding: str | None,
  sent: bytes,
  status: int,
  why: str,
):
  monkeypatch.setattr(channels, "MAX_BODY", 64)
  found = screen.refusal(Channel.CODEX, encoding, sent)
  assert found == screen.Refusal(status, why, False)


def test_every_line_of_content_encoding_comes_off(
  socket_dir: Path, capfd: pytest.CaptureFixture[str]
):
  path = socket_dir / "model.sock"
  sent = gzip.compress(gzip.compress(recorded("claude-code-web-search")))
  with (
    upstream() as (base, seen),
    proxy.make_server(Channel.CLAUDE, path, base, TOKEN) as server,
  ):
    _ = serving(server)
    with socket.socket(socket.AF_UNIX) as s:
      s.connect(str(path))
      head = [
        b"POST /v1/messages HTTP/1.1",
        b"Host: x",
        b"Content-Length: %d" % len(sent),
        b"Content-Encoding: gzip",
        b"Content-Encoding: gzip",
      ]
      s.sendall(b"\r\n".join(head) + b"\r\n\r\n" + sent)
      reply = s.makefile("rb").readline()
    server.shutdown()
  assert reply.startswith(b"HTTP/1.0 400 ")
  assert seen.requests == []
  why = "tool type 'web_search_20250305'"
  assert f"gateway: refused a model call: {why}\n" in capfd.readouterr().err


def test_a_body_the_proxy_cannot_read_is_answered_unsent(socket_dir: Path):
  path = socket_dir / "model.sock"
  status, answer, got = proxied(
    Channel.CLAUDE, "/v1/messages", b"{}", "br", path
  )
  assert status == 415
  assert json.loads(answer) == {
    "type": "error",
    "error": {
      "type": "invalid_request_error",
      "message": "the model proxy refuses Content-Encoding 'br'",
    },
  }
  assert got == []


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
