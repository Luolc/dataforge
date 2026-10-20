# pyright: reportAny=false, reportExplicitAny=false
# The tests read the ledger's JSON.

"""The cap and the ledger, against a fake gateway.

Invariant 28: a reply is priced the same as JSON or as an event stream, the
cap refuses every request once reached, after a reply it cannot price or once
the ledger cannot be written, and ends the session, a reply under way is
passed on whole, and each billed reply has one ledger line.
"""

from collections.abc import Callable
import gzip
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import threading
from typing import Any

import pytest

from dataforge.gateway import Channel, Price, proxy, read_ledger, serve, Spend
from dataforge.gateway.channels import DRAINED
from dataforge.gateway.session import holders
from tests.gateway.fakes import (
  call,
  Launcher,
  serving,
  stream,
  upstream,
  wait_for,
)

PRICES = {
  "claude-model": Price(
    input=4.0,
    output=20.0,
    cache_read=0.2,
    cache_write_5m=5.0,
    cache_write_1h=8.0,
  ),
  "codex-model": Price(
    input=1.0,
    output=8.0,
    cache_read=0.1,
    cache_write_5m=1.25,
    cache_write_1h=2.0,
  ),
}
CLAUDE_USAGE = {
  "input_tokens": 1000,
  "output_tokens": 500,
  "cache_read_input_tokens": 2000,
  "cache_creation": {
    "ephemeral_5m_input_tokens": 100,
    "ephemeral_1h_input_tokens": 200,
  },
}
CODEX_USAGE = {
  "input_tokens": 3000,
  "input_tokens_details": {"cached_tokens": 2000, "cache_write_tokens": 500},
  "output_tokens": 400,
  "output_tokens_details": {"reasoning_tokens": 300},
}
# 1000 * 4 + 500 * 20 + 2000 * 0.2 + 100 * 5 + 200 * 8 per million, and
# 500 * 1 + 2000 * 0.1 + 500 * 1.25 + 400 * 8 per million.
COST = {Channel.CLAUDE: 0.0165, Channel.CODEX: 0.004525}
MODEL = {Channel.CLAUDE: "claude-model", Channel.CODEX: "codex-model"}
ROUTE = {
  Channel.CLAUDE: "/v1/messages",
  Channel.CODEX: "/backend-api/codex/responses",
}


def data(event: dict[str, Any]) -> bytes:
  return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def events(channel: Channel) -> list[bytes]:
  # A reply's event stream, its usage in its first and last events.
  if channel is Channel.CODEX:
    response = {"model": MODEL[channel], "usage": CODEX_USAGE}
    return [
      data({"type": "response.created", "response": {"model": MODEL[channel]}}),
      data({"type": "response.output_text.delta", "delta": "ok"}),
      data({"type": "response.completed", "response": response}),
    ]
  first = {**CLAUDE_USAGE, "output_tokens": 1}
  return [
    data(
      {
        "type": "message_start",
        "message": {"model": MODEL[channel], "usage": first},
      }
    ),
    data({"type": "content_block_delta", "delta": {"text": "ok"}}),
    data({"type": "message_delta", "usage": {"output_tokens": 500}}),
  ]


def reply_json(channel: Channel):
  usage = CLAUDE_USAGE if channel is Channel.CLAUDE else CODEX_USAGE
  body = json.dumps({"model": MODEL[channel], "usage": usage}).encode()

  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    del n
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    _ = handler.wfile.write(body)

  return respond


