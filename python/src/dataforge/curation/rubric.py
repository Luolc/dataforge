"""Rubric reviews, which RubricState runs: agents with no tools that judge.

A rubric is a TOML file: a `system` text, which says what the reviewer
reviews and how to judge, and `[[criteria]]`, each with an `id`, a
`question` and optional `check`, `pass_if` and `fail_if` lines. A review gives
the agent the system text, its inputs as tagged blocks and the criteria, and
takes one verdict per criterion back as structured output. Any criterion that
fails or gets no verdict fails the review.

The text of every input block, and of every other block made of what an
author or a reviewer wrote, is HTML-escaped, so that no block can close itself
or open another.

A review may also give the criteria of the other reviews of its state
(others_text), so that its fixes do not break them (CROSS_CRITERIA).

A review may be called again, as RubricState's later visits do: a re-review
also gives the reviewer its last verdicts and what changed in each block since
(rereview_prompt), and asks it to judge everything again (REREVIEW). Each call
is kept as a Call.

RubricState writes what its reviews judged as RUBRIC_TABLE, the same for any
dataset (rubric_table), and as RUBRIC_REPORT, Markdown for people
(rubric_report).
"""

from collections.abc import Mapping, Sequence
import dataclasses
from dataclasses import dataclass, field
import difflib
import html
import json
from pathlib import Path
import tomllib
from typing import cast, TypeIs

from dataforge.curation.actions import ActionResult
from dataforge.curation.engine import Data, Input, Version

MAX_FILE_CHARS = 40_000
"""How much of one file a review reads; the rest is cut."""

RUBRIC_TABLE = "rubric.json"
"""The output that holds a visit's scores, as rubric_table gives them."""

RUBRIC_REPORT = "report.md"
"""The output that holds a visit's report, as rubric_report gives it."""

CALL_SUFFIX = ".inputs.json"
"""What follows a review's name in the name of the output that keeps its call
in a visit, as Call.to_json gives it."""

ANSWER = (
  "Give one verdict per rubric criterion, using its id, then the summary."
)
"""The last line of every prompt of a review."""

NOTES = (
  "The author's notes follow, each version with the state and visit that"
  " wrote it. They are the author's account of the work, not evidence: check"
  " what they claim against the inputs."
)
"""What a prompt says before the author's notes."""

REREVIEW = (
  "This is a re-review. Judge every criterion of the rubric again, on the"
  " whole of the inputs as they are now, not only on what changed: that a"
  " criterion passed last time is no reason for it to pass now. The author's"
  " notes are the author's account of what they changed, not evidence: check"
  " each change they claim against the diffs, and take a change they claim"
  " that no diff shows as not made. You may fail a criterion you passed last"
  " time on content that has not changed since; when you do, say in its"
  " evidence that this part has not changed since your last review, and why"
  " your verdict on it then was wrong."
)
"""What a re-review asks of the reviewer, after its last verdicts and the
changes since."""

CROSS_CRITERIA = (
  "The other reviews of this work judge it by the criteria below. Before you"
  " write a fix, check it against each of them: no fix may make any of them"
  " fail. When a fix cannot avoid a conflict with one of them, say in the fix"
  " which criterion of which review it conflicts with."
)
"""What a prompt says before the criteria of the other reviews."""

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
    question: What is judged.
    check: How to establish it.
    pass_if: What a pass looks like.
    fail_if: What a fail looks like.
  """

  id: str
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
    title: Its review's name for people, in the report; None to name the
      review by its own name.
  """

  system: str
  criteria: tuple[Criterion, ...]
  title: str | None = None

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
    title = cast(object, raw.get("title"))
    if title is not None and (not isinstance(title, str) or not title.strip()):
      raise ValueError(f"{path}: title: not a non-empty string")
    for criterion in criteria:
      fields = cast(tuple[object, ...], dataclasses.astuple(criterion))
      if not all(isinstance(field, str) for field in fields):
        raise ValueError(f"{path}: {criterion.id}: a field is not a string")
    return cls(system, criteria, title)


