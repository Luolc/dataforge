# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportAny=false, reportPrivateUsage=false
# Harbor is a development dependency only; its classes are typed loosely.

"""Harbor's Docker environment as dataforge gives it.

It runs no probe for no-network, and puts a tmpfs at /tmp of the task's main.

A Docker daemon that took longer than Harbor's 30 s to run the probe's
container made Harbor's own environment refuse every task that asks for no
network; dataforge's runs no probe, also for a separate verifier, which Harbor
makes from the same configuration.
"""

import asyncio
from collections.abc import Generator
from dataclasses import dataclass
from importlib import metadata
import inspect
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import cast

from harbor.cli.utils import parse_kwargs
from harbor.environments.base import ExecResult
from harbor.environments.docker.docker import (
  DockerEnvironment as HarborDockerEnvironment,
)
from harbor.environments.factory import EnvironmentFactory
from harbor.models.task.config import (
  EnvironmentConfig,
  NetworkMode,
  NetworkPolicy,
)
from harbor.models.trial.config import EnvironmentConfig as TrialEnvironment
from harbor.models.trial.paths import TrialPaths
from harbor.trial.trial import Trial
import pytest
import yaml

from dataforge.curation import harbor, harbor_env
from dataforge.curation.harbor_env import DockerEnvironment, OOM_KILLED

NO_NETWORK = NetworkPolicy(network_mode=NetworkMode.NO_NETWORK)


@pytest.fixture
def slow_docker(
  monkeypatch: pytest.MonkeyPatch,
) -> Generator[list[tuple[list[str], float | None]]]:
  # A Docker that runs no command within its timeout; each command is recorded
  # with that timeout. Harbor caches its probe's answer for the process.
  calls: list[tuple[list[str], float | None]] = []

  def run(
    command: list[str],
    *_args: object,
    timeout: float | None = None,
    **_kwargs: object,
  ) -> None:
    calls.append((list(command), timeout))
    raise subprocess.TimeoutExpired(command, timeout or 0)

  monkeypatch.setattr(subprocess, "run", run)
  HarborDockerEnvironment._egress_control_kernel_support.cache_clear()
  yield calls
  HarborDockerEnvironment._egress_control_kernel_support.cache_clear()


def environment_dir(tmp_path: Path) -> Path:
  path = tmp_path / "environment"
  path.mkdir()
  _ = (path / "Dockerfile").write_text("FROM scratch\n")
  return path


def trial_paths(tmp_path: Path) -> TrialPaths:
  paths = TrialPaths(tmp_path / "trial")
  paths.mkdir()
  return paths


def make(kind: type[HarborDockerEnvironment], tmp_path: Path) -> object:
  # A trial's environment of a task that asks for no network.
  return kind(
    environment_dir=environment_dir(tmp_path),
    environment_name="task",
    session_id="task__1__env",
    trial_paths=trial_paths(tmp_path),
    task_env_config=EnvironmentConfig(),
    network_policy=NO_NETWORK,
  )


def test_harbors_own_environment_takes_a_slow_probe_for_no_support(
  tmp_path: Path, slow_docker: list[tuple[list[str], float | None]]
) -> None:
  # The pinned Harbor decides no-network by the method dataforge's overrides.
  assert metadata.version("harbor") == "0.24.0"
  assert "_egress_control_kernel_support" in vars(HarborDockerEnvironment)
  with pytest.raises(ValueError, match="'no-network' is not supported"):
    _ = make(HarborDockerEnvironment, tmp_path)
  [(command, timeout)] = slow_docker
  assert command[1:4] == ["container", "run", "--rm"]
  assert timeout == 30


def test_dataforges_runs_no_probe_and_denies_the_network(
  tmp_path: Path, slow_docker: list[tuple[list[str], float | None]]
) -> None:
  env = make(DockerEnvironment, tmp_path)
  assert slow_docker == []
  assert isinstance(env, HarborDockerEnvironment)
  assert env.capabilities.disable_internet
  assert env._enable_egress_control
  env.validate_network_policy_support()


