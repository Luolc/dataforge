# dataforge viz design

The design of viz, the attempt viewer, kept apart from [`design.md`](design.md) for length; it follows the same conventions. **Planned, not built**: every part below is to be built in the phases of section 6, and once a part is built, a design PR moves it from section 6's list to the present tense, as for `design.md`; the PR that builds it describes its effect under "Design impact".

## 1. What this is, and what it is not

viz is a read-only web viewer for the attempts a dataset runs on `dataforge.curation` (design.md section 5, ["The state machine"](design.md#5-the-state-machine)). It shows, for each attempt, its record, the runs it made and the deliverables its declaration names, so that a person can see what a machine did and what it produced. It has two parts in this repo:

- a backend, the module `dataforge.viz` in the Python package, that reads attempt directories and their records and serves a JSON API and the frontend's files;
- a frontend in `web/`, a single-page app built with Vite, React, TypeScript, Tailwind and Radix, with every third-party library bundled, so it works with no network.

A dataset repo gives only configuration (section 4, "The project file"): where its attempts are, what its states and statuses are called and how they are coloured, how its attempts group into data items, and the extra columns of its home page. The viewer knows no dataset: it picks a view only by a deliverable's `kind`.

Not in scope:

- Writing anything: no POST, no comments, no tickets. The viewer never writes to an attempt directory (invariant 1).
- The Monitor page: the sidebar has its entry, and the page says it is not built.
- Running, resuming or stopping an attempt, and live updates; a page shows what was on disk when it was loaded, and reloads by the browser's own reload.
- Deploying: the service unit, the machine, the network and its access rules belong to whoever runs the viewer. How the built frontend reaches a machine is an open question (section 7).
- A single-file export for people outside the machine.

## 2. Parts and how they connect

```text
  browser: web/ (React SPA) --- GET /api/... (JSON, files as bytes) ---> dataforge.viz (FastAPI)
                               GET /<anything else> (the built web/dist)        |
                                                                                 v
     site file (listen, projects) -> project files (one per dataset) -> attempt directories
                                                                         record file (Attempt.to_json())
                                                                         artifacts/, summaries/, trials
```

`dataforge viz serve --config <site file> --static <dir>` starts one process. It reads the site file and each project file once at start and refuses to start when one fails its check (invariant 6); it then reads attempt directories on each request, with no cache and no database. `<dir>` is the output of `npm run build` in `web/`. The frontend talks to the backend only through the API in section 4 and draws everything from the record as written.

## 3. Invariants

Each invariant links to the tests that guard it, in `python/tests/viz/` or next to the frontend module in `web/src/`; invariant 5 is also kept by review.

1. Read-only: no route writes, creates, moves or deletes anything, under an attempt's root or elsewhere; every route answers only `GET` and `HEAD`, and any other method gets 405. Tests: [`test_app.py`](../python/tests/viz/test_app.py): `test_every_route_is_a_get_and_writes_nothing`.
2. No symlink is followed and no read leaves the attempt's root (its directory, as in design.md, ["Artifacts"](design.md#artifacts)), except that the item's `lock` file is `stat`ed, never read, from the item's directory: an attempt ID or a path is refused (404 with a reason) when it is absolute or holds a `..` or empty component, and an attempt ID also when it does not match `attempts`. The project file's `attempts_dir` is configuration and is opened as given, its own components included; from it, every component of the attempt ID, then of the path or the `record` name, is opened one at a time with `O_NOFOLLOW`, as is every component of the item and the `lock` path, so an attempt directory, a record, a lock file or anything in an attempt that is a symlink is never followed. A symlink in a listing is shown as a `symlink` entry with no target. Tests: [`test_files.py`](../python/tests/viz/test_files.py): `test_a_path_out_of_the_root_is_refused`, `test_a_symlink_is_listed_and_never_opened`, `test_a_symlinked_record_is_never_read`; and for the lock file [`test_live.py`](../python/tests/viz/test_live.py): `test_a_symlinked_lock_is_never_followed`.
3. Content from an attempt is untrusted. Markdown is rendered with no raw HTML (it shows as text), an image is shown as a link and never loaded on its own, and a link opens only when a person clicks it, in a new tab with `noopener noreferrer`; a trace, a file and a summary are shown as text. Files are served as `application/octet-stream` with `X-Content-Type-Options: nosniff`, so a browser never runs one as a page, and every response carries a Content-Security-Policy that allows scripts, images and connections only from the viewer's own origin (images also as `data:`). Tests: [`web/src/ui/markdown.test.tsx`](../web/src/ui/markdown.test.tsx): `raw HTML shows as text`, `an image is a link, not loaded`; [`test_app.py`](../python/tests/viz/test_app.py): `test_files_are_served_as_bytes_with_nosniff`, `test_every_response_has_the_csp`.
4. The server binds only `listen.host` and `listen.port` from the site file: there is no default address, and a host that is not an IP address literal, or is an unspecified one (`0.0.0.0`, `::`), is refused before anything binds. Tests: [`test_config.py`](../python/tests/viz/test_config.py): `test_the_server_binds_only_the_configured_address`, `test_a_wildcard_or_a_name_is_refused`.
5. The frontend chooses a view only by `kind`: no module under `web/src/` holds a state, output or deliverable name, and a deliverable of a kind it does not know shows as a card with its name, kind, path and reason. Tests: [`web/src/views/registry.test.tsx`](../web/src/views/registry.test.tsx): `a view is chosen by kind only`, `an unknown kind falls back to a card`; the rest is for review.
6. A site or project file loads only when it passes every check: no unknown or repeated key, each value of its type, project IDs unique and of `[a-z0-9-]`, each tone one of section 4's, `attempts_dir` an existing directory, `attempts` a pattern of plain and `*` components only, `item.depth` at least 1 and at most the number of components of `attempts`, `not_ended` a list of words and `lock` a relative path of plain components; one that fails stops the start with the file and the key that is wrong. Tests: [`test_config.py`](../python/tests/viz/test_config.py): `test_a_project_file_that_breaks_a_rule_is_refused`.
7. The record is served as the file holds it, never reshaped: `GET .../record` returns the record file's JSON as written, and the frontend reads its types from design.md section 5, ["The record"](design.md#the-record), not from a schema of its own; how a page shows a record that lacks a key or is not of that shape is section 5's "Older records". Tests: [`test_app.py`](../python/tests/viz/test_app.py): `test_the_record_is_the_file_as_written`.
8. This file has at most 200 lines. [`design-length.test.sh`](../.github/scripts/design-length.test.sh).
9. No lock is ever taken: the backend learns whether a lock file is held only from `/proc/locks` and never calls `flock`, `fcntl.lockf` or `fcntl` on it, also not to test it, since an attempt that tries to take the lock at that moment would find it busy and fail. Tests: [`test_live.py`](../python/tests/viz/test_live.py): `test_a_held_lock_is_found`, `test_a_free_lock_is_not`, `test_the_backend_never_takes_a_lock`.

## 4. Interfaces

### The site file

YAML, read with PyYAML's safe loader, held by whoever runs the viewer:

```yaml
listen: {host: 192.0.2.10, port: 8800}   # invariant 4
projects: [/path/to/dataset/viz-project.yaml]
```

### The project file

YAML, safe loader, held by the dataset in its own repo; this is the whole of what a dataset gives the viewer:

```yaml
id: example            # in URLs; [a-z0-9-]
name: Example dataset  # in the sidebar's Projects menu and the breadcrumb
color: "#e5a54b"       # the project's dot in the menu
attempts_dir: /data/example/attempts
attempts: "*/*"        # directories under attempts_dir; an attempt's ID is its path relative to it
record: record.json    # the record file, relative to the attempt's directory
states:                # optional: a state's title in the graph and the run list; else its name
  author: {title: Author}
statuses:              # an attempt status or a visit outcome: its label and tone
  passed: {label: Passed, tone: good}
  too_easy: {label: Too easy, tone: warn}
item: {depth: 1, title: Item}   # an attempt ID's first `depth` components name its data item; `title` heads the ID column
columns:               # extra columns of the home page, after the fixed ones
  - {title: Cap, field: [budget, max_usd], format: usd}
  - {title: Kept visit, field: [kept_visit], format: number}
session: {thinking: collapsed}   # collapsed (default), expanded or hidden
not_ended: [running]   # optional: status words of an attempt that has not ended
lock: .lock            # optional: the item's lock file, relative to the item's directory
```

In `attempts`, a `*` matches one name that does not start with `.`. A tone is `good`, `bad`, `warn` or `neutral`; a word with no entry shows as itself, `neutral`, except `raised`, the engine's own word for an exception, which shows as itself, `bad`. A column's `field` is a list of keys into the record, as a deliverable's `Value` is (design.md, "The record"), and `format` is `text` (default), `number`, `usd`, `duration` or `time`. `session.thinking` is how a session view first shows thinking blocks; a person can still open, collapse or hide them on the page.

A dataset's record file may hold more than `Attempt.to_json()`: more top-level keys, which only a column's `field` reads, and its own `status` and `reason` in place of the engine's, such as a status word while the attempt runs. An attempt has not ended when its record's `status` is one of `not_ended`; without `not_ended`, when its `status` is null and its last visit's `outcome` is not `raised`, as the engine leaves it: `Attempt.status` is None until the attempt ends ([`engine.py`](../python/src/dataforge/curation/engine.py), `Attempt`), and an attempt a state's exception ended keeps status null after recording that visit as `raised` (design.md, invariant 12). The backend applies this rule for the listing's `live`, and the frontend, from `/api/projects`, for what it reads from the record alone, such as the attempt page's status and a run's end, where an attempt that has not ended shows as `Not ended`, never as its status word. With `not_ended`, an attempt whose `status` is null has ended with no status word, and shows everywhere as `No status`, `neutral`, a viewer word defined with the others (L37). A visit with no `outcome` shows as `Not ended` while its attempt has not ended, and once it has, as `No outcome`, `neutral`, another viewer word, wherever a visit's outcome label appears (the home page's rounds, the Runs column, a run page, the `trials` view's heading, a state's tone in the Summary's machine). `lock` names a file that a running attempt of the item holds a lock on (`flock` or a POSIX lock); it is a relative path of plain components, opened from the item's directory (`attempts_dir` and the ID's first `item.depth` components) one component at a time with `O_NOFOLLOW`, as invariant 2 opens an attempt, so it never holds `..`. The backend `stat`s it without following a symlink and looks for its device and inode in `/proc/locks` (invariant 9). Since one lock covers the whole item, an attempt that has not ended is `running` only when the lock is held and it is its item's newest attempt by `modified`; every other one that has not ended is `may have stopped`, the two cases a record alone cannot tell apart. Newest here is by `modified`, the record file's mtime and the listing's order, not by `started_at` as on the home page: a running attempt rewrites its record after each visit, while `started_at` is null until its first action has ended. An attempt whose record cannot be read has no `live` and is not its item's newest. A lock file that is missing or not a regular file counts as not held. `running`, `may have stopped` and `not ended` are the viewer's own status words, defined with the others the frontend shows (L37): `running` and `not ended` `neutral`, `may have stopped` `warn`.

### The API

Every route answers `GET` and `HEAD` and returns JSON unless it says otherwise; an error is `{error, reason}` with 400, 404 or 413. An attempt is named by `?attempt=<id>`, its ID from the listing.

| Route | Returns |
| -- | -- |
| `/api/projects` | `[{id, name, color, states, statuses, item, columns, session, not_ended}]`, each project file as loaded, without `attempts_dir`, `attempts`, `record` and `lock`; `not_ended` null when the file has none |
| `/api/projects/<p>/attempts` | `[{id, item, modified, status, reason, cost_usd, started_at, ended_at, rounds, values, live, error}]`, one per directory matching `attempts`, newest `modified` (the record file's mtime) first: `item` the ID's first `item.depth` components; `status`, `reason` and `cost_usd` (`budget.cost_usd`) from the record; `started_at` and `ended_at` its first and last action's times; `rounds` the last visit of each round, `[{round, state, outcome}]`; `values` each column's value, null where the record has no such key; `live` null when the attempt has ended, else `running` or `may have stopped` with a `lock`, or `not ended` without one (section 4, "The project file"); `error` says why when the record is missing, not a regular file or not JSON (the rest null) |
| `/api/projects/<p>/record?attempt=<id>` | the record file as written (invariant 7), at most `RECORD_LIMIT`, 64 MiB |
| `/api/projects/<p>/files?attempt=<id>&path=<rel>` | for a directory, `{entries: [{name, type, size}]}`, `type` one of `file`, `dir`, `symlink`, `other`, sorted by name; for a regular file, its bytes (invariant 3), at most `FILE_LIMIT`, 16 MiB, else 413 with the size in its `reason`; `path` is relative to the attempt's directory, as a record's paths are, and empty for the directory itself |

Everything else under `/api/` is 404; any other path serves a file of the static directory, or its `index.html` when there is none, so the frontend's routes load on reload.

### The frontend's routes

`/` opens the first project; `/<p>` is its home page, with `?q=`, `?status=` and `?sort=`; `/<p>/attempt?id=<id>` an attempt, with `&tab=` (`runs`, else a deliverable kind), `&run=<visit_index>` and `&file=<path>`; `/monitor` the Monitor entry. A link copied from the address bar opens the same view.

## 5. The pages

**Shell.** A fixed left sidebar, always shown, with the Projects menu (the projects of `/api/projects`, in the site file's order) and the Monitor entry; a breadcrumb at the top, `Projects › <name> › <attempt ID>`; a light theme, with body text in Mona Sans and code in Fira Code, its ligatures on, both fonts bundled with the frontend so a page needs no font server. Colours, typefaces, type sizes and spacing are Tailwind theme tokens in one file, `web/src/ui/theme.css`, and `web/src/ui/theme.test.ts` checks the scheme, the typefaces, that ligatures stay on and the contrast of the tones.

**Home page** (P-home). One row per data item, standing for its newest attempt, newest by `started_at` here and below; a readable record with no `started_at` has not finished its first action yet and counts as newer than any that has, an unreadable one as the oldest, and an item takes its group from its newest attempt alone. An item with more than one attempt opens to a sub-row per attempt, newest first, showing the part of the attempt ID after its item's ID, and a row or sub-row opens that attempt. Columns: the item ID (headed `item.title`), status as a tone-coloured label (the record's `reason` on hover, in a tooltip on a focusable label, so the keyboard opens it too), rounds (one cell per round: its last visit's state title and outcome label, in the outcome's tone), attempts, spent (the sum over the item's attempts), started, duration (`—` until the attempt ends), then the project's `columns`. An attempt has not ended when the listing's `live` is not null (section 4, "The project file"). Items whose newest attempt has not ended form the top group, newest first, its status shown as `live` says, `Running`, `May have stopped` or `Not ended`; ended ones follow, newest `ended_at` first, one ended by an exception shown as `raised`, one with no status word as `No status`, `neutral`; an item whose record cannot be read (`error`) comes last, with the status `Unreadable`, `bad`, and the `error` on hover, and counts as bad in its group's heading; each group folds and has a count as L44 asks. Above the table: a search box that matches item IDs, one chip at a time to filter by status, besides an All chip, with its count (the number a filter hides shown as L45 asks), and a refresh button with the time of the last read, to the minute (no polling; while it reads, `Reading…`, and the last read stays on the page). A chip counts the items the search keeps, so it says what choosing it shows, and the chosen chip stays, at 0, when the search leaves it none. There is a chip for each status the items have, the viewer's own among them (`Running`, `May have stopped`, `Not ended`, `No status` and `Unreadable`); its key in the URL is the project's status word as it is, or `~running`, `~may-have-stopped`, `~not-ended`, `~no-status`, `~unreadable`. Clicking a column header sorts within the groups: a column of numbers or times largest first, text A to Z, again to reverse, and a value a row does not have sorts last both ways. Built with TanStack Table. Not built: queued items, paging, columns a person picks, saved views.

**Attempt page.** A row of tabs below the breadcrumb: Runs first, then one per deliverable `kind` in the order its first deliverable is declared, named by that deliverable's `title`, each with a count (Runs: the visits; `files`: the files under its path; `trials`: `value` of `k`, as `2 / 4`; `report`: the reports). Several deliverables of one kind: `report` stacks them in one tab, `files` and `trials` show one, chosen from a menu, the first by default. A deliverable with `path` null shows its `reason`.

- **Runs.** A second column beside the sidebar: Summary, then one entry per visit in the record's order, each with its state's title, outcome label, duration and cost. Summary has a top row (Started, Status, Total duration, Cost, Deliverables), the whole machine drawn from `graph` (transitions not taken dashed, taken ones solid with a count; a state opens its last visit), the `summary` of each action that has one, in order, under its state and visit, and a card per deliverable with one line on it that opens its tab. A run page (P4) is the visit's steps, folded: its input (the report it was handed, when any), each action (`kind`, status, cost, times, outputs; an agent's session in the session view), then its output (outcome, the next visit, a mark); only failed steps and the visit's first agent session start open.
- **`files`.** A file tree of the deliverable's path on the left, a read-only preview on the right: syntax highlighting by extension, a diff coloured by line, Markdown rendered (invariant 3), other files as text, or their size when they are not UTF-8 or over `FILE_LIMIT`. It opens `instruction.md` or `README.md` when either is at the top, else the first file.
- **`trials`.** A list of the deliverable's trials on the left, headed by `value` (for example `2 / 4 solved`) and the visit's outcome, each trial by its `id` and whether its `reward` is at least 1.0 (what the default `solved` accepts); on the right a row of reward, exception, duration, cost and trace format, and below it the trial's trace in the session view, or the trace's absence; a trace over `FILE_LIMIT`, which design.md's `TRACE_LIMIT` (64 MiB) may still let through for its cost, shows its path and the 413's reason, here and on a run page.
- **`report`.** Each report's Markdown, in declaration order, under its title. In a table, a cell whose whole text is a key of the project's `statuses` is drawn as its label.

**Older records.** Not built: what follows will replace the attempt page's one line `unexpected data at <key>: ...`, which it now shows for every record its types refuse. A record file the backend serves (invariant 7) is one of three:

- A JSON object of the shape design.md, "The record", gives, except that any key `Attempt.to_json()` gained after its first version (#32, which wrote `{status, reason, kept_visit, final, visits}`) may be missing, as in a record an older dataforge wrote. The page shows what the record has, and each part that needs a missing key says so, as in the table below.
- Any other JSON object, such as one with no `visits`: the page does not read its keys. It has the Runs tab only, with no count, and no status mark by the attempt ID; the tab shows `This record is not in a shape the viewer knows (<why>). Below is the record as written.`, `<why>` naming the first key that does not fit, as `it has no visits`, then the record as indented JSON, as text (invariant 3).
- Not a JSON object, or larger than `RECORD_LIMIT` (the backend's 413): the whole page shows why, as for any read that fails.

| Missing key | Since | The page |
| -- | -- | -- |
| top-level `round` | #62 | nothing reads it |
| `budget` | #62 | the Summary's Cost shows `This record has no budget.` |
| a visit's `round` | #62 | its run page's heading leaves out `round N` |
| an action's `summary` | #78 | counts as null; when no action has the key, the Summary shows `This record has no summaries.` |
| `deliverables` | #86 | the Runs tab only; the Summary's Deliverables shows `—` and, in place of its card, `This record has no deliverables.` |
| `graph` | #86 | as when it is null: the Summary shows `This record has no graph.`, and a run page's input says that whether the visit was handed a report is not known |

**Session view.** One column, for traces whose `format` is `claude-code-stream-json`, parsed in the browser from the raw file; another format shows as text. A sticky header with the totals (duration, cost, turns, tokens, model); a narrow gutter with the time since the start; a tool call as one line that opens to its input and output, opened from the start when it failed; more than 4 tool calls of one message folded into one line; subagent calls indented under their parent; thinking shown as `session.thinking` says, one line by default, with a toggle to hide all of it; the result badge taken from the trial's reward, not from the agent's own last word. A trace with no thinking renders as usual.

## 6. Phases and where each part lives

Each phase owns the directories named for it and touches no other's, so P-home, P2, P3 and P4 run at once, P1 having merged. P1 wrote a placeholder in each later phase's entry module, `index.tsx` in its directory (for a view, its default export takes the deliverable or the visit and the record, for P-home the project; for P3, `web/src/session/index.tsx` exports `SessionView`), showing the deliverable's or visit's name, path and reason (on the home page, the attempt IDs), which that phase replaces. All of them develop and take screenshots against synthetic attempts in `web/fixtures/` and `python/tests/viz/fixtures/`, made by P1 (merged), which each phase may add to under a directory of its own name.

| Phase | Builds | Owns |
| -- | -- | -- |
| P1 | the backend, section 4 but for what P5 adds, and invariants 1, 2, 4, 6, 7; the shell, routes, API client and record types; shared UI (Markdown, code view and highlighting, status labels, the formatters L43 asks for, theme); the kind registry; Runs with its Summary | `python/src/dataforge/viz/`, `python/tests/viz/`, `web/` outside the directories below, `web/src/views/runs/summary/` |
| P2 | the `files` and `report` views | `web/src/views/files/`, `web/src/views/report/` |
| P3 | the session view and the `trials` view | `web/src/session/`, `web/src/views/trials/` |
| P4 | the run page | `web/src/views/runs/run/` |
| P-home | the home page, adding TanStack Table to the dependencies | `web/src/views/home/` |
| P5 | reading a dataset's real attempts: `not_ended`, `lock`, the listing's `live` and invariant 9 | `python/src/dataforge/viz/`, `python/tests/viz/` (after P1) |

P1's backend modules: `config.py` (site and project files, their checks), `paths.py` (opening an attempt and a path in it, invariant 2), `app.py` (the FastAPI app and its routes) and `serve.py` (the `dataforge viz serve` command). The frontend: `web/src/api/` (the client and the record's TypeScript types), `web/src/shell/`, `web/src/ui/`, `web/src/views/registry.tsx` (kind to view). P4 renders an agent's session with P3's `SessionView` from `web/src/session/`; until P3 merges, the placeholder shows the trace's path. A change one phase needs in another's directory goes to that phase's owner, or after its merge, in a PR of its own. P1 created `web/package.json` and `web/package-lock.json`; a later phase's PR that needs a new library may change them, but only one open PR changes them at a time, and once that one merges, the next merges main into its branch and regenerates the lock file.

The checks, besides the repo's Python checks with the `viz` extra installed: in `web/`, `npm ci`, then `npm run lint` (ESLint, Prettier and knip), `npm run typecheck`, `npm test` (Vitest) and `npm run build`. The backend's dependencies, FastAPI and uvicorn, are the package's optional extra `viz`, so a dataset repo that only runs attempts does not install them.

## 7. Open questions

- How the built frontend reaches a machine: built there from a checkout of the pinned commit, or shipped with the package (which would be a release).
- The Monitor page: what it shows and where its data comes from.
- A run page's input: the record does not yet say which files a visit read, so P4 shows only the report it was handed.
