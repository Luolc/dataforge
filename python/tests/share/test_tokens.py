from contextlib import closing
from datetime import timedelta
import hashlib
import hmac
from pathlib import Path
import re
import sqlite3
import stat

import pytest

from dataforge.cli import main
from dataforge.share import tokens
from tests.share.sites import NOW

TOKEN = re.compile(r"dfs_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")


def test_a_token_is_shown_once_and_kept_only_as_its_hash(
  tmp_path: Path,
) -> None:
  db = tmp_path / "tokens.sqlite"

  token, row = tokens.create(db, ["example"], "r1", now=NOW)

  assert TOKEN.fullmatch(token)
  secret = token.split("_", 2)[2]
  digest = hashlib.sha256(secret.encode()).digest()
  with closing(sqlite3.connect(db)) as connection:
    stored = connection.execute(
      "SELECT id, secret_sha256 FROM token"
    ).fetchall()
  assert stored == [(row.id, digest)]
  data = db.read_bytes()
  assert secret.encode() not in data
  assert token.encode() not in data
  assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_a_database_that_exists_is_tightened_to_its_owner(
  tmp_path: Path,
) -> None:
  db = tmp_path / "tokens.sqlite"
  db.touch(mode=0o644)
  db.chmod(0o644)

  _ = tokens.create(db, ["example"], "r1", now=NOW)

  assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_a_token_is_valid_only_until_it_expires(tmp_path: Path) -> None:
  db = tmp_path / "tokens.sqlite"
  token, row = tokens.create(db, ["example", "other"], "r1", now=NOW)

  found = tokens.check(db, token, NOW + timedelta(days=14, seconds=-1))

  assert found is not None
  assert found == row
  assert found.projects == ("example", "other")
  assert row.expires_at - row.created_at == timedelta(days=tokens.DAYS)
  assert tokens.check(db, token, NOW + timedelta(days=14)) is None


def test_a_token_that_differs_or_is_revoked_is_refused(tmp_path: Path) -> None:
  db = tmp_path / "tokens.sqlite"
  token, row = tokens.create(db, ["example"], "r1", now=NOW)
  other, _ = tokens.create(db, ["example"], "r2", now=NOW)
  wrong = token[:-1] + ("A" if token[-1] != "A" else "B")

  assert tokens.check(db, wrong, NOW) is None
  assert tokens.check(db, f"{token[:13]}{other[13:]}", NOW) is None
  assert tokens.check(db, "dfs_00000000" + token[12:], NOW) is None
  assert tokens.check(db, token + "x", NOW) is None
  assert tokens.check(db, "", NOW) is None
  assert tokens.revoke(db, row.id, NOW)
  assert tokens.check(db, token, NOW) is None
  assert not tokens.revoke(db, row.id, NOW)
  assert tokens.check(db, other, NOW) is not None


def test_the_secret_is_compared_in_constant_time(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  db = tmp_path / "tokens.sqlite"
  token, _ = tokens.create(db, ["example"], "r1", now=NOW)
  calls: list[tuple[bytes, bytes]] = []
  compare_digest = hmac.compare_digest

  def compare(a: bytes, b: bytes) -> bool:
    calls.append((a, b))
    return compare_digest(a, b)

  monkeypatch.setattr(hmac, "compare_digest", compare)

  assert tokens.check(db, token, NOW) is not None
  assert len(calls) == 1


def test_a_check_opens_the_database_read_only(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  db = tmp_path / "tokens.sqlite"
  token, _ = tokens.create(db, ["example"], "r1", now=NOW)
  uris: list[str] = []
  connect = sqlite3.connect

  def spy(database: str, uri: bool = False) -> sqlite3.Connection:
    uris.append(database)
    return connect(database, uri=uri)

  monkeypatch.setattr(sqlite3, "connect", spy)

  assert tokens.check(db, token, NOW) is not None
  tokens.readable(db)
  assert uris and all(u.endswith("?mode=ro") for u in uris)


@pytest.mark.parametrize(
  ("projects", "label", "days", "words"),
  [
    ([], "r1", 14, "needs a project"),
    (["Not An ID"], "r1", 14, "is not of"),
    (["example"], "A Person", 14, "a code, not a name"),
    (["example"], "r1", 0, "is not from 1"),
    (["example"], "r1", tokens.MOST_DAYS + 1, "is not from 1"),
  ],
)
def test_a_token_that_fails_a_check_is_not_made(
  tmp_path: Path, projects: list[str], label: str, days: int, words: str
) -> None:
  db = tmp_path / "tokens.sqlite"

  with pytest.raises(ValueError, match=words):
    _ = tokens.create(db, projects, label, days, now=NOW)

  assert not db.exists()


def test_the_commands_show_the_token_once_and_list_no_secret(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  db = str(tmp_path / "tokens.sqlite")

  args = ["--tokens", db, "--project", "example", "--label", "r1"]
  status = main(["share", "token", "create", *args])
  made = capsys.readouterr()
  token = made.out.strip()
  id_, secret = token.split("_", 2)[1:]
  digest = hashlib.sha256(secret.encode()).digest()
  assert main(["share", "token", "list", "--tokens", db]) == 0
  listed = capsys.readouterr()

  assert status == 0
  assert TOKEN.fullmatch(token)
  assert made.out == token + "\n"
  assert secret not in made.err
  assert id_ in listed.out
  assert "r1" in listed.out
  assert "valid" in listed.out
  for shown in (secret, digest.hex(), digest.hex().upper()):
    assert shown not in listed.out + listed.err


def test_a_command_revokes_a_token_at_once(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  db = tmp_path / "tokens.sqlite"
  token, row = tokens.create(db, ["example"], "r1")

  status = main(["share", "token", "revoke", "--tokens", str(db), row.id])
  unknown = main(["share", "token", "revoke", "--tokens", str(db), "f00f00f0"])
  _ = main(["share", "token", "list", "--tokens", str(db)])

  assert status == 0
  assert unknown == 1
  assert tokens.check(db, token, row.created_at) is None
  assert "revoked" in capsys.readouterr().out.splitlines()[-1]


def test_list_and_revoke_make_no_database(tmp_path: Path) -> None:
  db = tmp_path / "tokens.sqlite"

  assert main(["share", "token", "list", "--tokens", str(db)]) == 1
  assert (
    main(["share", "token", "revoke", "--tokens", str(db), "f00f00f0"]) == 1
  )
  assert not db.exists()
