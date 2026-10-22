"""The gateway wired into dataforge's hooks: `trial_setup` and `bindings`.

A container behind the gateway has no network; its client reaches the model at
`127.0.0.1:8787`, where `forward.py` copies each connection to the proxy's
socket in the directory mounted at `/run/dataforge`, and holds only
placeholders (`CLAUDE_ENV`, `CODEX_ENV`), so that its code holds no credential.
"""

import base64
from collections.abc import Callable, Generator, Mapping
import contextlib
import fcntl
import json
from pathlib import Path
import tempfile
import time

from dataforge import stopping
from dataforge.curation.actions import Docker, Limits, Mount, TrialAgent
from dataforge.curation.harbor import TrialSetup
from dataforge.gateway.channels import Channel
from dataforge.gateway.clients import (
  CLAUDE_CODE,
  Client,
  CODEX,
  executable,
  mounts,
  own_dir,
  PI,
  unpack,
)
from dataforge.gateway.session import serve
from dataforge.gateway.spend import Price, Spend

# How often a serve that waits for another to end looks again.
LOCK_POLL_S = 0.2

ADDRESS = "http://127.0.0.1:8787"
SOCKET_DIR = "/run/dataforge"
SOCKET = f"{SOCKET_DIR}/model.sock"
FORWARDER = Path(__file__).with_name("forward.py")
FORWARDER_TARGET = "/opt/dataforge/forward.py"
PLACEHOLDER = "dataforge-placeholder"


def _placeholder_jwt() -> str:
  # Pi takes the `chatgpt_account_id` of the token's
  # `https://api.openai.com/auth` claim and fails without one; it does not
  # check the signature, and the proxy replaces the token anyway.
  def part(value: object) -> str:
    data = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

  claims = {"https://api.openai.com/auth": {"chatgpt_account_id": PLACEHOLDER}}
  return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


# What `claude` in a container needs: the forwarder's address, a placeholder,
# and the switches redcoast-client sets for its own `claude`, so that none of
# Claude Code's own traffic goes around the proxy.
CLAUDE_ENV: Mapping[str, str] = {
  "ANTHROPIC_BASE_URL": ADDRESS,
  "CLAUDE_CODE_OAUTH_TOKEN": PLACEHOLDER,
  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
  "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
  "DISABLE_AUTOUPDATER": "1",
}
# What Pi and Codex CLI in a container need: a placeholder token, Pi's
# directory, the one Harbor's pi agent itself uses, and Pi kept offline.
CODEX_ENV: Mapping[str, str] = {
  "CODEX_GATEWAY_TOKEN": _placeholder_jwt(),
  "PI_CODING_AGENT_DIR": "/tmp/harbor-pi-agent",
  "PI_OFFLINE": "1",
}
ENV = {Channel.CLAUDE: CLAUDE_ENV, Channel.CODEX: CODEX_ENV}
# The client version a trial's agent checks its client against; Harbor and
# the container get it from the trial's env.
VERSION_VAR = "DATAFORGE_CLIENT_VERSION"
DEFAULT_CLIENTS: Mapping[str, Client] = {
  "claude-code": CLAUDE_CODE,
  "pi": PI,
  "codex": CODEX,
}
# The agents of dataforge.gateway.harbor, by import path: the channel, the
# client's name and the command that runs it.
AGENTS: Mapping[str, tuple[Channel, str, str]] = {
  "dataforge.gateway.harbor:ClaudeCode": (
    Channel.CLAUDE,
    "claude-code",
    "claude",
  ),
  "dataforge.gateway.harbor:Pi": (Channel.CODEX, "pi", "pi"),
  "dataforge.gateway.harbor:Codex": (Channel.CODEX, "codex", "codex"),
}
# The command `main` runs: the forwarder, and sleep in place of the shell.
COMMAND = ["sh", "-c", f"python3 {FORWARDER_TARGET} & exec sleep infinity"]
# How long `start-forwarder` waits for the forwarder to listen.
FORWARDER_TIMEOUT = 30.0
_PROBE = (
  "import socket; socket.create_connection(('127.0.0.1', 8787), 1).close()"
)

type Setup = Callable[
  [Path, TrialAgent, Limits, Path],
  contextlib.AbstractContextManager[TrialSetup],
]


