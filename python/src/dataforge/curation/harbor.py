"""Run a trial with Harbor: start the task's environment, run an agent in it, run the verifier.

HarborRunner drives the `harbor` command line, which the dataset installs; dataforge does not
depend on Harbor. Harbor runs with an empty environment plus a short list of variables, so
that nothing of this process's environment (a token, a proxy's session) reaches it or its
containers unless the dataset passes it in `env`.

A compose overlay of ours, passed to Harbor with `--extra-docker-compose`, caps the task's
container: every capability dropped, `no-new-privileges`, a process limit, the trial's CPUs and
memory, a CPU weight, and a label that every container, network and image of the trial
carries. It leaves the container's network to `task.toml`: Harbor routes the container through
its egress sidecar only when no compose file names the container's network. When `task.toml`
declares a network mode other than `public`, for the environment or for the agent's or the
verifier's phase (a `[[steps]]` entry's phase overriding the task's), Harbor starts that
sidecar, and the overlay labels it and caps its CPUs, memory and processes (`Sidecar`).
Harbor reads its own compose file for the sidecar after ours, and that file sets none of these.
A task directory's own compose file, which Harbor reads before ours, can take the same limits
from DATAFORGE_SIDECAR_CPUS and DATAFORGE_SIDECAR_MEMORY_MB in Harbor's environment. After the
run, whatever happened, the trial's files are given back to this user and everything with the
label is removed.

A trial reserves from the pool the task's memory plus the sidecar's limit (`memory_gb`),
whether or not the task has a sidecar; image builds and the cleanup's container use memory
besides, so a dataset leaves the pool some headroom.

A dataset connects its own host side (a model gateway, say) through `mounts`, `env`, extra
compose files in `compose_overlay`, and `pre_start`, called before Harbor starts.
"""

import json
import os
import signal
import subprocess
import time
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

from dataforge.curation.actions import Limits, Mount, TrialAgent, TrialResult

LABEL = "dataforge.trial"
CPUS = 2.0
CPU_SHARES = 1024
PIDS = 512
# A bound on one trial past the timeouts Harbor enforces itself (the agent's and the
# verifier's in task.toml), for a Harbor that hangs.
TIMEOUT = 3600.0
SIDECAR = "harbor-docker-egress-control-sidecar"
SIDECAR_PIDS = 64
# The phases whose network_mode, besides the environment's, can make Harbor start the sidecar.
PHASES = ("agent", "verifier")
# The variables that give a task directory's compose file the sidecar's limits; compose reads
# them from Harbor's environment.
SIDECAR_CPUS_VAR = "DATAFORGE_SIDECAR_CPUS"
SIDECAR_MEMORY_VAR = "DATAFORGE_SIDECAR_MEMORY_MB"
# The task's container runs as root without CAP_CHOWN, so Harbor cannot give the files it
# writes back to this user; a container of this image does.
CHOWN_IMAGE = "ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517"
# The variables of this process that Harbor and docker get; everything else is dropped.
PASSED = ("HOME", "PATH", "DOCKER_HOST", "DOCKER_CONFIG")


@dataclass(frozen=True)
class Sidecar:
    """The limits of Harbor's egress sidecar: its CPUs and its memory in MiB.

    The defaults are about three times the most a sidecar took in nop and oracle trials with
    egress denied: 22 MiB of memory, 23 processes and 0.17 of a CPU, as a dataset repo
    measured them with its own Harbor wrapper on 2026-10-19; they were not measured again here.
    """

    cpus: float = 0.5
    memory_mb: int = 64

    @property
    def memory_gb(self) -> float:
        return self.memory_mb / 1024


