# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from dataforge.statemachine import (
    Actions,
    AgentRun,
    AgentSpec,
    AgentTask,
    Attempt,
    Context,
    End,
    Env,
    Goto,
    Guard,
    Limits,
    Outcome,
    ScriptSpec,
    State,
    TrialAgent,
    TrialResult,
    fan_out,
    run_attempt,
)

# A fake dataset shaped like a curate process: author writes a task (a validator checks it
# mechanically), rubric judges it, pass_at_k runs k trials of a solver.
K = 3

CURATE_TABLE: dict[str, dict[str, Goto | End]] = {
    "author": {
        "written": Goto("rubric"),
        "invalid": Goto("author"),
    },
    "rubric": {
        "pass": Goto("pass_at_k", report=False),
        "fail": Goto("author"),
    },
    "pass_at_k": {
        "in_range": End("passed"),
        "all_solved": Goto("author", mark="too_easy"),
        "none_solved": Goto("author", mark="too_hard"),
    },
}

MAX_VISITS = {"author": 3, "rubric": 3, "pass_at_k": 3}


@dataclass
class Script:
    """What the fakes do, call by call. Each list is consumed from the front."""

    tasks: list[str] = field(default_factory=list[str])
    verdicts: list[bool] = field(default_factory=list[bool])
    solved: list[int] = field(default_factory=list[int])
    prompts: list[str] = field(default_factory=list[str])
    closed: list[object] = field(default_factory=list[object])


class FakeAgent:
    """An agent runner: the author writes the next task text, the judge answers in JSON."""

    def __init__(self, script: Script) -> None:
        self.script: Script = script

    def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
        self.script.prompts.append(task.prompt)
        if spec.output_schema is not None:
            answer = json.dumps({"pass": self.script.verdicts.pop(0)})
            return AgentRun(("judge", task.outdir), "ok", 0.5, None, answer)
        return self._write(task.outdir)

    def resume(self, session: object, prompt: str) -> AgentRun:
        self.script.prompts.append(prompt)
        _, outdir = cast(tuple[str, str], session)
        return self._write(outdir)

    def close(self, session: object) -> None:
        self.script.closed.append(session)

    def _write(self, outdir: str) -> AgentRun:
        _ = (Path(outdir) / "task").write_text(self.script.tasks.pop(0))
        return AgentRun(("author", outdir), "ok", 1.0, None, "done")


class FakeTrials:
    """A trial runner: each of the K trials of a round writes a diff; the first few solve."""

    def __init__(self, script: Script) -> None:
        self.script: Script = script
        self.solved: list[int] = []

    def run(self, task_dir: Path, agent: TrialAgent, limits: Limits, outdir: Path) -> TrialResult:
        visit = int(outdir.parent.name)
        index = int(outdir.name.removeprefix("trial-"))
        while len(self.solved) < visit:
            self.solved.append(self.script.solved.pop(0))
        diff = outdir / "diff"
        _ = diff.write_text(f"diff of {task_dir.read_text()}")
        reward = 1.0 if index < self.solved[visit - 1] else 0.0
        return TrialResult({"reward": reward}, None, 0.1, 1.0, {"diff": diff})


def curate(validator: Path, actions: Actions) -> list[State]:
    def author(ctx: Context) -> Outcome:
        report = ctx.report
        prompt = "Write a task." if report is None else f"Revise: {report.outcome}."
        spec = AgentSpec(prompt, workdir=ctx.outdir, outputs=["task"], validator=validator)
        result = actions.agent(ctx, spec)
        seen = {} if report is None else {"report_artifacts": sorted(report.artifacts)}
        return Outcome("written" if result.ok else "invalid", seen)

    def rubric(ctx: Context) -> Outcome:
        spec = AgentSpec(
            "Judge the task.",
            workdir=ctx.outdir,
            inputs=["task"],
            output_schema={"type": "object"},
            tools=[],
        )
        verdict = cast(dict[str, bool], actions.agent(ctx, spec).value)
        return Outcome("pass" if verdict["pass"] else "fail")

    def pass_at_k(ctx: Context) -> Outcome:
        task = ctx.artifacts.get("task")

        def trial(i: int) -> Callable[[], TrialResult]:
            return lambda: actions.trial(ctx, f"trial-{i}", task, "solver", Limits(1.0))

        results = fan_out([trial(i) for i in range(K)])
        solved = sum(result.rewards["reward"] == 1.0 for result in results)
        outcome = {0: "none_solved", K: "all_solved"}.get(solved, "in_range")
        return Outcome(outcome, {"solved": solved})

    bodies = {"author": author, "rubric": rubric, "pass_at_k": pass_at_k}
    return [
        State(name, bodies[name], CURATE_TABLE[name], MAX_VISITS[name]) for name in CURATE_TABLE
    ]


@pytest.fixture
def validator(tmp_path: Path) -> Path:
    path = tmp_path / "check.sh"
    _ = path.write_text(
        '#!/bin/sh\ngrep -q valid "$1/task" || { echo "task is broken"; exit 1; }\n'
    )
    path.chmod(0o755)
    return path