def test_a_separate_verifier_gets_dataforges_by_its_import_path(
  tmp_path: Path, slow_docker: list[tuple[list[str], float | None]]
) -> None:
  # The trial's environment as `harbor run --env DOCKER_ENVIRONMENT` sets it
  # (`harbor/cli/jobs.py:1719-1722`), and a separate verifier's as Harbor makes
  # it from that, without the run's compose files
  # (`harbor/trial/trial.py:977-997`).
  config = TrialEnvironment(type=None, import_path=harbor.DOCKER_ENVIRONMENT)
  env = EnvironmentFactory.create_environment_from_config(
    config=config.model_copy(update={"extra_docker_compose": []}),
    stream=False,
    environment_dir=environment_dir(tmp_path),
    environment_name="task",
    session_id="task__1__verifier__0",
    trial_paths=trial_paths(tmp_path),
    task_env_config=EnvironmentConfig(),
    network_policy=NO_NETWORK,
    phase_network_policies=[NO_NETWORK],
  )
  assert type(env) is DockerEnvironment
  assert slow_docker == []
  assert env._enable_egress_control


TMPFS = ["/tmp:size=2147483648,mode=1777,exec"]


def compose_files(env: HarborDockerEnvironment) -> list[Path]:
  # The -f files of the compose commands Harbor runs for the environment.
  seen: list[list[str]] = []

  async def exec_(*argv: str, **_kwargs: object) -> None:
    seen.append(list(argv))
    raise OSError("no docker here")

  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(asyncio, "create_subprocess_exec", exec_)
    with pytest.raises(OSError, match="no docker here"):
      _ = asyncio.run(env._run_docker_compose_command(["config"]))
  [argv] = seen
  return [Path(argv[i + 1]) for i, arg in enumerate(argv) if arg == "-f"]


def harbors_files(env: DockerEnvironment) -> list[Path]:
  return super(DockerEnvironment, env)._docker_compose_paths


def added(env: object) -> dict[str, object] | None:
  # The services of the compose file dataforge's environment adds after
  # Harbor's, or None when it adds none.
  assert isinstance(env, DockerEnvironment)
  files = compose_files(env)
  harbors = harbors_files(env)
  if files == harbors:
    return None
  *before, ours = files
  assert before == harbors
  return json.loads(ours.read_text())["services"]


def with_tmpfs(
  tmp_path: Path, tmpfs_mb: int | None, directory: Path | None = None
) -> DockerEnvironment:
  return DockerEnvironment(
    environment_dir=directory or environment_dir(tmp_path),
    environment_name="task",
    session_id="task__1__env",
    trial_paths=trial_paths(tmp_path),
    task_env_config=EnvironmentConfig(),
    network_policy=NO_NETWORK,
    tmpfs_mb=tmpfs_mb,
  )


@pytest.mark.usefixtures("slow_docker")
def test_harbor_takes_its_compose_files_from_the_property_dataforges_extends(
  tmp_path: Path,
) -> None:
  assert metadata.version("harbor") == "0.24.0"
  assert isinstance(
    vars(HarborDockerEnvironment)["_docker_compose_paths"], property
  )
  harbors = HarborDockerEnvironment(
    environment_dir=environment_dir(tmp_path),
    environment_name="task",
    session_id="task__1__env",
    trial_paths=trial_paths(tmp_path),
    task_env_config=EnvironmentConfig(),
  )
  assert compose_files(harbors) == harbors._docker_compose_paths


@pytest.mark.usefixtures("slow_docker")
def test_main_gets_a_tmpfs_at_tmp_and_the_sidecar_none(tmp_path: Path) -> None:
  env = with_tmpfs(tmp_path, 2048)
  assert env._enable_egress_control
  assert added(env) == {"main": {"tmpfs": TMPFS}}


