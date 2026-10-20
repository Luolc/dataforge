"""Actions: agent, script, judge and trial, each in an env; fan_out.

What an action does (agent, script) and where it runs (Host, or a Docker
container) are independent. Memory is shared through one ResourcePool per
process: a Docker env reserves its memory_gb from start to stop, a trial what
its runner's memory_gb gives, a host script its memory_gb; each waits while the
pool is short. CPUs are not reserved; a container's cpus and cpu_shares divide
them.
"""

from collections.abc import Callable, Generator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager, nullcontext
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import tempfile
import threading
from types import TracebackType
from typing import cast, Protocol, Self

from dataforge.curation.engine import ActionRecord, Context, Data, Input


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
    cost_usd: What it cost, in US dollars.
    run: An agent's last run.
    validation: The last run of an agent's validator on the host, if it ran.
    fixes: How many times the validator resumed an agent's session.
  """

  status: str
  outputs: Mapping[str, Path]
  log: str
  value: object = None
  cost_usd: float = 0.0
  run: AgentRun | None = None
  validation: CommandResult | None = None
  fixes: int = 0

  @property
  def ok(self) -> bool:
    """Whether the status is "ok"."""
    return self.status == "ok"


class ResourcePool:
  """The memory the actions of every attempt in a process share, in GB.

  A reservation larger than the pool is a ValueError; a smaller one waits until
  it fits and, with max_running, until fewer than that many reservations are
  held. It counts whole MiB, so that a fraction of a GB is given back exactly as
  it was taken.
  """

  def __init__(self, memory_gb: float, max_running: int | None = None) -> None:
    """Make a pool.

    Args:
      memory_gb: The pool's memory, at most what the machine has available.
      max_running: How many reservations may be held at once; None for any.

    Raises:
      ValueError: When memory_gb is not positive or is more than is available.
    """
    available = _mem_available_gb()
    if memory_gb <= 0 or (available is not None and memory_gb > available):
      raise ValueError(
        f"pool of {memory_gb} GB, but {available} GB is available"
      )
    self.memory_gb: float = memory_gb
    self.max_running: int | None = max_running
    self._total_mb: int = _mb(memory_gb)
    self._used_mb: int = 0
    self._running: int = 0
    self._cond: threading.Condition = threading.Condition()

  @property
  def reserved_gb(self) -> float:
    """The memory reserved now."""
    with self._cond:
      return self._used_mb / 1024

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
    with self._cond:
      _ = self._cond.wait_for(
        lambda: (
          self._used_mb + mb <= self._total_mb
          and (self.max_running is None or self._running < self.max_running)
        )
      )
      self._used_mb += mb
      self._running += 1

  def release(self, memory_gb: float) -> None:
    """Give back a reservation.

    Args:
      memory_gb: The memory it reserved.
    """
    with self._cond:
      self._used_mb -= _mb(memory_gb)
      self._running -= 1
      self._cond.notify_all()

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
  pool: ResourcePool | None, memory_gb: float | None
) -> AbstractContextManager[None]:
  if pool is None or memory_gb is None:
    return nullcontext()
  return pool.reserve(memory_gb)


@dataclass(frozen=True)
class Staged:
  """An action's paths as the environment sees them.

  Attributes:
    inputs: The input paths, by name.
    outdir: Where the action writes its outputs.
    validator: The validator's path, if the action has one.
  """

  inputs: Mapping[str, str]
  outdir: str
  validator: str | None


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

  def collect(self, staged: Staged, outdir: Path) -> None:
    """Copy an action's outputs into the visit's outputs on the host.

    Args:
      staged: The action's paths in the env.
      outdir: The visit's output directory.
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
    )

  def exec(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult:
    """Run a command on the host.

    Args:
      argv: The command.
      env: Environment variables to set on top of this process's.

    Returns:
      Its result.
    """
    done = subprocess.run(
      list(argv),
      capture_output=True,
      text=True,
      check=False,
      env={**os.environ, **env},
    )
    return CommandResult(done.returncode, done.stdout + done.stderr)

  def collect(self, staged: Staged, outdir: Path) -> None:
    """Do nothing: the outputs are already in place.

    Args:
      staged: Unused.
      outdir: Unused.
    """
    _ = staged, outdir


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


def run_docker(args: Sequence[str]) -> CommandResult:
  """Run one docker command.

  Args:
    args: The arguments after `docker`.

  Returns:
    Its result.
  """
  done = subprocess.run(
    ["docker", *args], capture_output=True, text=True, check=False
  )
  return CommandResult(done.returncode, done.stdout + done.stderr)


@dataclass(eq=False)
class Docker:
  """A container, kept across visits, that each action starts and stops.

  `docker create` makes it on first use, `docker start` and `docker stop`
  bracket each action in it, and leaving its `with` removes it.

  Inputs are copied in under /dataforge/<state>/<visit>/<action>/inputs, outputs
  copied out from .../outputs, and a validator copied to
  /dataforge/validator/<name>, read-only. These directories are made as root and
  outputs is left writable by all, so that any user the container runs as can
  write there.

  Each collect leaves the visit's outputs as the action's output directory is
  then: what it copies out replaces what has the same name, and what an earlier
  collect of the same action copied out and is gone now, such as a file a fix
  deleted, is removed. Nothing else in the visit's outputs is touched. The image
  must have `sleep`. mounts, env, pre_start and post_start are the hooks for
  what a dataset adds, such as a gateway.

  Attributes:
    image: The image.
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
    env: Variables set at create.
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
  env: Mapping[str, str] = field(default_factory=dict[str, str])
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

  @property
  def container(self) -> str | None:
    """The container's ID once created.

    For a runner that drives an agent in it.
    """
    return self._id

  def create_args(self) -> list[str]:
    """Return the arguments of the `docker create` that makes the container.

    Returns:
      The arguments after `docker`.
    """
    return [
      "create",
      "--init",
      *self._limit_args(),
      *self._run_args(),
      self.image,
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
    return args

  def start(self, pool: ResourcePool | None) -> None:
    """Create the container if needed and start it, unless it runs already.

    Args:
      pool: The pool to reserve memory_gb from, if any.

    Raises:
      BaseException: Whatever creating, starting or post_start raised; the
        memory is given back, and after a failed post_start the container is
        stopped.
    """
    with self._lock:
      if self._users == 0:
        if pool is not None and self.memory_gb is not None:
          pool.acquire(self.memory_gb)
          self._pool = pool
        try:
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
    if self.outputs is None:
      outdir = f"{base}/outputs"
      _ = self._exec_ok(["mkdir", "-p", *sorted(dirs), outdir])
      _ = self._exec_ok(["chmod", "0777", outdir])
    else:
      outdir = self.outputs.target
      _ = self._exec_ok(["mkdir", "-p", *sorted(dirs)])
    for name, path in inputs.items():
      _ = self._docker(["cp", str(path), f"{self._id}:{staged[name]}"])
    copy = None
    if validator is not None:
      copy = f"/dataforge/validator/{validator.name}"
      _ = self._exec_ok(["mkdir", "-p", "/dataforge/validator"])
      _ = self._docker(["cp", str(validator), f"{self._id}:{copy}"])
      _ = self._exec_ok(["chmod", "0555", copy], root=False)
    return Staged(staged, outdir, copy)

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

  def collect(self, staged: Staged, outdir: Path) -> None:
    """Copy the action's outputs out of the container, or the outputs mount.

    Args:
      staged: The action's paths in the container.
      outdir: The visit's output directory.
    """
    fresh = Path(tempfile.mkdtemp(prefix=f".{outdir.name}.", dir=outdir.parent))
    try:
      if self.outputs is None:
        _ = self._docker(["cp", f"{self._id}:{staged.outdir}/.", str(fresh)])
      else:
        _ = shutil.copytree(
          self.outputs.source, fresh, symlinks=True, dirs_exist_ok=True
        )
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
    """
    user = ["--user", "0"] if root else []
    result = self.cli(["exec", *user, cast(str, self._id), *argv])
    if result.exit_code != 0:
      raise RuntimeError(f"{' '.join(argv)} failed in {self._id}: {result.log}")
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


@dataclass(frozen=True)
class AgentTask:
  """What an AgentRunner starts: the prompt the engine wrote and the paths.

  Attributes:
    prompt: The prompt, the spec's with the paths added.
    inputs: The input paths in the env, by name.
    outdir: The output directory in the env.
    validator: The validator's path in the env, if any.
  """

  prompt: str
  inputs: Mapping[str, str]
  outdir: str
  validator: str | None


@dataclass(frozen=True)
class AgentRun:
  """One run of an agent.

  Attributes:
    session: Whatever the runner needs to resume it.
    status: "ok" when it finished normally.
    cost_usd: What it cost, in US dollars.
    trace: Its trace, if the runner keeps one.
    result: Its answer.
    ending: How it ended, in the runner's words, if the runner says.
    resumable: Whether the session can go on after it.
  """

  session: object
  status: str
  cost_usd: float
  trace: Path | None
  result: str
  ending: str | None = None
  resumable: bool = True


class AgentRunner(Protocol):
  """Drives a headless coding agent in an env.

  The agent action also calls close(session) when it ends, if the runner has
  close.
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

  The output directory is in the environment variable DATAFORGE_OUTPUTS. Exit 0
  is a pass.

  Attributes:
    argv: The command.
    inputs: Its inputs.
    outputs: The names of its outputs.
    memory_gb: On the host, reserved from the pool while it runs; in a Docker
      env the env's reservation covers it.
    optional_inputs: More inputs, each left out when it does not exist yet.
  """

  argv: Sequence[str]
  inputs: Sequence[Input] = ()
  outputs: Sequence[str] = ()
  memory_gb: float | None = None
  optional_inputs: Sequence[Input] = ()


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
  """

  rewards: Mapping[str, float]
  exception: str | None
  cost_usd: float
  seconds: float
  artifacts: Mapping[str, Path]


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
    """Run an agent, and resume it while its validator fails.

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
      staged = env.stage(ctx, index, inputs, spec.validator)
      prompt = _agent_prompt(spec, staged)
      run = runner.start(
        spec,
        AgentTask(prompt, staged.inputs, staged.outdir, staged.validator),
        env,
      )
      cost, log, fixes, check = run.cost_usd, [run.result], 0, None
      try:
        while True:
          env.collect(staged, ctx.outdir)
          if run.status != "ok" or spec.validator is None:
            break
          argv = [spec.validator, ctx.outdir, *inputs.values()]
          check = HOST.exec([str(arg) for arg in argv], {})
          if check.exit_code == 0 or fixes == spec.max_fixes:
            break
          fixes += 1
          run = runner.resume(run.session, _fix_prompt(check.log))
          cost += run.cost_usd
          log.append(run.result)
      finally:
        close = getattr(runner, "close", None)
        if callable(close):
          _ = close(run.session)
    finally:
      env.stop()
    passed = run.status == "ok" and (check is None or check.exit_code == 0)
    if check is not None and check.exit_code != 0:
      log.append(f"validator failed after {fixes} fixes:\n{check.log}")
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
    """
    started_at, done = self._run(ctx, spec, env, {})
    passed = done.exit_code == 0
    return _finish(ctx, "script", started_at, spec.outputs, passed, done.log)

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
    started_at, done = self._run(
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
      ctx, "judge", started_at, spec.outputs, verdict is not None, log
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
  ) -> tuple[datetime, CommandResult]:
    # Runs a script and collects its outputs; the caller records it.
    index, started_at = ctx.claim(spec.outputs), datetime.now(UTC)
    inputs = ctx.resolve(spec.inputs, spec.optional_inputs)
    memory_gb = spec.memory_gb if isinstance(env, Host) else None
    with _reserve(self.pool, memory_gb):
      env.start(self.pool)
      try:
        staged = env.stage(ctx, index, inputs, None)
        done = env.exec(
          [*spec.argv, *staged.inputs.values()],
          {**variables, "DATAFORGE_OUTPUTS": staged.outdir},
        )
        env.collect(staged, ctx.outdir)
      finally:
        env.stop()
    return started_at, done

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
    _, started_at = ctx.claim([name]), datetime.now(UTC)
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
    )
    ctx.record(record)
    return result


def fan_out[T](calls: Sequence[Callable[[], T]]) -> list[T]:
  """Run every call at once, each in its own thread.

  How many actually run at a time is up to the pool their actions reserve from.

  Args:
    calls: The calls.

  Returns:
    Their results, in order.
  """
  if not calls:
    return []
  with ThreadPoolExecutor(max_workers=len(calls)) as executor:
    futures = [executor.submit(call) for call in calls]
    return [future.result() for future in futures]


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
    ActionRecord(kind, status, outputs, started_at, datetime.now(UTC), cost_usd)
  )
  return ActionResult(status, outputs, log, value, cost_usd)


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


def _agent_prompt(spec: AgentSpec, staged: Staged) -> str:
  parts = [spec.prompt]
  if staged.inputs:
    lines = "\n".join(
      f"- {name}: {path}" for name, path in staged.inputs.items()
    )
    parts.append(f"Your inputs, read-only:\n{lines}")
  if spec.outputs:
    names = ", ".join(spec.outputs)
    parts.append(f"Write your outputs into {staged.outdir}, named: {names}.")
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


def _fix_prompt(output: str) -> str:
  return (
    "The validator failed after you finished. Its output:\n\n"
    + f"{output}\n\nFix the outputs."
  )
