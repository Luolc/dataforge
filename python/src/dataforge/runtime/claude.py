"""Headless `claude -p` sessions, with their trace and usage kept on disk.

The module knows Claude Code and nothing of a dataset: the caller gives the
prompt, the model, the spend cap, the tools and their permission rules, the
directories and the schema of the result, and decides what the result means.
It runs the command the caller gives, in the environment the caller gives:
`claude` on this machine, or a prefix that runs it elsewhere, such as
`docker exec -i <container> claude` in a container. It knows no gateway and
reads or passes no credential.

A session reads its prompt from stdin and prints stream-json. It takes in
none of the machine's or a project's CLAUDE.md, AGENTS.md, settings, skills,
hooks or MCP servers: safe mode, together with `--setting-sources ""`,
`--strict-mcp-config` and `--disable-slash-commands`; without safe mode,
`--setting-sources ""` would turn the user's disabled auto memory back on. A
new session starts in an empty working directory of its own. Restricted mode
confines the file tools to the working directory and the directories added
with `--add-dir`; only the tools named are available, and in `dontAsk` mode
every use the allowed rules do not cover is denied. The result comes back as
structured output that `--json-schema` validates, when the session is given a
schema, or in files the session writes. `--max-budget-usd` stops the session
at its spend cap.

Even so, `claude` writes large tool output, and a kept session, under
`<config>/projects/<the working directory's name>/`; that directory is
removed when a session that is not kept ends, and by whoever keeps one when
it is done with it.

`Runner` drives these sessions as `dataforge.curation.AgentRunner`.
"""

import argparse
from collections.abc import Callable, Mapping, Sequence
import contextlib
from contextlib import AbstractContextManager, nullcontext
import dataclasses
from dataclasses import dataclass, field
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import threading
from typing import cast

from dataforge.curation.actions import (
  AgentRun,
  AgentSpec,
  AgentTask,
  Docker,
  Env,
  Host,
)
from dataforge.curation.engine import Data

DEFAULT_TIMEOUT = 1800.0
# The values `--effort` takes: the levels of Harbor's claude-code agent
# (`Literal` in harbor/agents/installed/claude_code.py), which rejects any
# other. `claude` itself ignores an unknown level and runs at its default,
# so a mistyped one would be recorded but not used. Read on 2026-10-19.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The tools an agent has when its spec names none.
TOOLS = ("Read", "Glob", "Grep", "Write", "Edit", "Bash")
# How a session ends when it did no work the host could judge: it printed no
# result, or Claude Code stopped on an error of its own.
FAILED_ENDINGS = ("no result", "error_during_execution")
# The least of a start's cap that a fix of the validator's problems runs on.
MIN_FIX_USD = 0.25
# The format of a session's trace: what `claude -p --output-format
# stream-json` prints, one JSON event per line.
TRACE_FORMAT = "claude-code-stream-json"
# The file tools whose uses are allowed only in the agent's directories.
_SCOPED_TOOLS = ("Write", "Edit")

# Claude Code 2.1.296 names a working directory's directory under
# `<config>/projects` by turning each character of its path but ASCII
# letters and digits into `-`; a name longer than this is cut to it and
# given a hash, which is not computed here.
_MAX_PROJECT_NAME = 200

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Trace:
  """What a stream-json trace says about how its session ended.

  Attributes:
    result: The last `result` event, or an empty dict when there is none.
    unparsed: How many lines are not JSON.
    rejected: The input of the last `StructuredOutput` call the session
      rejected against the schema, or None when it rejected none.
  """

  result: Mapping[str, object]
  unparsed: int = 0
  rejected: object = None

  @property
  def subtype(self) -> str:
    """The result's `subtype`, or `no result` when there is none."""
    found = self.result.get("subtype")
    return found if isinstance(found, str) else "no result"

  @property
  def session_id(self) -> str | None:
    """The result's `session_id`, which `--resume` takes, if it has one."""
    found = self.result.get("session_id")
    return found if isinstance(found, str) else None

  @property
  def cost_usd(self) -> float | None:
    """What the session reported it cost, in US dollars, if it did."""
    found = self.result.get("total_cost_usd")
    if isinstance(found, bool) or not isinstance(found, int | float):
      return None
    return float(found)

  @property
  def num_turns(self) -> int | None:
    """The turns the session took, if the result gives them."""
    found = self.result.get("num_turns")
    if isinstance(found, bool) or not isinstance(found, int):
      return None
    return found

  @property
  def output(self) -> object:
    """The structured output as decoded JSON, or None when there is none."""
    return self.result.get("structured_output")

  @property
  def text(self) -> str | None:
    """The result's text, the session's last answer, if it has one."""
    found = self.result.get("result")
    return found if isinstance(found, str) else None


