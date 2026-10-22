"""Run a trial with Harbor: start the environment, run an agent, verify.

The trial starts the task's environment, runs an agent in it and runs the
verifier. Its result has the rewards, the cost and the agent's trace, which is
read for Harbor's claude-code, pi and codex agents (TRACES).

HarborRunner drives the `harbor` command line, which the dataset installs;
dataforge does not depend on Harbor. Harbor runs with an empty environment plus
a short list of variables, so that nothing of this process's environment (a
token, a proxy's session) reaches it or its containers unless the dataset passes
it in `env`.

A compose overlay of ours, passed to Harbor with `--extra-docker-compose`, caps
the task's container: every capability dropped, `no-new-privileges`, a process
limit, the trial's CPUs and memory, a CPU weight, and a label that every
container, network and image of the trial carries. It leaves the container's
network to `task.toml`: Harbor routes the container through its egress sidecar
only when no compose file names the container's network. When `task.toml`
declares a network mode other than `public`, for the environment or for the
agent's or the verifier's phase (a `[[steps]]` entry's phase overriding the
task's), Harbor starts that sidecar, and the overlay labels it and caps its
CPUs, memory and processes (`Sidecar`). Harbor reads its own compose file for
the sidecar after ours, and that file sets none of these. A task directory's own
compose file, which Harbor reads before ours, can take the same limits from
DATAFORGE_SIDECAR_CPUS and DATAFORGE_SIDECAR_MEMORY_MB in Harbor's environment.
After the run, whatever happened, the trial's files are given back to this user
and everything with the label is removed, and so is every container, labelled
or not, that mounts from under the trial's output directory, and every
container, network and volume of the compose projects Harbor named after the
trial (`remove_projects`), such as a separate verifier's sidecar; when run is
interrupted, Harbor and its children are stopped first. Then, as the
containers wrote into the trial's directory, each symlink there that resolves,
or may, outside it, and each entry that is no regular file, directory or
symlink, is removed and named in `harbor.log`, before run gives the trial to any
reader; when the cleanup fails, run raises DockerError and gives no trial.

When Harbor exits before it starts the trial (no `job/*/config.json` or
`job/*/result.json`), nothing of the task has run and no model has been called:
run moves what that run left (`harbor.log`, `job/`, `overlay.json`) into
`not-started-<n>/` and runs the trial again, from its setup on, up to RETRIES
times while time is left of `timeout`, which bounds all the runs together. An
interrupt, such as the SystemExit of a SIGTERM, escapes run at once, so no run
follows it. A trial that still did not start gives an exception that starts with
`HarborNotStarted:` and holds the end of the last `harbor.log`, with the values
of the trial's env (HIDE_MIN characters or more) replaced by `***`.

Harbor's own Docker environment probes, with a timeout of 30 s, whether the
Docker host can deny a container the network, and fails each task that asks for
no network when the probe does not answer in time; `environment` set to
DOCKER_ENVIRONMENT makes Harbor use `dataforge.curation.harbor_env`'s, which
runs no probe.

`tmpfs_mb`, 2048 by default, puts a tmpfs of that many MiB at /tmp of the
task's `main`, in the trial's environment and a separate verifier's, so that
what a trial writes to /tmp goes to memory, not the disk: it reaches the
environment as `--ek tmpfs_mb=<MiB>`, which DOCKER_ENVIRONMENT's takes. The
tmpfs holds memory only for what is written to it, and counts it against the
container's memory limit: a full /tmp gives ENOSPC, and /tmp and the
processes' memory past the limit get the container's processes killed for
memory, which the trial's `trial.log` then says (`harbor_env.OOM_KILLED`).
None or 0, or an environment of Harbor's own, gives no tmpfs.

A trial reserves from the pool the task's memory plus the sidecar's limit
(`memory_gb`), whether or not the task has a sidecar; image builds and the
cleanup's container use memory besides, so a dataset leaves the pool some
headroom.

The task directory's own compose file may set the same `cap_drop` and
`security_opt` items as the overlay; compose rejects a list that has an item
twice, so the overlay leaves out the items the task's `main` already has, and
`main` still ends up with every one of them.

A dataset connects its own host side (a model gateway, say) through `mounts`,
`env`, extra compose files in `compose_overlay`, and `setup`, a context that
spans the whole trial and may add to the other three for that trial.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import tomllib
from typing import cast
import uuid

from dataforge import stopping
from dataforge.curation.actions import (
  drop_escaping_links,
  drop_special_files,
  Limits,
  Mount,
  TrialAgent,
  TrialResult,
)
from dataforge.runtime import claude, codex, pi

_log = logging.getLogger(__name__)

LABEL = "dataforge.trial"
CPUS = 2.0
CPU_SHARES = 1024
PIDS = 512
# A bound on one trial past the timeouts Harbor enforces itself (the agent's and
# the verifier's in task.toml), for a Harbor that hangs.
TIMEOUT = 3600.0
# How long a stopped Harbor gets after SIGTERM before SIGKILL, and STOPPED_S,
# how long one gets when its fan_out was stopped, within the stop's budget.
STOP_S = 30.0
STOPPED_S = 10.0
# What `cleanup` removes, in its order.
KINDS = ("container", "image", "network", "volume")
SIDECAR = "harbor-docker-egress-control-sidecar"
SIDECAR_PIDS = 64
# The phases whose network_mode, besides the environment's, can make Harbor
# start the sidecar.
PHASES = ("agent", "verifier")
# The variables that give a task directory's compose file the sidecar's limits;
# compose reads them from Harbor's environment.
SIDECAR_CPUS_VAR = "DATAFORGE_SIDECAR_CPUS"
SIDECAR_MEMORY_VAR = "DATAFORGE_SIDECAR_MEMORY_MB"
# The task's container runs as root without CAP_CHOWN, so Harbor cannot give the
# files it writes back to this user; a container of this image does. The
# reference stays on one line so that it is found and replaced whole.
CHOWN_IMAGE = "ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517"  # noqa: E501
# The variables of this process that Harbor and docker get; everything else is
# dropped.
PASSED = ("HOME", "PATH", "DOCKER_HOST", "DOCKER_CONFIG")
# Harbor's Docker environment without its probe for no-network, as `environment`
# names it; only Harbor's process imports the module.
DOCKER_ENVIRONMENT = "dataforge.curation.harbor_env:DockerEnvironment"
# The tmpfs at /tmp of a trial's `main`, in MiB, by default.
TMPFS_MB = 2048
# How many more times a trial Harbor did not start is run.
RETRIES = 2
# The most lines, and bytes, of harbor.log that a trial Harbor did not start
# gives in its exception.
LOG_LINES = 20
LOG_BYTES = 4096
# The shortest value of the trial's env that is hidden in that exception; a
# shorter one, such as "1", would hide ordinary text.
HIDE_MIN = 8
NOT_STARTED = "HarborNotStarted"
# How the exception of a trial whose Harbor ran past `timeout` starts.
TIMED_OUT = "TimeoutError"
# The label compose gives what a project holds, its value the project's name.
PROJECT = "com.docker.compose.project"
# How long one docker command of the cleanup may run, in seconds.
DOCKER_TIMEOUT = 120.0


class DockerError(subprocess.SubprocessError, RuntimeError):
  """Docker failed, ran past its timeout or left something in a cleanup.

  An infrastructure failure, not the task's: what is left of the trial is not
  known, so the trial gives no result. It is a SubprocessError, as the
  `subprocess.TimeoutExpired` and `CalledProcessError` that a cleanup's docker
  raised before were, and a RuntimeError, as a container left or a failed
  `docker container ls` or `inspect` raised before, so that a caller that
  caught either catches it.
  """


@dataclass(frozen=True)
class TraceFile:
  """Where in a trial's directory one of Harbor's agents tees its trace.

  Attributes:
    path: The file, relative to the trial's directory.
    format: What it holds.
    cost: Reads the cost a trace gives, in US dollars, or None when it gives
      none.
  """

  path: Path
  format: str
  cost: Callable[[Path], float | None]


# The traces of Harbor's claude-code, pi and codex agents; a trial's trace is
# the first of them that is a regular file.
TRACES = (
  TraceFile(
    Path("agent", "claude-code.txt"),
    # claude.TRACE_FORMAT, which runtime.claude, importing this module, has not
    # set yet when this runs.
    "claude-code-stream-json",
    lambda path: claude.read_trace(path).cost_usd,
  ),
  TraceFile(
    Path("agent", "pi.txt"),
    pi.TRACE_FORMAT,
    lambda path: pi.read_trace(path).cost_usd,
  ),
  TraceFile(Path("agent", "codex.txt"), codex.TRACE_FORMAT, lambda _: None),
)

TRACE_LIMIT = 64 * 2**20
"""The most bytes of a trial's trace read for its cost."""


