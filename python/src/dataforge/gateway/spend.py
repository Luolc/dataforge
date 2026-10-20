"""What a session's model calls cost: prices, a reply's reading, the ledger.

The proxy reads the model and usage of each 200 reply to a model call as it
passes it on (`Meter`), whatever its `Content-Type`: of a reply with `data:`
events, from `message_start` and the last `message_delta` (`CLAUDE`) or from
the `response` of `response.completed`, `response.done`,
`response.incomplete` or `response.failed` (`CODEX`); of a reply without any,
read as JSON, from its top-level `model` and `usage`. `Spend` prices it, and
the ledger gets one line per reply (`record`), each written whole and synced
before the proxy goes on; `read_ledger` reads it back.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import re
from typing import cast, TypedDict

from dataforge.gateway.channels import Channel

_log = logging.getLogger(__name__)

NO_USAGE = "a reply without usage"

# The events of a `CODEX` stream whose `response` holds the reply's usage; Pi
# reads `response.done` as an end as well as the other three.
_CODEX_ENDS = frozenset(
  {
    "response.completed",
    "response.done",
    "response.incomplete",
    "response.failed",
  }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Price:
  """A model's prices, in US dollars per million tokens.

  Attributes:
    input: Input tokens neither read from nor written to the cache.
    output: Output tokens, thinking and reasoning included.
    cache_read: Input tokens read from the cache.
    cache_write_5m: Input tokens written to the cache for 5 minutes; `CODEX`'s
      cache writes, when it gives them.
    cache_write_1h: Input tokens written to the cache for an hour; `CLAUDE`'s
      cache writes without the split into 5m and 1h, the dearer.
  """

  input: float
  output: float
  cache_read: float
  cache_write_5m: float
  cache_write_1h: float


class _PriceJSON(TypedDict):
  input: float
  output: float
  cache_read: float
  cache_write_5m: float
  cache_write_1h: float


class _SpendJSON(TypedDict):
  prices: dict[str, _PriceJSON]
  max_usd: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Spend:
  """A cap on what one gateway session's replies cost.

  Attributes:
    prices: Each model's prices, which the caller gives.
    max_usd: Once the session's replies cost this much, in US dollars, the
      proxy refuses every later request and ends the session.
  """

  prices: Mapping[str, Price]
  max_usd: float

  def to_json(self) -> str:
    """Return the cap as JSON, as the proxy's command line takes it.

    Returns:
      The JSON.
    """
    prices = {
      model: {
        "input": p.input,
        "output": p.output,
        "cache_read": p.cache_read,
        "cache_write_5m": p.cache_write_5m,
        "cache_write_1h": p.cache_write_1h,
      }
      for model, p in self.prices.items()
    }
    return json.dumps({"prices": prices, "max_usd": self.max_usd})

  @classmethod
  def from_json(cls, text: str) -> Spend:
    """Read a cap that `to_json` wrote.

    Args:
      text: The JSON.

    Returns:
      The cap.
    """
    found = cast(_SpendJSON, json.loads(text))
    prices = {m: Price(**p) for m, p in found["prices"].items()}
    return cls(prices=prices, max_usd=found["max_usd"])


def _tokens(found: object, key: str) -> int:
  value = (
    cast(Mapping[str, object], found).get(key)
    if isinstance(found, Mapping)
    else None
  )
  return value if isinstance(value, int) and not isinstance(value, bool) else 0


def cost(channel: Channel, price: Price, usage: Mapping[str, object]) -> float:
  """Return what a reply cost.

  Args:
    channel: The reply's channel, which says how its usage counts tokens.
    price: The model's prices.
    usage: The reply's usage.

  Returns:
    The cost, in US dollars.
  """
  if channel is Channel.CODEX:
    # `input_tokens` holds the cached and the written tokens.
    details = usage.get("input_tokens_details")
    read = _tokens(details, "cached_tokens")
    written = _tokens(details, "cache_write_tokens")
    fresh = max(_tokens(usage, "input_tokens") - read - written, 0)
    per_million = (
      fresh * price.input
      + read * price.cache_read
      + written * price.cache_write_5m
      + _tokens(usage, "output_tokens") * price.output
    )
    return per_million / 1e6
  split = usage.get("cache_creation")
  if isinstance(split, Mapping):
    split = cast(Mapping[str, object], split)
    write_5m = _tokens(split, "ephemeral_5m_input_tokens")
    write_1h = _tokens(split, "ephemeral_1h_input_tokens")
  else:
    write_5m, write_1h = 0, _tokens(usage, "cache_creation_input_tokens")
  per_million = (
    _tokens(usage, "input_tokens") * price.input
    + _tokens(usage, "output_tokens") * price.output
    + _tokens(usage, "cache_read_input_tokens") * price.cache_read
    + write_5m * price.cache_write_5m
    + write_1h * price.cache_write_1h
  )
  return per_million / 1e6


_BOM = b"\xef\xbb\xbf"
_HEAD = 64
_FIELD = 16
_KEYS = 8
_NAME = re.compile(rb"[A-Za-z0-9_-]*")
_KEY = re.compile(r"[A-Za-z0-9_.-]{1,32}")


class Meter:
  """The model and usage of one reply, read as it passes."""

  def __init__(
    self,
    channel: Channel,
    content_type: str | None,
    content_encoding: str = "identity",
  ):
    """Read a reply of a channel.

    Args:
      channel: The channel, which says where the usage is.
      content_type: The reply's `Content-Type`, None for none, which only
        the diagnosis of a reply without usage reports: whatever it says,
        every line is looked at for a `data:` event, and a reply with none is
        read whole as JSON.
      content_encoding: The reply's `Content-Encoding`; a reply compressed is
        not read, and `unreadable` says so.
    """
    self.channel: Channel = channel
    self.content_type: str | None = content_type
    self._buffer: bytes = b""
    # The whole reply, kept while it may yet be JSON.
    self._body: bytearray | None = bytearray()
    self.model: str | None = None
    self.usage: dict[str, object] = {}
    self.unreadable: str | None = None
    # What the diagnosis of a reply without usage reports: the bytes fed, the
    # `type` of each kind of event in order of first appearance, the `data:`
    # lines, and the length and top-level keys of the first line that is not
    # blank.
    self.size: int = 0
    self.kinds: list[str] = []
    self.data_lines: int = 0
    self.first_len: int = 0
    self.first_keys: list[str] | None = None
    self._head: bytes = b""
    self._lead: bool = True
    self._seen_first: bool = False
    self._ended: bool = False
    if content_encoding.lower() != "identity":
      self.unreadable = f"a reply in Content-Encoding {content_encoding}"

  def feed(self, chunk: bytes) -> None:
    """Read the next bytes of the reply.

    Args:
      chunk: The bytes.
    """
    if self.unreadable is not None:
      return
    self.size += len(chunk)
    self._head += chunk[: _HEAD - len(self._head)]
    if self._body is not None:
      self._body += chunk
    self._buffer += chunk
    if self._lead:
      # A reply may open with a byte order mark, which a split chunk can leave
      # half-read.
      if _BOM.startswith(self._buffer) and len(self._buffer) < len(_BOM):
        return
      self._buffer = self._buffer.removeprefix(_BOM)
      self._lead = False
    *lines, self._buffer = self._buffer.split(b"\n")
    for line in lines:
      self._line(line)

  def end(self) -> None:
    """Read what is left once the reply has ended or was cut off."""
    if self.unreadable is not None:
      return
    self._ended = True
    self._line(self._buffer)
    if self._body is not None and not self.data_lines:
      self._message(_json(bytes(self._body)))
    self._buffer = b""
    self._body = None

  @property
  def mode(self) -> str:
    """How the reply was read: `stream`, `json` or `undecided`."""
    if self.data_lines:
      return "stream"
    return "json" if self._ended else "undecided"

  @property
  def field(self) -> str:
    """The field name before the first colon of a stream's first line.

    It is a protocol field, never content: at most 16 bytes of letters,
    digits, `_` and `-`, else `?`; a reply read as JSON has none.
    """
    if self.mode != "stream":
      return "-"
    line = self._head.removeprefix(_BOM).lstrip().split(b"\n")[0]
    name, colon, _ = line.partition(b":")
    if not colon or len(name) > _FIELD or not _NAME.fullmatch(name):
      return "?"
    return name.decode()

  def _first(self, line: bytes) -> None:
    if self._seen_first or not line.strip():
      return
    self._seen_first = True
    self.first_len = len(line.strip())
    found = _json(line)
    if found:
      # Protocol keys only: a key that is not short and plain is `?`.
      self.first_keys = [
        k if _KEY.fullmatch(k) else "?" for k in list(found)[:_KEYS]
      ]

  def _line(self, line: bytes) -> None:
    self._first(line)
    if line.startswith(b"data:"):
      self.data_lines += 1
      # A stream is not JSON, so the whole reply need not be kept.
      self._body = None
      self._event(_json(line.removeprefix(b"data:")))

  def _event(self, event: Mapping[str, object]) -> None:
    kind = event.get("type")
    if isinstance(kind, str) and kind not in self.kinds:
      self.kinds.append(kind)
    if self.channel is Channel.CODEX:
      if kind in _CODEX_ENDS:
        self._message(event.get("response"))
    elif kind == "message_start":
      self._message(event.get("message"))
    elif kind == "message_delta":
      self._usage(event.get("usage"))

  def _message(self, message: object) -> None:
    if not isinstance(message, Mapping):
      return
    found = cast(Mapping[str, object], message)
    model = found.get("model")
    if isinstance(model, str):
      self.model = model
    self._usage(found.get("usage"))

  def _usage(self, usage: object) -> None:
    if isinstance(usage, Mapping):
      found = cast(Mapping[object, object], usage)
      self.usage |= {str(k): v for k, v in found.items() if v is not None}


def _json(data: bytes) -> dict[str, object]:
  try:
    found = cast(object, json.loads(data))
  except ValueError:
    return {}
  return cast(dict[str, object], found) if isinstance(found, dict) else {}


def price(meter: Meter, spend: Spend | None) -> tuple[float | None, str | None]:
  """Price a reply that has ended.

  Args:
    meter: What was read of it.
    spend: The session's cap, which holds the prices; None for none.

  Returns:
    Its cost in US dollars and None, or None and why it could not be priced.
  """
  if meter.unreadable is not None:
    return None, meter.unreadable
  if not meter.usage:
    return None, NO_USAGE
  if spend is None:
    return None, "no prices"
  found = spend.prices.get(meter.model or "")
  if found is None:
    return None, f"no price for the model {meter.model!r}"
  return cost(meter.channel, found, meter.usage), None


def now() -> str:
  """Return the time now, in UTC, as the ledger writes it.

  Returns:
    An ISO 8601 time.
  """
  return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def record(ledger: Path, line: Mapping[str, object]) -> None:
  """Append a line to a ledger and sync it to disk.

  Processes that append to one ledger at once each write whole lines.

  Args:
    ledger: The ledger, created if it does not exist.
    line: The line.
  """
  data = (json.dumps(line) + "\n").encode()
  fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    _ = os.write(fd, data)
    os.fsync(fd)
  finally:
    os.close(fd)


def read_ledger(path: Path) -> list[dict[str, object]]:
  """Read a ledger back.

  Each reply's line is `{at, channel, model, usage, cost_usd, reason}`,
  `cost_usd` null with the `reason` when the proxy could not price the reply;
  the line of a session the cap ended is `{at, channel, capped}`. A line that
  is not a JSON object is left out, with a warning that counts them.

  Args:
    path: The ledger; none reads as empty.

  Returns:
    Its lines, in order.
  """
  try:
    text = path.read_text()
  except FileNotFoundError:
    return []
  found: list[dict[str, object]] = []
  skipped = 0
  for line in text.splitlines():
    if entry := _json(line.encode()):
      found.append(entry)
    elif line.strip():
      skipped += 1
  if skipped:
    _log.warning("%s: %d lines are not JSON objects", path, skipped)
  return found
