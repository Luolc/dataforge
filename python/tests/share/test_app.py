from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from typing import cast

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import httpx2 as httpx
import pytest

from dataforge.share import tokens
from dataforge.share.app import (
  COOKIE,
  create_app,
  DENIED,
  LOGIN_LIMIT,
  PLACEHOLDER,
)
from dataforge.share.config import load_site
from tests.share.sites import (
  export_json,
  make_tree,
  NOW,
  RECORD,
  Tree,
  write_export,
)

API = "/api/projects/example"
ITEM = {"attempt": "item-1"}
REFUSED = {"error": "unauthorized", "reason": DENIED}
# The only routes that are not GETs or HEADs; they write only a cookie.
POSTS = ("/api/login", "/api/logout")
# The headers of every response: scripts, images and connections only from
# the site's own origin, images also as data:; no sniffing, no caching and
# no Referer.
HEADERS = {
  "content-security-policy": (
    "default-src 'self'; script-src 'self'; connect-src 'self';"
    " img-src 'self' data:; font-src 'self'; style-src 'self';"
    " object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
    " form-action 'none'"
  ),
  "x-content-type-options": "nosniff",
  "cache-control": "no-store",
  "referrer-policy": "no-referrer",
}


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  return make_tree(tmp_path)


@pytest.fixture
def static(tmp_path: Path) -> Path:
  dist = tmp_path / "dist"
  (dist / "assets").mkdir(parents=True)
  _ = (dist / "index.html").write_text("<!doctype html><title>share</title>")
  _ = (dist / "assets" / "app.js").write_text("console.log(1)")
  return dist


def client(tree: Tree, static: Path | None = None) -> TestClient:
  return TestClient(create_app(load_site(tree.site), static, lambda: NOW))


def signed_in(tree: Tree, *projects: str) -> TestClient:
  token, _ = tokens.create(tree.tokens, projects or ["example"], "r1", now=NOW)
  web = client(tree)
  assert login(web, token).status_code == 204
  return web


def login(web: TestClient, token: str) -> httpx.Response:
  return web.post("/api/login", json={"token": token})


def ids(response: httpx.Response) -> list[str]:
  rows = cast(list[dict[str, object]], response.json())
  return [cast(str, row["id"]) for row in rows]


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
  found: dict[str, tuple[int, int, int]] = {}
  for top, dirs, files in os.walk(root):
    for name in dirs + files:
      path = Path(top) / name
      status = path.lstat()
      found[str(path)] = (status.st_mode, status.st_size, status.st_mtime_ns)
  return found


def test_every_route_is_a_get_but_the_two_posts_and_writes_nothing(
  tree: Tree, static: Path
) -> None:
  app = create_app(load_site(tree.site), static, lambda: NOW)
  token, _ = tokens.create(tree.tokens, ["example"], "r1", now=NOW)
  web = TestClient(app)
  before = snapshot(tree.site.parent)
  assert login(web, token).status_code == 204
  urls = [
    "/api/projects",
    f"{API}/items",
    f"{API}/record?attempt=item-1",
    f"{API}/files?attempt=item-1&path=task",
    "/index.html",
  ]

  routes = [r for r in app.routes if isinstance(r, APIRoute)]
  posts = {r.path: r.methods for r in routes if r.path in POSTS}
  methods = {m for r in routes if r.path not in POSTS for m in r.methods or ()}
  for url in urls:
    assert web.get(url).status_code == 200, url
    assert web.head(url).status_code == 200, url
    for method in ("POST", "PUT", "PATCH", "DELETE"):
      assert web.request(method, url).status_code == 405, (method, url)
  assert web.post("/api/logout").status_code == 204

  assert methods == {"GET", "HEAD"}
  assert posts == {path: {"POST"} for path in POSTS}
  assert snapshot(tree.site.parent) == before


