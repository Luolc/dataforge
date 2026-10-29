"""Harbor 0.24.0's claude-code, pi and codex agents, behind the gateway.

Only Harbor imports this module, by import path, in its own process where the
dataset installed it; nothing else in dataforge does, so dataforge does not
depend on Harbor. `trial_setup` mounts each agent's client, pinned, into a
container with no network, and serves the gateway on `127.0.0.1:8787` there.

Each agent's `install` runs nothing over the network: it fails unless the
client's `--version` gives the version `trial_setup` pinned, in
`DATAFORGE_CLIENT_VERSION`. `Pi.install` also points Pi's `openai-codex`
provider at the forwarder; `Codex`'s configuration is redcoast-client's. After
the agent's phase, Harbor's own agents read their logs under `agent/`, which
the container, still running, can point at a file of the host through a link;
these read nothing there, and the trial's trace and cost come from
`HarborRunner` once the containers are gone.

`ClaudeCode` goes on with an earlier session when Harbor loads one
(`--load-trajectory <id>.jsonl`): it uploads, besides the session, the
directory of the same name beside it, whose tool results and subagents'
sessions the session names by their paths in the container, and with
`resume_instruction` sends that file's text as the session's next message in
place of the task's instruction.
"""

# Harbor is a development dependency only; its classes are typed loosely.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportUntypedBaseClass=false, reportAny=false, reportExplicitAny=false

from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
from typing import Any, ClassVar, override

from harbor.agents.base import AgentOptions
from harbor.agents.installed.claude_code import ClaudeCode as HarborClaudeCode
from harbor.agents.installed.claude_code import ClaudeCodeOptions
from harbor.agents.installed.codex import Codex as HarborCodex
from harbor.agents.installed.pi import Pi as HarborPi
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from dataforge.gateway.hooks import ADDRESS, VERSION_VAR

MODELS_DIR = "/tmp/harbor-pi-agent"
# Pi's provider for ChatGPT subscriptions, pointed at the forwarder; the token
# is the placeholder in CODEX_GATEWAY_TOKEN, which the proxy replaces.
PI_MODELS = {
  "providers": {
    "openai-codex": {
      "baseUrl": f"{ADDRESS}/backend-api",
      "apiKey": "${CODEX_GATEWAY_TOKEN}",
    }
  }
}
# Pi's settings: the server-sent events transport, so that Pi does not first
# try the WebSocket one (`transport` in Pi 1.2.0's docs/settings.md).
PI_SETTINGS = {"transport": "sse"}
# What redcoast-client v0.1.11 passes Codex CLI with -c (`codex.go`), with the
# provider's base URL at the forwarder.
CODEX_CONFIG: dict[str, Any] = {
  "model_provider": "gateway",
  "model_providers": {
    "gateway": {
      "name": "gateway",
      "base_url": f"{ADDRESS}/backend-api/codex",
      "wire_api": "responses",
      "env_key": "CODEX_GATEWAY_TOKEN",
      "supports_websockets": False,
    }
  },
  "features": {"plugins": False, "shell_snapshot": False},
  "analytics": {"enabled": False},
  "otel": {"metrics_exporter": "none"},
  "check_for_update_on_startup": False,
  "feedback": {"enabled": False},
  "cli_auth_credentials_store": "file",
}
# Codex CLI 0.159.3 declares the hosted `web_search` tool by default for some
# models, which the proxy refuses (`screen`); a dataset that asks for it is
# refused.
CODEX_OFFLINE: dict[str, Any] = {"web_search": "disabled"}


async def _check_version(
  agent: Any, environment: BaseEnvironment, command: str
) -> None:
  # Fails unless the client in the container gives the pinned version.
  pinned = os.environ.get(VERSION_VAR)
  if not pinned:
    raise RuntimeError(
      f"no {VERSION_VAR}: run the trial with dataforge.gateway.trial_setup"
    )
  result = await environment.exec(command=f"{command} --version")
  found = (
    agent.parse_version(result.stdout or "")
    if result.return_code == 0
    else None
  )
  if found != pinned:
    raise RuntimeError(
      f"{command} --version gives {found!r}, not the pinned {pinned!r}"
    )
  agent._version = pinned


def _read_nothing(context: AgentContext) -> None:
  # An empty model_usage, so that Harbor reads no trajectory either.
  context.model_usage = {}


class ResumableOptions(ClaudeCodeOptions):
  """Harbor's claude-code options and `resume_instruction`.

  Attributes:
    resume_instruction: A file on this host whose text a loaded session gets
      as its next message.
  """

  resume_instruction: str | None = None


