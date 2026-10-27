# pyright: reportAny=false, reportPrivateUsage=false
# The tests read the overlay's JSON and reach into a Docker and a Runner.

"""`trial_setup` and `bindings`, against a fake gateway and a fake launcher.

Invariant 29, for what a trial and a declaration get.
"""

import base64
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, ExitStack
import fcntl
import json
from pathlib import Path
import threading
import time
from typing import cast

import pytest

from dataforge import stopping
from dataforge.curation import (
  Actions,
  AgentState,
  CommandResult,
  Docker,
  Limits,
  load,
)
from dataforge.gateway import (
  bindings,
  Channel,
  CLAUDE_ENV,
  Client,
  CODEX_ENV,
  hooks,
  Spend,
  start_forwarder,
  trial_setup,
)
from dataforge.gateway.session import holders
from dataforge.runtime.claude import Runner
from tests.gateway.fakes import (
  call,
  directory,
  executable,
  file,
  Launcher,
  package,
  tarball,
  upstream,
  wait_for,
)

type Serve = Callable[..., Callable[[], AbstractContextManager[object]]]
ROUTE = {
  Channel.CLAUDE: "/v1/messages",
  Channel.CODEX: "/backend-api/codex/responses",
}


def codex(tmp_path: Path) -> Client:
  return tarball(
    tmp_path / "codex.tar.gz",
    [
      directory("bin"),
      file("bin/codex", b"#!/bin/sh\n", 0o755),
      directory("codex-path"),
      file("codex-path/rg", b"#!/bin/sh\n", 0o755),
    ],
    program="bin/codex",
  )


@pytest.mark.parametrize(
  ("agent", "channel", "command"),
  [
    ("ClaudeCode", Channel.CLAUDE, "claude"),
    ("Pi", Channel.CODEX, "pi"),
    ("Codex", Channel.CODEX, "codex"),
  ],
)
def test_a_gateway_agents_trial_gets_the_socket_forwarder_client_and_overlay(
  launcher: Launcher,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  agent: str,
  channel: Channel,
  command: str,
):
  monkeypatch.setattr(hooks, "own_dir", lambda: tmp_path / "own")
  chosen = {
    "claude-code": executable(tmp_path),
    "pi": package(tmp_path),
    "codex": codex(tmp_path),
  }
  setup = trial_setup(clients=chosen)
  with upstream() as (base, seen):
    launcher.write(base)
    with setup(
      tmp_path,
      {"import_path": f"dataforge.gateway.harbor:{agent}", "model": "m"},
      Limits(memory_gb=1),
      tmp_path,
    ) as found:
      mount, forwarder, *client = found.mounts
      assert (mount.target, mount.read_only) == (
        "/run/dataforge",
        False,
      )
      socket = mount.source / "model.sock"
      assert call(socket, ROUTE[channel])[0] == 200
      assert (forwarder.source, forwarder.target) == (
        hooks.FORWARDER,
        "/opt/dataforge/forward.py",
      )
      assert forwarder.read_only
      assert [m.target for m in client][-1] == f"/usr/local/bin/{command}"
      assert all(m.read_only for m in client)
      [overlay] = found.compose_overlay
      assert json.loads(overlay.read_text()) == {
        "services": {
          "main": {
            "network_mode": "none",
            "command": [
              "sh",
              "-c",
              "python3 /opt/dataforge/forward.py & exec sleep infinity",
            ],
          }
        }
      }
      version = chosen[{"claude": "claude-code"}.get(command, command)].version
      env = CLAUDE_ENV if channel is Channel.CLAUDE else CODEX_ENV
      assert found.env == {**env, "DATAFORGE_CLIENT_VERSION": version}
    assert not socket.exists()
  assert len(seen.requests) == 1


