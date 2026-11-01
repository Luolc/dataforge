# pyright: reportAny=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# Harbor's classes are typed loosely; the tests call into them.

"""`dataforge.gateway.harbor`'s agents, against a fake Harbor environment.

Invariant 29, for the agents: they install nothing over the network, fail when
the client's version is not the pinned one, and read nothing under `agent/`.
"""

import asyncio
from collections.abc import Iterator
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import cast, override
import uuid

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
import pytest

from dataforge.gateway.harbor import (
  ClaudeCode,
  Codex,
  INSTRUCTION_DIR,
  INSTRUCTION_VAR,
  Pi,
  PI_MODELS,
  PI_SETTINGS,
)

VERSION = "DATAFORGE_CLIENT_VERSION"
AGENTS = {
  "claude": (ClaudeCode, "claude-model", "2.1.292", "2.1.292 (Claude Code)"),
  "pi": (Pi, "openai-codex/codex-model", "1.2.0", "1.2.0"),
  "codex": (Codex, "codex-model", "0.159.3", "codex-cli 0.159.3"),
}


class Environment:
  # What an agent's install sees of a container: its commands and uploads.

  default_user: str | None = None

  def __init__(self, version_output: str):
    self.version_output: str = version_output
    self.commands: list[str] = []
    self.uploads: dict[str, str] = {}

  async def exec(self, command: str, **_: object) -> ExecResult:
    self.commands.append(command)
    if command.endswith("--version"):
      return ExecResult(stdout=f"{self.version_output}\n", return_code=0)
    return ExecResult(stdout="", return_code=0)

  async def upload_file(self, source: Path, target: str) -> None:
    self.uploads[target] = source.read_text()


def container(environment: Environment) -> BaseEnvironment:
  return cast(BaseEnvironment, cast(object, environment))


def make(name: str, logs: Path):
  cls, model, _, _ = AGENTS[name]
  return cls(logs_dir=logs, model_name=model)


@pytest.mark.parametrize("name", list(AGENTS))
@pytest.mark.parametrize("pinned", [True, False, None])
def test_an_agent_installs_nothing_and_checks_the_pinned_version(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  name: str,
  pinned: bool | None,
):
  _, _, version, output = AGENTS[name]
  if pinned is None:
    monkeypatch.delenv(VERSION, raising=False)
  else:
    monkeypatch.setenv(VERSION, version if pinned else "9.9.9")
  agent = make(name, tmp_path)
  environment = Environment(output)
  try:
    asyncio.run(agent.install(container(environment)))
  except RuntimeError as e:
    raised = str(e)
  else:
    raised = None
  # Pi's models.json holds the forwarder's URL, which is not a download.
  run = " ".join(c for c in environment.commands if "models.json" not in c)
  for word in ("npm", "curl", "apt", "apk", "nvm", "install", "http"):
    assert word not in run
  if pinned is not None:
    assert environment.commands[0] == f"{name} --version"
  if pinned:
    assert raised is None
    assert agent.version() == version
  else:
    assert raised is not None
    assert (VERSION if pinned is None else "9.9.9") in raised


