"""Writing a static site of exports, for a static host such as Cloudflare Pages.

The site is the frontend's static build and the exports as files it reads,
with no server: whoever hosts it decides who may read it. These files are
generated:

  _headers                              the host's response headers
  data/projects.json                    [each export's project]
  data/<project>/items.json             [{id, revision}], from export.json
  data/<project>/items/<id>/tree.json   {<dir>: [{name, type, size,
                                        listed_only?}]}, every directory of
                                        the item, "" for the item's own

and every other file is a copy, byte for byte, of an input:

  <path>                                 the frontend's <path>
  data/<project>/items/<id>/record.json  the export's items/<id>/record.json
  data/<project>/items/<id>/files/<path> the export's items/<id>/<path>, for
                                         each file but those of
                                         export.json's listed_only and the
                                         download
  data/<project>/items/<id>/<download>   the export's items/<id>/<download>,
                                         where <download> is record.json's
                                         download.path, checked against its
                                         size and sha256

A file of listed_only, and the download, are in tree.json with listed_only
true, by their name and size; a file of listed_only is in no other file of
the site. A path of listed_only that an item does not hold is skipped for
that item, as not every item has every such file. The site has no 404.html,
so a host such as Cloudflare Pages answers a path it does not hold with
index.html, as a single-page application needs.

The command fails, and leaves nothing, when --out exists, a file of the
site would hold more than 25 MiB, the site would hold more than 20,000
files, or an input fails a check. Every input is opened one component at a
time with O_NOFOLLOW, as share's server opens it (dataforge.share.exports);
a symlink or anything else that is not a regular file or a directory fails
the command. So does a .git, node_modules or .DS_Store anywhere in an input,
and a functions or .wrangler at the frontend's top: wrangler pages deploy
leaves those out of what it uploads, and the host would answer for them
with index.html.
"""

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import cast

from dataforge.share.exports import (
  Export,
  ExportError,
  Item,
  open_export,
  open_item,
)
from dataforge.viz.paths import (
  Entry,
  is_dir,
  list_dir,
  open_path,
  read_regular,
  RefusedError,
)

FILE_LIMIT = 25 * 1024 * 1024
"""The most bytes any file of the site may hold, Cloudflare Pages' limit."""

FILES_LIMIT = 20_000
"""The most files the site may hold, Cloudflare Pages' limit."""

RESERVED = frozenset(
  {
    "data",
    "_headers",
    "_redirects",
    "_routes.json",
    "_worker.js",
    "404.html",
    "functions",
    ".wrangler",
  }
)
"""Names the frontend may not have at its top: the command writes data and
_headers, and the others change what the host serves or are not uploaded."""

UNSENT = frozenset({".git", "node_modules", ".DS_Store"})
"""Names wrangler pages deploy leaves out at any depth, so no input may have
them."""

CSP = (
  "default-src 'self'; script-src 'self'; connect-src 'self';"
  " img-src 'self' data:; font-src 'self'; style-src 'self';"
  " object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
  " form-action 'none'"
)
"""The policy of share's server, dataforge.share.app.CSP, which imports
FastAPI; this command needs no more than the package itself."""

HEADERS = f"""\
/*
  Content-Security-Policy: {CSP}
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  X-Robots-Tag: noindex
/data/*
  Cache-Control: no-cache
  Content-Security-Policy: sandbox; default-src 'none'
"""
"""The site's _headers. What data/ holds came from an export: it is read by
the page's scripts, and opened by itself it runs nothing."""

_SHA256 = re.compile(r"[0-9a-f]{64}")
_NOT_DOWNLOAD = frozenset({"record.json", "tree.json", "files"})


class SiteError(Exception):
  """An input the site cannot be written from, or a limit it would pass."""


@dataclass(frozen=True, slots=True)
class Download:
  """What an item's record.json says of its download.

  Attributes:
    path: Its name in the item's directory.
    size: Its size in bytes.
    sha256: The SHA-256 of its bytes, in hex.
  """

  path: str
  size: int
  sha256: str