def test_a_sign_in_sets_a_cookie_no_script_or_other_site_reads(
  tree: Tree,
) -> None:
  token, _ = tokens.create(tree.tokens, ["example"], "r1", now=NOW)
  web = client(tree)

  response = login(web, token)

  assert response.status_code == 204
  cookie = response.headers["set-cookie"]
  parts = {p.strip().lower() for p in cookie.split(";")[1:]}
  assert cookie.startswith(f"{COOKIE}={token};")
  assert parts == {
    "httponly",
    f"max-age={14 * 24 * 3600}",
    "path=/",
    "samesite=strict",
  }
  assert web.get(f"{API}/items").json() == [
    {"id": "item-1", "revision": 1},
    {"id": "item-2", "revision": 1},
  ]


def refused_tokens(tree: Tree) -> dict[str, str | None]:
  def made(projects: list[str], when: datetime = NOW) -> str:
    return tokens.create(tree.tokens, projects, "r1", now=when)[0]

  valid = made(["example"])
  revoked = made(["example"])
  assert tokens.revoke(tree.tokens, revoked.split("_")[1], NOW)
  wrong = valid[:-1] + ("A" if valid[-1] != "A" else "B")
  return {
    "none": None,
    "not a token": "a-token",
    "a wrong secret": wrong,
    "an unknown id": "dfs_00000000" + valid[12:],
    "expired": made(["example"], NOW - timedelta(days=14)),
    "revoked": revoked,
    "of another project": made(["other"]),
  }


@pytest.mark.parametrize(
  "case",
  [
    "none",
    "not a token",
    "a wrong secret",
    "an unknown id",
    "expired",
    "revoked",
    "of another project",
  ],
)
def test_every_refused_token_gets_the_same_401(tree: Tree, case: str) -> None:
  token = refused_tokens(tree)[case]
  web = client(tree)
  if token is not None:
    web.cookies.set(COOKIE, token)
  urls = [
    f"{API}/items",
    f"{API}/record?attempt=item-1",
    f"{API}/files?attempt=item-1&path=task/README.md",
    f"{API}/files?attempt=nothing",
  ]
  if case != "of another project":
    urls.append("/api/projects")

  responses = [web.get(url) for url in urls]
  if case != "of another project":
    responses.append(login(web, token or ""))

  for response in responses:
    assert response.status_code == 401, response.url
    assert response.json() == REFUSED
  assert len({r.content for r in responses}) == 1


def test_a_project_that_does_not_exist_reads_as_one_not_named(
  tree: Tree,
) -> None:
  web = signed_in(tree, "example", "nothing")

  response = web.get("/api/projects/nothing/items")
  unnamed = web.get("/api/projects/missing/items")

  assert response.status_code == 404
  assert unnamed.status_code == 401
  assert unnamed.json() == REFUSED


def test_no_response_repeats_a_token(tree: Tree, static: Path) -> None:
  token, _ = tokens.create(tree.tokens, ["example"], "r1", now=NOW)
  web = client(tree, static)
  assert login(web, token).status_code == 204
  urls = [
    f"/api/{token}",
    f"/api/projects/{token}/items",
    f"{API}/items?token={token}",
    f"{API}/record?attempt={token}",
    f"{API}/files?attempt=item-1&path={token}",
    f"{API}/files?attempt=item-1&path=task/{token}",
    f"/{token}",
  ]

  responses = [web.get(url) for url in urls]

  assert [r.status_code for r in responses] == [
    404,
    401,
    200,
    404,
    404,
    404,
    200,
  ]
  for response in responses:
    assert token not in response.text, response.url
    assert token not in str(response.headers), response.url


def test_a_revoked_token_is_refused_from_the_next_request(tree: Tree) -> None:
  token, row = tokens.create(tree.tokens, ["example"], "r1", now=NOW)
  web = client(tree)
  assert login(web, token).status_code == 204
  assert web.get(f"{API}/items").status_code == 200

  assert tokens.revoke(tree.tokens, row.id, NOW)

  assert web.get(f"{API}/items").status_code == 401
  assert web.get("/api/projects").status_code == 401


