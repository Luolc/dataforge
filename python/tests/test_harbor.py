# pyright: reportAny=false
# The tests read the JSON the fakes and the runner write.

"""HarborRunner against a fake `harbor` and a fake `docker`.

The fakes are written here as scripts.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import cast

import pytest

from dataforge.curation.actions import Limits, Mount, TrialAgent
from dataforge.curation.harbor import (
  HarborRunner,
  LABEL,
  SIDECAR,
  Sidecar,
  TrialSetup,
)

# The fake harbor writes what it got to <jobs-dir>/fake.json and then does what
# FAKE_MODE says: "ok", "exception", "no-reward", "exit" (exit 3 with no result)
# or "hang" (start a child, write its own and the child's pid, sleep). The
# agent's cost is FAKE_COST, 0.25 by default and null for "none"; with
# FAKE_TRACE, the agent directory gets a Claude Code trace whose last result
# event costs that much.
FAKE_HARBOR = """\
import json, os, pathlib, subprocess, sys, time
argv = sys.argv[1:]
jobs = pathlib.Path(argv[argv.index("--jobs-dir") + 1])
trial = jobs / argv[argv.index("--job-name") + 1] / "task__abc"
(jobs / "fake.json").write_text(
    json.dumps({"argv": argv, "env": dict(os.environ)})
)
mode = os.environ["FAKE_MODE"]
if mode == "exit":
    sys.exit(3)
if mode == "hang":
    child = subprocess.Popen(["sleep", "60"])
    (jobs / "pids").write_text(f"{os.getpid()} {child.pid}")
    time.sleep(60)
(trial / "agent").mkdir(parents=True)
result = {
    "started_at": "2026-10-19T10:00:00+00:00",
    "finished_at": "2026-10-19T10:01:30+00:00",
    "agent_result": {"cost_usd": 0.25},
    "verifier_result": {"rewards": {"reward": 1}},
    "exception_info": None,
}
if mode == "exception":
    result["verifier_result"] = None
    result["exception_info"] = {"exception_type": "AgentTimeoutError",
                                "exception_message": "agent ran past 600 s"}
if mode == "no-reward":
    result["verifier_result"] = None
cost = os.environ.get("FAKE_COST", "0.25")
result["agent_result"] = {"cost_usd": None if cost == "none" else float(cost)}
if "FAKE_TRACE" in os.environ:
    events = [
        "not json",
        {"type": "result", "subtype": "success", "total_cost_usd": 0.01},
        {"type": "assistant", "message": {"content": []}},
        {"type": "result", "subtype": "success",
         "total_cost_usd": float(os.environ["FAKE_TRACE"])},
    ]
    (trial / "agent" / "claude-code.txt").write_text("\\n".join(
        e if isinstance(e, str) else json.dumps(e) for e in events
    ))
(trial / "result.json").write_text(json.dumps(result))
"""

# The fake docker appends its argv to calls.jsonl next to it. `<kind> ls` prints
# the ids in <kind>.ids; `rm` and `rmi` remove them unless a file "stuck" is
# there. `compose` goes to the real docker on PATH, which only reads compose
# files here.
FAKE_DOCKER = """\
import json, os, pathlib, sys
here = pathlib.Path(__file__).parent
argv = sys.argv[1:]
with (here / "calls.jsonl").open("a") as f:
    f.write(json.dumps(argv) + "\\n")
if argv[0] == "compose":
    os.execvp("docker", ["docker", *argv])
if argv[0] in ("container", "image", "network", "volume"):
    ids = here / f"{argv[0]}.ids"
    if argv[1] == "ls":
        print(ids.read_text() if ids.exists() else "")
    elif not (here / "stuck").exists():
        ids.unlink(missing_ok=True)
