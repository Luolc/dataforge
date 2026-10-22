# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import dataclass, fields
from pathlib import Path
from typing import cast

import pytest
import yaml

from dataforge.cli import main
from dataforge.curation import (
  Actions,
  AgentSpec,
  AgentState,
  AgentTask,
  CommandResult,
  Docker,
  HarborRunner,
  HOST,
  Limits,
  load,
  Machine,
  Mount,
  SpecError,
  TrialAgent,
  TrialSetup,
  TrialState,
)
from dataforge.curation.harbor import Sidecar
from dataforge.runtime.claude import numbered, Runner, Session

type Doc = dict[str, object]


@dataclass(frozen=True)
class Forwarder:
  port: int

  def __call__(self, docker: Docker) -> None:
    pass


@dataclass(frozen=True)
class Proxy:
  socket: str

  def __call__(self) -> AbstractContextManager[object]:
    return nullcontext()


class Cli:
  def __init__(self) -> None:
    self.calls: list[list[str]] = []

  def __call__(self, args: Sequence[str]) -> CommandResult:
    self.calls.append(list(args))
    return CommandResult(0, "c1\n" if args[0] == "create" else "")


def count_spend(cost_usd: float) -> None:
  pass


def gateway(
  task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path
) -> AbstractContextManager[TrialSetup]:
  return nullcontext(TrialSetup())


class Agents:
  def __init__(self, n: int) -> None:
    self.n: int = n

  def start(self, *args: object) -> object:
    raise NotImplementedError

  def resume(self, *args: object) -> object:
    raise NotImplementedError


DECLARATION = """\
dataforge: 1
name: demo
start: author
inputs:
  image: string
  runs: path
  proxy: path
  model: string
  cap: number
environments:
  session:
    image: ${{ inputs.image }}
    mounts:
      - {source: "${{ inputs.proxy }}", target: /run/proxy, read_only: false}
      - {source: {binding: tool-binary}, target: /usr/local/bin/tool}
      - {source: {uses: cache-dir, with: {name: sessions}}, target: /cache}
    outputs: {source: out, target: /out, read_only: false}
    cpus: 2
    cpu_shares: 512
    memory_gb: 4
    memory_swap_gb: 4
    pids: 1024
    network: true
    labels: {demo.run: r1}
    security: [no-new-privileges]
    cap_drop: [ALL]
    user: "1000:1000"
    workdir: /app
    env: {binding: placeholder-env}
    pass_env: [GATEWAY_TOKEN]
    post_start: {uses: start-forwarder, with: {port: 8080}}
    cli: {binding: docker-cli}
runners:
  session:
    uses: claude-code
    with:
      model: ${{ inputs.model }}
      effort: high
      thinking_display: omitted
      max_budget_usd: ${{ inputs.cap }}
      command: {binding: claude-command}
      timeout: 600
      pass_env: [HOME, PATH, UNSET_HERE]
      files: {numbered: {dir: "${{ inputs.runs }}", prefix: session}}
      tools: [Read, Bash]
      add_dirs: [/input]
      continue_session: true
      min_fix_usd: 0.5
      serve: {uses: model-proxy, with: {socket: proxy/model.sock}}
      spent: {binding: count-spend}
  trials:
    uses: harbor
    with:
      harbor: .venv/bin/harbor
      cpus: 1
      cpu_shares: 256
      pids: 128
      sidecar: {cpus: 0.25, memory_mb: 32}
      timeout: 900
      mounts: [{source: data, target: /data}]
      pass_env: [GATEWAY_TOKEN]
      env: {binding: placeholder-env}
      compose_overlay: [overlays/offline.yaml, {binding: label-overlay}]
      setup: {binding: harbor-gateway}
      docker: docker
states:
  author:
    uses: agent
    env: session
    runner: session
    with: {prompt: Write it., workdir: /app}
    transitions:
      ok: {goto: solve}
      failed: {goto: author}
      error: {end: error}
  solve:
    uses: trial
    runner: trials
    with: {task_dir: task, agent: oracle, limits: {memory_gb: 2}}
    transitions:
      solved: {end: passed}
      unsolved: {goto: author}
      error: {end: error}
"""

