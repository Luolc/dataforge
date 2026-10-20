# dataforge viz design

The design of viz, the attempt viewer, kept apart from [`design.md`](design.md) for length; it follows the same conventions. **Planned, not built**: every part below is to be built in the phases of section 6, and each PR that builds a part moves it from section 6's list to the present state.

## 1. What this is, and what it is not

viz is a read-only web viewer for the attempts a dataset runs on `dataforge.curation` (design.md section 5, ["The state machine"](design.md#5-the-state-machine)). It shows, for each attempt, its record, the runs it made and the deliverables its declaration names, so that a person can see what a machine did and what it produced. It has two parts in this repo:

- a backend, the module `dataforge.viz` in the Python package, that reads attempt directories and their records and serves a JSON API and the frontend's files;
- a frontend in `web/`, a single-page app built with Vite, React, TypeScript, Tailwind and Radix, with every third-party library bundled, so it works with no network.

A dataset repo gives only configuration (section 4, "The project file"): where its attempts are, what its states and statuses are called and how they are coloured, and the columns of its attempt table. The viewer knows no dataset: it picks a view only by a deliverable's `kind`.

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

None is tested yet: each names the test that the phase in section 6 adds, in `python/tests/viz/` or next to the frontend module in `web/src/`.

1. Read-only: no route writes, creates, moves or deletes anything, under an attempt's root or elsewhere; every route is a `GET` (a `HEAD` aside), and any other method gets 405. Untested (P1 adds `test_app.py`: `test_every_route_is_a_get_and_writes_nothing`).
2. No symlink is followed and no read leaves the attempt's root: an attempt ID or a path is refused (404 with a reason) when it is absolute or holds a `..` or empty component, and an attempt ID also when it does not match `attempts`; and it is opened one component at a time from the root with `O_NOFOLLOW`; a symlink in a listing is shown as a `symlink` entry with no target and is never opened, nor is the record or a project file's `root` when it is reached through one. Untested (P1 adds `test_files.py`: `test_a_path_out_of_the_root_is_refused`, `test_a_symlink_is_listed_and_never_opened`, `test_a_symlinked_record_is_never_read`).
3. Content from an attempt is untrusted. Markdown is rendered with no raw HTML (it shows as text), an image is shown as a link and never loaded on its own, and a link opens only when a person clicks it, in a new tab with `noopener noreferrer`; a trace, a file and a summary are shown as text. Files are served as `application/octet-stream` with `X-Content-Type-Options: nosniff`, so a browser never runs one as a page, and every response carries a Content-Security-Policy that allows scripts, images and connections only from the viewer's own origin (images also as `data:`). Untested (P1 adds `web/src/ui/markdown.test.tsx`: `raw HTML shows as text`, `an image is a link, not loaded`; `test_app.py`: `test_files_are_served_as_bytes_with_nosniff`, `test_every_response_has_the_csp`).
4. The server binds only `listen.host` and `listen.port` from the site file: there is no default address, and a host that is not an IP address literal, or is an unspecified one (`0.0.0.0`, `::`), is refused before anything binds. Untested (P1 adds `test_config.py`: `test_the_server_binds_only_the_configured_address`, `test_a_wildcard_or_a_name_is_refused`).
5. The frontend chooses a view only by `kind`: no module under `web/src/` holds a state, output or deliverable name, and a deliverable of a kind it does not know shows as a card with its name, kind, path and reason. Untested (P1 adds `web/src/views/registry.test.tsx`: `a view is chosen by kind only`, `an unknown kind falls back to a card`; the rest is for review).
6. A site or project file loads only when it passes every check: no unknown or repeated key, each value of its type, project IDs unique and of `[a-z0-9-]`, each tone one of section 4's, `root` an existing directory and `attempts` a pattern of plain and `*` components only; one that fails stops the start with the file and the key that is wrong. Untested (P1 adds `test_config.py`: `test_a_project_file_that_breaks_a_rule_is_refused`).
7. The record is served as the file holds it, never reshaped: `GET .../record` returns the JSON of `Attempt.to_json()` as written, and the frontend reads its types from design.md section 5, ["The record"](design.md#the-record), not from a schema of its own. Untested (P1 adds `test_app.py`: `test_the_record_is_the_file_as_written`).
8. This file has at most 200 lines; the same check as design.md's invariant 8 guards it.

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
root: /data/example/attempts
attempts: "*/*"        # directories under root; an attempt's ID is its path relative to root
record: record.json    # the record file, relative to the attempt's directory
states:                # optional: a state's title in the graph and the run list; else its name
  author: {title: Author}
statuses:              # an attempt status or a visit outcome: its label and tone
  passed: {label: Passed, tone: good}
  too_easy: {label: Too easy, tone: warn}
columns:               # the attempt table, after the ID
  - {title: Status, field: [status]}
  - {title: Cost, field: [budget, cost_usd], format: usd}
  - {title: Rounds, field: [round]}
session: {thinking: collapsed}   # collapsed (default), expanded or hidden
```

A tone is `good`, `bad`, `warn` or `neutral`; a word with no entry shows as itself, `neutral`. A column's `field` is a list of keys into the record, as a deliverable's `Value` is (design.md, "The record"), and `format` is `text` (default), `number`, `usd`, `duration` or `time`. `session.thinking` is how a session view first shows thinking blocks; a person can still open, collapse or hide them on the page.

### The API

Every route is a `GET` and returns JSON unless it says otherwise; an error is `{error, reason}` with 400, 404 or 413. An attempt is named by `?attempt=<id>`, its ID from the listing.

| Route | Returns |
| -- | -- |
| `/api/projects` | `[{id, name, color, states, statuses, columns, session}]`, each project file as loaded, without `root`, `attempts` and `record` |
| `/api/projects/<p>/attempts` | `[{id, modified, values, reason}]`, one per directory matching `attempts`, newest `modified` (the record file's mtime) first; `values` is each column's value, null where the record has no such key, and `reason` says why when the record is missing, not a regular file or not JSON (all values null) |
| `/api/projects/<p>/record?attempt=<id>` | the record file as written (invariant 7), at most `RECORD_LIMIT`, 64 MiB |
| `/api/projects/<p>/files?attempt=<id>&path=<rel>` | for a directory, `{entries: [{name, type, size}]}`, `type` one of `file`, `dir`, `symlink`, `other`, sorted by name; for a regular file, its bytes (invariant 3), at most `FILE_LIMIT`, 16 MiB, else 413; `path` is relative to the attempt's directory, as a record's paths are, and empty for the directory itself |

Everything else under `/api/` is 404; any other path serves a file of the static directory, or its `index.html` when there is none, so the frontend's routes load on reload.

### The frontend's routes

`/` opens the first project; `/<p>` is its attempt table; `/<p>/attempt?id=<id>` an attempt, with `&tab=` (`runs`, else a deliverable kind), `&run=<visit_index>` and `&file=<path>`; `/monitor` the Monitor entry. A link copied from the address bar opens the same view.

## 5. The pages

**Shell.** A fixed left sidebar, always shown, with the Projects menu (the projects of `/api/projects`, in the site file's order) and the Monitor entry; a breadcrumb at the top, `Projects › <name> › <attempt ID>`; a dark theme. Colours, type sizes and spacing are Tailwind theme tokens in one file, `web/src/ui/theme.css`.

**Attempt table.** One row per attempt, the ID first and then the project's columns, statuses drawn as tone-coloured labels; a row opens the attempt.

**Attempt page.** A row of tabs below the breadcrumb: Runs first, then one per deliverable `kind` in the order its first deliverable is declared, named by that deliverable's `title`, each with a count (Runs: the visits; `files`: the files under its path; `trials`: `value` of `k`, as `2 / 4`; `report`: the reports). Several deliverables of one kind: `report` stacks them in one tab, `files` and `trials` show one, chosen from a menu, the first by default. A deliverable with `path` null shows its `reason`.

- **Runs.** A second column beside the sidebar: Summary, then one entry per visit in the record's order, each with its state's title, outcome label, duration and cost. Summary has a top row (Started, Status, Total duration, Cost, Deliverables), the whole machine drawn from `graph` (transitions not taken dashed, taken ones solid with a count; a state opens its last visit), the `summary` of each action that has one, in order, under its state and visit, and a card per deliverable with one line on it that opens its tab. A run page (P4) is the visit's steps, folded: its input (the report it was handed, when any), each action (`kind`, status, cost, times, outputs; an agent's session in the session view), then its output (outcome, the next visit, a mark); only failed steps and the visit's first agent session start open.
- **`files`.** A file tree of the deliverable's path on the left, a read-only preview on the right: syntax highlighting by extension, a diff coloured by line, Markdown rendered (invariant 3), other files as text, or their size when they are not UTF-8 or over `FILE_LIMIT`. It opens `instruction.md` or `README.md` when either is at the top, else the first file.
- **`trials`.** A list of the deliverable's trials on the left, headed by `value` (for example `2 / 4 solved`) and the visit's outcome, each trial by its `id` and whether its `reward` is at least 1.0 (what the default `solved` accepts); on the right a row of reward, exception, duration, cost and trace format, and below it the trial's trace in the session view, or the trace's absence.
- **`report`.** Each report's Markdown, in declaration order, under its title. In a table, a column headed `Result` draws `pass` and `fail` as labels.

**Session view.** One column, for traces whose `format` is `claude-code-stream-json`, parsed in the browser from the raw file; another format shows as text. A sticky header with the totals (duration, cost, turns, tokens, model); a narrow gutter with the time since the start; a tool call as one line that opens to its input and output, opened from the start when it failed; more than 4 tool calls of one message folded into one line; subagent calls indented under their parent; thinking shown as `session.thinking` says, one line by default, with a toggle to hide all of it; the result badge taken from the trial's reward, not from the agent's own last word. A trace with no thinking renders as usual.

## 6. Phases and where each part lives

Each phase owns the directories named for it and touches no other's, so P2, P3 and P4 run at once once P1 is merged. P1 writes a placeholder in each later phase's entry module (the deliverable's or visit's name, path and reason), which that phase replaces. All of them develop and take screenshots against synthetic attempts in `web/fixtures/` and `python/tests/viz/fixtures/`, made by P1, which each phase may add to under a directory of its own name.

| Phase | Builds | Owns |
| -- | -- | -- |
| P1 | the backend, all of section 4 and invariants 1, 2, 4, 6, 7; the shell, routes, API client and record types; shared UI (Markdown, code view and highlighting, status labels, theme); the kind registry; the attempt table; Runs with its Summary | `python/src/dataforge/viz/`, `python/tests/viz/`, `web/` outside the directories below, `web/src/views/runs/summary/` |
| P2 | the `files` and `report` views | `web/src/views/files/`, `web/src/views/report/` |
| P3 | the session view and the `trials` view | `web/src/session/`, `web/src/views/trials/` |
| P4 | the run page | `web/src/views/runs/run/` |

P1's backend modules: `config.py` (site and project files, their checks), `paths.py` (opening an attempt and a path in it, invariant 2), `app.py` (the FastAPI app and its routes) and `serve.py` (the `dataforge viz serve` command). The frontend: `web/src/api/` (the client and the record's TypeScript types), `web/src/shell/`, `web/src/ui/`, `web/src/views/registry.tsx` (kind to view), `web/src/views/table/`. P4 renders an agent's session with P3's `SessionView` from `web/src/session/`; until P3 merges, the placeholder shows the trace's path. A change one phase needs in another's directory goes to that phase's owner, or after its merge, in a PR of its own.

The checks, besides the repo's Python checks with the `viz` extra installed: in `web/`, `npm ci`, then `npm run lint` (ESLint and Prettier), `npm run typecheck`, `npm test` (Vitest) and `npm run build`. The backend's dependencies, FastAPI and uvicorn, are the package's optional extra `viz`, so a dataset repo that only runs attempts does not install them.

## 7. Open questions

- How the built frontend reaches a machine: built there from a checkout of the pinned commit, or shipped with the package (which would be a release).
- The Monitor page: what it shows and where its data comes from.
- A run page's input: the record does not yet say which files a visit read, so P4 shows only the report it was handed.
