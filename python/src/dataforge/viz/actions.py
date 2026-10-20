"""A data item's actions: whether each may be submitted, and submitting one.

An action puts one job in a queue of dataforge.queue. Its command is the
project file's argv with the item's ID in place of the placeholder, run with
no shell; nothing of a request but the action's name and the item's ID goes
into the job.
"""

from dataforge import queue
from dataforge.viz import live
from dataforge.viz.config import Action, PLACEHOLDER, Project, split_relative
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


def fill(template: str, item: str) -> str:
  """Put an item's ID in place of the placeholder.

  Args:
    template: An argument or a key of an action.
    item: The item's ID, which check_item passed.

  Returns:
    The template with each PLACEHOLDER replaced by the ID.
  """
  return template.replace(PLACEHOLDER, item)


def unavailable(
  action: Action,
  item: str,
  rows: list[dict[str, object]],
  open_jobs: list[queue.Job],
) -> str | None:
  """Say why an action may not be submitted for an item now.

  Args:
    action: The action.
    item: The item's ID.
    rows: The listing's rows of the item's attempts, newest modified first,
      with live and held marked.
    open_jobs: The jobs queued or running now.

  Returns:
    The reason, for a person; None when the action may be submitted.
  """
  key = fill(action.key, item)
  holder = next((j for j in open_jobs if j.dedup_key == key), None)
  if holder is not None:
    return f"Job {holder.id} for this item is {holder.state} in {holder.queue}."
  if action.when == "idle" and any(r["held"] is True for r in rows):
    return "An attempt of this item is running: its lock is held."
  if action.when == "stopped":
    newest = next((r for r in rows if r["error"] is None), None)
    if newest is None:
      return "No attempt of this item can be read."
    if newest["live"] is None:
      return "The newest attempt of this item has ended."
    if newest["live"] != live.MAY_HAVE_STOPPED:
      return "The newest attempt of this item is running."
  return None


def submit(
  config: queue.Config, project: Project, action: Action, item: str
) -> queue.Job:
  """Put an action for an item in its queue.

  Args:
    config: The queue's configuration.
    project: The project.
    action: The action, one of the project's.
    item: The item's ID, which check_item passed.

  Returns:
    The job queued.

  Raises:
    DuplicateError: When a job of the same key is queued or running.
  """  # noqa: DOC502
  return queue.submit(
    config,
    action.queue,
    [fill(arg, item) for arg in action.argv],
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
