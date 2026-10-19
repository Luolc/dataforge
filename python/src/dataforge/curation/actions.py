"""Actions a state runs: agent, script and trial, each in an environment, and fan_out.

What an action does (agent, script) and where it runs (Host, or a Docker container) are
independent. Memory is shared through one ResourcePool per process: a Docker env reserves
its memory_gb from start to stop, a trial what its runner's memory_gb gives, a host script
its memory_gb; each waits while the pool is short. CPUs are not reserved; a container's cpus
and cpu_shares divide them.
"""

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, cast

from dataforge.curation.engine import ActionRecord, Context, Data, Input


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    log: str


@dataclass(frozen=True)
class ActionResult:
    """The result of an agent or a script. status is "ok" or "failed".

    value is the parsed JSON answer of an agent run with an output_schema, such as a judge.
    """

    status: str
    outputs: Mapping[str, Path]
    log: str
    value: object = None
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ResourcePool:
    """The memory the actions of every attempt in a process share, in GB.

    A reservation larger than the pool is a ValueError; a smaller one waits until it fits
    and, with max_running, until fewer than that many reservations are held. It counts whole
    MiB, so that a fraction of a GB is given back exactly as it was taken.
    """

    def __init__(self, memory_gb: float, max_running: int | None = None) -> None:
        available = _mem_available_gb()
        if memory_gb <= 0 or (available is not None and memory_gb > available):
            raise ValueError(f"pool of {memory_gb} GB, but {available} GB is available")
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
        with self._cond:
            self._used_mb -= _mb(memory_gb)
            self._running -= 1
            self._cond.notify_all()

    @contextmanager
    def reserve(self, memory_gb: float) -> Generator[None]:
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


def _reserve(pool: ResourcePool | None, memory_gb: float | None) -> AbstractContextManager[None]:
    if pool is None or memory_gb is None:
        return nullcontext()
    return pool.reserve(memory_gb)


@dataclass(frozen=True)
class Staged:
    """An action's paths as the environment sees them."""

    inputs: Mapping[str, str]
    outdir: str
    validator: str | None


