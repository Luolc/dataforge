# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

import shutil
import threading
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from dataforge.statemachine import (
    Actions,
    AgentRun,
    AgentSpec,
    AgentTask,
    CommandResult,
    Context,
    Docker,
    End,
    Env,
    Limits,
    Mount,
    Outcome,
    ResourcePool,
    ScriptSpec,
    State,
    TrialAgent,
    TrialResult,
    fan_out,
    run_attempt,
)

WAIT = 5.0


def once(tmp_path: Path, body: Callable[[Context], Outcome]) -> None:
    """Run body as the one state of an attempt that must end "ok"."""
    attempt = run_attempt([State("a", body, {"ok": End("ok")}, 1)], tmp_path / "attempt")
    assert attempt.status == "ok"


def blocked(call: Callable[[], object], release: Callable[[], None]) -> None:
    """Assert that call waits until release is called, then finishes."""
    done = threading.Event()
    thread = threading.Thread(target=lambda: (call(), done.set()))
    thread.start()
    assert not done.wait(0.2)
    release()
    assert done.wait(WAIT)
    thread.join(WAIT)


def test_script_gets_inputs_and_writes_outputs(tmp_path: Path) -> None:
    source = tmp_path / "in.txt"
    _ = source.write_text("hello")

    def body(ctx: Context) -> Outcome:
        argv = ["sh", "-c", 'cp "$0" "$DATAFORGE_OUTPUTS/out.txt"']
        result = Actions().script(ctx, ScriptSpec(argv, inputs=[source], outputs=["out.txt"]))
        assert result.outputs["out.txt"].read_text() == "hello"
        return Outcome(result.status)

    once(tmp_path, body)


def test_a_missing_output_fails_the_action(tmp_path: Path) -> None:
    def body(ctx: Context) -> Outcome:
        result = Actions().script(ctx, ScriptSpec(["true"], outputs=["out.txt"]))
        assert result.status == "failed"
        assert "missing outputs: out.txt" in result.log
        return Outcome("ok")

    once(tmp_path, body)


def test_an_output_named_twice_in_a_visit_is_an_error(tmp_path: Path) -> None:
    def body(ctx: Context) -> Outcome:
        _ = Actions().script(ctx, ScriptSpec(["true"], outputs=["n"]))
        with pytest.raises(ValueError, match="named twice"):
            _ = Actions().script(ctx, ScriptSpec(["true"], outputs=["n"]))
        return Outcome("ok")

    once(tmp_path, body)


def test_fan_out_runs_every_call_at_once_and_keeps_the_order() -> None:
    barrier = threading.Barrier(5, timeout=WAIT)

    def call(i: int) -> Callable[[], int]:
        return lambda: (barrier.wait(), i)[1]

    assert fan_out([call(i) for i in range(5)]) == [0, 1, 2, 3, 4]


def test_pool_waits_until_memory_is_returned() -> None:
    pool = ResourcePool(0.5)
    pool.acquire(0.5)
    blocked(lambda: pool.acquire(0.25), lambda: pool.release(0.5))
    assert pool.reserved_gb == 0.25


def test_pool_max_running_caps_the_reservations_held() -> None:
    pool = ResourcePool(0.5, max_running=1)
    pool.acquire(0.1)
    blocked(lambda: pool.acquire(0.1), lambda: pool.release(0.1))


def test_pool_rejects_a_reservation_larger_than_itself() -> None:
    with pytest.raises(ValueError, match=r"asked of a pool of 0\.5 GB"):
        ResourcePool(0.5).acquire(1.0)


def test_pool_larger_than_the_available_memory_is_an_error() -> None:
    with pytest.raises(ValueError, match="GB is available"):
        _ = ResourcePool(10.0**9)


def test_a_host_script_reserves_its_memory(tmp_path: Path) -> None:
    pool = ResourcePool(0.5)
    pool.acquire(0.5)

    def body(ctx: Context) -> Outcome:
        spec = ScriptSpec(["true"], memory_gb=0.5)
        blocked(lambda: Actions(pool=pool).script(ctx, spec), lambda: pool.release(0.5))
        return Outcome("ok")

    once(tmp_path, body)
    assert pool.reserved_gb == 0.0


class Trials:
    def __init__(self, pool: ResourcePool) -> None:
        self.pool: ResourcePool = pool
        self.reserved: list[float] = []

    def run(self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path) -> TrialResult:
        self.reserved.append(self.pool.reserved_gb)
        return TrialResult({"reward": 1.0}, None, 0.25, 1.0, {})


def test_a_trial_reserves_its_memory_and_records_its_directory(tmp_path: Path) -> None:
    pool = ResourcePool(0.5)
    trials = Trials(pool)

    def body(ctx: Context) -> Outcome:
        result = Actions(trial_runner=trials, pool=pool).trial(
            ctx, "t", tmp_path, "oracle", Limits(0.5)
        )
        assert result.rewards == {"reward": 1.0}
        assert ctx.attempt.visits[0].outputs == {"t": ctx.outdir / "t"}
        return Outcome("ok")

    once(tmp_path, body)
    assert trials.reserved == [0.5]
    assert pool.reserved_gb == 0.0