@dataclass(frozen=True, slots=True, kw_only=True)
class Result:
  """How a session ended.

  Attributes:
    subtype: The result event's `subtype`: `timeout` when the session was
      killed, `no result` when it printed none.
    session_id: The result event's `session_id`, which `--resume` takes,
      if it printed one.
    cost_usd: What the session reported it cost, in US dollars, if it did;
      a resumed session reports its own and that of the runs before it.
    output: The structured output as decoded JSON, or None when there is
      none; not yet checked against the schema.
    text: The session's last answer, if it printed one.
    rejected: The input of the last `StructuredOutput` call the session
      rejected against the schema, or None when it rejected none.
    paths: The files the session wrote: its prompt, trace, stderr and usage.
  """

  subtype: str
  session_id: str | None
  cost_usd: float | None
  output: object
  text: str | None
  rejected: object
  paths: tuple[Path, ...]

  @property
  def trace(self) -> Path:
    """The session's stream-json trace."""
    return self.paths[1]


def add_arguments(
  parser: argparse.ArgumentParser,
  *,
  model: str,
  effort: str,
  max_budget_usd: float,
) -> None:
  """Add the options that `Session.from_args` reads to a command.

  Args:
    parser: The command's parser.
    model: The default of `--model`.
    effort: The default of `--effort`.
    max_budget_usd: The default of `--max-budget-usd`.
  """
  _ = parser.add_argument(
    "--claude",
    default="claude",
    help="the claude command, split as a shell would (default: claude)",
  )
  _ = parser.add_argument("--model", default=model)
  _ = parser.add_argument("--effort", default=effort, choices=EFFORTS)
  _ = parser.add_argument(
    "--max-budget-usd",
    type=float,
    default=max_budget_usd,
    help="spend cap of each session (default: %(default)s)",
  )
  _ = parser.add_argument(
    "--timeout",
    type=float,
    default=DEFAULT_TIMEOUT,
    help="seconds before a session is killed (default: %(default)s)",
  )


