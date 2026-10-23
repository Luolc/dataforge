"""A data item's actions: whether each may be submitted, and submitting one.

An action puts one job in a queue of dataforge.queue. Its command is the
project file's argv with the item's ID in place of the placeholder, then each
of its parameters as "<flag>=<value>", run with no shell; nothing of a request
but the action's name, the item's ID and a number for each parameter it
declares goes into the job.
"""

from collections.abc import Callable, Mapping, Sequence

from dataforge import queue
from dataforge.viz import live, runs
from dataforge.viz.config import (
  Action,
  number,
  NUMBER_LIMIT,
  PLACEHOLDER,
  plain,
  Project,
  split_relative,
)
from dataforge.viz.paths import RefusedError

ITEM_LIMIT = 200
"""The most characters an item ID may have."""

OPEN = frozenset({"queued", "running"})
"""The states of a job that holds its key."""

ENDED = frozenset({"done", "failed", "canceled"})
"""The states of a job that has ended."""


def check_item(project: Project, item: str) -> None:
  """Check that a data item's ID may go into a command.

  Args:
    project: The project, whose item.pattern the ID must match.
    item: The item's ID.

  Raises:
    RefusedError: 400 when the ID does not match item.pattern as a whole, is
      longer than ITEM_LIMIT, or is not item.depth plain components none of
      which starts with "." or "-".
  """
  parts = split_relative(item)
  pattern = project.item.pattern
  if (
    pattern is None
    or len(item) > ITEM_LIMIT
    or not parts
    or len(parts) != project.item.depth
    or any(part.startswith((".", "-")) for part in parts)
    or not pattern.fullmatch(item)
  ):
    raise RefusedError(400, f"{item!r} is not an item ID of {project.id}")


def param_args(action: Action, given: Mapping[str, str]) -> tuple[str, ...]:
  """Turn the numbers a submit gives into the arguments of the command.

  Args:
    action: The action.
    given: Each parameter's value as the request carries it, by name; one
      not there takes its default. Names the action does not declare must
      have been refused already.

  Returns:
    "<flag>=<value>" for each of the action's parameters, in order, the
    value in plain decimal notation.

  Raises:
    RefusedError: 400 when a value is not NUMBER of at most NUMBER_LIMIT
      characters, or is not from its parameter's min to max.
  """
  args: list[str] = []
  for param in action.params:
    text = given.get(param.name)
    value = param.default if text is None else number(text)
    if value is None:
      raise RefusedError(
        400,
        f"{param.name}: {text!r} is not a decimal number of at most"
        + f" {NUMBER_LIMIT} characters and 6 decimal places, as 12 or 12.5",
      )
    if not param.min <= value <= param.max:
      raise RefusedError(
        400,
        f"{param.name}: {plain(value)} is not from {plain(param.min)}"
        + f" to {plain(param.max)}",
      )
    args.append(f"{param.flag}={plain(value)}")
  return tuple(args)


def fill(template: str, item: str) -> str:
  """Put an item's ID in place of the placeholder.

  Args:
    template: An argument or a key of an action.
    item: The item's ID, which check_item passed.

  Returns:
    The template with each PLACEHOLDER replaced by the ID.
  """
  return template.replace(PLACEHOLDER, item)


def newest_row(rows: list[dict[str, object]]) -> dict[str, object] | None:
  """Find the attempt an action with when "stopped" goes on with.

  Args:
    rows: The listing's rows of an item's attempts, newest modified first.

  Returns:
    The newest row whose record can be read; None when there is none.
  """
  return next((r for r in rows if r["error"] is None), None)


def newest_attempt(
  rows: list[dict[str, object]], found: runs.Found | None
) -> str | None:
  """Find the ID of the attempt an action with when "stopped" goes on with.

  Args:
    rows: The listing's rows of an item's attempts, newest modified first.
    found: With runs, the item's rows in the store, the oldest first, which
      then decide in place of rows.

  Returns:
    The store's newest row's, with runs; else the newest row's whose record
    can be read; None when there is none.
  """
  if found is not None:
    return (
      None if found.error is not None or not found.rows else found.rows[-1].id
    )
  newest = newest_row(rows)
  return None if newest is None else str(newest["id"])


def unavailable(
  project: Project,
  action: Action,
  item: str,
  rows: list[dict[str, object]],
  open_jobs: list[queue.Job],
  found: runs.Found | None = None,
) -> str | None:
  """Say why an action may not be submitted for an item now.

  Args:
    project: The project, whose resumable statuses let an action with when
      "stopped" go on with an attempt that has ended.
    action: The action.
    item: The item's ID.
    rows: The listing's rows of the item's attempts, newest modified first,
      with live and held marked.
    open_jobs: The jobs queued or running now.
    found: With runs, the item's rows in the store, which then decide when
      in place of rows.

  Returns:
    The reason, for a person; None when the action may be submitted.
  """
  key = fill(action.key, item)
  holder = next((j for j in open_jobs if j.dedup_key == key), None)
  if holder is not None:
    return f"Job {holder.id} for this item is {holder.state} in {holder.queue}."
  if found is not None:
    return runs.unavailable(action.when, found)
  if action.when == "idle" and any(r["held"] is True for r in rows):
    return "An attempt of this item is running: its lock is held."
  if action.when == "stopped":
    newest = newest_row(rows)
    if newest is None:
      return "No attempt of this item can be read."
    if newest["live"] is None:
      if newest["status"] not in project.resumable:
        return _not_resumable(project, action, item, newest)
      if newest["held"] is True:
        return "An attempt of this item is running: its lock is held."
      return None
    if newest["live"] != live.MAY_HAVE_STOPPED:
      return "The newest attempt of this item is running."
  return None


