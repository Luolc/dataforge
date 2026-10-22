"""Actions: agent, script, judge and trial, each in an env; fan_out.

What an action does (agent, script) and where it runs (Host, or a Docker
container) are independent. Memory is shared through a ResourcePool, whose
reservations every process of the machine counts: a Docker env reserves its
memory_gb from start to stop, a trial what its runner's memory_gb gives, a host
script its memory_gb; each waits while the pool is short. CPUs are not
reserved; a container's cpus and cpu_shares divide them. Image builds, whose
writes to the disk no limit of a container reaches, take one of the pool's
build slots each.
"""

import codecs
from collections.abc import Callable, Generator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import AbstractContextManager, contextmanager, nullcontext
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, UTC
import errno
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
from types import TracebackType
from typing import cast, NoReturn, Protocol, Self

from dataforge import stopping
from dataforge.curation.engine import (
  ActionRecord,
  Context,
  Data,
  Input,
  TrialRecord,
)
from dataforge.leases import Lease, Leases
from dataforge.statedir import state_dir

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
  """The result of one command.

  Attributes:
    exit_code: Its exit status.
    log: Its stdout followed by its stderr.
  """

  exit_code: int
  log: str


@dataclass(frozen=True)
class ActionResult:
  """The result of an agent or a script.

  Attributes:
    status: "ok" or "failed".
    outputs: The outputs present, by name.
    log: What the action printed, and why it failed if it did.
    value: The parsed JSON answer of an agent run with an output_schema, such
      as a judge.
    cost_usd: What it cost, in US dollars; for an agent whose runs did not
      all report their cost, what those that did cost.
    run: An agent's last run.
    validation: The last run of an agent's validator on the host, if it ran.
    fixes: How many times the validator resumed an agent's session.
    cost_known: Whether cost_usd is all it cost: False when a run of the
      agent did not report its cost.
  """

  status: str
  outputs: Mapping[str, Path]
  log: str
  value: object = None
  cost_usd: float = 0.0
  run: AgentRun | None = None
  validation: CommandResult | None = None
  fixes: int = 0
  cost_known: bool = True

  @property
  def ok(self) -> bool:
    """Whether the status is "ok"."""
    return self.status == "ok"


BUILDS = 1
"""How many image builds a pool lets run at once unless it is given another
number."""


class ResourcePool:
  """The memory the actions of every process of the machine share, in GB.

  Each reservation is a lease (`dataforge.leases`) in `memory/` of the state
  directory (`dataforge.statedir`), so that every pool of the machine counts
  the reservations of all of them, in whatever process. A reservation that
  would take what they hold past this pool's memory_gb, or, with max_running,
  their number past it, waits until it fits; one larger than the pool is a
  ValueError. The reservations of a process that ended, killed too, are given
  back. It counts whole MiB, so that a fraction of a GB is given back exactly
  as it was taken.

  Image builds also take a build slot (`build`), a lease in `builds/` of the
  state directory, counted the same way: no more than this pool's builds hold
  one at once, in all processes.
  """

  def __init__(
    self,
    memory_gb: float,
    max_running: int | None = None,
    builds: int = BUILDS,
  ) -> None:
    """Make a pool.

    Args:
      memory_gb: The pool's memory, at most what the machine has available
        with what the reservations of every process hold, which the memory
        they reserved may already be using.
      max_running: How many reservations may be held at once; None for any.
      builds: How many build slots may be held at once.

    Raises:
      ValueError: When memory_gb is not positive or is more than is
        available, or builds is less than 1.
    """
    if builds < 1:
      raise ValueError(f"a pool of {builds} build slots")
    self._leases: Leases = Leases(state_dir() / "memory")
    self._builds: Leases = Leases(state_dir() / "builds")
    available = _mem_available_gb()
    if available is not None:
      available += sum(self._leases.held()) / 1024
    if memory_gb <= 0 or (available is not None and memory_gb > available):
      raise ValueError(
        f"pool of {memory_gb} GB, but {available} GB is available"
      )
    self.memory_gb: float = memory_gb
    self.max_running: int | None = max_running
    self.builds: int = builds
    self._total_mb: int = _mb(memory_gb)
    self._held: dict[int, list[Lease]] = {}
    self._lock: threading.Lock = threading.Lock()

  @property
  def reserved_gb(self) -> float:
    """The memory reserved now, by every process of the machine."""
    return sum(self._leases.held()) / 1024

  def acquire(self, memory_gb: float) -> None:
    """Reserve memory, waiting until it fits.

    Args:
      memory_gb: The memory to reserve.

    Raises:
      ValueError: When it is more than the pool.
    """
    mb = _mb(memory_gb)
    if mb > self._total_mb:
      raise ValueError(f"{memory_gb} GB asked of a pool of {self.memory_gb} GB")
    lease = self._leases.wait(mb, lambda held: self._fits(held, mb))
    with self._lock:
      self._held.setdefault(mb, []).append(lease)

  def _fits(self, held: list[int], mb: int) -> bool:
    return sum(held) + mb <= self._total_mb and (
      self.max_running is None or len(held) < self.max_running
    )

  def release(self, memory_gb: float) -> None:
    """Give back a reservation this pool holds.

    Args:
      memory_gb: The memory it reserved.

    Raises:
      ValueError: When this pool holds no reservation of that memory.
    """
    with self._lock:
      leases = self._held.get(_mb(memory_gb))
      if not leases:
        raise ValueError(f"no reservation of {memory_gb} GB is held")
      lease = leases.pop()
    lease.release()

  @contextmanager
  def reserve(self, memory_gb: float) -> Generator[None]:
    """Hold a reservation while the with block runs.

    Args:
      memory_gb: The memory to reserve.

    Yields:
      Nothing; the memory is reserved until the block ends.
    """
    self.acquire(memory_gb)
    try:
      yield
    finally:
      self.release(memory_gb)

  @contextmanager
  def build(self, memory_gb: float | None) -> Generator[None]:
    """Hold a build slot and a reservation while the with block runs.

    For an image build, whose writes to the disk no container limit caps: it
    waits for a slot first and then for the memory, so that a build waiting
    for a slot holds no memory. A caller that holds memory of this pool
    already must not take a slot, as it would wait for one while holding
    memory that a build holding a slot may be waiting for.

    Args:
      memory_gb: The memory to reserve, as reserve takes it; None for none.

    Yields:
      Nothing; the slot and the memory are held until the block ends.
    """
    slot = self._builds.wait(1, lambda held: len(held) < self.builds)
    try:
      with nullcontext() if memory_gb is None else self.reserve(memory_gb):
        yield
    finally:
      slot.release()


def _mb(gb: float) -> int:
  return round(gb * 1024)


def _mem_available_gb() -> float | None:
  try:
    lines = Path("/proc/meminfo").read_text().splitlines()
  except OSError:
    return None
  for line in lines:
    if line.startswith("MemAvailable:"):
      return int(line.split()[1]) / 1024**2
  return None


def _reserve(
  pool: ResourcePool | None, memory_gb: float | None, build: bool = False
) -> AbstractContextManager[None]:
  if pool is None:
    return nullcontext()
  if build:
    return pool.build(memory_gb)
  if memory_gb is None:
    return nullcontext()
  return pool.reserve(memory_gb)


@dataclass(frozen=True)
class Staged:
  """An action's paths as the environment sees them.

  Attributes:
    inputs: The input paths, by name.
    outdir: Where the action writes its outputs.
    validator: The validator's path, if the action has one.
    summary: Where a script may write a Markdown summary of its run, a file
      not there yet in a directory it may write, which collect leaves at
      summary_path; None when the env gives none.
  """

  inputs: Mapping[str, str]
  outdir: str
  validator: str | None
  summary: str | None = None


SUMMARY = "summary.md"
"""The file name of an action's summary."""

SUMMARY_LIMIT = 1024 * 1024
"""The most bytes of a summary the record keeps; the rest is cut off."""

NOT_A_FILE = "(not kept: the summary is not a regular file)"
"""The summary in the record when DATAFORGE_SUMMARY is a symlink, a directory
or anything else that is not a regular file; it is never read."""


def summary_path(ctx: Context, index: int) -> Path:
  """Return where an action's summary is on the host.

  Args:
    ctx: The visit's context.
    index: The action's number in the visit.

  Returns:
    <root>/summaries/<state>/<visit>/<index>/summary.md.
  """
  root = ctx.attempt.root
  return root / "summaries" / ctx.state / str(ctx.visit) / str(index) / SUMMARY


