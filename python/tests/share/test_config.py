from pathlib import Path

import pytest

from dataforge.share.config import ConfigError, load_site
from tests.share.sites import make_tree


def test_the_site_file_loads(tmp_path: Path) -> None:
  tree = make_tree(tmp_path)

  site = load_site(tree.site)

  assert (site.listen.host, site.listen.port) == ("127.0.0.1", 8810)
  assert site.tokens == tree.tokens
  assert site.projects == (tree.export, tmp_path / "exports" / "other")


@pytest.mark.parametrize("host", ['"::1"', "100.64.0.1"])
def test_a_loopback_or_a_private_address_is_taken(
  tmp_path: Path, host: str
) -> None:
  tree = make_tree(tmp_path)
  text = tree.site.read_text().replace("127.0.0.1", host)
  _ = tree.site.write_text(text)

  assert load_site(tree.site).listen.host == host.strip('"')


@pytest.mark.parametrize(
  ("host", "words"),
  [
    ("0.0.0.0", "would listen everywhere"),
    ('"::"', "would listen everywhere"),
    ("localhost", "is not an IP address"),
    ("share.example", "is not an IP address"),
  ],
)
def test_a_wildcard_or_a_name_is_refused(
  tmp_path: Path, host: str, words: str
) -> None:
  tree = make_tree(tmp_path)
  _ = tree.site.write_text(tree.site.read_text().replace("127.0.0.1", host))

  with pytest.raises(ConfigError, match=f"listen.host: .*{words}"):
    _ = load_site(tree.site)


_REST = "tokens: t\nprojects: [.]\n"


@pytest.mark.parametrize(
  ("text", "words"),
  [
    ("listen: {host: 127.0.0.1}\n" + _REST, "listen.port: missing"),
    (_REST, "listen: missing"),
    ("listen: {host: 127.0.0.1, port: 0}\n" + _REST, "out of range"),
    (
      "listen: {host: 127.0.0.1, port: 1, host: 127.0.0.2}\n" + _REST,
      "repeated",
    ),
    ("listen: {host: 127.0.0.1, port: 1}\ntokens: t\nprojects: []\n", "list"),
  ],
)
def test_a_site_file_that_fails_a_check_is_refused(
  tmp_path: Path, text: str, words: str
) -> None:
  site = tmp_path / "site.yaml"
  _ = site.write_text(text)

  with pytest.raises(ConfigError, match=words):
    _ = load_site(site)


def test_an_unknown_key_or_a_missing_directory_is_refused(
  tmp_path: Path,
) -> None:
  tree = make_tree(tmp_path)
  text = tree.site.read_text()
  _ = tree.site.write_text(text + "queue: q.yaml\n")
  with pytest.raises(ConfigError, match="queue: unknown key"):
    _ = load_site(tree.site)

  _ = tree.site.write_text(text.replace("exports/other", "exports/none"))
  with pytest.raises(ConfigError, match=r"projects\[1\]: .* not a directory"):
    _ = load_site(tree.site)