def test_pi_points_its_provider_at_the_forwarder(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setenv(VERSION, "1.2.0")
  environment = Environment("1.2.0")
  asyncio.run(make("pi", tmp_path).install(container(environment)))
  # Nothing is copied in, which a container without CAP_FOWNER could not
  # chmod; the file is written by one command that runs as the agent's user.
  assert environment.uploads == {}
  command = environment.commands[-1]
  assert "docker" not in command
  target = tmp_path / "pi-agent"
  _ = subprocess.run(
    ["bash", "-c", command.replace("/tmp/harbor-pi-agent", str(target))],
    check=True,
  )
  models = target / "models.json"
  assert json.loads(models.read_text()) == PI_MODELS
  assert stat.S_IMODE(models.stat().st_mode) == 0o600
  settings = target / "settings.json"
  assert json.loads(settings.read_text()) == PI_SETTINGS == {"transport": "sse"}
  assert stat.S_IMODE(settings.stat().st_mode) == 0o600
  assert stat.S_IMODE(target.stat().st_mode) == 0o700
  assert PI_MODELS["providers"]["openai-codex"] == {
    "baseUrl": "http://127.0.0.1:8787/backend-api",
    "apiKey": "${CODEX_GATEWAY_TOKEN}",
  }


def test_codex_takes_redcoast_clients_settings_over_the_datasets(
  tmp_path: Path,
):
  agent = Codex(
    logs_dir=tmp_path,
    model_name="codex-model",
    config={
      "model_provider": "openai",
      "model_reasoning_effort": "high",
      "features": {"plugins": True, "unified_exec": True},
    },
  )
  config = agent._build_effective_config()
  assert config["model_provider"] == "gateway"
  assert config["model_providers"]["gateway"] == {
    "name": "gateway",
    "base_url": "http://127.0.0.1:8787/backend-api/codex",
    "wire_api": "responses",
    "env_key": "CODEX_GATEWAY_TOKEN",
    "supports_websockets": False,
  }
  assert config["features"] == {
    "plugins": False,
    "shell_snapshot": False,
    "unified_exec": True,
  }
  assert config["model_reasoning_effort"] == "high"
  assert config["cli_auth_credentials_store"] == "file"


@pytest.mark.parametrize(
  ("given", "found"), [(None, "disabled"), ("live", "live")]
)
def test_codex_declares_no_web_search_unless_the_dataset_asks(
  tmp_path: Path, given: str | None, found: str
):
  agent = Codex(
    logs_dir=tmp_path,
    model_name="codex-model",
    config={} if given is None else {"web_search": given},
  )
  assert agent._build_effective_config()["web_search"] == found


def session_line(cost: float) -> str:
  # One assistant message's end, as Pi writes it, with its usage and cost.
  return json.dumps(
    {
      "type": "message_end",
      "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input": 10, "output": 5, "cost": {"total": cost}},
        "stopReason": "stop",
        "timestamp": 1,
      },
    }
  )


@pytest.mark.parametrize("name", list(AGENTS))
def test_an_agent_reads_nothing_under_agent_after_the_run(
  tmp_path: Path, name: str
):
  # What the container left in agent/: each trace and session file Harbor's
  # agents read, as links to a file of the host.
  host = tmp_path / "host.jsonl"
  _ = host.write_text(session_line(0.5) + "\n")
  logs = tmp_path / "agent"
  for link in (
    "claude-code.txt",
    "pi.txt",
    "codex.txt",
    "pi/sessions/s.jsonl",
    "sessions/projects/p/s.jsonl",
  ):
    (logs / link).parent.mkdir(parents=True, exist_ok=True)
    (logs / link).symlink_to(host)
  before = sorted(p.relative_to(logs) for p in logs.rglob("*"))
  context = AgentContext()
  make(name, logs).populate_context_post_run(context)
  assert context.model_usage == {}
  assert context.cost_usd is None
  assert context.n_input_tokens is None
  assert sorted(p.relative_to(logs) for p in logs.rglob("*")) == before
  assert host.read_text() == session_line(0.5) + "\n"


SESSION_ID = "0b6c1d2e-3f40-4a5b-8c6d-7e8f9a0b1c2d"