class ClaudeCode(HarborClaudeCode):
  """Harbor's claude-code agent, its client pinned and its logs unread.

  A loaded session goes where Harbor puts it, under the project of the
  working directory /app, with the directory of its tool results and
  subagents beside it, given to the agent's user as Harbor gives the
  session; a session from another working directory is not found there.

  Attributes:
    options_model: The options Harbor checks the agent's kwargs against.
  """

  options_model: ClassVar[type[AgentOptions] | None] = ResumableOptions

  def __init__(
    self,
    logs_dir: Path,
    *args: Any,
    resume_instruction: str | None = None,
    **kwargs: Any,
  ) -> None:
    """Make the agent.

    Args:
      logs_dir: Where Harbor keeps the agent's logs.
      *args: Harbor's.
      resume_instruction: A file on this host whose text a loaded session
        gets as its next message, in place of the task's instruction.
      **kwargs: Harbor's.
    """
    self._resume_instruction: Path | None = (
      Path(resume_instruction) if resume_instruction else None
    )
    super().__init__(logs_dir, *args, **kwargs)

  @override
  def render_instruction(self, instruction: str) -> str:
    """Give a loaded session `resume_instruction`'s text, if there is one.

    Args:
      instruction: The task's instruction.

    Returns:
      The session's message.
    """
    if self._load and self._resume_instruction is not None:
      return self._resume_instruction.read_text()
    return super().render_instruction(instruction)

  @override
  async def _upload_load_trajectory(
    self, environment: BaseEnvironment, source: Path
  ) -> None:
    await super()._upload_load_trajectory(environment, source)
    beside = source.with_suffix("")
    if beside.is_dir(follow_symlinks=False):
      projects = EnvironmentPaths.agent_dir / "sessions" / "projects"
      target = (projects / "-app" / beside.name).as_posix()
      await environment.upload_dir(beside, target)
      if environment.default_user is not None:
        user = shlex.quote(str(environment.default_user))
        await self.exec_as_root(
          environment, command=f"chown -R {user} {shlex.quote(target)}"
        )

  @override
  async def install(self, environment: BaseEnvironment) -> None:
    """Check the mounted `claude`; install nothing.

    Args:
      environment: The trial's environment.
    """
    await _check_version(self, environment, "claude")

  @override
  def populate_context_post_run(self, context: AgentContext) -> None:
    """Read nothing under `agent/`.

    Args:
      context: The agent's result, which Harbor writes to `result.json`.
    """
    _read_nothing(context)


class Pi(HarborPi):
  """Harbor's pi agent, through the gateway, its client pinned."""

  @override
  async def install(self, environment: BaseEnvironment) -> None:
    """Check the mounted `pi`; point its provider at the forwarder, over SSE.

    Args:
      environment: The trial's environment.
    """
    await _check_version(self, environment, "pi")
    # Written by a command in the container, as the agent's user: Harbor's
    # own writer copies the file in as the host's user and then chmods it as
    # root, which a container without CAP_FOWNER refuses.
    directory = shlex.quote(MODELS_DIR)
    path = shlex.quote(f"{MODELS_DIR}/models.json")
    settings = shlex.quote(f"{MODELS_DIR}/settings.json")
    text = shlex.quote(json.dumps(deepcopy(PI_MODELS), indent=2) + "\n")
    config = shlex.quote(json.dumps(PI_SETTINGS, indent=2) + "\n")
    await self.exec_as_agent(
      environment,
      command=(
        f"mkdir -p {directory} && chmod 700 {directory} && "
        f"printf %s {text} > {path} && chmod 600 {path} && "
        f"printf %s {config} > {settings} && chmod 600 {settings}"
      ),
    )

  @override
  def populate_context_post_run(self, context: AgentContext) -> None:
    """Read nothing under `agent/`.

    Args:
      context: The agent's result, which Harbor writes to `result.json`.
    """
    _read_nothing(context)


class Codex(HarborCodex):
  """Harbor's codex agent, through the gateway, its client pinned."""

  @override
  def _load_base_config(self) -> dict[str, Any]:
    # The dataset's configuration, with redcoast-client's settings over it;
    # without the dataset's own `web_search`, none.
    return _merged(CODEX_OFFLINE | super()._load_base_config(), CODEX_CONFIG)

  @override
  async def install(self, environment: BaseEnvironment) -> None:
    """Check the mounted `codex`; install nothing.

    Args:
      environment: The trial's environment.
    """
    await _check_version(self, environment, "codex")

  @override
  def populate_context_post_run(self, context: AgentContext) -> None:
    """Read nothing under `agent/`.

    Args:
      context: The agent's result, which Harbor writes to `result.json`.
    """
    _read_nothing(context)


def _merged(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
  found = deepcopy(base)
  for key, value in over.items():
    if isinstance(value, dict) and isinstance(found.get(key), dict):
      found[key] = _merged(found[key], value)
    else:
      found[key] = deepcopy(value)
  return found