@dataclass(frozen=True)
class Review:
  """One review of a RubricState (dataforge.curation.states).

  Attributes:
    name: Its name, unique in the state; the agent's name.
    rubric: The rubric's TOML file.
    inputs: What it judges, by the tag of its block, in order: a file is
      given as its text, a directory as each file in it.
    notes: The author's notes, by the tag of their block: the names of
      artifacts, each given as every version written since the last call of
      the review that gave verdicts, or since the attempt began.
  """

  name: str
  rubric: Path
  inputs: Mapping[str, Input]
  notes: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Call:
  """One call of a review in a visit, as its CALL_SUFFIX output keeps it.

  Attributes:
    call: Its number among the calls of the review in its state, from 1.
    blocks: The text of each input block as the prompt gives it, by tag.
    notes: How many versions each note's artifact had, by tag.
    verdicts: The answer that it gave or reused, as JSON data, or None when
      it has none.
    ending: How the run that gave the answer ended, if the runner says.
    reused: The number of the call whose answer it reused, or None when it
      ran the reviewer.
    resumed: Whether its run went on with the session of an earlier call.
    changed: The tags of the blocks that differ from those of the last call
      that gave verdicts before it, or None when there was none.
  """

  call: int
  blocks: Mapping[str, str]
  notes: Mapping[str, int]
  verdicts: object
  ending: str | None
  reused: int | None = None
  resumed: bool = False
  changed: tuple[str, ...] | None = None

  def judged(self, rubric: Rubric) -> bool:
    """Return whether it gave verdicts for every criterion of a rubric.

    Args:
      rubric: The review's rubric.

    Returns:
      Whether its answer has verdicts, each with every field a string, and
      one for each criterion.
    """
    given = {v["id"] for v in _verdicts(self.verdicts) or []}
    return all(c.id in given for c in rubric.criteria)

  def passed(self, rubric: Rubric) -> bool:
    """Return whether its answer passes a rubric, as judge_review judges it.

    Args:
      rubric: The review's rubric.

    Returns:
      Whether its answer has verdicts and a verdict "pass" for each
      criterion.
    """
    verdicts = _verdicts(self.verdicts)
    if verdicts is None:
      return False
    given = {v["id"]: v["verdict"] for v in verdicts}
    return all(given.get(c.id) == "pass" for c in rubric.criteria)

  def to_json(self) -> Data:
    """Return the call as JSON data.

    Returns:
      Its fields by name.
    """
    data = dataclasses.asdict(self)
    data["changed"] = None if self.changed is None else list(self.changed)
    return data

  @classmethod
  def load(cls, path: Path) -> Call:
    """Read a call that to_json gave.

    Args:
      path: Its JSON file.

    Returns:
      The call.

    Raises:
      ValueError: When the file is not a call.
    """
    try:
      data = cast(object, json.loads(path.read_text()))
    except (OSError, ValueError) as error:
      raise ValueError(f"{path}: not a review's call: {error!r}") from error
    raw = cast(dict[str, object], data) if isinstance(data, dict) else {}
    number, blocks, notes = raw.get("call"), raw.get("blocks"), raw.get("notes")
    ending, reused, changed = (
      raw.get("ending"),
      raw.get("reused"),
      raw.get("changed"),
    )
    resumed = raw.get("resumed")
    if not (
      _integer(number)
      and _mapping_of(blocks, str)
      and _mapping_of(notes, int)
      and (ending is None or isinstance(ending, str))
      and (reused is None or _integer(reused))
      and isinstance(resumed, bool)
      and (changed is None or _strings(changed))
    ):
      raise ValueError(f"{path}: not a review's call")
    return cls(
      number,
      blocks,
      notes,
      raw.get("verdicts"),
      ending,
      reused,
      resumed,
      None if changed is None else tuple(changed),
    )


@dataclass(frozen=True)
class Score:
  """How one criterion was judged.

  Attributes:
    criterion: The criterion's ID.
    passed: Whether its verdict was a pass.
    note: The reviewer's evidence, or "no verdict" when it gave none.
    fix: The reviewer's fix, as it gave it; empty when it gave no verdict.
  """

  criterion: str
  passed: bool
  note: str
  fix: str