class Session(Environment):
  # As Environment, keeping each command's env and each directory uploaded.

  def __init__(self) -> None:
    super().__init__("2.1.292 (Claude Code)")
    self.envs: list[dict[str, str]] = []
    self.dirs: dict[str, list[str]] = {}
    self.modes: dict[str, int] = {}

  @override
  async def exec(self, command: str, **kwargs: object) -> ExecResult:
    self.envs.append(cast(dict[str, str], kwargs.get("env") or {}))
    return await super().exec(command, **kwargs)

  @override
  async def upload_file(self, source: Path, target: str) -> None:
    self.modes[target] = stat.S_IMODE(source.stat().st_mode)
    await super().upload_file(source, target)

  async def upload_dir(self, source: Path, target: str) -> None:
    self.dirs[target] = sorted(
      p.relative_to(source).as_posix() for p in source.rglob("*")
    )
    self.modes[target] = stat.S_IMODE(source.stat().st_mode)
    for p in source.rglob("*"):
      self.modes[f"{target}/{p.relative_to(source)}"] = stat.S_IMODE(
        p.lstat().st_mode
      )


def session_files(tmp_path: Path) -> Path:
  # An earlier trial's session and the directory of its tool results.
  project = tmp_path / "earlier" / "-app"
  results = project / SESSION_ID / "tool-results"
  results.mkdir(parents=True)
  _ = (results / "out.txt").write_text("a tool's output\n")
  session = project / f"{SESSION_ID}.jsonl"
  _ = session.write_text(json.dumps({"type": "user", "cwd": "/app"}) + "\n")
  # As the host's user leaves them after a trial.
  session.chmod(0o600)
  for path in (results / "out.txt",):
    path.chmod(0o600)
  for path in (results, results.parent):
    path.chmod(0o700)
  return session


def run_claude(
  tmp_path: Path,
  *,
  load: bool,
  resume_instruction: Path | None,
  user: str | None = None,
  task: str = "The task's instruction.",
) -> Session:
  agent = ClaudeCode(
    logs_dir=tmp_path / "logs",
    model_name="claude-model",
    load_trajectory=str(session_files(tmp_path)) if load else None,
    resume_instruction=None
    if resume_instruction is None
    else str(resume_instruction),
  )
  environment = Session()
  environment.default_user = user
  start = agent.load if load else agent.run
  asyncio.run(start(task, container(environment), AgentContext()))
  return environment


INSTRUCTION_FILES = f"{INSTRUCTION_DIR}/dataforge-instruction-"
# Linux's MAX_ARG_STRLEN, the most an argument or a variable of a command can
# hold, its terminating NUL counted.
ARGUMENT_LIMIT = 131072


def message(environment: Session) -> str:
  # The text the last command reads into the variable it pipes into `claude`.
  command = environment.commands[-1]
  return next(
    text
    for path, text in environment.uploads.items()
    if path.startswith(INSTRUCTION_FILES) and f" -- {path} " in command
  )


def copied(environment: Session) -> dict[str, str]:
  # What was uploaded besides the message.
  return {
    path: text
    for path, text in environment.uploads.items()
    if not path.startswith(INSTRUCTION_FILES)
  }


def test_claude_code_goes_on_with_a_loaded_session_and_its_tool_results(
  tmp_path: Path,
):
  instruction = tmp_path / "continue.md"
  _ = instruction.write_text("Go on from where you were.\n")
  environment = run_claude(tmp_path, load=True, resume_instruction=instruction)
  project = "/logs/agent/sessions/projects/-app"
  assert copied(environment) == {
    f"{project}/{SESSION_ID}.jsonl.upload": '{"type": "user", "cwd": "/app"}\n'
  }
  assert environment.dirs == {
    f"{project}/{SESSION_ID}.upload": ["tool-results", "tool-results/out.txt"]
  }
  assert f"--resume {SESSION_ID} " in environment.commands[-1]
  assert message(environment) == "Go on from where you were.\n"


def test_claude_code_preflight_takes_only_known_options() -> None:
  ClaudeCode.preflight(
    {"version": "2.1.296", "resume_instruction": "/x/resume.txt"}
  )
  with pytest.raises(ValueError, match="Unknown option 'no_such_option'"):
    ClaudeCode.preflight({"version": "2.1.296", "no_such_option": 1})


