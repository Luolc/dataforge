"""Harbor 0.24.0's Docker environment, without its probe for no-network.

Only Harbor imports this module, by the import path `HarborRunner` passes it
as `--env` (`harbor.DOCKER_ENVIRONMENT`), in its own process where the dataset
installed it; nothing else in dataforge does, so dataforge does not depend on
Harbor. Harbor makes a separate verifier's environment from the same
configuration, so that environment is this one too
(`harbor/trial/trial.py:977-997`).

Harbor's own environment gives a container no network through its egress
sidecar only when a probe says the Docker host's kernel can load the sidecar's
nftables rules: a container run with a timeout of 30 s, whose timeout or any
other failure counts as "cannot", so that a task that asks for no network fails
before its trial starts (`harbor/environments/docker/docker.py:210-217`,
`971-1002`; `harbor/environments/base.py:826-838`). A Docker daemon slow to run
a container makes the probe time out. `DockerEnvironment` runs no probe. A
kernel that cannot load the rules still fails the trial: the sidecar's
entrypoint exits before it writes the file its health check waits for, and the
containers routed through the sidecar start only once it is healthy
(`docker.py:480-509`), so a container never runs with the network it was to be
denied.
"""

# Harbor is a development dependency only; its classes are typed loosely.
# pyright: reportMissingTypeStubs=false, reportUntypedBaseClass=false

from typing import override

from harbor.environments.docker.docker import (
  DockerEnvironment as HarborDockerEnvironment,
)


class DockerEnvironment(HarborDockerEnvironment):
  """Harbor's Docker environment, which takes no-network as supported."""

  # Harbor's caches its probe's answer (`functools.cache`); this has none to
  # cache.
  @classmethod
  @override
  def _egress_control_kernel_support(  # pyright: ignore[reportIncompatibleVariableOverride]
    cls,
  ) -> bool:
    return True