class FakeDocker:
    """The docker CLI, with the container's file system under root."""

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self.calls: list[list[str]] = []

    def path(self, inside: str) -> Path:
        return self.root / inside.lstrip("/")

    def __call__(self, args: Sequence[str]) -> CommandResult:
        args = list(args)
        self.calls.append(args)
        match args:
            case ["create", *_]:
                return CommandResult(0, "c1\n")
            case ["exec", "c1", "mkdir", "-p", *paths]:
                for path in paths:
                    self.path(path).mkdir(parents=True, exist_ok=True)
            case ["exec", "c1", "chmod", mode, path]:
                self.path(path).chmod(int(mode, 8))
            case ["cp", source, target] if source.startswith("c1:"):
                _ = shutil.copytree(
                    self.path(source[3:].removesuffix("/.")), target, dirs_exist_ok=True
                )
            case ["cp", source, target]:
                _ = shutil.copy(source, self.path(target.removeprefix("c1:")))
            case _:
                pass
        return CommandResult(0, "")


def test_docker_create_args(tmp_path: Path) -> None:
    env = Docker(
        "solver:1",
        mounts=[Mount(tmp_path / "cache", "/cache", read_only=False)],
        cpus=2.0,
        cpu_shares=512,
        memory_gb=4.0,
        pids=256,
        labels={"attempt": "7"},
        security=["no-new-privileges"],
    )
    assert env.create_args() == [
        "create", "--init",
        "--network", "none",
        "--cpus", "2.0",
        "--cpu-shares", "512",
        "--memory", "4096m",
        "--pids-limit", "256",
        "--label", "attempt=7",
        "--security-opt", "no-new-privileges",
        "--volume", f"{tmp_path}/cache:/cache",
        "solver:1", "sleep", "infinity",
    ]  # fmt: skip


class DockerAgent:
    """Writes "broken", then "valid", into the container's output directory."""

    def __init__(self, docker: FakeDocker, pool: ResourcePool) -> None:
        self.docker: FakeDocker = docker
        self.pool: ResourcePool = pool
        self.tasks: list[str] = ["broken", "valid"]
        self.reserved: list[float] = []
        self.validator: Path | None = None

    def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
        assert task.validator is not None
        self.validator = self.docker.path(task.validator)
        return self._write(task.outdir)

    def resume(self, session: object, prompt: str) -> AgentRun:
        assert isinstance(session, str)
        return self._write(session)

    def _write(self, outdir: str) -> AgentRun:
        self.reserved.append(self.pool.reserved_gb)
        _ = (self.docker.path(outdir) / "task").write_text(self.tasks.pop(0))
        return AgentRun(outdir, "ok", 1.0, None, "done")


def test_an_agent_in_docker_is_checked_on_its_copied_out_outputs(tmp_path: Path) -> None:
    validator = tmp_path / "check.sh"
    _ = validator.write_text('#!/bin/sh\ngrep -q valid "$1/task"\n')
    validator.chmod(0o755)
    docker = FakeDocker(tmp_path / "container")
    pool = ResourcePool(0.5)
    agent = DockerAgent(docker, pool)
    actions = Actions(agent_runner=agent, pool=pool)

    with Docker("author:1", memory_gb=0.5, cli=docker) as env:

        def body(ctx: Context) -> Outcome:
            spec = AgentSpec("Write.", tmp_path, outputs=["task"], validator=validator)
            result = actions.agent(ctx, spec, env)
            assert result.ok
            assert result.outputs["task"].read_text() == "valid"
            _ = actions.script(ctx, ScriptSpec(["true"]), env)
            return Outcome("ok")

        once(tmp_path, body)
        assert env.container == "c1"

    assert agent.validator is not None
    assert agent.validator == docker.path("/dataforge/validator/check.sh")
    assert agent.validator.stat().st_mode & 0o777 == 0o555
    assert agent.reserved == [0.5, 0.5]
    assert pool.reserved_gb == 0.0
    lifecycle = [call[0] for call in docker.calls if call[0] in {"create", "start", "stop", "rm"}]
    assert lifecycle == ["create", "start", "stop", "start", "stop", "rm"]


def test_a_failed_docker_start_returns_its_memory(tmp_path: Path) -> None:
    docker = FakeDocker(tmp_path / "container")
    failures = [CommandResult(1, "no such image")]

    def cli(args: Sequence[str]) -> CommandResult:
        return failures.pop() if args[0] == "create" and failures else docker(args)

    pool = ResourcePool(0.5)
    actions = Actions(pool=pool)
    with Docker("solver:1", memory_gb=0.5, cli=cli) as env:

        def body(ctx: Context) -> Outcome:
            with pytest.raises(RuntimeError, match="no such image"):
                _ = actions.script(ctx, ScriptSpec(["true"]), env)
            assert pool.reserved_gb == 0.0
            return Outcome(actions.script(ctx, ScriptSpec(["true"]), env).status)

        once(tmp_path, body)


def test_the_package_has_no_runtime_dependencies() -> None:
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["dependencies"] == []