def test_claude_code_without_a_loaded_session_gets_the_tasks_instruction(
  tmp_path: Path,
):
  instruction = tmp_path / "continue.md"
  _ = instruction.write_text("Go on from where you were.\n")
  environment = run_claude(tmp_path, load=False, resume_instruction=instruction)
  assert copied(environment) == {}
  assert environment.dirs == {}
  assert "--resume" not in environment.commands[-1]
  assert message(environment) == "The task's instruction."


def test_claude_code_loaded_without_a_resume_instruction_gets_the_tasks(
  tmp_path: Path,
):
  environment = run_claude(tmp_path, load=True, resume_instruction=None)
  assert f"--resume {SESSION_ID} " in environment.commands[-1]
  assert message(environment) == "The task's instruction."


@pytest.mark.parametrize("load", [False, True])
def test_claude_code_passes_no_argument_over_the_kernels_limit(
  tmp_path: Path, load: bool
):
  # A message Linux would refuse to `docker exec`, as a variable, with E2BIG.
  text = "x" * ARGUMENT_LIMIT + "\n\n"
  instruction = tmp_path / "continue.md"
  _ = instruction.write_text(text)
  environment = run_claude(
    tmp_path, load=load, resume_instruction=instruction if load else None,
    task=text,
  )  # fmt: skip
  for command, env in zip(environment.commands, environment.envs, strict=True):
    assert len(command.encode()) < ARGUMENT_LIMIT
    for name, value in env.items():
      assert len(f"{name}={value}".encode()) < ARGUMENT_LIMIT
    assert not any(name.startswith(INSTRUCTION_VAR) for name in env)
  assert message(environment) == text


def test_claude_code_copies_a_loaded_sessions_files_in_as_the_agents_user(
  tmp_path: Path,
):
  environment = run_claude(
    tmp_path, load=True, resume_instruction=None, user="agent"
  )
  project = "/logs/agent/sessions/projects/-app"
  session = f"{project}/{SESSION_ID}.jsonl"
  beside = f"{project}/{SESSION_ID}"
  # Harbor runs each command after a prefix of its own.
  ran = [c.split("; ", 1)[-1] for c in environment.commands]
  assert f"cp {session}.upload {session} && rm {session}.upload" in ran
  assert (
    f"mkdir -p {beside} && cp -R {beside}.upload/. {beside} && "
    f"rm -rf {beside}.upload"
  ) in ran
  assert not any("chown" in c or "chmod" in c for c in ran)
  # The copies are readable by all, and a directory of them removable by the
  # command that copies them, which a container without CAP_DAC_OVERRIDE
  # needs whoever owns them.
  assert environment.modes[f"{session}.upload"] == 0o644
  assert environment.modes[f"{beside}.upload/tool-results/out.txt"] == 0o644
  for directory in ("", "/tool-results"):
    assert environment.modes[f"{beside}.upload{directory}"] == 0o777


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    ["docker", *args], check=check, capture_output=True, text=True
  )


IMAGE = "ubuntu:24.04"


class Hardened(Environment):
  # A container as the curated tasks run: no network, no capabilities, root.

  def __init__(self, name: str) -> None:
    super().__init__("2.1.292 (Claude Code)")
    self.name: str = name

  @override
  async def exec(self, command: str, **kwargs: object) -> ExecResult:
    # Each variable an argument of `docker exec`, as Harbor passes it.
    env = cast(dict[str, str], kwargs.get("env") or {})
    variables = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]
    done = docker(
      "exec", *variables, self.name, "bash", "-c", command, check=False
    )
    return ExecResult(
      stdout=done.stdout, stderr=done.stderr, return_code=done.returncode
    )

  @override
  async def upload_file(self, source: Path, target: str) -> None:
    _ = docker("cp", str(source), f"{self.name}:{target}")

  async def upload_dir(self, source: Path, target: str) -> None:
    _ = docker("cp", f"{source}/.", f"{self.name}:{target}")


