# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import cast, override

import pytest

from dataforge import stopping
from dataforge.curation import (
  Actions,
  AgentRun,
  AgentSpec,
  AgentTask,
  BY_VISIT,
  CommandResult,
  Context,
  Docker,
  End,
  Env,
  fan_out,
  Goto,
  Judge,
  JudgeError,
  Limits,
  Machine,
  Mount,
  Outcome,
  ResourcePool,
  ScriptSpec,
  ScriptState,
  State,
  SUMMARY_LIMIT,
  summary_path,
  TrialAgent,
  TrialResult,
)
from dataforge.curation import actions as actions_module
from dataforge.curation.actions import NOT_A_FILE
from tests.processes import holding, kill
from tests.sigterm import sigterm_once

WAIT = 5.0


class Once(State):
  def __init__(self, body: Callable[[Context], Outcome]) -> None:
    super().__init__("a", {"ok": End("ok")})
    self.body: Callable[[Context], Outcome] = body

  @override
  def run(self, ctx: Context) -> Outcome:
    return self.body(ctx)


def once(tmp_path: Path, body: Callable[[Context], Outcome]) -> None:
  """Run body as the one state of an attempt that must end "ok".

  Args:
    tmp_path: The test's directory.
    body: The state's run.
  """
  assert Machine([Once(body)]).run(tmp_path / "attempt").status == "ok"


def blocked(call: Callable[[], object], release: Callable[[], None]) -> None:
  """Assert that call waits until release is called, then finishes.

  Args:
    call: What waits.
    release: What lets it go on.
  """
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
    result = Actions().script(
      ctx, ScriptSpec(argv, inputs=[source], outputs=["out.txt"])
    )
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


def test_an_optional_output_is_recorded_when_written_and_not_missed(
  tmp_path: Path,
) -> None:
  def body(ctx: Context) -> Outcome:
    argv = ["sh", "-c", 'touch "$DATAFORGE_OUTPUTS/a"']
    spec = ScriptSpec(argv, outputs=["a"], optional_outputs=["b", "c"])
    result = Actions().script(ctx, spec)
    assert (result.status, sorted(result.outputs)) == ("ok", ["a"])
    argv = ["sh", "-c", 'touch "$DATAFORGE_OUTPUTS/e"']
    spec = ScriptSpec(argv, outputs=["d"], optional_outputs=["e"])
    result = Actions().script(ctx, spec)
    assert (result.status, sorted(result.outputs)) == ("failed", ["e"])
    assert "missing outputs: d" in result.log
    return Outcome("ok")

  once(tmp_path, body)


def test_an_optional_output_is_claimed_as_an_output(tmp_path: Path) -> None:
  def body(ctx: Context) -> Outcome:
    _ = Actions().script(ctx, ScriptSpec(["true"], optional_outputs=["n"]))
    with pytest.raises(ValueError, match="named twice"):
      _ = Actions().script(ctx, ScriptSpec(["true"], outputs=["n"]))
    return Outcome("ok")

  once(tmp_path, body)


def test_a_script_s_stdout_and_stderr_are_its_log_in_the_record(
  tmp_path: Path,
) -> None:
  root = tmp_path / "attempt"

  def body(ctx: Context) -> Outcome:
    argv = ["sh", "-c", "echo out; echo err >&2; exit 3"]
    _ = Actions().script(ctx, ScriptSpec(argv))
    _ = Actions().script(ctx, ScriptSpec(["true"]))
    return Outcome("ok")

  attempt = Machine([Once(body)]).run(root)
  first, second = attempt.visits[0].actions
  assert first.logs == [root / "logs/a/1/1.log"]
  assert second.logs == [root / "logs/a/1/2.log"]
  assert first.logs[0].read_text() == "out\nerr\n"
  visits = cast(list[dict[str, object]], attempt.to_json()["visits"])
  actions = cast(list[dict[str, object]], visits[0]["actions"])
  assert actions[0]["logs"] == ["logs/a/1/1.log"]


def test_a_visit_s_files_are_all_under_by_visit(tmp_path: Path) -> None:
  root = tmp_path / "attempt"
  out = '"$DATAFORGE_OUTPUTS"'
  write = f'echo hi > "$DATAFORGE_SUMMARY"; touch {out}/a'
  verdict = f'echo \'{{"outcome": "ok"}}\' > {out}/verdict.json'

  def body(ctx: Context) -> Outcome:
    _ = Actions().script(ctx, ScriptSpec(["sh", "-c", write], outputs=["a"]))
    _ = Actions().judge(ctx, Judge(ScriptSpec(["sh", "-c", verdict])))
    return Outcome("ok")

  _ = Machine([Once(body)]).run(root)
  files = sorted(
    str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
  )
  assert files
  for name in files:
    top, rest = name.split("/", 1)
    assert top in BY_VISIT, name
    assert rest.startswith("a/1/") or rest == "a/1.json", name


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


@pytest.mark.parametrize("failed", [False, True])
def test_a_sigterm_during_a_fan_out_ends_its_waits_for_the_pool(
  failed: bool,
) -> None:
  # With failed, the first call failed 0.2 s before the SIGTERM comes.
  pool = ResourcePool(0.5)
  pool.acquire(0.5)
  started: list[float] = []
  ended: list[float] = []

  def call() -> None:
    started.append(time.monotonic())
    try:
      pool.acquire(0.25)
    finally:
      ended.append(time.monotonic())

  def fail() -> None:
    started.append(time.monotonic())
    raise RuntimeError("failed")

  calls = [fail, call, call] if failed else [call, call]

  def ready() -> bool:
    return len(started) == len(calls) and time.monotonic() - max(started) > 0.2

  released: list[float] = []

  def release() -> None:
    released.append(time.monotonic())
    pool.release(0.5)

  # Were they not stopped, the calls would get the memory after WAIT.
  timer = threading.Timer(WAIT, release)
  timer.start()
  try:
    with sigterm_once(ready) as sent:
      with pytest.raises(SystemExit):
        _ = fan_out(calls)
      raised = time.monotonic()
    assert raised - sent[0] < 2
    assert len(ended) == 2
  finally:
    timer.cancel()
    timer.join()
    if not released:
      release()