def run(
    tmp_path: Path,
    validator: Path,
    script: Script,
    *,
    guard: Guard | None = None,
    on_step: Callable[[Attempt], None] | None = None,
    states: Callable[[list[State]], list[State]] = lambda states: states,
) -> Attempt:
    actions = Actions(agent_runner=FakeAgent(script), trial_runner=FakeTrials(script))
    return run_attempt(
        states(curate(validator, actions)), tmp_path / "attempt", guard=guard, on_step=on_step
    )


def path_of(attempt: Attempt) -> list[tuple[str, str | None]]:
    return [(visit.state, visit.outcome) for visit in attempt.visits]


def test_in_range_pass_at_k_passes(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid 1"], verdicts=[True], solved=[1])
    attempt = run(tmp_path, validator, script)
    assert attempt.status == "passed"
    assert path_of(attempt) == [
        ("author", "written"),
        ("rubric", "pass"),
        ("pass_at_k", "in_range"),
    ]
    assert attempt.final["pass_at_k"].data == {"solved": 1}


def test_validator_failure_resumes_the_agent_with_its_output(
    tmp_path: Path, validator: Path
) -> None:
    script = Script(tasks=["broken", "valid 1"], verdicts=[True], solved=[1])
    attempt = run(tmp_path, validator, script)
    assert attempt.status == "passed"
    assert "task is broken" in script.prompts[1]
    assert attempt.visits[0].outputs["task"].read_text() == "valid 1"


def test_validator_failing_past_max_fixes_returns_to_author(
    tmp_path: Path, validator: Path
) -> None:
    script = Script(tasks=["broken"] * 3 + ["valid 2"], verdicts=[True], solved=[2])
    attempt = run(tmp_path, validator, script)
    assert path_of(attempt)[:2] == [("author", "invalid"), ("author", "written")]
    assert attempt.status == "passed"


def test_validator_given_to_the_agent_is_a_read_only_copy(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid 1"], verdicts=[True], solved=[1])
    _ = run(tmp_path, validator, script)
    copy = tmp_path / "attempt" / "validators" / "author" / "1" / "1" / "check.sh"
    assert f"`{copy} " in script.prompts[0]
    assert copy.stat().st_mode & 0o222 == 0
    assert copy.read_text() == validator.read_text()


def test_rubric_failure_returns_to_author_with_a_report(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid 1", "valid 2"], verdicts=[False, True], solved=[1])
    attempt = run(tmp_path, validator, script)
    assert path_of(attempt)[:3] == [
        ("author", "written"),
        ("rubric", "fail"),
        ("author", "written"),
    ]
    assert script.prompts[2].startswith("Revise: fail.")
    assert attempt.status == "passed"


def test_all_solved_returns_to_author_with_the_diffs(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid 1", "valid 2"], verdicts=[True, True], solved=[K, 1])
    attempt = run(tmp_path, validator, script)
    assert path_of(attempt)[2:4] == [("pass_at_k", "all_solved"), ("author", "written")]
    assert attempt.visits[2].mark == "too_easy"
    assert script.prompts[2].startswith("Revise: all_solved.")
    assert attempt.visits[3].data == {"report_artifacts": [f"trial-{i}" for i in range(K)]}
    diff = attempt.visits[2].outputs["trial-0"] / "diff"
    assert diff.read_text() == "diff of valid 1"
    assert attempt.status == "passed"
    assert attempt.kept_visit is None


def test_cap_after_a_mark_keeps_the_marked_visit(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"] * 3, verdicts=[True] * 3, solved=[K, 0, K])
    attempt = run(tmp_path, validator, script)
    assert attempt.status == "too_easy"
    assert attempt.kept_visit == 8
    assert attempt.reason == "author visit 4: entered over max_visits"
    assert attempt.final["pass_at_k"] is attempt.visits[8]


def test_kept_visit_cuts_the_final_record(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"] * 3, verdicts=[True, True, False], solved=[1, K])

    def in_range_goes_back(states: list[State]) -> list[State]:
        pass_at_k = states[2]
        transitions = {**pass_at_k.transitions, "in_range": Goto("author")}
        return [*states[:2], State("pass_at_k", pass_at_k.body, transitions, 3)]

    attempt = run(tmp_path, validator, script, states=in_range_goes_back)
    assert attempt.status == "too_easy"
    assert attempt.kept_visit == 5
    assert attempt.final["rubric"] is attempt.visits[4]
    assert attempt.visits[-1].state == "rubric"


def test_cap_without_a_mark_fails(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"] * 3, verdicts=[False] * 3)
    attempt = run(tmp_path, validator, script)
    assert attempt.status == "failed"
    assert attempt.kept_visit is None
    assert attempt.reason == "author visit 4: entered over max_visits"


def test_guard_blocks_with_budget(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"], verdicts=[True])

    def guard(_ctx: Context, state: State) -> str | None:
        return "out of budget" if state.name == "pass_at_k" else None

    attempt = run(tmp_path, validator, script, guard=guard)
    assert attempt.status == "budget"
    assert attempt.reason == "pass_at_k visit 1: out of budget"
    assert path_of(attempt) == [("author", "written"), ("rubric", "pass")]


def test_guard_after_a_mark_keeps_the_marked_visit(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"] * 2, verdicts=[True], solved=[0])

    def guard(ctx: Context, state: State) -> str | None:
        return "out of budget" if state.name == "rubric" and ctx.visit == 2 else None

    attempt = run(tmp_path, validator, script, guard=guard)
    assert attempt.status == "too_hard"
    assert attempt.kept_visit == 2


def test_each_agent_session_is_closed(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"], verdicts=[True], solved=[1])
    _ = run(tmp_path, validator, script)
    kinds = [cast(tuple[str, str], session)[0] for session in script.closed]
    assert kinds == ["author", "judge"]


def test_on_step_sees_each_visit_and_the_end(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"], verdicts=[True], solved=[1])
    seen: list[tuple[int, str | None]] = []
    attempt = run(
        tmp_path, validator, script, on_step=lambda a: seen.append((len(a.visits), a.status))
    )
    assert seen == [(1, None), (2, None), (3, "passed")]
    assert attempt.to_json()["status"] == "passed"


def test_to_json_is_json(tmp_path: Path, validator: Path) -> None:
    script = Script(tasks=["valid"], verdicts=[True], solved=[1])
    record = run(tmp_path, validator, script).to_json()
    assert json.loads(json.dumps(record)) == record
    final = cast(dict[str, dict[str, object]], record["final"])
    actions = cast(list[dict[str, dict[str, str]]], final["pass_at_k"]["actions"])
    outputs = {name: path for action in actions for name, path in action["outputs"].items()}
    assert outputs == {f"trial-{i}": f"artifacts/pass_at_k/1/trial-{i}" for i in range(K)}
    visits = cast(list[dict[str, object]], record["visits"])
    assert [visit["state"] for visit in visits] == ["author", "rubric", "pass_at_k"]


def test_artifacts_keep_each_version(tmp_path: Path) -> None:
    actions = Actions()

    def body(ctx: Context) -> Outcome:
        argv = ["sh", "-c", f'echo {ctx.visit} > "$DATAFORGE_OUTPUTS/n"']
        _ = actions.script(ctx, ScriptSpec(argv, outputs=["n"]))
        versions = [(v.state, v.visit, v.path) for v in ctx.artifacts.history("n")]
        return Outcome("again", {"latest": str(ctx.artifacts.get("n")), "n": len(versions)})

    attempt = run_attempt([State("a", body, {"again": Goto("a")}, 2)], tmp_path)
    assert [visit.data for visit in attempt.visits] == [
        {"latest": str(tmp_path / "artifacts/a/1/n"), "n": 1},
        {"latest": str(tmp_path / "artifacts/a/2/n"), "n": 2},
    ]


def test_last_outcome_ends_on_reentry(tmp_path: Path) -> None:
    states = [
        State(
            "a", lambda _ctx: Outcome("on", last=True), {"on": Goto("b")}, 5, on_exhausted="error"
        ),
        State("b", lambda _ctx: Outcome("back"), {"back": Goto("a")}, 5),
    ]
    attempt = run_attempt(states, tmp_path)
    assert attempt.status == "error"
    assert attempt.reason == "a visit 2: entered after its last visit"


def test_a_raising_body_is_recorded_and_raised(tmp_path: Path) -> None:
    def body(_ctx: Context) -> Outcome:
        raise OSError("disk full")

    seen: list[str | None] = []
    with pytest.raises(OSError, match="disk full"):
        _ = run_attempt(
            [State("a", body, {}, 1)],
            tmp_path,
            on_step=lambda a: seen.append(a.visits[-1].outcome),
        )
    assert seen == ["raised"]


def test_an_outcome_without_a_transition_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="a has no transition for outcome 'odd'"):
        _ = run_attempt([State("a", lambda _ctx: Outcome("odd"), {}, 1)], tmp_path)


def test_a_goto_to_an_unknown_state_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown b"):
        _ = run_attempt([State("a", lambda _ctx: Outcome("x"), {"x": Goto("b")}, 1)], tmp_path)


def test_a_raising_guard_or_on_step_adds_nothing_to_the_record(tmp_path: Path) -> None:
    attempts: list[Attempt] = []

    def guard(ctx: Context, state: State) -> str | None:
        attempts.append(ctx.attempt)
        raise RuntimeError("guard broke")

    states = [State("a", lambda _ctx: Outcome("x"), {"x": End("ok")}, 1)]
    with pytest.raises(RuntimeError, match="guard broke"):
        _ = run_attempt(states, tmp_path, guard=guard)
    assert attempts[0].visits == []

    def on_step(attempt: Attempt) -> None:
        attempts.append(attempt)
        raise RuntimeError("on_step broke")

    with pytest.raises(RuntimeError, match="on_step broke"):
        _ = run_attempt(states, tmp_path, on_step=on_step)
    assert [(visit.state, visit.outcome) for visit in attempts[1].visits] == [("a", "x")]
