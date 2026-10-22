"""What the proxy refuses in a model call's body.

A container has no network, but a model call can still ask the model's side
to reach it for the container: to search or fetch the web, run code, connect
to an MCP server, or fetch an image or a file by its URL. The proxy reads each
model call's body, decompressed as its `Content-Encoding` says, and refuses it
unless every tool it declares is one its client runs itself and it names
nothing for the model's side to fetch. The tool types are allowed, not
refused, so that one an API adds later is refused until it is added here.

A body is read as UTF-8 JSON whose objects have no duplicate keys, so that the
gateway cannot read in it a field the proxy did not see; one that is not, or
that decompresses past `channels.MAX_BODY`, is refused too.

The tool types and fields, as read on 2026-10-22: the Messages API's at
https://platform.claude.com/docs/en/api/messages (each `tools` variant and
content source) and
https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview (which
tools are server tools); the Responses API's at
https://developers.openai.com/api/reference/resources/responses/methods/create
(each `tools` variant, `additional_tools`, `tool_search_output`, and the
`image_url` and `file_url` inputs).
"""

from collections.abc import Callable
from compression import zstd
from dataclasses import dataclass
import json
from typing import cast, final, Protocol
import zlib

from dataforge.gateway import channels
from dataforge.gateway.channels import Channel

# The tool types a client runs itself, by channel: the Messages API's own
# tools, with no `type` or `custom`; the Responses API's `function` and
# `custom` tools, a `namespace` of tools, `local_shell`, and `tool_search`
# when the client executes it.
TOOLS: dict[Channel, frozenset[str | None]] = {
  Channel.CLAUDE: frozenset({None, "custom"}),
  Channel.CODEX: frozenset(
    {"function", "custom", "namespace", "local_shell", "tool_search"}
  ),
}
# The Messages API's top-level fields that connect the model's side to an MCP
# server or a code execution container.
_FIELDS = ("mcp_servers", "container")
# The Messages API's image and document sources other than a URL.
_SOURCES = frozenset({"base64", "text", "content", "file"})
# The Responses API's fields that may name a URL for the model's side to fetch.
_URLS = ("image_url", "file_url")
# What is not read for tools and URLs: JSON schemas, and with `CLAUDE` the
# inputs of the model's own tool calls.
_SKIP: dict[Channel, frozenset[str]] = {
  Channel.CLAUDE: frozenset({"input_schema", "parameters", "schema", "input"}),
  Channel.CODEX: frozenset({"input_schema", "parameters", "schema"}),
}


@dataclass(frozen=True, slots=True)
class Refusal:
  """Why the proxy refuses a body.

  Attributes:
    status: The answer's status: 400, or 413 or 415 for a body too large or
      in an encoding the proxy does not read.
    why: What it refuses, naming a tool type or field only.
    asked: Whether the body asks the model's side for what the proxy
      refuses, rather than being one the proxy cannot read.
  """

  status: int
  why: str
  asked: bool


@final
class _RefusedError(Exception):
  def __init__(self, why: str, status: int = 400, *, asked: bool = False):
    super().__init__(why)
    self.refusal = Refusal(status, why, asked)


class _Decompressor(Protocol):
  @property
  def eof(self) -> bool: ...
  @property
  def unused_data(self) -> bytes: ...
  def decompress(self, data: bytes, max_length: int, /) -> bytes: ...


_DECOMPRESSORS: dict[str, Callable[[], _Decompressor]] = {
  "gzip": lambda: zlib.decompressobj(wbits=31),
  "deflate": zlib.decompressobj,
  "zstd": zstd.ZstdDecompressor,
}


def refusal(
  channel: Channel, encoding: str | None, body: bytes
) -> Refusal | None:
  """Say why the proxy refuses a model call's body, if it does.

  Args:
    channel: The channel the call came on.
    encoding: The request's `Content-Encoding`, or None.
    body: The body as it came.

  Returns:
    Why it is refused, or None when it may go to the gateway.
  """
  try:
    found = _parsed(_decoded(encoding or "", body))
    _check(channel, found)
  except _RefusedError as e:
    return e.refusal
  return None