@dataclass(frozen=True)
class Sidecar:
  """The limits of Harbor's egress sidecar.

  The defaults are about three times the most a sidecar took in nop and oracle
  trials with egress denied: 22 MiB of memory, 23 processes and 0.17 of a CPU,
  as a dataset repo measured them with its own Harbor wrapper on 2026-10-19;
  they were not measured again here.

  Attributes:
    cpus: Its CPUs.
    memory_mb: Its memory, in MiB.
  """

  cpus: float = 0.5
  memory_mb: int = 64

  @property
  def memory_gb(self) -> float:
    """Its memory, in GB."""
    return self.memory_mb / 1024


@dataclass(frozen=True)
class TrialSetup:
  """What a dataset adds to one trial, on top of the runner's own.

  Attributes:
    mounts: Added to the runner's mounts.
    env: Added to the runner's env; its variables replace the runner's of the
      same name. Left out of the repr, as its values may be credentials.
    compose_overlay: Added to the runner's compose_overlay.
  """

  mounts: Sequence[Mount] = ()
  env: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)
  compose_overlay: Sequence[Path] = ()


@dataclass(frozen=True)
class HarborRunner:
  """A TrialRunner that runs each trial as a Harbor job of one attempt.

  A trial's agent is "nop", "oracle", or a mapping with "name" (a Harbor agent)
  or "import_path", and optionally "model" and "kwargs" (Harbor's
  --agent-kwarg). The task's container gets limits.memory_gb and limits.cpus;
  the egress sidecar, when there is one, gets sidecar's limits and SIDECAR_PIDS
  processes.

  A trial's trace is the first file of TRACES that is a regular file in the
  trial's directory, not a symlink: `agent/claude-code.txt`, which Harbor's
  claude-code agent writes (`claude-code-stream-json`), `agent/pi.txt`, which
  its pi agent writes (`pi-json`), or `agent/codex.txt`, which its codex agent
  writes (`codex-exec-jsonl`). A trial without one has no trace and no trace
  format.

  A trial's cost is the `agent_result.cost_usd` of Harbor's `result.json`. When
  that is not a number, it is read from the trial's trace, only when that is at
  most TRACE_LIMIT bytes: for Claude Code, the `total_cost_usd` of the last
  `result` event; for Pi, the sum of the `usage.cost.total` of the assistant
  messages' `message_end` events, the price Pi lists, not what was charged.
  Codex's trace gives no cost; for it, and for a trial without a trace or
  whose trace gives none, the cost is 0.

  Attributes:
    harbor: The `harbor` command.
    cpus: The task container's CPUs when the limits give none.
    cpu_shares: The task container's CPU weight.
    pids: The task container's process limit.
    sidecar: The egress sidecar's limits.
    timeout: How long Harbor may run, over all its runs of one trial; a
      trial that ran past it gives an exception that starts with
      `TimeoutError: Harbor ran past`, and then names what Harbor recorded,
      such as the CancelledError of being stopped.
    mounts: Go to the task's container.
    env: Go to the task's container and to Harbor, whose agents read some
      variables from their own environment. The overlay names the variables
      without their values, and the repr leaves them out.
    compose_overlay: Compose files that come after ours, so they can override
      it; one that names the container's network (`network_mode: none`, say)
      takes it off Harbor's egress sidecar.
    setup: Called with run's arguments, so that it can tell an agent's trial
      from nop's or oracle's; run enters the context it returns before it
      writes the overlay and starts Harbor, adds the TrialSetup it yields to
      the trial, and exits it after the cleanup, also when Harbor or the
      cleanup failed. A dataset serves a model gateway on a socket there, say,
      and yields the socket's mount. It runs inside the trial's reservation
      from the pool, as run does. A container of its own that mounts from
      under the output directory is removed with the trial's, before the
      context exits. A trial that Harbor runs again, as it did not start,
      enters it again.
    docker: The docker command that reads the task's compose file and that the
      cleanup runs; when docker fails during the cleanup, run raises, since it
      cannot tell what is left.
    verifier: The import path (`module:Class`) of the verifier Harbor runs
      instead of its own, passed as `--verifier`, so that a dataset can take
      over how the reward is read.
    environment: The environment Harbor runs the trial in, passed as `--env`:
      a type of Harbor's, such as `docker`, or the import path of a class,
      such as DOCKER_ENVIRONMENT; None leaves Harbor's default, `docker`.
    tmpfs_mb: The size of the tmpfs at /tmp of the task's `main`, in MiB,
      passed to the environment as its `tmpfs_mb` keyword argument when
      environment is set, which DOCKER_ENVIRONMENT's takes; None or 0 for
      none.
  """

  harbor: Path
  cpus: float = CPUS
  cpu_shares: int = CPU_SHARES
  pids: int = PIDS
  sidecar: Sidecar = Sidecar()
  timeout: float = TIMEOUT
  mounts: Sequence[Mount] = ()
  env: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)
  compose_overlay: Sequence[Path] = ()
  setup: (
    Callable[
      [Path, TrialAgent, Limits, Path], AbstractContextManager[TrialSetup]
    ]
    | None
  ) = None
  docker: str = "docker"
  verifier: str | None = None
  environment: str | None = None
  tmpfs_mb: int | None = TMPFS_MB

  def memory_gb(self, limits: Limits) -> float:
    """Return the memory a trial reserves: the task's and the sidecar's.

    Args:
      limits: The trial's limits.

    Returns:
      The memory, in GB.
    """
    return limits.memory_gb + self.sidecar.memory_gb

  def run(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> TrialResult:
    """Run one trial as a Harbor job.

    Args:
      task_dir: The task's directory.
      agent: Who the trial runs.
      limits: Its limits.
      outdir: The directory it writes into.

    Returns:
      Its result.

    Raises:
      DockerError: When docker, in the cleanup after a run of Harbor, fails,
        runs past DOCKER_TIMEOUT (300 s for the chown) or leaves a container
        that mounts from under outdir or something with the trial's label; no
        run follows it.
      stopping.Stopped: The stop of the call this thread runs is set; a run
        then starts nothing, or stops Harbor and cleans up within the stop's
        budget, and no run follows it.
    """  # noqa: DOC502
    deadline = time.monotonic() + self.timeout
    number = 1
    while True:
      result = self._set_up_and_run(
        task_dir, agent, limits, outdir, deadline, number
      )
      if number > RETRIES or _started(outdir) or time.monotonic() >= deadline:
        return result
      kept = outdir / f"not-started-{number}"
      kept.mkdir()
      for name in ("harbor.log", "job", "overlay.json"):
        if os.path.lexists(outdir / name):
          _ = (outdir / name).rename(kept / name)
      number += 1

  def _set_up_and_run(
    self,
    task_dir: Path,
    agent: TrialAgent,
    limits: Limits,
    outdir: Path,
    deadline: float,
    number: int,
  ) -> TrialResult:
    # Before anything that the cleanup would have to undo, on every run.
    stopping.check()
    context = (
      nullcontext(TrialSetup())
      if self.setup is None
      else self.setup(task_dir, agent, limits, outdir)
    )
    with context as extra:
      runner = replace(
        self,
        mounts=(*self.mounts, *extra.mounts),
        env={**self.env, **extra.env},
        compose_overlay=(*self.compose_overlay, *extra.compose_overlay),
      )
      return runner._run(task_dir, agent, limits, outdir, deadline, number)

  def _run(
    self,
    task_dir: Path,
    agent: TrialAgent,
    limits: Limits,
    outdir: Path,
    deadline: float,
    number: int,
  ) -> TrialResult:
    egress = _has_sidecar(task_dir)
    agent_args = _agent_args(agent)
    label = uuid.uuid4().hex
    overlay = outdir / "overlay.json"
    task = _task_main(task_dir, self.docker)
    _ = overlay.write_text(
      json.dumps(self.overlay(egress, limits, label, task), indent=2)
    )
    args = [
      str(self.harbor), "run",
      "--path", str(task_dir.resolve()),
      *agent_args,
      *(() if self.verifier is None else ("--verifier", self.verifier)),
      *(() if self.environment is None else ("--env", self.environment)),
      *(
        ("--ek", f"tmpfs_mb={self.tmpfs_mb}")
        if self.environment is not None and self.tmpfs_mb
        else ()
      ),
      "--jobs-dir", str(outdir.resolve()),
      "--job-name", "job",
      "--n-attempts", "1",
      "--n-concurrent", "1",
      "--extra-docker-compose", str(overlay.resolve()),
      *[arg for path in self.compose_overlay
        for arg in ("--extra-docker-compose", str(path.resolve()))],
      "--yes",
    ]  # fmt: skip
    log = outdir / "harbor.log"
    started = time.monotonic()
    try:
      error = self._harbor(args, log, deadline)
    finally:
      held, stray, left = self._clean_up(outdir, label)
    if held:
      raise DockerError(f"containers still mount {outdir}: {held}")
    if any(stray.values()):
      raise DockerError(f"left in the compose projects of {outdir}: {stray}")
    if any(left.values()):
      raise DockerError(f"left after the cleanup of {LABEL}={label}: {left}")
    # No container can reach the trial now, so nothing in it changes after it
    # was checked.
    notes: list[str] = []
    for trial in sorted((outdir / "job").glob("*/")):
      if not trial.is_symlink():
        notes += drop_escaping_links(trial, f"trial {trial.name}")
        notes += drop_special_files(trial, f"trial {trial.name}")
    if notes:
      with log.open("a") as out:
        _ = out.write("".join(f"{note}\n" for note in notes))
    elapsed = time.monotonic() - started
    if not _started(outdir):
      why = error or "harbor exited 0"
      runs = f"run {number} of at most {RETRIES + 1}"
      reason = (
        f"{NOT_STARTED}: Harbor did not start the trial ({why}), on {runs};"
        + f" the end of harbor.log:\n{_tail(log, self.env.values())}"
      )
      return TrialResult({}, reason, 0.0, elapsed, {"log": log})
    return _read(outdir, log, error, elapsed)

  def overlay(
    self,
    egress: bool,
    limits: Limits,
    label: str,
    task: Mapping[str, object] | None = None,
  ) -> object:
    """Return the compose overlay of one trial.

    Args:
      egress: Whether Harbor starts its egress sidecar for the task.
      limits: The trial's limits.
      label: The trial's label.
      task: The `main` service of the task's own compose file; the overlay
        leaves out the `cap_drop` and `security_opt` items it already has.

    Returns:
      The overlay, as compose reads it from JSON.
    """

    def missing(key: str, item: str) -> list[str]:
      return (
        []
        if item in cast(Sequence[object], (task or {}).get(key) or ())
        else [item]
      )

    labels = {LABEL: label}
    main: dict[str, object] = {
      "cap_drop": missing("cap_drop", "ALL"),
      "security_opt": missing("security_opt", "no-new-privileges:true"),
      "pids_limit": self.pids,
      "cpus": self.cpus if limits.cpus is None else limits.cpus,
      "cpu_shares": self.cpu_shares,
      "mem_limit": f"{round(limits.memory_gb * 1024)}m",
      "labels": labels,
      "build": {"labels": labels},
    }
    if self.mounts:
      main["volumes"] = [
        f"{m.source.resolve()}:{m.target}{':ro' if m.read_only else ''}"
        for m in self.mounts
      ]
    if self.env:
      # Names only: compose takes the values from Harbor's environment, which
      # has env, so no value is written to the overlay in outdir.
      main["environment"] = sorted(self.env)
    services: dict[str, object] = {"main": main}
    if egress:
      services[SIDECAR] = {
        "cpus": self.sidecar.cpus,
        "mem_limit": f"{self.sidecar.memory_mb}m",
        "pids_limit": SIDECAR_PIDS,
        "labels": labels,
      }
    return {"services": services, "networks": {"default": {"labels": labels}}}

  def _harbor(
    self, args: Sequence[str], log: Path, deadline: float
  ) -> str | None:
    """Run Harbor.

    Args:
      args: Its command line.
      log: The file its output goes to.
      deadline: When it is stopped, by time.monotonic().

    Returns:
      Why it failed, or None when it exited 0.

    Raises:
      BaseException: Whatever interrupted the wait, such as the SystemExit of a
        SIGTERM, a KeyboardInterrupt or the Stopped of a stopped `fan_out`,
        once Harbor is stopped; Stopped also before Harbor starts.
    """
    env = {
      **_passed(),
      "HARBOR_TELEMETRY": "0",
      **self.env,
      SIDECAR_CPUS_VAR: str(self.sidecar.cpus),
      SIDECAR_MEMORY_VAR: str(self.sidecar.memory_mb),
    }
    stopping.check()
    with log.open("w") as out:
      process = subprocess.Popen(
        args,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
      )
      try:
        code = stopping.wait(process, max(0.0, deadline - time.monotonic()))
      except subprocess.TimeoutExpired:
        _stop(process)
        return f"{TIMED_OUT}: Harbor ran past {self.timeout:g} s"
      except BaseException:
        # Interrupted (a SIGTERM made SystemExit, Ctrl-C, a stopped fan_out):
        # Harbor could still start a container or write after the cleanup
        # gave its files back.
        _stop(process)
        raise
    return None if code == 0 else f"HarborError: harbor exited {code}"

  def _clean_up(
    self, outdir: Path, label: str
  ) -> tuple[list[str], dict[str, int], dict[str, int]]:
    # What still mounts the trial, what its compose projects hold and what has
    # its label, once removed.
    if stopping.stopped():
      self._clean_up_stopped(outdir, label)
      raise stopping.Stopped
    try:
      held = release(outdir, self.docker)
    finally:
      try:
        stray = remove_projects(outdir, self.docker)
      finally:
        try:
          self._chown(outdir, label)
        finally:
          left = cleanup(f"{LABEL}={label}", self.docker)
    return held, stray, left

  def _clean_up_stopped(self, outdir: Path, label: str) -> None:
    # For a stopped fan_out, within its budget: what may still run, and cost,
    # goes first, the containers that mount the trial, have its label or are
    # in its compose projects; giving the files back and removing the rest
    # take what is left. What the budget does not reach is logged, not waited
    # for.
    selector = f"{LABEL}={label}"
    seen: dict[str, int] = {}
    held: list[str] = []

    def mounting() -> None:
      held[:] = release(outdir, self.docker)

    def projects() -> None:
      for kind, n in remove_projects(outdir, self.docker).items():
        seen[f"project {kind}"] = n

    steps: list[tuple[str, Callable[[], object]]] = [
      ("its mounting containers", mounting),
      (
        "its containers",
        lambda: cleanup(selector, self.docker, KINDS[:1], seen),
      ),
      ("its compose projects", projects),
      ("its files", lambda: self._chown(outdir, label)),
      ("the rest", lambda: cleanup(selector, self.docker, KINDS, seen)),
    ]
    for i, (what, step) in enumerate(steps):
      try:
        _ = step()
      except Exception as error:  # noqa: BLE001
        # The other steps still run while time is left; what one could not
        # do is logged.
        if stopping.left(1.0) > 0:
          _log.warning(
            "stopped: the cleanup of %s failed at %s: %s", selector, what, error
          )
          continue
        _log.warning(
          "stopped: out of time cleaning up %s at %s; not done: %s; seen: %s;"
          + " still mounting it: %s",
          selector,
          what,
          ", ".join(name for name, _ in steps[i + 1 :]) or "nothing",
          seen,
          held,
        )
        return
    left = {kind: n for kind, n in seen.items() if n}
    if left or held:
      _log.warning(
        "stopped: left after the cleanup of %s: %s; still mounting it: %s",
        selector,
        left,
        held,
      )

  def _chown(self, outdir: Path, label: str) -> None:
    _ = _docker(
      self.docker, "run", "--rm",
      f"--label={LABEL}={label}",
      "--network=none",
      "--cap-drop=ALL",
      "--cap-add=CHOWN", "--cap-add=DAC_READ_SEARCH",
      "--security-opt=no-new-privileges",
      "--cpus=1", "--memory=256m", "--pids-limit=64",
      f"--volume={outdir.resolve()}:/trial",
      CHOWN_IMAGE,
      "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/trial",
      timeout=300,
      check=True,
    )  # fmt: skip


def cleanup(
  selector: str,
  docker: str = "docker",
  kinds: Sequence[str] = KINDS,
  seen: dict[str, int] | None = None,
) -> dict[str, int]:
  """Remove the containers, images, networks and volumes a label selects.

  Args:
    selector: A docker label filter, such as "dataforge.trial=<id>".
    docker: The docker command.
    kinds: Which of KINDS to remove, in KINDS's order whatever theirs.
    seen: Gets, by kind, how many each listing found, the last one's.

  Returns:
    How many of each kind are left.

  Raises:
    DockerError: When `docker <kind> ls` exits non-zero, or a docker command
      runs past DOCKER_TIMEOUT.
  """  # noqa: DOC502

  def ids(kind: str) -> list[str]:
    # Without -a, `image ls` leaves out labelled intermediate images.
    extra = ["-a"] if kind in ("container", "image") else []
    out = _docker(
      docker, kind, "ls", "-q", *extra, "--filter", f"label={selector}",
      check=True,
    ).stdout  # fmt: skip
    found = sorted(set(out.split()))
    if seen is not None:
      seen[kind] = len(found)
    return found

  # An image whose child is still there is not removed, so images take as many
  # rounds as the deepest chain of them.
  for kind, remove, rounds in (
    ("container", ["rm", "-f"], 1),
    ("image", ["rmi", "-f"], 20),
    ("network", ["rm"], 1),
    ("volume", ["rm", "-f"], 1),
  ):
    if kind not in kinds:
      continue
    for _ in range(rounds):
      if not (found := ids(kind)):
        break
      _ = _docker(docker, kind, *remove, *found)
  return {kind: len(ids(kind)) for kind in kinds}


def release(path: Path, docker: str = "docker") -> list[str]:
  """Remove every container, whatever its labels, that mounts from under path.

  Harbor starts some containers, such as a separate verifier's, without the
  trial's label, and may leave one when it is stopped or its own cleanup
  fails. Only the containers that `docker container ls` shows with a mount from
  under path are inspected, so that a container of another trial that docker
  cannot inspect in time holds up only its own trial's cleanup.

  Args:
    path: A directory on the host.
    docker: The docker command.

  Returns:
    The containers that still mount from under path.

  Raises:
    DockerError: When `docker container ls` exits non-zero, `docker container
      inspect` says anything but "No such container" or exits non-zero
      without a word, or a docker command runs past DOCKER_TIMEOUT.
  """  # noqa: DOC502
  if found := _mounting(path, docker):
    _ = _docker(docker, "container", "rm", "-f", *found)
  return _mounting(path, docker)


def remove_projects(outdir: Path, docker: str = "docker") -> dict[str, int]:
  """Remove what the compose projects of the trials in outdir hold.

  Harbor 0.24.0 runs each environment of a trial as a compose project named
  after the trial: `<trial>__env` for the agent's, and
  `<trial>__verifier__<step>`, cut and hashed past 63 characters, for a
  separate verifier's, which has no trial label (`harbor/trial/trial.py:1034-
  1043`, `harbor/environments/docker/docker.py:94-107`). Stopped while it stops
  that one, Harbor leaves its sidecar and network, which mount nothing. A
  trial's name is the task's name, at most 32 characters, `__` and 7 random
  letters and digits, so a project of another trial matches only when its
  task's name holds this trial's random part.

  Args:
    outdir: The trial's output directory, whose `job/` holds a directory for
      each trial Harbor started.
    docker: The docker command.

  Returns:
    How many containers, networks and volumes of those projects are left.

  Raises:
    DockerError: When `docker <kind> ls` exits non-zero, or a docker command
      runs past DOCKER_TIMEOUT.
  """  # noqa: DOC502
  trials = [
    path.name for path in (outdir / "job").glob("*/") if not path.is_symlink()
  ]
  exact = {_project(f"{trial}__env") for trial in trials}
  verifiers = tuple(_project(f"{_safe(trial)}__verifier__") for trial in trials)

  def ids(kind: str) -> list[str]:
    key = "Name" if kind == "volume" else "ID"
    out = _docker(
      docker, kind, "ls", *(["-a"] if kind == "container" else []),
      "--filter", f"label={PROJECT}",
      "--format", f'{{{{.{key}}}}}\t{{{{.Label "{PROJECT}"}}}}',
      check=True,
    ).stdout  # fmt: skip
    found: list[str] = []
    for line in out.splitlines():
      name, _, project = line.partition("\t")
      if project in exact or project.startswith(verifiers):
        found.append(name)
    return sorted(found)

  if not trials:
    return {}
  for kind, remove in (
    ("container", ["rm", "-f"]),
    ("network", ["rm"]),
    ("volume", ["rm", "-f"]),
  ):
    if found := ids(kind):
      _ = _docker(docker, kind, *remove, *found)
  return {kind: len(ids(kind)) for kind in ("container", "network", "volume")}


def _project(name: str) -> str:
  # Harbor 0.24.0's compose project of a session
  # (`_sanitize_docker_compose_project_name`).
  name = name.lower()
  if not re.match(r"^[a-z0-9]", name):
    name = f"0{name}"
  return re.sub(r"[^a-z0-9_-]", "-", name)


def _safe(name: str) -> str:
  # The characters Harbor 0.24.0 keeps of a separate verifier's session.
  return "".join(c if c.isalnum() or c in "-._" else "_" for c in name)


_LS = ("-a", "--no-trunc", "--format", "{{.ID}}\t{{.Mounts}}")
_GONE_WHILE_LISTING = ("No such container", "snapshot not found")


def _mounting(path: Path, docker: str) -> list[str]:
  root = str(path.resolve())
  # `ls` asks the daemon for each container's size only when its format uses
  # one (`{{json .}}` does), and that fails for a container removed while it
  # runs; a format of two fields leaves the sizes out.
  listed = _docker(docker, "container", "ls", *_LS)
  if listed.returncode != 0 and any(
    gone in listed.stderr for gone in _GONE_WHILE_LISTING
  ):
    listed = _docker(docker, "container", "ls", *_LS)
  if listed.returncode != 0:
    raise DockerError(f"docker container ls failed: {listed.stderr.strip()}")
  # `ls` joins a container's mount sources with commas, a source that has
  # one included, so this keeps every container with a source under root, and
  # perhaps a few more, which inspect then tells apart.
  under = re.compile(f"(?:^|,){re.escape(root)}(?:/|,|$)")
  ids: list[str] = []
  for line in listed.stdout.splitlines():
    container, _, mounts = line.partition("\t")
    if container.strip() and under.search(mounts):
      ids.append(container.strip())
  if not ids:
    return []
  inspected = _docker(docker, "container", "inspect", *ids)
  # A container may go between the two commands; any other error could hide
  # one.
  errors = [
    line
    for line in inspected.stderr.splitlines()
    if line.strip() and "No such container" not in line
  ]
  if errors or (inspected.returncode != 0 and not inspected.stderr.strip()):
    raise DockerError(f"docker container inspect failed: {errors}")
  containers = cast(
    list[Mapping[str, object]], json.loads(inspected.stdout or "[]")
  )
  return sorted(
    str(container["Id"])
    for container in containers
    if any(
      Path(str(mount.get("Source", ""))).is_relative_to(root)
      for mount in cast(
        list[Mapping[str, object]], container.get("Mounts") or []
      )
    )
  )


def _docker(
  docker: str, *args: str, timeout: float | None = None, check: bool = False
) -> subprocess.CompletedProcess[str]:
  """Run docker.

  Args:
    docker: The docker command.
    *args: Its arguments.
    timeout: How long it may run, in seconds; DOCKER_TIMEOUT when None; at
      most what is left of the budget of a stopped `fan_out`.
    check: Whether a status other than 0 is an error.

  Returns:
    What it did.

  Raises:
    DockerError: When it ran past timeout, or, with check, exited non-zero.
  """
  timeout = stopping.left(DOCKER_TIMEOUT if timeout is None else timeout)
  try:
    done = subprocess.run(
      [docker, *args],
      env=_passed(),
      capture_output=True,
      text=True,
      timeout=timeout,
      check=False,
    )
  except subprocess.TimeoutExpired as e:
    raise DockerError(
      f"docker {args[0]} {args[1]} ran past {timeout:g} s"
    ) from e
  if check and done.returncode != 0:
    error = done.stderr.strip()
    raise DockerError(
      f"docker {args[0]} {args[1]} exited {done.returncode}: {error}"
    )
  return done


def _passed() -> dict[str, str]:
  return {key: os.environ[key] for key in PASSED if key in os.environ}


def _task_main(task_dir: Path, docker: str) -> Mapping[str, object]:
  """Return the `main` service of the task's own compose file.

  Args:
    task_dir: The task's directory.
    docker: The docker command.

  Returns:
    The service; empty when the task has no compose file or no `main`.
  """
  path = task_dir / "environment" / "docker-compose.yaml"
  if not path.exists():
    return {}
  out = subprocess.run(
    [
      docker, "compose", "--file", str(path.resolve()),
      "config", "--format", "json",
      "--no-interpolate", "--no-normalize",
      "--no-path-resolution", "--no-consistency",
    ],
    env=_passed(),
    capture_output=True,
    text=True,
    timeout=stopping.left(120),
    check=True,
  ).stdout  # fmt: skip
  config = cast(Mapping[str, object], json.loads(out))
  services = cast(Mapping[str, object], config.get("services") or {})
  return cast(Mapping[str, object], services.get("main") or {})


def _has_sidecar(task_dir: Path) -> bool:
  with (task_dir / "task.toml").open("rb") as f:
    config = tomllib.load(f)

  def mode(table: object, name: str) -> object:
    return (
      cast(Mapping[str, Mapping[str, object]], table)
      .get(name, {})
      .get("network_mode")
    )

  # A step's phase takes the task's network_mode when it sets none of its own.
  steps = cast(list[object], config.get("steps") or [{}])
  modes = [mode(config, "environment")]
  modes += [mode(step, p) or mode(config, p) for step in steps for p in PHASES]
  return any(m not in (None, "public") for m in modes)


def _agent_args(agent: TrialAgent) -> list[str]:
  if isinstance(agent, str):
    return ["--agent", agent]
  unknown = set(agent) - {"name", "import_path", "model", "kwargs"}
  if unknown or ("name" in agent) == ("import_path" in agent):
    allowed = "one of name and import_path, and optionally model and kwargs"
    raise ValueError(f"an agent takes {allowed}, not {sorted(agent)}")
  args = (
    ["--agent", str(agent["name"])]
    if "name" in agent
    else ["--agent-import-path", str(agent["import_path"])]
  )
  if "model" in agent:
    args += ["--model", str(agent["model"])]
  kwargs = cast(Mapping[str, object], agent.get("kwargs", {}))
  for key, value in kwargs.items():
    args += ["--agent-kwarg", f"{key}={value}"]
  return args


def _stop(process: subprocess.Popen[bytes]) -> None:
  # Harbor's children (docker compose) are in its session; stop them all.
  grace = min(STOP_S, STOPPED_S) if stopping.stopped() else STOP_S
  for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, None)):
    try:
      os.killpg(process.pid, sig)
    except ProcessLookupError:
      break
    try:
      _ = process.wait(wait)
      break
    except subprocess.TimeoutExpired:
      continue
    except BaseException:
      # An interrupt in the grace period (a second Ctrl-C, say) kills them
      # before it goes on.
      with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
      _ = process.wait()
      raise
  # What Harbor started after the SIGTERM never got it and can outlive Harbor.
  # While any of them runs, the group keeps its ID, so this reaches only them.
  with suppress(ProcessLookupError):
    os.killpg(process.pid, signal.SIGKILL)