@dataclass(frozen=True)
class Judgment:
  """How a review ended.

  Attributes:
    name: The review's name.
    errored: Whether its agent run itself failed.
    passed: Whether it gave verdicts and no criterion failed.
    findings: The failed criteria, in the rubric's order, with evidence and
      fix.
    data: What the visit's data keeps of it.
    scores: Each criterion's score, in the rubric's order.
    call: The number of its call (Call.call).
    resumed: Whether its run went on with an earlier call's session.
    reused: The call whose answer it reused, or None when it ran.
    changed: The tags of its blocks that changed (Call.changed).
  """

  name: str
  errored: bool
  passed: bool
  findings: str
  data: Data
  scores: tuple[Score, ...] = ()
  call: int = 1
  resumed: bool = False
  reused: int | None = None
  changed: tuple[str, ...] | None = None


def block_text(path: Path) -> str:
  """Return the text of an input block, escaped.

  Args:
    path: A file, given as its text, or a directory, given as each file in
      it; a file that is not text, UTF-8 that holds no NUL, only named.

  Returns:
    The text; each file's text and path HTML-escaped, and cut after
    MAX_FILE_CHARS characters.
  """
  if not path.is_dir():
    text = _read(path)
    return _binary(path, path.name) if text is None else text
  parts: list[str] = []
  for file in sorted(path.rglob("*")):
    if not file.is_file() or "__pycache__" in file.parts:
      continue
    at = str(file.relative_to(path))
    text = _read(file)
    if text is None:
      parts.append(_binary(file, at))
    else:
      parts.append(f'<file path="{html.escape(at)}">\n{text}\n</file>')
  return "\n".join(parts) or "None."


def notes_text(notes: Mapping[str, Sequence[Version]]) -> str:
  """Return the author's notes as the prompt gives them.

  Args:
    notes: The versions of each note to give, oldest first, by its tag.

  Returns:
    NOTES, then a block for each note with a block for each version, its
    text escaped as block_text does; empty when there are no notes.
  """
  if not notes:
    return ""
  blocks = [NOTES]
  for tag, versions in notes.items():
    text = "\n".join(
      f'<version state="{html.escape(v.state)}" visit="{v.visit}">\n'
      + f"{block_text(v.path)}\n</version>"
      for v in versions
    )
    blocks.append(f"<{tag}>\n{text or 'None.'}\n</{tag}>")
  return "\n\n".join(blocks)


def others_text(rubrics: Mapping[str, Rubric]) -> str:
  """Return the criteria of the other reviews as the prompt gives them.

  Args:
    rubrics: The rubric of each other review, by the review's name, in order.

  Returns:
    CROSS_CRITERIA, then a block with a block for each review that holds its
    criteria, their text escaped as block_text does; empty when there are no
    other reviews.
  """
  if not rubrics:
    return ""
  reviews = "\n\n".join(
    f'<review name="{html.escape(name)}">\n'
    + "\n\n".join(_escaped(c).render() for c in rubric.criteria)
    + "\n</review>"
    for name, rubric in rubrics.items()
  )
  return f"{CROSS_CRITERIA}\n\n<other_criteria>\n{reviews}\n</other_criteria>"


def review_prompt(
  rubric: Rubric,
  blocks: Mapping[str, str],
  notes: str = "",
  again: str = "",
  others: str = "",
) -> str:
  """Return the whole prompt of a review.

  Args:
    rubric: The rubric.
    blocks: The text of each block it judges, as block_text gives it, by its
      tag, in order.
    notes: The author's notes, as notes_text gives them.
    again: For a re-review, what rereview_prompt gives with whole=True.
    others: The criteria of the other reviews, as others_text gives them.

  Returns:
    The rubric's system text, the inputs as tagged blocks, the notes, the
    criteria, those of the other reviews, what a re-review adds and ANSWER.
  """
  criteria = "\n\n".join(c.render() for c in rubric.criteria)
  parts = [
    rubric.system.strip(),
    *(f"<{tag}>\n{text}\n</{tag}>" for tag, text in blocks.items()),
    notes,
    f"<rubric>\n{criteria}\n</rubric>",
    others,
    again,
    ANSWER,
  ]
  return "\n\n".join(part for part in parts if part)