class Env(Protocol):
    """Where an action runs. start and stop bracket each action that uses it."""

    def start(self, pool: ResourcePool | None) -> None: ...

    def stop(self) -> None: ...

    def stage(
        self, ctx: Context, index: int, inputs: Mapping[str, Path], validator: Path | None
    ) -> Staged: ...

    def exec(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult: ...

    def collect(self, staged: Staged, outdir: Path) -> None: ...


@dataclass(frozen=True)
class Host:
    """The machine the engine runs on. Paths are host paths; outputs are written in place."""

    def start(self, pool: ResourcePool | None) -> None:
        _ = pool

    def stop(self) -> None:
        pass

    def stage(
        self, ctx: Context, index: int, inputs: Mapping[str, Path], validator: Path | None
    ) -> Staged:
        copy = None
        if validator is not None:
            copy = ctx.attempt.root / "validators" / ctx.state / str(ctx.visit) / str(index)
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
        done = subprocess.run(
            list(argv), capture_output=True, text=True, check=False, env={**os.environ, **env}
        )
        return CommandResult(done.returncode, done.stdout + done.stderr)

    def collect(self, staged: Staged, outdir: Path) -> None:
        _ = staged, outdir


HOST = Host()


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    read_only: bool = True


def run_docker(args: Sequence[str]) -> CommandResult:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    return CommandResult(done.returncode, done.stdout + done.stderr)


@dataclass(eq=False)
class Docker:
    """A container that `docker create` makes on first use and `docker start` / `docker stop`
    bracket each action in, so it is kept across visits; leaving its `with` removes it.

    Inputs are copied in under /dataforge/<state>/<visit>/<action>/inputs, outputs copied
    out from .../outputs, and a validator copied to /dataforge/validator/<name>, read-only.
    These directories are made as root and outputs is left writable by all, so that any
    user the container runs as can write there.
    With outputs, a writable mount, every action writes its outputs straight into that
    mount instead, so what one visit wrote is still there in the next, as a session resumed
    across visits needs; collect copies the mount's source on the host into the visit's
    outputs, so that each visit keeps its own version. The container's user must be able to
    write the source.
    The image must have `sleep`. Hooks for what a dataset adds, such as a gateway: mounts,
    env (variables set at create), pre_start, run on the host before each docker start, and
    post_start, run on the host after it and before the action, such as to start a process
    in the container with exec.
    memory_swap_gb is memory plus swap, as docker's --memory-swap counts it, and needs
    memory_gb: equal to it, the container gets no swap. cli runs one docker command; tests
    replace it.
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

    @property
    def container(self) -> str | None:
        """The container's ID once created, for a runner that drives an agent in it."""
        return self._id

    def create_args(self) -> list[str]:
        args = ["create", "--init"]
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
        for mount in [*self.mounts, *([] if self.outputs is None else [self.outputs])]:
            suffix = ":ro" if mount.read_only else ""
            args += ["--volume", f"{mount.source.resolve()}:{mount.target}{suffix}"]
        for key, value in self.env.items():
            args += ["--env", f"{key}={value}"]
        return [*args, self.image, "sleep", "infinity"]

    def start(self, pool: ResourcePool | None) -> None:
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
        with self._lock:
            if self._id is not None:
                _ = self._docker(["rm", "--force", self._id])
                self._id = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.remove()

    def stage(
        self, ctx: Context, index: int, inputs: Mapping[str, Path], validator: Path | None
    ) -> Staged:
        base = f"/dataforge/{ctx.state}/{ctx.visit}/{index}"
        if self.outputs is None:
            outdir = f"{base}/outputs"
            _ = self._exec_ok(["mkdir", "-p", f"{base}/inputs", outdir])
            _ = self._exec_ok(["chmod", "0777", outdir])
        else:
            outdir = self.outputs.target
            _ = self._exec_ok(["mkdir", "-p", f"{base}/inputs"])
        staged = {name: f"{base}/inputs/{name}" for name in inputs}
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
        variables = [arg for key, value in env.items() for arg in ("--env", f"{key}={value}")]
        return self.cli(["exec", *variables, cast(str, self._id), *argv])

    def collect(self, staged: Staged, outdir: Path) -> None:
        if self.outputs is None:
            _ = self._docker(["cp", f"{self._id}:{staged.outdir}/.", str(outdir)])
        else:
            _ = shutil.copytree(self.outputs.source, outdir, symlinks=True, dirs_exist_ok=True)

    def _exec_ok(self, argv: Sequence[str], root: bool = True) -> str:
        """Runs argv as root, or with root=False as the container's user, who owns what
        docker cp copies in; under cap_drop ALL root cannot chmod another user's file."""
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

    validator is an executable that takes the output directory as its one argument and
    exits 0 when the outputs are acceptable. The agent gets a read-only copy in its env and
    is told to make it pass; after the outputs are copied out, the host runs the original
    on them, and while it fails the session is resumed with its output, at most max_fixes
    times. A judge is an agent with no tools and an output_schema; its answer is parsed as
    JSON into ActionResult.value.
    """

    prompt: str
    workdir: Path
    inputs: Sequence[Input] = ()
    outputs: Sequence[str] = ()
    validator: Path | None = None
    max_fixes: int = 2
    output_schema: Data | None = None
    tools: Sequence[str] | None = None


@dataclass(frozen=True)
class AgentTask:
    """What an AgentRunner starts: the prompt the engine wrote and the env's paths."""

    prompt: str
    inputs: Mapping[str, str]
    outdir: str
    validator: str | None


@dataclass(frozen=True)
class AgentRun:
    """One run of an agent. status "ok" means it finished normally; result is its answer.

    session is whatever the runner needs to resume it.
    """

    session: object
    status: str
    cost_usd: float
    trace: Path | None
    result: str


class AgentRunner(Protocol):
    """Drives a headless coding agent in an env.

    The agent action also calls close(session) when it ends, if the runner has close.
    """

    def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun: ...

    def resume(self, session: object, prompt: str) -> AgentRun: ...


@dataclass(frozen=True)
class ScriptSpec:
    """A command, run as argv followed by the input paths, with the output directory in the
    environment variable DATAFORGE_OUTPUTS. Exit 0 is a pass. On the host, memory_gb is
    reserved from the pool while it runs; in a Docker env the env's reservation covers it."""

    argv: Sequence[str]
    inputs: Sequence[Input] = ()
    outputs: Sequence[str] = ()
    memory_gb: float | None = None


@dataclass(frozen=True)
class Limits:
    memory_gb: float
    cpus: float | None = None


type TrialAgent = str | Mapping[str, object]
"""Who a trial runs: "nop", "oracle", or an agent's configuration, as the TrialRunner reads."""


@dataclass(frozen=True)
class TrialResult:
    rewards: Mapping[str, float]
    exception: str | None
    cost_usd: float
    seconds: float
    artifacts: Mapping[str, Path]


class TrialRunner(Protocol):
    """Runs one trial (start an environment, run the agent, run the verifier) into outdir.

    memory_gb gives what a trial with these limits reserves from the pool: limits.memory_gb
    and whatever else the runner starts beside the task's container.
    """

    def memory_gb(self, limits: Limits) -> float: ...

    def run(
        self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
    ) -> TrialResult: ...


@dataclass(frozen=True)
class Actions:
    """The actions, bound to the runners and the pool they use."""

    agent_runner: AgentRunner | None = None
    trial_runner: TrialRunner | None = None
    pool: ResourcePool | None = None

    def agent(self, ctx: Context, spec: AgentSpec, env: Env = HOST) -> ActionResult:
        runner = self.agent_runner
        if runner is None:
            raise RuntimeError("an agent action needs an agent_runner")
        index, started_at = ctx.claim(spec.outputs), datetime.now(UTC)
        inputs = ctx.resolve(spec.inputs)
        env.start(self.pool)
        try:
            staged = env.stage(ctx, index, inputs, spec.validator)
            prompt = _agent_prompt(spec, staged)
            run = runner.start(
                spec, AgentTask(prompt, staged.inputs, staged.outdir, staged.validator), env
            )
            cost, log, fixes, check = run.cost_usd, [run.result], 0, None
            try:
                while True:
                    env.collect(staged, ctx.outdir)
                    if run.status != "ok" or spec.validator is None:
                        break
                    check = HOST.exec([str(spec.validator), str(ctx.outdir)], {})
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
        return _finish(ctx, "agent", started_at, spec.outputs, passed, "\n".join(log), value, cost)

    def script(self, ctx: Context, spec: ScriptSpec, env: Env = HOST) -> ActionResult:
        index, started_at = ctx.claim(spec.outputs), datetime.now(UTC)
        inputs = ctx.resolve(spec.inputs)
        memory_gb = spec.memory_gb if isinstance(env, Host) else None
        with _reserve(self.pool, memory_gb):
            env.start(self.pool)
            try:
                staged = env.stage(ctx, index, inputs, None)
                done = env.exec(
                    [*spec.argv, *staged.inputs.values()], {"DATAFORGE_OUTPUTS": staged.outdir}
                )
                env.collect(staged, ctx.outdir)
            finally:
                env.stop()
        return _finish(ctx, "script", started_at, spec.outputs, done.exit_code == 0, done.log)

    def trial(
        self, ctx: Context, name: str, task_dir: Path, agent: TrialAgent, limits: Limits
    ) -> TrialResult:
        """Run one trial into the output named name, a directory of this visit."""
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
            "trial", status, {name: outdir}, started_at, datetime.now(UTC), result.cost_usd
        )
        ctx.record(record)
        return result


