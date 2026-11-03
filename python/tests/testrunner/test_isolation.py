import functools
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import cast

import pytest

from dataforge.testrunner import core, isolation
from dataforge.testrunner.core import Isolation, load, run
from dataforge.testrunner.core import Test as Listed
from tests.testrunner.projects import write

needs_landlock = pytest.mark.skipif(
  isolation.abi() < isolation.MIN_ABI, reason="no Landlock of ABI 3 or later"
)
CONFINE = isolation.abi() >= isolation.MIN_ABI

# Runs a manifest's tests with an isolation in a process of its own, as run
# makes the process that calls it a child subreaper, and prints each test's
# name, status and message.
RUN = """import json, sys
from pathlib import Path
from dataforge.testrunner.core import Isolation, load, run
manifest, tests, app, private, writable, confine = sys.argv[1:]
found = run(
  load(Path(manifest).read_text()),
  tests=Path(tests),
  app=Path(app),
  isolation=Isolation(
    Path(private), tuple(map(Path, json.loads(writable))), confine == "1"
  ),
)
tests = [[t["name"], t["status"], t.get("message")] for t in found.tests]
print(json.dumps(tests))
"""


def isolated(
  tmp_path: Path,
  manifest: str,
  app: dict[str, str],
  tests: dict[str, str],
  env: dict[str, str] | None = None,
) -> list[list[object]]:
  write(tmp_path / "app", app | {"keep.txt": ""})
  write(tmp_path / "tests", tests | {"manifest.toml": manifest})
  (tmp_path / "private").mkdir()
  done = subprocess.run(
    [
      sys.executable,
      "-c",
      RUN,
      str(tmp_path / "tests" / "manifest.toml"),
      str(tmp_path / "tests"),
      str(tmp_path / "app"),
      str(tmp_path / "private"),
      json.dumps([str(tmp_path / "app"), "/dev"]),
      "1" if CONFINE else "0",
    ],
    check=True,
    capture_output=True,
    text=True,
    env=None if env is None else os.environ | env,
  )
  return cast("list[list[object]]", json.loads(done.stdout.splitlines()[-1]))


def stat_line(pid: int, ppid: int, state: str, start: int) -> str:
  # A /proc/<pid>/stat line, its command name holding ") ".
  return f"{pid} (a) b) {state} {ppid} {' '.join(['0'] * 17)} {start}\n"


