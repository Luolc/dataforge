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

from dataforge.curation import harbor
from dataforge.curation.actions import Limits, Mount, TrialAgent
from dataforge.curation.harbor import (
  DockerError,
  HarborRunner,
  LABEL,
  NOT_STARTED,
  SIDECAR,
  Sidecar,
  TrialSetup,
)

# The fake harbor writes what it got to <jobs-dir>/fake.json, starts the trial
# (writes its config.json, in a directory named FAKE_TRIAL, task__abc by
# default) and then does what FAKE_MODE says: "ok", "exception", "no-reward",
# "exit" (exit 3 with no result) or "hang" (start a child, write its own and the
# child's pid, sleep); "interrupt" also sends its parent SIGUSR1 before it
# sleeps, and "stubborn" does too, and again on SIGTERM instead of exiting;
# "cancelled" sleeps, and on SIGTERM writes a result whose exception is
# CancelledError, as Harbor does, and exits 1. With FAKE_NOT_STARTED, it prints
# that many lines, each padded with FAKE_PAD x's, and then the text of
# FAKE_LAST_FILE if given, instead of starting the trial. With FAKE_STRAGGLER,
# the child ignores SIGTERM, so it outlives harbor. The agent's cost is
# FAKE_COST, 0.25 by default and null for "none"; with FAKE_TRACE, the agent
# directory gets a Claude Code trace whose last result event costs that much.
# FAKE_LINKS, a JSON object, gives symlinks to make in the trial's directory, by
# path inside it, FAKE_FILES files to write there and FAKE_FIFO a FIFO to make
# there.
FAKE_HARBOR = """\
import json, os, pathlib, signal, subprocess, sys, time
argv = sys.argv[1:]
jobs = pathlib.Path(argv[argv.index("--jobs-dir") + 1])
trial = jobs / argv[argv.index("--job-name") + 1] / os.environ.get(
    "FAKE_TRIAL", "task__abc"
)
(jobs / "fake.json").write_text(
    json.dumps({"argv": argv, "env": dict(os.environ)})
)
mode = os.environ["FAKE_MODE"]
if "FAKE_NOT_STARTED" in os.environ:
    pad = "x" * int(os.environ.get("FAKE_PAD", "0"))
    for i in range(1, int(os.environ["FAKE_NOT_STARTED"]) + 1):
        print(f"line {i}{pad}")
    if "FAKE_LAST_FILE" in os.environ:
        print(pathlib.Path(os.environ["FAKE_LAST_FILE"]).read_text())
    sys.stdout.flush()
else:
    trial.mkdir(parents=True)
    (trial / "config.json").write_text("{}")
if mode == "exit":
    sys.exit(3)
if mode == "cancelled":
    def cancelled(*_):
        (trial / "result.json").write_text(json.dumps({
            "verifier_result": None,
            "exception_info": {"exception_type": "CancelledError",
                               "exception_message": ""},
        }))
        sys.exit(1)
    signal.signal(signal.SIGTERM, cancelled)
    (jobs / "pids").write_text(str(os.getpid()))
    time.sleep(60)
if mode in ("hang", "interrupt", "stubborn"):
    if "FAKE_STRAGGLER" in os.environ:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen(["sleep", "60"])
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    (jobs / "pids").write_text(f"{os.getpid()} {child.pid}")
    if mode == "stubborn":
        signal.signal(
            signal.SIGTERM, lambda *_: os.kill(os.getppid(), signal.SIGUSR1)
        )
    if mode != "hang":
        os.kill(os.getppid(), signal.SIGUSR1)
    time.sleep(60)
(trial / "agent").mkdir()
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
if "FAKE_TRACE_LINK" in os.environ:
    link = trial / "agent" / "claude-code.txt"
    link.symlink_to(os.environ["FAKE_TRACE_LINK"])
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
for name, text in json.loads(os.environ.get("FAKE_FILES", "{}")).items():
    (trial / name).parent.mkdir(parents=True, exist_ok=True)
    (trial / name).write_text(text)
if "FAKE_FIFO" in os.environ:
    (trial / os.environ["FAKE_FIFO"]).parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(trial / os.environ["FAKE_FIFO"])
for name, to in json.loads(os.environ.get("FAKE_LINKS", "{}")).items():
    (trial / name).parent.mkdir(parents=True, exist_ok=True)
    (trial / name).symlink_to(to)
(trial / "result.json").write_text(json.dumps(result))
"""