def test_a_fan_out_interrupted_while_it_starts_its_calls_stops_those_started(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # The interrupt comes as the second call is being submitted.
  under = stopping.under
  bound: list[stopping.Stop] = []

  def interrupted[T](
    stop: stopping.Stop, call: Callable[[], T]
  ) -> Callable[[], T]:
    if bound:
      raise KeyboardInterrupt
    bound.append(stop)
    return under(stop, call)

  monkeypatch.setattr(stopping, "under", interrupted)
  timed_out: list[bool] = []

  def first() -> None:
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
      stopping.check()
      time.sleep(0.05)
    timed_out.append(True)

  with pytest.raises(KeyboardInterrupt):
    _ = fan_out([first, lambda: None])
  assert timed_out == []


def test_a_docker_env_of_a_stopped_fan_out_does_not_start() -> None:
  calls: list[Sequence[str]] = []

  def cli(args: Sequence[str]) -> CommandResult:
    calls.append(args)
    return CommandResult(0, "c1\n")

  env, stop = Docker("solver:1", cli=cli), stopping.Stop()

  def start() -> None:
    stop.set()
    env.start(None)

  with pytest.raises(stopping.Stopped):
    stopping.under(stop, start)()
  assert calls == []


def test_pool_waits_until_memory_is_returned() -> None:
  pool = ResourcePool(0.5)
  pool.acquire(0.5)
  blocked(lambda: pool.acquire(0.25), lambda: pool.release(0.5))
  assert pool.reserved_gb == 0.25


def test_pool_max_running_caps_the_reservations_held() -> None:
  pool = ResourcePool(0.5, max_running=1)
  pool.acquire(0.1)
  blocked(lambda: pool.acquire(0.1), lambda: pool.release(0.1))


def test_pool_gives_back_fractions_of_a_gb_exactly() -> None:
  pool = ResourcePool(1.0)
  for _ in range(3):
    pool.acquire(0.1)
  for _ in range(3):
    pool.release(0.1)
  assert pool.reserved_gb == 0.0


def test_pool_rejects_a_reservation_larger_than_itself() -> None:
  with pytest.raises(ValueError, match=r"asked of a pool of 0\.5 GB"):
    ResourcePool(0.5).acquire(1.0)


def test_pool_larger_than_the_available_memory_is_an_error() -> None:
  with pytest.raises(ValueError, match="GB is available"):
    _ = ResourcePool(10.0**9)


POOL = """
import sys
from dataforge.curation import ResourcePool
ResourcePool(float(sys.argv[1])).acquire(float(sys.argv[2]))
print("held", flush=True)
sys.stdin.read()
"""


def test_pool_counts_another_processs_reservation_until_it_is_killed() -> None:
  with holding(POOL, "0.5", "0.5") as other:
    pool = ResourcePool(0.5)
    assert pool.reserved_gb == 0.5
    blocked(lambda: pool.acquire(0.25), lambda: kill(other))
    assert pool.reserved_gb == 0.25


def test_a_pool_counts_another_processs_reservation_as_available(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # The other process's reservation may be in use, so the machine has less
  # available than the pool both give.
  with holding(POOL, "1.0", "0.5"):
    monkeypatch.setattr(actions_module, "_mem_available_gb", lambda: 0.75)
    assert ResourcePool(1.0).memory_gb == 1.0
    with pytest.raises(ValueError, match=r"1\.25 GB is available"):
      _ = ResourcePool(1.5)


def build(pool: ResourcePool) -> None:
  with pool.build(0.25):
    pass


def test_pool_build_slots_cap_the_builds_held() -> None:
  pool = ResourcePool(1.0, builds=1)
  with ExitStack() as held:
    held.enter_context(pool.build(0.25))

    def release() -> None:
      # A build waiting for its slot holds no memory meanwhile.
      assert pool.reserved_gb == 0.25
      held.close()

    blocked(lambda: build(pool), release)
  assert pool.reserved_gb == 0.0


def test_a_pool_lets_one_build_run_at_a_time_by_default() -> None:
  pool = ResourcePool(1.0)
  with ExitStack() as held:
    held.enter_context(pool.build(0.25))
    blocked(lambda: build(pool), held.close)


BUILD = """
import sys
from dataforge.curation import ResourcePool
build = ResourcePool(1.0, builds=1).build(0.25)
build.__enter__()
print("held", flush=True)
sys.stdin.read()
"""


def test_pool_counts_another_processs_build_until_it_is_killed() -> None:
  with holding(BUILD) as other:
    pool = ResourcePool(1.0, builds=1)
    blocked(lambda: build(pool), lambda: kill(other))


def test_a_host_script_reserves_its_memory(tmp_path: Path) -> None:
  pool = ResourcePool(0.5)
  pool.acquire(0.5)

  def body(ctx: Context) -> Outcome:
    spec = ScriptSpec(["true"], memory_gb=0.5)
    blocked(
      lambda: Actions(pool=pool).script(ctx, spec), lambda: pool.release(0.5)
    )
    return Outcome("ok")

  once(tmp_path, body)
  assert pool.reserved_gb == 0.0


def test_a_host_script_that_builds_takes_a_slot_before_its_memory(
  tmp_path: Path,
) -> None:
  pool = ResourcePool(1.0, builds=1)

  def body(ctx: Context) -> Outcome:
    spec = ScriptSpec(["true"], memory_gb=0.5, build=True)
    with ExitStack() as held:
      held.enter_context(pool.build(None))

      def release() -> None:
        # Waiting for its slot, the script holds no memory.
        assert pool.reserved_gb == 0.0
        held.close()

      blocked(lambda: Actions(pool=pool).script(ctx, spec), release)
    return Outcome("ok")

  once(tmp_path, body)
  assert pool.reserved_gb == 0.0


def test_a_script_that_builds_runs_only_on_the_host(tmp_path: Path) -> None:
  def body(ctx: Context) -> Outcome:
    spec = ScriptSpec(["true"], build=True)
    env = Docker("solver:1", cli=FakeDocker(tmp_path / "container"))
    with pytest.raises(ValueError, match="builds an image runs on the host"):
      _ = Actions(pool=ResourcePool(1.0)).script(ctx, spec, env)
    return Outcome("ok")

  once(tmp_path, body)


class Trials:
  def __init__(self, pool: ResourcePool) -> None:
    self.pool: ResourcePool = pool
    self.reserved: list[float] = []

  def memory_gb(self, limits: Limits) -> float:
    return limits.memory_gb + 0.0625

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    self.reserved.append(self.pool.reserved_gb)
    return TrialResult({"reward": 1.0}, None, 0.25, 1.0, {})


def test_a_trial_reserves_its_runners_memory_and_records_its_directory(
  tmp_path: Path,
) -> None:
  pool = ResourcePool(0.5625)
  trials = Trials(pool)

  def body(ctx: Context) -> Outcome:
    result = Actions(trial_runner=trials, pool=pool).trial(
      ctx, "t", tmp_path, "oracle", Limits(0.5)
    )
    assert result.rewards == {"reward": 1.0}
    assert ctx.attempt.visits[0].outputs == {"t": ctx.outdir / "t"}
    return Outcome("ok")

  once(tmp_path, body)
  assert trials.reserved == [0.5625]
  assert pool.reserved_gb == 0.0


class FakeDocker:
  """The docker CLI, with the container's file system under root.

  It acts for a non-root user and cap_drop ALL: only root makes directories
  under /, and only the container's user, who owns what docker cp copies in, can
  chmod a copied file.

  Args:
    root: Where the container's file system is.
  """

  def __init__(self, root: Path) -> None:
    self.root: Path = root
    self.calls: list[list[str]] = []
    self.copied: set[Path] = set()

  def path(self, inside: str) -> Path:
    return self.root / inside.lstrip("/")

  def __call__(self, args: Sequence[str]) -> CommandResult:
    args = list(args)
    self.calls.append(args)
    match args:
      case ["create", *_]:
        return CommandResult(0, "c1\n")
      case ["exec", "--user", "0", "c1", "mkdir", "-p", *paths]:
        for path in paths:
          self.path(path).mkdir(parents=True, exist_ok=True)
      case ["exec", "--user", "0", "c1", "chmod", mode, *paths] if not (
        self.copied & {self.path(path) for path in paths}
      ):
        for path in paths:
          self.path(path).chmod(int(mode, 8))
      case ["exec", "c1", "chmod", mode, path] if (
        self.path(path) in self.copied
      ):
        self.path(path).chmod(int(mode, 8))
      case ["exec", *_, "chmod", _, _]:
        return CommandResult(1, "chmod: Operation not permitted")
      case ["cp", source, target] if source.startswith("c1:"):
        _ = shutil.copytree(
          self.path(source[3:].removesuffix("/.")),
          target,
          symlinks=True,
          dirs_exist_ok=True,
        )
      case ["cp", source, target]:
        self.copied.add(self.path(target.removeprefix("c1:")))
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
    memory_swap_gb=4.0,
    pids=256,
    labels={"attempt": "7"},
    security=["no-new-privileges"],
    cap_drop=["ALL"],
    user="1000:1000",
    workdir="/app",
    env={"PROXY": "http://gateway.example.test"},
    pass_env=["GATEWAY_TOKEN"],
  )
  assert env.create_args() == [
    "create", "--init",
    "--network", "none",
    "--cpus", "2.0",
    "--cpu-shares", "512",
    "--memory", "4096m",
    "--memory-swap", "4096m",
    "--pids-limit", "256",
    "--label", "attempt=7",
    "--security-opt", "no-new-privileges",
    "--cap-drop", "ALL",
    "--user", "1000:1000",
    "--workdir", "/app",
    "--volume", f"{tmp_path}/cache:/cache",
    "--env", "PROXY=http://gateway.example.test",
    "--env", "GATEWAY_TOKEN",
    "solver:1", "sleep", "infinity",
  ]  # fmt: skip


def test_docker_create_args_by_default() -> None:
  assert Docker("solver:1").create_args() == [
    "create", "--init", "--network", "none", "solver:1", "sleep", "infinity"
  ]  # fmt: skip


class DockerAgent:
  """Writes "broken", then "valid", into the container's output directory.

  Args:
    docker: The fake docker CLI.
    pool: The pool, read at each run.
  """

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


def test_an_agent_in_docker_is_checked_on_its_copied_out_outputs(
  tmp_path: Path,
) -> None:
  validator = tmp_path / "check.sh"
  _ = validator.write_text('#!/bin/sh\ngrep -q valid "$1/task"\n')
  validator.chmod(0o755)
  docker = FakeDocker(tmp_path / "container")
  pool = ResourcePool(0.5)
  agent = DockerAgent(docker, pool)
  actions = Actions(agent_runner=agent, pool=pool)

  started: list[str | None] = []

  def pre_start(env: Docker) -> None:
    started.append(env.container)

  with Docker(
    "author:1", memory_gb=0.5, user="1000:1000", pre_start=pre_start, cli=docker
  ) as env:

    def body(ctx: Context) -> Outcome:
      spec = AgentSpec(
        "Write.", tmp_path, outputs=["task"], validator=validator
      )
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
  outdirs = list(docker.root.glob("dataforge/*/*/*/outputs"))
  assert outdirs and all(
    path.stat().st_mode & 0o777 == 0o777 for path in outdirs
  )
  assert agent.reserved == [0.5, 0.5]
  assert pool.reserved_gb == 0.0
  lifecycle = [
    call[0]
    for call in docker.calls
    if call[0] in {"create", "start", "stop", "rm"}
  ]
  assert lifecycle == ["create", "start", "stop", "start", "stop", "rm"]
  assert started == ["c1", "c1"]


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


class Twice(State):
  """Runs body on two visits; the first goes back to itself.

  Args:
    body: What each visit runs.
  """

  def __init__(self, body: Callable[[Context], None]) -> None:
    super().__init__("a", {"again": Goto("a"), "ok": End("ok")}, max_visits=2)
    self.body: Callable[[Context], None] = body

  @override
  def run(self, ctx: Context) -> Outcome:
    self.body(ctx)
    return Outcome("again" if ctx.visit == 1 else "ok")


class MountAgent:
  """Writes the visit into task and appends it to log, in the outdir's mount.

  Args:
    source: The mount's source on the host.
  """

  def __init__(self, source: Path) -> None:
    self.source: Path = source
    self.outdirs: list[str] = []

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    self.outdirs.append(task.outdir)
    visit = f"visit {len(self.outdirs)}"
    _ = (self.source / "task").write_text(visit)
    with (self.source / "log").open("a") as log:
      _ = log.write(visit + "\n")
    return AgentRun(None, "ok", 0.0, None, "done")

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("not resumed")


def test_outputs_in_a_mount_are_kept_across_visits_and_not_copied_out(
  tmp_path: Path,
) -> None:
  source = tmp_path / "out"
  source.mkdir()
  docker = FakeDocker(tmp_path / "container")
  agent = MountAgent(source)
  actions = Actions(agent_runner=agent)
  with Docker(
    "author:1", outputs=Mount(source, "/out", read_only=False), cli=docker
  ) as env:

    def body(ctx: Context) -> None:
      assert actions.agent(
        ctx, AgentSpec("Write.", tmp_path, outputs=["task"]), env
      ).ok

    attempt = Machine([Twice(body)]).run(tmp_path / "attempt")
  assert attempt.status == "ok"
  assert agent.outdirs == ["/out", "/out"]
  first, second = (visit.outputs["task"] for visit in attempt.visits)
  assert (first.read_text(), second.read_text()) == ("visit 1", "visit 2")
  assert (second.parent / "log").read_text() == "visit 1\nvisit 2\n"
  create = docker.calls[0]
  assert create[create.index("--volume") + 1] == f"{source.resolve()}:/out"
  assert not [call for call in docker.calls if call[0] == "cp"]


def test_post_start_runs_after_each_start_and_before_the_action(
  tmp_path: Path,
) -> None:
  docker = FakeDocker(tmp_path / "container")
  actions = Actions()

  def post_start(env: Docker) -> None:
    _ = env.exec(["forwarder"], {})

  with Docker("solver:1", post_start=post_start, cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      for _ in range(2):
        assert actions.script(ctx, ScriptSpec(["true"]), env).ok
      return Outcome("ok")

    once(tmp_path, body)
  steps = [call[0] if call[0] != "exec" else call[-1] for call in docker.calls]
  steps = [
    step for step in steps if step in {"start", "forwarder", "true", "stop"}
  ]
  assert steps == ["start", "forwarder", "true", "stop"] * 2


def test_a_failed_post_start_stops_the_container_and_returns_its_memory(
  tmp_path: Path,
) -> None:
  docker = FakeDocker(tmp_path / "container")
  pool = ResourcePool(0.5)

  def post_start(env: Docker) -> None:
    raise OSError("the forwarder did not start")

  env = Docker("solver:1", memory_gb=0.5, post_start=post_start, cli=docker)
  with env, pytest.raises(OSError, match="the forwarder did not start"):
    env.start(pool)
  assert pool.reserved_gb == 0.0
  assert [call[0] for call in docker.calls if call[0] in {"start", "stop"}] == [
    "start",
    "stop",
  ]


class DeletingAgent:
  """Writes a broken task and notes, then on the fix a valid task, no notes.

  Args:
    host: Gives the host path of a path in the container.
  """

  def __init__(self, host: Callable[[str], Path]) -> None:
    self.host: Callable[[str], Path] = host

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    outdir = self.host(task.outdir)
    _ = (outdir / "task").write_text("broken")
    _ = (outdir / "notes").write_text("notes")
    return AgentRun(outdir, "ok", 0.0, None, "done")

  def resume(self, session: object, prompt: str) -> AgentRun:
    assert isinstance(session, Path)
    _ = (session / "task").write_text("valid")
    (session / "notes").unlink()
    return AgentRun(session, "ok", 0.0, None, "done")


@pytest.mark.parametrize("mounted", [False, True])
def test_collect_removes_what_a_fix_deleted(
  tmp_path: Path, mounted: bool
) -> None:
  validator = tmp_path / "check.sh"
  _ = validator.write_text(
    '#!/bin/sh\ngrep -q valid "$1/task" && test -f "$1/notes"\n'
  )
  validator.chmod(0o755)
  docker = FakeDocker(tmp_path / "container")
  source = tmp_path / "out"
  source.mkdir()
  agent = DeletingAgent(
    lambda outdir: source if mounted else docker.path(outdir)
  )
  outputs = Mount(source, "/out", read_only=False) if mounted else None
  actions = Actions(agent_runner=agent)
  with Docker("author:1", outputs=outputs, cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      ctx.outdir.mkdir(parents=True)
      _ = (ctx.outdir / "other").write_text("another action's")
      spec = AgentSpec(
        "Write.",
        tmp_path,
        outputs=["task", "notes"],
        validator=validator,
        max_fixes=1,
      )
      result = actions.agent(ctx, spec, env)
      assert not result.ok
      assert "missing outputs: notes" in result.log
      assert sorted(path.name for path in ctx.outdir.iterdir()) == [
        "other",
        "task",
      ]
      assert sorted(path.name for path in ctx.outdir.parent.iterdir()) == ["1"]
      return Outcome("ok")

    once(tmp_path, body)


class LinkingAgent:
  """Writes a file, a directory and links, as links, into its outdir.

  Args:
    host: Gives the host path of a path in the container.
    links: The links to make, from path to target.
  """

  def __init__(
    self, host: Callable[[str], Path], links: dict[str, str]
  ) -> None:
    self.host: Callable[[str], Path] = host
    self.links: dict[str, str] = links

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    outdir = self.host(task.outdir)
    (outdir / "sub").mkdir()
    _ = (outdir / "sub" / "file").write_text("inside")
    for path, target in self.links.items():
      (outdir / path).symlink_to(target)
    return AgentRun(None, "ok", 0.0, None, "done")

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("not resumed")


def collect_links(
  tmp_path: Path, mounted: bool, links: Callable[[Path], dict[str, str]]
) -> tuple[Path, str]:
  """Run a LinkingAgent in Docker and return its visit's outputs and its log.

  Args:
    tmp_path: The test's directory.
    mounted: Whether it writes into an outputs mount.
    links: Gives the links to make from the visit's output directory.

  Returns:
    The visit's output directory on the host and the agent's log.
  """
  docker = FakeDocker(tmp_path / "container")
  source = tmp_path / "out"
  source.mkdir()
  outputs = Mount(source, "/out", read_only=False) if mounted else None
  found: list[tuple[Path, str]] = []
  with Docker("author:1", outputs=outputs, cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      agent = LinkingAgent(
        lambda outdir: source if mounted else docker.path(outdir),
        links(ctx.outdir),
      )
      result = Actions(agent_runner=agent).agent(
        ctx, AgentSpec("Write.", tmp_path, outputs=["sub"]), env
      )
      found.append((ctx.outdir, result.log))
      return Outcome(result.status)

    once(tmp_path, body)
  return found[0]


CLIMB = "sub/up/" * 4 + "../" * 3 + ".."


@pytest.mark.parametrize("mounted", [False, True])
@pytest.mark.parametrize(
  "kind", ["absolute", "dot-dot", "chain", "directory", "directory chain"]
)
def test_collect_removes_a_symlink_that_leads_out(
  tmp_path: Path, mounted: bool, kind: str
) -> None:
  hostdir = tmp_path / "host"
  hostdir.mkdir()
  _ = (hostdir / "secret").write_text("host-only")

  def links(outdir: Path) -> dict[str, str]:
    # outdir's parent is <attempt>/outputs/a, so ../.. from outdir is the
    # attempt, in tmp_path; each link reads host/secret when followed.
    _ = outdir
    return {
      "absolute": {"leak": str(hostdir / "secret")},
      "dot-dot": {"leak": "../../../../host/secret"},
      # sub/up leads back to outdir, so leak climbs four levels up from
      # there, though its text climbs no higher than sub/up/sub/up.
      "chain": {"sub/up": "..", "leak": f"{CLIMB}/host/secret"},
      "directory": {"leak": str(hostdir)},
      "directory chain": {"sub/up": "..", "leak": f"{CLIMB}/host"},
    }[kind]

  outdir, log = collect_links(tmp_path, mounted, links)
  for path in [outdir / "leak", outdir / "leak" / "secret"]:
    with pytest.raises(OSError):
      _ = path.read_text()
  assert f"outputs: removed symlink leak -> {links(outdir)['leak']}" in log
  assert (outdir / "sub" / "file").read_text() == "inside"


@pytest.mark.parametrize("mounted", [False, True])
def test_collect_keeps_a_symlink_that_stays_inside(
  tmp_path: Path, mounted: bool
) -> None:
  outdir, log = collect_links(
    tmp_path,
    mounted,
    lambda outdir: {
      "file": "sub/file",
      "sub/up": "..",
      "via": "sub/up/sub/./file",
      "dir": "sub",
      "missing": "sub/not-yet",
    },
  )
  assert (outdir / "file").read_text() == "inside"
  assert (outdir / "via").read_text() == "inside"
  assert (outdir / "dir" / "file").read_text() == "inside"
  assert (outdir / "sub" / "up" / "sub" / "file").read_text() == "inside"
  assert (outdir / "missing").is_symlink()
  assert "removed symlink" not in log


def test_collect_checks_a_directory_it_cannot_list(tmp_path: Path) -> None:
  hostdir = tmp_path / "host"
  hostdir.mkdir()
  _ = (hostdir / "secret").write_text("host-only")

  class LockingDocker(FakeDocker):
    # docker cp keeps the mode the container gave a directory.
    @override
    def __call__(self, args: Sequence[str]) -> CommandResult:
      result = super().__call__(args)
      if args[0] == "cp" and args[1].endswith("/outputs/."):
        locked = Path(args[2]) / "sub" / "locked"
        locked.mkdir()
        (locked / "leak").symlink_to(hostdir / "secret")
        locked.chmod(0o300)
      return result

  docker = LockingDocker(tmp_path / "container")
  outdirs: list[Path] = []
  logs: list[str] = []
  with Docker("author:1", cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      outdirs.append(ctx.outdir)
      agent = LinkingAgent(docker.path, {})
      spec = AgentSpec("Write.", tmp_path, outputs=["sub"])
      result = Actions(agent_runner=agent).agent(ctx, spec, env)
      logs.append(result.log)
      return Outcome(result.status)

    once(tmp_path, body)
  with pytest.raises(OSError):
    _ = (outdirs[0] / "sub" / "locked" / "leak").read_text()
  assert "outputs: removed symlink sub/locked/leak" in logs[0]
  assert [path.name for path in outdirs[0].parent.iterdir()] == ["1"]


def test_collect_from_a_mount_fails_on_a_file_it_cannot_read(
  tmp_path: Path,
) -> None:
  def make(outdir: Path) -> dict[str, str]:
    unreadable = tmp_path / "out" / "unreadable"
    _ = unreadable.write_text("the container's")
    unreadable.chmod(0)
    return {}

  with pytest.raises(PermissionError):
    _ = collect_links(tmp_path, True, make)
  assert not list((tmp_path / "attempt").glob("artifacts/a/.*"))


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_collect_from_a_mount_never_follows_a_link_swapped_in(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
  hostdir = tmp_path / "host"
  hostdir.mkdir()
  _ = (hostdir / "secret").write_text("host-only")
  swapped = tmp_path / "out" / "swapped"
  done: list[bool] = []

  def make(outdir: Path) -> dict[str, str]:
    _ = outdir
    if kind == "file":
      _ = swapped.write_text("plain")
    else:
      swapped.mkdir()
    return {}

  def swap() -> None:
    # The container swaps the file or directory for a link once collect has
    # looked at it, before collect copies it.
    if not done:
      done.append(True)
      if kind == "file":
        swapped.unlink()
      else:
        swapped.rmdir()
      swapped.symlink_to(hostdir / "secret" if kind == "file" else hostdir)

  real_stat, real_scandir = os.stat, os.scandir

  def stat(
    path: int | str | os.PathLike[str],
    *,
    dir_fd: int | None = None,
    follow_symlinks: bool = True,
  ) -> os.stat_result:
    status = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    if path == "swapped":
      swap()
    return status

  def scandir(path: int | str) -> AbstractContextManager[Iterator[object]]:
    if isinstance(path, int) or Path(path) != swapped.parent:
      return real_scandir(path)
    with real_scandir(path) as listed:
      entries = list(listed)
    swap()
    return nullcontext(iter(entries))

  monkeypatch.setattr(os, "stat", stat)
  monkeypatch.setattr(os, "scandir", scandir)
  outdir, log = collect_links(tmp_path, True, make)
  assert done
  copied = [
    Path(top, name) for top, _, names in os.walk(outdir) for name in names
  ]
  assert all(
    path.read_text() != "host-only" for path in copied if not path.is_symlink()
  )
  assert not os.path.lexists(outdir / "swapped")
  assert "outputs: left out swapped: it changed while copied" in log


MAKE_TASK = ScriptSpec(
  ["sh", "-c", 'T="$DATAFORGE_OUTPUTS/task"; mkdir "$T"; echo hi > "$T/a.txt"'],
  outputs=["task"],
)
ECHO_ARGS = ["sh", "-c", 'echo "$@" > "$DATAFORGE_OUTPUTS/args"', "echo"]


def test_inputs_name_paths_in_artifacts_and_may_be_optional(
  tmp_path: Path,
) -> None:
  def body(ctx: Context) -> Outcome:
    actions = Actions()
    assert actions.script(ctx, MAKE_TASK).ok
    spec = ScriptSpec(
      ECHO_ARGS,
      inputs=["task/a.txt"],
      outputs=["args"],
      optional_inputs=["absent", "task/absent.txt", "task"],
    )
    result = actions.script(ctx, spec)
    task = ctx.outdir / "task"
    assert result.outputs["args"].read_text() == f"{task}/a.txt {task}\n"
    with pytest.raises(KeyError):
      _ = actions.script(ctx, ScriptSpec(["true"], inputs=["absent/x"]))
    # An output whose name has a "/" is found by its whole name first.
    nested = [
      "sh",
      "-c",
      'mkdir "$DATAFORGE_OUTPUTS/d"; touch "$DATAFORGE_OUTPUTS/d/x"',
    ]
    assert actions.script(ctx, ScriptSpec(nested, outputs=["d/x"])).ok
    echo = ["sh", "-c", 'echo "$0" > "$DATAFORGE_OUTPUTS/named"']
    spec = ScriptSpec(echo, inputs=["d/x"], outputs=["named"])
    assert actions.script(ctx, spec).ok
    assert (ctx.outdir / "named").read_text() == f"{ctx.outdir}/d/x\n"
    return Outcome("ok")

  once(tmp_path, body)


class HostAgent:
  """Writes "done" to the output "out" and keeps its prompts."""

  def __init__(self) -> None:
    self.prompts: list[str] = []

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    self.prompts.append(task.prompt)
    _ = (Path(task.outdir) / "out").write_text("done")
    return AgentRun(None, "ok", 0.0, None, "done")

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("no fixes here")


def test_the_validator_gets_the_input_paths(tmp_path: Path) -> None:
  validator = tmp_path / "check.sh"
  seen = tmp_path / "seen"
  _ = validator.write_text(f'#!/bin/sh\necho "$@" > {seen}\n')
  validator.chmod(0o755)
  hint = tmp_path / "hint.txt"
  _ = hint.write_text("hint")
  agent = HostAgent()

  def body(ctx: Context) -> Outcome:
    spec = AgentSpec(
      "Write.",
      tmp_path,
      inputs=[hint],
      outputs=["out"],
      validator=validator,
      optional_inputs=[tmp_path / "absent.txt", validator],
    )
    assert Actions(agent_runner=agent).agent(ctx, spec).ok
    copy = ctx.attempt.root / "validators/a/1/1/check.sh"
    args = f"{ctx.outdir} {hint} {validator}"
    assert f"run `{copy} {args}`" in agent.prompts[0]
    assert seen.read_text() == f"{args}\n"
    return Outcome("ok")

  once(tmp_path, body)


def test_a_judge_without_a_valid_verdict_is_recorded_and_raises(
  tmp_path: Path,
) -> None:
  out = '"$DATAFORGE_OUTPUTS/verdict.json"'
  for script, why in [
    ("exit 2", "the judge exited 2"),
    ("true", "no valid verdict.json"),
    (f"echo '[]' > {out}", "verdict.json is not an object"),
    (
      f"echo '{{\"outcome\": 1}}' > {out}",
      "verdict.json has no outcome string",
    ),
    (
      f'echo \'{{"outcome": "x", "data": []}}\' > {out}',
      "verdict.json: data is not an object",
    ),
    (
      f'echo \'{{"outcome": "x", "feedback": {{"a": 1}}}}\' > {out}',
      "verdict.json: feedback is not an object of strings",
    ),
  ]:

    def body(ctx: Context, script: str = script, why: str = why) -> Outcome:
      judge = Judge(ScriptSpec(["sh", "-c", script]))
      with pytest.raises(JudgeError, match=f"a visit 1: {why}"):
        _ = Actions().judge(ctx, judge)
      assert [(a.kind, a.status) for a in ctx.attempt.visits[0].actions] == [
        ("judge", "failed")
      ]
      return Outcome("ok")

    once(tmp_path / str(len(why)), body)


def test_a_path_in_an_artifact_is_staged_in_docker(tmp_path: Path) -> None:
  docker = FakeDocker(tmp_path / "container")
  with Docker("task:1", cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      actions = Actions()
      assert actions.script(ctx, MAKE_TASK).ok
      spec = ScriptSpec(["true"], inputs=["task/a.txt"])
      assert actions.script(ctx, spec, env).ok
      return Outcome("ok")

    once(tmp_path, body)
  staged = docker.path("/dataforge/a/1/2/inputs/task/a.txt")
  assert staged.read_text() == "hi\n"


def test_an_input_may_name_the_state_that_wrote_it(tmp_path: Path) -> None:
  def writes(text: str) -> ScriptSpec:
    script = f'D="$DATAFORGE_OUTPUTS/v"; mkdir "$D"; echo {text} > "$D/x"'
    return ScriptSpec(["sh", "-c", script], outputs=["v"])

  cat = ScriptSpec(
    ["sh", "-c", 'cat "$@" > "$DATAFORGE_OUTPUTS/out"', "cat"],
    inputs=["a:v/x", "v/x"],
    outputs=["out"],
    optional_inputs=["nowhere:v/x", "a:absent"],
  )
  actions = Actions()
  states = [
    ScriptState("a", {"ok": Goto("b")}, actions=actions, spec=writes("from-a")),
    ScriptState("b", {"ok": Goto("c")}, actions=actions, spec=writes("from-b")),
    ScriptState("c", {"ok": End("ok")}, actions=actions, spec=cat),
  ]
  attempt = Machine(states).run(tmp_path / "attempt")
  assert attempt.status == "ok"
  assert attempt.visits[2].outputs["out"].read_text() == "from-a\nfrom-b\n"


def test_a_judge_gives_only_the_outcomes_it_names(tmp_path: Path) -> None:
  write = 'echo \'{"outcome": "maybe"}\' > "$DATAFORGE_OUTPUTS/verdict.json"'

  def body(ctx: Context) -> Outcome:
    judge = Judge(ScriptSpec(["sh", "-c", write]), outcomes=("yes", "no"))
    with pytest.raises(JudgeError, match="'maybe' is not one it may give"):
      _ = Actions().judge(ctx, judge)
    return Outcome("ok")

  once(tmp_path / "named", body)

  def loose(ctx: Context) -> Outcome:
    judge = Judge(ScriptSpec(["sh", "-c", write]))
    assert Actions().judge(ctx, judge).outcome == "maybe"
    return Outcome("ok")

  once(tmp_path / "loose", loose)


def test_scripts_and_judges_write_summaries_into_the_record(
  tmp_path: Path,
) -> None:
  verdict = 'echo \'{"outcome": "yes"}\' > "$DATAFORGE_OUTPUTS/verdict.json"'
  summary = 'echo "## $DATAFORGE_STATE" > "$DATAFORGE_SUMMARY"'

  def body(ctx: Context) -> Outcome:
    actions = Actions()
    built = ["sh", "-c", 'echo "# built" > "$DATAFORGE_SUMMARY"']
    assert actions.script(ctx, ScriptSpec(built)).ok
    assert actions.script(ctx, ScriptSpec(["true"])).ok
    judge = Judge(ScriptSpec(["sh", "-c", f"{verdict}; {summary}"]))
    assert actions.judge(ctx, judge).outcome == "yes"
    return Outcome("ok")

  attempt = Machine([Once(body)]).run(tmp_path / "attempt")
  assert [action.summary for action in attempt.visits[0].actions] == [
    "# built\n",
    None,
    "## a\n",
  ]
  visits = cast(
    list[dict[str, list[dict[str, object]]]], attempt.to_json()["visits"]
  )
  assert [action["summary"] for action in visits[0]["actions"]] == [
    "# built\n",
    None,
    "## a\n",
  ]


def test_a_summary_over_the_limit_is_cut_off_in_the_record(
  tmp_path: Path,
) -> None:
  # The last character, two bytes in UTF-8, straddles the limit.
  write = (
    "import os; open(os.environ['DATAFORGE_SUMMARY'], 'w')"
    f".write('a' * {SUMMARY_LIMIT - 1} + '\\u00e9')"
  )

  def body(ctx: Context) -> Outcome:
    assert Actions().script(ctx, ScriptSpec([sys.executable, "-c", write])).ok
    assert summary_path(ctx, 1).stat().st_size == SUMMARY_LIMIT + 1
    return Outcome("ok")

  attempt = Machine([Once(body)]).run(tmp_path / "attempt")
  summary = attempt.visits[0].actions[0].summary
  assert summary == "a" * (SUMMARY_LIMIT - 1) + (
    f"\n\n(cut off: the summary has {SUMMARY_LIMIT + 1} bytes, over the"
    f" {SUMMARY_LIMIT} kept)"
  )


class SummaryDocker(FakeDocker):
  """FakeDocker, where each exec of a script writes its summary."""

  @override
  def __call__(self, args: Sequence[str]) -> CommandResult:
    for arg in args:
      if arg.startswith("DATAFORGE_SUMMARY="):
        _ = self.path(arg.partition("=")[2]).write_text("in docker\n")
    return super().__call__(args)


def test_a_script_in_docker_writes_its_summary(tmp_path: Path) -> None:
  docker = SummaryDocker(tmp_path / "container")
  with Docker("builder:1", cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      return Outcome(Actions().script(ctx, ScriptSpec(["true"]), env).status)

    attempt = Machine([Once(body)]).run(tmp_path / "attempt")
  assert attempt.visits[0].actions[0].summary == "in docker\n"
  inside = docker.path("/dataforge/a/1/1/summary")
  assert inside.stat().st_mode & 0o777 == 0o777


def test_a_script_in_a_mount_copies_out_only_its_summary(
  tmp_path: Path,
) -> None:
  source = tmp_path / "out"
  source.mkdir()
  docker = SummaryDocker(tmp_path / "container")
  with Docker(
    "builder:1", outputs=Mount(source, "/out", read_only=False), cli=docker
  ) as env:

    def body(ctx: Context) -> Outcome:
      _ = (source / "built").write_text("in the mount")
      spec = ScriptSpec(["true"], outputs=["built"])
      return Outcome(Actions().script(ctx, spec, env).status)

    attempt = Machine([Once(body)]).run(tmp_path / "attempt")
  visit = attempt.visits[0]
  assert visit.actions[0].summary == "in docker\n"
  assert visit.outputs["built"].read_text() == "in the mount"
  copied_out = [
    call
    for call in docker.calls
    if call[0] == "cp" and call[1].startswith("c1:")
  ]
  summaries = tmp_path / "attempt" / "summaries" / "a" / "1" / "1"
  assert copied_out == [["cp", "c1:/dataforge/a/1/1/summary/.", str(summaries)]]


def test_a_summary_that_is_a_symlink_is_never_read(tmp_path: Path) -> None:
  secret = tmp_path / "host-only.txt"
  _ = secret.write_text("host-only text")
  link = ["sh", "-c", f'ln -s {secret} "$DATAFORGE_SUMMARY"']

  def body(ctx: Context) -> Outcome:
    return Outcome(Actions().script(ctx, ScriptSpec(link)).status)

  attempt = Machine([Once(body)]).run(tmp_path / "attempt")
  assert attempt.visits[0].actions[0].summary == NOT_A_FILE
  assert "host-only" not in json.dumps(attempt.to_json())


def test_a_symlink_in_dockers_summary_directory_that_leads_out_is_removed(
  tmp_path: Path,
) -> None:
  secret = tmp_path / "host-only.txt"
  _ = secret.write_text("host-only text")

  class LinkDocker(FakeDocker):
    @override
    def __call__(self, args: Sequence[str]) -> CommandResult:
      for arg in args:
        if arg.startswith("DATAFORGE_SUMMARY="):
          self.path(arg.partition("=")[2]).symlink_to(secret)
      return super().__call__(args)

  docker = LinkDocker(tmp_path / "container")
  logs: list[str] = []
  with Docker("builder:1", cli=docker) as env:

    def body(ctx: Context) -> Outcome:
      result = Actions().script(ctx, ScriptSpec(["true"]), env)
      logs.append(result.log)
      assert not os.path.lexists(summary_path(ctx, 1))
      return Outcome(result.status)

    attempt = Machine([Once(body)]).run(tmp_path / "attempt")
  assert attempt.visits[0].actions[0].summary is None
  assert logs == [
    f"summary: removed symlink summary.md -> {secret}: it leads out"
  ]


def test_a_dockers_repr_leaves_out_its_env() -> None:
  env = Docker("solver:1", env={"MODEL_TOKEN": "s3cret"})
  assert "image='solver:1'" in repr(env)
  assert "s3cret" not in repr(env)


class InspectDocker(FakeDocker):
  """FakeDocker with one container, c1, that docker inspect describes.

  Args:
    root: Where the container's file system is.
    found: What inspect prints for c1, besides its Id.
    stderr: What inspect also prints on stderr.
  """

  def __init__(
    self, root: Path, found: Mapping[str, object], stderr: str = ""
  ) -> None:
    super().__init__(root)
    self.found: Mapping[str, object] = found
    self.stderr: str = stderr

  @override
  def __call__(self, args: Sequence[str]) -> CommandResult:
    if args[0] != "inspect":
      return super().__call__(args)
    self.calls.append(list(args))
    if args[-1] != "c1":
      return CommandResult(1, f"Error: No such container: {args[-1]}")
    printed = json.dumps({"Id": "c1", **self.found})
    return CommandResult(0, f"{printed}\n{self.stderr}")


def isolated(tmp_path: Path, cli: InspectDocker) -> Docker:
  return Docker(
    "solver:1",
    mounts=[Mount(tmp_path / "in", "/in")],
    cpus=2.0,
    cpu_shares=512,
    memory_gb=0.5,
    memory_swap_gb=0.5,
    pids=256,
    labels={"run": "r1"},
    security=["no-new-privileges"],
    cap_drop=["ALL"],
    user="1000:1000",
    workdir="/app",
    cli=cli,
  )


def inspected(tmp_path: Path) -> dict[str, dict[str, object]]:
  """Return what docker inspect gives for a container isolated() created.

  Args:
    tmp_path: The test's directory.

  Returns:
    Its Config and HostConfig.
  """
  return {
    "Config": {
      "Image": "solver:1",
      "Labels": {"run": "r1", "from": "image"},
      "User": "1000:1000",
      "WorkingDir": "/app",
    },
    "HostConfig": {
      "NetworkMode": "none",
      "Binds": [f"{(tmp_path / 'in').resolve()}:/in:ro"],
      "CapDrop": ["ALL"],
      "SecurityOpt": ["no-new-privileges"],
      "NanoCpus": 2_000_000_000,
      "CpuShares": 512,
      "Memory": 512 * 2**20,
      "MemorySwap": 512 * 2**20,
      "PidsLimit": 256,
      "PidMode": "",
      "IpcMode": "shareable",
      "UTSMode": "",
      "UsernsMode": "",
      "CgroupnsMode": "private",
      "Mounts": [],
      "VolumesFrom": None,
    },
  }


def test_an_adopted_container_is_started_without_a_create(
  tmp_path: Path,
) -> None:
  docker = InspectDocker(
    tmp_path / "container", inspected(tmp_path), "WARNING: a note\n"
  )
  env = isolated(tmp_path, docker)
  env.adopt("c1")

  def body(ctx: Context) -> Outcome:
    assert Actions().script(ctx, ScriptSpec(["true"]), env).ok
    return Outcome("ok")

  once(tmp_path, body)
  assert env.container == "c1"
  assert "create" not in [call[0] for call in docker.calls]
  assert ["start", "c1"] in docker.calls


@pytest.mark.parametrize(
  ("part", "key", "value", "item"),
  [
    ("Config", "Image", "solver:2", "image"),
    ("Config", "Labels", {"run": "r2"}, "label run"),
    ("Config", "Labels", None, "label run"),
    ("Config", "User", "0", "user"),
    ("Config", "WorkingDir", "/", "workdir"),
    ("HostConfig", "NetworkMode", "bridge", "network"),
    ("HostConfig", "Binds", ["/:/in"], "mounts"),
    ("HostConfig", "CapDrop", ["NET_RAW"], "cap_drop"),
    ("HostConfig", "SecurityOpt", None, "security"),
    ("HostConfig", "NanoCpus", 0, "cpus"),
    ("HostConfig", "CpuShares", 1024, "cpu_shares"),
    ("HostConfig", "Memory", 0, "memory_gb"),
    ("HostConfig", "MemorySwap", -1, "memory_swap_gb"),
    ("HostConfig", "PidsLimit", None, "pids"),
    ("HostConfig", "Privileged", True, "privileged"),
    ("HostConfig", "CapAdd", ["SYS_ADMIN"], "cap_add"),
    ("HostConfig", "Devices", [{"PathOnHost": "/dev/fuse"}], "devices"),
    ("HostConfig", "PidMode", "host", "pid_mode"),
    ("HostConfig", "PidMode", "container:x", "pid_mode"),
    ("HostConfig", "IpcMode", "host", "ipc_mode"),
    ("HostConfig", "IpcMode", "container:x", "ipc_mode"),
    (
      "HostConfig",
      "SecurityOpt",
      ["no-new-privileges", "seccomp=unconfined"],
      "security",
    ),
    (
      "HostConfig",
      "SecurityOpt",
      ["no-new-privileges", "apparmor=unconfined"],
      "security",
    ),
    (
      "HostConfig",
      "Mounts",
      [{"Type": "bind", "Source": "/", "Target": "/host"}],
      "other_mounts",
    ),
    ("HostConfig", "VolumesFrom", ["other"], "volumes_from"),
    ("HostConfig", "UTSMode", "host", "uts_mode"),
    ("HostConfig", "UsernsMode", "host", "userns_mode"),
    ("HostConfig", "CgroupnsMode", "host", "cgroupns_mode"),
  ],
)
def test_a_container_unlike_the_env_is_not_adopted(
  tmp_path: Path, part: str, key: str, value: object, item: str
) -> None:
  found = inspected(tmp_path)
  found[part][key] = value
  env = isolated(tmp_path, InspectDocker(tmp_path, found))
  with pytest.raises(ValueError, match=f"c1 differs in {item}$"):
    env.adopt("c1")
  assert env.container is None


@pytest.mark.parametrize(
  ("ipc", "security"),
  [("", None), ("private", []), ("shareable", None)],
)
def test_a_container_in_dockers_default_namespaces_is_adopted(
  tmp_path: Path, ipc: str, security: list[str] | None
) -> None:
  # What a daemon gives a container run with no --ipc, --pid or
  # --security-opt depends on its configuration and version.
  host = {"NetworkMode": "none", "PidMode": "", "IpcMode": ipc}
  found = {
    "Config": {"Image": "solver:1"},
    "HostConfig": host | {"SecurityOpt": security},
  }
  env = Docker("solver:1", cli=InspectDocker(tmp_path, found))
  env.adopt("c1")
  assert env.container == "c1"


def test_a_container_is_checked_only_for_what_the_env_sets(
  tmp_path: Path,
) -> None:
  found = {
    "Config": {"Image": "solver:1", "User": "root"},
    "HostConfig": {"NetworkMode": "bridge", "CapDrop": ["CAP_NET_RAW"]},
  }
  env = Docker("solver:1", network=True, cli=InspectDocker(tmp_path, found))
  env.adopt("c1")
  assert env.container == "c1"
  for mode in ["none", "host"]:
    found["HostConfig"] = {"NetworkMode": mode}
    env = Docker("solver:1", network=True, cli=InspectDocker(tmp_path, found))
    with pytest.raises(ValueError, match=r"c1 differs in network$"):
      env.adopt("c1")


def test_a_privileged_container_with_an_added_capability_is_not_adopted(
  tmp_path: Path,
) -> None:
  # Like the env in every item it sets, but run with --privileged
  # --cap-add SYS_ADMIN.
  found = inspected(tmp_path)
  found["HostConfig"] |= {"Privileged": True, "CapAdd": ["CAP_SYS_ADMIN"]}
  env = isolated(tmp_path, InspectDocker(tmp_path, found))
  with pytest.raises(ValueError, match=r"c1 differs in privileged, cap_add$"):
    env.adopt("c1")
  assert env.container is None


def test_an_env_with_a_container_adopts_no_other(tmp_path: Path) -> None:
  docker = InspectDocker(tmp_path, inspected(tmp_path))
  env = isolated(tmp_path, docker)
  env.adopt("c1")
  with pytest.raises(ValueError, match="has a container already: c1"):
    env.adopt("c1")
  with pytest.raises(RuntimeError, match="No such container"):
    isolated(tmp_path, docker).adopt("c2")


@pytest.mark.parametrize("printed", ["[]\n", "WARNING: a note\n"])
def test_an_inspect_without_a_json_object_is_an_error(printed: str) -> None:
  env = Docker("solver:1", cli=lambda args: CommandResult(0, printed))
  with pytest.raises(ValueError):
    env.adopt("c1")
  assert env.container is None


class GitDocker:
  """The docker CLI over a directory per container, running exec on the host.

  An image is a directory, which create copies as the new container's file
  system. exec runs its command here in the container's directory, each
  argument and variable that is an absolute path, or one after "=", moved
  under that directory; git reads no configuration but the work tree's.

  Args:
    root: Where the containers' directories go.
    images: Each image's directory, by name.
  """

  def __init__(self, root: Path, images: Mapping[str, Path]) -> None:
    self.root: Path = root
    self.images: Mapping[str, Path] = images
    self.calls: list[list[str]] = []
    self.created: dict[str, list[str]] = {}
    self.running: set[str] = set()
    self.reserved: list[float] = []
    self.pool: ResourcePool | None = None

  def path(self, container: str, inside: str) -> Path:
    return self.root / container / inside.lstrip("/")

  def _move(self, container: str, arg: str) -> str:
    if arg.startswith("/"):
      return str(self.path(container, arg))
    key, equals, value = arg.partition("=/")
    return f"{key}={self.path(container, '/' + value)}" if equals else arg

  def __call__(self, args: Sequence[str]) -> CommandResult:
    args = list(args)
    self.calls.append(args)
    match args:
      case ["create", *_, image, "sleep", "infinity"]:
        container = f"c{len(self.created) + 1}"
        _ = shutil.copytree(
          self.images[image], self.root / container, symlinks=True
        )
        self.created[container] = args
        return CommandResult(0, f"{container}\n")
      case ["start", container]:
        if self.pool is not None:
          self.reserved.append(self.pool.reserved_gb)
        self.running.add(container)
        assert self.running == {container}, "two containers run at once"
      case ["stop", container]:
        self.running.discard(container)
      case ["rm", "--force", container]:
        self.running.discard(container)
        shutil.rmtree(self.root / container)
      case ["cp", source, target] if ":" in source:
        container, _, inside = source.partition(":")
        _ = shutil.copy(self.path(container, inside), target)
      case ["cp", source, target]:
        container, _, inside = target.partition(":")
        _ = shutil.copy(source, self.path(container, inside))
      case ["exec", *rest]:
        return self._exec(rest)
      case _:
        raise AssertionError(f"unexpected docker {args}")
    return CommandResult(0, "")

  def _exec(self, rest: list[str]) -> CommandResult:
    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    workdir = "/"
    while rest[0].startswith("--"):
      option, value, *rest = rest
      if option == "--workdir":
        workdir = value
      elif option == "--env":
        key, _, variable = value.partition("=")
        env[key] = variable
    container, *argv = rest
    assert container in self.running, f"exec in {container}, not running"
    env = {key: self._move(container, value) for key, value in env.items()}
    done = subprocess.run(
      [self._move(container, arg) for arg in argv],
      cwd=self.path(container, workdir),
      env={**os.environ, **env},
      capture_output=True,
      text=True,
      check=False,
    )
    return CommandResult(done.returncode, done.stdout + done.stderr)


def git(tree: Path, *args: str) -> str:
  return subprocess.run(
    ["git", "-C", str(tree), *args],
    capture_output=True,
    text=True,
    check=True,
    env={
      **os.environ,
      "GIT_CONFIG_GLOBAL": os.devnull,
      "GIT_CONFIG_NOSYSTEM": "1",
      "GIT_AUTHOR_NAME": "a",
      "GIT_AUTHOR_EMAIL": "a@example.test",
      "GIT_COMMITTER_NAME": "a",
      "GIT_COMMITTER_EMAIL": "a@example.test",
    },
  ).stdout


def commit_repository(tree: Path) -> None:
  tree.mkdir()
  _ = git(tree, "init", "--quiet")
  _ = (tree / "file.txt").write_text("one\n")
  _ = git(tree, "add", "--all")
  _ = git(tree, "commit", "--quiet", "--message", "one")


def images(tmp_path: Path) -> dict[str, Path]:
  """Make an old image and a new one whose /app has the same HEAD.

  HEAD has a repository inside it, as a checked out submodule is, and
  another whose directory is empty, as one never checked out. Both builds have
  rewritten a tracked file and left one git does not ignore, and the new one
  also another and one git ignores, as an image's build may.

  Args:
    tmp_path: The test's directory.

  Returns:
    The images' directories, by name.
  """
  app = tmp_path / "images" / "old" / "app"
  app.mkdir(parents=True)
  _ = git(app, "init", "--quiet")
  _ = (app / ".gitignore").write_text("build/\n")
  _ = (app / "kept.txt").write_text("one\n")
  _ = (app / "gone.txt").write_text("gone\n")
  _ = (app / "blob.bin").write_bytes(bytes(range(256)))
  _ = (app / "stamp.txt").write_text("in HEAD\n")
  commit_repository(app / "sub")
  commit = git(app / "sub", "rev-parse", "HEAD").strip()
  _ = git(app, "update-index", "--add", "--cacheinfo", f"160000,{commit},lib")
  (app / "lib").mkdir()
  _ = git(app, "add", "--all")
  _ = git(app, "commit", "--quiet", "--message", "one")
  _ = (app / "stamp.txt").write_text("by each image\n")
  _ = (app / "built.txt").write_text("by each image\n")
  new = tmp_path / "images" / "new"
  _ = shutil.copytree(app.parent, new, symlinks=True)
  _ = (new / "app" / "only-new.txt").write_text("by the new image\n")
  (new / "app" / "build").mkdir()
  _ = (new / "app" / "build" / "made").write_text("by the new image\n")
  return {"old:1": app.parent, "new:1": new}


def work(docker: GitDocker, container: str) -> None:
  # Change /app in container as a session would.
  app = docker.path(container, "/app")
  _ = (app / "kept.txt").write_text("two\n")
  (app / "gone.txt").unlink()
  _ = (app / "blob.bin").write_bytes(bytes(range(255, -1, -1)))
  _ = (app / "added.txt").write_text("added\n")
  _ = (app / "run.sh").write_text("#!/bin/sh\n")
  (app / "run.sh").chmod(0o755)
  (app / "build").mkdir()
  _ = (app / "build" / "stale").write_text("built in the old container\n")


def swap_env(docker: GitDocker) -> Docker:
  return Docker(
    "old:1",
    memory_gb=0.5,
    pids=256,
    labels={"attempt": "7"},
    security=["no-new-privileges"],
    cap_drop=["ALL"],
    user="1000:1000",
    workdir="/app",
    cli=docker,
  )


def test_replace_carries_the_changes_git_does_not_ignore(
  tmp_path: Path,
) -> None:
  docker = GitDocker(tmp_path / "containers", images(tmp_path))
  with swap_env(docker) as env:
    env.start(None)
    work(docker, "c1")
    env.stop()

    docker.pool = pool = ResourcePool(1.0)
    env.replace("new:1", carry=["/app"], pool=pool)

    assert (env.container, env.image) == ("c2", "new:1")
    old, new = docker.created["c1"], docker.created["c2"]
    assert new == [*old[:-3], "new:1", "sleep", "infinity"]
    assert "old:1" in old
    assert not (tmp_path / "containers" / "c1").exists()
    app = docker.path("c2", "/app")
    assert (app / "kept.txt").read_text() == "two\n"
    assert not (app / "gone.txt").exists()
    assert (app / "blob.bin").read_bytes() == bytes(range(255, -1, -1))
    assert (app / "added.txt").read_text() == "added\n"
    assert (app / "built.txt").read_text() == "by each image\n"
    assert (app / "stamp.txt").read_text() == "by each image\n"
    assert not (app / "only-new.txt").exists()
    assert os.access(app / "run.sh", os.X_OK)
    assert sorted(p.name for p in (app / "build").iterdir()) == ["made"]
    assert not docker.path("c2", "/dataforge/carry").exists()
    assert docker.running == set()
    assert docker.reserved == [0.5, 0.5]
    assert pool.reserved_gb == 0.0


def another_head(new: Path, app: Path) -> None:
  _ = (new / "app" / "other.txt").write_text("other\n")
  _ = git(new / "app", "add", "other.txt")
  _ = git(new / "app", "commit", "--quiet", "--message", "two")


def ignored_there(new: Path, app: Path) -> None:
  # The session no longer ignores build/ and adds what the new image built.
  _ = (app / ".gitignore").write_text("")
  _ = (app / "build" / "made").write_text("by the session\n")


@pytest.mark.parametrize(
  ("unlike", "why"),
  [
    (another_head, "HEAD has the tree"),
    (ignored_there, "git apply .* already exists"),
  ],
)
def test_a_replace_that_cannot_carry_keeps_the_old_container(
  tmp_path: Path, unlike: Callable[[Path, Path], None], why: str
) -> None:
  found = images(tmp_path)
  docker = GitDocker(tmp_path / "containers", found)
  with swap_env(docker) as env:
    env.start(None)
    work(docker, "c1")
    unlike(found["new:1"], docker.path("c1", "/app"))
    env.stop()

    with pytest.raises(RuntimeError, match=why):
      env.replace("new:1", carry=["/app"])

    assert (env.container, env.image) == ("c1", "old:1")
    assert ["rm", "--force", "c2"] in docker.calls
    assert not (tmp_path / "containers" / "c2").exists()
    app = docker.path("c1", "/app")
    assert (app / "kept.txt").read_text() == "two\n"
    assert (app / "added.txt").read_text() == "added\n"
    assert docker.running == set()


def a_new_repository(app: Path) -> None:
  commit_repository(app / "nested")


def a_changed_submodule(app: Path) -> None:
  _ = (app / "sub" / "file.txt").write_text("two\n")


def a_deleted_submodule(app: Path) -> None:
  shutil.rmtree(app / "sub")


def a_file_in_a_submodule_never_checked_out(app: Path) -> None:
  _ = (app / "lib" / "mine.c").write_text("int mine;\n")


@pytest.mark.parametrize(
  "change",
  [
    a_new_repository,
    a_changed_submodule,
    a_deleted_submodule,
    a_file_in_a_submodule_never_checked_out,
  ],
)
def test_a_changed_repository_in_the_tree_stops_replace_before_it_creates(
  tmp_path: Path, change: Callable[[Path], None]
) -> None:
  docker = GitDocker(tmp_path / "containers", images(tmp_path))
  with swap_env(docker) as env:
    env.start(None)
    work(docker, "c1")
    change(docker.path("c1", "/app"))
    env.stop()

    with pytest.raises(RuntimeError, match="repository inside the tree"):
      env.replace("new:1", carry=["/app"])

    assert (env.container, env.image) == ("c1", "old:1")
    assert list(docker.created) == ["c1"]
    assert (docker.path("c1", "/app") / "added.txt").read_text() == "added\n"
    assert docker.running == set()


def test_an_old_container_replace_cannot_remove_is_left_to_the_cleanup(
  tmp_path: Path,
) -> None:
  class Stuck(GitDocker):
    @override
    def __call__(self, args: Sequence[str]) -> CommandResult:
      if list(args) == ["rm", "--force", "c1"]:
        self.calls.append(list(args))
        return CommandResult(1, "removal of container c1 is in progress")
      return super().__call__(args)

  docker = Stuck(tmp_path / "containers", images(tmp_path))
  with swap_env(docker) as env:
    env.start(None)
    env.stop()

    env.replace("new:1", carry=["/app"])

    assert (env.container, env.image) == ("c2", "new:1")
    assert ["rm", "--force", "c1"] in docker.calls


def test_replace_waits_until_no_action_runs(tmp_path: Path) -> None:
  docker = GitDocker(tmp_path / "containers", images(tmp_path))
  with swap_env(docker) as env:
    env.start(None)
    with pytest.raises(RuntimeError, match="between actions"):
      env.replace("new:1", carry=["/app"])
    assert list(docker.created) == ["c1"]
    env.stop()


class CheckpointAgent:
  """Writes "out", gives a trace and a log and checkpoints its session.

  Args:
    trace: The trace its run gives.
  """

  def __init__(self, trace: Path) -> None:
    self.trace: Path = trace

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    _ = (Path(task.outdir) / "out").write_text("done")
    log = self.trace.with_name("agent-1.stderr.log")
    return AgentRun(
      "s1", "ok", 0.0, self.trace, "done", trace_format="fmt", log=log
    )

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("no fixes here")

  def checkpoint(self, session: object) -> dict[str, object]:
    return {"id": session}


@pytest.mark.parametrize("inside", [True, False])
def test_an_agent_records_its_checkpoint_and_the_trace_in_the_attempt(
  tmp_path: Path, inside: bool
) -> None:
  root = tmp_path / "attempt"
  trace = (root if inside else tmp_path) / "runs/agent-1.trace.jsonl"
  agent = CheckpointAgent(trace)
  seen: list[dict[str, object]] = []

  def body(ctx: Context) -> Outcome:
    spec = AgentSpec("Write.", tmp_path, outputs=["out"])
    assert Actions(agent_runner=agent).agent(ctx, spec).ok
    visits = cast(list[dict[str, object]], ctx.attempt.to_json()["visits"])
    seen.extend(cast(list[dict[str, object]], visits[0]["actions"]))
    return Outcome("ok")

  once(tmp_path, body)
  [action] = seen
  assert action["session"] == {"id": "s1"}
  expected = {"path": "runs/agent-1.trace.jsonl", "format": "fmt"}
  assert action["trace"] == (expected if inside else None)
  runs = ["runs/agent-1.stderr.log"] if inside else []
  assert action["logs"] == [*runs, "logs/a/1/1.log"]
  assert (root / "logs/a/1/1.log").read_text() == "done"


class FailingTrials(Trials):
  @override
  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    trace = outdir / "agent/trace.txt"
    trace.parent.mkdir()
    _ = trace.write_text("{}")
    return TrialResult(
      {"reward": 0.0},
      "TimeoutError: Harbor ran past 5 s",
      0.25,
      5.0,
      {},
      id="t-1",
      path=outdir,
      trace=trace,
      trace_format="fmt",
    )


def test_a_judge_sees_each_trials_record_and_exception(tmp_path: Path) -> None:
  out = '"$DATAFORGE_OUTPUTS"'
  script = (
    f'cp "$DATAFORGE_RECORD" {out}/seen.json'
    f' && echo \'{{"outcome": "ok"}}\' > {out}/verdict.json'
  )
  seen: list[object] = []

  def body(ctx: Context) -> Outcome:
    actions = Actions(trial_runner=FailingTrials(ResourcePool(1.0)))
    _ = actions.trial(ctx, "trial-0", tmp_path, "solver", Limits(0.5))
    judge = Judge(ScriptSpec(["sh", "-c", script], outputs=["seen.json"]))
    _ = actions.judge(ctx, judge)
    seen.append(
      cast("object", json.loads((ctx.outdir / "seen.json").read_text()))
    )
    return Outcome("ok")

  once(tmp_path, body)
  record = cast(dict[str, list[dict[str, list[dict[str, object]]]]], seen[0])
  [trial] = record["visits"][0]["actions"]
  assert trial["trial"] == {
    "id": "t-1",
    "path": "artifacts/a/1/trial-0",
    "trace": {"path": "artifacts/a/1/trial-0/agent/trace.txt", "format": "fmt"},
    "rewards": {"reward": 0.0},
    "exception": "TimeoutError: Harbor ran past 5 s",
  }
