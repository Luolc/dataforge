# pyright: reportUnusedParameter=false
# The fakes implement protocols and take parameters they do not need.

import json
from pathlib import Path
import threading

import pytest

from dataforge.curation import (
  Actions,
  AgentRun,
  AgentSpec,
  AgentTask,
  End,
  Env,
  Goto,
  Machine,
  Outcome,
  Review,
  RubricState,
  ScriptSpec,
  ScriptState,
)
from dataforge.curation.rubric import MAX_FILE_CHARS, VERDICTS_SCHEMA

RUBRIC = """\
system = "Review the task."

[[criteria]]
id = "sound"
severity = "error"
question = "Is it  sound?"
check = "Read it."
pass_if = "It is."
fail_if = "It is not."

[[criteria]]
id = "tidy"
severity = "warning"
question = "Is it tidy?"
"""


def verdicts(**found: tuple[str, str, str]) -> str:
  """Return a review's answer.

  Args:
    **found: Each criterion's verdict, evidence and fix, by its ID.

  Returns:
    The answer as JSON.
  """
  return json.dumps(
    {
      "criteria": [
        {"id": cid, "verdict": v, "evidence": e, "fix": f}
        for cid, (v, e, f) in found.items()
      ],
      "summary": "Read it all.",
    }
  )


PASS = ("pass", "fine", "")


class Reviewers:
  """An agent runner that answers each review as told, by its name.

  Args:
    answers: Each review's run status and answer, by its name.
  """

  def __init__(self, answers: dict[str, tuple[str, str]]) -> None:
    self.answers: dict[str, tuple[str, str]] = answers
    self.specs: dict[str, AgentSpec] = {}
    self.prompts: dict[str, str] = {}
    self._lock: threading.Lock = threading.Lock()

  def start(self, spec: AgentSpec, task: AgentTask, env: Env) -> AgentRun:
    assert spec.name is not None
    with self._lock:
      self.specs[spec.name] = spec
      self.prompts[spec.name] = task.prompt
    status, answer = self.answers[spec.name]
    return AgentRun(None, status, 0.25, None, answer, "success", True)

  def resume(self, session: object, prompt: str) -> AgentRun:
    raise AssertionError("a review has no validator")


@pytest.fixture
def task(tmp_path: Path) -> Path:
  root = tmp_path / "task"
  (root / "tests").mkdir(parents=True)
  _ = (root / "tests" / "test.sh").write_text("exit 0\n")
  _ = (root / "tests" / "data.bin").write_bytes(b"\0\1\2")
  _ = (root / "instruction.md").write_text("Fix the bug.\n")
  return root


def rubric_state(
  tmp_path: Path, reviewers: Reviewers, task: Path, **kwargs: object
) -> RubricState:
  rubric = tmp_path / "rubric.toml"
  _ = rubric.write_text(RUBRIC)
  return RubricState(
    "rubric",
    {"pass": End("passed"), "fail": Goto("end"), "error": End("errored")},
    actions=Actions(agent_runner=reviewers),
    reviews=[
      Review("tests", rubric, {"instruction": task / "instruction.md"}),
      Review("docs", rubric, {"tests": task / "tests"}),
    ],
    feedback="${details}",
    **kwargs,  # pyright: ignore[reportArgumentType]
  )


def run(state: RubricState, root: Path) -> tuple[str | None, Outcome | None]:
  end = ScriptState(
    "end",
    {"ok": End("sent back")},
    actions=Actions(),
    spec=ScriptSpec(["true"]),
  )
  attempt = Machine([state, end]).run(root)
  visit = attempt.visits[0]
  feedback = visit.outputs.get("feedback")
  text = None if feedback is None else feedback.read_text()
  return attempt.status, Outcome(
    str(visit.outcome),
    visit.data,
    feedback=(None if text is None else {"details": text}),
  )


def test_every_review_passing_passes(tmp_path: Path, task: Path) -> None:
  reviewers = Reviewers(
    {
      "tests": ("ok", verdicts(sound=PASS, tidy=PASS)),
      "docs": ("ok", verdicts(sound=PASS, tidy=("fail", "messy", "tidy"))),
    }
  )
  status, outcome = run(rubric_state(tmp_path, reviewers, task), tmp_path / "a")
  assert status == "passed"
  assert outcome is not None
  docs = outcome.data["docs"]
  assert docs == {
    "ending": "success",
    "cost_usd": 0.25,
    "passed": True,
    "failed": [],
    "warnings": ["tidy"],
    "verdicts": json.loads(
      verdicts(sound=PASS, tidy=("fail", "messy", "tidy"))
    ),
  }
  spec = reviewers.specs["docs"]
  assert (spec.tools, spec.output_schema) == ((), VERDICTS_SCHEMA)
  assert spec.workdir == tmp_path / "a/artifacts/rubric/1"


