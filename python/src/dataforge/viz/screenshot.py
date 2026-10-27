"""The dataforge viz screenshot command: a shot of one panel of the viewer.

With --share it is a shot of a share site's item page instead, a tab of it
and the deliverable the tab shows (design-share.md, section 5). It drives
headless Google Chrome over the DevTools protocol on a pipe, with the
standard library alone, so that the shot is of the page after its scripts
ran, in the colour template the page itself applied.
"""

import argparse
import base64
from collections.abc import Generator, Sequence
import contextlib
import json
import os
from pathlib import Path
import select
import signal
import sys
import tempfile
import time
from typing import cast
import urllib.parse

CHROME = "/usr/bin/google-chrome"
# The viewer's colour templates (web/src/ui/template.ts) the themes name, and
# where the viewer keeps the template a reader chose.
TEMPLATES = {"light": "ac", "dark": "dark"}
TEMPLATE_KEY = "viz.template"
# How long a page may take to show its panel, how long it must stay the same
# for a panel that is not there to count as missing, and how often it is read.
TIMEOUT = 30.0
QUIET = 1.5
POLL = 0.1
# What the viewer shows while a read has not landed.
LOADING = "Loading…"
# The first of two descriptors that hold the pipes while they move to 3, 4.
SPARE = 100
# How long the processes Chrome left after it closed are given to end.
GRACE = 5.0

# The tab bar's label and the parameter that names a panel, on the viewer's
# attempt page and on a share site's item page.
_VIEWER = ("Attempt", "part")
_SHARE = ("Item", "deliverable")

_PAGE = """\
(() => {
  const want = {tab: %s, panel: %s, nav: %s, param: %s};
  const current = (selector) => [...document.querySelectorAll(selector)]
    .map((a) => new URL(a.href).searchParams);
  const bar = `nav[aria-label="${want.nav}"]`;
  const tabs = current(`${bar} a[aria-current="page"]`);
  const parts = current(`a[aria-current="page"]:not(${bar} a)`);
  return JSON.stringify({
    ready: document.readyState === "complete",
    text: document.body ? document.body.innerText : "",
    template: document.documentElement.dataset.template ?? null,
    tab: tabs.some((q) => q.get("tab") === want.tab),
    panel: want.panel === null
      || parts.some((q) => q.get(want.param) === want.panel),
  });
})()
"""


class ShotError(Exception):
  """A shot that cannot be taken; the message says why."""


class Browser:
  """Headless Google Chrome, driven over the DevTools protocol on a pipe.

  Chrome reads the protocol's messages on its fd 3 and writes on its fd 4
  (`--remote-debugging-pipe`), each JSON followed by a NUL byte.
  """

  def __init__(self, to: int, back: int) -> None:
    """Talk to a started Chrome.

    Args:
      to: The pipe Chrome reads.
      back: The pipe Chrome writes.
    """
    self._to: int = to
    self._back: int = back
    self._buffer: bytes = b""
    self._next: int = 0
    self._session: str | None = None

  def call(
    self,
    method: str,
    params: dict[str, object] | None = None,
    *,
    timeout: float = TIMEOUT,
  ) -> dict[str, object]:
    """Send one command and wait for its answer.

    Args:
      method: The protocol's method, such as `Page.navigate`.
      params: Its parameters.
      timeout: Seconds to wait for the answer.

    Returns:
      The answer's `result`.

    Raises:
      ShotError: Chrome answered with an error, closed the pipe or did not
        answer in time.
    """
    self._next += 1
    message: dict[str, object] = {"id": self._next, "method": method}
    if params is not None:
      message["params"] = params
    if self._session is not None:
      message["sessionId"] = self._session
    try:
      _ = os.write(self._to, json.dumps(message).encode() + b"\0")
    except OSError as error:
      raise ShotError(f"Chrome closed the pipe: {error}") from error
    deadline = time.monotonic() + timeout
    while True:
      answer = self._receive(deadline)
      if answer.get("id") != self._next:
        continue
      if "error" in answer:
        raise ShotError(f"{method}: {answer['error']}")
      return cast(dict[str, object], answer.get("result", {}))

  def _receive(self, deadline: float) -> dict[str, object]:
    while b"\0" not in self._buffer:
      left = deadline - time.monotonic()
      if left <= 0:
        raise ShotError("Chrome did not answer in time")
      ready, _, _ = select.select([self._back], [], [], left)
      if not ready:
        continue
      chunk = os.read(self._back, 1 << 16)
      if not chunk:
        raise ShotError("Chrome closed the pipe")
      self._buffer += chunk
    message, self._buffer = self._buffer.split(b"\0", 1)
    return cast(dict[str, object], json.loads(message))

  def open_tab(self, width: int, height: int) -> None:
    """Open a blank tab of a size, and send the commands that follow to it.

    Args:
      width: The viewport's width in pixels.
      height: The viewport's height in pixels.
    """
    target = self.call("Target.createTarget", {"url": "about:blank"})
    attached = self.call(
      "Target.attachToTarget",
      {"targetId": target["targetId"], "flatten": True},
    )
    self._session = cast(str, attached["sessionId"])
    _ = self.call(
      "Emulation.setDeviceMetricsOverride",
      {
        "width": width,
        "height": height,
        "deviceScaleFactor": 1,
        "mobile": False,
      },
    )

  def go(self, url: str) -> None:
    """Load a page in the tab.

    Args:
      url: The page.

    Raises:
      ShotError: Chrome could not load it.
    """
    # From a blank page, which never settles, so that what is read next is
    # not the page before.
    _ = self.call("Page.navigate", {"url": "about:blank"})
    done = self.call("Page.navigate", {"url": url})
    if done.get("errorText"):
      raise ShotError(f"{url}: {done['errorText']}")

  def evaluate(self, expression: str) -> str:
    """Run an expression in the page.

    Args:
      expression: JavaScript whose value is a string.

    Returns:
      The string.

    Raises:
      ShotError: The page could not be read.
    """
    done = self.call(
      "Runtime.evaluate", {"expression": expression, "returnByValue": True}
    )
    value = cast(dict[str, object], done.get("result", {})).get("value")
    if not isinstance(value, str):
      raise ShotError(f"the page could not be read: {done}")
    return value

  def capture(self) -> bytes:
    """Take a PNG of the viewport.

    Returns:
      The PNG's bytes.
    """
    done = self.call("Page.captureScreenshot", {"format": "png"})
    return base64.b64decode(cast(str, done["data"]))