def rereview_prompt(
  last: Call,
  blocks: Mapping[str, str],
  notes: str,
  *,
  whole: bool,
  later: Mapping[str, str] | None = None,
) -> str:
  """Return what a re-review gives after the reviewer's last verdicts.

  Args:
    last: The last call that gave verdicts.
    blocks: The text of each block now, as block_text gives it, by its tag,
      in order.
    notes: The author's notes since that call, as notes_text gives them.
    whole: True for the part review_prompt adds to the whole prompt, which
      already has the blocks and the notes; False for the prompt of a
      session that goes on, which gives also each changed block whole, and
      the notes.
    later: The blocks of the last call that ran after that call and gave no
      verdicts, if one did: the session may have read them last.

  Returns:
    The last verdicts, a diff of each changed block, the tags of those that
    did not change, then REREVIEW, and for whole=False also ANSWER; for
    whole=False, an unchanged block that later read otherwise is given whole
    too.
  """
  parts = [
    f"Your verdicts in your last review, call {last.call}:",
    f"<previous_verdicts>\n{_previous(last.verdicts)}\n</previous_verdicts>",
  ]
  unchanged: list[str] = []
  for tag, text in blocks.items():
    before = last.blocks.get(tag, "")
    if before == text:
      unchanged.append(tag)
      continue
    diff = difflib.unified_diff(
      before.splitlines(),
      text.splitlines(),
      f"{tag} (call {last.call})",
      f"{tag} (now)",
      lineterm="",
    )
    parts.append(f'<diff block="{tag}">\n' + "\n".join(diff) + "\n</diff>")
    if not whole:
      parts.append(f"<{tag}>\n{text}\n</{tag}>")
  later = {} if whole or later is None else later
  read = [
    tag for tag in unchanged if later.get(tag, blocks[tag]) == blocks[tag]
  ]
  if read:
    seen = "" if whole else ", as you read them then"
    parts.append(f"Unchanged since your last review{seen}: {', '.join(read)}.")
  if other := [tag for tag in unchanged if tag not in read]:
    which = ", ".join(other)
    parts.append(
      "Unchanged since your last review, but not as your later call, which"
      + f" gave no verdicts, read them: {which}. As they are now:"
    )
    parts += [f"<{tag}>\n{blocks[tag]}\n</{tag}>" for tag in other]
  if not whole and notes:
    parts.append(notes)
  parts.append(REREVIEW)
  if not whole:
    parts.append(ANSWER)
  return "\n\n".join(parts)


def judge_review(
  name: str,
  rubric: Rubric,
  result: ActionResult,
  *,
  earlier: ActionResult | None = None,
  unretried: str | None = None,
) -> Judgment:
  """Read a review's verdicts against its rubric.

  A review that gave no verdicts has the finding `<name> returned no
  verdicts (<how its run ended>).`, where how it ended is its ending, and
  its fault and error when it has them; after a retry, it names how both
  runs ended, and without one it says why there was none.

  Args:
    name: The review's name.
    rubric: The rubric.
    result: The review's agent action, its retry's when it was retried.
    earlier: The action whose run gave no verdicts, when result is its
      retry.
    unretried: Why a review that gave no verdicts was not retried, if it
      was not.

  Returns:
    How it ended; a criterion with no verdict counts as failed. Its data's
    cost_usd is what its actions cost, None when one did not report it.
  """
  run = result.run
  ending = None if run is None else run.ending
  verdicts = _verdicts(result.value)
  actions = [result] if earlier is None else [earlier, result]
  cost = (
    sum(action.cost_usd for action in actions)
    if all(action.cost_known for action in actions)
    else None
  )
  given = {} if verdicts is None else {v["id"]: v for v in verdicts}
  failed = [
    c.id
    for c in rubric.criteria
    if c.id not in given or given[c.id]["verdict"] != "pass"
  ]
  if verdicts is None and earlier is not None:
    findings = (
      f"{name} returned no verdicts twice: first ({_ended(earlier)}), then"
      + f" on a retry ({_ended(result)})."
    )
  elif verdicts is None:
    findings = f"{name} returned no verdicts ({_ended(result)})."
    if unretried is not None:
      findings += f" It was not retried: {unretried}."
  else:
    findings = "\n\n".join(
      f"{cid}: no verdict."
      if cid not in given
      else f"{cid}: {given[cid]['evidence']}\nFix: {given[cid]['fix']}"
      for cid in failed
    )
  passed = verdicts is not None and not failed
  scores = tuple(
    Score(c.id, False, "no verdict", "")
    if c.id not in given
    else Score(
      c.id,
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
      "cost_usd": cost,
      "passed": passed,
      "failed": failed,
      "verdicts": result.value if verdicts is not None else None,
      "fault": None if run is None else run.fault,
      "error": None if run is None else run.error,
      "retried": earlier is not None,
    },
    scores,
  )


