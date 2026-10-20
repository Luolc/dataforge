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
