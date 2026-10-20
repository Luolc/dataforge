"""The clients a container runs behind the gateway, checked before use.

A `Client` is the executable of Claude Code or the release tarball of Pi or
Codex CLI, as the machine setup keeps them, pinned by version and sha256. A file
whose sha256 is not its pin is neither unpacked nor mounted. A tarball is
unpacked once per process into a directory of the gateway's own, refusing a
member that would land outside it and anything but regular files and
directories; its whole package is mounted read-only at
`/opt/dataforge/<name>`, with a one-line `/usr/local/bin/<name>` that runs its
program, so that Pi finds its assets and Codex CLI its `rg` and helpers.
"""

import atexit
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading

from dataforge.curation.actions import Mount


@dataclass(frozen=True, slots=True)
class Client:
  """A client a container runs, pinned.

  Attributes:
    path: The executable, or the release tarball.
    version: What its `--version` gives.
    sha256: The file's sha256.
    program: For a tarball, its program's path inside it; None for an
      executable.
  """

  path: Path
  version: str
  sha256: str
  program: str | None = None


# The versions and sha256 of machine/versions.yml, where the machine setup
# keeps each client.
CLAUDE_CODE = Client(
  Path.home() / ".local/share/claude/versions/2.1.292",
  "2.1.292",
  "a967e7b1d8b4e47ee421d5433027880347952b0c0857abf880e2c942a4ec93b3",
)
PI = Client(
  Path.home() / ".local/libexec/pi/1.2.0.tar.gz",
  "1.2.0",
  "00fc36cffdebceea5a48230da62cf3fb463178f8fe185766652f96002a246847",
  "pi/pi",
)
CODEX = Client(
  Path.home() / ".local/libexec/codex/0.159.3.tar.gz",
  "0.159.3",
  "3930f31ac5fca861ea3e444e2683f261190d96b63fba58e0a40a879174369cdf",
  "bin/codex",
)
# Where a container finds each client's package.
PACKAGES = "/opt/dataforge"


def _checked(client: Client) -> bytes | None:
  # The file's bytes when it is a tarball, else None, once its sha256 is its
  # pin; a tarball is unpacked from these very bytes.
  digest = hashlib.sha256()
  data = io.BytesIO() if client.program is not None else None
  with client.path.open("rb") as f:
    while chunk := f.read(2**20):
      digest.update(chunk)
      if data is not None:
        _ = data.write(chunk)
  if digest.hexdigest() != client.sha256:
    found = digest.hexdigest()
    raise RuntimeError(f"{client.path} has sha256 {found}, not its pin")
  return None if data is None else data.getvalue()


def executable(client: Client) -> Path:
  """Check an executable client.

  Args:
    client: The client; its `program` must be None.

  Returns:
    Its path.

  Raises:
    RuntimeError: Its sha256 is not its pin.
  """  # noqa: DOC502
  _ = _checked(client)
  return client.path


def _only_files(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
  if not (member.isreg() or member.isdir()):
    raise tarfile.FilterError(
      f"{member.name} is neither a regular file nor a directory"
    )
  return tarfile.data_filter(member, dest)


_lock = threading.Lock()
_unpacked: dict[tuple[Client, Path], Path] = {}
_own: list[Path] = []


def unpack(client: Client, name: str, into: Path) -> Path:
  """Unpack a tarball client, once per process, after checking it.

  Args:
    client: The client; its `program` must be set.
    name: Its name, which its directory starts with.
    into: The gateway's directory it goes in.

  Returns:
    The directory of its package, readable by any user.

  Raises:
    RuntimeError: Its sha256 is not its pin, or it holds a member outside its
      directory or other than a regular file or directory.
  """
  with _lock:
    if (found := _unpacked.get((client, into))) is not None:
      return found
    data = _checked(client)
    assert data is not None
    package = into / f"{name}-{client.version}"
    into.mkdir(parents=True, exist_ok=True)
    fresh = Path(tempfile.mkdtemp(prefix=f".{package.name}.", dir=into))
    try:
      with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(fresh, filter=_only_files)
    except tarfile.TarError as e:
      shutil.rmtree(fresh)
      raise RuntimeError(f"{client.path} cannot be unpacked: {e}") from e
    fresh.chmod(0o755)
    shutil.rmtree(package, ignore_errors=True)
    _ = fresh.rename(package)
    _unpacked[(client, into)] = package
    return package


def own_dir() -> Path:
  """Return this process's directory for unpacked clients, removed at exit.

  Returns:
    The directory.
  """
  with _lock:
    if not _own:
      _own.append(Path(tempfile.mkdtemp(prefix="dataforge-gateway-")))
      _ = atexit.register(shutil.rmtree, _own[0], True)
    return _own[0]


def mounts(client: Client, name: str, into: Path) -> tuple[Mount, ...]:
  """Return the read-only mounts that put a client on a container's PATH.

  An executable is mounted at `/usr/local/bin/<name>`; a tarball's package at
  `PACKAGES/<name>`, with a one-line `/usr/local/bin/<name>` that runs its
  program.

  Args:
    client: The client.
    name: The command it is run by.
    into: The gateway's directory a tarball is unpacked in.

  Returns:
    The mounts.

  Raises:
    RuntimeError: As `unpack` raises, and for an executable whose sha256 is
      not its pin.
  """  # noqa: DOC502
  if client.program is None:
    return (Mount(executable(client), f"/usr/local/bin/{name}"),)
  package = unpack(client, name, into)
  program = package.with_name(f"{package.name}.run")
  if not program.exists():
    _ = program.write_text(
      f'#!/bin/sh\nexec {PACKAGES}/{name}/{client.program} "$@"\n'
    )
    program.chmod(0o755)
  return (
    Mount(package, f"{PACKAGES}/{name}"),
    Mount(program, f"/usr/local/bin/{name}"),
  )
