"""The statements that wrote a store of attempts at schema 1.0.

They are dataforge.runs.store's as it wrote them then, kept here unchanged
when the schema grows, so that a test runs them on the schema of today, as a
holder of that version would.
"""

import sqlite3

from dataforge.queue.proc import Leader

ADD = """
INSERT INTO attempt (id, item, root, state, outcome, reason, machine_status,
  stopped_by, record, cost_usd, progress_at, job_id, job_db, resumable,
  resume_problem, resume_checked_at, created_at, ended_at, settled_at, extra)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_HOLDER = "holder_pid = ? AND holder_starttime = ? AND holder_boot_id = ?"

INSERT = """
INSERT INTO attempt (id, item, root, state, holder_pid, holder_starttime,
  holder_boot_id, job_id, job_db, created_at, progress_at)
VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
"""

STEP = f"""
UPDATE attempt SET record = ?, machine_status = ?, cost_usd = ?,
  progress_at = ?
WHERE id = ? AND state = 'running' AND {_HOLDER}
"""

END = f"""
UPDATE attempt SET state = 'ended', outcome = ?, reason = ?,
  machine_status = ?, record = ?, cost_usd = ?, ended_at = ?
WHERE id = ? AND state = 'running' AND {_HOLDER}
"""

INTERRUPT = f"""
UPDATE attempt SET state = 'interrupted', stopped_by = ?, resumable = ?,
  resume_problem = ?, resume_checked_at = ?
WHERE id = ? AND state = 'running' AND {_HOLDER}
"""

TAKE = """
UPDATE attempt SET state = 'running', stopped_by = NULL, resumable = NULL,
  resume_problem = NULL, resume_checked_at = NULL, holder_pid = ?,
  holder_starttime = ?, holder_boot_id = ?, job_id = ?, job_db = ?,
  progress_at = ?
WHERE id = ? AND state = 'interrupted'
"""

ABANDON = """
UPDATE attempt SET state = 'ended', outcome = ?, reason = ?, ended_at = ?
WHERE id = ? AND state = 'interrupted' AND machine_status IS NULL
"""

CHECK = """
UPDATE attempt SET resumable = ?, resume_problem = ?, resume_checked_at = ?,
  cost_usd = coalesce(?, cost_usd)
WHERE id = ? AND state = 'interrupted'
"""

NEWER_RECORD = """
UPDATE attempt SET record = ?, machine_status = ?
WHERE id = ? AND state = 'interrupted'
"""

SETTLE = """
UPDATE attempt SET settled_at = ?
WHERE id = ? AND state = 'ended' AND settled_at IS NULL
"""

EVENT = """
INSERT INTO event (attempt_id, at, from_state, to_state, by, detail)
VALUES (?, ?, ?, ?, ?, ?)
"""

AT = "2026-01-01T00:00:00+00:00"


def transitions(conn: sqlite3.Connection, me: Leader, gone: Leader) -> None:
  """Run every statement, each changing one row, on a store of today.

  It leaves a/1 ended and settled after it was interrupted and taken again,
  b/1 ended as given up, c/1 interrupted, not resumable, and d/1 added
  ended and settled, with a field of its own.

  Args:
    conn: A connection to the store.
    me: A holder that runs.
    gone: A holder that is gone.
  """

  def one(sql: str, *params: object) -> None:
    assert conn.execute(sql, params).rowcount == 1, sql

  record = '{"status": "passed", "visits": []}'
  one(INSERT, "a/1", "a", "/r/a", *gone, None, None, AT, AT)
  one(EVENT, "a/1", AT, None, "running", "runner", "{}")
  one(STEP, "{}", None, 0.5, AT, "a/1", *gone)
  one(INTERRUPT, "lost", None, None, None, "a/1", *gone)
  one(EVENT, "a/1", AT, "running", "interrupted", "reconcile", "{}")
  one(NEWER_RECORD, record, "passed", "a/1")
  one(TAKE, *me, None, None, AT, "a/1")
  one(EVENT, "a/1", AT, "interrupted", "running", "reconcile", "{}")
  one(END, "passed", None, "passed", record, 0.5, AT, "a/1", *me)
  one(EVENT, "a/1", AT, "running", "ended", "reconcile", "{}")
  one(SETTLE, AT, "a/1")
  one(INSERT, "b/1", "b", "/r/b", *me, 1, "/q", AT, AT)
  one(INTERRUPT, "person", None, None, None, "b/1", *me)
  one(ABANDON, "given_up", "b/2 started in its place", AT, "b/1")
  one(INSERT, "c/1", "c", "/r/c", *me, None, None, AT, AT)
  one(INTERRUPT, "lost", 0, "gone", AT, "c/1", *me)
  one(CHECK, 0, "gone", AT, 1.0, "c/1")
  one(
    ADD,
    *("d/1", "d", "/r/d", "ended", "passed", None, None, None, None, None),
    *(None, None, None, None, None, None, AT, AT, AT, '{"stage": "ab"}'),
  )
  one(EVENT, "d/1", AT, None, "ended", "add", "{}")