@dataclass(frozen=True, slots=True, kw_only=True)
class Session:
  """How to start a session.

  Attributes:
    command: The `claude` to run and its leading arguments, or a prefix
      that runs it elsewhere followed by it.
    model: The model.
    effort: The effort level.
    max_budget_usd: The spend cap of each session, in US dollars.
    timeout: Seconds before the session's process group on this machine is
      killed; what a prefix started elsewhere, such as `claude` in a
      container, may go on until that place stops it.
    persist: Whether `claude` keeps the session, so that a later session
      can `--resume` it. It is kept where the command runs `claude`; on this
      machine, `run` then leaves the working directory's project directory
      for the caller to remove with `remove_project_dir`.
    env: The environment the command runs in on this machine, or None for
      this process's; with a prefix such as `docker exec`, the prefix's. Give
      one without the credentials `claude` has no need of. Left out of
      the repr, as its values may be credentials.
  """

  command: tuple[str, ...] = ("claude",)
  model: str
  effort: str
  max_budget_usd: float
  timeout: float = DEFAULT_TIMEOUT
  persist: bool = False
  env: Mapping[str, str] | None = field(default=None, repr=False)

  @classmethod
  def from_args(cls, args: argparse.Namespace) -> Session:
    """Return the session that the options of `add_arguments` give.

    Args:
      args: The parsed arguments.

    Returns:
      The session.
    """
    return cls(
      command=tuple(shlex.split(cast(str, args.claude))),
      model=cast(str, args.model),
      effort=cast(str, args.effort),
      max_budget_usd=cast(float, args.max_budget_usd),
      timeout=cast(float, args.timeout),
    )

  def args(
    self,
    *,
    tools: Sequence[str],
    allow: Sequence[str],
    schema: Mapping[str, object] | None,
    add_dirs: Sequence[Path | str],
    resume: str | None = None,
  ) -> list[str]:
    """Return the command line of a session.

    Args:
      tools: The tools the session has; empty for none.
      allow: The permission rules of the tool uses allowed, such as `Read`
        or `Write(./**)`; any other use is denied.
      schema: The JSON Schema of the structured output, or None for none.
      add_dirs: Directories the file tools may read besides the working
        directory.
      resume: The ID of a kept session this one goes on from, or None.

    Returns:
      The command line.
    """
    args = [
      *self.command,
      "-p",
      "--output-format",
      "stream-json",
      "--verbose",
      "--model",
      self.model,
      "--effort",
      self.effort,
      "--max-budget-usd",
      str(self.max_budget_usd),
      *(() if self.persist else ("--no-session-persistence",)),
      "--safe-mode",
      "--setting-sources",
      "",
      "--strict-mcp-config",
      "--disable-slash-commands",
      "--restricted",
      "--permission-mode",
      "dontAsk",
      "--tools",
      ",".join(tools),
    ]
    if schema is not None:
      args += ["--json-schema", json.dumps(schema)]
    if allow:
      args += ["--allowedTools", ",".join(allow)]
    for d in add_dirs:
      args += ["--add-dir", str(d)]
    if resume is not None:
      args += ["--resume", resume]
    return args

  def run(
    self,
    name: str,
    prompt: str,
    *,
    run_dir: Path,
    cwd: Path,
    tools: Sequence[str],
    allow: Sequence[str],
    schema: Mapping[str, object] | None,
    add_dirs: Sequence[Path | str] = (),
    resume: str | None = None,
  ) -> Result:
    """Run one session and keep its files in `run_dir`.

    A new session's working directory is made for it, so that nothing in it
    is read as instructions; a resumed one runs in the directory of the run
    it goes on from, where `claude` on this machine finds the session. The
    files are `<name>.prompt.md`, `<name>.trace.jsonl` (stdout),
    `<name>.stderr.log` and `<name>.usage.json` (the result event without
    its result, and `unparsed_lines`, the count of trace lines that are not
    JSON, when there are any).

    Args:
      name: The prefix of the session's files.
      prompt: The prompt.
      run_dir: The directory for the session's files, which exists.
      cwd: The working directory on this machine: one that does not exist
        yet for a new session, the resumed run's for a resumed one.
      tools: The tools the session has.
      allow: The permission rules of the tool uses allowed.
      schema: The JSON Schema of the structured output, or None for none.
      add_dirs: Directories the file tools may read besides the working
        directory.
      resume: The ID of a kept session this one goes on from, or None.

    Returns:
      How the session ended.
    """
    paths = [run_dir / f"{name}.{ext}" for ext in ("prompt.md", "trace.jsonl")]
    paths += [run_dir / f"{name}.stderr.log", run_dir / f"{name}.usage.json"]
    prompt_path, trace_path, stderr_path, usage_path = paths
    _ = prompt_path.write_text(prompt)
    if resume is None:
      cwd.mkdir()
    command = self.args(
      tools=tools, allow=allow, schema=schema, add_dirs=add_dirs, resume=resume
    )
    timed_out = False
    with trace_path.open("wb") as out, stderr_path.open("wb") as err:
      proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=out,
        stderr=err,
        start_new_session=True,
        env=None if self.env is None else dict(self.env),
      )
      try:
        _ = proc.communicate(prompt.encode(), timeout=self.timeout)
      except subprocess.TimeoutExpired:
        timed_out = True
      finally:
        # Whatever the session started and left running goes with it.
        with contextlib.suppress(ProcessLookupError):
          os.killpg(proc.pid, signal.SIGKILL)
        _ = proc.wait()
        if not self.persist:
          remove_project_dir(cwd, self.env)
    trace = (
      Trace({"subtype": "timeout"}) if timed_out else read_trace(trace_path)
    )
    usage = {
      k: v
      for k, v in trace.result.items()
      if k not in ("result", "structured_output")
    } or {"subtype": trace.subtype}
    if trace.unparsed:
      usage["unparsed_lines"] = trace.unparsed
    _ = usage_path.write_text(json.dumps(usage, indent=2) + "\n")
    return Result(
      subtype=trace.subtype,
      session_id=trace.session_id,
      cost_usd=trace.cost_usd,
      output=trace.output,
      text=trace.text,
      rejected=trace.rejected,
      paths=tuple(paths),
    )


