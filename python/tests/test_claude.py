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
`hang`, whether it starts a child, writes its pid and sleeps.
"""

from collections.abc import Callable
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import cast, override

import pytest

from dataforge.curation import (
  ActionResult,
  Actions,
  AgentSpec,
  AgentTask,
  CommandResult,
  Context,
  Docker,
  End,
  HOST,
  Machine,
  Outcome,
  State,
)
from dataforge.runtime import claude

FAKE_CLAUDE = """\
import json, os, pathlib, re, subprocess, sys, time
log = pathlib.Path(os.environ["FAKE_LOG"])
n = len(list(log.glob("call-*.json"))) + 1
prompt = sys.stdin.read()
cwd = os.getcwd()
(log / f"call-{n}.json").write_text(
  json.dumps({"argv": sys.argv[1:], "cwd": cwd, "prompt": prompt})
)
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
        "lines": [
          {**result(session_id="s1"), "subtype": "error_max_budget_usd"}
        ],
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


def test_a_judges_structured_output_is_its_value(tmp_path: Path) -> None:
  fake = Fake(
    tmp_path,
    [
      {"lines": [result(structured_output={"score": 1})]},
      {"lines": [result(result="no tool call")]},
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
    assert second.result == "no structured output: success"
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
