"""Pauses of model calls after a 429 or a quota refusal, shared by proxies.

Inside `pausing`, the proxies `serve` starts for one channel share one `Pause`,
kept in a file of the state directory (`dataforge.statedir`) under a lock, so
that the proxies of every process of the machine share it: a 429 from the
gateway holds every call of that channel, and the proxy that got it waits and
sends it again, so that the client gets the answer late instead of the 429. A
session the gateway refuses for its quota pauses the channel too. The other
channel's calls go on. Inside `sessions`, at most a given number of proxies of
each channel run at once on the machine, counted in leases
(`dataforge.leases`) of the state directory.
"""

from collections.abc import Callable, Generator, Mapping
import contextlib
from dataclasses import dataclass
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import typing

from dataforge import stopping
from dataforge.gateway.channels import Channel
from dataforge.leases import Lease, Leases
from dataforge.statedir import state_dir

# redcoast-client exits with 3 on any failure before the client starts; when
# the gateway refused the session, it prints the refusal's message first, as a
# line of its own. Only the quota's refusal starts with `QUOTA_REFUSAL`; it ends
# with the earliest recovery when the gateway knows it (redcoast v0.1.11,
# `session/server.go`, read on 2026-10-20). Both channels share that server.
LAUNCHER_FAILURE = 3
QUOTA_REFUSAL = "redcoast: local gateway quota exhausted"
_RECOVERY = re.compile(r", earliest recovery (\S+)$")
# Quota refusals in a row, each once a pause was over, after which the pause
# stops: a recovery the gateway named that did not come, or a quota the
# backoff found spent three times, is for a person to look at; three spans a
# recovery a few seconds late, which the next pause covers.
REFUSALS = 3
# How long a block's calls may be paused, the pause still under way when it
# ends, before what it served counts as cut (`QuotaError`): Claude Code gives
# up on a request with no answer after about 6 minutes.
CUT_S = 360.0
# How long after a pause ended a 429 or a quota refusal still goes on with
# its run of pauses: a proxy sends a held call again, and a launcher starts
# again, within seconds of the pause's end.
LAPSE_S = 900.0
# How often a paused request looks whether its caller still waits for it.
POLL_S = 1.0
# What began a pause or last made it longer.
PauseCause = typing.Literal["429", "quota"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Backoff:
  """How long a channel's model calls pause after a 429 or a quota refusal.

  The first 429 pauses them for `start_s`; each 429 that comes once a pause is
  over, before any other answer, pauses them `factor` times as long as the
  pause before, up to `max_s`, and at least as long as the 429's `Retry-After`
  asks. Any other answer once a pause is over ends the run of pauses. A session
  the gateway refuses for its quota pauses them until the recovery the refusal
  names, or as a 429 without `Retry-After` does when it names none.

  Attributes:
    start_s: The first pause, in seconds.
    max_s: The longest pause, in seconds.
    factor: What each pause is multiplied by; 1 keeps it fixed.
    limit_s: The longest run of pauses, in seconds, after which the pause
      stops for good; None for no limit.
  """

  start_s: float = 60.0
  max_s: float = 900.0
  factor: float = 2.0
  limit_s: float | None = None


class _BackoffState(typing.TypedDict):
  start_s: float
  max_s: float
  factor: float
  limit_s: float | None


class Lifted(typing.TypedDict):
  """Who lifted a pause under way, and when.

  Attributes:
    by: The user and host that ran the command.
    at: When, in seconds since the epoch.
  """

  by: str
  at: float


class _State(typing.TypedDict):
  # What a pause's file holds (`Pause`).
  backoff: _BackoffState
  until: float
  paused_s: float
  delay: float | None
  since: float | None
  refusals: int
  refusal: str | None
  cause: PauseCause | None
  stopped: str | None
  # Absent from files written before `Pause.lift` existed.
  lifted: typing.NotRequired[Lifted | None]


class QuotaError(RuntimeError):
  """The gateway's quota stopped a proxy, or cut what it served."""


class Pause:
  """A pause of one channel's model calls, shared by processes through a file.

  The file holds the `Backoff`, the time the pause ends (`until`), the last
  pause's length (`delay`), when the run of pauses began (`since`), how long
  all pauses together last (`paused_s`), the quota refusals in a row that came
  once a pause was over (`refusals`), the gateway's last quota refusal
  (`refusal`), what began or last made longer the latest pause (`cause`) and,
  once the pause stopped for good, why (`stopped`). A stopped pause holds no
  call: a 429 goes on to the client and no proxy starts. A file that is gone
  holds no pause. The pause `lift` ended early also holds who lifted it and
  when (`lifted`).
  """

  def __init__(self, path: Path):
    """Use the pause a file holds.

    Args:
      path: The file, as `create` wrote it.
    """
    self.path: Path = path
    self._backoff: Backoff = Backoff()

  @classmethod
  def create(cls, path: Path, backoff: Backoff) -> Pause:
    """Write a new pause, with none under way.

    Args:
      path: The file, created anew.
      backoff: How long to pause.

    Returns:
      The pause.
    """
    _write(path, _new(backoff))
    pause = cls(path)
    pause._backoff = backoff
    return pause

  @classmethod
  def shared(cls, path: Path, backoff: Backoff) -> Pause:
    """Use the pause a file holds, written as `create` does when missing.

    A pause the file already holds keeps its state, only its backoff set to
    `backoff`, so that two processes that come at once use the same pause.

    Args:
      path: The file.
      backoff: How long to pause.

    Returns:
      The pause.
    """
    new = _written(path, _new(backoff))
    try:
      with contextlib.suppress(FileExistsError):
        os.link(new, path)
    finally:
      new.unlink()
    pause = cls(path)
    pause._backoff = backoff
    with pause._state() as state:
      state["backoff"] = _new(backoff)["backoff"]
    return pause

  @contextlib.contextmanager
  def _state(self) -> Generator[_State]:
    # The file's state, under the lock of the file beside it; what the block
    # leaves in it is written to a new file that then takes the file's name,
    # so that a process killed while it writes leaves the file whole.
    lock = self.path.with_name(f"{self.path.name}.lock")
    with lock.open("a") as f:
      fcntl.flock(f, fcntl.LOCK_EX)
      try:
        state = typing.cast(_State, json.loads(self.path.read_text()))
      except FileNotFoundError:
        # A file removed by hand holds no pause; a change is written anew.
        state = _new(self._backoff)
      before = state.copy()
      yield state
      if state != before:
        _write(self.path, state)

  def remaining(self) -> float:
    """Return how long the pause under way lasts.

    Returns:
      Seconds until it ends; 0 or less when none is under way or the pause
      stopped.
    """
    with self._state() as state:
      if state["stopped"] is not None:
        return 0.0
      return state["until"] - time.time()

  def paused(self) -> float:
    """Return how long the pauses so far have lasted, up to now.

    Returns:
      Seconds.
    """
    with self._state() as state:
      return state["paused_s"] - max(state["until"] - time.time(), 0.0)

  def stopped(self) -> str | None:
    """Return why the pause stopped for good.

    Returns:
      Why, with the gateway's last quota refusal; None while it goes on.
    """
    with self._state() as state:
      return state["stopped"]

  def wait(self, gone: Callable[[], bool] = lambda: False) -> bool:
    """Wait while a pause is under way.

    Args:
      gone: Whether whoever waits for the call has stopped waiting; it is
        asked every `POLL_S` seconds.

    Returns:
      False when `gone` said so, else True.

    Raises:
      stopping.Stopped: The stop of the call this thread runs was set first.
    """  # noqa: DOC502
    while (left := self.remaining()) > 0:
      if gone():
        return False
      stopping.check()
      time.sleep(min(left, POLL_S))
    return not gone()

  def limited(self, retry_after_s: float | None = None) -> bool:
    """Pause after a 429, unless another call already did.

    Args:
      retry_after_s: What the 429's `Retry-After` asked, in seconds.

    Returns:
      Whether the call waits and goes again; False once the pause stopped,
      so that the 429 is passed on.
    """
    now = time.time()
    with self._state() as state:
      if not _goes_on(state, now):
        return False
      if state["until"] > now:
        return True
      delay = max(_next(state), retry_after_s or 0)
      if not _begin(state, now, delay, "429"):
        return False
    _ = sys.stderr.write(f"gateway: 429, pausing {delay:.0f}s\n")
    return True

  def refused(self, refusal: str) -> bool:
    """Pause after the gateway refused a session for its quota.

    The pause lasts until the recovery the refusal names, when that is still
    to come, else as long as a 429 without `Retry-After` would make it; a
    refusal during a pause makes it last until the recovery it names, when
    that comes later. The `REFUSALS`th refusal in a row that comes once a pause
    is over, with no other answer between, stops the pause for good.

    Args:
      refusal: The launcher's line, which starts with `QUOTA_REFUSAL`.

    Returns:
      Whether the proxy waits and starts again; False once the pause stopped.
    """
    recovery = _recovery(refusal)
    now = time.time()
    with self._state() as state:
      state["refusal"] = refusal
      if not _goes_on(state, now):
        return False
      if state["until"] > now:
        return _extend(state, now, recovery)
      if state["delay"] is not None:
        state["refusals"] += 1
        if state["refusals"] >= REFUSALS:
          _stop(
            state,
            now,
            (
              f"the gateway refused {REFUSALS} sessions in a row for its"
              " quota, each once a pause was over"
            ),
          )
          return False
      delay = (
        recovery - now
        if recovery is not None and recovery > now
        else _next(state)
      )
      if not _begin(state, now, delay, "quota"):
        return False
    _ = sys.stderr.write(
      f"gateway: a session refused for the quota, pausing {delay:.0f}s\n"
    )
    return True

  def lift(self, by: str) -> bool:
    """End the pause under way now, as a person decided.

    The pause ends as if its time had come: calls held by it go on, so this
    lets through the load the gateway's limit was holding back, and is for a
    person to decide. The backoff and the run of pauses are left as they are,
    and who lifted the pause and when is kept in the file. A pause that
    stopped for good stays stopped (`clear`).

    Args:
      by: Who lifts it.

    Returns:
      Whether a file holds the pause; a missing file is left missing.
    """
    if not self.path.exists():
      return False
    now = time.time()
    with self._state() as state:
      left = max(state["until"] - now, 0.0)
      state.update(
        until=state["until"] - left, paused_s=state["paused_s"] - left
      )
      state["lifted"] = {"by": by, "at": now}
    return True

  def clear(self) -> None:
    """Let the work go on after the pause stopped for good, as a person decided.

    A stopped pause loses its stop and the run of pauses and refusals before
    it; a pause that did not stop is left as it is.
    """
    with self._state() as state:
      if state["stopped"] is not None:
        state.update(
          stopped=None,
          delay=None,
          since=None,
          refusals=0,
          refusal=None,
          cause=None,
        )

  def answered(self) -> None:
    """End the run of pauses: the gateway gave an answer other than 429.

    An answer while a pause is under way, to a call sent before it began,
    leaves the run as it is, so that the pause keeps its start.
    """
    with self._state() as state:
      if state["until"] > time.time():
        return
      if state["delay"] is not None or state["refusals"]:
        state.update(delay=None, since=None, refusals=0)


def _written(path: Path, state: _State) -> Path:
  # A new file beside `path` that holds `state`.
  with tempfile.NamedTemporaryFile(
    "w", dir=path.parent, prefix=f".{path.name}.", delete=False
  ) as f:
    json.dump(state, f)
  return Path(f.name)


def _write(path: Path, state: _State) -> None:
  # Gives `path` the state whole.
  _ = _written(path, state).replace(path)


def _new(backoff: Backoff) -> _State:
  # A pause's state with none under way.
  return {
    "backoff": {
      "start_s": backoff.start_s,
      "max_s": backoff.max_s,
      "factor": backoff.factor,
      "limit_s": backoff.limit_s,
    },
    "until": 0.0,
    "paused_s": 0.0,
    "delay": None,
    "since": None,
    "refusals": 0,
    "refusal": None,
    "cause": None,
    "stopped": None,
  }


def _goes_on(state: _State, now: float) -> bool:
  # Whether the pause still holds calls; stops it once the run of pauses has
  # lasted the backoff's limit. A run that no call went on with for `LAPSE_S`
  # after its last pause ended, such as one a process that has since ended
  # left, is over, as an answer would have ended it.
  if state["stopped"] is not None:
    return False
  if now - state["until"] > LAPSE_S:
    state.update(delay=None, since=None, refusals=0)
  limit = state["backoff"]["limit_s"]
  since = state["since"] if state["since"] is not None else now
  if limit is not None and now - since >= limit:
    _stop(
      state,
      now,
      f"the pauses lasted {now - since:.0f}s, past the limit of {limit:.0f}s",
    )
    return False
  return True


def _next(state: _State) -> float:
  # The next pause's length by the backoff, from the last one's.
  backoff = Backoff(**state["backoff"])
  if state["delay"] is None:
    return backoff.start_s
  return min(state["delay"] * backoff.factor, backoff.max_s)


def _begin(state: _State, now: float, delay: float, cause: PauseCause) -> bool:
  # Begins a pause of `delay` for `cause`, unless it would carry the run past
  # the limit.
  since = state["since"] if state["since"] is not None else now
  if not _fits(state, now, since, now + delay):
    return False
  state.update(
    until=now + delay,
    delay=delay,
    since=since,
    paused_s=state["paused_s"] + delay,
    cause=cause,
  )
  return True


def _extend(state: _State, now: float, recovery: float | None) -> bool:
  # Makes the pause under way last until `recovery` when that is later,
  # unless it would carry the run past the limit.
  if recovery is None or recovery <= state["until"]:
    return True
  since = state["since"] if state["since"] is not None else now
  if not _fits(state, now, since, recovery):
    return False
  state.update(
    until=recovery,
    paused_s=state["paused_s"] + recovery - state["until"],
    cause="quota",
  )
  return True


def _fits(state: _State, now: float, since: float, until: float) -> bool:
  # Whether a pause until `until` keeps the run within the limit; stops the
  # pause for good when it would not, so that the work stops at the limit
  # rather than once the pause is over.
  limit = state["backoff"]["limit_s"]
  if limit is None or until - since <= limit:
    return True
  _stop(
    state,
    now,
    (
      f"the next pause would carry the pauses to {until - since:.0f}s, past"
      f" the limit of {limit:.0f}s"
    ),
  )
  return False


def _stop(state: _State, now: float, why: str) -> None:
  # The pause under way ends now, and none starts again.
  if state["refusal"] is not None:
    why += f"; the gateway's last quota refusal: {state['refusal']}"
  left = max(state["until"] - now, 0.0)
  state.update(
    stopped=why, until=state["until"] - left, paused_s=state["paused_s"] - left
  )
  _ = sys.stderr.write(f"gateway: the pause stops for good: {why}\n")


def _recovery(refusal: str) -> float | None:
  # The time a quota refusal names for the earliest recovery, in seconds since
  # the epoch, as `Pause` keeps times; None when it names none.
  found = _RECOVERY.search(refusal.strip())
  if found is None:
    return None
  try:
    return datetime.datetime.fromisoformat(found.group(1)).timestamp()
  except ValueError:
    return None


@dataclass(frozen=True, slots=True)
class Slots:
  """The gateway sessions a channel's proxies hold on the machine, capped.

  Attributes:
    leases: One lease per session held, in whatever process.
    limit: The most at once.
  """

  leases: Leases
  limit: int

  def take(self) -> Lease:
    """Hold a session's slot, waiting until fewer than `limit` are held.

    Returns:
      The slot, held until it is released or its process ends.
    """
    return self.leases.wait(1, lambda held: len(held) < self.limit)


_active: dict[Channel, Pause] = {}
_slots: dict[Channel, Slots] = {}


@contextlib.contextmanager
def pausing(
  backoff: Backoff, *, clear: bool = False
) -> Generator[Mapping[Channel, Pause]]:
  """Share one pause per channel among the proxies `serve` starts in the block.

  Each channel's pause is `pause/<channel>.json` of the state directory, which
  every process of the machine inside `pausing` shares and which stays after
  the block: a pause one of them began holds the others' calls too, and a
  pause that stopped for good stays stopped for all of them, in later blocks
  too, until it is cleared (`Pause.clear`).

  Args:
    backoff: How long to pause, set on the pauses as the block begins.
    clear: Clear a pause that stopped for good as the block begins, for work
      a person started again.

  Yields:
    Each channel's pause.
  """
  root = state_dir() / "pause"
  root.mkdir(mode=0o700, exist_ok=True)
  pauses = {c: Pause.shared(root / f"{c.value}.json", backoff) for c in Channel}
  if clear:
    for pause in pauses.values():
      pause.clear()
  _active.update(pauses)
  try:
    yield pauses
  finally:
    _active.clear()


@contextlib.contextmanager
def sessions(limit: int) -> Generator[None]:
  """Let at most `limit` proxies of each channel run on the machine.

  Each proxy holds one gateway session, and the gateway caps how many of each
  channel a machine holds at once. The proxies `serve` starts in the block
  count, with those of every other process of the machine inside `sessions`,
  in `sessions/<channel>/` of the state directory; a session of a process that
  ended, killed too, no longer counts.

  Args:
    limit: The most at once, per channel; processes that give different
      limits each keep to their own.

  Yields:
    Nothing; the block runs under the limit.
  """
  root = state_dir() / "sessions"
  _slots.update({c: Slots(Leases(root / c.value), limit) for c in Channel})
  try:
    yield
  finally:
    _slots.clear()


def active(channel: Channel) -> Pause | None:
  """Return the pause `pausing` shares for a channel, or None outside it.

  Args:
    channel: The channel.

  Returns:
    The pause, or None.
  """
  return _active.get(channel)


def slots(channel: Channel) -> Slots | None:
  """Return the slots `sessions` gives a channel, or None outside it.

  Args:
    channel: The channel.

  Returns:
    The slots, or None.
  """
  return _slots.get(channel)


def check_cut(pause: Pause, paused_s: float) -> None:
  """Raise when the calls of a block that ended were cut by the pause.

  Args:
    pause: The block's pause.
    paused_s: How long its calls were paused while it ran.

  Raises:
    QuotaError: The pause stopped, or the calls were paused `CUT_S` or longer
      and the pause is still under way.
  """
  if (why := pause.stopped()) is not None:
    raise QuotaError(why)
  if paused_s >= CUT_S and pause.remaining() > 0:
    why = f"its model calls were paused {paused_s:.0f}s, and the pause was"
    raise QuotaError(f"{why} still under way when it ended")