def remove_project_dir(cwd: Path, env: Mapping[str, str] | None = None) -> None:
  """Remove what `claude` kept for a working directory under its config.

  The config is `$CLAUDE_CONFIG_DIR`, or `$HOME/.claude` when that is not
  set, as `env` gives them. Only the directory named after `cwd` is removed,
  and only when it is a directory directly in `<config>/projects`, not a
  symlink; `Session.run` makes each new session's working directory new, so
  no other session it started has the name. Whether it is gone is logged.

  Args:
    cwd: The session's working directory.
    env: The environment `claude` ran in, or None for this process's.
  """
  found = os.environ if env is None else env
  home = found.get("HOME")
  config = (
    found.get("CLAUDE_CONFIG_DIR")
    or (Path(home) if home else Path.home()) / ".claude"
  )
  projects = (Path(config) / "projects").resolve()
  name = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd.resolve()))
  target = projects / name
  if (
    len(name) > _MAX_PROJECT_NAME
    or target.is_symlink()
    or os.path.realpath(target) != str(target)
  ):
    _log.warning("left %s: not known to be this session's", target)
    return
  if not target.exists():
    return
  shutil.rmtree(target, ignore_errors=True)
  if target.exists():
    _log.warning("could not remove %s", target)
  else:
    _log.info("removed %s", target)


def read_trace(trace: Path) -> Trace:
  """Read how a session ended from its stream-json trace.

  Args:
    trace: The trace file; one that does not exist reads as an empty trace.

  Returns:
    The trace's last result, its lines that are not JSON and its last
    rejected structured output.
  """
  if not trace.is_file():
    return Trace({})
  found: Mapping[str, object] = {}
  unparsed = 0
  calls: dict[object, object] = {}
  rejected = None
  for line in trace.read_text(errors="replace").splitlines():
    try:
      event = cast(object, json.loads(line))
    except json.JSONDecodeError:
      unparsed += 1
      continue
    if not isinstance(event, dict):
      continue
    event = cast(dict[str, object], event)
    if event.get("type") == "result":
      found = event
    for block in content(event):
      kind = block.get("type")
      if kind == "tool_use" and block.get("name") == "StructuredOutput":
        calls[block.get("id")] = block.get("input")
      elif kind == "tool_result" and block.get("is_error"):
        rejected = calls.get(block.get("tool_use_id"), rejected)
  return Trace(found, unparsed, rejected)


def content(event: Mapping[str, object]) -> list[dict[str, object]]:
  """Return the content blocks of a stream-json event.

  Args:
    event: The decoded event.

  Returns:
    The blocks of an assistant or user event that are objects; none for
    other events.
  """
  message = event.get("message")
  if not isinstance(message, dict):
    return []
  blocks = cast(dict[str, object], message).get("content")
  if not isinstance(blocks, list):
    return []
  return [
    cast(dict[str, object], b)
    for b in cast(list[object], blocks)
    if isinstance(b, dict)
  ]


@dataclass(eq=False, kw_only=True)
class Conversation:
  """One Claude Code session as a `Runner` drives it across its runs.

  It is the `session` of each `AgentRun` the runner returns.

  Attributes:
    cwd: The working directory of its runs on this machine.
    on_host: Whether `claude` runs on this machine, which keeps the session
      under its config until the runner removes it.
    command: The command its runs start, the prefix of its env included.
    tools: The tools of its runs.
    allow: The permission rules of its runs.
    add_dirs: The directories its file tools may read besides the working
      directory.
    schema: The JSON Schema of its answer, or None.
    id: The session's ID once a run printed one; `--resume` takes it.
    subtype: How its last run ended, once one ran.
    total_usd: What the session has cost, in US dollars.
    start_usd: What the runs since the last start have cost.
    number: The number of the last start in the runner.
    fixes: How many times the session was resumed since the last start.
    spec: The agent of the last start.
    task: The task of the last start.
    last: How its last run ended, once one ran.
  """

  cwd: Path
  on_host: bool
  command: tuple[str, ...] = ()
  tools: tuple[str, ...] = ()
  allow: tuple[str, ...] = ()
  add_dirs: tuple[str, ...] = ()
  schema: Mapping[str, object] | None = None
  id: str | None = None
  subtype: str | None = None
  total_usd: float = 0.0
  start_usd: float = 0.0
  number: int = 0
  fixes: int = 0
  spec: AgentSpec | None = None
  task: AgentTask | None = None
  last: Result | None = None

  @property
  def resumable(self) -> bool:
    """Whether the last run ended so that the session can go on."""
    return self.subtype == "success" and self.id is not None


