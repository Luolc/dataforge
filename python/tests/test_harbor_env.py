# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportAny=false, reportPrivateUsage=false
# Harbor is a development dependency only; its classes are typed loosely.

"""Harbor's Docker environment as dataforge gives it: no probe for no-network.

A Docker daemon that took longer than Harbor's 30 s to run the probe's
container made Harbor's own environment refuse every task that asks for no
network; dataforge's runs no probe, also for a separate verifier, which Harbor
makes from the same configuration.
"""

from collections.abc import Generator
from importlib import metadata
from pathlib import Path
import subprocess

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
import pytest

from dataforge.curation import harbor
from dataforge.curation.harbor_env import DockerEnvironment

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
