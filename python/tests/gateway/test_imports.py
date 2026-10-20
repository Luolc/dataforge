"""What the gateway imports, and who imports it.

Invariant 30: the core modules import no module of `dataforge.gateway`, which
imports only the standard library and dataforge, but for
`dataforge.gateway.harbor`; the forwarder runs on Python 3.8 alone.
"""

import ast
from pathlib import Path
import subprocess
import sys

import dataforge.gateway

GATEWAY = Path(dataforge.gateway.__file__).parent
CHECK = """
import sys
import dataforge, dataforge.curation, dataforge.runtime, dataforge.cli
print(sorted(m for m in sys.modules if m.startswith("dataforge.gateway")))
"""


def test_the_core_modules_import_no_gateway_module():
  found = subprocess.run(
    [sys.executable, "-I", "-c", CHECK],
    capture_output=True,
    text=True,
    check=True,
    timeout=60,
  )
  assert found.stdout.strip() == "[]"


def imports(path: Path) -> set[str]:
  # The top-level names of the modules a file imports.
  found: set[str] = set()
  for node in ast.walk(ast.parse(path.read_text())):
    if isinstance(node, ast.Import):
      found |= {alias.name.split(".")[0] for alias in node.names}
    elif isinstance(node, ast.ImportFrom):
      assert node.level == 0, f"{path.name} imports relatively"
      found.add((node.module or "").split(".")[0])
  return found


def test_the_gateway_imports_only_the_standard_library_and_dataforge():
  files = sorted(GATEWAY.glob("*.py"))
  assert {f.name for f in files} >= {"proxy.py", "session.py", "harbor.py"}
  for path in files:
    if path.name == "harbor.py":
      continue
    other = imports(path) - set(sys.stdlib_module_names) - {"dataforge"}
    assert other == set(), path.name
  assert "harbor" in imports(GATEWAY / "harbor.py")


def test_the_forwarder_needs_only_python_3_8():
  forwarder = GATEWAY / "forward.py"
  _ = ast.parse(forwarder.read_text(), feature_version=(3, 8))
  assert imports(forwarder) <= set(sys.stdlib_module_names)
