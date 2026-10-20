"""A synthetic site for the viewer's tests."""

from dataclasses import dataclass
from pathlib import Path
import shutil

FIXTURES = Path(__file__).parent / "fixtures"

PROJECT = """\
id: example
name: Example
color: "#e5a54b"
attempts_dir: attempts
attempts: "*/*"
record: record.json
item: {depth: 1, title: Batch}
statuses:
  passed: {label: Passed, tone: good}
columns:
  - {title: Status, field: [status]}
  - {title: Cost, field: [budget, cost_usd], format: usd}
  - {title: Missing, field: [absent, key]}
"""


@dataclass(frozen=True)
class Tree:
  site: Path
  project: Path
  root: Path
  attempt: Path


def make_tree(tmp_path: Path) -> Tree:
  """Make a site with one project, one attempt and a file outside the root.

  Args:
    tmp_path: The directory to make it in.

  Returns:
    Where its files are.
  """
  _ = (tmp_path / "outside.txt").write_text("a host file\n")
  project = tmp_path / "project"
  attempt = project / "attempts" / "batch-1" / "task-1"
  (attempt / "artifacts" / "write" / "1" / "task").mkdir(parents=True)
  _ = shutil.copy(FIXTURES / "record.json", attempt / "record.json")
  task = attempt / "artifacts" / "write" / "1" / "task"
  _ = (task / "README.md").write_text("# A task\n")
  _ = (project / "viz-project.yaml").write_text(PROJECT)
  site = tmp_path / "site.yaml"
  _ = site.write_text(
    "listen: {host: 127.0.0.1, port: 8800}\n"
    + "projects: [project/viz-project.yaml]\n"
  )
  return Tree(site, project / "viz-project.yaml", project / "attempts", attempt)


QUEUE = """\
queues: {fast: 2, slow: 1}
max_running: 3
min_mem_available_mib: 0
max_load1: 100
database: queue.sqlite
logs: logs
"""

ACTIONS = """\
lock: .lock
actions:
  - name: again
    label: Run again
    queue: fast
    argv: [run-item, --item, "{item}", "--note=from {item}"]
    cwd: {cwd}
    memory_mib: 2048
    key: "example:{item}"
    when: idle
  - name: go-on
    label: Go on
    queue: slow
    argv: [run-item, --go-on, "{item}"]
    cwd: {cwd}
    memory_mib: 1024
    key: "example:{item}"
    when: stopped
"""


@dataclass(frozen=True)
class QueueTree:
  tree: Tree
  queue: Path
  work: Path


def make_queue_tree(tmp_path: Path) -> QueueTree:
  """Make the site of make_tree with a job queue and two actions.

  Args:
    tmp_path: The directory to make it in.

  Returns:
    Where its files are; the queue's file is under queue, and each action
    runs in work.
  """
  tree = make_tree(tmp_path)
  queue = tmp_path / "queue"
  queue.mkdir()
  _ = (queue / "queue.yaml").write_text(QUEUE)
  work = tmp_path / "work"
  work.mkdir()
  project = PROJECT.replace(
    "item: {depth: 1, title: Batch}",
    'item: {depth: 1, title: Batch, pattern: "batch-[0-9]+"}',
  )
  # str.replace, not format: the templates hold {item}.
  _ = tree.project.write_text(project + ACTIONS.replace("{cwd}", str(work)))
  _ = tree.site.write_text(
    tree.site.read_text() + "queue: {config: queue/queue.yaml}\n"
  )
  return QueueTree(tree, queue, work)