# The fake docker appends its argv to calls.jsonl next to it, and first sleeps
# 10 s when a file "slow-<argv[0]>-<argv[1]>" is there. `<kind> ls` with a label
# filter prints the ids in <kind>.ids; `rm` and `rmi` remove them unless a file
# "stuck" is there. mounts.json maps the ids of containers without the label to
# their mounts' sources: `container ls` without a filter prints each as JSON,
# its sources joined with commas, `container inspect` gives their mounts, says
# "No such container" for each id in gone.json and the line in a file
# "inspect-error" when there is one, exiting 1 when it said anything, and
# `container rm` removes them unless "stuck" is there. `run` (the chown) writes
# to alive.json which of the pids in the trial's "pids" still run.
# projects.json maps a kind to the ids, or names, of what compose projects hold
# and their projects: `<kind> ls` filtered by the project label prints them,
# and `<kind> rm` removes them unless "stuck" is there.
# `compose` goes to the real docker on PATH, which only reads compose files
# here.
FAKE_DOCKER = """\
import json, os, pathlib, sys, time
here = pathlib.Path(__file__).parent
argv = sys.argv[1:]
with (here / "calls.jsonl").open("a") as f:
    f.write(json.dumps(argv) + "\\n")
if (here / f"slow-{argv[0]}-{argv[1]}").exists():
    time.sleep(10)
def running(pid):
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")")[-1].split()[0] != "Z"
if argv[0] == "run":
    volume = next(a for a in argv if a.startswith("--volume="))
    trial = volume.removeprefix("--volume=").rsplit(":", 1)[0]
    pids = pathlib.Path(trial, "pids")
    if pids.exists():
        alive = [int(p) for p in pids.read_text().split() if running(p)]
        (here / "alive.json").write_text(json.dumps(alive))
if argv[0] == "compose":
    os.execvp("docker", ["docker", *argv])
projects_file = here / "projects.json"
projects = (
    json.loads(projects_file.read_text()) if projects_file.exists() else {}
)
held = projects.get(argv[0], {})
if "label=com.docker.compose.project" in argv or (
    argv[1] == "rm" and set(argv[2:]) & set(held)
):
    if argv[1] == "ls":
        for i, project in held.items():
            print(f"{i}\\t{project}")
    elif not (here / "stuck").exists():
        for i in argv[2:]:
            held.pop(i, None)
        projects_file.write_text(json.dumps(projects))
    sys.exit(0)
mounts_file = here / "mounts.json"
mounts = json.loads(mounts_file.read_text()) if mounts_file.exists() else {}
gone_file = here / "gone.json"
gone_ids = json.loads(gone_file.read_text()) if gone_file.exists() else []
if argv[0] == "container" and "--filter" not in argv:
    if argv[1] == "ls":
        for i, sources in mounts.items():
            print(json.dumps({"ID": i, "Mounts": ",".join(sources)}))
        sys.exit(0)
    if argv[1] == "inspect":
        print(json.dumps([
            {"Id": i, "Mounts": [{"Source": s} for s in mounts[i]]}
            for i in argv[2:] if i in mounts and i not in gone_ids
        ]))
        gone = [i for i in argv[2:] if i in gone_ids]
        errors = [f"Error response from daemon: No such container: {i}"
                  for i in gone]
        if (here / "inspect-error").exists():
            errors.append((here / "inspect-error").read_text())
        print("\\n".join(errors), file=sys.stderr)
        sys.exit(1 if errors else 0)
    if argv[1] == "rm" and not (here / "stuck").exists():
        for i in argv[3:]:
            mounts.pop(i, None)
        mounts_file.write_text(json.dumps(mounts))
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


def test_harbor_gets_the_verifier_only_when_one_is_set(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  plain, custom = outdir(tmp_path, "plain"), outdir(tmp_path, "custom")
  _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), plain)
  _ = replace(runner(fakes), verifier="my_dataset.verifier:Mine").run(
    task(tmp_path), "nop", Limits(2), custom
  )
  assert "--verifier" not in seen_argv(plain)
  argv = seen_argv(custom)
  assert argv[argv.index("--verifier") + 1] == "my_dataset.verifier:Mine"


def test_harbor_gets_the_environment_only_when_one_is_set(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  plain, custom = outdir(tmp_path, "plain"), outdir(tmp_path, "custom")
  _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), plain)
  _ = replace(runner(fakes), environment=harbor.DOCKER_ENVIRONMENT).run(
    task(tmp_path), "nop", Limits(2), custom
  )
  assert "--env" not in seen_argv(plain)
  argv = seen_argv(custom)
  assert argv[argv.index("--env") + 1] == harbor.DOCKER_ENVIRONMENT
  assert argv.index("--env") < argv.index("--yes")


def not_started(
  fakes: tuple[Path, Path], mode: str = "exit", **env: str
) -> HarborRunner:
  # A runner whose fake harbor prints 30 lines, or as many as FAKE_NOT_STARTED
  # in env says, and does what mode says before it starts the trial.
  return runner(fakes, mode, {"FAKE_NOT_STARTED": "30", **env})


def test_a_trial_harbor_did_not_start_gives_the_end_of_its_log(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  result = not_started(fakes).run(task(tmp_path), "nop", Limits(2), out)
  assert result.exception is not None
  first, *lines = result.exception.splitlines()
  assert first == (
    f"{NOT_STARTED}: Harbor did not start the trial (HarborError: harbor"
    + " exited 3), on run 3 of at most 3; the end of harbor.log:"
  )
  assert lines == [f"line {i}" for i in range(11, 31)]
  assert str(out) not in result.exception
  assert result.rewards == {}
  assert result.artifacts == {"log": out / "harbor.log"}


def test_the_end_of_the_log_is_cut_to_its_bytes(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  # 30 lines of 300 bytes: the last 4096 bytes hold 13 of them whole.
  result = not_started(fakes, FAKE_PAD="292").run(
    task(tmp_path), "nop", Limits(2), outdir(tmp_path)
  )
  assert result.exception is not None
  lines = result.exception.splitlines()[1:]
  assert lines == [f"line {i}{'x' * 292}" for i in range(18, 31)]


def test_the_end_of_the_log_hides_the_values_of_the_env(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  value = "a value of the env"
  last = tmp_path / "last.txt"
  _ = last.write_text(f"Error: {value}, short, FAKE_MODE=exit\n{value[:7]}")
  result = not_started(
    fakes, FAKE_LAST_FILE=str(last), VALUE=value, SHORT="short"
  ).run(task(tmp_path), "nop", Limits(2), outdir(tmp_path))
  assert result.exception is not None
  assert result.exception.splitlines()[-2:] == [
    "Error: ***, short, FAKE_MODE=exit",
    "a value",
  ]


def test_a_trial_harbor_did_not_start_is_run_again_and_each_run_kept(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  setup = Setup(fakes, tmp_path)
  _ = replace(not_started(fakes), setup=setup).run(
    task(tmp_path), "nop", Limits(2), out
  )
  kept = sorted(path.name for path in out.glob("not-started-*"))
  assert kept == ["not-started-1", "not-started-2"]
  for name in kept:
    assert sorted(os.listdir(out / name)) == ["harbor.log", "overlay.json"]
    assert (out / name / "harbor.log").read_text().endswith("line 30\n")
  assert (out / "harbor.log").exists()
  # Each run has its own setup, entered after the last run's cleanup.
  assert [call[0] for call in setup.calls] == ["enter", "exit"] * 3
  counts = [call[3] for call in setup.calls]
  assert counts[1] == counts[2] and counts[3] == counts[4]
  assert counts[0] < counts[1] < counts[3] < counts[5]
  chowns = [call for call in docker_calls(fakes) if call[0] == "run"]
  assert len(chowns) == 3
  labels = {chown[2] for chown in chowns}
  assert len(labels) == 3


def test_a_trial_harbor_started_is_not_run_again(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  result = runner(fakes, "exit").run(task(tmp_path), "nop", Limits(2), out)
  assert result.exception == "HarborError: harbor exited 3"
  assert list(out.glob("not-started-*")) == []


def test_a_run_that_starts_the_trial_ends_the_runs(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  # A harbor that starts the trial on its second run.
  wrapper = script(
    fakes[0].with_name("flaky-harbor"),
    "import os, pathlib, sys\n"
    + f"marker = pathlib.Path({str(tmp_path / 'ran')!r})\n"
    + "if marker.exists():\n"
    + "    os.environ.pop('FAKE_NOT_STARTED')\n"
    + "marker.touch()\n"
    + f"os.execv({str(fakes[0])!r}, [{str(fakes[0])!r}, *sys.argv[1:]])\n",
  )
  result = replace(not_started(fakes, "ok"), harbor=wrapper).run(
    task(tmp_path), "nop", Limits(2), out
  )
  assert result.exception is None
  assert result.rewards == {"reward": 1.0}
  assert [path.name for path in out.glob("not-started-*")] == ["not-started-1"]


def test_no_run_follows_once_the_time_is_up(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  try:
    result = replace(not_started(fakes, "hang"), timeout=1.0).run(
      task(tmp_path), "nop", Limits(2), out
    )
    assert result.exception is not None
    assert result.exception.startswith(
      f"{NOT_STARTED}: Harbor did not start the trial (TimeoutError: Harbor"
      + " ran past 1 s), on run 1 of at most 3;"
    )
    assert list(out.glob("not-started-*")) == []
  finally:
    for pid in alive(out):
      os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(143)])
def test_no_run_follows_an_interrupt(
  tmp_path: Path, fakes: tuple[Path, Path], interrupt: BaseException
) -> None:
  out = outdir(tmp_path)

  def handler(*_: object) -> None:
    raise interrupt

  previous = signal.signal(signal.SIGUSR1, handler)
  try:
    with pytest.raises(type(interrupt)):
      _ = not_started(fakes, "interrupt").run(
        task(tmp_path), "nop", Limits(2), out
      )
    assert list(out.glob("not-started-*")) == []
    assert len([c for c in docker_calls(fakes) if c[0] == "run"]) == 1
  finally:
    _ = signal.signal(signal.SIGUSR1, previous)
    for pid in alive(out):
      os.kill(pid, signal.SIGKILL)


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
  chown = next(call for call in calls if call[0] == "run")
  assert chown[:3] == ["run", "--rm", f"--label={LABEL}={label}"]
  assert f"--volume={out.resolve()}:/trial" in chown
  assert ["container", "rm", "-f", "c1", "c2"] in calls
  release = ["container", "ls", "-a", "--no-trunc", "--format", "{{json .}}"]
  assert all(
    f"label={LABEL}={label}" in call
    for call in calls
    if "ls" in call
    and call != release
    and f"label={harbor.PROJECT}" not in call
  )


def test_something_left_after_the_cleanup_is_an_error(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  _ = (fakes[1].parent / "network.ids").write_text("n1")
  _ = (fakes[1].parent / "stuck").write_text("")
  with pytest.raises(DockerError, match="'network': 1"):
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


def test_a_trial_past_the_timeout_says_so_before_what_harbor_recorded(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  # Harbor, stopped, records the trial's cancellation as its exception.
  out = outdir(tmp_path)
  try:
    result = replace(runner(fakes, "cancelled"), timeout=1.0).run(
      task(tmp_path), "nop", Limits(2), out
    )
    assert result.exception == (
      "TimeoutError: Harbor ran past 1 s; Harbor recorded CancelledError: "
    )
    assert result.rewards == {}
  finally:
    for pid in alive(out):
      os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(143)])
@pytest.mark.parametrize("mode", ["interrupt", "stubborn"])
def test_an_interrupted_harbor_is_stopped_before_the_chown(
  tmp_path: Path, fakes: tuple[Path, Path], interrupt: BaseException, mode: str
) -> None:
  out = outdir(tmp_path)
  raised: list[BaseException] = []

  def handler(*_: object) -> None:
    # A new exception each time, so that which one escaped can be told.
    raised.append(type(interrupt)(*interrupt.args))
    raise raised[-1]

  previous = signal.signal(signal.SIGUSR1, handler)
  started = time.monotonic()
  try:
    with pytest.raises(type(interrupt)) as caught:
      _ = runner(fakes, mode).run(task(tmp_path), "nop", Limits(2), out)
    # In "stubborn", the second interrupt, in the grace period, escapes.
    assert caught.value is raised[-1]
    assert len(raised) == (1 if mode == "interrupt" else 2)
    assert caught.value.args == interrupt.args
    assert time.monotonic() - started < 20
    assert json.loads((fakes[1].parent / "alive.json").read_text()) == []
  finally:
    _ = signal.signal(signal.SIGUSR1, previous)
    for pid in alive(out):
      os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("mode", ["hang", "interrupt"])
def test_a_child_that_outlives_a_stopped_harbor_is_killed_before_the_chown(
  tmp_path: Path, fakes: tuple[Path, Path], mode: str
) -> None:
  out = outdir(tmp_path)
  base = runner(fakes, mode, {"FAKE_STRAGGLER": "1"})
  previous = signal.signal(signal.SIGUSR1, signal.default_int_handler)
  started = time.monotonic()
  try:
    if mode == "hang":
      result = replace(base, timeout=1.0).run(
        task(tmp_path), "nop", Limits(2), out
      )
      assert result.exception == "TimeoutError: Harbor ran past 1 s"
    else:
      with pytest.raises(KeyboardInterrupt):
        _ = base.run(task(tmp_path), "nop", Limits(2), out)
    assert time.monotonic() - started < 20
    assert json.loads((fakes[1].parent / "alive.json").read_text()) == []
  finally:
    _ = signal.signal(signal.SIGUSR1, previous)
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


@pytest.mark.parametrize(
  "module", ["dataforge.runtime.claude", "dataforge.curation"]
)
def test_each_package_imports_first_in_a_fresh_interpreter(module: str) -> None:
  # harbor and runtime.claude import each other; each must load on its own.
  done = subprocess.run(
    [sys.executable, "-I", "-c", f"import {module}"],
    capture_output=True,
    text=True,
    check=False,
  )
  assert done.returncode == 0, done.stderr


def test_the_reprs_leave_out_env(tmp_path: Path) -> None:
  runner = HarborRunner(tmp_path / "harbor", env={"MODEL_TOKEN": "s3cret"})
  setup = TrialSetup(env={"MODEL_TOKEN": "s3cret"})
  assert "harbor=" in repr(runner)
  assert "mounts=" in repr(setup)
  assert "s3cret" not in repr(runner)
  assert "s3cret" not in repr(setup)


def test_a_trace_that_is_a_symlink_gives_no_cost(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  host = tmp_path / "host-trace.txt"
  _ = host.write_text(json.dumps({"type": "result", "total_cost_usd": 0.75}))
  env = {"FAKE_COST": "none", "FAKE_TRACE_LINK": str(host)}
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.cost_usd == 0.0


def test_a_trace_over_the_limit_gives_no_cost(
  tmp_path: Path, fakes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr(harbor, "TRACE_LIMIT", 10)
  env = {"FAKE_COST": "none", "FAKE_TRACE": "0.75"}
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.cost_usd == 0.0


def test_a_trace_that_is_a_symlink_is_not_given(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  secret = tmp_path / "host-only.txt"
  _ = secret.write_text("host-only text")
  env = {"FAKE_TRACE_LINK": str(secret)}
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.trace is None


@pytest.mark.parametrize("traced", [True, False])
def test_a_trial_gives_its_name_directory_and_claude_code_trace(
  tmp_path: Path, fakes: tuple[Path, Path], traced: bool
) -> None:
  out = outdir(tmp_path)
  env = {"FAKE_TRACE": "0.75"} if traced else {}
  result = runner(fakes, env=env).run(task(tmp_path), "oracle", Limits(2), out)
  trial = out / "job" / "task__abc"
  assert (result.id, result.path) == ("task__abc", trial)
  trace = trial / "agent" / "claude-code.txt" if traced else None
  assert result.trace == trace
  assert result.trace_format == ("claude-code-stream-json" if traced else None)


def jsonl(*events: object) -> str:
  return "\n".join(json.dumps(e) for e in events)


def pi_end(role: str, cost: float) -> dict[str, object]:
  return {
    "type": "message_end",
    "message": {"role": role, "usage": {"cost": {"total": cost}}},
  }


# Synthetic traces of Harbor's pi and codex agents. Pi's two assistant messages
# cost 0.25 and 0.5; the user's message and the turn_end that repeats the last
# message are not counted.
PI_TRACE = jsonl(
  pi_end("user", 1.0),
  pi_end("assistant", 0.25),
  pi_end("assistant", 0.5),
  {**pi_end("assistant", 0.5), "type": "turn_end"},
)
CODEX_TRACE = jsonl(
  {"type": "thread.started", "thread_id": "t1"},
  {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
)


@pytest.mark.parametrize(
  ("name", "text", "trace_format", "cost"),
  [
    ("pi.txt", PI_TRACE, "pi-json", 0.75),
    ("codex.txt", CODEX_TRACE, "codex-exec-jsonl", 0.0),
  ],
  ids=["pi", "codex"],
)
def test_a_trial_gives_its_pi_or_codex_trace_and_the_cost_it_gives(
  tmp_path: Path,
  fakes: tuple[Path, Path],
  name: str,
  text: str,
  trace_format: str,
  cost: float,
) -> None:
  out = outdir(tmp_path)
  env = {"FAKE_COST": "none", "FAKE_FILES": json.dumps({f"agent/{name}": text})}
  result = runner(fakes, env=env).run(task(tmp_path), "oracle", Limits(2), out)
  assert result.trace == out / "job" / "task__abc" / "agent" / name
  assert result.trace_format == trace_format
  assert result.cost_usd == cost


def test_harbors_cost_comes_before_the_pi_traces(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  env = {"FAKE_FILES": json.dumps({"agent/pi.txt": PI_TRACE})}
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.cost_usd == 0.25


def test_a_pi_trace_that_is_a_symlink_is_neither_given_nor_read(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  host = tmp_path / "host-pi.txt"
  _ = host.write_text(PI_TRACE)
  env = {
    "FAKE_COST": "none",
    "FAKE_LINKS": json.dumps({"agent/pi.txt": str(host)}),
  }
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert (result.trace, result.trace_format) == (None, None)
  assert result.cost_usd == 0.0


def test_no_symlink_in_a_trial_leads_a_reader_out_of_it(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  host = tmp_path / "host"
  host.mkdir()
  _ = (host / "secret.txt").write_text("host-only text")
  out = outdir(tmp_path)
  # From the trial's agent/ directory, five levels up is tmp_path.
  links = {
    "agent/direct": str(host / "secret.txt"),
    "agent/relative": "../../../../../host/secret.txt",
    "agent/dir": str(host),
    "verifier/hop": "../agent/up",
    "agent/up": "../../..",
    "agent/inside": "../verifier/notes.txt",
    "agent/inside-dir": "../verifier",
  }
  env = {
    "FAKE_LINKS": json.dumps(links),
    "FAKE_FILES": json.dumps({"verifier/notes.txt": "trial text"}),
  }
  result = runner(fakes, env=env).run(task(tmp_path), "oracle", Limits(2), out)
  trial = cast(Path, result.path)
  outside = (
    "agent/direct",
    "agent/relative",
    "agent/dir/secret.txt",
    "verifier/hop/fake.json",
  )
  read = {name: text for name in outside if (text := _read(trial / name))}
  assert read == {}
  assert not os.path.lexists(trial / "agent" / "up")
  assert (trial / "agent" / "inside").read_text() == "trial text"
  assert (trial / "agent" / "inside-dir" / "notes.txt").read_text() == (
    "trial text"
  )
  log = (out / "harbor.log").read_text()
  assert "removed symlink agent/direct" in log


def test_a_symlink_in_place_of_a_trials_directory_is_not_given(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  env = {"FAKE_LINKS": json.dumps({"verifier": "agent"})}
  result = runner(fakes, env=env).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert "verifier" not in result.artifacts
  assert "agent" in result.artifacts


def test_containers_that_mount_the_trial_are_removed_whatever_their_labels(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  trial = out.resolve() / "job" / "task__abc"
  mounts = {"verifier": [str(trial / "verifier")], "other": [str(tmp_path)]}
  _ = (fakes[1].parent / "mounts.json").write_text(json.dumps(mounts))
  _ = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)
  assert ["container", "rm", "-f", "verifier"] in docker_calls(fakes)
  left = json.loads((fakes[1].parent / "mounts.json").read_text())
  assert set(left) == {"other"}


def test_a_container_that_still_mounts_the_trial_is_an_error(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  source = str(out.resolve() / "job" / "task__abc" / "verifier")
  _ = (fakes[1].parent / "mounts.json").write_text(json.dumps({"v": [source]}))
  _ = (fakes[1].parent / "stuck").write_text("")
  with pytest.raises(DockerError, match="containers still mount"):
    _ = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)


def test_a_container_gone_before_it_is_inspected_is_passed_over(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  source = str(out.resolve() / "job" / "task__abc" / "verifier")
  mounts = {"gone": [source], "verifier": [source]}
  _ = (fakes[1].parent / "mounts.json").write_text(json.dumps(mounts))
  _ = (fakes[1].parent / "gone.json").write_text(json.dumps(["gone"]))
  result = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)
  assert result.exception is None
  assert ["container", "rm", "-f", "verifier"] in docker_calls(fakes)


def test_any_other_error_of_docker_inspect_is_an_error(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  source = str(out.resolve() / "job" / "task__abc" / "verifier")
  mounts = {"gone": [source], "verifier": [source]}
  _ = (fakes[1].parent / "mounts.json").write_text(json.dumps(mounts))
  _ = (fakes[1].parent / "gone.json").write_text(json.dumps(["gone"]))
  _ = (fakes[1].parent / "inspect-error").write_text("permission denied")
  with pytest.raises(DockerError, match="docker container inspect failed"):
    _ = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)


def test_only_containers_that_ls_shows_mounting_the_trial_are_inspected(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  # Docker cannot inspect a container of another trial in time; that holds up
  # only that trial's cleanup.
  out = outdir(tmp_path)
  root = out.resolve()
  mounts = {
    "verifier": [str(tmp_path / "a,b"), str(root / "job" / "t" / "verifier")],
    "whole": [str(root)],
    "next-door": [f"{root}-2/job", f"{root}x"],
    "other": [str(tmp_path), "/var/lib/docker/volumes/v/_data"],
  }
  _ = (fakes[1].parent / "mounts.json").write_text(json.dumps(mounts))
  result = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)
  assert result.exception is None
  inspected = [call for call in docker_calls(fakes) if call[1] == "inspect"]
  assert inspected[0] == ["container", "inspect", "verifier", "whole"]
  assert ["container", "rm", "-f", "verifier", "whole"] in docker_calls(fakes)
  left = json.loads((fakes[1].parent / "mounts.json").read_text())
  assert set(left) == {"next-door", "other"}


@pytest.mark.parametrize(
  "slow", ["container-ls", "container-inspect", "image-ls"]
)
def test_docker_past_its_timeout_in_the_cleanup_is_a_docker_error(
  tmp_path: Path,
  fakes: tuple[Path, Path],
  monkeypatch: pytest.MonkeyPatch,
  slow: str,
) -> None:
  monkeypatch.setattr(harbor, "DOCKER_TIMEOUT", 0.5)
  out = outdir(tmp_path)
  source = str(out.resolve() / "job" / "task__abc" / "verifier")
  _ = (fakes[1].parent / "mounts.json").write_text(json.dumps({"v": [source]}))
  _ = (fakes[1].parent / f"slow-{slow}").write_text("")
  with pytest.raises(DockerError, match="ran past"):
    _ = runner(fakes).run(task(tmp_path), "oracle", Limits(2), out)


def test_a_cleanup_past_its_timeout_raises_what_callers_caught_before(
  fakes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
  # A timeout raised subprocess.TimeoutExpired, and a container left a
  # RuntimeError; a caller that caught either still catches DockerError.
  monkeypatch.setattr(harbor, "DOCKER_TIMEOUT", 0.5)
  _ = (fakes[1].parent / "slow-network-ls").write_text("")
  with pytest.raises(DockerError, match=r"ran past 0\.5 s") as caught:
    _ = harbor.cleanup("dataforge.trial=x", str(fakes[1]))
  assert isinstance(caught.value, subprocess.SubprocessError)
  assert isinstance(caught.value, RuntimeError)


@pytest.mark.parametrize(
  ("trial", "ours", "theirs"),
  [
    # A separate verifier's sidecar and network, which Harbor stopped in its
    # stop left, and the agent's environment, beside those of trials whose
    # names this one's is a prefix of, or the other way round.
    (
      "x__1",
      ["x__1__env", "x__1__verifier__trial"],
      ["x__10__env", "x__10__verifier__trial", "x__env", "x__1__verifier"],
    ),
    # Harbor's names are lower case, with "-" for "."; a long step's name is
    # cut and hashed.
    (
      "Task.A__AbC1234",
      ["task-a__abc1234__env", "task-a__abc1234__verifier__x-y__0123abcd"],
      ["task-a__abc12345__env", "task.a__abc1234__env"],
    ),
  ],
)
def test_what_the_trials_compose_projects_hold_is_removed(
  tmp_path: Path,
  fakes: tuple[Path, Path],
  trial: str,
  ours: list[str],
  theirs: list[str],
) -> None:
  projects = {
    kind: {f"{kind[0]}{i}": p for i, p in enumerate(ours + theirs)}
    for kind in ("container", "network", "volume")
  }
  file = fakes[1].parent / "projects.json"
  _ = file.write_text(json.dumps(projects))
  result = runner(fakes, env={"FAKE_TRIAL": trial}).run(
    task(tmp_path), "oracle", Limits(2), outdir(tmp_path)
  )
  assert result.exception is None
  left = json.loads(file.read_text())
  for kind in ("container", "network", "volume"):
    assert sorted(left[kind].values()) == sorted(theirs)


def test_what_is_left_in_the_trials_compose_projects_is_an_error(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  projects = {"network": {"n1": "task__abc__verifier__trial"}}
  _ = (fakes[1].parent / "projects.json").write_text(json.dumps(projects))
  _ = (fakes[1].parent / "stuck").write_text("")
  with pytest.raises(DockerError, match="left in the compose projects"):
    _ = runner(fakes).run(task(tmp_path), "oracle", Limits(2), outdir(tmp_path))


def test_an_entry_of_a_trial_that_is_no_file_directory_or_link_is_removed(
  tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
  out = outdir(tmp_path)
  env = {"FAKE_FIFO": "verifier/tests.json"}
  result = runner(fakes, env=env).run(task(tmp_path), "oracle", Limits(2), out)
  trial = cast(Path, result.path)
  assert not os.path.lexists(trial / "verifier" / "tests.json")
  assert "removed verifier/tests.json" in (out / "harbor.log").read_text()


def _read(path: Path) -> str | None:
  try:
    return path.read_text()[:40]
  except OSError:
    return None
