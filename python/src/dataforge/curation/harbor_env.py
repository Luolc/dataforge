"""Harbor 0.24.0's Docker environment, without its probe for no-network.

It also puts a tmpfs at /tmp of the task's `main`, when given `tmpfs_mb`,
which `HarborRunner` passes it with `--ek`.

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

With `tmpfs_mb`, a compose file this environment adds after all of Harbor's
(`_docker_compose_paths`, `docker.py:358-431`) gives `main` a tmpfs of that
many MiB at /tmp, mode 1777 and `exec`, as a container's own /tmp is, since
Docker mounts a tmpfs `noexec` unless told otherwise; it hides what the image
has under /tmp. Harbor's run-level compose files, dataforge's overlay among
them, never reach a separate verifier's environment (`trial.py:977-979`), but
the environment's kwargs do, so the verifier's `main` gets the tmpfs too.

The tmpfs holds memory only for what is written to it, and counts it against
the container's memory limit. So a /tmp that is full gives the process that
writes ENOSPC, at the tmpfs's size, while /tmp and the processes' memory
together past the limit get the container's processes killed for memory: as
it stops, this environment reads `State.OOMKilled` of `main` and says in the
trial's log (`trial.log`) when it was killed so.

A task whose own compose file mounts something at /tmp of `main` keeps it and
gets no tmpfs, since compose refuses two mounts at one target. The egress
sidecar gets none: it writes only a file that says it is ready, and a tmpfs
would take from its small memory limit. Without `tmpfs_mb` there is no such
file and the compose files are Harbor's.
"""

# Harbor is a development dependency only; its classes are typed loosely.
# pyright: reportMissingTypeStubs=false, reportUntypedBaseClass=false
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, cast, override
import weakref

from harbor.environments.docker.docker import (
  DockerEnvironment as HarborDockerEnvironment,
)
import yaml

_MAIN = "main"
_TMP = "/tmp"
# What the trial's log says of a `main` killed for memory, after its
# environment's session ID.
OOM_KILLED = "was killed for memory (State.OOMKilled)"
# How long the read of State.OOMKilled may take, in seconds.
_INSPECT_TIMEOUT = 60


class DockerEnvironment(HarborDockerEnvironment):
  """Harbor's Docker environment, which takes no-network as supported."""

  # Harbor's own arguments pass through as Harbor gives them, for Harbor to
  # check.
  def __init__(
    self,
    *args: Any,
    tmpfs_mb: int | None = None,
    **kwargs: Any,
  ) -> None:
    """Make the environment as Harbor's, with a tmpfs at main's /tmp.

    Args:
      *args: Harbor's arguments.
      tmpfs_mb: The size of the tmpfs at `main`'s /tmp, in MiB; None or 0
        for none.
      **kwargs: Harbor's keyword arguments.
    """
    super().__init__(*args, **kwargs)
    self._compose_path: Path | None = None
    if tmpfs_mb and not any(_mounts_tmp(main) for main in self._mains()):
      # exec as a container's own /tmp is: an agent or a test may build and
      # run a program there.
      options = f"size={tmpfs_mb * 2**20},mode=1777,exec"
      services = {_MAIN: {"tmpfs": [f"{_TMP}:{options}"]}}
      # Removed when the environment is collected, or at the latest when
      # Harbor's process exits.
      directory = tempfile.mkdtemp(prefix="dataforge-compose-")
      _ = weakref.finalize(self, shutil.rmtree, directory, ignore_errors=True)
      self._compose_path = Path(directory) / "docker-compose-dataforge.json"
      text = json.dumps({"services": services}, indent=2)
      _ = self._compose_path.write_text(text)

  def _mains(self) -> list[object]:
    # What each of the task's and the run's compose files says of `main`.
    found: list[object] = []
    for path in self._user_compose_paths():
      document = cast(object, yaml.safe_load(path.read_text()))
      if not isinstance(document, dict):
        continue
      services = cast(dict[object, object], document).get("services")
      if isinstance(services, dict) and _MAIN in services:
        found.append(cast(dict[object, object], services)[_MAIN])
    return found

  @property
  @override
  def _docker_compose_paths(self) -> list[Path]:
    paths = super()._docker_compose_paths
    if self._compose_path is None:
      return paths
    return [*paths, self._compose_path]

  @override
  async def stop(self, delete: bool) -> None:
    """Say in the trial's log whether main was killed for memory, then stop.

    Args:
      delete: As Harbor's.
    """
    if self._compose_path is not None:
      await self._log_oom_killed()
    await super().stop(delete)

  async def _log_oom_killed(self) -> None:
    try:
      listed = await self._run_docker_compose_command(
        ["ps", "--all", "--quiet", _MAIN], timeout_sec=_INSPECT_TIMEOUT
      )
      ids = (listed.stdout or "").split()
      if not ids:
        return
      process = await asyncio.create_subprocess_exec(
        "docker", "inspect", "--format", "{{.State.OOMKilled}}", *ids,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
      )  # fmt: skip
      out, _ = await asyncio.wait_for(process.communicate(), _INSPECT_TIMEOUT)
    except (OSError, RuntimeError, TimeoutError) as error:
      # The trial goes on without the answer; the log says it is missing.
      self.logger.warning("dataforge: State.OOMKilled not read: %s", error)
      return
    if "true" in out.decode(errors="replace").split():
      self.logger.warning(
        "dataforge: main of %s %s", self.session_id, OOM_KILLED
      )

  # Harbor's caches its probe's answer (`functools.cache`); this has none to
  # cache.
  @classmethod
  @override
  def _egress_control_kernel_support(  # pyright: ignore[reportIncompatibleVariableOverride]
    cls,
  ) -> bool:
    return True


def _mounts_tmp(service: object) -> bool:
  # Whether a compose file's service mounts something at /tmp itself, as a
  # tmpfs or a volume, in the short syntax or the long.
  if not isinstance(service, dict):
    return False
  service = cast(dict[str, object], service)
  targets = [
    *(_target(e, 0) for e in _entries(service.get("tmpfs"))),
    *(_target(e, 1) for e in _entries(service.get("volumes"))),
  ]
  return _TMP in targets


def _entries(value: object) -> list[object]:
  if isinstance(value, str):
    return [value]
  return cast(list[object], value) if isinstance(value, list) else []


def _target(entry: object, index: int) -> object:
  # A mount's target: the field of the long syntax, or, in the short one,
  # the index-th part ("/tmp:size=1m" 0, "src:/tmp:ro" 1), or the only part.
  if isinstance(entry, dict):
    return cast(dict[str, object], entry).get("target")
  if isinstance(entry, str):
    parts = entry.split(":")
    return parts[index] if len(parts) > index else parts[0]
  return None