def _started(outdir: Path) -> bool:
  # Harbor 0.24.0 writes a trial's config.json first when it runs the trial,
  # after it has made the trial's environments and before any container of the
  # task starts (`harbor/trial/trial.py:492-493`, `1066-1069`).
  job = outdir / "job"
  return any(job.glob("*/config.json")) or any(job.glob("*/result.json"))


def _tail(log: Path, env: Iterable[str]) -> str:
  """Return the end of a harbor.log, with the values of env hidden.

  Args:
    log: The file.
    env: The values to hide; each line of one that has HIDE_MIN characters or
      more becomes `***`.

  Returns:
    Its last LOG_LINES lines, of its last LOG_BYTES bytes at most, less the
    line the first of those bytes cut.
  """
  try:
    with log.open("rb") as f:
      start = max(0, f.seek(0, os.SEEK_END) - LOG_BYTES)
      _ = f.seek(start)
      lines = f.read().decode(errors="replace").splitlines()
  except FileNotFoundError:
    return "(no harbor.log)"
  text = "\n".join(lines[1 if start else 0 :][-LOG_LINES:])
  hidden = {
    line
    for value in env
    for line in value.splitlines()
    if len(line) >= HIDE_MIN
  }
  for value in sorted(hidden, key=len, reverse=True):
    text = text.replace(value, "***")
  return text


