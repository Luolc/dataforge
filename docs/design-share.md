# dataforge share design

The design of share, the read-only site that shows an exported set of data to a person who holds an access token, kept apart from [`design.md`](design.md) and [`design-viz.md`](design-viz.md) for length; it follows the same conventions. The backend, `dataforge.share`, the token commands and the frontend are built.

## 1. What this is, and what it is not

share is a site for people outside the machine. It reads one or more export directories (section 4, "The export directory") and gives a person who holds a valid token the projects the token names, and nothing else. It has two parts:

- a backend, the module `dataforge.share` in the Python package, a FastAPI app that reads export directories and serves a JSON API and, when it is given one, the frontend's files; `dataforge share token create|list|revoke` manage the tokens;
- a frontend, `web/share.html` and `web/src/share/` (section 5).

It is not the viewer ([`design-viz.md`](design-viz.md)), and the two are kept apart so that nothing the viewer can do is one mistake away from the outside:

- It is another process (`dataforge share serve`), with its own site file and token database, and the viewer's process serves no share route.
- It has its own frontend entry, built on its own, so the viewer's pages are not in what the site serves (section 5).
- Its routes are a short list (section 4, "The API"); every other path under `/api` is 404, and a path outside `/api` gets the frontend's files. It has no route for a queue, an action, a monitor or an attempt's record as the engine writes it, so an `/api` request for one gets a 404, not data.
- It has no write route: the only `POST`s are login and logout, which write a cookie and nothing else (invariant 2).
- What it can show is at most what an export directory holds. Which data goes into an export, and how it is checked, belongs to whoever writes the export, not to share.

Not in scope: writing an export (share only reads one), deploying (the service unit, the machine, the network and its access rules belong to whoever runs it), and TLS (section 4, "Plain HTTP").

## 2. Parts and how they connect

```text
  browser --- POST /api/login (token -> cookie) ---> dataforge.share (FastAPI)
          --- GET /api/projects... (cookie) ------->        |
          --- GET /<anything else> (frontend) ----->        |
                                                            v
     site file (listen, tokens, projects) -> token database (SQLite, read-only here)
                                          -> export directories (export.json, items/<id>/...)
```

`dataforge share serve --config <site file> [--static <dir>]` starts one process. It reads the site file once at start and refuses to start when it fails its check or the token database cannot be read, before it binds. It then reads the token database and the export directories on each request, with no cache. `dataforge share token ...` runs by itself, on the machine that holds the database.

## 3. Invariants

Each invariant links to the tests that guard it, in `python/tests/share/`.

