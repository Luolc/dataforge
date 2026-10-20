# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, nullcontext
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from typing import cast, override

import pytest

from dataforge.curation import (
  Actions,
  AgentRun,
  AgentSpec,
  AgentTask,
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
from dataforge.curation.actions import NOT_A_FILE

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