@dataclass(frozen=True)
class HarborRunner:
    """A TrialRunner that runs each trial as a Harbor job of one attempt.

    agent is "nop", "oracle", or a mapping with "name" (a Harbor agent) or "import_path",
    and optionally "model" and "kwargs" (Harbor's --agent-kwarg). The task's container gets
    limits.memory_gb and limits.cpus (cpus when the limits give none); the egress sidecar,
    when there is one, gets sidecar's limits and SIDECAR_PIDS processes.

    mounts and env go to the task's container; env also goes to Harbor, whose agents read
    some variables from their own environment. The overlay names env's variables without
    their values. compose_overlay files come after ours, so they can override it; one that
    names the container's network (`network_mode: none`, say) takes it off Harbor's egress
    sidecar. pre_start is called with the trial's outdir before Harbor starts. docker is the
    docker command the cleanup runs; when docker fails during the cleanup, run raises, since
    it cannot tell what is left.
    """

    harbor: Path
    cpus: float = CPUS
    cpu_shares: int = CPU_SHARES
    pids: int = PIDS
    sidecar: Sidecar = Sidecar()
    timeout: float = TIMEOUT
    mounts: Sequence[Mount] = ()
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    compose_overlay: Sequence[Path] = ()
    pre_start: Callable[[Path], None] | None = None
    docker: str = "docker"

    def memory_gb(self, limits: Limits) -> float:
        """The memory a trial reserves: the task's and the sidecar's."""
        return limits.memory_gb + self.sidecar.memory_gb

    def run(self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path) -> TrialResult:
        egress = _has_sidecar(task_dir)
        agent_args = _agent_args(agent)
        label = uuid.uuid4().hex
        overlay = outdir / "overlay.json"
        _ = overlay.write_text(json.dumps(self.overlay(egress, limits, label), indent=2))
        args = [
            str(self.harbor), "run",
            "--path", str(task_dir.resolve()),
            *agent_args,
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
            if self.pre_start is not None:
                self.pre_start(outdir)
            error = self._harbor(args, log)
        finally:
            try:
                self._chown(outdir, label)
            finally:
                left = cleanup(f"{LABEL}={label}", self.docker)
        if any(left.values()):
            raise RuntimeError(f"left after the cleanup of {LABEL}={label}: {left}")
        return _read(outdir, log, error, time.monotonic() - started)

    def overlay(self, egress: bool, limits: Limits, label: str) -> object:
        """The compose overlay of one trial, as compose reads it from JSON.

        egress says whether Harbor starts its egress sidecar for the task.
        """
        labels = {LABEL: label}
        main: dict[str, object] = {
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
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
            # Names only: compose takes the values from Harbor's environment, which has env,
            # so no value is written to the overlay in outdir.
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

    def _harbor(self, args: Sequence[str], log: Path) -> str | None:
        """Run Harbor; return why it failed, or None when it exited 0."""
        env = {
            **_passed(),
            "HARBOR_TELEMETRY": "0",
            **self.env,
            SIDECAR_CPUS_VAR: str(self.sidecar.cpus),
            SIDECAR_MEMORY_VAR: str(self.sidecar.memory_mb),
        }
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
                code = process.wait(self.timeout)
            except subprocess.TimeoutExpired:
                _stop(process)
                return f"TimeoutError: Harbor ran past {self.timeout:g} s"
        return None if code == 0 else f"HarborError: harbor exited {code}; see {log}"

    def _chown(self, outdir: Path, label: str) -> None:
        _ = subprocess.run(
            [
                self.docker, "run", "--rm",
                f"--label={LABEL}={label}",
                "--network=none",
                "--cap-drop=ALL", "--cap-add=CHOWN", "--cap-add=DAC_READ_SEARCH",
                "--security-opt=no-new-privileges",
                "--cpus=1", "--memory=256m", "--pids-limit=64",
                f"--volume={outdir.resolve()}:/trial",
                CHOWN_IMAGE,
                "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/trial",
            ],
            env=_passed(),
            capture_output=True,
            timeout=300,
            check=True,
        )  # fmt: skip


def cleanup(selector: str, docker: str = "docker") -> dict[str, int]:
    """Remove the containers, images, networks and volumes a label selects; count what is left.

    selector is a docker label filter, such as "dataforge.trial=<id>".
    """

    def ids(kind: str) -> list[str]:
        # Without -a, `image ls` leaves out labelled intermediate images.
        extra = ["-a"] if kind in ("container", "image") else []
        out = subprocess.run(
            [docker, kind, "ls", "-q", *extra, "--filter", f"label={selector}"],
            env=_passed(),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        ).stdout
        return sorted(set(out.split()))

    # An image whose child is still there is not removed, so images take as many rounds as
    # the deepest chain of them.
    for kind, remove, rounds in (
        ("container", ["rm", "-f"], 1),
        ("image", ["rmi", "-f"], 20),
        ("network", ["rm"], 1),
        ("volume", ["rm", "-f"], 1),
    ):
        for _ in range(rounds):
            if not (found := ids(kind)):
                break
            _ = subprocess.run(
                [docker, kind, *remove, *found],
                env=_passed(),
                capture_output=True,
                timeout=120,
                check=False,
            )
    return {kind: len(ids(kind)) for kind in ("container", "image", "network", "volume")}


def _passed() -> dict[str, str]:
    return {key: os.environ[key] for key in PASSED if key in os.environ}


def _has_sidecar(task_dir: Path) -> bool:
    with (task_dir / "task.toml").open("rb") as f:
        config = tomllib.load(f)

    def mode(table: object, name: str) -> object:
        return cast(Mapping[str, Mapping[str, object]], table).get(name, {}).get("network_mode")

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
    for sig, wait in ((signal.SIGTERM, 30.0), (signal.SIGKILL, None)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            _ = process.wait(wait)
            break
        except subprocess.TimeoutExpired:
            continue


def _read(outdir: Path, log: Path, error: str | None, elapsed: float) -> TrialResult:
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
        exception = f"{info.get('exception_type')}: {info.get('exception_message')}"
    elif exception is None and not rewards:
        exception = "NoReward: the verifier gave no reward"
    seconds = elapsed
    started, finished = result.get("started_at"), result.get("finished_at")
    if isinstance(started, str) and isinstance(finished, str):
        delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        seconds = delta.total_seconds()
    artifacts = {"log": log, "trial": trial}
    for name in ("agent", "verifier"):
        if (trial / name).is_dir():
            artifacts[name] = trial / name
    cost = agent.get("cost_usd")
    return TrialResult(
        {key: float(value) for key, value in rewards.items()},
        exception,
        float(cost) if isinstance(cost, int | float) else 0.0,
        seconds,
        artifacts,
    )
