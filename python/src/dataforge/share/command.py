"""The dataforge share command: serve, site, and token create, list, revoke."""

import argparse
from datetime import datetime, UTC
from pathlib import Path
import sqlite3
import sys
from typing import cast

from dataforge.share import site, tokens


def add_arguments(share: argparse.ArgumentParser) -> None:
  """Add the share command's subcommands and their arguments.

  Args:
    share: The parser of dataforge share.
  """
  subs = share.add_subparsers(dest="share_command", required=True)
  serve = subs.add_parser("serve", help="serve the site at its address")
  _ = serve.add_argument(
    "--config", type=Path, required=True, help="the site file (YAML)"
  )
  _ = serve.add_argument(
    "--static", type=Path, help="the built frontend; none for a placeholder"
  )
  static = subs.add_parser(
    "site",
    help="write a static site of exports, for a host such as Cloudflare Pages",
    description=site.__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  _ = static.add_argument(
    "--frontend",
    type=Path,
    required=True,
    help="the frontend's static build (npm run build:share-static)",
  )
  _ = static.add_argument(
    "--out", type=Path, required=True, help="the site to write; must not exist"
  )
  _ = static.add_argument(
    "exports", type=Path, nargs="+", help="the export directories"
  )
  token = subs.add_parser("token", help="the site's access tokens")
  verbs = token.add_subparsers(dest="token_command", required=True)
  create = verbs.add_parser(
    "create", help="make a token and print it, the one time it is shown"
  )
  _ = create.add_argument(
    "--project",
    action="append",
    required=True,
    help="a project it may see, by ID; repeat for more",
  )
  _ = create.add_argument(
    "--label", required=True, help="a code for who holds it, not a name"
  )
  _ = create.add_argument(
    "--days",
    type=int,
    default=tokens.DAYS,
    help=f"how many days it is valid for (default {tokens.DAYS})",
  )
  listing = verbs.add_parser(
    "list", help="list the tokens, without their secrets"
  )
  revoke = verbs.add_parser("revoke", help="revoke a token at once")
  _ = revoke.add_argument("id", help="the token's ID, its public half")
  for parser in (create, listing, revoke):
    _ = parser.add_argument(
      "--tokens", type=Path, required=True, help="the token database"
    )


def run(args: argparse.Namespace) -> int:
  """Run a share subcommand.

  Args:
    args: The parsed arguments.

  Returns:
    For serve, see dataforge.share.serve; else 0 on success, 1 for an
    unknown token or a database that cannot be read or written, or a site
    that cannot be written, 2 for bad arguments or without the extra viz.
  """
  if cast(str, args.share_command) == "site":
    return _site(args)
  if cast(str, args.share_command) == "serve":
    # Imported here: FastAPI and uvicorn come only with the extra viz.
    try:
      from dataforge.share.serve import serve
    except ImportError as error:
      print(
        f"dataforge share: {error}; install dataforge with the extra viz",
        file=sys.stderr,
      )
      return 2
    return serve(cast(Path, args.config), cast(Path | None, args.static))
  command = cast(str, args.token_command)
  db = cast(Path, args.tokens)
  try:
    if command == "create":
      return _create(args, db)
    if command == "list":
      _list(db)
      return 0
    id_ = cast(str, args.id)
    if tokens.revoke(db, id_):
      print(f"token {id_}: revoked")
    else:
      print(f"token {id_}: revoked before")
  except tokens.NoSuchTokenError as error:
    print(f"dataforge share token: no token {error}", file=sys.stderr)
    return 1
  except (sqlite3.Error, OSError) as error:
    print(f"dataforge share token: {db}: {error}", file=sys.stderr)
    return 1
  return 0


def _site(args: argparse.Namespace) -> int:
  out = cast(Path, args.out)
  try:
    files = site.write_site(
      cast(Path, args.frontend), out, cast(list[Path], args.exports)
    )
  except (site.SiteError, OSError) as error:
    print(f"dataforge share site: {error}", file=sys.stderr)
    return 1
  print(f"dataforge share site: {out}: {files} files", file=sys.stderr)
  return 0


def _create(args: argparse.Namespace, db: Path) -> int:
  try:
    token, row = tokens.create(
      db,
      cast(list[str], args.project),
      cast(str, args.label),
      cast(int, args.days),
    )
  except ValueError as error:
    print(f"dataforge share token create: {error}", file=sys.stderr)
    return 2
  # The token alone on stdout; what it is, on stderr.
  print(token)
  print(
    f"token {row.id} ({row.label}) for {', '.join(row.projects)}, "
    + f"expires {row.expires_at.isoformat()}; it is not shown again",
    file=sys.stderr,
  )
  return 0


def _list(db: Path) -> None:
  now = datetime.now(UTC)
  rows = [("ID", "LABEL", "PROJECTS", "CREATED", "EXPIRES", "STATE")]
  rows += [
    (
      t.id,
      t.label,
      ",".join(t.projects),
      t.created_at.isoformat(),
      t.expires_at.isoformat(),
      t.state(now),
    )
    for t in tokens.tokens(db)
  ]
  widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
  for row in rows:
    print(
      "  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)).rstrip()
    )