def test_a_token_sees_only_the_projects_it_names(tree: Tree) -> None:
  one = signed_in(tree, "example")
  both = signed_in(tree, "example", "other")

  shown = cast(object, one.get("/api/projects").json())

  assert shown == [
    {
      "id": "example",
      "name": "Example",
      "color": "#3a7bd5",
      "session": {"thinking": "collapsed"},
      "statuses": {"pass": {"label": "Pass", "tone": "good"}},
    }
  ]
  assert one.get("/api/projects/other/items").status_code == 401
  assert ids(both.get("/api/projects")) == ["example", "other"]
  assert both.get("/api/projects/other/items").status_code == 200


def test_export_json_is_read_again_for_every_request(tree: Tree) -> None:
  web = signed_in(tree)
  (tree.export / "items" / "item-3").mkdir()

  write_export(tree.export, export_json("example", ["item-1", "item-3"]))
  items = ids(web.get(f"{API}/items"))
  dropped = web.get(f"{API}/files", params={"attempt": "item-2"})

  assert items == ["item-1", "item-3"]
  assert dropped.status_code == 404


def test_an_item_export_json_does_not_list_is_not_served(tree: Tree) -> None:
  web = signed_in(tree)
  unlisted = tree.export / "items" / "item-x"
  unlisted.mkdir()
  _ = (unlisted / "record.json").write_text("{}")

  for route in ("record", "files"):
    response = web.get(f"{API}/{route}", params={"attempt": "item-x"})
    assert response.status_code == 404
    assert response.json()["reason"] == "no such item"


def test_the_record_is_the_file_as_written(tree: Tree) -> None:
  web = signed_in(tree)

  response = web.get(f"{API}/record", params=ITEM)

  assert response.status_code == 200
  assert response.headers["content-type"] == "application/json"
  assert response.json() == RECORD


def test_files_are_listed_and_served_as_bytes(tree: Tree) -> None:
  web = signed_in(tree)
  _ = (tree.item / "page.html").write_text("<script>alert(1)</script>")

  listing = web.get(f"{API}/files", params=ITEM | {"path": "task"})
  page = web.get(f"{API}/files", params=ITEM | {"path": "page.html"})

  assert listing.json() == {
    "entries": [{"name": "README.md", "type": "file", "size": 9}]
  }
  assert page.headers["content-type"] == "application/octet-stream"
  assert page.content == b"<script>alert(1)</script>"


def test_no_symlink_is_followed_and_no_path_leaves_the_export(
  tree: Tree,
) -> None:
  web = signed_in(tree)
  (tree.item / "link.txt").symlink_to(tree.outside)
  (tree.item / "up").symlink_to(tree.outside.parent)
  items = tree.export / "items"
  (items / "item-3").symlink_to(tree.item)
  write_export(tree.export, export_json("example", ["item-1", "item-3"]))
  paths = ["link.txt", "up/outside.txt", "../item-2", "/etc/hostname", ".."]

  listing = cast(
    dict[str, list[object]], web.get(f"{API}/files", params=ITEM).json()
  )
  refused = [
    web.get(f"{API}/files", params=ITEM | {"path": p}) for p in paths
  ] + [
    web.get(f"{API}/files", params={"attempt": "item-3"}),
    web.get(f"{API}/record", params={"attempt": "item-3"}),
    web.get(f"{API}/files", params={"attempt": "../other/items/item-9"}),
  ]

  assert {"name": "link.txt", "type": "symlink", "size": None} in listing[
    "entries"
  ]
  for response in refused:
    assert response.status_code == 404, response.url
    assert b"a host file" not in response.content