def test_a_capped_trial_gets_the_403_through_the_mounted_directory(
  launcher: Launcher,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
):
  # A container mounts the directory, so that the cap's replacement of the
  # socket reaches it; a mounted socket file would still be the old one.
  monkeypatch.setattr(hooks, "own_dir", lambda: tmp_path / "own")
  setup = trial_setup(
    clients={"pi": package(tmp_path)}, spend=Spend(prices={}, max_usd=1.0)
  )
  with upstream() as (base, _):
    launcher.write(base)
    with setup(
      tmp_path,
      {"import_path": "dataforge.gateway.harbor:Pi", "model": "m"},
      Limits(memory_gb=1),
      tmp_path,
    ) as found:
      socket = found.mounts[0].source / "model.sock"
      assert (found.mounts[0].source.stat().st_mode & 0o777) == 0o711
      # The reply has no usage, which caps the session.
      assert call(socket, ROUTE[Channel.CODEX])[0] == 200
      assert wait_for(lambda: not holders(socket))
      assert call(socket, ROUTE[Channel.CODEX])[0] == 403


@pytest.mark.parametrize(
  "agent",
  ["nop", "oracle", {"name": "claude-code"}, {"import_path": "other:Pi"}],
)
def test_any_other_trial_gets_nothing(
  launcher: Launcher, tmp_path: Path, agent: str | dict[str, str]
):
  setup = trial_setup()
  with setup(tmp_path, agent, Limits(memory_gb=1), tmp_path) as found:
    assert (found.mounts, dict(found.env), found.compose_overlay) == (
      (),
      {},
      (),
    )
  assert launcher.launches() == []


def test_the_codex_placeholder_carries_the_account_pi_reads():
  token = CODEX_ENV["CODEX_GATEWAY_TOKEN"]
  header, claims, signature = token.split(".")
  payload = json.loads(
    base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4))
  )
  assert payload["https://api.openai.com/auth"]["chatgpt_account_id"]
  assert json.loads(base64.urlsafe_b64decode(header + "==")) == {
    "alg": "none",
    "typ": "JWT",
  }
  assert signature == ""


DECLARATION = """\
dataforge: 1
name: gated
start: author
environments:
  box:
    image: agent:1
    mounts:
      - source: {binding: socket-dir}
        target: /run/dataforge
        read_only: false
      - {source: {binding: forwarder}, target: /opt/dataforge/forward.py}
      - source: {binding: client/claude-code}
        target: /usr/local/bin/claude
    env: {binding: claude-env}
    post_start: {binding: start-forwarder}
runners:
  claude:
    uses: claude-code
    with:
      model: a-model
      effort: high
      max_budget_usd: 1
      files: {numbered: {dir: runs}}
      serve: {uses: serve, with: {channel: CLAUDE}}
states:
  author:
    uses: agent
    env: box
    runner: claude
    with: {prompt: Write it., workdir: /app}
    transitions:
      ok: {end: passed}
      failed: {end: failed}
      error: {end: error}
"""


def test_bindings_give_a_declaration_the_gateway(
  launcher: Launcher, tmp_path: Path, socket_dir: Path
):
  claude = executable(tmp_path)
  found = bindings(
    socket_dir, clients={"claude-code": claude, "pi": package(tmp_path)}
  )
  gateway = socket_dir / "dataforge-gateway"
  assert gateway.stat().st_mode & 0o777 == 0o700
  assert found["socket-dir"] == gateway / "socket"
  assert (gateway / "socket").stat().st_mode & 0o777 == 0o711
  assert found["client/claude-code"] == claude.path
  pi = found["client/pi"]
  assert isinstance(pi, Path) and (pi / "pi" / "pi").is_file()
  assert found["claude-env"] == CLAUDE_ENV
  assert found["codex-env"] == CODEX_ENV
  with ExitStack() as stack, upstream() as (base, seen):
    launcher.write(base)
    machine = load(
      DECLARATION,
      inputs={},
      actions=Actions(),
      bindings=found,
      base_dir=tmp_path,
      stack=stack,
    )
    [state] = machine.states
    assert isinstance(state, AgentState)
    env = state.env
    [runner] = state.runners()
    assert isinstance(env, Docker) and isinstance(runner, Runner)
    assert [(m.source, m.target) for m in env.mounts] == [
      (gateway / "socket", "/run/dataforge"),
      (hooks.FORWARDER, "/opt/dataforge/forward.py"),
      (claude.path, "/usr/local/bin/claude"),
    ]
    assert env.env == CLAUDE_ENV
    assert env.post_start is start_forwarder
    with runner._serve():
      sock = gateway / "socket" / "model.sock"
      assert sock.stat().st_mode & 0o777 == 0o666
      assert call(sock, "/v1/messages")[0] == 200
  assert len(seen.requests) == 1