def _ended(result: ActionResult) -> str:
  # How a review's run ended, for a finding: its ending, then its fault and
  # the error it ended on, when it has them.
  run = result.run
  if run is None:
    return "no run"
  parts = [str(run.ending)]
  if run.fault is not None:
    parts.append(
      run.fault if run.error is None else f"{run.fault}: {run.error}"
    )
  return "; ".join(parts)


def rubric_table(judgments: Sequence[Judgment]) -> Data:
  """Return what reviews judged, in a shape the same for any dataset.

  Args:
    judgments: The reviews' judgments, in order.

  Returns:
    {"passed": every review passed, "reviews": [{"name", "passed", "ending",
    "call", "resumed", "reused", "changed", "criteria": [{"criterion",
    "passed", "note", "fix"}]}]}, as JSON data; "call", "resumed", "reused"
    and "changed" are the judgment's.
  """
  return {
    "passed": all(judgment.passed for judgment in judgments),
    "reviews": [
      {
        "name": judgment.name,
        "passed": judgment.passed,
        "ending": judgment.data.get("ending"),
        "call": judgment.call,
        "resumed": judgment.resumed,
        "reused": judgment.reused,
        "changed": (
          None if judgment.changed is None else list(judgment.changed)
        ),
        "criteria": [dataclasses.asdict(score) for score in judgment.scores],
      }
      for judgment in judgments
    ],
  }


def rubric_report(
  judgments: Sequence[Judgment], rubrics: Mapping[str, Rubric]
) -> str:
  """Return what reviews judged as a Markdown report for people.

  A line on the whole, then for each review a heading, its rubric's title or
  else its name, a table of its criteria, each by its question and then its
  ID, and a paragraph on what failed. A cell is one line, with "&", "<", ">"
  and "|" escaped, so that neither a reviewer's words nor a rubric's end the
  table or become HTML; so is a heading.

  Args:
    judgments: The reviews' judgments, in order.
    rubrics: Each review's rubric, by the review's name.

  Returns:
    The report.
  """

  def title(judgment: Judgment) -> str:
    rubric = rubrics.get(judgment.name)
    return _cell(
      judgment.name if rubric is None or rubric.title is None else rubric.title
    )

  failed = [title(judgment) for judgment in judgments if not judgment.passed]
  whole = (
    "Every review passes." if not failed else f"Failed: {', '.join(failed)}."
  )
  parts = ["# Rubric report", whole]
  for judgment in judgments:
    result = "passes" if judgment.passed else "fails"
    rubric = rubrics.get(judgment.name)
    questions = (
      {} if rubric is None else {c.id: c.question for c in rubric.criteria}
    )
    rows = [
      "| Criterion | Result | Note |",
      "| --- | --- | --- |",
      *(
        _row(score, questions.get(score.criterion)) for score in judgment.scores
      ),
    ]
    parts += [
      f"## {title(judgment)} {result}{_reused(judgment)}",
      "\n".join(rows),
      _summary(judgment),
    ]
  return "\n\n".join(parts) + "\n"