@pytest.mark.parametrize("tmpfs_mb", [None, 0])
@pytest.mark.usefixtures("slow_docker")
def test_without_a_size_the_compose_files_are_harbors(
  tmp_path: Path, tmpfs_mb: int | None
) -> None:
  assert added(with_tmpfs(tmp_path, tmpfs_mb)) is None
  plain = tmp_path / "plain"
  plain.mkdir()
  assert added(make(DockerEnvironment, plain)) is None


@pytest.mark.parametrize(
  "main",
  [
    {"tmpfs": "/tmp"},
    {"tmpfs": ["/run", "/tmp:size=64m"]},
    {"volumes": ["scratch:/tmp"]},
    {"volumes": ["/tmp"]},
    {"volumes": [{"type": "tmpfs", "target": "/tmp"}]},
  ],
)
@pytest.mark.usefixtures("slow_docker")
def test_a_task_that_mounts_its_own_tmp_gets_no_tmpfs(
  tmp_path: Path, main: dict[str, object]
) -> None:
  directory = environment_dir(tmp_path)
  services = {"services": {"main": main}}
  _ = (directory / "docker-compose.yaml").write_text(yaml.safe_dump(services))
  assert added(with_tmpfs(tmp_path, 2048, directory)) is None


@pytest.mark.usefixtures("slow_docker")
def test_a_task_that_mounts_elsewhere_still_gets_the_tmpfs(
  tmp_path: Path,
) -> None:
  directory = environment_dir(tmp_path)
  main = {"tmpfs": "/run", "volumes": ["data:/data", "/tmp/x:/x"]}
  services = {"services": {"main": main, "db": {"tmpfs": "/tmp"}}}
  _ = (directory / "docker-compose.yaml").write_text(yaml.safe_dump(services))
  assert added(with_tmpfs(tmp_path, 2048, directory)) == {
    "main": {"tmpfs": TMPFS}
  }


@pytest.mark.usefixtures("slow_docker")
def test_a_separate_verifier_gets_the_tmpfs_of_the_run(tmp_path: Path) -> None:
  # `--ek` as HarborRunner passes it, read as `harbor run` reads it
  # (`harbor/cli/jobs.py:1749-1752`), into the configuration from which Harbor
  # makes a separate verifier's environment, dropping only the run's compose
  # files (`harbor/trial/trial.py:977-979`).
  source = inspect.getsource(Trial._separate_verifier_env)
  assert (
    'model_copy(\n            update={"extra_docker_compose": []}' in source
  )
  config = TrialEnvironment(
    type=None,
    import_path=harbor.DOCKER_ENVIRONMENT,
    kwargs=parse_kwargs(["tmpfs_mb=2048"]),
    extra_docker_compose=[tmp_path / "overlay.json"],
  )
  env = EnvironmentFactory.create_environment_from_config(
    config=config.model_copy(update={"extra_docker_compose": []}),
    stream=False,
    environment_dir=environment_dir(tmp_path),
    environment_name="task",
    session_id="task__1__verifier__0",
    trial_paths=trial_paths(tmp_path),
    task_env_config=EnvironmentConfig(),
    network_policy=NO_NETWORK,
    phase_network_policies=[NO_NETWORK],
  )
  assert type(env) is DockerEnvironment
  assert added(env) == {"main": {"tmpfs": TMPFS}}


@dataclass
class Inspect:
  """A `docker inspect` process that answers, or hangs when it has no answer.

  Attributes:
    answer: What it writes to its stdout; None to hang.
    returncode: None while it runs.
    killed: Whether it was killed.
  """

  answer: bytes | None
  returncode: int | None = None
  killed: bool = False

  async def communicate(self) -> tuple[bytes, bytes]:
    if self.answer is None:
      _ = await asyncio.Event().wait()
    self.returncode = 0
    return self.answer or b"", b""

  def kill(self) -> None:
    self.killed = True

  async def wait(self) -> int:
    self.returncode = -9
    return self.returncode