"""


def script(path: Path, body: str) -> Path:
  _ = path.write_text(f"#!{sys.executable}\n{body}")
  path.chmod(0o755)
  return path


@pytest.fixture
def fakes(tmp_path: Path) -> tuple[Path, Path]:
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  return script(bin_dir / "harbor", FAKE_HARBOR), script(
    bin_dir / "docker", FAKE_DOCKER
  )


def task(
  tmp_path: Path,
  network_mode: str = "public",
  verifier: str | None = None,
  steps: str = "",
) -> Path:
  task_dir = tmp_path / "task"
  task_dir.mkdir(exist_ok=True)
  toml = f'[environment]\nnetwork_mode = "{network_mode}"\nmemory_mb = 2048\n'
  if verifier is not None:
    toml += f'[verifier]\nnetwork_mode = "{verifier}"\n'
  toml += steps
  _ = (task_dir / "task.toml").write_text(toml)
  return task_dir


def outdir(tmp_path: Path, name: str = "trial") -> Path:
  path = tmp_path / "attempt" / name
  path.mkdir(parents=True)
  return path


def runner(
  fakes: tuple[Path, Path], mode: str = "ok", env: dict[str, str] | None = None
) -> HarborRunner:
  harbor, docker = fakes
  return HarborRunner(
    harbor, docker=str(docker), env={"FAKE_MODE": mode, **(env or {})}
  )


def seen_argv(out: Path) -> list[str]:
  return cast(list[str], json.loads((out / "fake.json").read_text())["argv"])


def seen_env(out: Path) -> dict[str, str]:
  return cast(
    dict[str, str], json.loads((out / "fake.json").read_text())["env"]
  )


def docker_calls(fakes: tuple[Path, Path]) -> list[list[str]]:
  lines = (fakes[1].parent / "calls.jsonl").read_text().splitlines()
  return [cast(list[str], json.loads(line)) for line in lines]


def test_a_solved_trial_gives_its_rewards_cost_and_time(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  result = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)
  assert result.exception is None
  assert result.rewards == {"reward": 1.0}
  assert result.cost_usd == 0.25
  assert result.seconds == 90.0
  assert result.artifacts["trial"] == out / "job" / "task__abc"
  assert result.artifacts["agent"] == out / "job" / "task__abc" / "agent"
  assert "verifier" not in result.artifacts


@pytest.mark.parametrize(
  ("env", "cost"),
  [
    ({"FAKE_COST": "0.25", "FAKE_TRACE": "0.75"}, 0.25),
    ({"FAKE_COST": "none", "FAKE_TRACE": "0.75"}, 0.75),
    ({"FAKE_COST": "none"}, 0.0),
  ],
)
def test_a_trials_cost_falls_back_to_the_claude_code_trace(
  tmp_path: Path, fakes: tuple[Path, Path], env: dict[str, str], cost: float
) -> None:
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.cost_usd == cost


@pytest.mark.parametrize(
  ("mode", "exception"),
  [
    ("exception", "AgentTimeoutError: agent ran past 600 s"),
    ("no-reward", "NoReward: the verifier gave no reward"),
    ("exit", "HarborError: harbor exited 3"),
  ],
)
def test_a_failed_trial_returns_its_exception(
  tmp_path: Path, fakes: tuple[Path, Path], mode: str, exception: str
) -> None:
  result = runner(fakes, mode).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.exception is not None
  assert result.exception.startswith(exception)
  assert result.rewards == {}


def test_harbor_gets_only_the_passed_variables_and_env(
  tmp_path: Path, fakes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("SOME_TOKEN", "not for harbor")
  out = outdir(tmp_path)
  _ = runner(fakes, env={"MODEL_URL": "http://gateway.example.test"}).run(
    task(tmp_path), "nop", Limits(2), out
  )
  env = seen_env(out)
  assert "SOME_TOKEN" not in env
  assert env["MODEL_URL"] == "http://gateway.example.test"
  assert env["HARBOR_TELEMETRY"] == "0"
  assert env["PATH"] == os.environ["PATH"]


def test_harbor_gets_the_agent_and_the_overlays_in_order(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  extra = tmp_path / "gateway.yaml"
  _ = extra.write_text("services: {}\n")
  out = outdir(tmp_path)
  agent = {
    "name": "claude-code",
    "model": "some-model",
    "kwargs": {"max_budget_usd": 2},
  }
  _ = replace(runner(fakes), compose_overlay=[extra]).run(
    task(tmp_path), agent, Limits(2), out
  )
  argv = seen_argv(out)
  assert argv[:3] == ["run", "--path", str((tmp_path / "task").resolve())]
  for pair in (
    ["--agent", "claude-code"],
    ["--model", "some-model"],
    ["--agent-kwarg", "max_budget_usd=2"],
    ["--n-attempts", "1"],
  ):
    assert any(argv[i : i + 2] == pair for i in range(len(argv))), pair
  overlays = [
    argv[i + 1] for i, arg in enumerate(argv) if arg == "--extra-docker-compose"
  ]
  assert overlays == [
    str((out / "overlay.json").resolve()),
    str(extra.resolve()),
  ]


def test_an_agent_by_import_path(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  agent = {"import_path": "my_dataset.agents:Mine"}
  _ = runner(fakes).run(task(tmp_path), agent, Limits(2), out)
  argv = seen_argv(out)
  assert "--agent" not in argv
  assert argv[argv.index("--agent-import-path") + 1] == "my_dataset.agents:Mine"


def test_the_overlay_caps_the_task_container(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  mount = Mount(tmp_path / "socket", "/run/gateway.sock", read_only=False)
  run = replace(
    runner(fakes, env={"MODEL_URL": "http://gateway.example.test"}),
    mounts=[mount],
  )
  _ = run.run(task(tmp_path), "nop", Limits(1.5, cpus=3), out)
  overlay = json.loads((out / "overlay.json").read_text())
  main = overlay["services"]["main"]
  assert "network_mode" not in main
  assert "networks" not in main
  assert main["cap_drop"] == ["ALL"]
  assert main["security_opt"] == ["no-new-privileges:true"]
  assert main["mem_limit"] == "1536m"
  assert main["cpus"] == 3
  assert main["pids_limit"] == 512
  assert main["volumes"] == [
    f"{(tmp_path / 'socket').resolve()}:/run/gateway.sock"
  ]
  assert main["environment"] == ["FAKE_MODE", "MODEL_URL"]
  assert "http://gateway.example.test" not in (out / "overlay.json").read_text()
  label = main["labels"][LABEL]
  assert main["build"]["labels"] == {LABEL: label}
  assert overlay["networks"]["default"]["labels"] == {LABEL: label}
  assert SIDECAR not in overlay["services"]


def test_the_overlay_takes_the_defaults_and_caps_and_labels_a_sidecar(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  _ = runner(fakes).run(task(tmp_path, "no-network"), "nop", Limits(2), out)
  overlay = json.loads((out / "overlay.json").read_text())
  assert overlay["services"]["main"]["cpus"] == 2.0
  label = overlay["services"]["main"]["labels"][LABEL]
  assert overlay["services"][SIDECAR] == {
    "cpus": 0.5,
    "mem_limit": "64m",
    "pids_limit": 64,
    "labels": {LABEL: label},
  }
  env = seen_env(out)
  assert env["DATAFORGE_SIDECAR_CPUS"] == "0.5"
  assert env["DATAFORGE_SIDECAR_MEMORY_MB"] == "64"


def test_the_sidecar_takes_the_limits_it_is_given(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  run = replace(runner(fakes), sidecar=Sidecar(cpus=1.0, memory_mb=128))
  _ = run.run(task(tmp_path, "no-network"), "nop", Limits(2), out)
  sidecar = json.loads((out / "overlay.json").read_text())["services"][SIDECAR]
  assert (sidecar["cpus"], sidecar["mem_limit"]) == (1.0, "128m")
  env = seen_env(out)
  assert env["DATAFORGE_SIDECAR_CPUS"] == "1.0"
  assert env["DATAFORGE_SIDECAR_MEMORY_MB"] == "128"


def test_a_phase_without_public_network_caps_and_labels_a_sidecar(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  _ = runner(fakes).run(
    task(tmp_path, verifier="no-network"), "nop", Limits(2), out
  )
  sidecar = json.loads((out / "overlay.json").read_text())["services"][SIDECAR]
  assert sidecar["mem_limit"] == "64m"
  assert LABEL in sidecar["labels"]


STEPS = """
[[steps]]
name = "one"
[[steps]]
name = "two"
[steps.agent]
network_mode = "no-network"
"""


def test_a_step_without_public_network_caps_and_labels_a_sidecar(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  _ = runner(fakes).run(task(tmp_path, steps=STEPS), "nop", Limits(2), out)
  sidecar = json.loads((out / "overlay.json").read_text())["services"][SIDECAR]
  assert sidecar["mem_limit"] == "64m"
  assert LABEL in sidecar["labels"]


def test_steps_that_set_a_public_phase_override_the_tasks(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  steps = (
    '\n[[steps]]\nname = "one"\n[steps.verifier]\nnetwork_mode = "public"\n'
  )
  _ = runner(fakes).run(
    task(tmp_path, verifier="no-network", steps=steps), "nop", Limits(2), out
  )
  assert (
    SIDECAR not in json.loads((out / "overlay.json").read_text())["services"]
  )


def test_a_trial_reserves_the_tasks_memory_and_the_sidecars(
  fakes: tuple[Path, Path],
) -> None:
  assert runner(fakes).memory_gb(Limits(2)) == 2 + 64 / 1024
  bigger = replace(runner(fakes), sidecar=Sidecar(memory_mb=256))
  assert bigger.memory_gb(Limits(2)) == 2.25


class Setup:
  """A setup that records what it saw on entering and leaving.

  It also adds to the trial.

  Args:
    fakes: The fake harbor and docker.
    tmp_path: The test's directory.
  """

  def __init__(self, fakes: tuple[Path, Path], tmp_path: Path) -> None:
    self.fakes: tuple[Path, Path] = fakes
    self.socket: Path = tmp_path / "gateway"
    self.overlay: Path = tmp_path / "extra.json"
    _ = self.overlay.write_text("{}")
    self.calls: list[tuple[str, Path, bool, int]] = []
    self.args: list[tuple[Path, TrialAgent, Limits]] = []

  @contextmanager
  def __call__(
    self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
  ) -> Generator[TrialSetup]:
    self.args.append((task_dir, agent, limits))
    self.calls.append(self._seen("enter", outdir))
    try:
      yield TrialSetup(
        mounts=[Mount(self.socket, "/run/gateway", read_only=False)],
        env={"MODEL_URL": "http://gateway.example.test"},
        compose_overlay=[self.overlay],
      )
    finally:
      self.calls.append(self._seen("exit", outdir))

  def _seen(self, what: str, outdir: Path) -> tuple[str, Path, bool, int]:
    docker = (
      len(docker_calls(self.fakes))
      if (self.fakes[1].parent / "calls.jsonl").exists()
      else 0
    )
    return what, outdir, (outdir / "fake.json").exists(), docker


@pytest.mark.parametrize("mode", ["ok", "exit"])
def test_the_setup_spans_harbor_and_the_cleanup(
  tmp_path: Path, fakes: tuple[Path, Path], mode: str
) -> None:
  out = outdir(tmp_path)
  setup = Setup(fakes, tmp_path)
  _ = replace(runner(fakes, mode), setup=setup).run(
    task(tmp_path), "nop", Limits(2), out
  )
  assert setup.calls == [
    ("enter", out, False, 0),
    ("exit", out, True, len(docker_calls(fakes))),
  ]


def test_what_the_setup_yields_reaches_the_trial(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  setup = Setup(fakes, tmp_path)
  run = replace(
    runner(fakes),
    mounts=[Mount(tmp_path / "cache", "/cache")],
    compose_overlay=[tmp_path / "own.json"],
    setup=setup,
  )
  agent = {"name": "claude-code"}
  _ = run.run(task(tmp_path), agent, Limits(2), out)
  assert setup.args == [(tmp_path / "task", agent, Limits(2))]
  main = json.loads((out / "overlay.json").read_text())["services"]["main"]
  assert main["volumes"] == [
    f"{(tmp_path / 'cache').resolve()}:/cache:ro",
    f"{setup.socket.resolve()}:/run/gateway",
  ]
  assert main["environment"] == ["FAKE_MODE", "MODEL_URL"]
  assert seen_env(out)["MODEL_URL"] == "http://gateway.example.test"
  argv = seen_argv(out)
  overlays = [
    argv[i + 1] for i, arg in enumerate(argv) if arg == "--extra-docker-compose"
  ]
  assert overlays[1:] == [
    str((tmp_path / "own.json").resolve()),
    str(setup.overlay.resolve()),
  ]


def test_the_setup_is_left_when_the_cleanup_fails(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  _ = (fakes[1].parent / "network.ids").write_text("n1")
  _ = (fakes[1].parent / "stuck").write_text("")
  setup = Setup(fakes, tmp_path)
  with pytest.raises(RuntimeError, match="'network': 1"):
    _ = replace(runner(fakes), setup=setup).run(
      task(tmp_path), "nop", Limits(2), outdir(tmp_path)
    )
  assert [call[0] for call in setup.calls] == ["enter", "exit"]


def test_every_trial_has_its_own_label(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  labels: list[str] = []
  for name in ("a", "b"):
    out = outdir(tmp_path, name)
    _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), out)
    overlay = json.loads((out / "overlay.json").read_text())
    labels.append(overlay["services"]["main"]["labels"][LABEL])
  assert labels[0] != labels[1]


def test_the_cleanup_chowns_and_removes_what_has_the_label(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  _ = (fakes[1].parent / "container.ids").write_text("c1 c2")
  out = outdir(tmp_path)
  _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), out)
  label = json.loads((out / "overlay.json").read_text())["services"]["main"][
    "labels"
  ][LABEL]
  calls = docker_calls(fakes)
  assert calls[0][:3] == ["run", "--rm", f"--label={LABEL}={label}"]
  assert f"--volume={out.resolve()}:/trial" in calls[0]
  assert ["container", "rm", "-f", "c1", "c2"] in calls
  assert all(
    f"label={LABEL}={label}" in call for call in calls[1:] if "ls" in call
  )


def test_something_left_after_the_cleanup_is_an_error(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  _ = (fakes[1].parent / "network.ids").write_text("n1")
  _ = (fakes[1].parent / "stuck").write_text("")
  with pytest.raises(RuntimeError, match="'network': 1"):
    _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), outdir(tmp_path))


def test_a_hung_harbor_is_stopped_with_its_children(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  started = time.monotonic()
  try:
    result = replace(runner(fakes, "hang"), timeout=1.0).run(
      task(tmp_path), "nop", Limits(2), out
    )
    assert time.monotonic() - started < 30
    assert result.exception == "TimeoutError: Harbor ran past 1 s"
    for pid in alive(out):
      pytest.fail(f"process {pid} of the fake harbor is still running")
  finally:
    for pid in alive(out):
      os.kill(pid, signal.SIGKILL)


def alive(out: Path) -> list[int]:
  """Return the processes of the hung fake harbor that still run.

  Args:
    out: The trial's directory.

  Returns:
    Their IDs.
  """
  pids = [int(pid) for pid in (out / "pids").read_text().split()]
  running: list[int] = []
  for pid in pids:
    try:
      os.kill(pid, 0)
    except ProcessLookupError:
      continue
    running.append(pid)
  return running


@pytest.mark.parametrize(
  "lists",
  [
    "",
    "    cap_drop: [ALL]\n",
    '    security_opt: ["no-new-privileges:true"]\n',
    "    cap_drop: [ALL, NET_RAW]\n"
    + '    security_opt: [label=disable, "no-new-privileges"]\n',
  ],
)
def test_the_merged_main_has_each_cap_once_with_the_tasks_compose_file(
  tmp_path: Path, fakes: tuple[Path, Path], lists: str
) -> None:
  compose = tmp_path / "task" / "environment" / "docker-compose.yaml"
  compose.parent.mkdir(parents=True)
  _ = compose.write_text(
    f"services:\n  main:\n    image: ubuntu:24.04\n{lists}"
  )
  out = outdir(tmp_path)
  _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), out)
  merged = subprocess.run(
    ["docker", "compose", "--file", str(compose),
     "--file", str(out / "overlay.json"),
     "config", "--format", "json"],
    capture_output=True,
    text=True,
    check=False,
  )  # fmt: skip
  assert merged.returncode == 0, merged.stderr
  main = json.loads(merged.stdout)["services"]["main"]
  assert main["cap_drop"].count("ALL") == 1
  assert main["security_opt"].count("no-new-privileges:true") == 1