PLACEHOLDER = {"MODEL_TOKEN": "placeholder"}


def cache_dir(root: Path) -> Callable[..., Path]:
  def make(name: str) -> Path:
    return root / f"cache-{name}"

  return make


def bindings(root: Path, cli: Cli) -> dict[str, object]:
  return {
    "tool-binary": Path("/opt/tool"),
    "cache-dir": cache_dir(root),
    "placeholder-env": PLACEHOLDER,
    "start-forwarder": Forwarder,
    "docker-cli": cli,
    "model-proxy": Proxy,
    "label-overlay": root / "labels.yaml",
    "harbor-gateway": gateway,
    "count-spend": count_spend,
    "my-runner": Agents,
    "claude-command": ["claude", "--verbose"],
  }


def inputs(root: Path) -> dict[str, object]:
  return {
    "image": "agent:1",
    "runs": str(root / "runs"),
    "proxy": str(root / "proxy"),
    "model": "a-model",
    "cap": 3,
  }


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("HOME", "/home/someone")
  monkeypatch.setenv("PATH", "/usr/bin")
  monkeypatch.setenv("GATEWAY_TOKEN", "from-the-environment")
  monkeypatch.delenv("UNSET_HERE", raising=False)


def load_doc(
  text: str,
  root: Path,
  cli: Cli,
  stack: ExitStack | None,
  extra: Mapping[str, object] | None = None,
) -> Machine:
  return load(
    text,
    inputs=inputs(root),
    actions=Actions(),
    bindings={**bindings(root, cli), **(extra or {})},
    base_dir=root,
    stack=stack,
  )


def docker_fields(docker: Docker) -> dict[str, object]:
  return {f.name: getattr(docker, f.name) for f in fields(docker) if f.init}


def runner_fields(runner: Runner) -> dict[str, object]:
  spec = AgentSpec("p", Path("/app"))
  task = AgentTask("p", {}, "/out", None)
  attributes: dict[str, object] = vars(runner)
  found = {k: v for k, v in attributes.items() if k not in {"_lock", "files"}}
  found["files"] = [
    runner.files(spec, task, 1, 0),
    runner.files(spec, task, 2, 1),
  ]
  return found


@pytest.mark.usefixtures("environment")
def test_environments_and_runners_load_as_they_would_by_hand(
  tmp_path: Path,
) -> None:
  cli = Cli()
  with ExitStack() as stack:
    machine = load_doc(DECLARATION, tmp_path, cli, stack)
    author, solve = machine.states
    assert isinstance(author, AgentState)
    assert isinstance(solve, TrialState)
    docker = author.env
    assert isinstance(docker, Docker)
    assert docker_fields(docker) == docker_fields(
      Docker(
        "agent:1",
        mounts=(
          Mount(tmp_path / "proxy", "/run/proxy", read_only=False),
          Mount(Path("/opt/tool"), "/usr/local/bin/tool"),
          Mount(tmp_path / "cache-sessions", "/cache"),
        ),
        outputs=Mount(tmp_path / "out", "/out", read_only=False),
        cpus=2,
        cpu_shares=512,
        memory_gb=4,
        memory_swap_gb=4,
        pids=1024,
        network=True,
        labels={"demo.run": "r1"},
        security=("no-new-privileges",),
        cap_drop=("ALL",),
        user="1000:1000",
        workdir="/app",
        env=PLACEHOLDER,
        pass_env=("GATEWAY_TOKEN",),
        post_start=Forwarder(8080),
        cli=cli,
      )
    )
    runner = author.actions.agent_runner
    assert isinstance(runner, Runner)
    assert runner_fields(runner) == runner_fields(
      Runner(
        Session(
          command=("claude", "--verbose"),
          model="a-model",
          effort="high",
          thinking_display="omitted",
          max_budget_usd=3,
          timeout=600,
          env={"HOME": "/home/someone", "PATH": "/usr/bin"},
        ),
        numbered(tmp_path / "runs", "session"),
        tools=("Read", "Bash"),
        add_dirs=("/input",),
        continue_session=True,
        min_fix_usd=0.5,
        serve=Proxy("proxy/model.sock"),
        spent=count_spend,
      )
    )
    assert solve.actions.trial_runner == HarborRunner(
      tmp_path / ".venv/bin/harbor",
      cpus=1,
      cpu_shares=256,
      pids=128,
      sidecar=Sidecar(0.25, 32),
      timeout=900,
      mounts=(Mount(tmp_path / "data", "/data"),),
      env={"GATEWAY_TOKEN": "from-the-environment", **PLACEHOLDER},
      compose_overlay=(
        tmp_path / "overlays/offline.yaml",
        tmp_path / "labels.yaml",
      ),
      setup=gateway,
      docker="docker",
    )
    docker.start(None)
    docker.stop()
  assert cli.calls[-1] == ["rm", "--force", "c1"]
  assert "GATEWAY_TOKEN" in cli.calls[0]
  assert not [arg for arg in cli.calls[0] if "from-the-environment" in arg]