def stop_with(
  env: DockerEnvironment, monkeypatch: pytest.MonkeyPatch, process: Inspect
) -> list[list[str]]:
  """Stop env with compose, the inspect process and Harbor's stop faked.

  Args:
    env: The environment to stop.
    monkeypatch: The test's.
    process: What starting a process gives.

  Returns:
    The compose commands, the processes' argvs and Harbor's stops, in order.
  """
  calls: list[list[str]] = []

  async def compose(command: list[str], **_kwargs: object) -> ExecResult:
    calls.append(command)
    return ExecResult(stdout="c1\n", return_code=0)

  async def exec_(*argv: str, **_kwargs: object) -> Inspect:
    calls.append(list(argv))
    return process

  async def stop(_self: object, delete: bool) -> None:
    calls.append(["harbor stop", str(delete)])

  monkeypatch.setattr(env, "_run_docker_compose_command", compose)
  monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_)
  monkeypatch.setattr(HarborDockerEnvironment, "stop", stop)
  asyncio.run(env.stop(delete=True))
  return calls


INSPECT = ["inspect", "--format", "{{.State.OOMKilled}} {{.State.Pid}}", "c1"]
# What the host shows of main's process 4242 and its cgroup.
CGROUP = "/system.slice/docker-c1.scope"
PIDS_READ = {"max": 530, "limit": "512"}


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  # The host's /proc and cgroup hierarchy, under tmp_path/host.
  root = tmp_path / "host"
  proc, cgroup = root / "proc", root / "sys" / "fs" / "cgroup"
  (proc / "4242").mkdir(parents=True)
  _ = (proc / "4242" / "cgroup").write_text(f"0::{CGROUP}\n")
  directory = cgroup / CGROUP.lstrip("/")
  directory.mkdir(parents=True)
  _ = (directory / "pids.events").write_text("max 530\n")
  _ = (directory / "pids.max").write_text("512\n")
  monkeypatch.setattr(harbor_env, "_PROC", proc)
  monkeypatch.setattr(harbor_env, "_CGROUP", cgroup)
  return root


def pids_read(tmp_path: Path, session: str = "task__1__env") -> object:
  path = tmp_path / "trial" / harbor.PIDS_FILE.format(session)
  return json.loads(path.read_text())


@pytest.mark.parametrize(
  ("inspected", "logged"),
  [
    (
      "true 4242\n",
      [
        f"dataforge: main of task__1__env {OOM_KILLED}",
        "dataforge: main of task__1__env hit its process limit (512) 530 times",
      ],
    ),
    (
      "false 4242\n",
      ["dataforge: main of task__1__env hit its process limit (512) 530 times"],
    ),
  ],
)
@pytest.mark.usefixtures("slow_docker", "host")
def test_stop_says_in_the_log_when_main_was_killed_for_memory(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
  inspected: str,
  logged: list[str],
) -> None:
  env = with_tmpfs(tmp_path, 2048)
  with caplog.at_level(logging.WARNING):
    calls = stop_with(env, monkeypatch, Inspect(inspected.encode()))
  assert calls == [
    ["ps", "--all", "--quiet", "main"],
    ["docker", *INSPECT],
    ["harbor stop", "True"],
  ]
  assert [r.getMessage() for r in caplog.records] == logged


@pytest.mark.parametrize("tmpfs_mb", [2048, None])
@pytest.mark.usefixtures("slow_docker", "host")
def test_stop_writes_how_often_main_hit_its_process_limit(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmpfs_mb: int | None
) -> None:
  env = with_tmpfs(tmp_path, tmpfs_mb)
  calls = stop_with(env, monkeypatch, Inspect(b"false 4242\n"))
  assert pids_read(tmp_path) == PIDS_READ
  # Read while main still ran, before Harbor's stop removed it.
  assert calls[-1] == ["harbor stop", "True"]