def trial_setup(
  *,
  spend: Spend | None = None,
  ledger: Path | None = None,
  clients: Mapping[str, Client] | None = None,
) -> Setup:
  """Return a `HarborRunner` `setup` that serves an agent's trial the gateway.

  For an agent whose import path names one of `dataforge.gateway.harbor`'s
  (`AGENTS`), it checks the agent's client, serves its channel for the trial
  and yields the socket, the forwarder and the client to mount, the channel's
  env with the client's version, and an overlay that gives `main` no network
  and a command that starts the forwarder and sleeps, so that the task's image
  needs `python3`. Any other trial gets nothing.

  Args:
    spend: Each trial session's cap, or None.
    ledger: Where each session's proxy writes down each reply, or None.
    clients: The clients by name (`claude-code`, `pi`, `codex`), instead of
      `DEFAULT_CLIENTS`.

  Returns:
    The setup.
  """
  chosen = DEFAULT_CLIENTS if clients is None else clients

  @contextlib.contextmanager
  def setup(
    task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> Generator[TrialSetup]:
    del task_dir, limits, outdir
    found = _agent(agent)
    if found is None:
      yield TrialSetup()
      return
    channel, name, command = found
    if name not in chosen:
      raise ValueError(f"no client named {name!r}")
    client = chosen[name]
    client_mounts = mounts(client, command, own_dir())
    # A socket's path has at most 107 bytes, which a trial's directory may
    # pass.
    with (
      tempfile.TemporaryDirectory(prefix="dataforge-gw-") as short,
      serve(
        channel,
        _socket_dir(Path(short)) / "model.sock",
        spend=spend,
        ledger=ledger,
      ) as socket,
    ):
      overlay = Path(short) / "overlay.json"
      _ = overlay.write_text(json.dumps(OVERLAY, indent=2))
      yield TrialSetup(
        mounts=(
          Mount(socket.parent, SOCKET_DIR, read_only=False),
          Mount(FORWARDER, FORWARDER_TARGET),
          *client_mounts,
        ),
        env={**ENV[channel], VERSION_VAR: client.version},
        compose_overlay=(overlay,),
      )

  return setup


def _socket_dir(short: Path) -> Path:
  # The container mounts the socket's directory, not the socket: the cap
  # replaces the socket by another file, which a mounted file would not follow.
  # Mode 0711 lets the container's user reach the socket in it.
  directory = short / "socket"
  directory.mkdir()
  directory.chmod(0o711)
  return directory


OVERLAY = {"services": {"main": {"network_mode": "none", "command": COMMAND}}}


def _agent(agent: TrialAgent) -> tuple[Channel, str, str] | None:
  if not isinstance(agent, Mapping):
    return None
  path = agent.get("import_path")
  return AGENTS.get(path) if isinstance(path, str) else None


def bindings(
  root: Path,
  *,
  prices: Mapping[str, Price] | None = None,
  ledger: Path | None = None,
  clients: Mapping[str, Client] | None = None,
) -> dict[str, object]:
  """Return the gateway's parts for a declaration's `Docker` environment.

  Under `root/dataforge-gateway/`, a directory mode 0700: `socket-dir`, a
  directory mode 0711 inside it, which the environment mounts at
  `/run/dataforge` and every `serve` reuses; `forwarder`, the forwarder's
  file; `client/<name>` for each client, checked and, for a tarball,
  unpacked; `claude-env` and `codex-env`; `start-forwarder`, a `post_start`
  that starts the forwarder and waits until it listens; and `serve`, a factory
  whose `with` is `{channel, max_usd}` (`max_usd` optional, priced by
  `prices`), for a `Runner`'s `serve`. The serves of one socket take turns,
  each waiting while another's block runs, in this process or another.

  Args:
    root: The directory the gateway's parts go under.
    prices: Each model's prices, for a `serve` with `max_usd`.
    ledger: Where each session's proxy writes down each reply, or None.
    clients: The clients by name, instead of `DEFAULT_CLIENTS`.

  Returns:
    The bindings, by name.

  Raises:
    RuntimeError: A client's sha256 is not its pin, or its tarball cannot be
      unpacked.
  """  # noqa: DOC502
  top = root / "dataforge-gateway"
  if not top.exists():
    top.mkdir(mode=0o700, parents=True)
  socket_dir = top / "socket"
  socket_dir.mkdir(exist_ok=True)
  socket_dir.chmod(0o711)
  lock = top / "serve.lock"

  def serve_factory(
    channel: str, max_usd: float | None = None
  ) -> Callable[[], contextlib.AbstractContextManager[object]]:
    which = Channel(channel)
    if max_usd is not None and prices is None:
      raise ValueError("max_usd needs the prices the bindings were given")
    cap = (
      None
      if max_usd is None or prices is None
      else Spend(prices=prices, max_usd=max_usd)
    )

    @contextlib.contextmanager
    def serving() -> Generator[Path]:
      with lock.open("a") as held:
        # The serve that holds it may run for minutes; a stopped fan_out's
        # call stops waiting.
        while True:
          try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
          except BlockingIOError:
            stopping.check()
            time.sleep(LOCK_POLL_S)
        with serve(
          which, socket_dir / "model.sock", spend=cap, ledger=ledger
        ) as socket:
          yield socket

    return serving

  found: dict[str, object] = {
    "socket-dir": socket_dir,
    "forwarder": FORWARDER,
    "claude-env": dict(CLAUDE_ENV),
    "codex-env": dict(CODEX_ENV),
    "start-forwarder": start_forwarder,
    "serve": serve_factory,
  }
  for name, client in (DEFAULT_CLIENTS if clients is None else clients).items():
    found[f"client/{name}"] = (
      executable(client)
      if client.program is None
      else unpack(client, name, top / "clients")
    )
  return found


def start_forwarder(env: Docker) -> None:
  """Start the forwarder in a running container and wait until it listens.

  Args:
    env: The container's environment, started.

  Raises:
    RuntimeError: The container is not created, or the forwarder does not
      listen within `FORWARDER_TIMEOUT`.
  """
  container = env.container
  if container is None:
    raise RuntimeError("the container is not created")
  started = env.cli(["exec", "-d", container, "python3", FORWARDER_TARGET])
  if started.exit_code != 0:
    raise RuntimeError(f"the forwarder did not start: {started.log}")
  deadline = time.monotonic() + FORWARDER_TIMEOUT
  while env.cli(["exec", container, "python3", "-c", _PROBE]).exit_code != 0:
    if time.monotonic() > deadline:
      raise RuntimeError(
        f"the forwarder is not listening after {FORWARDER_TIMEOUT}s"
      )
    time.sleep(0.2)
