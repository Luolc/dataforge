# pyright: reportAny=false
# The tests read the JSON the fakes and the runner write.

"""HarborRunner against a fake `harbor` and a fake `docker`, written here as scripts."""

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from dataforge.curation.actions import Limits, Mount
from dataforge.curation.harbor import LABEL, SIDECAR, HarborRunner

# The fake harbor writes what it got to <jobs-dir>/fake.json and then does what FAKE_MODE
# says: "ok", "exception", "no-reward", "exit" (exit 3 with no result) or "hang" (start a
# child, write its pid, sleep).
FAKE_HARBOR = """\
import json, os, pathlib, subprocess, sys, time
argv = sys.argv[1:]
jobs = pathlib.Path(argv[argv.index("--jobs-dir") + 1])
trial = jobs / argv[argv.index("--job-name") + 1] / "task__abc"
(jobs / "fake.json").write_text(json.dumps({"argv": argv, "env": dict(os.environ)}))
mode = os.environ["FAKE_MODE"]
if mode == "exit":
    sys.exit(3)
if mode == "hang":
    child = subprocess.Popen(["sleep", "300"])
    (jobs / "child.pid").write_text(str(child.pid))
    time.sleep(300)
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
(trial / "result.json").write_text(json.dumps(result))
"""

# The fake docker appends its argv to calls.jsonl next to it. `<kind> ls` prints the ids
# in <kind>.ids; `rm` and `rmi` remove them unless a file "stuck" is there.
FAKE_DOCKER = """\
import json, pathlib, sys
here = pathlib.Path(__file__).parent
argv = sys.argv[1:]
with (here / "calls.jsonl").open("a") as f:
    f.write(json.dumps(argv) + "\\n")
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
    return script(bin_dir / "harbor", FAKE_HARBOR), script(bin_dir / "docker", FAKE_DOCKER)


def task(tmp_path: Path, network_mode: str = "public") -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir(exist_ok=True)
    toml = f'[environment]\nnetwork_mode = "{network_mode}"\nmemory_mb = 2048\n'
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
    return HarborRunner(harbor, docker=str(docker), env={"FAKE_MODE": mode, **(env or {})})


def seen_argv(out: Path) -> list[str]:
    return cast(list[str], json.loads((out / "fake.json").read_text())["argv"])


def seen_env(out: Path) -> dict[str, str]:
    return cast(dict[str, str], json.loads((out / "fake.json").read_text())["env"])


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
    result = runner(fakes, mode).run(task(tmp_path), "oracle", Limits(2), outdir(tmp_path))
    assert result.exception is not None
    assert result.exception.startswith(exception)
    assert result.rewards == {}


def test_harbor_gets_only_the_passed_variables_and_env(
    tmp_path: Path, fakes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOME_TOKEN", "not for harbor")
    out = outdir(tmp_path)
    _ = runner(fakes, env={"MODEL_URL": "http://gateway"}).run(
        task(tmp_path), "nop", Limits(2), out
    )
    env = seen_env(out)
    assert "SOME_TOKEN" not in env
    assert env["MODEL_URL"] == "http://gateway"
    assert env["HARBOR_TELEMETRY"] == "0"
    assert env["PATH"] == os.environ["PATH"]


def test_harbor_gets_the_agent_and_the_overlays_in_order(
    tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
    extra = tmp_path / "gateway.yaml"
    _ = extra.write_text("services: {}\n")
    out = outdir(tmp_path)
    agent = {"name": "claude-code", "model": "some-model", "kwargs": {"max_budget_usd": 2}}
    _ = replace(runner(fakes), compose_overlay=[extra]).run(task(tmp_path), agent, Limits(2), out)
    argv = seen_argv(out)
    assert argv[:3] == ["run", "--path", str((tmp_path / "task").resolve())]
    for pair in (
        ["--agent", "claude-code"],
        ["--model", "some-model"],
        ["--agent-kwarg", "max_budget_usd=2"],
        ["--n-attempts", "1"],
    ):
        assert any(argv[i : i + 2] == pair for i in range(len(argv))), pair
    overlays = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--extra-docker-compose"]
    assert overlays == [str((out / "overlay.json").resolve()), str(extra.resolve())]


def test_an_agent_by_import_path(tmp_path: Path, fakes: tuple[Path, Path]) -> None:
    out = outdir(tmp_path)
    agent = {"import_path": "my_dataset.agents:Mine"}
    _ = runner(fakes).run(task(tmp_path), agent, Limits(2), out)
    argv = seen_argv(out)
    assert "--agent" not in argv
    assert argv[argv.index("--agent-import-path") + 1] == "my_dataset.agents:Mine"


def test_the_overlay_caps_the_task_container(tmp_path: Path, fakes: tuple[Path, Path]) -> None:
    out = outdir(tmp_path)
    mount = Mount(tmp_path / "socket", "/run/gateway.sock", read_only=False)
    run = replace(runner(fakes, env={"MODEL_URL": "http://gateway"}), mounts=[mount])
    _ = run.run(task(tmp_path), "nop", Limits(1.5, cpus=3), out)
    overlay = json.loads((out / "overlay.json").read_text())
    main = overlay["services"]["main"]
    assert main["network_mode"] == "none"
    assert main["cap_drop"] == ["ALL"]
    assert main["security_opt"] == ["no-new-privileges:true"]
    assert main["mem_limit"] == "1536m"
    assert main["cpus"] == 3
    assert main["pids_limit"] == 512
    assert main["volumes"] == [f"{(tmp_path / 'socket').resolve()}:/run/gateway.sock"]
    assert main["environment"]["MODEL_URL"] == "http://gateway"
    label = main["labels"][LABEL]
    assert main["build"]["labels"] == {LABEL: label}
    assert overlay["networks"]["default"]["labels"] == {LABEL: label}
    assert SIDECAR not in overlay["services"]


def test_the_overlay_takes_the_default_cpus_and_labels_a_sidecar(
    tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
    out = outdir(tmp_path)
    _ = runner(fakes).run(task(tmp_path, "no-network"), "nop", Limits(2), out)
    overlay = json.loads((out / "overlay.json").read_text())
    assert overlay["services"]["main"]["cpus"] == 2.0
    label = overlay["services"]["main"]["labels"][LABEL]
    assert overlay["services"][SIDECAR] == {"labels": {LABEL: label}}


def test_pre_start_runs_with_the_outdir_before_harbor(
    tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
    calls: list[tuple[Path, bool]] = []
    out = outdir(tmp_path)

    def pre_start(path: Path) -> None:
        calls.append((path, (path / "fake.json").exists()))

    _ = replace(runner(fakes), pre_start=pre_start).run(task(tmp_path), "nop", Limits(2), out)
    assert calls == [(out, False)]
    assert (out / "fake.json").exists()


def test_every_trial_has_its_own_label(tmp_path: Path, fakes: tuple[Path, Path]) -> None:
    labels: list[str] = []
    for name in ("a", "b"):
        out = outdir(tmp_path, name)
        _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), out)
        labels.append(json.loads((out / "overlay.json").read_text())["services"]["main"]
                      ["labels"][LABEL])  # fmt: skip
    assert labels[0] != labels[1]


def test_the_cleanup_chowns_and_removes_what_has_the_label(
    tmp_path: Path, fakes: tuple[Path, Path]
) -> None:
    _ = (fakes[1].parent / "container.ids").write_text("c1 c2")
    out = outdir(tmp_path)
    _ = runner(fakes).run(task(tmp_path), "nop", Limits(2), out)
    label = json.loads((out / "overlay.json").read_text())["services"]["main"]["labels"][LABEL]
    calls = docker_calls(fakes)
    assert calls[0][:3] == ["run", "--rm", f"--label={LABEL}={label}"]
    assert f"--volume={out.resolve()}:/trial" in calls[0]
    assert ["container", "rm", "-f", "c1", "c2"] in calls
    assert all(f"label={LABEL}={label}" in call for call in calls[1:] if "ls" in call)


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
    result = replace(runner(fakes, "hang"), timeout=1.0).run(task(tmp_path), "nop", Limits(2), out)
    assert time.monotonic() - started < 30
    assert result.exception == "TimeoutError: Harbor ran past 1 s"
    child = int((out / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
