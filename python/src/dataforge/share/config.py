"""The site file of share, read at start.

A site file loads only when it passes every check; one that fails raises
ConfigError naming the file and the key that is wrong.
"""

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
from typing import cast

import yaml


class ConfigError(Exception):
  """A site file that fails a check."""


@dataclass(frozen=True, slots=True)
class Listen:
  """The address the server binds.

  Attributes:
    host: An IP address literal, never an unspecified one.
    port: The port.
  """

  host: str
  port: int


@dataclass(frozen=True, slots=True)
class Site:
  """The site file.

  Attributes:
    listen: The address the server binds.
    tokens: The token database, absolute.
    projects: The export directories, absolute, in the file's order.
  """

  listen: Listen
  tokens: Path
  projects: tuple[Path, ...]


def load_site(path: Path) -> Site:
  """Load a site file.

  Paths in it are relative to the file. Each export directory must exist;
  the token database is checked when the server starts.

  Args:
    path: The site file.

  Returns:
    The site.

  Raises:
    ConfigError: When the file fails a check.
  """
  data = _read(path)
  _keys(path, "", data, {"listen", "tokens", "projects"})
  listen = _mapping(path, "listen", data["listen"])
  _keys(path, "listen", listen, {"host", "port"})
  host = _str(path, "listen.host", listen["host"])
  try:
    address = ipaddress.ip_address(host)
  except ValueError:
    raise ConfigError(
      f"{path}: listen.host: {host!r} is not an IP address"
    ) from None
  if address.is_unspecified:
    raise ConfigError(f"{path}: listen.host: {host} would listen everywhere")
  port = listen["port"]
  if not isinstance(port, int) or isinstance(port, bool):
    raise ConfigError(f"{path}: listen.port: not an integer")
  if not 1 <= port <= 65535:
    raise ConfigError(f"{path}: listen.port: {port} is out of range")
  tokens = _absolute(path, _str(path, "tokens", data["tokens"]))
  dirs = data["projects"]
  if not isinstance(dirs, list) or not dirs:
    raise ConfigError(f"{path}: projects: not a non-empty list")
  projects: list[Path] = []
  for i, value in enumerate(cast(list[object], dirs)):
    # Configuration: opened as given, a symlink in it included, so that a
    # new export can take the place of the old one.
    found = _absolute(path, _str(path, f"projects[{i}]", value))
    if not found.is_dir():
      raise ConfigError(f"{path}: projects[{i}]: {found} is not a directory")
    if found in projects:
      raise ConfigError(f"{path}: projects[{i}]: {found} is repeated")
    projects.append(found)
  return Site(Listen(host, port), tokens, tuple(projects))


def _read(path: Path) -> dict[str, object]:
  try:
    text = path.read_text(encoding="utf-8")
    repeated = _repeated(yaml.SafeLoader(text).get_single_node())
    data = cast(object, yaml.safe_load(text))
  except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
    raise ConfigError(f"{path}: {error}") from None
  if repeated:
    raise ConfigError(f"{path}: {repeated}")
  return _mapping(path, "", data)


def _repeated(node: yaml.Node | None) -> str | None:
  # safe_load keeps the last of a repeated key; a site file may not repeat
  # one.
  if isinstance(node, yaml.SequenceNode):
    items = cast(list[yaml.Node], node.value)
    return next(filter(None, map(_repeated, items)), None)
  if not isinstance(node, yaml.MappingNode):
    return None
  keys: set[str] = set()
  for key, value in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
    text = cast(str, key.value) if isinstance(key, yaml.ScalarNode) else ""
    if text and text in keys:
      return f"line {cast(int, key.start_mark.line) + 1}: key {text} repeated"
    keys.add(text)
    if found := _repeated(value):
      return found
  return None


def _mapping(path: Path, key: str, value: object) -> dict[str, object]:
  if not isinstance(value, dict):
    raise ConfigError(f"{path}: {key or 'the file'}: not a mapping")
  mapping = cast(dict[object, object], value)
  for k in mapping:
    if not isinstance(k, str):
      raise ConfigError(f"{path}: {key or 'the file'}: key {k!r} not a string")
  return cast(dict[str, object], mapping)


def _keys(
  path: Path, where: str, data: dict[str, object], required: set[str]
) -> None:
  prefix = f"{where}." if where else ""
  for key in data:
    if key not in required:
      raise ConfigError(f"{path}: {prefix}{key}: unknown key")
  for key in sorted(required):
    if key not in data:
      raise ConfigError(f"{path}: {prefix}{key}: missing")


def _str(path: Path, key: str, value: object) -> str:
  if not isinstance(value, str) or not value:
    raise ConfigError(f"{path}: {key}: not a non-empty string")
  return value


def _absolute(path: Path, value: str) -> Path:
  # Absolute, so that it does not depend on the working directory later.
  return Path(os.path.abspath(path.parent / value))