def _read(
  outdir: Path, log: Path, error: str | None, elapsed: float
) -> TrialResult:
  results = sorted((outdir / "job").glob("*/result.json"))
  if not results:
    reason = error or f"HarborError: no trial result; see {log}"
    return TrialResult({}, reason, 0.0, elapsed, {"log": log})
  trial = results[0].parent
  result = cast(dict[str, object], json.loads(results[0].read_text()))
  verifier = cast(dict[str, object], result.get("verifier_result") or {})
  rewards = cast(dict[str, float], verifier.get("rewards") or {})
  info = cast(dict[str, object], result.get("exception_info") or {})
  agent = cast(dict[str, object], result.get("agent_result") or {})
  exception = error
  if info:
    recorded = f"{info.get('exception_type')}: {info.get('exception_message')}"
    # Harbor records the cancellation that stopping it caused.
    timed_out = error is not None and error.startswith(f"{TIMED_OUT}:")
    exception = (
      f"{error}; Harbor recorded {recorded}" if timed_out else recorded
    )
  elif exception is None and not rewards:
    exception = "NoReward: the verifier gave no reward"
  seconds = elapsed
  started, finished = result.get("started_at"), result.get("finished_at")
  if isinstance(started, str) and isinstance(finished, str):
    delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    seconds = delta.total_seconds()
  artifacts = {"log": log, "trial": trial}
  for name in ("agent", "verifier"):
    if (trial / name).is_dir(follow_symlinks=False):
      artifacts[name] = trial / name
  # The agent's container writes agent/, and a symlink there could point
  # the record or the cost at a file of the host.
  traced = next(
    (
      (kind, trial / kind.path)
      for kind in TRACES
      if (trial / kind.path).is_file(follow_symlinks=False)
    ),
    None,
  )
  cost = agent.get("cost_usd")
  if isinstance(cost, bool) or not isinstance(cost, int | float):
    cost = None
    if traced and traced[1].stat(follow_symlinks=False).st_size <= TRACE_LIMIT:
      cost = traced[0].cost(traced[1])
  return TrialResult(
    {key: float(value) for key, value in rewards.items()},
    exception,
    0.0 if cost is None else float(cost),
    seconds,
    artifacts,
    id=trial.name,
    path=trial,
    trace=None if traced is None else traced[1],
    trace_format=None if traced is None else traced[0].format,
  )