def _children(pid: int) -> dict[int, str]:
  # Every process under pid by parent PID, with its start time, which tells
  # it from another process that later takes the same PID.
  parents: dict[int, tuple[int, str]] = {}
  for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
      continue
    try:
      stat = (entry / "stat").read_text()
    except OSError:
      continue
    fields = stat[stat.rindex(")") + 2 :].split()
    parents[int(entry.name)] = (int(fields[1]), fields[19])
  found: dict[int, str] = {}
  todo = [pid]
  while todo:
    top = todo.pop()
    for child, (parent, started) in parents.items():
      if parent == top and child not in found:
        found[child] = started
        todo.append(child)
  return found


def _alive(pid: int, started: str) -> bool:
  try:
    stat = Path(f"/proc/{pid}/stat").read_text()
  except OSError:
    return False
  fields = stat[stat.rindex(")") + 2 :].split()
  return fields[19] == started and fields[0] != "Z"


@contextlib.contextmanager
def chrome(path: str, width: int, height: int) -> Generator[Browser]:
  """Start headless Chrome with a new profile, and stop it after.

  Every process Chrome started is gone when the block ends, or the block
  raises.

  Args:
    path: Chrome's program.
    width: The viewport's width in pixels.
    height: The viewport's height in pixels.

  Yields:
    The browser, with one blank tab open.

  Raises:
    ShotError: Chrome did not start, or a process of it is left.
  """
  with tempfile.TemporaryDirectory(prefix="dataforge-shot-") as tmp:
    to_read, to_write = os.pipe()
    back_read, back_write = os.pipe()
    log = Path(tmp) / "chrome.log"

    # Started with posix_spawn, which moves the pipes to fd 3 and 4 in the
    # child alone, with no code run between fork and exec in this process, a
    # threaded one. They go through two spare descriptors first, so that
    # neither dup2 overwrites the other when a pipe's own end is 3 or 4.
    actions: list[
      tuple[int, int] | tuple[int, int, int] | tuple[int, int, str, int, int]
    ] = [
      (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
      (os.POSIX_SPAWN_DUP2, to_read, SPARE),
      (os.POSIX_SPAWN_DUP2, back_write, SPARE + 1),
      (os.POSIX_SPAWN_DUP2, SPARE, 3),
      (os.POSIX_SPAWN_DUP2, SPARE + 1, 4),
      (os.POSIX_SPAWN_CLOSE, SPARE),
      (os.POSIX_SPAWN_CLOSE, SPARE + 1),
    ]
    try:
      with log.open("wb") as err:
        actions += [
          (os.POSIX_SPAWN_DUP2, err.fileno(), 1),
          (os.POSIX_SPAWN_DUP2, err.fileno(), 2),
        ]
        argv = [
          path,
          "--headless=new",
          "--remote-debugging-pipe",
          f"--user-data-dir={tmp}/profile",
          "--no-first-run",
          "--no-default-browser-check",
          "--disable-extensions",
          "--hide-scrollbars",
          "about:blank",
        ]
        pid = os.posix_spawn(path, argv, os.environ, file_actions=actions)
    except OSError as error:
      for fd in (to_read, to_write, back_read, back_write):
        os.close(fd)
      raise ShotError(f"Chrome did not start: {error}") from error
    os.close(to_read)
    os.close(back_write)
    browser = Browser(to_write, back_read)
    under: dict[int, str] = {}
    try:
      try:
        browser.open_tab(width, height)
      except ShotError as error:
        tail = log.read_text(errors="replace")[-2000:]
        raise ShotError(f"Chrome did not start: {error}\n{tail}") from error
      yield browser
      under = _children(pid)
    finally:
      under = under or _children(pid)
      with contextlib.suppress(ShotError):
        _ = browser.call("Browser.close", timeout=5)
      _wait(pid, 10)
      os.close(to_write)
      os.close(back_read)
      left = _reap(under)
    if left:
      raise ShotError(f"processes of Chrome are left: {sorted(left)}")


def _wait(pid: int, timeout: float) -> None:
  # Waits for the process this one started; kills it, by the PID that
  # waitpid has not yet reaped, when it does not end in time.
  deadline = time.monotonic() + timeout
  while os.waitpid(pid, os.WNOHANG) == (0, 0):
    if time.monotonic() >= deadline:
      os.kill(pid, signal.SIGKILL)
      _ = os.waitpid(pid, 0)
      return
    time.sleep(POLL)


def _reap(under: dict[int, str]) -> list[int]:
  # The processes of Chrome's tree that outlive its main process, found
  # before it closed: given a moment, then stopped by PID, each only while it
  # is still the process found. Returns those that remain.
  deadline = time.monotonic() + GRACE
  while time.monotonic() < deadline and any(
    _alive(pid, started) for pid, started in under.items()
  ):
    time.sleep(POLL)
  for pid, started in under.items():
    if _alive(pid, started):
      with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
  time.sleep(POLL)
  return [pid for pid, started in under.items() if _alive(pid, started)]


def wait_for_panel(
  browser: Browser,
  tab: str,
  panel: str | None,
  template: str,
  *,
  share: bool = False,
) -> None:
  """Wait until the page shows the tab and panel, in the template.

  The page counts as showing them when it has loaded, shows no "Loading…",
  marks the tab named as the current one, and, for a panel, marks it as the
  one shown. A page that stays the same for QUIET seconds without that has
  nothing more to show.

  Args:
    browser: The browser, on the page.
    tab: The tab's name.
    panel: The panel's name, or None for the tab's first.
    template: The template the page must have applied.
    share: Whether the page is a share site's item page.

  Raises:
    ShotError: The tab or panel is not there, the page did not settle in
      TIMEOUT seconds, or the template it applied is another.
  """
  nav, param = _SHARE if share else _VIEWER
  expression = _PAGE % tuple(map(json.dumps, (tab, panel, nav, param)))
  start = time.monotonic()
  last = ""
  since = start
  while True:
    page = cast(dict[str, object], json.loads(browser.evaluate(expression)))
    text = cast(str, page["text"])
    settled = page["ready"] and LOADING not in text
    if settled and page["tab"] and page["panel"]:
      if not text.strip():
        raise ShotError("the page shows no content")
      applied = page["template"]
      if applied != template:
        raise ShotError(
          f"the page applied the template {applied!r}, not {template!r}"
        )
      return
    now = time.monotonic()
    if text != last:
      last, since = text, now
    if settled and now - since >= QUIET:
      if not page["tab"]:
        raise ShotError(f"no tab {tab!r} on the page")
      raise ShotError(f"no panel {panel!r} on the page")
    if now - start >= TIMEOUT:
      raise ShotError(f"the page did not settle in {TIMEOUT:g} seconds")
    time.sleep(POLL)


def page_url(
  base: str, project: str, attempt: str, tab: str, *, share: bool = False
) -> str:
  """Make the URL of an attempt's tab, or of a share site's item's.

  Args:
    base: The viewer's or the share site's address.
    project: The project's ID.
    attempt: The attempt's ID, or the item's.
    tab: The tab's name.
    share: Whether the address is a share site's.

  Returns:
    The URL, with the panel left to the caller.
  """
  root = f"{base.rstrip('/')}/{urllib.parse.quote(project, safe='')}"
  if share:
    item = urllib.parse.quote(attempt, safe="")
    return f"{root}/items/{item}?{urllib.parse.urlencode({'tab': tab})}"
  query = urllib.parse.urlencode({"id": attempt, "tab": tab})
  return f"{root}/attempt?{query}"


def shoot(
  *,
  base: str,
  project: str,
  attempt: str,
  tab: str,
  panel: str | None,
  width: int,
  height: int,
  theme: str,
  out: Path,
  program: str = CHROME,
  share: bool = False,
) -> None:
  """Take the shot and write it.

  The theme is set as a reader sets it: the template is written where the
  viewer keeps the reader's choice, and the page is loaded again to apply it.
  Nothing is written, and ShotError is raised, unless the page shows the
  tab and panel in the template.

  Args:
    base: The viewer's address.
    project: The project's ID.
    attempt: The attempt's ID.
    tab: The tab's name.
    panel: The panel's name, or None for the tab's first.
    width: The viewport's width in pixels.
    height: The viewport's height in pixels.
    theme: "light" or "dark".
    out: Where to write the PNG.
    program: Chrome's program.
    share: Whether base is a share site's address, attempt an item on it,
      and panel a deliverable of the tab.
  """
  template = TEMPLATES[theme]
  url = page_url(base, project, attempt, tab, share=share)
  if panel is not None:
    param = (_SHARE if share else _VIEWER)[1]
    url += "&" + urllib.parse.urlencode({param: panel})
  with chrome(program, width, height) as browser:
    # The origin first, since a blank page has no storage to write to.
    browser.go(base)
    key, value = json.dumps(TEMPLATE_KEY), json.dumps(template)
    _ = browser.evaluate(f"localStorage.setItem({key}, {value}) ?? 'set'")
    browser.go(url)
    wait_for_panel(browser, tab, panel, template, share=share)
    png = browser.capture()
  _ = out.write_bytes(png)


def add_arguments(parser: argparse.ArgumentParser) -> None:
  """Add the screenshot command's arguments.

  Args:
    parser: The parser of dataforge viz screenshot.
  """
  _ = parser.add_argument(
    "--url",
    required=True,
    help="the viewer's address, such as http://host:8000",
  )
  _ = parser.add_argument(
    "--share",
    action="store_true",
    help="--url is a share site's: shoot an item's page, --attempt naming"
    + " the item, --tab its kind and --panel a deliverable of the tab",
  )
  _ = parser.add_argument("--project", required=True, help="the project's ID")
  _ = parser.add_argument("--attempt", required=True, help="the attempt's ID")
  _ = parser.add_argument(
    "--tab",
    help="the tab's name (default runs; with --share, required)",
  )
  _ = parser.add_argument(
    "--panel", help="the panel (the part of the tab) by name; default the first"
  )
  _ = parser.add_argument(
    "--width", type=int, default=1440, help="viewport width (default 1440)"
  )
  _ = parser.add_argument(
    "--height", type=int, default=1000, help="viewport height (default 1000)"
  )
  _ = parser.add_argument(
    "--theme", choices=sorted(TEMPLATES), required=True, help="the theme"
  )
  _ = parser.add_argument(
    "--out", type=Path, required=True, help="where to write the PNG"
  )
  _ = parser.add_argument(
    "--chrome", default=CHROME, help=f"Chrome's program (default {CHROME})"
  )


def run(args: argparse.Namespace) -> int:
  """Run the screenshot command.

  Args:
    args: The parsed arguments.

  Returns:
    0 once the PNG is written and its path printed; 1 when the shot cannot
    be taken, with nothing written; 2 for bad arguments.
  """
  width = cast(int, args.width)
  height = cast(int, args.height)
  if width < 1 or height < 1:
    print(
      "dataforge viz screenshot: width and height must be positive",
      file=sys.stderr,
    )
    return 2
  share = cast(bool, args.share)
  tab = cast(str | None, args.tab)
  if tab is None and share:
    print("dataforge viz screenshot: --share needs --tab", file=sys.stderr)
    return 2
  out = cast(Path, args.out)
  try:
    shoot(
      base=cast(str, args.url),
      project=cast(str, args.project),
      attempt=cast(str, args.attempt),
      tab=tab or "runs",
      panel=cast(str | None, args.panel),
      width=width,
      height=height,
      theme=cast(str, args.theme),
      out=out,
      program=cast(str, args.chrome),
      share=share,
    )
  except (ShotError, OSError) as error:
    print(f"dataforge viz screenshot: {error}", file=sys.stderr)
    return 1
  print(out)
  return 0


def main(argv: Sequence[str] | None = None) -> int:
  """Run the command as `python -m dataforge.viz.screenshot`.

  Args:
    argv: The arguments; None for the command line's.

  Returns:
    The exit status, as run.
  """
  parser = argparse.ArgumentParser(prog="dataforge viz screenshot")
  add_arguments(parser)
  return run(parser.parse_args(argv))


if __name__ == "__main__":
  sys.exit(main())
