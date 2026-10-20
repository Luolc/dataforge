# pyright: reportAny=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# Harbor's classes are typed loosely; the tests call into them.

"""`dataforge.gateway.harbor`'s agents, against a fake Harbor environment.

Invariant 29, for the agents: they install nothing over the network, fail when
the client's version is not the pinned one, and read nothing under `agent/`.
"""

import asyncio
import json
from pathlib import Path
import stat
import subprocess
from typing import cast

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
import pytest

from dataforge.gateway.harbor import (
  ClaudeCode,
  Codex,
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