def fan_out[T](calls: Sequence[Callable[[], T]]) -> list[T]:
    """Run every call at once, each in its own thread, and return the results in order.

    How many actually run at a time is up to the pool their actions reserve from.
    """
    if not calls:
        return []
    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(call) for call in calls]
        return [future.result() for future in futures]


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
    outputs = {name: ctx.outdir / name for name in names if (ctx.outdir / name).exists()}
    missing = [name for name in names if name not in outputs]
    if missing:
        passed = False
        log += f"\nmissing outputs: {', '.join(missing)}"
    status = "ok" if passed else "failed"
    ctx.record(ActionRecord(kind, status, outputs, started_at, datetime.now(UTC), cost_usd))
    return ActionResult(status, outputs, log, value, cost_usd)


def _agent_prompt(spec: AgentSpec, staged: Staged) -> str:
    parts = [spec.prompt]
    if staged.inputs:
        lines = "\n".join(f"- {name}: {path}" for name, path in staged.inputs.items())
        parts.append(f"Your inputs, read-only:\n{lines}")
    if spec.outputs:
        names = ", ".join(spec.outputs)
        parts.append(f"Write your outputs into {staged.outdir}, named: {names}.")
    if staged.validator is not None:
        parts.append(
            f"Before you finish, run `{staged.validator} {staged.outdir}`; it must exit 0."
            + " It is read-only, and the same check runs again after you finish."
        )
    return "\n\n".join(parts)


def _fix_prompt(output: str) -> str:
    return f"The validator failed after you finished. Its output:\n\n{output}\n\nFix the outputs."
