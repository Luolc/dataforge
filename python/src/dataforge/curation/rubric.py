"""Rubric reviews, which RubricState runs: agents with no tools that judge.

A rubric is a TOML file: a `system` text, which says what the reviewer
reviews and how to judge, and `[[criteria]]`, each with an `id`, a
`severity` ("error" or "warning"), a `question` and optional `check`,
`pass_if` and `fail_if` lines. A review gives the agent the system text, its
inputs as tagged blocks and the criteria, and takes one verdict per criterion
back as structured output. A criterion of severity "error" that fails or gets
no verdict fails the review; one of severity "warning" is only reported.

RubricState writes what its reviews judged as RUBRIC_TABLE, the same for any
dataset (rubric_table), and as RUBRIC_REPORT, Markdown for people
(rubric_report).
"""

from collections.abc import Mapping, Sequence
import dataclasses
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import cast

from dataforge.curation.actions import ActionResult
from dataforge.curation.engine import Data, Input

TEXT_SUFFIXES = frozenset(
  {"", ".cfg", ".md", ".patch", ".py", ".sh", ".toml", ".txt"}
)
"""The file suffixes a review reads as text; other files are named only."""

MAX_FILE_CHARS = 40_000
"""How much of one file a review reads; the rest is cut."""

RUBRIC_TABLE = "rubric.json"
"""The output that holds a visit's scores, as rubric_table gives them."""

RUBRIC_REPORT = "report.md"
"""The output that holds a visit's report, as rubric_report gives it."""

VERDICTS_SCHEMA: Data = {
  "type": "object",
  "additionalProperties": False,
  "required": ["criteria", "summary"],
  "properties": {
    "criteria": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "verdict", "evidence", "fix"],
        "properties": {
          "id": {"type": "string"},
          "verdict": {"enum": ["pass", "fail"]},
          "evidence": {
            "type": "string",
            "description": (
              "What decides it: the files and lines, quoted. For a fail,"
              " what exactly is wrong."
            ),
          },
          "fix": {
            "type": "string",
            "description": (
              "For a fail: concretely what to change. Empty for a pass."
            ),
          },
        },
      },
    },
    "summary": {
      "type": "string",
      "description": (
        "What you examined, in order, what you found, and the overall"
        " conclusion."
      ),
    },
  },
}
"""The JSON Schema of a review's answer."""


@dataclass(frozen=True)
class Criterion:
  """One criterion of a rubric.

  Attributes:
    id: Its ID.
    severity: "error" fails the review when it fails; "warning" is only
      reported.
    question: What is judged.
    check: How to establish it.
    pass_if: What a pass looks like.
    fail_if: What a fail looks like.
  """

  id: str
  severity: str
  question: str
  check: str = ""
  pass_if: str = ""
  fail_if: str = ""

  def render(self) -> str:
    """Return the criterion as the prompt gives it.

    Returns:
      Its ID as a heading, then its question and lines.
    """
    lines = [f"### {self.id}", _flat(self.question)]
    labels = (
      ("How to check", self.check),
      ("Pass if", self.pass_if),
      ("Fail if", self.fail_if),
    )
    lines += [f"{label}: {_flat(text)}" for label, text in labels if text]
    return "\n".join(lines)


@dataclass(frozen=True)
class Rubric:
  """A rubric file, read.

  Attributes:
    system: What the reviewer reviews and how to judge.
    criteria: The criteria.
  """

  system: str
  criteria: tuple[Criterion, ...]

  @classmethod
  def load(cls, path: Path) -> Rubric:
    """Read a rubric file.

    Args:
      path: The TOML file.

    Returns:
      The rubric.

    Raises:
      ValueError: When the file is not a rubric.
    """
    with path.open("rb") as f:
      raw = tomllib.load(f)
    try:
      system = cast(object, raw["system"])
      items = cast(list[dict[str, object]], raw["criteria"])
      criteria = tuple(Criterion(**item) for item in items)  # pyright: ignore[reportArgumentType]
    except (KeyError, TypeError) as error:
      raise ValueError(f"{path}: not a rubric: {error!r}") from error
    if not isinstance(system, str) or not criteria:
      raise ValueError(f"{path}: not a rubric: no system text or criteria")
    for criterion in criteria:
      fields = cast(tuple[object, ...], dataclasses.astuple(criterion))
      if not all(isinstance(field, str) for field in fields):
        raise ValueError(f"{path}: {criterion.id}: a field is not a string")
      if criterion.severity not in {"error", "warning"}:
        raise ValueError(f"{path}: {criterion.id}: unknown severity")
    return cls(system, criteria)