@pytest.fixture
def hardened() -> Iterator[Hardened]:
  if shutil.which("docker") is None or os.getuid() == 0:
    pytest.skip("needs docker and a host user other than root")
  if (
    docker("image", "inspect", IMAGE, check=False).returncode != 0
    and docker("pull", IMAGE, check=False).returncode != 0
  ):
    pytest.skip(f"needs {IMAGE}")
  name = f"dataforge-resume-test-{uuid.uuid4().hex[:12]}"
  _ = docker(
    "run", "-d", "--name", name, "--network", "none", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges", "--cpus", "1", "--memory", "256m",
    "--pids-limit", "64", "--init", IMAGE, "sleep", "300",
  )  # fmt: skip
  try:
    _ = docker(
      "exec", name, "mkdir", "-p", "/logs/agent/sessions/projects/-app"
    )
    yield Hardened(name)
  finally:
    _ = docker("rm", "-f", name, check=False)


def reads_and_writes(name: str, path: str) -> bool:
  done = docker(
    "exec", name, "bash", "-c", f"head -c 1 {path} >/dev/null && : >> {path}",
    check=False,
  )  # fmt: skip
  return done.returncode == 0


def test_a_file_copied_in_with_the_hosts_owner_is_out_of_reach_of_root(
  tmp_path: Path, hardened: Hardened
):
  # What Harbor's own upload leaves, and why it is not used.
  session = session_files(tmp_path)
  project = "/logs/agent/sessions/projects/-app"
  _ = docker("exec", hardened.name, "mkdir", "-p", project)
  _ = docker("cp", str(session), f"{hardened.name}:{project}/{session.name}")
  assert not reads_and_writes(hardened.name, f"{project}/{session.name}")


def test_a_loaded_session_and_its_directory_are_readable_and_writable_by_root(
  tmp_path: Path, hardened: Hardened
):
  session = session_files(tmp_path)
  agent = ClaudeCode(
    logs_dir=tmp_path / "logs",
    model_name="claude-model",
    load_trajectory=str(session),
  )
  asyncio.run(agent._upload_load_trajectory(container(hardened), session))
  project = "/logs/agent/sessions/projects/-app"
  files = [
    f"{project}/{session.name}",
    f"{project}/{SESSION_ID}/tool-results/out.txt",
  ]
  for path in files:
    assert reads_and_writes(hardened.name, path), path
  # A subagent's or a tool's new file goes into the directories.
  inside = f"{project}/{SESSION_ID}"
  made = docker(
    "exec", hardened.name, "bash", "-c",
    f"echo x > {inside}/tool-results/new.txt && echo x > {inside}/new.txt",
    check=False,
  )  # fmt: skip
  assert made.returncode == 0, made.stderr
  left = docker("exec", hardened.name, "ls", "-A", project).stdout.split()
  assert sorted(left) == [SESSION_ID, f"{SESSION_ID}.jsonl"]


def test_a_message_over_the_kernels_limit_reaches_claude_whole(
  tmp_path: Path, hardened: Hardened
):
  # A `claude` that keeps what it reads.
  claude = "/usr/local/bin/claude"
  script = "#!/bin/sh\ncat > /tmp/received\n"
  made = docker(
    "exec", hardened.name, "bash", "-c",
    f"printf %s '{script}' > {claude} && chmod +x {claude}",
    check=False,
  )  # fmt: skip
  assert made.returncode == 0, made.stderr
  text = "x" * ARGUMENT_LIMIT + ' $HOME `true` \\ "\n\n'
  agent = ClaudeCode(logs_dir=tmp_path / "logs", model_name="claude-model")
  asyncio.run(agent.run(text, container(hardened), AgentContext()))
  received = docker("exec", hardened.name, "cat", "/tmp/received").stdout
  assert received == text
  left = docker("exec", hardened.name, "ls", "-A", INSTRUCTION_DIR).stdout
  assert left.split() == ["received"]