@pytest.mark.parametrize(
  ("inspected", "change", "unknown"),
  [
    (b"false 0\n", None, "main is not running"),
    (b"false\n", None, "main's State.Pid is not one number: []"),
    (b"false 4242\nfalse 4343\n", None,
     "main's State.Pid is not one number: ['4242', '4343']"),
    (b"false 4343\n", None,
     "FileNotFoundError: [Errno 2] No such file or directory: "),
    (b"false 4242\n", ("proc/4242/cgroup", "12:pids:/docker/c1\n"),
     "process 4242 is in no cgroup v2"),
    (b"false 4242\n", (".", None),
     "FileNotFoundError: [Errno 2] No such file or directory: "),
    (b"false 4242\n", ("pids.events", "max.imposed 1\n"),
     "KeyError: 'max'"),
    (b"false 4242\n", ("pids.events", "max lots\n"),
     "ValueError: invalid literal for int() with base 10: 'lots'"),
  ],
)  # fmt: skip
@pytest.mark.usefixtures("slow_docker")
def test_a_reading_that_fails_is_written_as_unknown_and_the_stop_goes_on(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
  host: Path,
  inspected: bytes,
  change: tuple[str, str | None] | None,
  unknown: str,
) -> None:
  # A file of /proc, or of main's cgroup, or the cgroup itself, changed.
  directory = host / "sys" / "fs" / "cgroup" / CGROUP.lstrip("/")
  if change is not None:
    name, text = change
    target = host / name if name.startswith("proc/") else directory / name
    if text is None:
      shutil.rmtree(target)
    else:
      _ = target.write_text(text)
  env = with_tmpfs(tmp_path, 2048)
  with caplog.at_level(logging.WARNING):
    calls = stop_with(env, monkeypatch, Inspect(inspected))
  assert calls[-1] == ["harbor stop", "True"]
  read = cast(dict[str, str], pids_read(tmp_path))
  assert list(read) == ["unknown"]
  assert read["unknown"].startswith(unknown)
  assert (
    caplog.records[-1]
    .getMessage()
    .startswith(
      f"dataforge: pids.events of main of task__1__env not read: {unknown}"
    )
  )


@pytest.mark.usefixtures("slow_docker", "host")
def test_a_separate_verifier_writes_its_own_reading(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  env = DockerEnvironment(
    environment_dir=environment_dir(tmp_path),
    environment_name="task",
    session_id="task__1__verifier__0",
    trial_paths=trial_paths(tmp_path),
    task_env_config=EnvironmentConfig(),
    network_policy=NO_NETWORK,
  )
  _ = stop_with(env, monkeypatch, Inspect(b"false 4242\n"))
  assert pids_read(tmp_path, "task__1__verifier__0") == PIDS_READ
  assert not (tmp_path / "trial" / "task__1__env.pids.json").exists()


@pytest.mark.usefixtures("slow_docker")
def test_stop_inspects_main_with_the_environments_engine(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  def podman(_cls: object, *args: str) -> list[str]:
    return ["podman", *args]

  monkeypatch.setattr(DockerEnvironment, "_engine_cmd", classmethod(podman))
  env = with_tmpfs(tmp_path, 2048)
  calls = stop_with(env, monkeypatch, Inspect(b"false 4242\n"))
  assert ["podman", *INSPECT] in calls


@pytest.mark.usefixtures("slow_docker")
def test_an_inspect_past_its_timeout_is_killed_and_the_stop_goes_on(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  monkeypatch.setattr(harbor_env, "_INSPECT_TIMEOUT", 0.01)
  env = with_tmpfs(tmp_path, 2048)
  hung = Inspect(None)
  with caplog.at_level(logging.WARNING):
    calls = stop_with(env, monkeypatch, hung)
  assert (hung.killed, hung.returncode) == (True, -9)
  assert calls[-1] == ["harbor stop", "True"]
  assert [r.getMessage() for r in caplog.records] == [
    "dataforge: State.OOMKilled not read: ",
    "dataforge: pids.events of main of task__1__env not read: "
    + "main not inspected: ",
  ]
  assert pids_read(tmp_path) == {"unknown": "main not inspected: "}
