"""The gateway's two channels and what each forwards.

redcoast's gateway runs one process per channel, each with its own session
interface: `CLAUDE` for Claude Code, `CODEX` for Pi and Codex CLI. A session of
a channel is good for whatever that process forwards, so the proxy forwards
only the model calls of its channel's clients.
"""

import enum


class Channel(enum.StrEnum):
  """A channel of the gateway.

  Attributes:
    CLAUDE: Claude Code's.
    CODEX: Pi's and Codex CLI's.
  """

  CLAUDE = "CLAUDE"
  CODEX = "CODEX"


# The requests that get the session, by channel: the clients' model calls.
ROUTES: dict[Channel, frozenset[tuple[str, str]]] = {
  Channel.CLAUDE: frozenset(
    {("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens")}
  ),
  Channel.CODEX: frozenset({("POST", "/backend-api/codex/responses")}),
}
# The requests whose 200 replies are model calls the gateway bills, by
# channel; the proxy prices and records only these.
BILLED: dict[Channel, str] = {
  Channel.CLAUDE: "/v1/messages",
  Channel.CODEX: "/backend-api/codex/responses",
}