def _decoded(encoding: str, body: bytes) -> bytes:
  # The codings apply in the order they are listed, so they come off in the
  # reverse one.
  codings = [c.strip().lower() for c in encoding.split(",")]
  for coding in reversed([c for c in codings if c not in ("", "identity")]):
    make = _DECOMPRESSORS.get(coding)
    if make is None:
      raise _RefusedError(f"Content-Encoding {_shown(coding)}", 415)
    try:
      body = _inflated(make, body)
    except zlib.error, zstd.ZstdError:
      raise _RefusedError(f"a body that is not {coding}") from None
  return body


def _inflated(make: Callable[[], _Decompressor], data: bytes) -> bytes:
  # Every member or frame in turn, never more than one byte past the limit.
  out = bytearray()
  while True:
    decompressor = make()
    out += decompressor.decompress(data, channels.MAX_BODY + 1 - len(out))
    if len(out) > channels.MAX_BODY:
      raise _RefusedError(f"a body past {channels.MAX_BODY} bytes", 413)
    if not decompressor.eof:
      raise _RefusedError("a truncated body")
    data = decompressor.unused_data
    if not data:
      return bytes(out)


type _Json = dict[str, _Json] | list[_Json] | str | int | float | bool | None


def _object(pairs: list[tuple[str, _Json]]) -> dict[str, _Json]:
  found = dict(pairs)
  if len(found) != len(pairs):
    raise _RefusedError("a JSON object with a duplicate key")
  return found


def _parsed(body: bytes) -> dict[str, _Json]:
  try:
    found = cast(_Json, json.loads(body.decode(), object_pairs_hook=_object))
  except ValueError, RecursionError:
    raise _RefusedError("a body that is not UTF-8 JSON") from None
  if not isinstance(found, dict):
    raise _RefusedError("a body that is not a JSON object")
  return found


def _check(channel: Channel, body: dict[str, _Json]) -> None:
  if channel is Channel.CLAUDE:
    for field in _FIELDS:
      if body.get(field) not in (None, [], {}):
        raise _RefusedError(f"field {field!r}", asked=True)
  skip = _SKIP[channel]
  nodes: list[_Json] = [body]
  while nodes:
    node = nodes.pop()
    if isinstance(node, list):
      nodes.extend(node)
    elif isinstance(node, dict):
      _check_tools(channel, node.get("tools"))
      if channel is Channel.CLAUDE:
        _check_source(node.get("source"))
      else:
        _check_urls(node)
      nodes.extend(v for k, v in node.items() if k not in skip)


def _check_tools(channel: Channel, tools: _Json) -> None:
  for tool in tools if isinstance(tools, list) else [tools]:
    if not isinstance(tool, dict):
      continue
    kind = tool.get("type")
    if not _among(kind, TOOLS[channel]):
      raise _RefusedError(f"tool type {_shown(kind)}", asked=True)
    if kind == "tool_search" and tool.get("execution") != "client":
      raise _RefusedError(
        "tool type 'tool_search' that the client does not execute", asked=True
      )


def _check_source(source: _Json) -> None:
  if isinstance(source, dict) and not _among(
    kind := source.get("type"), _SOURCES
  ):
    raise _RefusedError(f"source type {_shown(kind)}", asked=True)


def _among(
  kind: _Json, allowed: frozenset[str | None] | frozenset[str]
) -> bool:
  # A type that is a list or an object is among none.
  return (kind is None or isinstance(kind, str)) and kind in allowed


def _check_urls(node: dict[str, _Json]) -> None:
  for field in _URLS:
    url = node.get(field)
    if isinstance(url, dict):
      url = url.get("url")
    if url is not None and not (
      isinstance(url, str) and url.startswith("data:")
    ):
      raise _RefusedError(
        f"field {field!r} with a URL other than data:", asked=True
      )


def _shown(value: object) -> str:
  # A value of the body, escaped and cut short for a line of stderr.
  return ascii(value)[:64]


def answer(channel: Channel, refused: Refusal) -> bytes:
  """Return the body of the answer to a refused call, as its API's error.

  Args:
    channel: The channel the call came on.
    refused: Why it is refused.

  Returns:
    The JSON error.
  """
  kind = (
    "request_too_large" if refused.status == 413 else "invalid_request_error"
  )
  message = f"the model proxy refuses {refused.why}"
  if refused.asked:
    message += (
      ": a model call from a container may declare only tools its client"
      + " runs, and name nothing for the model's side to fetch"
    )
  error = {"type": kind, "message": message}
  found = (
    {"type": "error", "error": error}
    if channel is Channel.CLAUDE
    else {"error": error}
  )
  return json.dumps(found).encode()