def _reused(judgment: Judgment) -> str:
  return (
    "" if judgment.reused is None else f" (reused from call {judgment.reused})"
  )


def _row(score: Score, question: str | None) -> str:
  result = "pass" if score.passed else "fail"
  criterion = (
    _cell(score.criterion)
    if question is None
    else f"{_cell(question)} ({_cell(score.criterion)})"
  )
  cells = (criterion, result, _cell(score.note))
  return f"| {' | '.join(cells)} |"


def _summary(judgment: Judgment) -> str:
  if judgment.data.get("verdicts") is None:
    # The finding holds the error a run ended on, which is not ours.
    return _cell(judgment.findings)
  scores = judgment.scores
  bad = [s.criterion for s in scores if not s.passed]
  if not bad:
    return f"All {len(scores)} criteria pass."
  return f"{len(bad)} of {len(scores)} criteria fail: {', '.join(bad)}."


def _cell(text: str) -> str:
  # A cell holds a reviewer's words, which are not to be trusted: it is one
  # line, "|" would end it, and HTML in it is shown as text.
  line = " ".join(text.split())
  for char, escaped in _CELL_ESCAPES:
    line = line.replace(char, escaped)
  return line


# "&" first, since the other escapes hold it.
_CELL_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("|", "\\|"))


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


def _previous(value: object) -> str:
  verdicts = _verdicts(value) or []
  lines = [
    f"### {_escape(v['id'])}: {_escape(v['verdict'])}\n"
    + f"Evidence: {_escape(v['evidence'])}\nFix: {_escape(v['fix'])}"
    for v in verdicts
  ]
  if isinstance(value, dict):
    summary = cast(dict[str, object], value).get("summary")
    if isinstance(summary, str):
      lines.append(f"Summary: {_escape(summary)}")
  return "\n\n".join(lines)


def _read(path: Path) -> str | None:
  # None for a file that is not strict UTF-8 or holds a NUL. Read a part at
  # a time, so that a large binary file stops at its first part and a large
  # text file is not held whole.
  kept, size = "", 0
  try:
    with path.open(encoding="utf-8") as file:
      while part := file.read(MAX_FILE_CHARS):
        if "\0" in part:
          return None
        kept += part[: max(MAX_FILE_CHARS - size, 0)]
        size += len(part)
  except UnicodeDecodeError:
    return None
  if size <= MAX_FILE_CHARS:
    return _escape(kept)
  return f"{_escape(kept)}\n... [cut, {size} characters in all]"


def _binary(path: Path, at: str) -> str:
  size = path.stat().st_size
  return f'<file path="{html.escape(at)}" skipped="binary" bytes="{size}"/>'


def _integer(value: object) -> TypeIs[int]:
  return isinstance(value, int) and not isinstance(value, bool)


def _mapping_of[T](value: object, kind: type[T]) -> TypeIs[dict[str, T]]:
  if not isinstance(value, dict):
    return False
  items = cast(dict[object, object], value).items()
  return all(
    isinstance(k, str)
    and isinstance(v, kind)
    and (kind is not int or not isinstance(v, bool))
    for k, v in items
  )


def _strings(value: object) -> TypeIs[list[str]]:
  return isinstance(value, list) and all(
    isinstance(item, str) for item in cast(list[object], value)
  )


def _escape(text: str) -> str:
  # What an author or a reviewer wrote: "&", "<" and ">" escaped, so that it
  # can neither close the block it is in nor open another.
  return html.escape(text, quote=False)


def _escaped(criterion: Criterion) -> Criterion:
  # Another review's criterion, which its rubric's author wrote: escaped as
  # what an author wrote is, since it is given inside a block.
  return dataclasses.replace(
    criterion,
    id=_escape(criterion.id),
    question=_escape(criterion.question),
    check=_escape(criterion.check),
    pass_if=_escape(criterion.pass_if),
    fail_if=_escape(criterion.fail_if),
  )


def _flat(text: str) -> str:
  return " ".join(text.split())
