# pyright: reportAny=false
# versions.yml is read as YAML.

"""The pinned clients: their pins, their check and their unpacking.

Invariant 29, for the clients themselves.
"""

from collections.abc import Callable
from pathlib import Path
import tarfile
from typing import cast

import pytest
import yaml

from dataforge.curation.actions import Limits
from dataforge.gateway import (
  bindings,
  CLAUDE_CODE,
  Client,
  clients,
  CODEX,
  hooks,
  PI,
  trial_setup,
)
from tests.gateway.fakes import (
  directory,
  executable,
  file,
  Launcher,
  package,
  tarball,
)

VERSIONS = Path(__file__).parents[3] / "machine" / "versions.yml"


def test_the_default_clients_are_the_pins_of_versions_yml():
  pins = cast(dict[str, object], yaml.safe_load(VERSIONS.read_text()))
  home = Path.home()
  for client, key, path in [
    (CLAUDE_CODE, "claude_code", ".local/share/claude/versions/{}"),
    (PI, "pi", ".local/libexec/pi/{}.tar.gz"),
    (CODEX, "codex", ".local/libexec/codex/{}.tar.gz"),
  ]:
    assert client.version == pins[f"{key}_version"]
    assert client.sha256 == pins[f"{key}_sha256"]
    assert client.path == home / path.format(client.version)


def test_a_tarball_is_unpacked_once_and_mounted_with_a_program(tmp_path: Path):
  client = package(tmp_path)
  into = tmp_path / "gateway"
  found = clients.mounts(client, "pi", into)
  package_mount, program_mount = found
  assert (package_mount.target, package_mount.read_only) == (
    "/opt/dataforge/pi",
    True,
  )
  assert (
    package_mount.source / "pi" / "assets" / "theme.json"
  ).read_text() == "{}"
  assert package_mount.source.stat().st_mode & 0o777 == 0o755
  assert (program_mount.target, program_mount.read_only) == (
    "/usr/local/bin/pi",
    True,
  )
  assert program_mount.source.read_text() == (
    '#!/bin/sh\nexec /opt/dataforge/pi/pi/pi "$@"\n'
  )
  assert program_mount.source.stat().st_mode & 0o777 == 0o755
  # Once per process: the tarball is not read again.
  _ = client.path.write_bytes(b"changed")
  assert clients.mounts(client, "pi", into) == found


SYMLINK = tarfile.TarInfo("pi/link")
SYMLINK.type, SYMLINK.linkname = tarfile.SYMTYPE, "pi"
HARDLINK = tarfile.TarInfo("pi/hard")
HARDLINK.type, HARDLINK.linkname = tarfile.LNKTYPE, "pi/pi"
FIFO = tarfile.TarInfo("pi/fifo")
FIFO.type = tarfile.FIFOTYPE


@pytest.mark.parametrize(
  "member",
  [
    file("../escaped", b"x"),
    (SYMLINK, b""),
    (HARDLINK, b""),
    (FIFO, b""),
  ],
)
def test_unpacking_refuses_a_member_outside_or_not_a_regular_file(
  tmp_path: Path, member: tuple[tarfile.TarInfo, bytes]
):
  client = tarball(
    tmp_path / "bad.tar.gz",
    [directory("pi"), file("pi/pi", b"", 0o755), member],
  )
  into = tmp_path / "work" / "gateway"
  with pytest.raises(RuntimeError, match="cannot be unpacked"):
    _ = clients.unpack(client, "pi", into)
  assert list(into.iterdir()) == []
  assert not (tmp_path / "work" / "escaped").exists()


def wrong(client: Client) -> Client:
  return Client(client.path, client.version, "0" * 64, client.program)


@pytest.mark.parametrize(
  ("name", "agent", "make"),
  [
    ("claude-code", "ClaudeCode", executable),
    ("pi", "Pi", package),
  ],
)
def test_a_client_not_its_pin_is_neither_unpacked_nor_mounted(
  launcher: Launcher,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  name: str,
  agent: str,
  make: Callable[[Path], Client],
):
  own = tmp_path / "own"
  monkeypatch.setattr(hooks, "own_dir", lambda: own)
  client = wrong(make(tmp_path))
  setup = trial_setup(clients={name: client})
  with (
    pytest.raises(RuntimeError, match=f"{client.path} has sha256"),
    setup(
      tmp_path,
      {"import_path": f"dataforge.gateway.harbor:{agent}"},
      Limits(memory_gb=1),
      tmp_path,
    ),
  ):
    pass
  assert launcher.launches() == []
  assert not own.exists() or list(own.iterdir()) == []
  with pytest.raises(RuntimeError, match=f"{client.path} has sha256"):
    _ = bindings(tmp_path / "root", clients={name: client})
  gateway = tmp_path / "root" / "dataforge-gateway"
  assert not (gateway / "clients").exists()
