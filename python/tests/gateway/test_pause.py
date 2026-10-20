"""Pauses and session slots, against a fake gateway and a fake launcher.

Invariant 27: a 429 or a quota refusal of a channel holds that channel's calls
only, the pause stops for good as `Backoff` and `REFUSALS` say, and `sessions`
caps each channel's proxies.
"""

import contextlib
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import threading
import time
from typing import cast

import pytest

from dataforge.curation.actions import Limits
from dataforge.gateway import (
  Backoff,
  Channel,
  hooks,
  Pause,
  pause,
  pausing,
  proxy,
  QuotaError,
  serve,
  sessions,
  trial_setup,
)
from tests.gateway.fakes import (
  call,
  executable,
  Launcher,
  QUOTA,
  serving,
  upstream,
)

ROUTE = {
  Channel.CLAUDE: "/v1/messages",
  Channel.CODEX: "/backend-api/codex/responses",
}


def limited(first: int, times: list[float] | None = None):
  # Answers the first `first` requests with 429 and the rest with a reply,
  # and notes when each came in `times`.
  def respond(handler: BaseHTTPRequestHandler, n: int) -> None:
    if times is not None:
      times.append(time.monotonic())
    status, body = (429, b"") if n <= first else (200, b"one")
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    _ = handler.wfile.write(body)

  return respond


def test_a_429_holds_the_calls_of_its_channel_only(socket_dir: Path):
  claude, codex = socket_dir / "claude.sock", socket_dir / "codex.sock"
  times: dict[str, float] = {}

  def timed(name: str, path: Path, url: str) -> None:
    assert call(path, url) == (200, b"one")
    times[name] = time.monotonic()

  with (
    upstream(limited(1)) as (base, seen),
    pausing(Backoff(start_s=1.0)) as pauses,
    proxy.make_server(
      Channel.CLAUDE, claude, base, "t", pause=pauses[Channel.CLAUDE]
    ) as a,
    proxy.make_server(
      Channel.CODEX, codex, base, "t", pause=pauses[Channel.CODEX]
    ) as b,
  ):
    _ = serving(a), serving(b)
    start = time.monotonic()
    first = threading.Thread(
      target=timed, args=("first", claude, ROUTE[Channel.CLAUDE])
    )
    first.start()
    deadline = time.monotonic() + 10
    while not seen.requests and time.monotonic() < deadline:
      time.sleep(0.01)
    timed("codex", codex, ROUTE[Channel.CODEX])
    timed("claude", claude, ROUTE[Channel.CLAUDE])
    first.join()
    a.shutdown()
    b.shutdown()
  assert times["codex"] - start < 0.8
  assert times["claude"] - start >= 0.9
  assert times["first"] - start >= 0.9


def test_a_429_pauses_and_the_call_gets_the_later_answer(socket_dir: Path):
  path = socket_dir / "model.sock"
  times: list[float] = []
  with (
    upstream(limited(2, times)) as (base, _),
    pausing(Backoff(start_s=0.2, max_s=0.3)) as pauses,
    proxy.make_server(
      Channel.CLAUDE, path, base, "t", pause=pauses[Channel.CLAUDE]
    ) as server,
  ):
    _ = serving(server)
    assert call(path, "/v1/messages") == (200, b"one")
    server.shutdown()
  first, second, third = times
  assert second - first >= 0.2
  assert third - second >= 0.3


def test_a_429_past_the_limit_reaches_the_caller_and_stops_the_pause(
  socket_dir: Path,
):
  path = socket_dir / "model.sock"
  backoff = Backoff(start_s=0.1, max_s=0.1, limit_s=0.25)
  with (
    upstream(limited(10)) as (base, seen),
    pausing(backoff) as pauses,
    proxy.make_server(
      Channel.CODEX, path, base, "t", pause=pauses[Channel.CODEX]
    ) as server,
  ):
    _ = serving(server)
    assert call(path, ROUTE[Channel.CODEX]) == (429, b"")
    server.shutdown()
    why = pauses[Channel.CODEX].stopped()
    assert why is not None and "past the limit" in why
    assert pauses[Channel.CLAUDE].stopped() is None
  # Pauses of 0.1 s fit at most three 429s into 0.25 s, the fourth is passed
  # on; a slow runner fits fewer.
  assert 2 <= len(seen.requests) <= 4