1. The server binds only `listen.host` and `listen.port` from the site file; a host that is not an IP address literal, or is an unspecified one (`0.0.0.0`, `::`), is refused before anything binds (the tests guard the refusal). Tests: [`test_config.py`](../python/tests/share/test_config.py): `test_a_wildcard_or_a_name_is_refused`; [`test_serve.py`](../python/tests/share/test_serve.py): `test_serve_refuses_a_wildcard_or_a_name_before_it_binds`.
2. Every route answers only `GET` and `HEAD`, but `POST /api/login` and `POST /api/logout`, and no request writes a file. Test: [`test_app.py`](../python/tests/share/test_app.py): `test_every_route_is_a_get_but_the_two_posts_and_writes_nothing`.
3. A token that is missing, malformed, wrong, unknown, expired or revoked, or is for another project, gets the same 401 with the same body on every project route and on login, so a refusal does not say which; a project that does not exist reads as one the token does not name. Tests: [`test_app.py`](../python/tests/share/test_app.py): `test_every_refused_token_gets_the_same_401`, `test_a_project_that_does_not_exist_reads_as_one_not_named`; [`test_tokens.py`](../python/tests/share/test_tokens.py): `test_a_token_that_differs_or_is_revoked_is_refused`, `test_a_token_is_valid_only_until_it_expires`.
4. A revoke takes effect on the next request: the cookie's token is checked against the database on every request, and there is no session table. Test: [`test_app.py`](../python/tests/share/test_app.py): `test_a_revoked_token_is_refused_from_the_next_request`.
5. A token sees only the projects it names. Test: [`test_app.py`](../python/tests/share/test_app.py): `test_a_token_sees_only_the_projects_it_names`.
6. The database keeps only the SHA-256 of a token's secret; a check compares it with `hmac.compare_digest`, and the server opens the database read-only. Tests: [`test_tokens.py`](../python/tests/share/test_tokens.py): `test_a_token_is_shown_once_and_kept_only_as_its_hash`, `test_the_secret_is_compared_in_constant_time`, `test_a_check_opens_the_database_read_only`.
7. The cookie is `HttpOnly`, `SameSite=Strict` and `Path=/`, with `Max-Age` until the token expires; login takes only JSON from the site's own origin, so a page of another site cannot sign a person in. Tests: [`test_app.py`](../python/tests/share/test_app.py): `test_a_sign_in_sets_a_cookie_no_script_or_other_site_reads`, `test_a_sign_in_takes_only_json_from_the_same_site`, `test_a_sign_out_clears_the_cookie`.
8. `export.json` is read again for every request, and only the items it lists are served. Tests: [`test_app.py`](../python/tests/share/test_app.py): `test_export_json_is_read_again_for_every_request`, `test_an_item_export_json_does_not_list_is_not_served`.
9. No symlink inside an export is followed and no read leaves the export: every component of an item and a path is opened one at a time with `O_NOFOLLOW`, as design-viz.md invariant 2 does for an attempt. A project's directory in the site file is configuration and is opened as given. Tests: [`test_app.py`](../python/tests/share/test_app.py): `test_no_symlink_is_followed_and_no_path_leaves_the_export`, `test_an_export_json_that_is_a_symlink_is_not_read`.
10. Every response carries `Content-Security-Policy` (the viewer's policy), `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` and `Referrer-Policy: no-referrer`, a server failure (500) included. Tests: [`test_app.py`](../python/tests/share/test_app.py): `test_every_response_has_the_headers`, `test_a_failure_of_the_server_has_the_headers_too`.
11. No token reaches the server's log or a response it did not set: there is no access log, and no refusal repeats a request's path, item or parameters. Tests: [`test_serve.py`](../python/tests/share/test_serve.py): `test_no_token_and_no_request_reaches_the_log`; [`test_app.py`](../python/tests/share/test_app.py): `test_no_response_repeats_a_token`, `test_a_listed_item_that_is_a_symlink_or_missing_names_no_path`.
12. `token create` shows a token once, on standard output; `token list` shows no secret and no hash. Test: [`test_tokens.py`](../python/tests/share/test_tokens.py): `test_the_commands_show_the_token_once_and_list_no_secret`.
13. The token goes only in the body of `POST /api/login`, from a `type=password` field with `autocomplete=off`; it reaches no URL and no `localStorage`, and the field is cleared after a sign-in. Tests: [`share.test.tsx`](../web/src/share/share.test.tsx): `asks for a token in a field that does not show or keep it`, `signs in with the token in a POST body, then lists the items`.
14. Every refused sign-in shows the same sentence, whatever the server's reason. Test: [`share.test.tsx`](../web/src/share/share.test.tsx): `tells every refused token the same thing, whatever the server's reason`.
15. A failed sign-out says so and keeps the person signed in. Test: [`share.test.tsx`](../web/src/share/share.test.tsx): `says so when a sign-out fails, and stays signed in`.
16. The list has one column, an item's ID with its revision, a search that says how many items it hides, and a refresh, and no filter. Tests: [`share.test.tsx`](../web/src/share/share.test.tsx): `has one column, of each item's ID with its revision`, `searches the IDs and says how many the search hides`, `has a refresh and a colour template switch, and no filter`.
17. The item page has one tab for each deliverable of kind `files`, `trials` or `report`, and nothing to act on. Test: [`share.test.tsx`](../web/src/share/share.test.tsx): `has exactly the three tabs of the item's deliverables, and nothing to act on`.
18. The Pass@K tab shows no duration, cost, turns, tokens, tool call count or diff, its trace's gutter shows no time whatever timestamps the trace holds, and its trace starts with every fold open. Tests: [`share.test.tsx`](../web/src/share/share.test.tsx): `shows no duration, cost, turns, tokens or diff anywhere`, `opens every fold of the trace from the start`; [`Session.test.tsx`](../web/src/session/Session.test.tsx): `shows the model alone in the header when told to`, `shows every total in the header by default`.
19. The viewer's build holds no share page and the share build none of the viewer's: untested (it follows from the two inputs in `vite.config.ts`).
20. This file has at most 200 lines. [`design-length.test.sh`](../.github/scripts/design-length.test.sh).

## 4. Interfaces

### The site file

YAML, held by whoever runs the site, paths relative to the file:

```yaml
listen: {host: 192.0.2.10, port: 8810}   # invariant 1
tokens: tokens.sqlite
projects: [exports/example-live]         # export directories
```

The file loads only when it passes every check, as the viewer's does (design-viz.md invariant 6): no repeated key, each value of its type, `projects` a non-empty list of existing directories, none repeated. A project's directory is configuration and opened as given, so a symlink in its path is followed and a new export is put live by moving a symlink; each request reads the export again from one open directory, so a swap in the middle of a request does not mix two exports. An export whose `export.json` is missing, is a symlink or fails a check hides only itself: `/api/projects` leaves it out and its routes answer 503.

### The export directory

```text
<export dir>/
  export.json     {format: 1, project: {id, name, color}, session: {thinking},
                   statuses: {<word>: {label, tone}}, items: [{id, revision}]}
  items/<id>/     record.json and the files its deliverables name
```

`project.id` is of `[a-z0-9-]` and `color` is `#rrggbb`. `session.thinking` is `hidden` (default), `collapsed` or `expanded`, as in the viewer's project file. `statuses` gives each status word its `label` and a `tone` of `good`, `bad`, `warn` or `neutral`. `items` lists the items served, each by an `id` (a plain name, not repeated) and a `revision` from 1. `record.json` is served as written. Its `deliverables` take the shape of design.md's ["The record"](design.md#the-record) (`files`, `trials` and `report` kinds), so the viewer's views can draw them. Whoever writes an export owns what is in it and its checks; share trusts nothing in it (the CSP and `nosniff` of invariant 10 hold for its files).

### Tokens

A token is `dfs_<id>_<secret>`: `id` 8 hex digits, the public half, and `secret` 32 random bytes in base64url. The database is SQLite with two tables, `token` (`id`, the SHA-256 of the secret's text, `label`, `created_at`, `expires_at`, `revoked_at`) and `token_project` (`token_id`, `project`); `token create` makes it with mode 0600, or tightens an existing file to it ([`test_tokens.py`](../python/tests/share/test_tokens.py): `test_a_database_that_exists_is_tightened_to_its_owner`). A check finds the row by `id`, compares the hash (invariant 6), and refuses an expired or revoked token. A secret of 256 random bits is not guessed, so there is no login rate limit.

- `dataforge share token create --tokens <db> --project <id> [--project ...] --label <code> [--days 14]` prints the token alone on standard output and what it is on standard error. `label` matches `[A-Za-z0-9._-]{1,64}`: a code for who holds the token, never a name. `--days` is from 1 to 365, 14 by default. A token is created by a person at a terminal, not by an agent, since the token's value would reach the agent's output.
- `token list --tokens <db>` shows ID, label, projects, created, expires and state (`valid`, `expired`, `revoked`).
- `token revoke --tokens <db> <id>`. `list` and `revoke` never make a database.

### The API

Every route answers `GET` and `HEAD`, but the two `POST`s, and returns JSON unless it says otherwise; an error is `{error, reason}`, and a refusal names no path, item or route (invariant 11).

| Route | Needs | Returns |
| -- | -- | -- |
| `POST /api/login` | JSON `{token}`, at most 4096 bytes | 204 and the cookie, or 401 |
| `POST /api/logout` | nothing | 204, the cookie cleared |
| `/api/projects` | a valid token | the token's projects, `[{id, name, color, session, statuses}]` from `export.json` |
| `/api/projects/<p>/items` | a token for `p` | `[{id, revision}]` |
| `/api/projects/<p>/record?attempt=<item>` | a token for `p` | `items/<item>/record.json` as written |
| `/api/projects/<p>/files?attempt=<item>&path=<rel>` | a token for `p` | a listing or the file's bytes, with the path and parameters of the viewer's route (design-viz.md, section 4) so the viewer's file and report views work on it |
| any other path under `/api` | nothing | 404 |
| any other path | nothing | the built frontend, or a placeholder page without `--static` |

A project the token does not name gives the 401 of invariant 3, so a token cannot probe project names; one it names that no export holds gives 404.

### Login, cookie and headers

Login takes `Content-Type: application/json` alone, which a page of another origin cannot send without a CORS preflight the site never allows, and, when the browser sends it, `Sec-Fetch-Site: same-origin`; every failure is the 401 of invariant 3. Success sets `share=<token>; HttpOnly; SameSite=Strict; Path=/; Max-Age=<seconds until expiry>`. A token never goes in a URL, where it would reach history, `Referer` and logs. uvicorn runs with `access_log=False`; the app logs no body and no cookie.

### Plain HTTP

The site has no TLS, so the cookie has no `Secure`. Anyone who can see the traffic between a person and the site, such as on public Wi-Fi or a provider's network, reads the token at login and the cookie on every request, and the pages, until the token expires or is revoked. What limits it: 14 days by default, one token per holder (a leak revokes one), `token revoke` at any time (invariant 4). TLS, `Secure` and HSTS come when the site is given a domain.

## 5. The frontend

`web/share.html` and `web/src/share/` are an entry of their own, built by `npm run build:share` (`vite build --mode share`) with only that entry into `web/dist-share/`, where a small plugin writes the page as `index.html`; `--static` serves that directory. `npm run build` builds the viewer alone. The pages reuse the viewer's `ui/`, `session/` and `views/` components, which read through the same file route.

- Signed in or not: the shell reads `/api/projects`; a 401, there or on any later read, shows the landing page, a single field for a token. The token is sent only in the body of `POST /api/login` (invariant 13).
- The shell has the token's projects in a sidebar and a Sign out button, but no Monitor and no Queue, with the viewer's colour template switch; the sidebar is fixed at the left, or a drawer below the `lg` breakpoint, as the viewer's is (design-viz.md, ["The pages"](design-viz.md#5-the-pages), Shell). `/` goes to the first project.
- `/<project>` lists the items, one column of each item's ID with its revision, with a search and a refresh (invariant 16).
- `/<project>/items/<id>?tab=<name>` shows one tab for each `files`, `trials` or `report` deliverable, titled as `record.json` titles it, in `record.json`'s order (invariant 17). Files and report use the viewer's file and report views, with status cells drawn from `export.json`'s `statuses`. The Pass@K tab gives the number solved, each trial with its status word, and the chosen trial's reward, exception and model, with its trace in the session view (invariant 18).
- The frontend's tests and screenshots load the synthetic export `web/fixtures/share/`.

## 6. Open questions

- Whether the site needs a login rate limit once it is behind a domain; today none, for the reason in "Tokens".
- How the built frontend reaches a machine, as for the viewer (design-viz.md, section 7).