@dataclass(frozen=True)
class Review:
  """One review of a RubricState (dataforge.curation.states).

  Attributes:
    name: Its name, unique in the state; the agent's name.
    rubric: The rubric's TOML file.
    inputs: What it judges, by the tag of its block, in order: a file is
      given as its text, a directory as each file in it.
  """

  name: str
  rubric: Path
  inputs: Mapping[str, Input]


@dataclass(frozen=True)
class Score:
  """How one criterion was judged.

  Attributes:
    criterion: The criterion's ID.
    severity: Its severity, "error" or "warning".
    passed: Whether its verdict was a pass.
    note: The reviewer's evidence, or "no verdict" when it gave none.
    fix: The reviewer's fix; empty for a pass or no verdict.
  """

  criterion: str
  severity: str
  passed: bool
  note: str
  fix: str


@dataclass(frozen=True)
class Judgment:
  """How a review ended.

  Attributes:
    name: The review's name.
    errored: Whether its agent run itself failed.
    passed: Whether it gave verdicts and no "error" criterion failed.
    findings: The failed criteria, errors first, with evidence and fix.
    data: What the visit's data keeps of it.
    scores: Each criterion's score, in the rubric's order.
  """

  name: str
  errored: bool
  passed: bool
  findings: str
  data: Data
  scores: tuple[Score, ...] = ()


def review_prompt(rubric: Rubric, blocks: Mapping[str, Path]) -> str:
  """Return the prompt of a review.

  Args:
    rubric: The rubric.
    blocks: The paths it judges, by the tag of their block, in order.

  Returns:
    The rubric's system text, the inputs as tagged blocks and the criteria.
  """
  text = "\n\n".join(
    f"<{tag}>\n{_text(path)}\n</{tag}>" for tag, path in blocks.items()
  )
  criteria = "\n\n".join(c.render() for c in rubric.criteria)
  return (
    f"{rubric.system.strip()}\n\n{text}\n\n"
    f"<rubric>\n{criteria}\n</rubric>\n\n"
    "Give one verdict per rubric criterion, using its id, then the summary."
  )


def judge_review(name: str, rubric: Rubric, result: ActionResult) -> Judgment:
  """Read a review's verdicts against its rubric.

  Args:
    name: The review's name.
    rubric: The rubric.
    result: The review's agent action.

  Returns:
    How it ended; a criterion with no verdict counts as failed.
  """
  run = result.run
  ending = None if run is None else run.ending
  verdicts = _verdicts(result.value)
  given = {} if verdicts is None else {v["id"]: v for v in verdicts}
  bad = [
    c
    for c in rubric.criteria
    if c.id not in given or given[c.id]["verdict"] != "pass"
  ]
  failed = [c.id for c in bad if c.severity == "error"]
  warnings = [c.id for c in bad if c.severity == "warning"]
  if verdicts is None:
    findings = f"{name} returned no verdicts ({ending})."
  else:
    findings = "\n\n".join(
      f"{cid}: no verdict."
      if cid not in given
      else f"{cid}: {given[cid]['evidence']}\nFix: {given[cid]['fix']}"
      for cid in (*failed, *warnings)
    )
  passed = verdicts is not None and not failed
  scores = tuple(
    Score(c.id, c.severity, False, "no verdict", "")
    if c.id not in given
    else Score(
      c.id,
      c.severity,
      given[c.id]["verdict"] == "pass",
      given[c.id]["evidence"],
      given[c.id]["fix"],
    )
    for c in rubric.criteria
  )
  return Judgment(
    name,
    run is None or run.status != "ok",
    passed,
    findings,
    {
      "ending": ending,
      "cost_usd": result.cost_usd,
      "passed": passed,
      "failed": failed,
      "warnings": warnings,
      "verdicts": result.value if verdicts is not None else None,
    },
    scores,
  )


