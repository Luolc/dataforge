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

As it stops, while `main` still runs, this environment also reads from the
host how often a process in `main` could not start because the container was
at its process limit: the `max` count of `pids.events` in the cgroup that
`/proc/<State.Pid>/cgroup` names under /sys/fs/cgroup (cgroup v2, whatever
Docker's cgroup driver), with `pids.max` beside it; both files are readable by
any user, and the cgroup is gone once the container stops. Harbor's process
writes what it read into the trial's directory, which no container mounts, as
`<session_id>.pids.json` (`harbor.PIDS_FILE`): `{"max": <count>, "limit":
"<pids.max>"}`, or `{"unknown": "<why>"}` when it could not be read (cgroup
v1, a `main` that no longer runs, a cgroup already gone), and the trial's log
says so too; a failed read never fails the stop. The trial's environment
writes its file, and so does a separate verifier's, whose reading is a record
only: `HarborRunner` reads the trial environment's (`harbor`'s `_read`).

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

from dataforge.curation.harbor import PIDS_FILE

_MAIN = "main"
_TMP = "/tmp"
# Where the host sees each process's cgroups, and the cgroup v2 hierarchy.
_PROC = Path("/proc")
_CGROUP = Path("/sys/fs/cgroup")
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
    """Read main's end before Harbor's stop removes it, then stop.

    The log says whether main was killed for memory, and the trial's
    directory gets main's process limit hits (PIDS_FILE).

    Args:
      delete: As Harbor's.
    """
    await self._read_main()
    await super().stop(delete)

  async def _read_main(self) -> None:
    try:
      listed = await self._run_docker_compose_command(
        ["ps", "--all", "--quiet", _MAIN], timeout_sec=_INSPECT_TIMEOUT
      )
      ids = (listed.stdout or "").split()
      if not ids:
        self._write_pids({"unknown": "no main container"})
        return
      process = await asyncio.create_subprocess_exec(
        *type(self)._engine_cmd(
          "inspect", "--format", "{{.State.OOMKilled}} {{.State.Pid}}", *ids
        ),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
      )
      try:
        out, _ = await asyncio.wait_for(process.communicate(), _INSPECT_TIMEOUT)
      finally:
        if process.returncode is None:
          process.kill()
          _ = await process.wait()
    except (OSError, RuntimeError, TimeoutError) as error:
      # The trial goes on without the answers; the log says they are missing.
      self.logger.warning("dataforge: State.OOMKilled not read: %s", error)
      self._write_pids({"unknown": f"main not inspected: {error}"})
      return
    states = [
      line.split() for line in out.decode(errors="replace").splitlines()
    ]
    if any(state[:1] == ["true"] for state in states):
      self.logger.warning(
        "dataforge: main of %s %s", self.session_id, OOM_KILLED
      )
    pids = [state[1] for state in states if len(state) == 2]
    self._write_pids(
      _pids_hits(int(pids[0]), _PROC, _CGROUP)
      if len(pids) == 1 and pids[0].isdigit()
      else {"unknown": f"main's State.Pid is not one number: {pids}"}
    )

  def _write_pids(self, reading: dict[str, object]) -> None:
    if "unknown" in reading:
      self.logger.warning(
        "dataforge: pids.events of main of %s not read: %s",
        self.session_id,
        reading["unknown"],
      )
    elif reading["max"]:
      self.logger.warning(
        "dataforge: main of %s hit its process limit (%s) %s times",
        self.session_id,
        reading["limit"],
        reading["max"],
      )
    path = self.trial_paths.trial_dir / PIDS_FILE.format(self.session_id)
    try:
      _ = path.write_text(json.dumps(reading) + "\n")
    except OSError as error:
      self.logger.warning("dataforge: %s not written: %s", path, error)

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


def _pids_hits(pid: int, proc: Path, cgroup: Path) -> dict[str, object]:
  """Read how often the cgroup of a host process was at its process limit.

  Args:
    pid: The process, as the host numbers it; 0 for a container that is not
      running.
    proc: Where the host's /proc is.
    cgroup: Where the host's cgroup v2 hierarchy is.

  Returns:
    `{"max": <the max count of pids.events>, "limit": <pids.max>}`, or
    `{"unknown": <why>}` when they cannot be read.
  """
  if pid <= 0:
    return {"unknown": "main is not running"}
  try:
    lines = (proc / str(pid) / "cgroup").read_text().splitlines()
    path = next((line[3:] for line in lines if line.startswith("0::")), None)
    if path is None:
      return {"unknown": f"process {pid} is in no cgroup v2"}
    directory = cgroup / path.lstrip("/")
    events: dict[str, str] = {}
    for line in (directory / "pids.events").read_text().splitlines():
      key, _, value = line.partition(" ")
      events[key] = value
    limit = (directory / "pids.max").read_text().strip()
    return {"max": int(events["max"]), "limit": limit}
  except (OSError, KeyError, ValueError) as error:
    return {"unknown": f"{type(error).__name__}: {error}"}