type Files = Callable[[AgentSpec, AgentTask, int, int], Path]
"""Where a run's files go: called with the start's spec and task, the
runner's number of the start, from 1, and the fix's, 0 for the start
itself, it returns the files' path without its suffixes, such as
`<dir>/review` for `<dir>/review.trace.jsonl`."""


def numbered(run_dir: Path, prefix: str = "agent") -> Files:
  """Return the run files of a `Runner` numbered in one directory.

  Args:
    run_dir: The directory.
    prefix: The start of each name.

  Returns:
    `run_dir/<prefix>-<n>` for the n-th start and
    `run_dir/<prefix>-<n>-fix-<k>` for its k-th fix, with the spec's name, when
    it has one, in place of prefix.
  """

  def files(spec: AgentSpec, task: AgentTask, number: int, fix: int) -> Path:
    del task
    start = spec.name or prefix
    name = f"{start}-{number}" + (f"-fix-{fix}" if fix else "")
    return run_dir / name

  return files


class Runner:
  """Claude Code sessions as dataforge's `AgentRunner`.

  A session runs on the `Host` with `session.command`, or in a `Docker`
  environment through `docker exec -i -w <workdir> <container>` followed by
  it, as the container's user; the session is kept where `claude` runs, so
  that a fix of what the validator found resumes it. Its tools are the
  spec's, or the runner's `tools` when the spec names none; `Write` and
  `Edit` are allowed only in the spec's workdir and the task's output
  directory, each other tool by its name. The file tools may read those
  two, the directory of each input, and the runner's `add_dirs`. On the
  host, `Bash` runs as this user with this machine's reach.

  Each start runs on one spend cap, `session.max_budget_usd`, which its
  fixes share: a fix gets what the runs before it left, and does not run
  when less than `min_fix_usd` is left or the session cannot go on. A
  resumed session reports in its cost that of the runs before it, so each
  run's cost is what that total grew by; each is passed to `spent` as soon
  as it ends.

  Where a run's files go is the caller's `files`, such as `numbered`; a new
  session's working directory on this machine is a directory of the path
  `files` gives for its start, without suffixes. `last` is how the runner's
  last run ended, and each `Conversation` keeps its own.

  A timeout kills only what runs on this machine: in `Docker`, the `docker
  exec`, while `claude` in the container goes on until the container stops.

  With `continue_session`, the runner drives one session at a time: each
  start after the first resumes the session of the one before when that
  printed an ID, and starts a new one when it did not. `current` is that
  session; leave a runner of this kind to one attempt, whose states run it
  one at a time.
  """

  def __init__(
    self,
    session: Session,
    files: Files,
    *,
    tools: Sequence[str] = TOOLS,
    add_dirs: Sequence[str] = (),
    continue_session: bool = False,
    min_fix_usd: float = MIN_FIX_USD,
    serve: Callable[[], AbstractContextManager[object]] | None = None,
    spent: Callable[[float], None] | None = None,
  ) -> None:
    """Make the runner.

    Args:
      session: How to start a session; its command runs `claude` where the
        environment runs it, and it is kept whatever its `persist` says.
      files: Where each run's files go.
      tools: The tools of an agent whose spec names none.
      add_dirs: More directories, as the environment sees them, that the
        file tools may read, such as those mounted into a container.
      continue_session: Whether each start resumes the session of the one
        before.
      min_fix_usd: The least of a start's cap that a fix runs on.
      serve: Entered around each run of `claude`, for what the dataset
        serves while one runs, such as a gateway.
      spent: Called with each run's cost.
    """
    self.session: Session = dataclasses.replace(session, persist=True)
    self.files: Files = files
    self.tools: tuple[str, ...] = tuple(tools)
    self.add_dirs: tuple[str, ...] = tuple(add_dirs)
    self.continue_session: bool = continue_session
    self.min_fix_usd: float = min_fix_usd
    self.current: Conversation | None = None
    self.last: Result | None = None
    self._serve: Callable[[], AbstractContextManager[object]] = (
      serve or nullcontext
    )
    self._spent: Callable[[float], None] = spent or (lambda cost_usd: None)
    self._lock: threading.Lock = threading.Lock()
    self._starts: int = 0

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    """Start a session, or resume the current one with `continue_session`.

    It raises `TypeError` when env is neither `Host` nor `Docker`, and
    `RuntimeError` when the `Docker` container has not been created.

    Args:
      spec: The agent.
      task: What it is to do, with its paths in env.
      env: Where it runs, started.

    Returns:
      The run.

    Raises:
      ValueError: When an agent on the host is given a relative path.
    """
    command, on_host = self._command(spec, env)
    dirs = [str(spec.workdir), task.outdir]
    inputs = [str(PurePosixPath(path).parent) for path in task.inputs.values()]
    paths = [*dirs, *inputs, *self.add_dirs]
    if on_host and (
      relative := [p for p in paths if not Path(p).is_absolute()]
    ):
      raise ValueError(f"an agent on the host needs absolute paths: {relative}")
    with self._lock:
      self._starts += 1
      number = self._starts
      conversation = self.current if self.continue_session else None
      path = self.files(spec, task, number, 0)
      if conversation is None or conversation.id is None:
        if conversation is not None and conversation.on_host:
          remove_project_dir(conversation.cwd, self.session.env)
        conversation = Conversation(cwd=path, on_host=on_host)
        if self.continue_session:
          self.current = conversation
    tools = self.tools if spec.tools is None else tuple(spec.tools)
    conversation.command = command
    conversation.tools = tools
    conversation.allow = tuple(_allow(tools, dirs))
    conversation.add_dirs = tuple(dict.fromkeys(paths))
    conversation.schema = spec.output_schema
    conversation.number, conversation.fixes = number, 0
    conversation.start_usd = 0.0
    conversation.spec, conversation.task = spec, task
    return self._run(
      conversation, path, task.prompt, self.session.max_budget_usd
    )

  def resume(self, session: object, prompt: str) -> AgentRun:
    """Resume a session to fix what the validator found.

    Args:
      session: The `Conversation` of an earlier run.
      prompt: The prompt.

    Returns:
      The run, or one that did not run when the session cannot go on or its
      start's cap has too little left.

    Raises:
      TypeError: When session is not a `Conversation`.
    """
    if not isinstance(session, Conversation):
      raise TypeError(f"not a session this runner started: {session!r}")
    if session.spec is None or session.task is None:
      raise TypeError(f"a session that never started: {session!r}")
    session.fixes += 1
    left = self.session.max_budget_usd - session.start_usd
    if not session.resumable or left < self.min_fix_usd:
      why = (
        "the session cannot be resumed"
        if not session.resumable
        else f"${left:.2f} of the start's cap is left"
      )
      return AgentRun(
        session,
        "ok",
        0.0,
        None,
        f"no fix run: {why}",
        session.subtype,
        session.resumable,
      )
    path = self.files(session.spec, session.task, session.number, session.fixes)
    return self._run(session, path, prompt, left)

  def checkpoint(self, session: object) -> Data:
    """Return what restore needs of a run's session, as JSON data.

    Args:
      session: The `Conversation` of the run.

    Returns:
      The session's ID, what it has cost, the number of its start, whether
      it runs on this machine and its working directory there.

    Raises:
      TypeError: When session is not a `Conversation`.
    """
    if not isinstance(session, Conversation):
      raise TypeError(f"not a session this runner started: {session!r}")
    return {
      "id": session.id,
      "total_usd": session.total_usd,
      "number": session.number,
      "on_host": session.on_host,
      "cwd": str(session.cwd),
    }

  def restore(self, points: Sequence[Data]) -> None:
    """Go on as the runner whose runs checkpoint gave points would.

    Starts are numbered on after the highest start number of points, so that
    no checkpointed run's files are written over; the files of a later run,
    such as one killed before it ended, are, unless the caller moves them.
    With `continue_session`, `current` becomes the session of that start,
    with the total it had then, so that the next start resumes it and counts
    as its cost only what the session's total grows by: a run killed before
    its result added nothing to that total.

    Args:
      points: What checkpoint gave, for the runs of an attempt.
    """
    if not points:
      return
    latest = max((_conversation(point) for point in points), key=_number)
    with self._lock:
      self._starts = max(self._starts, latest.number)
      if self.continue_session:
        self.current = latest

  def close(self, session: object) -> None:
    """Remove what a session left on this machine, unless it goes on.

    Args:
      session: The `Conversation` of the action's last run.
    """
    if (
      isinstance(session, Conversation)
      and session.on_host
      and not self.continue_session
    ):
      remove_project_dir(session.cwd, self.session.env)

  def end(self) -> None:
    """Remove what the current session left on this machine."""
    with self._lock:
      current, self.current = self.current, None
    if current is not None and current.on_host:
      remove_project_dir(current.cwd, self.session.env)

  def _command(self, spec: AgentSpec, env: Env) -> tuple[tuple[str, ...], bool]:
    # The command that runs `claude` in env, and whether that is here.
    if isinstance(env, Host):
      return self.session.command, True
    if isinstance(env, Docker):
      container = env.container
      if container is None:
        raise RuntimeError("the container has not been created")
      prefix = ("docker", "exec", "-i", "-w", str(spec.workdir), container)
      return (*prefix, *self.session.command), False
    raise TypeError(f"a claude.Runner runs on Host or Docker, not {env!r}")

  def _run(
    self, conversation: Conversation, path: Path, prompt: str, cap: float
  ) -> AgentRun:
    path.parent.mkdir(parents=True, exist_ok=True)
    session = dataclasses.replace(
      self.session,
      command=conversation.command,
      max_budget_usd=math.floor(round(cap * 100, 6)) / 100,
    )
    with self._serve():
      result = session.run(
        path.name,
        prompt,
        run_dir=path.parent,
        cwd=conversation.cwd,
        tools=conversation.tools,
        allow=conversation.allow,
        schema=conversation.schema,
        add_dirs=conversation.add_dirs,
        resume=conversation.id,
      )
    conversation.id = result.session_id
    conversation.subtype = result.subtype
    conversation.last = self.last = result
    spent = 0.0
    if result.cost_usd is not None:
      spent = max(result.cost_usd - conversation.total_usd, 0.0)
      conversation.total_usd = max(conversation.total_usd, result.cost_usd)
    conversation.start_usd += spent
    self._spent(spent)
    status = "failed" if result.subtype in FAILED_ENDINGS else "ok"
    answer = result.text if result.text is not None else result.subtype
    if conversation.schema is not None:
      if result.output is None:
        status = "failed"
        answer = f"no structured output: {result.subtype}"
        if result.rejected is not None:
          answer += f"; the last one rejected: {json.dumps(result.rejected)}"
      else:
        answer = json.dumps(result.output)
    return AgentRun(
      conversation,
      status,
      spent,
      result.trace,
      answer,
      result.subtype,
      conversation.resumable,
      TRACE_FORMAT,
    )