def test_seal_kills_each_process_under_the_root_but_keep_and_its_ancestors(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(isolation, "PAUSE", 0)
  # pid: parent, state; keep is 12, under 11, under the root 10.
  processes = {
    1: (0, "S"),
    10: (1, "S"),
    11: (10, "S"),
    12: (11, "S"),
    13: (12, "S"),
    14: (10, "Z"),
    15: (1, "S"),
    16: (13, "R"),
  }
  for pid, (ppid, state) in processes.items():
    write(tmp_path, {f"{pid}/stat": stat_line(pid, ppid, state, pid * 100)})
  sent: list[tuple[int, int]] = []

  def kill(pid: int, start: int) -> None:
    sent.append((pid, start))
    (tmp_path / str(pid) / "stat").unlink()
    (tmp_path / str(pid)).rmdir()

  killed = isolation.seal(10, 12, proc=tmp_path, kill=kill)
  assert killed == [13, 16]
  assert sent == [(13, 1300), (16, 1600)]
  assert sorted(int(p.name) for p in tmp_path.iterdir()) == [
    1,
    10,
    11,
    12,
    14,
    15,
  ]


def test_seal_gives_up_on_a_process_that_does_not_end(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(isolation, "PAUSE", 0)
  monkeypatch.setattr(isolation, "TRIES", 2)
  write(tmp_path, {"10/stat": stat_line(10, 1, "S", 1)})
  write(tmp_path, {"11/stat": stat_line(11, 10, "S", 2)})
  sent: list[int] = []
  with pytest.raises(RuntimeError, match=r"\[11\]"):
    _ = isolation.seal(10, proc=tmp_path, kill=lambda pid, _: sent.append(pid))
  assert sent == [11, 11]


def zombie(pid: int) -> bool:
  return (
    Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
  )


def test_reap_one_waits_for_an_ended_child_but_not_the_spare():
  with subprocess.Popen(["sleep", "60"]) as spare:
    other = os.posix_spawnp("true", ["true"], os.environ)
    while not zombie(other):
      time.sleep(0.01)
    assert isolation.reap_one(spare.pid)
    assert not Path(f"/proc/{other}").exists()
    assert not isolation.reap_one(spare.pid)
    spare.send_signal(signal.SIGKILL)
    while not zombie(spare.pid):
      time.sleep(0.01)
    assert not isolation.reap_one(spare.pid)
    assert spare.wait() == -signal.SIGKILL


# Tries to change files beneath the directory it is given, and prints which
# tries were refused.
CHANGES = """import json, os, sys
d = sys.argv[1]
tries = {
  "write": lambda: open(f"{d}/f", "a").close(),
  "create": lambda: open(f"{d}/new", "x").close(),
  "truncate": lambda: os.truncate(f"{d}/f", 0),
  "remove": lambda: os.unlink(f"{d}/f"),
  "mkdir": lambda: os.mkdir(f"{d}/sub"),
  "move in": lambda: os.rename(f"{sys.argv[2]}/g", f"{d}/g"),
}
refused = []
for name, change in tries.items():
  try:
    change()
  except PermissionError:
    refused.append(name)
print(json.dumps(refused))
"""


def changes(tmp_path: Path, writable: list[str], target: Path) -> list[str]:
  # The changes a command confined to writable may not make in target.
  write(target, {"f": "kept"})
  write(tmp_path / "app", {"g": ""})
  confine = functools.partial(isolation.confine, writable)
  done = subprocess.run(
    [sys.executable, "-c", CHANGES, str(target), str(tmp_path / "app")],
    check=True,
    capture_output=True,
    text=True,
    preexec_fn=confine,
  )
  return cast("list[str]", json.loads(done.stdout))


@needs_landlock
def test_a_confined_command_changes_files_beneath_its_paths(tmp_path: Path):
  app = str(tmp_path / "app")
  assert changes(tmp_path, [app], tmp_path / "app" / "here") == []


@needs_landlock
def test_a_command_confined_to_the_writable_paths_cannot_change_private_ones(
  tmp_path: Path,
):
  # As build and setup run beside the groups, and each group beside the
  # others: none may change the reports of the run's private directory.
  private = tmp_path / "private" / "runs" / "0"
  refused = changes(tmp_path, [str(tmp_path / "app"), "/dev"], private)
  assert refused == [
    "write",
    "create",
    "truncate",
    "remove",
    "mkdir",
    "move in",
  ]
  assert (private / "f").read_text() == "kept"


# Opens the harness's file again through the harness's /proc, and prints
# whether it was refused.
REOPEN = """import sys
try:
  open(f"/proc/{sys.argv[1]}/fd/{sys.argv[2]}", "a").close()
  print("opened")
except PermissionError:
  print("refused")
"""


@needs_landlock
def test_a_confined_command_cannot_open_the_harnesss_files_through_proc(
  tmp_path: Path,
):
  with (tmp_path / "report").open("w") as report:
    confine = functools.partial(isolation.confine, [str(tmp_path)])
    done = subprocess.run(
      [sys.executable, "-c", REOPEN, str(os.getpid()), str(report.fileno())],
      check=True,
      capture_output=True,
      text=True,
      preexec_fn=confine,
    )
  assert done.stdout == "refused\n"


def test_a_private_directory_beneath_or_above_a_writable_path_is_refused(
  tmp_path: Path,
):
  write(tmp_path / "tests", {"manifest.toml": ""})
  for private, writable in [
    (tmp_path / "app" / "private", tmp_path / "app"),
    (tmp_path, tmp_path / "app"),
  ]:
    with pytest.raises(ValueError, match="is not apart from the writable"):
      _ = run(
        load(""),
        tests=tmp_path / "tests",
        app=tmp_path / "app",
        isolation=Isolation(private, (writable,)),
      )


OWN = """
[[run]]
name = "own"
run = ["sh", "run.sh"]
parse = ["sh", "parse.sh"]

[[test]]
suite = "p2p"
run = "own"
id = "a::fails"
"""
CTRF = '{{"results": {{"tests": [{{"name": "a::fails", "status": "{}"}}]}}}}'


def test_what_a_run_left_running_is_killed_before_its_report_is_parsed(
  tmp_path: Path,
):
  # The run's report has the test failed; a process it left, out of its
  # session, makes it passed while the parse waits.
  run_sh = """cp "$(dirname "$0")/failed.json" "$TESTRUNNER_OUT/r.json"
passed="$(dirname "$0")/passed.json"
(setsid sh -c "sleep 0.3; cp '$passed' '$TESTRUNNER_OUT/r.json'" &)
"""
  parse_sh = 'sleep 1.5; cp "$TESTRUNNER_OUT/r.json" "$TESTRUNNER_CTRF"\n'
  found = isolated(
    tmp_path,
    OWN,
    {},
    {
      "run.sh": run_sh,
      "parse.sh": parse_sh,
      "failed.json": CTRF.format("failed"),
      "passed.json": CTRF.format("passed"),
    },
  )
  assert found == [["a::fails", "failed", None]]


def test_ended_processes_a_run_leaves_are_waited_for_while_it_runs(
  tmp_path: Path,
):
  # Hundreds of processes leave the run's session and end at once; while
  # the run goes on, few of them are left as the harness's zombies, and
  # how the run exited is still known.
  run_sh = """for i in $(seq 600); do (setsid true &); done
sleep 0.5
python3 -c '
import os, sys
n = 0
for pid in filter(str.isdigit, os.listdir("/proc")):
  try:
    fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
  except OSError:
    continue
  n += fields[0] == "Z" and fields[1] == sys.argv[1]
print(n)
' "$PPID" > zombies.txt
exit 3
"""
  found = isolated(
    tmp_path,
    OWN.replace('parse = ["sh", "parse.sh"]', 'parse = "ctrf"'),
    {},
    {"run.sh": run_sh},
  )
  assert found == [
    ["a::fails", "failed", f"{core.MISSING} (the run exited 3, with no report)"]
  ]
  assert int((tmp_path / "app" / "zombies.txt").read_text()) < 50


# A conftest that makes each failed test of the JUnit report passed once
# pytest has written it, before pytest exits 1.
FORGER = """import re

def pytest_unconfigure(config):
  path = config.option.xmlpath
  with open(path) as f:
    text = f.read()
  with open(path, "w") as f:
    f.write(re.sub(r"<failure.*?</failure>", "", text, flags=re.S))
"""
TESTS = """def test_a():
  assert 1 == 2

def test_b():
  pass

def test_canary():
  assert 1 == 2
"""


def pytest_manifest(*tests: tuple[str, str | None]) -> str:
  return "".join(
    f'[[test]]\nsuite = "p2p"\nrunner = "pytest"\nid = "{test}"\n'
    + (f'expect = "{expect}"\n' if expect else "")
    for test, expect in tests
  )


def test_a_report_that_passes_what_the_runner_failed_fails_every_test(
  tmp_path: Path,
):
  # A canary of the group runs apart, so that how the rest's command exits
  # is of the rest alone.
  manifest = pytest_manifest(
    ("t.py::test_a", None),
    ("t.py::test_b", None),
    ("t.py::test_canary", "failed"),
  )
  found = isolated(
    tmp_path, manifest, {"t.py": TESTS, "conftest.py": FORGER}, {}
  )
  said = f"{core.DISOWNED} (the runner exited 1)"
  assert found == [
    ["t.py::test_a", "failed", said],
    ["t.py::test_b", "failed", said],
    ["t.py::test_canary", "passed", None],
  ]


def test_the_runner_runs_only_the_listed_tests_of_a_file(tmp_path: Path):
  # test_a fails unlisted, and is not run: the command exits 0.
  manifest = pytest_manifest(
    ("t.py::test_b", None), ("t.py::test_canary", "failed")
  )
  found = isolated(tmp_path, manifest, {"t.py": TESTS}, {})
  assert found == [
    ["t.py::test_b", "passed", None],
    ["t.py::test_canary", "failed", "assert 1 == 2"],
  ]


def test_a_canary_a_test_of_the_rest_covers_is_not_run_apart():
  covered = Listed("p2p", "pytest", "t.py::test_c[1]", expect="failed")
  apart = Listed("p2p", "pytest", "t.py::test_d", expect="failed")
  rest = Listed("p2p", "pytest", "t.py::test_c")
  units = core._units  # pyright: ignore[reportPrivateUsage]
  assert units((rest, covered), True) == [[rest, covered]]
  assert units((rest, apart), True) == [[rest], [apart]]
  assert units((rest, apart), False) == [[rest, apart]]
  jest = [
    Listed("p2p", "jest", "a.test.js"),
    Listed("p2p", "jest", "b", expect="failed"),
  ]
  assert units(tuple(jest), True) == [jest]


GO_JSON = '{"Action":"pass","Test":"TestA"}'


@needs_landlock
def test_a_commands_output_is_its_stdout_alone_and_out_of_its_reach(
  tmp_path: Path,
):
  # The command writes its report to stdout and noise to stderr, then
  # tries to change the report where it goes, and leaves a process that
  # rewrites where the adapter reads it.
  script = f"""echo '{GO_JSON}'
echo noise >&2
echo forged >> "$1/0.outputs/0.out" || echo refused >&2
(setsid sh -c 'sleep 0.3; echo forged > "$2"' &)
"""
  code = """import json, os, sys, time
from pathlib import Path
from dataforge.testrunner import core, isolation
private, app, script = map(Path, sys.argv[1:])
isolation.subreaper()
here = private / "runs" / "0"
here.mkdir(parents=True)
wall = core._fence(core.Isolation(private, (app, Path("/dev"))), here)
call = core.Call(
  ["sh", str(script), str(private), str(here / "go.json")],
  output=here / "go.json",
)
ctx = core.Context(app, app, here, None)
status = core._call(call, ctx, dict(os.environ), wall)
core._deliver([call], wall)
time.sleep(0.6)
err = (wall.outputs / "0.err").read_text()
print(json.dumps([status, call.output.read_text(), err]))
"""
  write(tmp_path / "app", {"run.sh": script})
  done = subprocess.run(
    [
      sys.executable,
      "-c",
      code,
      str(tmp_path / "private"),
      str(tmp_path / "app"),
      str(tmp_path / "app" / "run.sh"),
    ],
    check=True,
    capture_output=True,
    text=True,
  )
  found = cast("list[object]", json.loads(done.stdout.splitlines()[-1]))
  status, out, err = cast("tuple[int, str, str]", tuple(found))
  assert status == 0
  assert out == GO_JSON + "\n"
  assert err.startswith("noise\n")
  assert err.endswith("Permission denied\nrefused\n")


# Stands in for go test on one package: m/a's test fails; m/b's passes,
# and the run makes m/a's report, where the adapter reads it, passed.
FAKE_GO = """#!/bin/sh
for package; do :; done
case "$package" in
  m/a)
    echo '{"Action":"fail","Package":"m/a","Test":"TestA"}'
    exit 1 ;;
  m/b)
    echo '{"Action":"pass","Package":"m/a","Test":"TestA"}' > "$WORK/go-0.json"
    echo '{"Action":"pass","Package":"m/b","Test":"TestB"}' ;;
esac
"""


def test_a_command_of_a_group_cannot_change_the_report_of_one_before(
  tmp_path: Path,
):
  bin_dir = tmp_path / "bin"
  write(bin_dir, {"go": FAKE_GO})
  (bin_dir / "go").chmod(0o755)
  manifest = "".join(
    f'[[test]]\nsuite = "p2p"\nrunner = "go"\nid = "{test}"\n'
    for test in ("m/a::TestA", "m/b::TestB")
  )
  env = {
    "PATH": f"{bin_dir}:{os.environ['PATH']}",
    "WORK": str(tmp_path / "private" / "runs" / "0"),
  }
  found = isolated(tmp_path, manifest, {}, {}, env)
  assert found == [
    ["m/a::TestA", "failed", None],
    ["m/b::TestB", "passed", None],
  ]


# Stands in for go test on m/calc: TestSub/one fails, TestSub/two_words
# passes, each only when its -run selects it.
FAKE_SUBTESTS = """#!/bin/sh
case "$*" in
  *-run=*one*|*-run=^\\(?:TestSub\\)\\$*)
    echo '{"Action":"fail","Package":"m/calc","Test":"TestSub/one"}'
    failed=1 ;;
esac
echo '{"Action":"pass","Package":"m/calc","Test":"TestSub/two_words"}'
exit ${failed:-0}
"""


def test_an_unlisted_subtest_that_fails_beside_a_listed_one_does_not_run(
  tmp_path: Path,
):
  bin_dir = tmp_path / "bin"
  write(bin_dir, {"go": FAKE_SUBTESTS})
  (bin_dir / "go").chmod(0o755)
  manifest = (
    '[[test]]\nsuite = "p2p"\nrunner = "go"\nid = "m/calc::TestSub/two_words"\n'
  )
  env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
  found = isolated(tmp_path, manifest, {}, {}, env)
  assert found == [["m/calc::TestSub/two_words", "passed", None]]


def test_confining_where_the_kernel_cannot_is_refused_before_anything_runs(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(isolation, "abi", lambda: isolation.MIN_ABI - 1)
  manifest = OWN.replace('parse = ["sh", "parse.sh"]', 'parse = "ctrf"')
  write(tmp_path / "app", {"keep.txt": ""})
  write(
    tmp_path / "tests", {"manifest.toml": manifest, "run.sh": "touch ran\n"}
  )
  (tmp_path / "private").mkdir()
  with pytest.raises(isolation.UnavailableError):
    _ = run(
      load(manifest),
      tests=tmp_path / "tests",
      app=tmp_path / "app",
      isolation=Isolation(tmp_path / "private", (tmp_path / "app",)),
    )
  assert not (tmp_path / "app" / "ran").exists()