def write_site(frontend: Path, out: Path, exports: list[Path]) -> int:
  """Write the static site of exports into a directory that is not there yet.

  The site is written into a new directory beside out and moved to out once
  it is whole; on a failure nothing is left.

  Args:
    frontend: The frontend's static build.
    out: The directory to write; it must not exist.
    exports: The export directories, each of a project of its own.

  Returns:
    How many files the site holds.

  Raises:
    SiteError: When out exists, an input fails a check, or the site would
      hold a file over FILE_LIMIT or more than FILES_LIMIT files.
  """
  if out.exists() or out.is_symlink():
    raise SiteError(f"{out}: already there; give a new directory")
  if not out.parent.is_dir():
    raise SiteError(f"{out.parent}: not a directory")
  work = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
  site = _Site(work)
  try:
    _copy_frontend(site, frontend)
    projects: list[dict[str, object]] = []
    for directory in exports:
      projects.append(_write_export(site, directory, projects))
    site.write_json("data/projects.json", projects)
    site.write("_headers", HEADERS.encode())
    work.chmod(0o755)
    _ = work.rename(out)
  finally:
    # Once moved to out, the site is whole; else nothing of it is left.
    if work.exists():
      shutil.rmtree(work)
  return site.files


@dataclass(slots=True)
class _Site:
  """The site's directory as it is written, with its limits.

  Attributes:
    root: The directory.
    files: How many files it holds so far.
  """

  root: Path
  files: int = 0

  def write(self, path: str, data: bytes) -> None:
    if len(data) > FILE_LIMIT:
      raise SiteError(
        f"{path}: {len(data)} bytes, over the {FILE_LIMIT} a file may hold"
      )
    self.files += 1
    if self.files > FILES_LIMIT:
      raise SiteError(f"more than the {FILES_LIMIT} files a site may hold")
    target = self.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    # "x": two inputs that would write the same file fail the command.
    with target.open("xb") as file:
      _ = file.write(data)

  def write_json(self, path: str, value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    self.write(path, text.encode())


def _copy_frontend(site: _Site, frontend: Path) -> None:
  try:
    top = os.open(frontend, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
  except OSError as error:
    raise SiteError(f"{frontend}: {error.strerror}") from None
  try:
    names = {entry.name: entry.type for entry in list_dir(top)}
    if names.get("index.html") != "file":
      raise SiteError(f"{frontend}: no index.html; not a frontend build")
    if reserved := sorted(RESERVED & names.keys()):
      raise SiteError(f"{frontend}: has {', '.join(reserved)} at its top")
    for path, fd in _files(top, str(frontend)):
      site.write(path, _read(fd, f"{frontend}/{path}"))
  finally:
    os.close(top)


def _write_export(
  site: _Site, directory: Path, before: list[dict[str, object]]
) -> dict[str, object]:
  try:
    with open_export(directory) as (top, export):
      if any(p["id"] == export.id for p in before):
        raise SiteError(f"{directory}: project {export.id} is given twice")
      base = f"data/{export.id}"
      site.write_json(
        f"{base}/items.json",
        [{"id": i.id, "revision": i.revision} for i in export.items],
      )
      for item in export.items:
        _write_item(site, top, export, item, f"{base}/items/{item.id}")
      return export.public()
  except (ExportError, RefusedError) as error:
    raise SiteError(f"{directory}: {error}") from None


def _write_item(
  site: _Site, top: int, export: Export, item: Item, base: str
) -> None:
  where = f"{export.id}/{item.id}"
  with open_item(top, export, item.id) as fd:
    with open_path(fd, "record.json") as record_fd:
      record = _read(record_fd, f"{where}/record.json")
    site.write(f"{base}/record.json", record)
    download = _download(record, where)
    tree: dict[str, list[dict[str, object]]] = {}
    for path, entries in _walk(fd, "", where):
      tree[path] = []
      for entry in entries:
        name = f"{path}/{entry.name}" if path else entry.name
        row: dict[str, object] = {
          "name": entry.name,
          "type": entry.type,
          "size": entry.size,
        }
        if entry.type == "file":
          if name in export.listed_only or (
            download is not None and name == download.path
          ):
            row["listed_only"] = True
          else:
            site.write(f"{base}/files/{name}", _read_at(fd, name, where))
        elif name in export.listed_only:
          raise SiteError(f"{where}/{name}: listed_only names a directory")
        tree[path].append(row)
    if download is not None:
      _write_download(site, fd, download, where, base)
    site.write_json(f"{base}/tree.json", tree)


def _download(record: bytes, where: str) -> Download | None:
  try:
    value = cast(object, json.loads(record))
  except ValueError, RecursionError:
    raise SiteError(f"{where}/record.json: not JSON") from None
  if not isinstance(value, dict):
    raise SiteError(f"{where}/record.json: not an object")
  found = cast(dict[str, object], value).get("download")
  if found is None:
    return None
  if not isinstance(found, dict):
    raise SiteError(f"{where}/record.json: download: not an object")
  fields = cast(dict[str, object], found)
  path = fields.get("path")
  size = fields.get("size")
  sha256 = fields.get("sha256")
  if (
    not isinstance(path, str)
    or not path
    or "/" in path
    or path in (".", "..")
    or path in _NOT_DOWNLOAD
  ):
    raise SiteError(
      f"{where}/record.json: download.path: not a plain name of a file"
    )
  if not isinstance(size, int) or isinstance(size, bool) or size < 0:
    raise SiteError(f"{where}/record.json: download.size: not a size")
  if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
    raise SiteError(f"{where}/record.json: download.sha256: not a SHA-256")
  return Download(path, size, sha256)


def _write_download(
  site: _Site, fd: int, download: Download, where: str, base: str
) -> None:
  data = _read_at(fd, download.path, where)
  if len(data) != download.size:
    raise SiteError(
      f"{where}/{download.path}: {len(data)} bytes, "
      + f"not the {download.size} record.json gives"
    )
  if hashlib.sha256(data).hexdigest() != download.sha256:
    raise SiteError(
      f"{where}/{download.path}: not the SHA-256 record.json gives"
    )
  site.write(f"{base}/{download.path}", data)


def _walk(fd: int, path: str, where: str) -> Iterator[tuple[str, list[Entry]]]:
  # The directory fd, at path, and every one under it, with its entries.
  entries = list_dir(fd)
  for entry in entries:
    name = f"{path}/{entry.name}" if path else entry.name
    _check_name(entry.name, f"{where}/{name}")
    if entry.type not in ("file", "dir"):
      raise SiteError(
        f"{where}/{name}: a symlink or not a regular file, never copied"
      )
    if entry.name in UNSENT:
      raise SiteError(f"{where}/{name}: a name the host's upload leaves out")
  yield path, entries
  for entry in entries:
    if entry.type != "dir":
      continue
    name = f"{path}/{entry.name}" if path else entry.name
    with open_path(fd, entry.name) as child:
      if not is_dir(child):
        raise SiteError(f"{where}/{name}: no longer a directory")
      yield from _walk(child, name, where)


def _files(top: int, where: str) -> Iterator[tuple[str, int]]:
  # Every file under top by its path, with it open.
  for path, entries in _walk(top, "", where):
    for entry in entries:
      if entry.type == "file":
        name = f"{path}/{entry.name}" if path else entry.name
        with open_path(top, name) as fd:
          yield name, fd


def _check_name(name: str, where: str) -> None:
  try:
    _ = name.encode()
  except UnicodeEncodeError:
    raise SiteError(f"{where!r}: a name that is not UTF-8") from None


def _read_at(fd: int, path: str, where: str) -> bytes:
  with open_path(fd, path) as file:
    return _read(file, f"{where}/{path}")


def _read(fd: int, where: str) -> bytes:
  try:
    return read_regular(fd, FILE_LIMIT).data
  except RefusedError as error:
    raise SiteError(f"{where}: {error.reason}") from None
