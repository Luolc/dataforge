"""An optional model gateway: redcoast's channels served to containers.

A dataset that runs its agents behind redcoast's gateway may use this module in
place of its own. It is wired in through dataforge's hooks only (`trial_setup`
for `HarborRunner`, `bindings` for a declaration's `Docker` environment and a
`Runner`'s `serve`), and nothing else in dataforge imports it, so a dataset
that does not use it installs, imports and runs as before. It serves the
gateway's two channels, `Channel.CLAUDE` for Claude Code and `Channel.CODEX`
for Pi and Codex CLI, to containers with no network, whose code then holds no
credential. `dataforge.gateway.harbor` holds the Harbor agents that run behind
it; only Harbor imports that module.
"""

from dataforge.gateway.channels import Channel
from dataforge.gateway.clients import CLAUDE_CODE, Client, CODEX, PI
from dataforge.gateway.hooks import (
  bindings,
  CLAUDE_ENV,
  CODEX_ENV,
  start_forwarder,
  trial_setup,
)
from dataforge.gateway.pause import (
  Backoff,
  CUT_S,
  Pause,
  pausing,
  QuotaError,
  REFUSALS,
  sessions,
)
from dataforge.gateway.session import serve
from dataforge.gateway.spend import (
  by_model,
  HIGHEST,
  Price,
  PRICES,
  read_ledger,
  Spend,
  Total,
  total,
)

__all__ = [
  "CLAUDE_CODE",
  "CLAUDE_ENV",
  "CODEX",
  "CODEX_ENV",
  "CUT_S",
  "HIGHEST",
  "PI",
  "PRICES",
  "REFUSALS",
  "Backoff",
  "Channel",
  "Client",
  "Pause",
  "Price",
  "QuotaError",
  "Spend",
  "Total",
  "bindings",
  "by_model",
  "pausing",
  "read_ledger",
  "serve",
  "sessions",
  "start_forwarder",
  "total",
  "trial_setup",
]