def edit(path: str, value: object) -> Callable[[Doc], None]:
  def change(doc: Doc) -> None:
    *parents, last = path.split(".")
    node = doc
    for key in parents:
      node = cast(Doc, node[key])
    node[last] = value

  return change


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize(
  ("change", "error"),
  [
    (
      edit("environments.session.image", None),
      "environments.session.image: expected a string, got null",
    ),
    (
      edit("environments.session.env", {"MODEL_TOKEN": "secret"}),
      "environments.session.env.MODEL_TOKEN: unknown field",
    ),
    (
      edit("environments.session.env", {"binding": "tool-binary"}),
      (
        "environments.session.env: the binding 'tool-binary' is not a mapping"
        " of strings"
      ),
    ),
    (
      edit("environments.session.pass_env", ["GATEWAY-TOKEN"]),
      "environments.session.pass_env[0]: not the name of a variable",
    ),
    (
      edit("environments.session.outputs", {"source": "out"}),
      "environments.session.outputs.target: missing",
    ),
    (
      edit(
        "environments.session.mounts",
        [{"source": {"binding": "placeholder-env"}, "target": "/x"}],
      ),
      "environments.session.mounts[0].source: the hook gives no path",
    ),
    (
      edit("environments.session.post_start", {"uses": "nope"}),
      "environments.session.post_start.uses: no binding named 'nope'",
    ),
    (
      edit(
        "environments.session.post_start",
        {"uses": "start-forwarder", "with": {"prt": 1}},
      ),
      (
        "environments.session.post_start.with: Forwarder.__init__() got an"
        " unexpected keyword argument 'prt'. Did you mean 'port'?"
      ),
    ),
    (
      edit("environments.session.cli", {"binding": "tool-binary"}),
      "environments.session.cli: the hook is not callable",
    ),
    (
      edit("environments.session.cli", {"uses": "tool-binary"}),
      (
        "environments.session.cli.uses: the binding 'tool-binary' is not"
        " callable"
      ),
    ),
    (
      edit("environments.session.cpus", "two"),
      "environments.session.cpus: expected a number, got a string",
    ),
    (
      edit("environments.session.write_limit", {"bps": 1}),
      "environments.session.write_limit: unknown field",
    ),
    (
      edit("environments.session.volumes", []),
      "environments.session.volumes: unknown field",
    ),
    (
      edit("runners.session.with.files", {"dir": "runs"}),
      "runners.session.with.files.dir: unknown field",
    ),
    (
      edit("runners.session.with.env", {"HOME": "/root"}),
      "runners.session.with.env.HOME: unknown field",
    ),
    (
      edit(
        "runners.session.with.command",
        ["env", "MODEL_TOKEN=literal-value", "claude"],
      ),
      "runners.session.with.command: expected a mapping, got a list",
    ),
    (
      edit("runners.session.with.command", {"binding": "placeholder-env"}),
      (
        "runners.session.with.command: the binding 'placeholder-env' is not a"
        " list of strings"
      ),
    ),
    (
      edit("runners.session.with.model", None),
      "runners.session.with.model: expected a string, got null",
    ),
    (
      edit("runners.session.with.thinking_display", "bogus"),
      "runners.session.with.thinking_display: unknown value 'bogus': use "
      + "summarized, omitted, highlights",
    ),
    (
      edit("runners.trials.with.sidecar", {"memory": 32}),
      "runners.trials.with.sidecar.memory: unknown field",
    ),
    (
      edit("runners.trials.with.harbor", 7),
      "runners.trials.with.harbor: expected a string, got an integer",
    ),
    (
      edit("runners.other", {"uses": "nope"}),
      "runners.other.uses: no binding named 'nope'",
    ),
    (
      edit("states.solve.runner", "session"),
      "states.solve.runner: 'session' is not a TrialRunner",
    ),
    (
      edit("states.author.env", "box"),
      "states.author.env: no binding named 'box'",
    ),
    (
      edit("environments.my-runner", {"image": "x"}),
      "environments.my-runner: a binding has this name too",
    ),
  ],
)
def test_a_wrong_environment_or_runner_is_refused(
  tmp_path: Path, change: Callable[[Doc], None], error: str
) -> None:
  doc = cast(Doc, yaml.safe_load(DECLARATION))
  change(doc)
  with ExitStack() as stack, pytest.raises(SpecError) as raised:
    _ = load_doc(yaml.safe_dump(doc), tmp_path, Cli(), stack)
  assert str(raised.value) == error