def _conversation(point: Data) -> Conversation:
  """Read back a session that `Runner.checkpoint` gave.

  Args:
    point: What checkpoint gave.

  Returns:
    The session, as far as a later start needs it.

  Raises:
    ValueError: When a field is missing or has the wrong type.
  """
  id_, total, number = (
    point.get("id"),
    point.get("total_usd"),
    point.get("number"),
  )
  on_host, cwd = point.get("on_host"), point.get("cwd")
  if (
    not (id_ is None or isinstance(id_, str))
    or isinstance(total, bool)
    or not isinstance(total, int | float)
    or isinstance(number, bool)
    or not isinstance(number, int)
    or not isinstance(on_host, bool)
    or not isinstance(cwd, str)
  ):
    raise ValueError(f"not a checkpoint of a claude.Runner: {point!r}")
  return Conversation(
    cwd=Path(cwd), on_host=on_host, id=id_, total_usd=total, number=number
  )


def _number(conversation: Conversation) -> int:
  return conversation.number


def _allow(tools: Sequence[str], dirs: Sequence[str]) -> list[str]:
  # An absolute pattern (`//` and the path), which a `cd` cannot move.
  allow: list[str] = []
  for tool in tools:
    if tool in _SCOPED_TOOLS:
      allow += [f"{tool}(/{d.rstrip('/')}/**)" for d in dirs]
    else:
      allow.append(tool)
  return allow