PROMPT = """\
Review the task.

<instruction>
{instruction}
</instruction>

<rubric>
### sound
Is it sound?
How to check: Read it.
Pass if: It is.
Fail if: It is not.

### tidy
Is it tidy?
</rubric>

Give one verdict per rubric criterion, using its id, then the summary."""

FILES = """\
<tests>
<file path="data.bin" skipped="true" bytes="3"/>
<file path="test.sh">
exit 0

</file>
</tests>"""


def test_a_review_sees_its_rubric_and_its_inputs(
  tmp_path: Path, task: Path
) -> None:
  _ = (task / "instruction.md").write_text("x" * (MAX_FILE_CHARS + 5))
  reviewers = Reviewers(
    {
      "tests": ("ok", verdicts(sound=PASS)),
      "docs": ("ok", verdicts(sound=PASS)),
    }
  )
  _ = run(rubric_state(tmp_path, reviewers, task), tmp_path / "a")
  cut = "x" * MAX_FILE_CHARS
  instruction = f"{cut}\n... [cut, {MAX_FILE_CHARS + 5} characters in all]"
  assert reviewers.prompts["tests"].startswith(
    PROMPT.format(instruction=instruction)
  )
  assert FILES in reviewers.prompts["docs"]


def test_a_failed_review_fails_with_its_findings(
  tmp_path: Path, task: Path
) -> None:
  for docs, findings in [
    (
      verdicts(sound=("fail", "it breaks", "mend it"), tidy=("fail", "x", "y")),
      "sound: it breaks\nFix: mend it\n\ntidy: x\nFix: y",
    ),
    (verdicts(tidy=PASS), "sound: no verdict."),
    (json.dumps({"criteria": "none"}), "docs returned no verdicts (success)."),
    ("not JSON", "docs returned no verdicts (success)."),
  ]:
    reviewers = Reviewers(
      {"tests": ("ok", verdicts(sound=PASS)), "docs": ("ok", docs)}
    )
    root = tmp_path / str(len(findings))
    status, outcome = run(rubric_state(tmp_path, reviewers, task), root)
    assert status == "sent back"
    assert outcome is not None
    assert outcome.feedback == {"details": f"### docs\n\n{findings}"}
    assert outcome.data["tests"]["passed"] is True  # pyright: ignore[reportIndexIssue]
    assert outcome.data["docs"]["failed"] == ["sound"]  # pyright: ignore[reportIndexIssue]


def test_a_review_whose_run_failed_is_an_error(
  tmp_path: Path, task: Path
) -> None:
  reviewers = Reviewers(
    {"tests": ("ok", verdicts(sound=PASS)), "docs": ("failed", "no result")}
  )
  status, _ = run(rubric_state(tmp_path, reviewers, task), tmp_path / "a")
  assert status == "errored"


FATAL = '[[criteria]]\nid = "a"\nseverity = "fatal"\nquestion = "q"\n'


def test_a_bad_rubric_or_a_repeated_name_is_an_error(
  tmp_path: Path, task: Path
) -> None:
  bad = tmp_path / "bad.toml"
  for text, why in [
    ('[[criteria]]\nid = "a"\nseverity = "error"\nquestion = "q"\n', "system"),
    ('system = "s"\n', "criteria"),
    (f'system = "s"\n{FATAL}', "unknown severity"),
  ]:
    _ = bad.write_text(text)
    with pytest.raises(ValueError, match=why):
      _ = RubricState(
        "r",
        {},
        actions=Actions(),
        reviews=[Review("a", bad, {})],
      )
  rubric = tmp_path / "rubric.toml"
  _ = rubric.write_text(RUBRIC)
  with pytest.raises(ValueError, match="two reviews have one name"):
    _ = RubricState(
      "r",
      {},
      actions=Actions(),
      reviews=[Review("a", rubric, {}), Review("a", rubric, {})],
    )
