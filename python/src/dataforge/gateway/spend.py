"""What a session's model calls cost: prices, a reply's reading, the ledger.

The proxy reads the model and usage of each 200 reply to a model call as it
passes it on (`Meter`), whatever its `Content-Type`: of a reply with `data:`
events, from `message_start` and the last `message_delta` (`CLAUDE`) or from
the `response` of `response.completed`, `response.done`,
`response.incomplete` or `response.failed` (`CODEX`); of a reply without any,
read as JSON, from its top-level `model` and `usage`. `Spend` prices it, and
the ledger gets one line per reply (`record`), each written whole and synced
before the proxy goes on; `read_ledger` reads it back, `total` prices and
sums it, and `by_model` sums it by model. A reply is priced by the model it
names, else by the model its request named, else at a fallback price
(`choose`): a gateway may answer under another name than the one asked for.
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
import threading
from types import MappingProxyType
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


# The Claude API's list prices for global routing at the standard speed, read
# on 2026-10-20 from https://platform.claude.com/docs/en/about-claude/pricing
# (`claude-sonnet-5`, whose $2 and $10 the page says are now its standard
# price, and `claude-opus-5` on 2026-10-22), and, for Pi and Codex CLI, an
# estimate, since the gateway's `CODEX` channel bills a ChatGPT subscription,
# not these prices: litellm's for OpenAI's API, from the entry `gpt-6-astra`
# of `model_prices_and_context_window.json` at its tag v1.106.0 (commit
# 955bdaafa8b72b2b6ff728048f272fbc06049f63), read on 2026-10-20, the prices up
# to 272k input tokens a request, so that a request past that, which that file
# prices at about twice as much, counts for less than the API would charge;
# its one price for cache writes is both of them.
PRICES: Mapping[str, Price] = MappingProxyType({
  "claude-opus-5-5": Price(
    input=4.0, output=20.0, cache_read=0.2, cache_write_5m=5.0,
    cache_write_1h=8.0,
  ),
  "claude-opus-5": Price(
    input=5.0, output=25.0, cache_read=0.5, cache_write_5m=6.25,
    cache_write_1h=10.0,
  ),
  "claude-opus-4-8": Price(
    input=5.0, output=25.0, cache_read=0.5, cache_write_5m=6.25,
    cache_write_1h=10.0,
  ),
  "claude-sonnet-5-5": Price(
    input=2.0, output=10.0, cache_read=0.1, cache_write_5m=2.5,
    cache_write_1h=4.0,
  ),
  "claude-sonnet-5": Price(
    input=2.0, output=10.0, cache_read=0.2, cache_write_5m=2.5,
    cache_write_1h=4.0,
  ),
  "gpt-6-astra": Price(
    input=10.0, output=50.0, cache_read=1.0, cache_write_5m=12.5,
    cache_write_1h=12.5,
  ),
})  # fmt: skip
# The default price of a reply that no model's price fits: the highest price
# of each kind on the Claude API's page on 2026-10-20 (Claude Opus 4 and 4.1:
# $15, $75, $1.50, $18.75 and $30 per million tokens), times the 1.1 of
# US-only inference, the page's largest multiplier above them, so that no
# reply of a model the page lists counts for less than it cost; it is above
# the `CODEX` price too.
HIGHEST = Price(
  input=16.5, output=82.5, cache_read=1.65, cache_write_5m=20.625,
  cache_write_1h=33.0,
)  # fmt: skip
# The values of `speed` and `inference_geo` of a `CLAUDE` reply that a model's
# price fits; a reply without them is taken as standard. A refused reply can
# have the `inference_geo` "not_available": it ran no US-only inference, the
# one `inference_geo` that the pricing page prices above global on 2026-10-22.
STANDARD: Mapping[str, frozenset[str]] = MappingProxyType(
  {
    "speed": frozenset({"standard"}),
    "inference_geo": frozenset({"global", "not_available"}),
  }
)
# Each search of Claude's web search tool, on the same page.
WEB_SEARCH_USD = 0.01
# How `choose` chose a price other than that of the model a reply names: a
# ledger line's `priced_as`. An earlier ledger's lines say "highest" for the
# fallback.
REQUESTED = "requested"
FALLBACK = "fallback"
_FALLBACKS = frozenset({FALLBACK, "highest"})


class _PriceJSON(TypedDict):
  input: float
  output: float
  cache_read: float
  cache_write_5m: float
  cache_write_1h: float


class _SpendJSON(TypedDict):
  prices: dict[str, _PriceJSON]
  max_usd: float
  fallback: _PriceJSON | None


def _price_json(price: Price) -> _PriceJSON:
  return {
    "input": price.input,
    "output": price.output,
    "cache_read": price.cache_read,
    "cache_write_5m": price.cache_write_5m,
    "cache_write_1h": price.cache_write_1h,
  }


@dataclass(frozen=True, slots=True, kw_only=True)
class Spend:
  """A cap on what one gateway session's replies cost.

  Attributes:
    prices: Each model's prices, which the caller gives, such as `PRICES`.
    max_usd: Once the session's replies cost this much, in US dollars, the
      proxy refuses every later request and ends the session.
    fallback: The price of a reply that no model's price fits (`choose`),
      such as `HIGHEST`; None to end the session on such a reply.
  """

  prices: Mapping[str, Price]
  max_usd: float
  fallback: Price | None = None

  def to_json(self) -> str:
    """Return the cap as JSON, as the proxy's command line takes it.

    Returns:
      The JSON.
    """
    found: _SpendJSON = {
      "prices": {m: _price_json(p) for m, p in self.prices.items()},
      "max_usd": self.max_usd,
      "fallback": None if self.fallback is None else _price_json(self.fallback),
    }
    return json.dumps(found)

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
    fallback = found["fallback"]
    return cls(
      prices=prices,
      max_usd=found["max_usd"],
      fallback=None if fallback is None else Price(**fallback),
    )


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
    The cost, in US dollars, with `WEB_SEARCH_USD` for each web search of a
    `CLAUDE` reply.
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
  searches = _tokens(usage.get("server_tool_use"), "web_search_requests")
  return per_million / 1e6 + searches * WEB_SEARCH_USD


def choose(
  channel: Channel,
  usage: Mapping[str, object],
  model: str | None,
  requested: str | None,
  prices: Mapping[str, Price],
  fallback: Price | None,
) -> tuple[Price, str | None] | None:
  """Choose the price of a reply.

  Args:
    channel: The reply's channel.
    usage: Its usage.
    model: The model it names, if any.
    requested: The model its request named, if known.
    prices: Each model's prices.
    fallback: The price of a reply that no model's price fits, or None.

  Returns:
    The price of the model the reply names, with None; else that of the model
    its request named, with `REQUESTED`; else the fallback, with `FALLBACK`,
    as for a `CLAUDE` reply at a `speed` or `inference_geo` that `STANDARD`
    does not hold; None when there is no fallback then.
  """
  standard = channel is not Channel.CLAUDE or all(
    usage.get(k) in (None, *values) for k, values in STANDARD.items()
  )
  if standard:
    for name, how in ((model, None), (requested, REQUESTED)):
      found = None if name is None else prices.get(name)
      if found is not None:
        return found, how
  return None if fallback is None else (fallback, FALLBACK)


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
    # The model the request named, when the proxy could read it.
    self.requested: str | None = None
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


def price(
  meter: Meter, spend: Spend | None
) -> tuple[float | None, str | None, str | None]:
  """Price a reply that has ended.

  Args:
    meter: What was read of it.
    spend: The session's cap, which holds the prices; None for none.

  Returns:
    Its cost in US dollars, None and how `choose` chose its price; or None,
    why it could not be priced and None.
  """
  if meter.unreadable is not None:
    return None, meter.unreadable, None
  if not meter.usage:
    return None, NO_USAGE, None
  if spend is None:
    return None, "no prices", None
  chosen = choose(
    meter.channel,
    meter.usage,
    meter.model,
    meter.requested,
    spend.prices,
    spend.fallback,
  )
  if chosen is None:
    why = f"no price for the model {meter.model!r} nor {meter.requested!r}"
    return None, why, None
  return cost(meter.channel, chosen[0], meter.usage), None, chosen[1]


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

  Each reply's line is `{at, channel, model, requested_model, usage,
  cost_usd, priced_as, reason}`, `requested_model` the model the request
  named, null when the proxy could not read it, `cost_usd` null with the
  `reason` when the proxy could not price the reply, and `priced_as` how
  `choose` chose the price; the line of a session the cap ended is `{at,
  channel, capped}`. A line that is not a JSON object is left out, with a
  warning that counts them.

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


# What `total` last warned of each ledger, so that a ledger read at every
# step of an attempt warns of its lines once, not at each step.
_warned: dict[Path, tuple[int, int]] = {}
_WARNED_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class Total:
  """What a ledger's replies cost together.

  Attributes:
    usd: The sum, in US dollars.
    fallback_usd: What of it was priced at the fallback price.
  """

  usd: float
  fallback_usd: float


def total(
  ledger: Path,
  *,
  prices: Mapping[str, Price] = PRICES,
  fallback: Price | None = HIGHEST,
) -> Total:
  """Price a ledger's replies and sum them.

  A line counts the cost it holds, `cost_usd` or an earlier proxy's `usd`;
  else its reply is priced by `choose` from its model, its `requested_model`
  (a line written before the proxy read it has none) and its usage, a line
  without `channel` being `CLAUDE`'s. A line whose `priced_as` is `FALLBACK`
  or an earlier ledger's "highest" counts in `fallback_usd` too. A line with
  no cost that cannot be priced, such as one of a reply without usage, is not
  counted, and the line of a session the cap ended holds no reply; one
  warning counts the lines not counted, and one those priced at the fallback,
  each only when its count for the ledger grew since this process last
  warned of it, since a machine reads its measure at every step.

  Args:
    ledger: The ledger; none counts as empty.
    prices: Each model's prices.
    fallback: The price of a reply that no model's price fits; None to leave
      such a reply out.

  Returns:
    The sum.
  """
  usd = fallback_usd = 0.0
  skipped = at_fallback = 0
  for line in read_ledger(ledger):
    found = _line_usd(line, prices, fallback)
    if found is None:
      skipped += "capped" not in line
      continue
    usd += found[0]
    if found[1]:
      fallback_usd += found[0]
      at_fallback += 1
  with _WARNED_LOCK:
    before = _warned.get(ledger, (0, 0))
    _warned[ledger] = (max(before[0], skipped), max(before[1], at_fallback))
  if skipped > before[0]:
    _log.warning("%s: %d lines without a price, not counted", ledger, skipped)
  if at_fallback > before[1]:
    _log.warning(
      "%s: %d lines priced at the fallback, $%.4f",
      ledger,
      at_fallback,
      fallback_usd,
    )
  return Total(usd, fallback_usd)


def by_model(
  ledger: Path,
  *,
  channel: Channel = Channel.CLAUDE,
  prices: Mapping[str, Price] = PRICES,
  fallback: Price | None = HIGHEST,
) -> dict[str, float]:
  """Price a channel's replies in a ledger by model, as `total` does.

  Args:
    ledger: The ledger; none counts as empty.
    channel: The channel whose lines count, a line without `channel` being
      `CLAUDE`'s.
    prices: Each model's prices.
    fallback: The price of a reply that no model's price fits; None to leave
      such a reply out.

  Returns:
    Each model's sum, in US dollars, by the model the reply names, else the
    model its request named, else the empty string; its values add up to
    what `total` counts of the channel's lines.
  """
  found: dict[str, float] = {}
  for line in read_ledger(ledger):
    if line.get("channel", Channel.CLAUDE) != channel:
      continue
    usd = _line_usd(line, prices, fallback)
    if usd is None:
      continue
    name = _text(line.get("model")) or _text(line.get("requested_model")) or ""
    found[name] = found.get(name, 0.0) + usd[0]
  return found


def _number(value: object) -> float | None:
  if isinstance(value, int | float) and not isinstance(value, bool):
    return value
  return None


def _text(value: object) -> str | None:
  return value if isinstance(value, str) else None


def _line_usd(
  line: Mapping[str, object],
  prices: Mapping[str, Price],
  fallback: Price | None,
) -> tuple[float, bool] | None:
  # A line's cost and whether it is at the fallback price; None for none.
  for key in ("cost_usd", "usd"):
    stored = _number(line.get(key))
    if stored is not None:
      return stored, line.get("priced_as") in _FALLBACKS
  usage = line.get("usage")
  if not isinstance(usage, Mapping) or not usage:
    return None
  usage = cast(Mapping[str, object], usage)
  try:
    channel = Channel(line.get("channel", Channel.CLAUDE))
  except ValueError:
    return None
  chosen = choose(
    channel,
    usage,
    _text(line.get("model")),
    _text(line.get("requested_model")),
    prices,
    fallback,
  )
  if chosen is None:
    return None
  return cost(channel, chosen[0], usage), chosen[1] == FALLBACK