@pytest.mark.usefixtures("environment")
def test_docker_environments_need_a_stack(tmp_path: Path) -> None:
  with pytest.raises(SpecError) as raised:
    _ = load_doc(DECLARATION, tmp_path, Cli(), None)
  assert str(raised.value) == (
    "environments: load needs a stack to enter them in"
  )
  doc = cast(Doc, yaml.safe_load(DECLARATION))
  del doc["environments"]
  edit("states.author.env", "box")(doc)
  machine = load_doc(yaml.safe_dump(doc), tmp_path, Cli(), None, {"box": HOST})
  assert machine.states


@pytest.mark.usefixtures("environment")
def test_a_runner_may_come_from_a_factory_the_dataset_registers(
  tmp_path: Path,
) -> None:
  doc = cast(Doc, yaml.safe_load(DECLARATION))
  edit("runners.mine", {"uses": "my-runner", "with": {"n": 2}})(doc)
  edit("states.author.runner", "mine")(doc)
  with ExitStack() as stack:
    machine = load_doc(yaml.safe_dump(doc), tmp_path, Cli(), stack)
  author = machine.states[0]
  assert isinstance(author, AgentState)
  runner = author.actions.agent_runner
  assert isinstance(runner, Agents)
  assert runner.n == 2


def test_dataforge_check_takes_a_declaration_with_environments(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  declaration = tmp_path / "machine.yaml"
  text = (
    "dataforge: 1\n"
    "name: demo\n"
    "start: make\n"
    "environments:\n"
    "  box: {image: 'builder:1', pass_env: [GATEWAY_TOKEN]}\n"
    "states:\n"
    "  make:\n"
    "    uses: script\n"
    "    env: box\n"
    "    with: {argv: [make]}\n"
    "    transitions: {ok: {end: passed}, failed: {end: failed}}\n"
  )
  _ = declaration.write_text(text)
  assert main(["check", str(declaration)]) == 0
  assert capsys.readouterr().out == f"{declaration}: ok, 1 states\n"


@pytest.mark.usefixtures("environment")
def test_a_binding_may_not_take_a_runners_name(tmp_path: Path) -> None:
  with ExitStack() as stack, pytest.raises(SpecError) as raised:
    _ = load_doc(DECLARATION, tmp_path, Cli(), stack, {"harbor": Agents})
  assert str(raised.value) == (
    "runners.trials.uses: the binding 'harbor' has a runner's name"
  )