@pytest.mark.parametrize("channel", list(Channel))
@pytest.mark.parametrize("kind", ["stream", "json"])
def test_a_json_reply_is_priced_as_an_event_stream(
  socket_dir: Path, channel: Channel, kind: str
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  respond = (
    stream(*events(channel)) if kind == "stream" else reply_json(channel)
  )
  with (
    upstream(respond) as (base, _),
    proxy.make_server(
      channel,
      path,
      base,
      "t",
      ledger=ledger,
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    status, _ = call(path, ROUTE[channel])
    # A reply with a length can reach the caller whole before its line is
    # written.
    assert wait_for(lambda: bool(read_ledger(ledger)))
    [line] = read_ledger(ledger)
    server.shutdown()
  assert status == 200
  assert line["channel"] == channel.value
  assert line["model"] == MODEL[channel]
  assert line["usage"] == (
    CLAUDE_USAGE if channel is Channel.CLAUDE else CODEX_USAGE
  )
  assert line["cost_usd"] == pytest.approx(COST[channel])
  assert line["reason"] is None


def test_only_a_model_calls_200_has_a_ledger_line(socket_dir: Path):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"

  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    if n == 3:
      handler.send_response(500)
      handler.end_headers()
      return
    stream(*events(Channel.CLAUDE))(handler, n)

  with (
    upstream(respond) as (base, seen),
    proxy.make_server(Channel.CLAUDE, path, base, "t", ledger=ledger) as server,
  ):
    _ = serving(server)
    _ = call(path, "/v1/messages")
    _ = call(path, "/v1/messages/count_tokens")
    _ = call(path, "/v1/messages")
    server.shutdown()
  assert len(seen.requests) == 3
  # Without a cap there are no prices.
  [line] = read_ledger(ledger)
  assert (line["model"], line["cost_usd"], line["reason"]) == (
    MODEL[Channel.CLAUDE],
    None,
    "no prices",
  )


@pytest.mark.parametrize("channel", list(Channel))
def test_the_cap_refuses_every_request_once_reached_and_ends_the_session(
  launcher: Launcher, socket_dir: Path, channel: Channel
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  # One reply costs more than the cap.
  spend = Spend(prices=PRICES, max_usd=COST[channel] / 2)
  with upstream(stream(*events(channel))) as (base, seen):
    launcher.write(base)
    with serve(channel, path, spend=spend, ledger=ledger):
      assert call(path, ROUTE[channel])[0] == 200
      # The proxy, and with it the session, ends.
      assert wait_for(lambda: not holders(path))
      # The socket is the refuser's now, open to the container's user.
      mode = path.stat().st_mode & 0o777
      after = [
        call(path, ROUTE[channel])[0],
        call(path, "/v1/other")[0],
        call(path, "*", method="OPTIONS")[0],
      ]
  assert mode == 0o666
  assert after == [403, 403, 403]
  assert len(seen.requests) == 1
  reply, capped = read_ledger(ledger)
  assert reply["cost_usd"] == pytest.approx(COST[channel])
  assert capped["channel"] == channel.value
  assert "spent" in str(capped["capped"])


def gzipped(*parts: bytes) -> Callable[[BaseHTTPRequestHandler, int], None]:
  body = gzip.compress(b"".join(parts))

  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    del n
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Encoding", "gzip")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    _ = handler.wfile.write(body)

  return respond


@pytest.mark.parametrize(
  ("respond", "reason"),
  [
    (stream(b'data: {"type": "message_start", "message": {}}\n\n'), "without"),
    (
      stream(
        *(
          e.replace(b"claude-model", b"claude-x")
          for e in events(Channel.CLAUDE)
        )
      ),
      "no price for the model 'claude-x'",
    ),
    (gzipped(*events(Channel.CLAUDE)), "Content-Encoding gzip"),
  ],
)
def test_a_reply_it_cannot_price_caps_the_session(
  socket_dir: Path,
  capfd: pytest.CaptureFixture[str],
  respond: Callable[[BaseHTTPRequestHandler, int], None],
  reason: str,
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  with (
    upstream(respond) as (base, seen),
    proxy.make_server(
      Channel.CLAUDE,
      path,
      base,
      "t",
      ledger=ledger,
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    first = call(path, "/v1/messages")[0]
    # A reply with a length can reach the caller whole before it is charged.
    assert wait_for(lambda: server.capped is not None)
    second = call(path, "/v1/messages")[0]
    server.shutdown()
  assert (first, second) == (200, 403)
  assert DRAINED in capfd.readouterr().err
  assert len(seen.requests) == 1
  reply, capped = read_ledger(ledger)
  assert reply["cost_usd"] is None
  assert reason in str(reply["reason"])
  assert "could not price" in str(capped["capped"])


def broken_price(*_: object) -> tuple[float | None, str | None]:
  raise ValueError("unexpected usage")


@pytest.mark.parametrize("broken", ["ledger", "price"])
def test_a_ledger_it_cannot_write_or_a_failure_to_price_caps_the_session(
  socket_dir: Path,
  monkeypatch: pytest.MonkeyPatch,
  capfd: pytest.CaptureFixture[str],
  broken: str,
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  if broken == "ledger":
    ledger = socket_dir / "missing" / "ledger.jsonl"
  else:
    monkeypatch.setattr(proxy, "price", broken_price)
  with (
    upstream(stream(*events(Channel.CLAUDE))) as (base, seen),
    proxy.make_server(
      Channel.CLAUDE,
      path,
      base,
      "t",
      ledger=ledger,
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    first = call(path, "/v1/messages")[0]
    # A reply with a length can reach the caller whole before it is charged.
    assert wait_for(lambda: server.capped is not None)
    second = call(path, "/v1/messages")[0]
    server.shutdown()
  assert (first, second) == (200, 403)
  err = capfd.readouterr().err
  assert DRAINED in err
  assert {
    "ledger": "the ledger could not be written",
    "price": "the reply could not be read",
  }[broken] in err
  assert len(seen.requests) == 1


def test_a_reply_under_way_when_the_cap_comes_is_passed_on_whole(
  socket_dir: Path, capfd: pytest.CaptureFixture[str]
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  parts = events(Channel.CLAUDE)
  release = threading.Event()

  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    for i, part in enumerate(parts):
      if n == 1 and i == len(parts) - 1:
        _ = release.wait(10)
      _ = handler.wfile.write(part)
      handler.wfile.flush()

  slow: list[tuple[int, bytes]] = []
  with (
    upstream(respond) as (base, seen),
    proxy.make_server(
      Channel.CLAUDE,
      path,
      base,
      "t",
      ledger=ledger,
      spend=Spend(prices=PRICES, max_usd=COST[Channel.CLAUDE] / 2),
    ) as server,
  ):
    _ = serving(server)
    first = threading.Thread(
      target=lambda: slow.append(call(path, "/v1/messages"))
    )
    first.start()
    assert wait_for(lambda: len(seen.requests) == 1)
    assert call(path, "/v1/messages")[0] == 200
    refused = call(path, "/v1/messages")[0]
    # Not drained while the first reply is under way.
    assert DRAINED not in capfd.readouterr().err
    release.set()
    first.join(10)
    server.shutdown()
  assert refused == 403
  assert DRAINED in capfd.readouterr().err
  assert slow == [(200, b"".join(parts))]
  assert len(seen.requests) == 2
  lines = read_ledger(ledger)
  assert [sorted(line) for line in lines] == [
    sorted(["at", "channel", "model", "usage", "cost_usd", "reason"]),
    sorted(["at", "channel", "capped"]),
    sorted(["at", "channel", "model", "usage", "cost_usd", "reason"]),
  ]
