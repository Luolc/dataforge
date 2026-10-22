# pyright: reportPrivateUsage=false
# The refuser test builds the session module's private refuser.
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
import socket as sockets
import socketserver
import threading
from typing import Any

import pytest

from dataforge.gateway import Channel, Price, proxy, read_ledger, serve, Spend
from dataforge.gateway.channels import DRAINED
from dataforge.gateway.session import _Refuser, _Refusing, holders
from dataforge.gateway.spend import Meter, Total, total
from dataforge.gateway.spend import PRICES as GATEWAY_PRICES
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


@pytest.mark.parametrize(
  "end", ["response.completed", "response.done", "response.incomplete"]
)
def test_a_codex_stream_is_priced_from_any_event_that_ends_it(
  socket_dir: Path, end: str
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  response = {"model": MODEL[Channel.CODEX], "usage": CODEX_USAGE}
  respond = stream(
    data({"type": "response.output_text.delta", "delta": "ok"}),
    data({"type": end, "response": response}),
  )
  with (
    upstream(respond) as (base, _),
    proxy.make_server(
      Channel.CODEX,
      path,
      base,
      "t",
      ledger=ledger,
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    _ = call(path, ROUTE[Channel.CODEX])
    assert wait_for(lambda: bool(read_ledger(ledger)))
    [line] = read_ledger(ledger)
    server.shutdown()
  assert line["cost_usd"] == pytest.approx(COST[Channel.CODEX])
  assert line["reason"] is None


def test_a_reply_without_usage_logs_its_event_types_and_size_not_its_content(
  socket_dir: Path, capfd: pytest.CaptureFixture[str]
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  respond = stream(
    data({"type": "response.created", "response": {"model": "m"}}),
    data({"type": "response.output_text.delta", "delta": "SECRET-WORDS"}),
    data({"type": "response.output_text.delta", "delta": "more"}),
  )
  with (
    upstream(respond) as (base, _),
    proxy.make_server(
      Channel.CODEX,
      path,
      base,
      "t",
      ledger=ledger,
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    _ = call(path, ROUTE[Channel.CODEX])
    assert wait_for(lambda: server.capped is not None)
    server.shutdown()
  err = capfd.readouterr().err
  [line] = [x for x in err.splitlines() if "without usage:" in x]
  assert "response.created,response.output_text.delta" in line
  assert "bytes" in line
  assert "SECRET-WORDS" not in err
  [reply, _] = read_ledger(ledger)
  assert reply["reason"] == "a reply without usage"


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


def bare(
  *parts: bytes, content_type: str | None = None
) -> Callable[[BaseHTTPRequestHandler, int], None]:
  # A stream the gateway sends without a Content-Type, as the Codex channel's,
  # or with the one given.
  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    del n
    handler.send_response(200)
    if content_type is not None:
      handler.send_header("Content-Type", content_type)
    handler.end_headers()
    for part in parts:
      _ = handler.wfile.write(part)
      handler.wfile.flush()

  return respond


@pytest.mark.parametrize("channel", list(Channel))
def test_a_stream_without_a_content_type_is_priced_as_a_stream(
  launcher: Launcher, socket_dir: Path, channel: Channel
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  spend = Spend(prices=PRICES, max_usd=1.0)
  with upstream(bare(*events(channel))) as (base, _):
    launcher.write(base)
    with serve(channel, path, spend=spend, ledger=ledger):
      assert call(path, ROUTE[channel])[0] == 200
  [line] = read_ledger(ledger)
  assert line["model"] == MODEL[channel]
  assert line["cost_usd"] == pytest.approx(COST[channel])
  assert line["reason"] is None


def split(body: bytes, sizes: tuple[int, ...]) -> list[bytes]:
  # The same reply cut into chunks of the given sizes, the rest in the last.
  chunks: list[bytes] = []
  for size in sizes:
    chunks.append(body[:size])
    body = body[size:]
  return [*chunks, body]


def read_bare(
  channel: Channel,
  body: bytes,
  sizes: tuple[int, ...],
  content_type: str | None = None,
) -> Meter:
  meter = Meter(channel, content_type)
  for chunk in split(body, sizes):
    meter.feed(chunk)
  meter.end()
  return meter


SPLITS = [(), (1,), (2, 1), (1, 1, 1, 7), (5, 40)]
OPENINGS = [
  b": keepalive\n\n",
  b"id: 7\nretry: 1000\n",
  b"\xef\xbb\xbf",
  b"event: x\n",
]


@pytest.mark.parametrize("sizes", SPLITS)
@pytest.mark.parametrize("opening", OPENINGS)
@pytest.mark.parametrize("channel", list(Channel))
def test_a_stream_without_a_content_type_is_read_whatever_it_opens_with(
  channel: Channel, opening: bytes, sizes: tuple[int, ...]
):
  body = opening + b"".join(events(channel))
  meter = read_bare(channel, body, sizes)
  assert meter.model == MODEL[channel]
  assert meter.mode == "stream"
  assert meter.usage != {}
  assert meter.usage == read_bare(channel, body, ()).usage


@pytest.mark.parametrize("sizes", SPLITS)
@pytest.mark.parametrize(
  "content_type",
  ["application/json", "text/plain", "Text/Event-Stream", "text/event-stream"],
)
@pytest.mark.parametrize("channel", list(Channel))
def test_a_stream_is_read_by_its_events_whatever_its_content_type(
  channel: Channel, content_type: str, sizes: tuple[int, ...]
):
  body = b": hi\r\n" + b"".join(events(channel)).replace(b"\n", b"\r\n")
  meter = read_bare(channel, body, sizes, content_type)
  assert meter.mode == "stream"
  assert meter.model == MODEL[channel]
  assert meter.usage != {}
  assert meter.usage == read_bare(channel, body, ()).usage


@pytest.mark.parametrize("sizes", SPLITS)
def test_a_stream_with_crlf_lines_and_an_event_across_chunks_is_read(
  sizes: tuple[int, ...],
):
  body = b"".join(events(Channel.CODEX)).replace(b"\n", b"\r\n")
  meter = read_bare(Channel.CODEX, b": hi\r\n" + body, sizes)
  assert meter.usage == CODEX_USAGE


# A reply seen once opened, to a first byte that is `{`, with a line that is
# JSON and no field of an event stream, and went on as one with usage.
JSON_LINES = [
  b'{"type": "x", "payload": {"a": 1}}\n',
  b' \r\n{"id": "r", "object": "x"}\r\n\r\n',
]


@pytest.mark.parametrize("sizes", SPLITS)
@pytest.mark.parametrize("opening", JSON_LINES)
@pytest.mark.parametrize("channel", list(Channel))
def test_a_stream_that_opens_with_a_line_of_json_is_read_by_its_events(
  channel: Channel, opening: bytes, sizes: tuple[int, ...]
):
  body = opening + b"".join(events(channel))
  meter = read_bare(channel, body, sizes)
  assert meter.mode == "stream"
  assert meter.model == MODEL[channel]
  assert meter.usage != {}
  assert meter.usage == read_bare(channel, body, ()).usage


@pytest.mark.parametrize("sizes", SPLITS)
def test_a_pretty_printed_json_reply_without_data_lines_is_read_as_json(
  sizes: tuple[int, ...],
):
  body = json.dumps({"model": "m", "usage": CODEX_USAGE}, indent=2).encode()
  meter = read_bare(Channel.CODEX, body, sizes)
  assert (meter.mode, meter.usage) == ("json", CODEX_USAGE)
  assert meter.data_lines == 0


def test_a_reply_that_opens_with_a_line_of_json_is_priced_through_the_proxy(
  launcher: Launcher, socket_dir: Path
):
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  opening = JSON_LINES[0]
  with upstream(bare(opening, *events(Channel.CODEX))) as (base, _):
    launcher.write(base)
    with serve(
      Channel.CODEX,
      path,
      spend=Spend(prices=PRICES, max_usd=1.0),
      ledger=ledger,
    ):
      assert call(path, ROUTE[Channel.CODEX])[0] == 200
  [line] = read_ledger(ledger)
  assert line["cost_usd"] == pytest.approx(COST[Channel.CODEX])
  assert line["reason"] is None


def test_the_diagnosis_of_a_reply_without_usage_counts_data_lines_names_keys(
  socket_dir: Path, capfd: pytest.CaptureFixture[str]
):
  path = socket_dir / "model.sock"
  opening = b'{"type": "x", "secret key": "SECRET", "id": 1}\n'
  respond = bare(opening, data({"type": "y", "delta": "SECRET"}))
  with (
    upstream(respond) as (base, _),
    proxy.make_server(
      Channel.CODEX,
      path,
      base,
      "t",
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    _ = call(path, ROUTE[Channel.CODEX])
    assert wait_for(lambda: server.capped is not None)
    server.shutdown()
  err = capfd.readouterr().err
  [line] = [x for x in err.splitlines() if "without usage:" in x]
  assert "events y, data lines 1, " in line
  assert f"first line {len(opening) - 1} bytes, keys type,?,id, " in line
  assert "SECRET" not in err


@pytest.mark.parametrize(
  ("content_type", "shown"),
  [
    ("application/json; charset=utf-8", "'application/json; charset=utf-8'"),
    (None, "none"),
  ],
)
def test_the_diagnosis_of_a_reply_without_usage_gives_its_content_type(
  socket_dir: Path,
  capfd: pytest.CaptureFixture[str],
  content_type: str | None,
  shown: str,
):
  path = socket_dir / "model.sock"
  respond = bare(
    data({"type": "y", "delta": "SECRET"}), content_type=content_type
  )
  with (
    upstream(respond) as (base, _),
    proxy.make_server(
      Channel.CODEX,
      path,
      base,
      "t",
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    _ = call(path, ROUTE[Channel.CODEX])
    assert wait_for(lambda: server.capped is not None)
    server.shutdown()
  err = capfd.readouterr().err
  [line] = [x for x in err.splitlines() if "without usage:" in x]
  assert f" bytes, Content-Type {shown}, events y, " in line
  assert "SECRET" not in err


@pytest.mark.parametrize("sizes", SPLITS)
@pytest.mark.parametrize("lead", [b"", b" \n"])
def test_a_json_reply_without_a_content_type_is_read_as_json(
  lead: bytes, sizes: tuple[int, ...]
):
  body = lead + json.dumps({"model": "m", "usage": CODEX_USAGE}).encode()
  meter = read_bare(Channel.CODEX, body, sizes)
  assert (meter.mode, meter.usage) == ("json", CODEX_USAGE)


def test_a_reply_without_usage_names_its_first_field_and_how_it_was_read(
  socket_dir: Path, capfd: pytest.CaptureFixture[str]
):
  path = socket_dir / "model.sock"
  respond = bare(b": SECRET-WORDS\n\n", data({"type": "x", "delta": "SECRET"}))
  with (
    upstream(respond) as (base, _),
    proxy.make_server(
      Channel.CODEX,
      path,
      base,
      "t",
      spend=Spend(prices=PRICES, max_usd=10.0),
    ) as server,
  ):
    _ = serving(server)
    _ = call(path, ROUTE[Channel.CODEX])
    assert wait_for(lambda: server.capped is not None)
    server.shutdown()
  err = capfd.readouterr().err
  [line] = [x for x in err.splitlines() if "without usage:" in x]
  assert line.endswith("read as stream, first field ''")
  assert "SECRET" not in err


def test_the_refuser_leaves_the_path_answering_while_it_takes_it_over(
  tmp_path: Path,
):
  path = tmp_path / "model.sock"
  missed = threading.Event()
  done = threading.Event()

  def poll():
    while not done.is_set():
      with sockets.socket(sockets.AF_UNIX) as s:
        try:
          s.connect(str(path))
        except OSError:
          missed.set()

  for _ in range(12):
    old = socketserver.ThreadingUnixStreamServer(str(path), _Refusing)
    serving_old = threading.Thread(target=old.serve_forever, daemon=True)
    serving_old.start()
    thread = threading.Thread(target=poll)
    thread.start()
    refuser = _Refuser(path)
    done.set()
    thread.join()
    done.clear()
    refuser.close()
    old.shutdown()
    old.server_close()
    path.unlink()
  assert not missed.is_set()


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


def broken_price(*_: object) -> tuple[float | None, str | None, str | None]:
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
  reply = [
    "at",
    "channel",
    "model",
    "requested_model",
    "usage",
    "cost_usd",
    "priced_as",
    "reason",
  ]
  assert [sorted(line) for line in lines] == [
    sorted(reply),
    sorted(["at", "channel", "capped"]),
    sorted(reply),
  ]


def renamed(channel: Channel) -> list[bytes]:
  # A reply's event stream that names a model no price is given for.
  return [
    e.replace(MODEL[channel].encode(), b"renamed") for e in events(channel)
  ]


JSON = {"Content-Type": "application/json"}
FALLBACK_PRICE = Price(
  input=10.0,
  output=10.0,
  cache_read=10.0,
  cache_write_5m=10.0,
  cache_write_1h=10.0,
)


def priced_line(
  socket_dir: Path,
  respond: Callable[[BaseHTTPRequestHandler, int], None],
  spend: Spend | None,
  body: bytes,
  headers: dict[str, str],
) -> tuple[dict[str, object], bytes, str | None]:
  # The ledger line of one model call, what the gateway got of its body and
  # whether the cap ended the session.
  path = socket_dir / "model.sock"
  ledger = socket_dir / "ledger.jsonl"
  with (
    upstream(respond) as (base, seen),
    proxy.make_server(
      Channel.CLAUDE, path, base, "t", ledger=ledger, spend=spend
    ) as server,
  ):
    _ = serving(server)
    assert call(path, "/v1/messages", body, headers)[0] == 200
    assert wait_for(lambda: bool(read_ledger(ledger)))
    server.shutdown()
  [line] = [found for found in read_ledger(ledger) if "capped" not in found]
  return line, seen.requests[0][3], server.capped


def test_a_reply_under_another_name_is_priced_by_the_model_requested(
  socket_dir: Path,
):
  line, _, capped = priced_line(
    socket_dir,
    stream(*renamed(Channel.CLAUDE)),
    Spend(prices=PRICES, max_usd=10.0),
    b'{"model": "claude-model", "messages": []}',
    JSON,
  )
  assert capped is None
  assert (line["model"], line["requested_model"]) == ("renamed", "claude-model")
  assert line["cost_usd"] == pytest.approx(COST[Channel.CLAUDE])
  assert (line["priced_as"], line["reason"]) == ("requested", None)


def test_a_reply_no_price_fits_is_priced_at_the_fallback(socket_dir: Path):
  spend = Spend(prices=PRICES, max_usd=10.0, fallback=FALLBACK_PRICE)
  assert Spend.from_json(spend.to_json()) == spend
  line, _, capped = priced_line(
    socket_dir,
    stream(*renamed(Channel.CLAUDE)),
    spend,
    b'{"model": "unknown"}',
    JSON,
  )
  assert capped is None
  # 3800 tokens in all at $10 per million.
  assert line["cost_usd"] == pytest.approx(0.038)
  assert line["priced_as"] == "fallback"


@pytest.mark.parametrize(
  ("body", "headers"),
  [
    pytest.param(b"not json", JSON, id="not-json"),
    pytest.param(b'{"model": "claude-model"}', {}, id="no-json-type"),
    pytest.param(
      gzip.compress(b'{"model": "claude-model"}'),
      JSON | {"Content-Encoding": "gzip"},
      id="compressed",
    ),
    pytest.param(b'["claude-model"]', JSON, id="not-an-object"),
    pytest.param(b'{"model": 1}', JSON, id="not-a-name"),
  ],
)
def test_a_request_whose_model_it_cannot_read_is_forwarded_as_it_came(
  socket_dir: Path, body: bytes, headers: dict[str, str]
):
  line, got, _ = priced_line(
    socket_dir, stream(*events(Channel.CLAUDE)), None, body, headers
  )
  assert got == body
  assert line["requested_model"] is None
  assert line["model"] == MODEL[Channel.CLAUDE]


def test_a_request_larger_than_it_reads_names_no_model(
  socket_dir: Path, monkeypatch: pytest.MonkeyPatch
):
  body = b'{"model": "claude-model"}'
  monkeypatch.setattr(proxy, "REQUEST_READ", len(body) - 1)
  line, got, _ = priced_line(
    socket_dir, stream(*events(Channel.CLAUDE)), None, body, JSON
  )
  assert got == body
  assert line["requested_model"] is None


def ledger_of(path: Path, *lines: object) -> Path:
  _ = path.write_text(
    "".join(
      (line if isinstance(line, str) else json.dumps(line)) + "\n"
      for line in lines
    )
  )
  return path


def test_total_prices_each_line_as_its_ledger_has_it(tmp_path: Path):
  fast = CLAUDE_USAGE | {"speed": "fast"}
  searched = CLAUDE_USAGE | {"server_tool_use": {"web_search_requests": 2}}
  ledger = ledger_of(
    tmp_path / "spend.jsonl",
    # An earlier proxy's lines: their own cost, one at its highest price.
    {"at": "t", "model": "claude-model", "usage": CLAUDE_USAGE, "usd": 1.0},
    {"at": "t", "model": "x", "priced_as": "highest", "usage": {}, "usd": 2.0},
    # A line the cap priced keeps its cost.
    {"channel": "CLAUDE", "model": "claude-model", "usage": CLAUDE_USAGE,
     "cost_usd": 0.5, "priced_as": None, "reason": None},
    # Lines without prices are priced by their reply's model, else by the
    # model requested, else at the fallback, as is a reply at another speed.
    {"channel": "CLAUDE", "model": "claude-model", "usage": CLAUDE_USAGE,
     "cost_usd": None, "reason": "no prices"},
    {"channel": "CODEX", "model": "codex-model", "usage": CODEX_USAGE,
     "cost_usd": None, "reason": "no prices"},
    {"channel": "CLAUDE", "model": "renamed", "requested_model": "claude-model",
     "usage": CLAUDE_USAGE, "cost_usd": None, "reason": "no prices"},
    {"model": "renamed", "requested_model": None, "usage": CLAUDE_USAGE},
    {"channel": "CLAUDE", "model": "claude-model", "usage": fast},
    {"channel": "CLAUDE", "model": "claude-model", "usage": searched},
    # Nothing to count.
    {"channel": "CLAUDE", "model": None, "usage": {}, "cost_usd": None,
     "reason": "a reply without usage"},
    {"at": "t", "channel": "CLAUDE", "capped": "spent"},
    '{"at": "t", "chan',
  )  # fmt: skip
  found = total(ledger, prices=PRICES, fallback=FALLBACK_PRICE)
  claude, codex = COST[Channel.CLAUDE], COST[Channel.CODEX]
  at_fallback = 2.0 + 0.038 * 2
  assert found.usd == pytest.approx(
    1.0 + 0.5 + claude * 3 + codex + 0.02 + at_fallback
  )
  assert found.fallback_usd == pytest.approx(at_fallback)
  without = total(ledger, prices=PRICES, fallback=None)
  assert without.usd == pytest.approx(found.usd - 0.038 * 2)


def test_total_prices_a_renamed_reply_at_the_requested_models_price(
  tmp_path: Path,
):
  usage = {"input_tokens": 1_000_000}
  ledger = ledger_of(
    tmp_path / "spend.jsonl",
    {"channel": "CLAUDE", "model": "claude-sonnet-5", "usage": usage,
     "requested_model": "claude-sonnet-5-5", "cost_usd": None},
  )  # fmt: skip
  assert total(ledger) == Total(GATEWAY_PRICES["claude-sonnet-5-5"].input, 0.0)
  assert total(tmp_path / "none.jsonl") == Total(0.0, 0.0)
