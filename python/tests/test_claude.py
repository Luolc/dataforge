# pyright: reportAny=false, reportUnusedParameter=false
# The tests read the JSON the fakes write; the state's run takes a context
# it may not need.

"""The Claude Code runtime against a fake `claude` and a fake `docker`.

The fake `claude` reads its plan, a JSON list with one entry per call, from
`$FAKE_PLAN`, and records each call, its argv, working directory and prompt,
as `call-<n>.json` in `$FAKE_LOG`. An entry gives the stream-json `lines` it
prints (objects as JSON, strings as they are), `write`, files it writes into
the output directory the first prompt named, `project`, whether it fills its
working directory's directory under `$CLAUDE_CONFIG_DIR/projects`, and
`hang`, whether it starts a child, writes its pid and sleeps, and `echo`,
whether it ends in success with the ID of the session it resumes, or of a
new one, `s-` and the prompt's first word. Calls are numbered in the order
they start, also when several run at once.
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from typing import cast, override

import pytest

from dataforge.curation import (
  ActionResult,
  Actions,
  AgentRun,
  AgentSpec,
  AgentTask,
  Attempt,
  CommandResult,
  Context,
  Docker,
  End,
  fan_out,
  HOST,
  Machine,
  Outcome,
  ResourcePool,
  Review,
  RubricState,
  State,
)
from dataforge.runtime import claude
from tests.sigterm import sigterm_once

FAKE_CLAUDE = """\
import json, os, pathlib, re, subprocess, sys, time
log = pathlib.Path(os.environ["FAKE_LOG"])
prompt = sys.stdin.read()
cwd = os.getcwd()
n = 1
while True:
  try:
    fd = os.open(log / f"call-{n}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    break
  except FileExistsError:
    n += 1
with os.fdopen(fd, "w") as call:
  call.write(json.dumps({"argv": sys.argv[1:], "cwd": cwd, "prompt": prompt}))
step = json.loads(pathlib.Path(os.environ["FAKE_PLAN"]).read_text())[n - 1]
found = re.search(r"Write your outputs into (\\S+), named", prompt)
if found:
  (log / "outdir").write_text(found.group(1))
for name, text in step.get("write", {}).items():
  (pathlib.Path((log / "outdir").read_text()) / name).write_text(text)
if step.get("project"):
  name = "".join(c if c.isascii() and c.isalnum() else "-" for c in cwd)
  project = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / name
  project.mkdir(parents=True, exist_ok=True)
  (project / "session.jsonl").write_text("{}")
for line in step.get("lines", []):
  print(line if isinstance(line, str) else json.dumps(line), flush=True)
if step.get("echo"):
  args = sys.argv[1:]
  resumed = args[args.index("--resume") + 1] if "--resume" in args else None
  session = resumed or "s-" + prompt.split()[0]
  ended = {"type": "result", "subtype": "success", "session_id": session}
  print(json.dumps(ended), flush=True)
if step.get("hang"):
  child = subprocess.Popen(["sleep", "60"])
  (log / "pids").write_text(f"{child.pid}")
  time.sleep(60)
"""

# Runs what follows `docker exec -i -w <dir> <container>` and records the
# prefix.
FAKE_DOCKER = """\
#!/bin/sh
echo "$@" > "$FAKE_LOG/docker"
shift 5
exec "$@"
"""


def result(**fields: object) -> dict[str, object]:
  return {"type": "result", "subtype": "success", **fields}


class Fake:
  """The fake `claude` with its plan, log and config, and a session of it."""

  def __init__(self, tmp_path: Path, plan: list[dict[str, object]]) -> None:
    """Write the fakes.

    Args:
      tmp_path: The test's directory.
      plan: One entry per call of the fake `claude`.
    """
    self.log: Path = tmp_path / "log"
    self.log.mkdir()
    self.config: Path = tmp_path / "config"
    script = tmp_path / "fake_claude.py"
    _ = script.write_text(FAKE_CLAUDE)
    plan_path = tmp_path / "plan.json"
    _ = plan_path.write_text(json.dumps(plan))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    _ = docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    self.env: dict[str, str] = {
      "PATH": f"{bin_dir}:{os.environ['PATH']}",
      "HOME": str(tmp_path / "home"),
      "CLAUDE_CONFIG_DIR": str(self.config),
      "FAKE_LOG": str(self.log),
      "FAKE_PLAN": str(plan_path),
    }
    self.session: claude.Session = claude.Session(
      command=(sys.executable, str(script)),
      model="m",
      effort="low",
      max_budget_usd=1.0,
      env=self.env,
    )

  def calls(self) -> list[dict[str, object]]:
    count = len(list(self.log.glob("call-*.json")))
    return [
      json.loads((self.log / f"call-{n}.json").read_text())
      for n in range(1, count + 1)
    ]

  def argv(self, n: int) -> list[str]:
    found = self.calls()[n - 1]["argv"]
    assert isinstance(found, list)
    return [str(arg) for arg in cast(list[object], found)]

  def projects(self) -> list[Path]:
    root = self.config / "projects"
    return sorted(root.iterdir()) if root.exists() else []


def option(argv: list[str], name: str) -> list[str]:
  return [argv[i + 1] for i, arg in enumerate(argv) if arg == name]


class Once(State):
  def __init__(self, body: Callable[[Context], ActionResult]) -> None:
    super().__init__("a", {"ok": End("ok")})
    self.body: Callable[[Context], ActionResult] = body
    self.results: list[ActionResult] = []

  @override
  def run(self, ctx: Context) -> Outcome:
    self.results.append(self.body(ctx))
    return Outcome("ok")


def act(root: Path, body: Callable[[Context], ActionResult]) -> ActionResult:
  state = Once(body)
  assert Machine([state]).run(root).status == "ok"
  return state.results[0]


def validator(tmp_path: Path) -> Path:
  path = tmp_path / "check.sh"
  _ = path.write_text('#!/bin/sh\ngrep -q valid "$1/task"\n')
  path.chmod(0o755)
  return path


def test_a_session_runs_safe_and_keeps_its_files(tmp_path: Path) -> None:
  rejected = {"verdict": "maybe"}
  fake = Fake(
    tmp_path,
    [
      {
        "project": True,
        "lines": [
          "not json",
          {
            "type": "assistant",
            "message": {
              "content": [
                {
                  "type": "tool_use",
                  "id": "t1",
                  "name": "StructuredOutput",
                  "input": rejected,
                }
              ]
            },
          },
          {
            "type": "user",
            "message": {
              "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True}
              ]
            },
          },
          result(
            session_id="s1",
            total_cost_usd=0.5,
            num_turns=3,
            result="done",
            structured_output={"verdict": "pass"},
          ),
        ],
      }
    ],
  )
  run_dir = tmp_path / "runs"
  run_dir.mkdir()
  cwd = run_dir / "review"
  found = fake.session.run(
    "review",
    "Judge.",
    run_dir=run_dir,
    cwd=cwd,
    tools=("Read",),
    allow=("Read",),
    schema={"type": "object"},
    add_dirs=[tmp_path / "in"],
  )

  assert found.subtype == "success"
  assert found.session_id == "s1"
  assert found.cost_usd == 0.5
  assert found.output == {"verdict": "pass"}
  assert found.text == "done"
  assert found.rejected == rejected
  assert found.trace == run_dir / "review.trace.jsonl"
  assert (run_dir / "review.prompt.md").read_text() == "Judge."
  usage = json.loads((run_dir / "review.usage.json").read_text())
  assert usage["num_turns"] == 3 and usage["unparsed_lines"] == 1
  assert "result" not in usage and "structured_output" not in usage
  argv = fake.argv(1)
  assert fake.calls()[0]["cwd"] == str(cwd)
  for flag in (
    "--safe-mode",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--restricted",
    "--no-session-persistence",
  ):
    assert flag in argv
  assert option(argv, "--setting-sources") == [""]
  assert option(argv, "--permission-mode") == ["dontAsk"]
  assert option(argv, "--max-budget-usd") == ["1.0"]
  assert option(argv, "--json-schema") == ['{"type": "object"}']
  assert option(argv, "--add-dir") == [str(tmp_path / "in")]
  assert fake.projects() == []


def test_a_kept_session_leaves_its_project_dir_to_the_caller(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"project": True, "lines": [result()]}])
  cwd = tmp_path / "work"
  kept = replace(fake.session, persist=True)
  _ = kept.run(
    "s",
    "Go.",
    run_dir=tmp_path,
    cwd=cwd,
    tools=(),
    allow=(),
    schema=None,
  )
  assert "--no-session-persistence" not in fake.argv(1)
  assert len(fake.projects()) == 1
  claude.remove_project_dir(cwd, fake.env)
  assert fake.projects() == []


def test_a_session_past_its_timeout_is_killed_with_its_children(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"hang": True}])
  quick = replace(fake.session, timeout=1.0)
  found = quick.run(
    "s", "Go.", run_dir=tmp_path, cwd=tmp_path / "w", tools=(), allow=(),
    schema=None,
  )  # fmt: skip
  assert found.subtype == "timeout"
  child = int((fake.log / "pids").read_text())
  deadline = time.monotonic() + 5
  while time.monotonic() < deadline:
    try:
      os.kill(child, 0)
    except ProcessLookupError:
      break
    time.sleep(0.05)
  else:
    os.kill(child, signal.SIGKILL)
    pytest.fail("the session's child outlived the timeout")


def test_a_sigterm_during_a_fan_out_kills_its_sessions_with_their_children(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"hang": True}])
  pids = fake.log / "pids"

  def run() -> claude.Result:
    return fake.session.run(
      "s", "Go.", run_dir=tmp_path, cwd=tmp_path / "w", tools=(), allow=(),
      schema=None,
    )  # fmt: skip

  with sigterm_once(pids.exists) as sent:
    with pytest.raises(SystemExit):
      _ = fan_out([run])
    ended = time.monotonic()
  assert ended - sent[0] < 5
  child = int(pids.read_text())
  deadline = time.monotonic() + 5
  while time.monotonic() < deadline:
    try:
      os.kill(child, 0)
    except ProcessLookupError:
      break
    time.sleep(0.05)
  else:
    os.kill(child, signal.SIGKILL)
    pytest.fail("the session's child outlived the stopped fan_out")


def test_a_sigterm_during_a_fan_out_starts_no_session_in_a_call_that_waits(
  tmp_path: Path,
) -> None:
  # Two agents in containers whose memory only one at a time fits in the pool:
  # one runs a session that hangs, the other waits for the memory.
  fake = Fake(tmp_path, [{"hang": True}, {"echo": True}])
  runner = claude.Runner(fake.session, claude.numbered(tmp_path / "runs"))
  pool = ResourcePool(0.5)
  calls: dict[int, list[list[str]]] = {0: [], 1: []}

  def cli(n: int) -> Callable[[Sequence[str]], CommandResult]:
    def run(args: Sequence[str]) -> CommandResult:
      calls[n].append(list(args))
      return CommandResult(0, "c\n")

    return run

  def agent(env: Docker) -> Callable[[], AgentRun]:
    def run() -> AgentRun:
      env.start(pool)
      try:
        task = AgentTask("Write.", {}, "/out", None)
        return runner.start(AgentSpec("Write.", Path("/app")), task, env)
      finally:
        env.stop()

    return run

  envs = [Docker("agent:1", memory_gb=0.5, cli=cli(n)) for n in (0, 1)]
  with envs[0], envs[1], sigterm_once((fake.log / "pids").exists) as sent:
    with pytest.raises(SystemExit):
      _ = fan_out([agent(env) for env in envs])
    ended = time.monotonic()
  assert ended - sent[0] < 5
  assert len(fake.calls()) == 1
  ran, waited = sorted(calls.values(), key=len, reverse=True)
  assert [args[0] for args in ran] == ["create", "start", "stop", "rm"]
  assert waited == []


def test_a_missing_or_resultless_trace_reads_as_no_result(
  tmp_path: Path,
) -> None:
  assert claude.read_trace(tmp_path / "none.jsonl").subtype == "no result"
  trace = tmp_path / "t.jsonl"
  _ = trace.write_text('{"type": "system"}\n[1]\n')
  found = claude.read_trace(trace)
  assert found.result == {} and found.subtype == "no result"
  assert found.num_turns is None and found.cost_usd is None


def test_the_runner_fixes_on_the_hosts_validator_within_one_cap(
  tmp_path: Path,
) -> None:
  fake = Fake(
    tmp_path,
    [
      {
        "write": {"task": "draft"},
        "project": True,
        "lines": [result(session_id="s1", total_cost_usd=0.5)],
      },
      {
        "write": {"task": "still a draft"},
        "lines": [result(session_id="s1", total_cost_usd=0.6)],
      },
    ],
  )
  spent: list[float] = []
  runner = claude.Runner(
    fake.session,
    claude.numbered(tmp_path / "runs"),
    min_fix_usd=0.45,
    spent=spent.append,
  )
  workdir = tmp_path / "work"
  workdir.mkdir()
  spec = AgentSpec(
    "Write.",
    workdir,
    outputs=["task"],
    validator=validator(tmp_path),
    max_fixes=2,
  )

  found = act(
    tmp_path / "attempt", lambda ctx: Actions(runner).agent(ctx, spec, HOST)
  )

  assert found.status == "failed"
  assert found.cost_usd == pytest.approx(0.6)
  assert spent == pytest.approx([0.5, 0.1])
  assert "no fix run: $0.40 of the start's cap is left" in found.log
  assert len(fake.calls()) == 2
  first, fix = fake.argv(1), fake.argv(2)
  outdir = tmp_path / "attempt" / "artifacts" / "a" / "1"
  assert option(first, "--allowedTools") == [
    f"Read,Glob,Grep,Write(/{workdir}/**),Write(/{outdir}/**),"
    + f"Edit(/{workdir}/**),Edit(/{outdir}/**),Bash"
  ]
  assert option(first, "--add-dir") == [str(workdir), str(outdir)]
  assert option(first, "--resume") == []
  assert option(fix, "--resume") == ["s1"]
  assert option(fix, "--max-budget-usd") == ["0.5"]
  assert "The validator failed" in str(fake.calls()[1]["prompt"])
  runs = tmp_path / "runs"
  assert fake.calls()[1]["cwd"] == str(runs / "agent-1")
  assert (runs / "agent-1.trace.jsonl").exists()
  assert (runs / "agent-1-fix-1.trace.jsonl").exists()
  assert runner.last is not None and runner.last.cost_usd == 0.6
  assert fake.projects() == []


def test_a_session_that_cannot_go_on_runs_no_fix(tmp_path: Path) -> None:
  fake = Fake(
    tmp_path,
    [
      {
        "write": {"task": "draft"},
        "lines": [result()],
      }
    ],
  )
  runner = claude.Runner(fake.session, claude.numbered(tmp_path / "runs"))
  spec = AgentSpec(
    "Write.", tmp_path, outputs=["task"], validator=validator(tmp_path)
  )
  found = act(
    tmp_path / "attempt", lambda ctx: Actions(runner).agent(ctx, spec, HOST)
  )
  assert found.status == "failed"
  assert "no fix run: the session cannot be resumed" in found.log
  assert len(fake.calls()) == 1
  assert found.run is not None
  assert (found.run.ending, found.run.resumable) == (
    "success",
    False,
  )


def test_a_judges_structured_output_is_its_value(tmp_path: Path) -> None:
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(structured_output={"score": 1})]},
      {
        "lines": [
          {
            "type": "assistant",
            "message": {
              "content": [
                {
                  "type": "tool_use",
                  "id": "t1",
                  "name": "StructuredOutput",
                  "input": {"score": "high"},
                }
              ]
            },
          },
          {
            "type": "user",
            "message": {
              "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True}
              ]
            },
          },
          result(result="gave up"),
        ]
      },
    ],
  )

  def files(spec: AgentSpec, task: AgentTask, number: int, fix: int) -> Path:
    return Path(task.outdir) / f"judge-{number}"

  runner = claude.Runner(fake.session, files)
  spec = AgentSpec(
    "Judge.", tmp_path, tools=(), output_schema={"type": "object"}
  )

  def body(ctx: Context) -> ActionResult:
    actions = Actions(runner)
    first = actions.agent(ctx, spec, HOST)
    assert first.ok and first.value == {"score": 1}
    task = AgentTask("Judge.", {}, str(ctx.outdir), None)
    second = runner.start(spec, task, HOST)
    assert second.status == "failed"
    assert second.result == (
      "no structured output: success;"
      + ' the last one rejected: {"score": "high"}'
    )
    return first

  _ = act(tmp_path / "attempt", body)
  argv = fake.argv(1)
  assert option(argv, "--tools") == [""]
  assert option(argv, "--allowedTools") == []
  assert option(argv, "--json-schema") == ['{"type": "object"}']
  outdir = tmp_path / "attempt" / "artifacts" / "a" / "1"
  assert (outdir / "judge-1.trace.jsonl").exists()
  assert (outdir / "judge-2.trace.jsonl").exists()


def test_an_agent_in_docker_runs_through_docker_exec(tmp_path: Path) -> None:
  fake = Fake(tmp_path, [{"lines": [result(session_id="s1")]}])

  def cli(args: object) -> CommandResult:
    return CommandResult(0, "c1\n")

  runner = claude.Runner(
    fake.session, claude.numbered(tmp_path / "runs"), add_dirs=["/input"]
  )
  with Docker("agent:1", cli=cli) as env:
    env.start(None)
    task = AgentTask("Write.", {"src": "/in/src.tar"}, "/out", None)
    found = runner.start(AgentSpec("Write.", Path("/app")), task, env)
    env.stop()

  assert found.status == "ok"
  assert (fake.log / "docker").read_text().split()[:5] == [
    "exec",
    "-i",
    "-w",
    "/app",
    "c1",
  ]
  argv = fake.argv(1)
  assert option(argv, "--add-dir") == ["/app", "/out", "/in", "/input"]
  assert "Write(//app/**)" in option(argv, "--allowedTools")[0]


def test_an_agent_on_the_host_needs_absolute_paths(tmp_path: Path) -> None:
  fake = Fake(tmp_path, [])
  runner = claude.Runner(fake.session, claude.numbered(tmp_path))
  task = AgentTask("Write.", {}, "out", None)
  with pytest.raises(ValueError, match="absolute"):
    _ = runner.start(AgentSpec("Write.", tmp_path), task, HOST)
  assert fake.calls() == []


def test_a_continued_session_is_resumed_by_each_start(tmp_path: Path) -> None:
  fake = Fake(
    tmp_path,
    [
      {"project": True, "lines": [result(session_id="s1", total_cost_usd=1)]},
      {"lines": [result(session_id="s1", total_cost_usd=1.5)]},
    ],
  )
  runner = claude.Runner(
    fake.session,
    claude.numbered(tmp_path / "runs", "session"),
    continue_session=True,
  )
  spec = AgentSpec("Write.", tmp_path)

  def body(ctx: Context) -> ActionResult:
    actions = Actions(runner)
    _ = actions.agent(ctx, spec, HOST)
    return actions.agent(ctx, spec, HOST)

  found = act(tmp_path / "attempt", body)

  assert found.ok and found.cost_usd == pytest.approx(0.5)
  assert option(fake.argv(2), "--resume") == ["s1"]
  runs = tmp_path / "runs"
  assert fake.calls()[1]["cwd"] == str(runs / "session-1")
  assert (runs / "session-2.trace.jsonl").exists()
  assert runner.current is not None and runner.current.total_usd == 1.5
  assert len(fake.projects()) == 1
  runner.end()
  assert fake.projects() == []
  assert runner.current is None


def test_serve_spans_each_run(tmp_path: Path) -> None:
  fake = Fake(tmp_path, [{"lines": [result()]}])
  seen: list[str] = []

  class Serve:
    def __enter__(self) -> None:
      seen.append("enter")

    def __exit__(self, *args: object) -> None:
      seen.append(f"exit after {len(fake.calls())} calls")

  runner = claude.Runner(
    fake.session, claude.numbered(tmp_path / "runs"), serve=Serve
  )
  task = AgentTask("Go.", {}, str(tmp_path), None)
  _ = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert seen == ["enter", "exit after 1 calls"]


@pytest.mark.parametrize(
  ("subtype", "fault"),
  [
    ("error_during_execution", "infra"),
    (None, "infra"),
    ("error_max_turns", "budget"),
    ("error_max_budget_usd", "budget"),
    ("error_max_structured_output_retries", "model"),
  ],
)
def test_a_run_fails_on_an_ending_with_a_fault(
  tmp_path: Path, subtype: str | None, fault: str
) -> None:
  ended = {**result(session_id="s1", is_error=True), "subtype": subtype}
  fake = Fake(tmp_path, [{"lines": [] if subtype is None else [ended]}])
  runner = claude.Runner(fake.session, claude.numbered(tmp_path / "runs"))
  task = AgentTask("Go.", {}, str(tmp_path), None)
  found = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert (found.status, found.fault) == ("failed", fault)
  assert found.ending == (subtype or "no result")
  assert found.resumable is False


def test_an_api_error_that_ends_in_success_fails_and_is_not_resumed(
  tmp_path: Path,
) -> None:
  # How Claude Code 2.1.296 ends a turn the model's safeguards refused.
  refused = result(
    session_id="s1",
    is_error=True,
    stop_reason="refusal",
    total_cost_usd=0.5,
    result="API Error: the safeguards flagged this message. " + "x" * 600,
  )
  fake = Fake(
    tmp_path,
    [
      {"write": {"task": "draft"}, "lines": [refused]},
      {"lines": [result(session_id="s2")]},
    ],
  )
  runner = claude.Runner(
    fake.session, claude.numbered(tmp_path / "runs"), continue_session=True
  )
  spec = AgentSpec(
    "Write.", tmp_path, outputs=["task"], validator=validator(tmp_path)
  )
  found = act(
    tmp_path / "attempt", lambda ctx: Actions(runner).agent(ctx, spec, HOST)
  )
  assert found.status == "failed"
  assert found.validation is None
  assert found.run is not None
  assert (found.run.status, found.run.fault, found.run.stop_reason) == (
    "failed",
    "model",
    "refusal",
  )
  assert found.run.error is not None
  assert found.run.error.startswith("API Error: the safeguards flagged")
  assert len(found.run.error) == claude.ERROR_LIMIT
  assert found.run.resumable is False
  task = AgentTask("Go.", {}, str(tmp_path), None)
  _ = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert len(fake.calls()) == 2
  assert option(fake.argv(2), "--resume") == []


def test_a_run_killed_on_its_timeout_fails_and_keeps_what_it_printed(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"lines": ["not json"], "hang": True}])
  quick = replace(fake.session, timeout=1.0)
  runner = claude.Runner(quick, claude.numbered(tmp_path / "runs"))
  spec = AgentSpec("Go.", tmp_path)
  state = Once(lambda ctx: Actions(runner).agent(ctx, spec, HOST))
  root = tmp_path / "attempt"
  attempt = Machine([state]).run(root)
  found = state.results[0].run
  assert found is not None
  assert (found.status, found.ending, found.fault) == (
    "failed",
    "timeout",
    "timeout",
  )
  assert found.cost_usd is None
  assert found.resumable is False
  usage = json.loads((tmp_path / "runs/agent-1.usage.json").read_text())
  assert usage == {"subtype": "timeout", "unparsed_lines": 1}
  read = Attempt.from_json(root, attempt.to_json())
  assert [a.cost_known for a in read.visits[0].actions] == [False]


def test_a_run_that_printed_its_result_before_the_kill_ends_as_it_says(
  tmp_path: Path,
) -> None:
  ended = result(session_id="s1", total_cost_usd=0.5)
  fake = Fake(tmp_path, [{"lines": [ended], "hang": True}])
  quick = replace(fake.session, timeout=1.0)
  runner = claude.Runner(quick, claude.numbered(tmp_path / "runs"))
  task = AgentTask("Go.", {}, str(tmp_path), None)
  found = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert (found.status, found.ending, found.cost_usd) == ("ok", "success", 0.5)


def test_a_run_tells_its_ending_and_a_named_spec_names_its_files(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"lines": [result(session_id="s1")]}])
  runs = tmp_path / "runs"
  runner = claude.Runner(fake.session, claude.numbered(runs))
  task = AgentTask("Go.", {}, str(tmp_path), None)
  spec = AgentSpec("Go.", tmp_path, name="review")
  found = runner.start(spec, task, HOST)
  assert (found.ending, found.resumable) == ("success", True)
  assert (runs / "review-1.trace.jsonl").exists()


def test_each_continued_start_has_a_cap_of_its_own(tmp_path: Path) -> None:
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(session_id="s1", total_cost_usd=0.8)]},
      {
        "write": {"task": "draft"},
        "lines": [result(session_id="s1", total_cost_usd=1.0)],
      },
      {
        "write": {"task": "valid"},
        "lines": [result(session_id="s1", total_cost_usd=1.1)],
      },
    ],
  )
  runner = claude.Runner(
    fake.session, claude.numbered(tmp_path / "runs"), continue_session=True
  )
  plain = AgentSpec("Plan.", tmp_path)
  checked = AgentSpec(
    "Write.", tmp_path, outputs=["task"], validator=validator(tmp_path)
  )

  def body(ctx: Context) -> ActionResult:
    actions = Actions(runner)
    _ = actions.agent(ctx, plain, HOST)
    return actions.agent(ctx, checked, HOST)

  found = act(tmp_path / "attempt", body)

  assert found.ok and found.cost_usd == pytest.approx(0.3)
  assert option(fake.argv(3), "--max-budget-usd") == ["0.8"]


def test_a_continued_session_without_an_id_is_replaced_and_removed(
  tmp_path: Path,
) -> None:
  fake = Fake(
    tmp_path,
    [
      {"project": True, "lines": ["not json"]},
      {"lines": [result(session_id="s2")]},
    ],
  )
  runs = tmp_path / "runs"
  runner = claude.Runner(
    fake.session, claude.numbered(runs), continue_session=True
  )
  spec = AgentSpec("Go.", tmp_path)
  task = AgentTask("Go.", {}, str(tmp_path), None)
  first = runner.start(spec, task, HOST)
  assert first.status == "failed" and len(fake.projects()) == 1
  second = runner.start(spec, task, HOST)
  assert second.status == "ok"
  assert option(fake.argv(2), "--resume") == []
  assert fake.calls()[1]["cwd"] == str(runs / "agent-2")
  assert fake.projects() == []
  assert runner.current is second.session


def test_the_cap_is_cut_to_whole_cents_below_it(tmp_path: Path) -> None:
  fake = Fake(tmp_path, [{"lines": [result()]}])
  session = replace(fake.session, max_budget_usd=1.13)
  runner = claude.Runner(session, claude.numbered(tmp_path / "runs"))
  task = AgentTask("Go.", {}, str(tmp_path), None)
  _ = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert option(fake.argv(1), "--max-budget-usd") == ["1.13"]


def test_the_options_give_the_session() -> None:
  parser = argparse.ArgumentParser()
  claude.add_arguments(parser, model="m", effort="low", max_budget_usd=0.5)
  default = claude.Session.from_args(parser.parse_args([]))
  assert default == claude.Session(
    command=("claude",), model="m", effort="low", max_budget_usd=0.5
  )
  shim = parser.parse_args(
    ["--claude", "/opt/shim/claude --x", "--timeout", "9"]
  )
  assert claude.Session.from_args(shim).command == ("/opt/shim/claude", "--x")
  assert claude.Session.from_args(shim).timeout == 9


def test_the_command_line_names_the_thinking_display(tmp_path: Path) -> None:
  fake = Fake(tmp_path, [{"lines": [result()]}, {"lines": [result()]}])
  task = AgentTask("Go.", {}, str(tmp_path), None)
  for n, thinking in enumerate((None, "highlights")):
    session = fake.session
    if thinking is not None:
      session = replace(session, thinking_display=thinking)
    runner = claude.Runner(session, claude.numbered(tmp_path / f"runs{n}"))
    _ = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert option(fake.argv(1), "--thinking-display") == ["summarized"]
  assert option(fake.argv(2), "--thinking-display") == ["highlights"]


def test_a_session_rejects_an_unknown_thinking_display() -> None:
  for value in claude.THINKING_DISPLAYS:
    session = claude.Session(
      model="m", effort="low", max_budget_usd=1, thinking_display=value
    )
    assert session.thinking_display == value
  with pytest.raises(ValueError, match="unknown thinking_display"):
    _ = claude.Session(
      model="m", effort="low", max_budget_usd=1, thinking_display="bogus"
    )


def test_the_thinking_display_option_takes_only_a_known_value(
  capsys: pytest.CaptureFixture[str],
) -> None:
  parser = argparse.ArgumentParser()
  claude.add_arguments(parser, model="m", effort="low", max_budget_usd=0.5)
  assert parser.parse_args([]).thinking_display == "summarized"
  for value in claude.THINKING_DISPLAYS:
    args = parser.parse_args(["--thinking-display", value])
    assert claude.Session.from_args(args).thinking_display == value
  with pytest.raises(SystemExit):
    _ = parser.parse_args(["--thinking-display", "bogus"])
  assert "invalid choice: 'bogus'" in capsys.readouterr().err


def test_the_effort_option_takes_only_a_known_level(
  capsys: pytest.CaptureFixture[str],
) -> None:
  parser = argparse.ArgumentParser()
  claude.add_arguments(parser, model="m", effort="low", max_budget_usd=0.5)
  for level in claude.EFFORTS:
    assert parser.parse_args(["--effort", level]).effort == level
  with pytest.raises(SystemExit):
    _ = parser.parse_args(["--effort", "bogus"])
  assert "invalid choice: 'bogus'" in capsys.readouterr().err


def projects_in(tmp_path: Path) -> tuple[Path, dict[str, str]]:
  """Make a config whose projects hold one other session's directory.

  Args:
    tmp_path: The test's directory.

  Returns:
    The projects directory and the environment that names the config.
  """
  config = tmp_path / "config"
  (config / "projects" / "other").mkdir(parents=True)
  return config / "projects", {"CLAUDE_CONFIG_DIR": str(config)}


def project_name(cwd: Path) -> str:
  return re.sub("[^a-zA-Z0-9]", "-", str(cwd.resolve()))


@pytest.mark.parametrize("to", ["elsewhere", "projects", "sibling"])
def test_a_name_that_leads_out_of_projects_is_left(
  tmp_path: Path, to: str, caplog: pytest.LogCaptureFixture
) -> None:
  projects, env = projects_in(tmp_path)
  target = {
    "elsewhere": tmp_path / "elsewhere",
    "projects": projects,
    "sibling": projects / "other",
  }[to]
  target.mkdir(exist_ok=True)
  cwd = tmp_path / "work"
  (projects / project_name(cwd)).symlink_to(target)
  claude.remove_project_dir(cwd, env)
  assert target.is_dir()
  assert (projects / "other").is_dir()
  assert "not known to be this session's" in caplog.text


def test_a_name_claude_would_hash_is_left(
  tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
  projects, env = projects_in(tmp_path)
  cwd = tmp_path / ("x" * (201 - len(project_name(tmp_path))))
  kept = projects / project_name(cwd)
  kept.mkdir()
  claude.remove_project_dir(cwd, env)
  assert kept.is_dir()
  assert "not known to be this session's" in caplog.text


def test_a_sessions_repr_leaves_out_its_env() -> None:
  session = claude.Session(
    model="m", effort="e", max_budget_usd=1, env={"MODEL_TOKEN": "s3cret"}
  )
  assert "model='m'" in repr(session)
  assert "s3cret" not in repr(session)


def test_a_restored_runner_goes_on_with_the_session_and_its_numbers(
  tmp_path: Path,
) -> None:
  # The second run is killed: it prints no result, and nothing checkpoints
  # it, so the session's total stays at the first run's.
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(session_id="s1", total_cost_usd=1)]},
      {"lines": []},
      {"lines": [result(session_id="s1", total_cost_usd=1.3)]},
    ],
  )
  runs = tmp_path / "runs"
  files = claude.numbered(runs, "session")
  spec = AgentSpec("Write.", tmp_path)
  task = AgentTask("Go.", {}, str(tmp_path), None)
  first = claude.Runner(fake.session, files, continue_session=True)
  point = first.checkpoint(first.start(spec, task, HOST).session)
  _ = first.start(spec, task, HOST)
  spent: list[float] = []
  second = claude.Runner(
    fake.session, files, continue_session=True, spent=spent.append
  )
  second.restore([json.loads(json.dumps(point))])

  found = second.start(spec, task, HOST)

  assert spent == [pytest.approx(0.3)]
  assert option(fake.argv(3), "--resume") == ["s1"]
  assert fake.calls()[2]["cwd"] == str(runs / "session-1")
  assert len(fake.calls()) == 3
  assert found.trace == runs / "session-2.trace.jsonl"
  assert found.trace_format == "claude-code-stream-json"


def test_a_restored_runner_numbers_on_after_its_last_start(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"lines": [result()]}])
  runs = tmp_path / "runs"
  runner = claude.Runner(fake.session, claude.numbered(runs, "review"))
  point = {"id": None, "total_usd": 0.0, "on_host": True, "cwd": str(runs)}
  runner.restore([point | {"number": 4}, point | {"number": 2}])
  task = AgentTask("Go.", {}, str(tmp_path), None)
  _ = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert (runs / "review-5.trace.jsonl").exists()
  assert runner.current is None
  with pytest.raises(ValueError, match="not a checkpoint"):
    runner.restore([point])


def test_a_keyed_start_resumes_its_session_with_the_resume_prompt(
  tmp_path: Path,
) -> None:
  fake = Fake(
    tmp_path,
    [
      {"project": True, "lines": [result(session_id="s1", total_cost_usd=1)]},
      {"lines": [result(session_id="s1", total_cost_usd=1.25)]},
    ],
  )
  runs = tmp_path / "runs"
  runner = claude.Runner(fake.session, claude.numbered(runs))
  check = tmp_path / "ok.sh"
  _ = check.write_text("#!/bin/sh\nexit 0\n")
  check.chmod(0o755)
  keyed = AgentSpec(
    "Judge.",
    tmp_path,
    validator=check,
    name="review",
    session="review",
    resume_prompt="Judge again.",
  )

  starts: list[ActionResult] = []

  def body(ctx: Context) -> ActionResult:
    actions = Actions(runner)
    starts.append(actions.agent(ctx, keyed, HOST))
    return actions.agent(ctx, keyed, HOST)

  found = act(tmp_path / "attempt", body)

  assert found.cost_usd == pytest.approx(0.25)
  assert [r.run.resumed for r in (*starts, found) if r.run] == [False, True]
  calls = fake.calls()
  first, second = str(calls[0]["prompt"]), str(calls[1]["prompt"])
  assert first.startswith("Judge.\n\nBefore you finish, run `")
  assert second.startswith("Judge again.\n\nBefore you finish, run `")
  assert option(fake.argv(1), "--resume") == []
  assert option(fake.argv(2), "--resume") == ["s1"]
  assert calls[1]["cwd"] == str(runs / "review-1")
  # close kept the keyed session; end removes it.
  assert len(fake.projects()) == 1
  runner.end()
  assert fake.projects() == []


def test_a_keyed_start_leaves_the_continued_session_alone(
  tmp_path: Path,
) -> None:
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(session_id="s1")]},
      {"lines": [result(session_id="s2")]},
    ],
  )
  runner = claude.Runner(
    fake.session, claude.numbered(tmp_path / "runs"), continue_session=True
  )
  task = AgentTask("Go.", {}, str(tmp_path), None, "Go on.")
  _ = runner.start(AgentSpec("Go.", tmp_path, session="k"), task, HOST)
  assert runner.current is None
  plain = runner.start(AgentSpec("Go.", tmp_path), task, HOST)
  assert option(fake.argv(2), "--resume") == []
  assert fake.calls()[1]["prompt"] == "Go."
  assert runner.current is plain.session


def test_keys_started_at_once_each_resume_their_own_session(
  tmp_path: Path,
) -> None:
  fake = Fake(tmp_path, [{"echo": True}] * 8)
  runner = claude.Runner(fake.session, claude.numbered(tmp_path / "runs"))
  keys = ("a", "b")
  barrier = threading.Barrier(len(keys))
  failures: list[BaseException] = []

  def start(key: str, prompt: str) -> None:
    spec = AgentSpec(prompt, tmp_path, name=key, session=key)
    task = AgentTask(prompt, {}, str(tmp_path), None, f"{key}-again")
    try:
      _ = barrier.wait(timeout=10)
      assert runner.start(spec, task, HOST).status == "ok"
    except BaseException as error:  # noqa: BLE001
      failures.append(error)

  for round_ in range(4):
    threads = [
      threading.Thread(target=start, args=(key, f"{key}-{round_}"))
      for key in keys
    ]
    for thread in threads:
      thread.start()
    for thread in threads:
      thread.join()
  assert failures == []

  calls = fake.calls()
  assert len(calls) == 8
  for n, call in enumerate(calls, 1):
    prompt = str(call["prompt"])
    resume = option(fake.argv(n), "--resume")
    key = prompt[0]
    if prompt.endswith("-0"):
      assert resume == []
    else:
      assert prompt == f"{key}-again"
      assert resume == [f"s-{key}-0"]


def test_a_keyed_session_that_cannot_go_on_is_replaced_and_removed(
  tmp_path: Path,
) -> None:
  fake = Fake(
    tmp_path,
    [
      {
        "project": True,
        "lines": [{**result(session_id="s1"), "subtype": "error_max_turns"}],
      },
      {"lines": [result(session_id="s2")]},
    ],
  )
  runs = tmp_path / "runs"
  runner = claude.Runner(fake.session, claude.numbered(runs))
  spec = AgentSpec("Go.", tmp_path, session="k")
  task = AgentTask("Go.", {}, str(tmp_path), None, "Go on.")
  first = runner.start(spec, task, HOST)
  assert first.status == "failed" and first.resumable is False
  assert len(fake.projects()) == 1
  _ = runner.start(spec, task, HOST)
  assert option(fake.argv(2), "--resume") == []
  assert fake.calls()[1]["prompt"] == "Go."
  assert fake.calls()[1]["cwd"] == str(runs / "agent-2")
  assert fake.projects() == []


def test_a_restored_keyed_session_that_ended_on_a_fault_starts_anew(
  tmp_path: Path,
) -> None:
  refused = result(session_id="s1", is_error=True, stop_reason="refusal")
  fake = Fake(
    tmp_path, [{"lines": [refused]}, {"lines": [result(session_id="s2")]}]
  )
  files = claude.numbered(tmp_path / "runs")
  spec = AgentSpec("Go.", tmp_path, session="k")
  task = AgentTask("Go.", {}, str(tmp_path), None, "Go on.")
  first = claude.Runner(fake.session, files)
  point = first.checkpoint(first.start(spec, task, HOST).session)
  second = claude.Runner(fake.session, files)
  second.restore([json.loads(json.dumps(point))])

  _ = second.start(spec, task, HOST)

  assert option(fake.argv(2), "--resume") == []
  assert fake.calls()[1]["prompt"] == "Go."


def test_a_keyed_session_claude_no_longer_has_starts_anew(
  tmp_path: Path,
) -> None:
  lost = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 0,
    "total_cost_usd": 0,
    "session_id": "s1",
    "errors": ["No conversation found with session ID: s1"],
  }
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(session_id="s1", total_cost_usd=0.5, num_turns=2)]},
      {"lines": [lost]},
      {"lines": [result(session_id="s2", total_cost_usd=0.25, num_turns=1)]},
    ],
  )
  runs = tmp_path / "runs"
  spent: list[float] = []
  runner = claude.Runner(
    fake.session, claude.numbered(runs), spent=spent.append
  )
  spec = AgentSpec("Go.", tmp_path, session="k")
  task = AgentTask("Go.", {}, str(tmp_path), None, "Go on.")
  _ = runner.start(spec, task, HOST)

  found = runner.start(spec, task, HOST)

  assert (found.status, found.ending, found.resumable, found.resumed) == (
    "ok",
    "success",
    True,
    False,
  )
  assert option(fake.argv(2), "--resume") == ["s1"]
  assert fake.calls()[1]["prompt"] == "Go on."
  assert option(fake.argv(3), "--resume") == []
  assert fake.calls()[2]["prompt"] == "Go."
  assert fake.calls()[2]["cwd"] == str(runs / "agent-2")
  assert found.cost_usd == pytest.approx(0.25)
  assert spent == [pytest.approx(0.5), 0.0, pytest.approx(0.25)]
  assert runner.checkpoint(found.session)["id"] == "s2"


def test_a_restored_runner_goes_on_with_each_keys_session(
  tmp_path: Path,
) -> None:
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(session_id="sa", total_cost_usd=1)]},
      {"lines": [result(session_id="sb", total_cost_usd=1)]},
      {"lines": [result(session_id="sa", total_cost_usd=1.5)]},
      {"lines": [result(session_id="sb", total_cost_usd=1.25)]},
      {"lines": [result(session_id="sa", total_cost_usd=1.75)]},
    ],
  )
  runs = tmp_path / "runs"
  files = claude.numbered(runs)
  task = AgentTask("Go.", {}, str(tmp_path), None)
  specs = {
    key: AgentSpec("Go.", tmp_path, name=key, session=key) for key in "ab"
  }
  first = claude.Runner(fake.session, files)
  points = [
    first.checkpoint(first.start(specs[key], task, HOST).session)
    for key in "aba"
  ]
  spent: list[float] = []
  second = claude.Runner(fake.session, files, spent=spent.append)
  second.restore([json.loads(json.dumps(point)) for point in points])

  found_b = second.start(specs["b"], task, HOST)
  found_a = second.start(specs["a"], task, HOST)

  assert option(fake.argv(4), "--resume") == ["sb"]
  assert fake.calls()[3]["cwd"] == str(runs / "b-2")
  assert option(fake.argv(5), "--resume") == ["sa"]
  assert fake.calls()[4]["cwd"] == str(runs / "a-1")
  assert found_b.trace == runs / "b-4.trace.jsonl"
  assert found_a.trace == runs / "a-5.trace.jsonl"
  assert spent == [pytest.approx(0.25), pytest.approx(0.25)]
  assert points[2] == {
    "id": "sa",
    "total_usd": 1.5,
    "number": 3,
    "on_host": True,
    "cwd": str(runs / "a-1"),
    "fault": None,
    "session": "a",
    "subtype": "success",
  }


def test_a_review_retried_after_a_refusal_starts_a_new_session(
  tmp_path: Path,
) -> None:
  rubric = tmp_path / "rubric.toml"
  _ = rubric.write_text(
    'system = "Judge."\n[[criteria]]\nid = "a"\nquestion = "A?"\n'
  )
  item = tmp_path / "item.md"
  _ = item.write_text("An item.\n")
  answer = {
    "criteria": [{"id": "a", "verdict": "pass", "evidence": "e", "fix": ""}]
  }
  refused = result(session_id="s1", is_error=True, stop_reason="refusal")
  fake = Fake(
    tmp_path,
    [
      {"lines": [refused]},
      {"lines": [result(session_id="s2", structured_output=answer)]},
    ],
  )
  runs = tmp_path / "runs"
  runner = claude.Runner(fake.session, claude.numbered(runs))
  state = RubricState(
    "rubric",
    {"pass": End("passed"), "fail": End("failed"), "error": End("errored")},
    actions=Actions(agent_runner=runner),
    reviews=[Review("r", rubric, {"item": item})],
    resume=True,
  )
  attempt = Machine([state]).run(tmp_path / "attempt")
  assert attempt.status == "passed"
  assert option(fake.argv(2), "--resume") == []
  assert (runs / "r-1.trace.jsonl").exists()
  assert (runs / "r-2.trace.jsonl").exists()