def test_a_quota_refusal_pauses_and_the_proxy_starts_once_it_is_over(
  launcher: Launcher, socket_dir: Path, capfd: pytest.CaptureFixture[str]
):
  launcher.write("http://127.0.0.1:9", refuse=1)
  with pausing(Backoff(start_s=0.5)) as pauses:
    with serve(Channel.CODEX, socket_dir / "model.sock"):
      pass
    assert pauses[Channel.CODEX].paused() == 0.5
    assert pauses[Channel.CODEX].stopped() is None
    assert pauses[Channel.CLAUDE].paused() == 0
  first, second = (cast(float, launch["at"]) for launch in launcher.launches())
  assert second - first >= 0.5
  assert QUOTA in capfd.readouterr().err


def test_a_quota_refusal_outside_pausing_fails_the_trials_setup(
  launcher: Launcher, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(hooks, "own_dir", lambda: tmp_path / "own")
  launcher.write("http://127.0.0.1:9", refuse=1)
  setup = trial_setup(clients={"claude-code": executable(tmp_path)})
  agent = {"import_path": "dataforge.gateway.harbor:ClaudeCode"}
  with (
    pytest.raises(QuotaError, match=QUOTA),
    setup(tmp_path, agent, Limits(memory_gb=1), tmp_path),
  ):
    pass
  assert len(launcher.launches()) == 1


@pytest.mark.parametrize(
  ("line", "code"),
  [
    ("redcoast: machine m is at its limit of 16 sessions", 3),
    (QUOTA, 1),
    ("redcoast-client: no session", 3),
  ],
)
def test_other_failures_of_the_launcher_are_errors(
  launcher: Launcher, socket_dir: Path, line: str, code: int
):
  launcher.write("http://127.0.0.1:9", refuse=1, line=line, code=code)
  with pausing(Backoff(start_s=0.5)) as pauses:
    with (
      pytest.raises(RuntimeError, match=f"model proxy exited with {code}$"),
      serve(Channel.CLAUDE, socket_dir / "model.sock"),
    ):
      pass
    assert pauses[Channel.CLAUDE].remaining() <= 0
  assert len(launcher.launches()) == 1


@pytest.mark.parametrize(
  ("limit_s", "why"),
  [
    # The first refusal pauses; three more, each once a pause is over, stop it.
    (None, "refused 3 sessions in a row"),
    (0.15, "past the limit of 0s"),
  ],
)
def test_a_quota_that_does_not_come_back_stops_the_pause_for_good(
  launcher: Launcher, socket_dir: Path, limit_s: float | None, why: str
):
  launcher.write("http://127.0.0.1:9", refuse=99)
  backoff = Backoff(start_s=0.1, max_s=0.1, limit_s=limit_s)
  with pausing(backoff) as pauses:
    with (
      pytest.raises(QuotaError, match=why) as raised,
      serve(Channel.CLAUDE, socket_dir / "model.sock"),
    ):
      pass
    assert QUOTA in str(raised.value)
    assert pauses[Channel.CLAUDE].stopped() == str(raised.value)
    launches = len(launcher.launches())
    assert launches == 4 if limit_s is None else launches >= 2
    # Once stopped, no proxy of the channel starts.
    with (
      pytest.raises(QuotaError, match=why),
      serve(Channel.CLAUDE, socket_dir / "model.sock"),
    ):
      pass
    assert len(launcher.launches()) == launches


@pytest.mark.parametrize(
  ("cut_s", "pause_s", "cut"),
  [
    (0.2, 1.0, True),
    # Paused less than the cut.
    (10.0, 1.0, False),
    # The pause was over when the block ended.
    (0.05, 0.1, False),
  ],
)
@pytest.mark.usefixtures("launcher")
def test_a_block_that_ends_in_a_long_pause_is_cut(
  socket_dir: Path,
  monkeypatch: pytest.MonkeyPatch,
  cut_s: float,
  pause_s: float,
  cut: bool,
):
  monkeypatch.setattr(pause, "CUT_S", cut_s)
  with (
    pausing(Backoff(start_s=0.01)) as pauses,
    pytest.raises(QuotaError, match="paused")
    if cut
    else contextlib.nullcontext(),
    serve(Channel.CLAUDE, socket_dir / "model.sock"),
  ):
    assert pauses[Channel.CLAUDE].limited(retry_after_s=pause_s)
    time.sleep(0.3)


RECOVERY = "2026-10-20T07:00:00Z"
AT_RECOVERY = 1792479600.0


@pytest.mark.parametrize(
  ("line", "now", "pause_s"),
  [
    (f"{QUOTA}, earliest recovery {RECOVERY}", AT_RECOVERY - 3146, 3146),
    (QUOTA, AT_RECOVERY - 3146, 60),
    # A recovery that has passed: the backoff's pause.
    (f"{QUOTA}, earliest recovery {RECOVERY}", AT_RECOVERY + 5, 60),
  ],
)
def test_a_refusal_pauses_until_its_recovery_else_by_the_backoff(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  line: str,
  now: float,
  pause_s: float,
):
  monkeypatch.setattr(time, "time", lambda: now)
  found = Pause.create(tmp_path / "pause.json", Backoff())
  assert found.refused(line)
  assert found.remaining() == pause_s


def test_a_refusal_during_a_pause_makes_it_last_until_its_recovery(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(time, "time", lambda: AT_RECOVERY - 3146)
  found = Pause.create(tmp_path / "pause.json", Backoff())
  assert found.limited()
  assert found.remaining() == 60
  assert found.refused(f"{QUOTA}, earliest recovery {RECOVERY}")
  assert found.remaining() == 3146
  # Joining a pause is no refusal once a pause was over.
  assert json.loads(found.path.read_text())["refusals"] == 0


def test_a_pause_that_would_pass_the_limit_stops_at_once(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(time, "time", lambda: AT_RECOVERY - 3146)
  found = Pause.create(tmp_path / "pause.json", Backoff(limit_s=1800))
  assert not found.refused(f"{QUOTA}, earliest recovery {RECOVERY}")
  why = found.stopped()
  assert why is not None and "past the limit of 1800s" in why
  assert found.remaining() == 0


@pytest.mark.parametrize("capped", [True, False])
@pytest.mark.usefixtures("launcher")
def test_at_most_limit_proxies_of_each_channel_run_at_once(
  socket_dir: Path, capped: bool
):
  # Inside `sessions(1)`, a second proxy of a channel starts only once the
  # first ended, and one of the other channel starts at once; outside, all
  # listen at once.
  up = {"second": threading.Event(), "other": threading.Event()}

  def start(name: str, channel: Channel) -> None:
    with serve(channel, socket_dir / name / "model.sock"):
      up[name].set()

  with sessions(1) if capped else contextlib.nullcontext():
    with serve(Channel.CLAUDE, socket_dir / "first" / "model.sock"):
      threads = [
        threading.Thread(target=start, args=("second", Channel.CLAUDE)),
        threading.Thread(target=start, args=("other", Channel.CODEX)),
      ]
      for thread in threads:
        thread.start()
      assert up["other"].wait(10)
      both = up["second"].wait(2 if capped else 10)
    for thread in threads:
      thread.join(20)
  assert both is not capped
  assert up["second"].is_set()