def _not_resumable(
  project: Project, action: Action, item: str, newest: dict[str, object]
) -> str:
  # Why an action with when "stopped" may not go on with the item's newest
  # attempt, which has ended: what it needs, and how that one ended.
  def label(status: object) -> str:
    if not isinstance(status, str):
      return "with no status"
    return project.statuses.get(status, {}).get("label", status)

  needs = " or ".join(
    ["may have stopped", *(f"ended {label(s)}" for s in project.resumable)]
  )
  stamp = str(newest["id"]).removeprefix(f"{item}/")
  return (
    f"{action.label} needs an attempt that {needs}; the newest, {stamp},"
    + f" ended {label(newest['status'])}."
  )


def submit(
  config: queue.Config,
  project: Project,
  action: Action,
  item: str,
  args: tuple[str, ...] = (),
) -> queue.Job:
  """Put an action for an item in its queue.

  Args:
    config: The queue's configuration.
    project: The project.
    action: The action, one of the project's.
    item: The item's ID, which check_item passed.
    args: The arguments param_args made of the action's parameters.

  Returns:
    The job queued.

  Raises:
    DuplicateError: When a job of the same key is queued or running.
  """  # noqa: DOC502
  return queue.submit(
    config,
    action.queue,
    [*(fill(arg, item) for arg in action.argv), *args],
    cwd=action.cwd,
    memory_mib=action.memory_mib,
    key=fill(action.key, item),
    labels={"project": project.id, "action": action.name, "item": item},
  )


def public(job: queue.Job) -> dict[str, object]:
  """Return what a page shows of a job.

  Args:
    job: The job.

  Returns:
    The job as JSON data, without its command, directory, key, process and
    log, so that no page shows what the server runs or where it keeps
    anything.
  """
  return {
    "id": job.id,
    "queue": job.queue,
    "labels": job.labels,
    "state": job.state,
    "memory_mib": job.memory_mib,
    "queued_at": job.queued_at,
    "started_at": job.started_at,
    "ended_at": job.ended_at,
    "exit_code": job.exit_code,
    "cancel_requested_at": job.cancel_requested_at,
  }


def of_project(project: Project, job: queue.Job) -> bool:
  """Return whether a job is a project's, as a page shows it.

  Args:
    project: The project.
    job: The job.

  Returns:
    Whether its project label is the project's ID or, with no project
    label, its action is one of the project's jobs.
  """
  named = job.labels.get("project")
  if named is not None:
    return named == project.id
  return any(kind.action == job.labels.get("action") for kind in project.jobs)


Queued = Callable[[str, str], dict[str, object]]
"""Gives, for a job action of a project and an item label, what queued()
gives."""


def queued(
  project: Project,
  jobs: Sequence[queue.Job],
  pauses: Sequence[queue.Pause],
) -> Queued:
  """Find the newest job of an action for an item, and its queue's pause.

  Args:
    project: The project, which names the actions in its jobs.
    jobs: The queue's jobs, newest first.
    pauses: The queue's pauses.

  Returns:
    A function that takes the action of one of the project's jobs and an
    item label and gives {job, paused, reason}: job the newest of the
    project's jobs of that action and item label, as public() gives it, or
    null; paused the pause of its queue, or of the queue the project's jobs
    name when there is none, {queue, paused_at, reason}, null when that
    queue is not paused; reason null.
  """
  newest: dict[tuple[str, str], queue.Job] = {}
  for job in jobs:
    action, item = job.labels.get("action"), job.labels.get("item")
    if action is not None and item is not None and of_project(project, job):
      _ = newest.setdefault((action, item), job)
  paused = {pause.queue: pause for pause in pauses}
  named = {kind.action: kind.queue for kind in project.jobs}

  def find(action: str, item: str) -> dict[str, object]:
    job = newest.get((action, item))
    pause = paused.get(named[action] if job is None else job.queue)
    return {
      "job": None if job is None else public(job),
      "paused": None if pause is None else pause.to_json(),
      "reason": None,
    }

  return find


def unread(reason: str) -> Queued:
  """Give what queued() gives when the queue cannot be read.

  Args:
    reason: Why.

  Returns:
    A function that gives {job, paused, reason}, job and paused null, for
    any action and item.
  """
  return lambda _action, _item: {"job": None, "paused": None, "reason": reason}