class Env(Protocol):
  """Where an action runs. start and stop bracket each action that uses it."""

  def start(self, pool: ResourcePool | None) -> None:
    """Make the env ready for an action.

    Args:
      pool: The pool to reserve the env's memory from, if any.
    """
    ...

  def stop(self) -> None:
    """End the use that the matching start began."""
    ...

  def stage(
    self,
    ctx: Context,
    index: int,
    inputs: Mapping[str, Path],
    validator: Path | None,
  ) -> Staged:
    """Put an action's inputs and validator where the env sees them.

    Args:
      ctx: The visit's context.
      index: The action's number in the visit.
      inputs: The input paths on the host, by name.
      validator: The validator on the host, if any.

    Returns:
      The action's paths as the env sees them.
    """
    ...

  def exec(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult:
    """Run a command in the env.

    Args:
      argv: The command.
      env: Environment variables to set for it.

    Returns:
      Its result.
    """
    ...

  def collect(self, staged: Staged, outdir: Path) -> str:
    """Copy an action's outputs into the visit's outputs on the host.

    Args:
      staged: The action's paths in the env.
      outdir: The visit's output directory.

    Returns:
      What the action's log should say of the copy, such as what it left
      out; empty when nothing.
    """
    ...


@dataclass(frozen=True)
class Host:
  """The machine the engine runs on.

  Paths are host paths; outputs are written in place.
  """

  def start(self, pool: ResourcePool | None) -> None:
    """Do nothing: the host is always ready.

    Args:
      pool: Unused.
    """
    _ = pool

  def stop(self) -> None:
    """Do nothing."""

  def stage(
    self,
    ctx: Context,
    index: int,
    inputs: Mapping[str, Path],
    validator: Path | None,
  ) -> Staged:
    """Give the inputs in place and copy the validator, read-only.

    Args:
      ctx: The visit's context.
      index: The action's number in the visit.
      inputs: The input paths, by name.
      validator: The validator, if any.

    Returns:
      The action's paths.
    """
    copy = None
    if validator is not None:
      copy = (
        ctx.attempt.root
        / "validators"
        / ctx.state
        / str(ctx.visit)
        / str(index)
      )
      copy = copy / validator.name
      copy.parent.mkdir(parents=True, exist_ok=True)
      _ = shutil.copy(validator, copy)
      copy.chmod(0o555)
    return Staged(
      {name: str(path) for name, path in inputs.items()},
      str(ctx.outdir),
      None if copy is None else str(copy),
      str(summary_path(ctx, index)),
    )

  def exec(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult:
    """Run a command on the host.

    Args:
      argv: The command.
      env: Environment variables to set on top of this process's.

    Returns:
      Its result.

    Raises:
      subprocess.TimeoutExpired: It ran past what is left of the budget of a
        stopped `fan_out` (`dataforge.stopping`); it is killed.
    """  # noqa: DOC502
    done = subprocess.run(
      list(argv),
      capture_output=True,
      text=True,
      check=False,
      env={**os.environ, **env},
      timeout=stopping.left(None),
    )
    return CommandResult(done.returncode, done.stdout + done.stderr)

  def collect(self, staged: Staged, outdir: Path) -> str:
    """Do nothing: the outputs are already in place.

    Args:
      staged: Unused.
      outdir: Unused.

    Returns:
      Nothing to log.
    """
    _ = staged, outdir
    return ""


HOST = Host()


@dataclass(frozen=True)
class Mount:
  """A bind mount.

  Attributes:
    source: The directory on the host.
    target: Where it is in the container.
    read_only: Whether the container may only read it.
  """

  source: Path
  target: str
  read_only: bool = True


_CARRY = "/dataforge/carry"
"""Where Docker.replace keeps the diffs it carries, in each container."""

# The diff git apply takes whatever the tree's config says: plain a/ and b/
# paths from the top, no external diff or text conversion, no renames.
_DIFF = (
  "--cached",
  "--binary",
  "--no-color",
  "--no-ext-diff",
  "--no-textconv",
  "--no-renames",
  "--no-relative",
  "--src-prefix=a/",
  "--dst-prefix=b/",
)

_OBJECT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

_GITLINK = "160000"
"""The mode git gives a repository inside a tree, as `--raw` prints it."""


def run_docker(args: Sequence[str]) -> CommandResult:
  """Run one docker command.

  Args:
    args: The arguments after `docker`.

  Returns:
    Its result.

  Raises:
    subprocess.TimeoutExpired: It ran past what is left of the budget of a
      stopped `fan_out` (`dataforge.stopping`); it is killed.
  """  # noqa: DOC502
  done = subprocess.run(
    ["docker", *args],
    capture_output=True,
    text=True,
    check=False,
    timeout=stopping.left(None),
  )
  return CommandResult(done.returncode, done.stdout + done.stderr)


@dataclass(eq=False)
class Docker:
  """A container, kept across visits, that each action starts and stops.

  `docker create` makes it on first use, `docker start` and `docker stop`
  bracket each action in it, and leaving its `with` removes it. Between
  actions, replace puts a container of another image in its place.

  Inputs are copied in under /dataforge/<state>/<visit>/<action>/inputs, outputs
  copied out from .../outputs, and a validator copied to
  /dataforge/validator/<name>, read-only. These directories are made as root and
  outputs is left writable by all, so that any user the container runs as can
  write there; so is .../summary, where a script may write summary.md, which
  each collect of a script copies to the action's summary_path.

  Each collect leaves the visit's outputs as the action's output directory is
  then: what it copies out replaces what has the same name, and what an earlier
  collect of the same action copied out and is gone now, such as a file a fix
  deleted, is removed. Nothing else in the visit's outputs is touched. The image
  must have `sleep`. mounts, env, pre_start and post_start are the hooks for
  what a dataset adds, such as a gateway.

  Attributes:
    image: The image, which replace changes.
    mounts: Bind mounts.
    outputs: A writable mount every action writes its outputs straight into,
      instead of .../outputs, so what one visit wrote is still there in the
      next, as a session resumed across visits needs; collect copies the
      mount's source on the host into the visit's outputs, so that each visit
      keeps its own version. The container's user must be able to write the
      source, and this process to read what that user writes.
    cpus: The container's --cpus.
    cpu_shares: Its --cpu-shares.
    memory_gb: Its memory, reserved from the pool from start to stop.
    memory_swap_gb: Memory plus swap, as docker's --memory-swap counts it;
      needs memory_gb. Equal to it, the container gets no swap.
    pids: Its --pids-limit.
    network: Whether it has a network.
    labels: Its labels.
    security: Its --security-opt options.
    cap_drop: The capabilities it drops.
    user: The user it runs as.
    workdir: Its working directory.
    env: Variables set at create, left out of the repr.
    pass_env: Variables set at create from the docker command's own
      environment, by name, so that their values are not on its command
      line; one unset there is left unset.
    pre_start: Run on the host before each docker start.
    post_start: Run on the host after each docker start and before the action,
      such as to start a process in the container with exec.
    cli: Runs one docker command; tests replace it.
  """

  image: str
  mounts: Sequence[Mount] = ()
  outputs: Mount | None = None
  cpus: float | None = None
  cpu_shares: int | None = None
  memory_gb: float | None = None
  memory_swap_gb: float | None = None
  pids: int | None = None
  network: bool = False
  labels: Mapping[str, str] = field(default_factory=dict[str, str])
  security: Sequence[str] = ()
  cap_drop: Sequence[str] = ()
  user: str | None = None
  workdir: str | None = None
  env: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)
  pass_env: Sequence[str] = ()
  pre_start: Callable[[Docker], None] | None = None
  post_start: Callable[[Docker], None] | None = None
  cli: Callable[[Sequence[str]], CommandResult] = run_docker
  _id: str | None = field(default=None, init=False)
  _users: int = field(default=0, init=False)
  _pool: ResourcePool | None = field(default=None, init=False)
  _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
  _collected: dict[tuple[str, Path], set[str]] = field(
    default_factory=dict[tuple[str, Path], set[str]], init=False
  )
  _summaries: dict[str, Path] = field(
    default_factory=dict[str, Path], init=False
  )

  @property
  def container(self) -> str | None:
    """The container's ID once created.

    For a runner that drives an agent in it.
    """
    return self._id

  def adopt(self, container: str) -> None:
    """Take an existing container as this env's, so that start creates none.

    Such as the container of an attempt that a resume goes on with. It
    checks what `docker inspect` gives against what create_args would set:
    the image, each of labels (the container may have more, from its image),
    the network (none without network, any but the host's with it), and the
    user, the working directory, the bind mounts, the security options
    (exactly security), and each of cap_drop (the container may drop more),
    cpus, cpu_shares, memory_gb, memory_swap_gb and pids that this env sets.
    It also refuses a container isolated less than create_args leaves one:
    privileged, with a capability added, a device, a mount other than the
    env's bind mounts or volumes from another container, a PID or IPC
    namespace other than docker's default, or the host's UTS, user or cgroup
    namespace; where docker's default cgroup namespace is the host's, as
    under cgroup v1, no container is adopted. The rest of what the env leaves
    to docker's defaults is not checked, nor is env or pass_env, whose
    values inspect would show.

    Args:
      container: The container's ID or name.

    Raises:
      ValueError: When this env has a container already, inspect printed no
        JSON object, or the container differs from this env in a checked
        item, which the error names.
      RuntimeError: When docker inspect fails, such as for no such container.
    """  # noqa: DOC503
    with self._lock:
      if self._id is not None:
        raise ValueError(f"the env has a container already: {self._id}")
      args = ["inspect", "--type", "container", "--format", "{{json .}}"]
      # The log is stdout and then stderr, where docker may warn; the format
      # prints the container as JSON on one line.
      printed = self._docker([*args, container]).partition("\n")[0]
      found = cast("object", json.loads(printed))
      if not isinstance(found, dict):
        raise ValueError(f"docker inspect {container} printed no JSON object")
      found = cast(dict[str, object], found)
      differ = self._differences(
        _json_object(found.get("Config")), _json_object(found.get("HostConfig"))
      )
      if differ:
        raise ValueError(f"{container} differs in {', '.join(differ)}")
      found_id = found.get("Id")
      self._id = found_id if isinstance(found_id, str) else container

  def _differences(
    self, config: dict[str, object], host: dict[str, object]
  ) -> list[str]:
    labels = _json_object(config.get("Labels"))
    mounts = [*self.mounts, *([] if self.outputs is None else [self.outputs])]
    binds = [
      f"{m.source.resolve()}:{m.target}{':ro' if m.read_only else ''}"
      for m in mounts
    ]
    caps = _capabilities(_strings(host.get("CapDrop")))
    mode = host.get("NetworkMode")
    # Each item the env sets: whether the container has it.
    checks: dict[str, bool | None] = {
      "image": config.get("Image") == self.image,
      **{f"label {k}": labels.get(k) == v for k, v in self.labels.items()},
      "network": mode not in ("none", "host")
      if self.network
      else mode == "none",
      "mounts": _strings(host.get("Binds")) == sorted(binds),
      "user": _same(config.get("User"), self.user),
      "workdir": _same(config.get("WorkingDir"), self.workdir),
      "cap_drop": caps >= _capabilities(self.cap_drop),
      "security": set(_strings(host.get("SecurityOpt"))) == set(self.security),
      "cpus": _same(
        host.get("NanoCpus"),
        None if self.cpus is None else round(self.cpus * 1e9),
      ),
      "cpu_shares": _same(host.get("CpuShares"), self.cpu_shares),
      "memory_gb": _same(host.get("Memory"), _bytes(self.memory_gb)),
      "memory_swap_gb": _same(
        host.get("MemorySwap"), _bytes(self.memory_swap_gb)
      ),
      "pids": _same(host.get("PidsLimit"), self.pids),
      # What create_args never asks for, whatever the env.
      "privileged": host.get("Privileged") is not True,
      "cap_add": not host.get("CapAdd"),
      "devices": not host.get("Devices"),
      "other_mounts": not host.get("Mounts"),
      "volumes_from": not host.get("VolumesFrom"),
      "pid_mode": not _shared(host.get("PidMode")),
      "ipc_mode": not _shared(host.get("IpcMode")),
      "uts_mode": host.get("UTSMode") != "host",
      "userns_mode": host.get("UsernsMode") != "host",
      "cgroupns_mode": host.get("CgroupnsMode") != "host",
    }
    return [name for name, same in checks.items() if same is False]

  def create_args(self) -> list[str]:
    """Return the arguments of the `docker create` that makes the container.

    Returns:
      The arguments after `docker`.
    """
    return self._create_args(self.image)

  def _create_args(self, image: str) -> list[str]:
    return [
      "create",
      "--init",
      *self._limit_args(),
      *self._run_args(),
      image,
      "sleep",
      "infinity",
    ]

  def _limit_args(self) -> list[str]:
    args: list[str] = []
    if not self.network:
      args += ["--network", "none"]
    if self.cpus is not None:
      args += ["--cpus", str(self.cpus)]
    if self.cpu_shares is not None:
      args += ["--cpu-shares", str(self.cpu_shares)]
    if self.memory_gb is not None:
      args += ["--memory", f"{round(self.memory_gb * 1024)}m"]
    if self.memory_swap_gb is not None:
      args += ["--memory-swap", f"{round(self.memory_swap_gb * 1024)}m"]
    if self.pids is not None:
      args += ["--pids-limit", str(self.pids)]
    return args

  def _run_args(self) -> list[str]:
    args: list[str] = []
    for key, value in self.labels.items():
      args += ["--label", f"{key}={value}"]
    for option in self.security:
      args += ["--security-opt", option]
    for capability in self.cap_drop:
      args += ["--cap-drop", capability]
    if self.user is not None:
      args += ["--user", self.user]
    if self.workdir is not None:
      args += ["--workdir", self.workdir]
    for mount in [
      *self.mounts,
      *([] if self.outputs is None else [self.outputs]),
    ]:
      suffix = ":ro" if mount.read_only else ""
      args += ["--volume", f"{mount.source.resolve()}:{mount.target}{suffix}"]
    for key, value in self.env.items():
      args += ["--env", f"{key}={value}"]
    for name in self.pass_env:
      args += ["--env", name]
    return args

  def start(self, pool: ResourcePool | None) -> None:
    """Create the container if needed and start it, unless it runs already.

    Args:
      pool: The pool to reserve memory_gb from, if any.

    Raises:
      BaseException: Whatever creating, starting or post_start raised; the
        memory is given back, and after a failed post_start the container is
        stopped. Stopped, with the memory given back, when the stop of the
        call this thread runs is set before the container is created or
        started.
    """
    with self._lock:
      if self._users == 0:
        if pool is not None and self.memory_gb is not None:
          pool.acquire(self.memory_gb)
          self._pool = pool
        try:
          # Also once the memory came, which may be long after the call began.
          stopping.check()
          if self._id is None:
            self._id = self._docker(self.create_args()).strip()
          if self.pre_start is not None:
            self.pre_start(self)
          _ = self._docker(["start", self._id])
          if self.post_start is not None:
            try:
              self.post_start(self)
            except BaseException:
              _ = self.cli(["stop", self._id])
              raise
        except BaseException:
          self._release()
          raise
      self._users += 1

  def stop(self) -> None:
    """Stop the container once its last action ends."""
    with self._lock:
      self._users -= 1
      if self._users == 0 and self._id is not None:
        try:
          _ = self._docker(["stop", self._id])
        finally:
          self._release()

  def _release(self) -> None:
    if self._pool is not None and self.memory_gb is not None:
      self._pool.release(self.memory_gb)
    self._pool = None

  def remove(self) -> None:
    """Remove the container, if it was created."""
    with self._lock:
      if self._id is not None:
        _ = self._docker(["rm", "--force", self._id])
        self._id = None

  def replace(
    self,
    image: str,
    carry: Sequence[str] = (),
    pool: ResourcePool | None = None,
  ) -> None:
    """Go on in a container of another image, with the changes of carry.

    Called between actions, such as once the host has built image from a
    Dockerfile a session wrote. The new container has this env's options,
    mounts and labels, so only the image differs. Of each git work tree that
    carry names, by its top in the container, it gets the changes against
    `HEAD`: the files `HEAD` tracks that differ and the untracked files git
    does not ignore, as a binary diff the old container makes and the new one
    applies to `HEAD`, so that those files are as in the old tree, but no file
    git ignores, which the new image may have built anew. It carries them only
    onto a tree whose `HEAD` has the same tree as the old one's. When that
    differs or a change does not apply, such as a file the diff adds that git
    ignores in the new tree, it removes the new container, keeps the old one
    as this env's and raises, so no change is lost; else it removes the old
    one, and a removal that fails is logged and left to the dataset's
    cleanup, as the env has gone on in the new one. A git repository inside
    a tree, such as a submodule, whose files or commit differ from `HEAD`, or
    a submodule's directory that holds files but is no repository, makes it
    raise before it creates the new one, since a diff holds only that
    repository's commit. The two run one at a time, only for git and
    the copies, without pre_start and post_start, and with memory_gb reserved
    from pool. An env with no container yet only takes the image.

    Args:
      image: The new container's image.
      carry: The work trees whose changes go along, by their paths in the
        container.
      pool: The pool to reserve memory_gb from while a container runs, if any.

    Raises:
      RuntimeError: When an action runs in the env, a docker or git command
        fails, such as on a path of carry that is not the top of a work tree,
        a tree's `HEAD` differs between the two containers, or a repository
        inside a tree differs from `HEAD`.
    """  # noqa: DOC503
    with self._lock:
      if self._users:
        raise RuntimeError("replace runs between actions, not during one")
      old = self._id
      if old is None:
        self.image = image
        return
      with (
        _reserve(pool, self.memory_gb),
        tempfile.TemporaryDirectory(prefix="dataforge-carry-") as into,
      ):
        taken = self._take(old, carry, Path(into))
        new = None
        try:
          new = self._docker(self._create_args(image)).strip()
          self._give(new, taken)
        except BaseException:
          if new is not None:
            _ = self.cli(["rm", "--force", new])
          raise
      self._id, self.image = new, image
      removed = self.cli(["rm", "--force", old])
      if removed.exit_code != 0:
        _log.warning("replace left the old container %s: %s", old, removed.log)

  def _take(
    self, container: str, carry: Sequence[str], into: Path
  ) -> list[tuple[str, str, Path]]:
    # Each tree's path, its HEAD's tree and the diff, copied out into into.
    if not carry:
      return []
    _ = self._docker(["start", container])
    try:
      taken: list[tuple[str, str, Path]] = []
      _ = self._in(container, ["mkdir", "-p", _CARRY], root=True)
      _ = self._in(container, ["chmod", "0777", _CARRY], root=True)
      for number, tree in enumerate(carry):
        head = self._head(container, tree)
        patch = f"{_CARRY}/{number}.patch"
        # An index of its own, so that the tree's own is left as it is.
        index = {"GIT_INDEX_FILE": f"{_CARRY}/{number}.index"}
        for argv in (
          ["git", "read-tree", "HEAD"],
          ["git", "add", "--all"],
          ["git", "diff", *_DIFF, f"--output={patch}", "HEAD"],
        ):
          _ = self._in(container, argv, workdir=tree, env=index)
        self._no_repositories(container, tree, index)
        local = into / f"{number}.patch"
        _ = self._docker(["cp", f"{container}:{patch}", str(local)])
        if not stat.S_ISREG(local.lstat().st_mode):
          raise RuntimeError(f"the diff of {tree} is not a regular file")
        taken.append((tree, head, local))
      _ = self._in(container, ["rm", "-rf", _CARRY], root=True)
    except BaseException:
      _ = self.cli(["stop", container])
      raise
    _ = self._docker(["stop", container])
    return taken

  def _give(
    self, container: str, taken: Sequence[tuple[str, str, Path]]
  ) -> None:
    # Apply what _take took; the caller removes the container if this raises.
    if not taken:
      return
    _ = self._docker(["start", container])
    _ = self._in(container, ["mkdir", "-p", _CARRY], root=True)
    _ = self._in(container, ["chmod", "0777", _CARRY], root=True)
    for number, (tree, head, local) in enumerate(taken):
      found = self._head(container, tree)
      if found != head:
        why = f"{tree}: HEAD has the tree {head} in the old container"
        raise RuntimeError(f"{why}, {found} in the new one")
      # What the image's build left in the tree that git does not ignore goes,
      # so the old tree's diff makes the new one's as the session left it.
      for argv in (
        ["git", "reset", "--hard", "--quiet", "HEAD"],
        ["git", "clean", "-d", "--force", "--quiet"],
      ):
        _ = self._in(container, argv, workdir=tree)
      if local.stat().st_size:
        patch = f"{_CARRY}/{number}.patch"
        _ = self._docker(["cp", str(local), f"{container}:{patch}"])
        apply = ["git", "apply", "--whitespace=nowarn", patch]
        _ = self._in(container, apply, workdir=tree)
    _ = self._in(container, ["rm", "-rf", _CARRY], root=True)
    _ = self._docker(["stop", container])

  def _no_repositories(
    self, container: str, tree: str, index: Mapping[str, str]
  ) -> None:
    # A repository inside the tree, a submodule or one the session made, is
    # one line of the diff, its commit, and none of its files; so one that
    # differs from HEAD, its files or commit, is refused, and so is a
    # submodule's directory that holds files but is no repository of its
    # own, such as one never checked out, whose files git does not see.
    argv = [
      "git",
      "diff",
      "--raw",
      "--no-renames",
      "--ignore-submodules=none",
      "HEAD",
    ]
    for line in self._in(container, argv, workdir=tree, env=index).split("\n"):
      modes, _, path = line.partition("\t")
      if line.startswith(":") and _GITLINK in modes.lstrip(":").split()[:2]:
        self._refuse(tree, "differs from HEAD", path)
    argv = ["git", "ls-files", "--stage", "-z"]
    for entry in self._in(container, argv, workdir=tree, env=index).split("\0"):
      mode, _, path = entry.partition("\t")
      if not mode.startswith(_GITLINK):
        continue
      listed = self.cli(
        ["exec", "--workdir", tree, container, "ls", "-A", "--", path]
      )
      if listed.exit_code != 0 or not listed.log.strip():
        continue
      inside = str(PurePosixPath(tree) / path)
      prefix = self._in(
        container, ["git", "rev-parse", "--show-prefix"], workdir=inside
      )
      if prefix.partition("\n")[0]:
        self._refuse(tree, "holds files but is no repository", path)

  @staticmethod
  def _refuse(tree: str, why: str, path: str) -> NoReturn:
    whole = f"a git repository inside the tree {why}, which a diff cannot carry"
    raise RuntimeError(f"{tree}: {whole}: {path}")

  def _head(self, container: str, tree: str) -> str:
    # The tree of HEAD in the work tree whose top is tree.
    argv = ["git", "rev-parse", "--show-prefix", "HEAD^{tree}"]
    lines = self._in(container, argv, workdir=tree).split("\n")
    if len(lines) < 2 or lines[0] or not _OBJECT.fullmatch(lines[1]):
      raise RuntimeError(f"{tree} is not the top of a git work tree")
    return lines[1]

  def __enter__(self) -> Self:
    """Return the env; leaving the with block removes the container.

    Returns:
      This env.
    """
    return self

  def __exit__(
    self,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: TracebackType | None,
  ) -> None:
    """Remove the container.

    Args:
      exc_type: Unused.
      exc: Unused.
      tb: Unused.
    """
    self.remove()

  def stage(
    self,
    ctx: Context,
    index: int,
    inputs: Mapping[str, Path],
    validator: Path | None,
  ) -> Staged:
    """Copy the inputs and the validator into the container.

    Args:
      ctx: The visit's context.
      index: The action's number in the visit.
      inputs: The input paths on the host, by name.
      validator: The validator on the host, if any.

    Returns:
      The action's paths in the container.
    """
    base = f"/dataforge/{ctx.state}/{ctx.visit}/{index}"
    staged = {name: f"{base}/inputs/{name}" for name in inputs}
    # A path inside an artifact ("task/instruction.md") keeps its directory.
    dirs = {
      f"{base}/inputs",
      *(str(PurePosixPath(p).parent) for p in staged.values()),
    }
    summary = f"{base}/summary"
    if self.outputs is None:
      outdir = f"{base}/outputs"
      _ = self._exec_ok(["mkdir", "-p", *sorted(dirs), outdir, summary])
      _ = self._exec_ok(["chmod", "0777", outdir, summary])
    else:
      outdir = self.outputs.target
      _ = self._exec_ok(["mkdir", "-p", *sorted(dirs), summary])
      _ = self._exec_ok(["chmod", "0777", summary])
    summary = f"{summary}/{SUMMARY}"
    with self._lock:
      self._summaries[summary] = summary_path(ctx, index)
    for name, path in inputs.items():
      _ = self._docker(["cp", str(path), f"{self._id}:{staged[name]}"])
    copy = None
    if validator is not None:
      copy = f"/dataforge/validator/{validator.name}"
      _ = self._exec_ok(["mkdir", "-p", "/dataforge/validator"])
      _ = self._docker(["cp", str(validator), f"{self._id}:{copy}"])
      _ = self._exec_ok(["chmod", "0555", copy], root=False)
    return Staged(staged, outdir, copy, summary)

  def exec(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult:
    """Run a command in the container, as its user.

    Args:
      argv: The command.
      env: Environment variables to set for it.

    Returns:
      Its result.
    """
    variables = [
      arg for key, value in env.items() for arg in ("--env", f"{key}={value}")
    ]
    return self.cli(["exec", *variables, cast(str, self._id), *argv])

  def collect(self, staged: Staged, outdir: Path) -> str:
    """Copy the action's outputs out of the container, or the outputs mount.

    Also copy its summary out to its summary_path. What is copied keeps the
    symlinks that resolve inside the tree they were copied in, the summary's
    directory or the outputs, and loses the others.

    Args:
      staged: The action's paths in the container.
      outdir: The visit's output directory.

    Returns:
      A line for each symlink removed and each file left out, joined.
    """
    notes: list[str] = []
    if staged.summary is not None:
      with self._lock:
        summary = self._summaries[staged.summary]
      inside = str(PurePosixPath(staged.summary).parent)
      _ = self._docker(["cp", f"{self._id}:{inside}/.", str(summary.parent)])
      notes += drop_escaping_links(summary.parent, "summary")
    fresh = Path(tempfile.mkdtemp(prefix=f".{outdir.name}.", dir=outdir.parent))
    try:
      # The copy is checked only once it is somewhere the container cannot
      # reach, so that no link can change after it was checked.
      if self.outputs is None:
        _ = self._docker(["cp", f"{self._id}:{staged.outdir}/.", str(fresh)])
      else:
        notes += _copy_untrusted(self.outputs.source, fresh)
      notes += drop_escaping_links(fresh, "outputs")
      names = {path.name for path in fresh.iterdir()}
      with self._lock:
        key = (staged.outdir, outdir)
        for name in self._collected.get(key, set()) | names:
          _remove(outdir / name)
        for name in names:
          _ = (fresh / name).rename(outdir / name)
        self._collected[key] = names
    finally:
      shutil.rmtree(fresh)
    return "\n".join(notes)

  def _exec_ok(self, argv: Sequence[str], root: bool = True) -> str:
    """Run a command in the container and require exit 0.

    Args:
      argv: The command.
      root: Whether it runs as root; else as the container's user, who owns
        what docker cp copies in. Under cap_drop ALL root cannot chmod another
        user's file.

    Returns:
      What it printed.

    Raises:
      RuntimeError: When it exits with another status.
    """  # noqa: DOC502
    return self._in(cast(str, self._id), argv, root=root)

  def _in(
    self,
    container: str,
    argv: Sequence[str],
    *,
    root: bool = False,
    workdir: str | None = None,
    env: Mapping[str, str] | None = None,
  ) -> str:
    # Run a command in container, as _exec_ok does.
    options = ["--user", "0"] if root else []
    if workdir is not None:
      options += ["--workdir", workdir]
    for key, value in (env or {}).items():
      options += ["--env", f"{key}={value}"]
    result = self.cli(["exec", *options, container, *argv])
    if result.exit_code != 0:
      raise RuntimeError(
        f"{' '.join(argv)} failed in {container}: {result.log}"
      )
    return result.log

  def _docker(self, args: Sequence[str]) -> str:
    result = self.cli(args)
    if result.exit_code != 0:
      raise RuntimeError(f"docker {' '.join(args)} failed: {result.log}")
    return result.log


@dataclass(frozen=True)
class AgentSpec:
  """A headless coding agent.

  A judge is an agent with no tools and an output_schema.

  Attributes:
    prompt: What the agent is asked to do.
    workdir: Its working directory.
    inputs: Its inputs.
    outputs: The names of its outputs.
    validator: An executable that takes the output directory and then the
      input paths as its arguments and exits 0 when the outputs are
      acceptable. The agent gets a read-only copy in its env and is told to
      make it pass; after the outputs are copied out, the host runs the
      original on them, and while it fails the session is resumed with its
      output, at most max_fixes times.
    max_fixes: How many times a failing validator resumes the session.
    output_schema: The JSON Schema of the agent's answer, which is parsed as
      JSON into ActionResult.value.
    tools: The tools it has; None for the runner's default.
    name: The agent's name, which a runner may use, such as in the names of
      its files.
    optional_inputs: More inputs, each left out when it does not exist yet.
    fresh_outputs: Outputs, each also in outputs, that this run must write
      anew: after the outputs are copied out, each must exist and differ in
      its bytes from the artifact's last version, if it has one. While one
      does not, the session is resumed and told which, and these resumes
      count against max_fixes too.
    session: The key of the session it goes on with, or None for none: a
      runner that keeps sessions by key resumes, for each start with a key,
      the session of the last start with the same key.
    resume_prompt: What the agent is asked when its start resumes a session,
      in place of prompt; None to ask prompt then too.
  """

  prompt: str
  workdir: Path
  inputs: Sequence[Input] = ()
  outputs: Sequence[str] = ()
  validator: Path | None = None
  max_fixes: int = 2
  output_schema: Data | None = None
  tools: Sequence[str] | None = None
  name: str | None = None
  optional_inputs: Sequence[Input] = ()
  fresh_outputs: Sequence[str] = ()
  session: str | None = None
  resume_prompt: str | None = None


@dataclass(frozen=True)
class AgentTask:
  """What an AgentRunner starts: the prompt the engine wrote and the paths.

  Attributes:
    prompt: The prompt, the spec's with the paths added.
    inputs: The input paths in the env, by name.
    outdir: The output directory in the env.
    validator: The validator's path in the env, if any.
    resume_prompt: The prompt of a start that resumes a session, the spec's
      resume_prompt with the paths added, or None to use prompt.
  """

  prompt: str
  inputs: Mapping[str, str]
  outdir: str
  validator: str | None
  resume_prompt: str | None = None


# Why a run of an agent or a trial gave nothing that can be judged, when the
# cause lies outside the task: the model's side (an API error, a refusal, rate
# limits that outlasted the retries), our own timeout, the machine (Harbor,
# Docker, the environment, the agent's tool itself) or a spend cap of the
# session's own. dataforge.curation.faults reads a trial's exception as one.
MODEL = "model"
TIMEOUT = "timeout"
INFRA = "infra"
BUDGET = "budget"
FAULTS = (MODEL, TIMEOUT, INFRA, BUDGET)


@dataclass(frozen=True)
class AgentRun:
  """One run of an agent.

  Attributes:
    session: Whatever the runner needs to resume it.
    status: "ok" when it finished normally; not "ok" whenever it has a fault.
    cost_usd: What it cost, in US dollars, or None when the runner could not
      tell, as for a session killed before it reported its cost.
    trace: Its trace, if the runner keeps one.
    result: Its answer.
    ending: How it ended, in the runner's words, if the runner says.
    resumable: Whether the session can go on after it.
    trace_format: The trace's format, such as "claude-code-stream-json".
    resumed: Whether it went on with the session of an earlier run rather
      than start one, as a resume does and a start may (AgentSpec.session).
    fault: One of FAULTS when it ended for a cause outside the task, or None
      when it ended normally or the runner does not say.
    stop_reason: Why the model stopped its last message, such as "refusal",
      if the runner says.
    error: The error the run ended on, such as an API error's message, cut
      to a length the runner sets, if it ended on one.
  """

  session: object
  status: str
  cost_usd: float | None
  trace: Path | None
  result: str
  ending: str | None = None
  resumable: bool = True
  trace_format: str | None = None
  resumed: bool = False
  fault: str | None = None
  stop_reason: str | None = None
  error: str | None = None


class AgentRunner(Protocol):
  """Drives a headless coding agent in an env.

  The agent action also calls close(session) when it ends, if the runner has
  close. A runner that can go on after this process ends has two more
  methods: checkpoint(session) returns, as JSON data, what restore needs of a
  run's session, and the agent action records it as the action's session;
  restore(points), given the checkpoints of an attempt's agent actions on
  this runner in the order they ended, makes a fresh runner go on as the one
  that made them would. Machine.resume calls restore.
  """

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    """Start a session.

    Args:
      spec: The agent.
      task: What it is to do, with its paths in env.
      env: Where it runs, started.

    Returns:
      The run.
    """
    ...

  def resume(self, session: object, prompt: str) -> AgentRun:
    """Resume a session with another prompt.

    Args:
      session: The session of an earlier run.
      prompt: The prompt.

    Returns:
      The run.
    """
    ...


@dataclass(frozen=True)
class ScriptSpec:
  """A command, run as argv followed by the input paths.

  The output directory is in the environment variable DATAFORGE_OUTPUTS. Where
  the env gives a summary file (Host and Docker do), DATAFORGE_SUMMARY is its
  path: Markdown written there becomes the action's summary in the record, up
  to SUMMARY_LIMIT bytes. Exit 0 is a pass.

  Attributes:
    argv: The command.
    inputs: Its inputs.
    outputs: The names of its outputs.
    memory_gb: On the host, reserved from the pool while it runs; in a Docker
      env the env's reservation covers it.
    optional_inputs: More inputs, each left out when it does not exist yet.
    build: Whether it builds an image: it then runs only on the host and
      holds one of the pool's build slots while it runs, taken before its
      memory_gb (`ResourcePool.build`).
  """

  argv: Sequence[str]
  inputs: Sequence[Input] = ()
  outputs: Sequence[str] = ()
  memory_gb: float | None = None
  optional_inputs: Sequence[Input] = ()
  build: bool = False


VERDICT = "verdict.json"
"""The file a judge writes in its output directory."""


@dataclass(frozen=True)
class Judge:
  """A script on the host that judges a visit by writing verdict.json.

  It runs as a ScriptSpec does, with the environment variables
  DATAFORGE_ROOT (the attempt's root), DATAFORGE_STATE, DATAFORGE_VISIT,
  DATAFORGE_ROUND and DATAFORGE_RECORD (a file holding the attempt's record
  so far, as Attempt.to_json gives it) besides DATAFORGE_OUTPUTS. It must exit
  0 and write verdict.json there: {"outcome": str, "data": {...},
  "feedback": {str: str} | null}, data and feedback optional.

  Attributes:
    spec: The script; verdict.json is one of its outputs whether it names it
      or not.
    outcomes: The outcomes it may give; a verdict with another one is not
      valid. Empty for any.
  """

  spec: ScriptSpec
  outcomes: Sequence[str] = ()


@dataclass(frozen=True)
class Verdict:
  """What a judge decided.

  Attributes:
    outcome: The outcome's name.
    data: Data for the outcome.
    feedback: The variables of a feedback template, if any.
  """

  outcome: str
  data: Data
  feedback: Mapping[str, str] | None


class JudgeError(Exception):
  """A judge exited non-zero or wrote no valid verdict.json."""


@dataclass(frozen=True)
class Limits:
  """The limits of a trial.

  Attributes:
    memory_gb: Its memory.
    cpus: Its CPUs, if limited.
  """

  memory_gb: float
  cpus: float | None = None


type TrialAgent = str | Mapping[str, object]
"""Who a trial runs: "nop", "oracle", or an agent's configuration, as the
TrialRunner reads."""


@dataclass(frozen=True)
class TrialResult:
  """The result of a trial.

  Attributes:
    rewards: The verifier's rewards, by name.
    exception: What ended the trial early, if anything did.
    cost_usd: What it cost, in US dollars.
    seconds: How long it took.
    artifacts: Its files, by name.
    id: The trial's name, stable and readable, if the runner gives one.
    path: The trial's own directory inside the output directory, if the
      runner gives one.
    trace: The agent's trace inside the output directory, if there is one.
    trace_format: The trace's format, such as "claude-code-stream-json".
  """

  rewards: Mapping[str, float]
  exception: str | None
  cost_usd: float
  seconds: float
  artifacts: Mapping[str, Path]
  id: str | None = None
  path: Path | None = None
  trace: Path | None = None
  trace_format: str | None = None


class TrialRunner(Protocol):
  """Runs one trial: start an environment, run the agent, run the verifier."""

  def memory_gb(self, limits: Limits) -> float:
    """Return what a trial with these limits reserves from the pool.

    Args:
      limits: The trial's limits.

    Returns:
      limits.memory_gb and whatever else the runner starts beside the task's
      container.
    """
    ...

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    """Run one trial.

    Args:
      task_dir: The task's directory.
      agent: Who the trial runs.
      limits: Its limits.
      outdir: The directory it writes into.

    Returns:
      Its result.
    """
    ...


@dataclass(frozen=True)
class Actions:
  """The actions, bound to the runners and the pool they use.

  Attributes:
    agent_runner: What drives the agents, if any run.
    trial_runner: What runs the trials, if any run.
    pool: The pool the actions reserve memory from, if any.
  """

  agent_runner: AgentRunner | None = None
  trial_runner: TrialRunner | None = None
  pool: ResourcePool | None = None

  def agent(
    self, ctx: Context, spec: AgentSpec, env: Env = HOST
  ) -> ActionResult:
    """Run an agent, and resume it while its outputs or validator fail.

    Args:
      ctx: The visit's context.
      spec: The agent.
      env: Where it runs.

    Returns:
      Its result.

    Raises:
      RuntimeError: When there is no agent_runner.
    """
    runner = self.agent_runner
    if runner is None:
      raise RuntimeError("an agent action needs an agent_runner")
    index, started_at = ctx.claim(spec.outputs), datetime.now(UTC)
    inputs = ctx.resolve(spec.inputs, spec.optional_inputs)
    env.start(self.pool)
    try:
      # An agent writes no summary, so collect copies none.
      staged = dataclasses.replace(
        env.stage(ctx, index, inputs, spec.validator), summary=None
      )
      prompt = _agent_prompt(spec.prompt, spec, staged)
      resume_prompt = (
        None
        if spec.resume_prompt is None
        else _agent_prompt(spec.resume_prompt, spec, staged)
      )
      task = AgentTask(
        prompt, staged.inputs, staged.outdir, staged.validator, resume_prompt
      )
      run = runner.start(spec, task, env)
      runs, log, fixes = [run], [run.result], 0
      check: CommandResult | None = None
      stale: list[str] = []
      first = run
      try:
        while True:
          note = env.collect(staged, ctx.outdir)
          if note:
            log.append(note)
          stale = []
          if run.status != "ok":
            break
          fix, check, stale = _check_outputs(ctx, spec, inputs)
          if fix is None or fixes == spec.max_fixes:
            break
          fixes += 1
          run = runner.resume(run.session, fix)
          runs.append(run)
          log.append(run.result)
        # Only once every run of the action has returned.
        session = _checkpoint(runner, run.session)
      finally:
        close = getattr(runner, "close", None)
        if callable(close):
          _ = close(run.session)
    finally:
      env.stop()
    trace = _trace_inside(ctx.attempt.root, first)
    costs = [r.cost_usd for r in runs]
    cost = sum(c for c in costs if c is not None)
    cost_known = None not in costs
    failures = _failures(fixes, check, stale)
    log.extend(failures)
    passed = run.status == "ok" and not failures
    value: object = None
    if passed and spec.output_schema is not None:
      try:
        value = cast("object", json.loads(run.result))
      except json.JSONDecodeError as error:
        passed = False
        log.append(f"the answer is not JSON: {error}")
    result = _finish(
      ctx,
      "agent",
      started_at,
      spec.outputs,
      passed,
      "\n".join(log),
      value,
      cost,
      session=session,
      trace=trace,
      trace_format=None if trace is None else first.trace_format,
      cost_known=cost_known,
    )
    return dataclasses.replace(result, run=run, validation=check, fixes=fixes)

  def script(
    self, ctx: Context, spec: ScriptSpec, env: Env = HOST
  ) -> ActionResult:
    """Run a script.

    Args:
      ctx: The visit's context.
      spec: The script.
      env: Where it runs.

    Returns:
      Its result.

    Raises:
      ValueError: When the script builds an image (spec.build) and env is
        not the host.
    """  # noqa: DOC502
    started_at, done, summary = self._run(ctx, spec, env, {})
    passed = done.exit_code == 0
    return _finish(
      ctx, "script", started_at, spec.outputs, passed, done.log, summary=summary
    )

  def judge(
    self, ctx: Context, judge: Judge, inputs: Sequence[Input] = ()
  ) -> Verdict:
    """Run a judge on the host and read its verdict.

    Args:
      ctx: The visit's context.
      judge: The judge.
      inputs: More inputs, after those of its spec.

    Returns:
      The verdict.

    Raises:
      JudgeError: When it exits non-zero or writes no valid verdict.json; the
        action is recorded as failed first.
    """
    spec = dataclasses.replace(
      judge.spec,
      inputs=[*judge.spec.inputs, *inputs],
      outputs=list(dict.fromkeys([*judge.spec.outputs, VERDICT])),
    )
    record = ctx.attempt.root / "records" / ctx.state / f"{ctx.visit}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    _ = record.write_text(json.dumps(ctx.attempt.to_json()))
    started_at, done, summary = self._run(
      ctx,
      spec,
      HOST,
      {
        "DATAFORGE_ROOT": str(ctx.attempt.root),
        "DATAFORGE_STATE": ctx.state,
        "DATAFORGE_VISIT": str(ctx.visit),
        "DATAFORGE_ROUND": str(ctx.round),
        "DATAFORGE_RECORD": str(record),
      },
    )
    verdict, why = None, f"the judge exited {done.exit_code}"
    if done.exit_code == 0:
      try:
        verdict = _verdict(ctx.outdir / VERDICT)
      except JudgeError as error:
        why = str(error)
      if (
        verdict is not None
        and judge.outcomes
        and (verdict.outcome not in judge.outcomes)
      ):
        why = f"{VERDICT}: outcome {verdict.outcome!r} is not one it may give"
        verdict = None
    log = done.log if verdict is not None else f"{done.log}\n{why}"
    _ = _finish(
      ctx,
      "judge",
      started_at,
      spec.outputs,
      verdict is not None,
      log,
      summary=summary,
    )
    if verdict is None:
      raise JudgeError(f"{ctx.state} visit {ctx.visit}: {why}\n{done.log}")
    return verdict

  def _run(
    self,
    ctx: Context,
    spec: ScriptSpec,
    env: Env,
    variables: Mapping[str, str],
  ) -> tuple[datetime, CommandResult, str | None]:
    # Runs a script, collects its outputs and reads its summary; the caller
    # records it.
    if spec.build and not isinstance(env, Host):
      # A Docker env's container may hold the pool's memory already.
      raise ValueError("a script that builds an image runs on the host")
    index, started_at = ctx.claim(spec.outputs), datetime.now(UTC)
    inputs = ctx.resolve(spec.inputs, spec.optional_inputs)
    memory_gb = spec.memory_gb if isinstance(env, Host) else None
    with _reserve(self.pool, memory_gb, spec.build):
      env.start(self.pool)
      try:
        staged = env.stage(ctx, index, inputs, None)
        variables = {**variables, "DATAFORGE_OUTPUTS": staged.outdir}
        if staged.summary is not None:
          variables["DATAFORGE_SUMMARY"] = staged.summary
          summary_path(ctx, index).parent.mkdir(parents=True, exist_ok=True)
        done = env.exec([*spec.argv, *staged.inputs.values()], variables)
        note = env.collect(staged, ctx.outdir)
        if note:
          lines = filter(None, [done.log.rstrip("\n"), note])
          done = CommandResult(done.exit_code, "\n".join(lines))
      finally:
        env.stop()
    summary = None
    if staged.summary is not None:
      summary = _summary(summary_path(ctx, index))
    return started_at, done, summary

  def trial(
    self,
    ctx: Context,
    name: str,
    task_dir: Path,
    agent: TrialAgent,
    limits: Limits,
  ) -> TrialResult:
    """Run one trial.

    Args:
      ctx: The visit's context.
      name: The name of its output, a directory of this visit.
      task_dir: The task's directory.
      agent: Who the trial runs.
      limits: Its limits.

    Returns:
      Its result.

    Raises:
      RuntimeError: When there is no trial_runner.
    """
    runner = self.trial_runner
    if runner is None:
      raise RuntimeError("a trial needs a trial_runner")
    _, started_at = ctx.claim([name], trial=True), datetime.now(UTC)
    outdir = ctx.outdir / name
    outdir.mkdir()
    with _reserve(self.pool, runner.memory_gb(limits)):
      result = runner.run(task_dir, agent, limits, outdir)
    status = "ok" if result.exception is None else "failed"
    record = ActionRecord(
      "trial",
      status,
      {name: outdir},
      started_at,
      datetime.now(UTC),
      result.cost_usd,
      trial=_trial_record(name, outdir, result),
    )
    ctx.record(record)
    return result


def _trial_record(name: str, outdir: Path, result: TrialResult) -> TrialRecord:
  # The record names paths relative to the attempt's root, so only paths
  # inside the trial's output directory are kept.
  def inside(path: Path | None) -> Path | None:
    return path if path is not None and path.is_relative_to(outdir) else None

  trace = inside(result.trace)
  return TrialRecord(
    result.id or name,
    inside(result.path) or outdir,
    trace,
    None if trace is None else result.trace_format,
    dict(result.rewards),
    result.exception,
  )


def fan_out[T](calls: Sequence[Callable[[], T]]) -> list[T]:
  """Run every call at once, each in its own thread.

  How many actually run at a time is up to the pool their actions reserve from.
  The calls run under a stop (`dataforge.stopping`), the one of the call that
  runs this fan_out, if any, else one of their own. When this thread is
  interrupted while it waits for them, by the SystemExit of a SIGTERM or a
  KeyboardInterrupt say, it sets the stop, so that the calls stop the children
  they wait for and start no more, and raises the interrupt once they ended.

  Args:
    calls: The calls.

  Returns:
    Their results, in order.

  Raises:
    Exception: The error of the first call, in order, that failed, once the
      calls ended.
    BaseException: The interrupt, once the calls ended.
  """  # noqa: DOC503
  if not calls:
    return []
  stop = stopping.current() or stopping.Stop()
  with ThreadPoolExecutor(max_workers=len(calls)) as executor:
    futures: list[Future[T]] = []
    try:
      for call in calls:
        futures.append(executor.submit(stopping.under(stop, call)))
      _ = wait(futures)
    except BaseException:
      stop.set()
      executor.shutdown(cancel_futures=True)
      for future in futures:
        error = None if future.cancelled() else future.exception()
        # What a stopped call could not do on its way out, the interrupt
        # would hide.
        if error is not None and not isinstance(error, stopping.Stopped):
          _log.warning("a call of a stopped fan_out raised %r", error)
      raise
    return [future.result() for future in futures]


_MAX_LINKS = 40
"""The most symlinks resolving one symlink may pass, as Linux allows."""


def _copy_untrusted(source: Path, target: Path) -> list[str]:
  """Copy a tree that untrusted code may still be changing into target.

  Nothing is followed but the directories it opens one by one, each opened
  without following a symlink, so that no change made while it copies can
  lead it out of source; symlinks are copied as symlinks.

  Args:
    source: The tree; it is trusted itself, but not what is in it.
    target: An existing directory to copy it into.

  Returns:
    A line for each file left out: a FIFO, socket or device, or one that
    changed while it was copied.
  """
  notes: list[str] = []
  fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
  try:
    _copy_dir(fd, target, PurePosixPath(), notes)
  finally:
    os.close(fd)
  return notes


def _copy_dir(
  fd: int, target: Path, where: PurePosixPath, notes: list[str]
) -> None:
  for name in sorted(os.listdir(fd)):
    path, into = where / name, target / name
    try:
      mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
      if stat.S_ISLNK(mode):
        os.symlink(os.readlink(name, dir_fd=fd), into)
      elif stat.S_ISDIR(mode):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        child = os.open(name, flags, dir_fd=fd)
        try:
          into.mkdir()
          _copy_dir(child, into, path, notes)
          _copy_status(child, into)
        finally:
          os.close(child)
      elif not (stat.S_ISREG(mode) and _copy_file(fd, name, into)):
        notes.append(f"outputs: left out {path}: not a regular file")
    except OSError as error:
      if error.errno not in _CHANGED:
        raise
      notes.append(f"outputs: left out {path}: it changed while copied")


_CHANGED = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EINVAL}
"""The errors of a stat, open or readlink of a file that something else
removed or replaced since the directory was listed or the file was stat'd."""


def _copy_file(fd: int, name: str, into: Path) -> bool:
  # False, copying nothing, when name is no longer a regular file.
  flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
  with os.fdopen(os.open(name, flags, dir_fd=fd), "rb") as file:
    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
      return False
    with into.open("xb") as copy:
      shutil.copyfileobj(file, copy)
    _copy_status(file.fileno(), into)
  return True


def _copy_status(fd: int, into: Path) -> None:
  status = os.fstat(fd)
  os.utime(into, ns=(status.st_atime_ns, status.st_mtime_ns))
  into.chmod(stat.S_IMODE(status.st_mode))


def drop_escaping_links(root: Path, what: str) -> list[str]:
  """Remove each symlink under root that resolves, or may, outside root.

  root must be out of reach of whoever wrote it, so that nothing changes
  while it is checked, and owned by this process: each directory in it is
  first given its owner's rwx, so that none can hide a link from the check
  or the copy's cleanup. A link is kept only when resolving it, through any
  link it passes, never leaves root; one that passes a missing name is
  judged as if that name were a directory there, so that what is put there
  later, itself checked the same way, cannot lead out either.

  Args:
    root: The tree.
    what: What the tree is, for the notes.

  Returns:
    A line for each symlink removed.
  """
  _let_owner_in(root)
  links = [
    Path(top, name)
    for top, dirs, files in os.walk(root, onerror=_fail)
    for name in [*dirs, *files]
    if Path(top, name).is_symlink()
  ]
  escaping = {
    link: os.readlink(link) for link in links if not _confined(root, link)
  }
  for link in escaping:
    link.unlink()
  return [
    f"{what}: removed symlink {link.relative_to(root)} -> {to}: it leads out"
    for link, to in escaping.items()
  ]


def drop_special_files(root: Path, what: str) -> list[str]:
  """Remove each entry under root that is no file, directory or symlink.

  A FIFO, a socket or a device would hang or mislead a reader that opens it
  as a file.

  Args:
    root: The tree.
    what: What the tree is, for the notes.

  Returns:
    A line for each entry removed.
  """
  notes: list[str] = []
  for top, dirs, files in os.walk(root, onerror=_fail):
    for name in [*dirs, *files]:
      path = Path(top, name)
      mode = path.lstat().st_mode
      if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
        path.unlink()
        notes.append(
          f"{what}: removed {path.relative_to(root)}: not a regular file"
        )
  return notes


def _let_owner_in(path: Path) -> None:
  path.chmod(stat.S_IMODE(path.lstat().st_mode) | stat.S_IRWXU)
  with os.scandir(path) as entries:
    for entry in entries:
      if entry.is_dir(follow_symlinks=False):
        _let_owner_in(Path(entry.path))


def _fail(error: OSError) -> None:
  # A directory the walk cannot read could hide an entry from the check.
  raise error


def _confined(root: Path, link: Path) -> bool:
  hops = 0

  def resolve(at: Sequence[str], to: str) -> list[str] | None:
    # Gives where to leads from the directory at, as names under root, or
    # None when it leaves root on the way.
    nonlocal hops
    if to.startswith("/"):
      return None
    names = list(at)
    for name in to.split("/"):
      if name in {"", "."}:
        continue
      if name == "..":
        if not names:
          return None
        _ = names.pop()
        continue
      path = root.joinpath(*names, name)
      if not path.is_symlink():
        names.append(name)
        continue
      hops += 1
      if hops > _MAX_LINKS:
        return None
      resolved = resolve(names, os.readlink(path))
      if resolved is None:
        return None
      names = resolved
    return names

  at = link.parent.relative_to(root).parts
  return resolve(at, os.readlink(link)) is not None


def _checkpoint(runner: AgentRunner, session: object) -> Data | None:
  checkpoint = getattr(runner, "checkpoint", None)
  if not callable(checkpoint):
    return None
  return cast("Data | None", checkpoint(session))


def _trace_inside(root: Path, run: AgentRun) -> Path | None:
  # The record names paths relative to the attempt's root.
  trace = run.trace
  return trace if trace is not None and trace.is_relative_to(root) else None


def _shared(mode: object) -> bool:
  # The daemon's default, such as "", "private" or "shareable", is not.
  return mode == "host" or str(mode).startswith("container:")


def _same(found: object, wanted: object) -> bool | None:
  # None: the env leaves it to docker's default, which is not checked.
  return None if wanted is None else found == wanted


def _bytes(gb: float | None) -> int | None:
  return None if gb is None else _mb(gb) * 2**20


def _capabilities(names: Sequence[str]) -> set[str]:
  return {name.upper().removeprefix("CAP_") for name in names}


def _strings(value: object) -> list[str]:
  items = cast(list[object], value) if isinstance(value, list) else []
  return sorted(item for item in items if isinstance(item, str))


def _json_object(value: object) -> dict[str, object]:
  return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _remove(path: Path) -> None:
  if path.is_dir() and not path.is_symlink():
    shutil.rmtree(path)
  else:
    path.unlink(missing_ok=True)


def _finish(
  ctx: Context,
  kind: str,
  started_at: datetime,
  names: Sequence[str],
  passed: bool,
  log: str,
  value: object = None,
  cost_usd: float = 0.0,
  summary: str | None = None,
  *,
  session: Data | None = None,
  trace: Path | None = None,
  trace_format: str | None = None,
  cost_known: bool = True,
) -> ActionResult:
  outputs = {
    name: ctx.outdir / name for name in names if (ctx.outdir / name).exists()
  }
  missing = [name for name in names if name not in outputs]
  if missing:
    passed = False
    log += f"\nmissing outputs: {', '.join(missing)}"
  status = "ok" if passed else "failed"
  ctx.record(
    ActionRecord(
      kind,
      status,
      outputs,
      started_at,
      datetime.now(UTC),
      cost_usd,
      summary,
      session=session,
      trace=trace,
      trace_format=trace_format,
      cost_known=cost_known,
    )
  )
  return ActionResult(
    status, outputs, log, value, cost_usd, cost_known=cost_known
  )


def _summary(path: Path) -> str | None:
  # The file comes from the action's env, which may be a container running
  # untrusted code: open it without following a symlink, so that it cannot
  # bring a host file into the record, and read no more than the limit.
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  except FileNotFoundError:
    return None
  except OSError:
    return NOT_A_FILE
  with os.fdopen(fd, "rb") as file:
    status = os.fstat(file.fileno())
    if not stat.S_ISREG(status.st_mode):
      return NOT_A_FILE
    data = file.read(SUMMARY_LIMIT)
  if not data:
    return None
  if status.st_size <= SUMMARY_LIMIT:
    return data.decode(errors="replace")
  # A character cut in two at the limit is dropped, not replaced.
  text = codecs.getincrementaldecoder("utf-8")("replace").decode(data)
  return (
    f"{text}\n\n(cut off: the summary has {status.st_size} bytes, over the"
    f" {SUMMARY_LIMIT} kept)"
  )


def _verdict(path: Path) -> Verdict:
  try:
    raw = cast("object", json.loads(path.read_text()))
  except (OSError, json.JSONDecodeError) as error:
    raise JudgeError(f"no valid {VERDICT}: {error}") from error
  if not isinstance(raw, dict):
    raise JudgeError(f"{VERDICT} is not an object")
  verdict = cast(dict[str, object], raw)
  outcome = verdict.get("outcome")
  data = verdict.get("data", {})
  feedback = verdict.get("feedback")
  if not isinstance(outcome, str):
    raise JudgeError(f"{VERDICT} has no outcome string")
  if not isinstance(data, dict):
    raise JudgeError(f"{VERDICT}: data is not an object")
  if feedback is not None and not (
    isinstance(feedback, dict)
    and all(
      isinstance(value, str)
      for value in cast(dict[str, object], feedback).values()
    )
  ):
    raise JudgeError(f"{VERDICT}: feedback is not an object of strings")
  return Verdict(
    outcome,
    cast(dict[str, object], data),
    None if feedback is None else cast(dict[str, str], feedback),
  )


def _agent_prompt(text: str, spec: AgentSpec, staged: Staged) -> str:
  parts = [text]
  if staged.inputs:
    lines = "\n".join(
      f"- {name}: {path}" for name, path in staged.inputs.items()
    )
    parts.append(f"Your inputs, read-only:\n{lines}")
  if spec.outputs:
    names = ", ".join(spec.outputs)
    parts.append(f"Write your outputs into {staged.outdir}, named: {names}.")
  if spec.fresh_outputs:
    names = ", ".join(spec.fresh_outputs)
    parts.append(
      "Write these anew this time; an unchanged copy of the last version"
      + f" does not count: {names}."
    )
  if staged.validator is not None:
    command = shlex.join(
      [staged.validator, staged.outdir, *staged.inputs.values()]
    )
    parts.append(
      f"Before you finish, run `{command}`;"
      + " it must exit 0. It is read-only, and the same check runs again"
      + " after you finish."
    )
  return "\n\n".join(parts)


def _check_outputs(
  ctx: Context, spec: AgentSpec, inputs: Mapping[str, Path]
) -> tuple[str | None, CommandResult | None, list[str]]:
  """Check an agent's collected outputs.

  Args:
    ctx: The visit's context.
    spec: The agent.
    inputs: Its input paths on the host, by name.

  Returns:
    The prompt that asks for a fix, None when they pass; the validator's
    result, if it ran; and what of fresh_outputs is not written anew, in
    which case the validator does not run.
  """
  stale = _stale(ctx, spec.fresh_outputs)
  if stale:
    return _fresh_prompt(stale), None, stale
  if spec.validator is None:
    return None, None, []
  argv = [spec.validator, ctx.outdir, *inputs.values()]
  check = HOST.exec([str(arg) for arg in argv], {})
  fix = None if check.exit_code == 0 else _fix_prompt(check.log)
  return fix, check, []


def _failures(
  fixes: int, check: CommandResult | None, stale: Sequence[str]
) -> list[str]:
  failures: list[str] = []
  if stale:
    failures.append(
      f"outputs not written anew after {fixes} fixes: {'; '.join(stale)}"
    )
  if check is not None and check.exit_code != 0:
    failures.append(f"validator failed after {fixes} fixes:\n{check.log}")
  return failures


def _stale(ctx: Context, names: Sequence[str]) -> list[str]:
  stale: list[str] = []
  for name in names:
    path = ctx.outdir / name
    versions = ctx.artifacts.history(name)
    if not path.exists():
      stale.append(f"{name} is missing")
    elif versions and _same_bytes(path, versions[-1].path):
      stale.append(f"{name} is unchanged from its last version")
  return stale


def _same_bytes(a: Path, b: Path) -> bool:
  if a.is_dir() and b.is_dir():
    names = sorted(path.name for path in a.iterdir())
    if names != sorted(path.name for path in b.iterdir()):
      return False
    return all(_same_bytes(a / name, b / name) for name in names)
  if a.is_file() and b.is_file():
    return a.read_bytes() == b.read_bytes()
  return False


def _fresh_prompt(stale: Sequence[str]) -> str:
  lines = "\n".join(f"- {item}" for item in stale)
  return (
    "These outputs must be written anew in this run, and are not:\n\n"
    + f"{lines}\n\nWrite them."
  )


def _fix_prompt(output: str) -> str:
  return (
    "The validator failed after you finished. Its output:\n\n"
    + f"{output}\n\nFix the outputs."
  )