def test_the_serves_of_one_socket_take_turns(
  launcher: Launcher, socket_dir: Path
):
  serve = cast(Serve, bindings(socket_dir, clients={})["serve"])
  inside = threading.Event()
  release = threading.Event()
  second = threading.Event()

  def first() -> None:
    with serve(channel="CLAUDE")():
      inside.set()
      _ = release.wait(10)

  def then() -> None:
    with serve(channel="CODEX")():
      second.set()

  threads = [threading.Thread(target=first), threading.Thread(target=then)]
  threads[0].start()
  assert inside.wait(10)
  threads[1].start()
  assert not second.wait(1)
  release.set()
  assert second.wait(10)
  for thread in threads:
    thread.join(10)
  assert [launch["argv"][1] for launch in launcher.launches()] == [
    "claude",
    "pi",
  ]


def test_a_serve_with_max_usd_needs_prices(tmp_path: Path):
  serve = cast(Serve, bindings(tmp_path, clients={})["serve"])
  with pytest.raises(ValueError, match="needs the prices"):
    _ = serve(channel="CLAUDE", max_usd=1.0)
  with pytest.raises(ValueError, match="'NONE' is not a valid Channel"):
    _ = serve(channel="NONE")


def test_start_forwarder_starts_it_and_waits_until_it_listens():
  calls: list[list[str]] = []

  def cli(args: Sequence[str]) -> CommandResult:
    calls.append(list(args))
    probes = sum(1 for c in calls if "-c" in c)
    return CommandResult(1 if "-c" in args and probes < 3 else 0, "")

  env = Docker("agent:1", cli=cli)
  env._id = "c1"
  start_forwarder(env)
  assert calls[0] == [
    "exec",
    "-d",
    "c1",
    "python3",
    "/opt/dataforge/forward.py",
  ]
  assert len(calls) == 4


def test_start_forwarder_stops_waiting_once_its_calls_stop_is_set():
  # A forwarder that never listens: the wait would last FORWARDER_TIMEOUT.
  def cli(args: Sequence[str]) -> CommandResult:
    return CommandResult(1 if "-c" in args else 0, "")

  env = Docker("agent:1", cli=cli)
  env._id = "c1"
  stop = stopping.Stop()
  threading.Timer(0.3, stop.set).start()
  started = time.monotonic()
  with pytest.raises(stopping.Stopped):
    stopping.under(stop, lambda: start_forwarder(env))()
  assert time.monotonic() - started < 2


def test_a_serve_that_waits_for_another_ends_once_its_calls_stop_is_set(
  launcher: Launcher, tmp_path: Path
):
  serve = cast(Serve, bindings(tmp_path / "root", clients={})["serve"])
  lock = tmp_path / "root" / "dataforge-gateway" / "serve.lock"
  stop = stopping.Stop()

  def enter() -> None:
    with serve(channel="CLAUDE")():
      pass

  with lock.open("a") as other:
    # Another serve holds the lock; were the wait not stopped, it would get
    # the lock after 5 s.
    fcntl.flock(other, fcntl.LOCK_EX)
    unlock = threading.Timer(5, fcntl.flock, (other, fcntl.LOCK_UN))
    unlock.start()
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    try:
      with pytest.raises(stopping.Stopped):
        stopping.under(stop, enter)()
      assert time.monotonic() - started < 2
    finally:
      unlock.cancel()
      unlock.join()
  assert launcher.launches() == []