def rubric_table(judgments: Sequence[Judgment]) -> Data:
  """Return what reviews judged, in a shape the same for any dataset.

  Args:
    judgments: The reviews' judgments, in order.

  Returns:
    {"passed": every review passed, "reviews": [{"name", "passed", "ending",
    "criteria": [{"criterion", "severity", "passed", "note", "fix"}]}]}, as
    JSON data.
  """
  return {
    "passed": all(judgment.passed for judgment in judgments),
    "reviews": [
      {
        "name": judgment.name,
        "passed": judgment.passed,
        "ending": judgment.data.get("ending"),
        "criteria": [dataclasses.asdict(score) for score in judgment.scores],
      }
      for judgment in judgments
    ],
  }


def rubric_report(judgments: Sequence[Judgment]) -> str:
  """Return what reviews judged as a Markdown report for people.

  A line on the whole, then for each review a heading, a table of its
  criteria and a paragraph on what failed.

  Args:
    judgments: The reviews' judgments, in order.

  Returns:
    The report.
  """
  failed = [judgment.name for judgment in judgments if not judgment.passed]
  whole = (
    "Every review passes." if not failed else f"Failed: {', '.join(failed)}."
  )
  parts = ["# Rubric report", whole]
  for judgment in judgments:
    result = "passes" if judgment.passed else "fails"
    rows = [
      "| Criterion | Severity | Result | Note |",
      "| --- | --- | --- | --- |",
      *(_row(score) for score in judgment.scores),
    ]
    parts += [
      f"## {judgment.name} {result}",
      "\n".join(rows),
      _summary(judgment),
    ]
  return "\n\n".join(parts) + "\n"


def _row(score: Score) -> str:
  result = "pass" if score.passed else "fail"
  cells = (_cell(score.criterion), score.severity, result, _cell(score.note))
  return f"| {' | '.join(cells)} |"


def _summary(judgment: Judgment) -> str:
  if judgment.data.get("verdicts") is None:
    return f"The review gave no verdicts ({judgment.data.get('ending')})."
  scores = judgment.scores
  bad = [s for s in scores if not s.passed]
  errors = [s.criterion for s in bad if s.severity == "error"]
  warnings = [s.criterion for s in bad if s.severity == "warning"]
  if not bad:
    return f"All {len(scores)} criteria pass."
  lines = [f"{len(bad)} of {len(scores)} criteria fail."]
  if errors:
    lines.append(f"Errors: {', '.join(errors)}.")
  if warnings:
    lines.append(f"Warnings: {', '.join(warnings)}.")
  return " ".join(lines)


def _cell(text: str) -> str:
  # A cell is one line, and "|" would end it.
  return " ".join(text.split()).replace("|", "\\|")


def _verdicts(value: object) -> list[dict[str, str]] | None:
  if not isinstance(value, dict):
    return None
  criteria = cast(dict[str, object], value).get("criteria")
  if not isinstance(criteria, list):
    return None
  found: list[dict[str, str]] = []
  for item in cast(list[object], criteria):
    if not isinstance(item, dict):
      return None
    fields = cast(dict[str, object], item)
    keys = ("id", "verdict", "evidence", "fix")
    if not all(isinstance(fields.get(key), str) for key in keys):
      return None
    found.append({key: cast(str, fields[key]) for key in keys})
  return found


def _text(path: Path) -> str:
  if not path.is_dir():
    return _read(path)
  parts: list[str] = []
  for file in sorted(path.rglob("*")):
    if not file.is_file() or "__pycache__" in file.parts:
      continue
    at = file.relative_to(path)
    if file.suffix not in TEXT_SUFFIXES:
      size = file.stat().st_size
      parts.append(f'<file path="{at}" skipped="true" bytes="{size}"/>')
    else:
      parts.append(f'<file path="{at}">\n{_read(file)}\n</file>')
  return "\n".join(parts) or "None."


def _read(path: Path) -> str:
  text = path.read_text(errors="replace")
  if len(text) <= MAX_FILE_CHARS:
    return text
  return f"{text[:MAX_FILE_CHARS]}\n... [cut, {len(text)} characters in all]"


def _flat(text: str) -> str:
  return " ".join(text.split())