def test_an_export_json_that_is_a_symlink_is_not_read(tree: Tree) -> None:
  web = signed_in(tree, "example", "other")
  real = tree.site.parent / "export.json"
  _ = (tree.export / "export.json").rename(real)
  (tree.export / "export.json").symlink_to(real)

  response = web.get(f"{API}/items")

  assert response.status_code == 503
  assert ids(web.get("/api/projects")) == ["other"]


def test_an_export_that_fails_a_check_hides_only_itself(tree: Tree) -> None:
  web = signed_in(tree, "example", "other")
  broken = export_json("other", ["item-9"]) | {"format": 2}
  write_export(tree.site.parent / "exports" / "other", broken)

  response = web.get("/api/projects/other/items")

  assert response.status_code == 503
  assert ids(web.get("/api/projects")) == ["example"]
  assert web.get(f"{API}/items").status_code == 200


def test_a_token_database_that_cannot_be_read_is_503(tree: Tree) -> None:
  web = signed_in(tree)
  tree.tokens.unlink()

  response = web.get(f"{API}/items")

  assert response.status_code == 503
  assert response.json()["reason"] == "the tokens cannot be read"


@pytest.mark.parametrize(
  ("kind", "site", "body"),
  [
    ("text/plain", None, "token"),
    ("application/x-www-form-urlencoded", None, "token"),
    ("application/json", "cross-site", "token"),
    ("application/json", None, "too long"),
    ("application/json", None, "a list"),
  ],
)
def test_a_sign_in_takes_only_json_from_the_same_site(
  tree: Tree, kind: str, site: str | None, body: str
) -> None:
  token, _ = tokens.create(tree.tokens, ["example"], "r1", now=NOW)
  content = {
    "token": json.dumps({"token": token}),
    "too long": json.dumps({"token": token}) + " " * LOGIN_LIMIT,
    "a list": json.dumps([token]),
  }[body]
  headers = {"content-type": kind} | ({"sec-fetch-site": site} if site else {})

  response = client(tree).post("/api/login", headers=headers, content=content)

  assert response.status_code == 401
  assert response.json() == REFUSED
  assert "set-cookie" not in response.headers


def test_a_sign_out_clears_the_cookie(tree: Tree) -> None:
  web = signed_in(tree)

  response = web.post("/api/logout")

  assert response.status_code == 204
  assert response.headers["set-cookie"].startswith(f'{COOKIE}="";')
  assert "max-age=0" in response.headers["set-cookie"].lower()
  assert web.get(f"{API}/items").status_code == 401


def test_every_response_has_the_headers(tree: Tree, static: Path) -> None:
  token, _ = tokens.create(tree.tokens, ["example"], "r1", now=NOW)
  web = client(tree, static)
  responses = [
    web.get("/"),
    web.get("/assets/app.js"),
    web.get("/api/projects"),
    login(web, "a-token"),
    login(web, token),
    web.get("/api/projects"),
    web.get(f"{API}/items"),
    web.get(f"{API}/record"),
    web.get(f"{API}/record", params={"attempt": "item-2"}),
    web.get(f"{API}/files", params=ITEM | {"path": "task/README.md"}),
    web.get("/api/nothing"),
    web.post("/api/logout"),
  ]

  assert {r.status_code for r in responses} == {200, 204, 400, 401, 404}
  for response in responses:
    for name, value in HEADERS.items():
      assert response.headers.get(name) == value, (response.url, name)


def test_the_frontend_is_served_or_a_placeholder(
  tree: Tree, static: Path
) -> None:
  (static / "out.txt").symlink_to(tree.outside)
  web = client(tree, static)
  none = client(tree)

  script = web.get("/assets/app.js")
  linked = web.get("/out.txt")

  assert script.headers["content-type"].startswith("text/javascript")
  assert script.text == "console.log(1)"
  assert linked.text == "<!doctype html><title>share</title>"
  assert web.get("/item/item-1").text == linked.text
  assert none.get("/").text == PLACEHOLDER
  assert none.get("/assets/app.js").text == PLACEHOLDER
