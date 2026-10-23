---
name: ui-audit
description: How to audit the viz (the web viewer of attempts) of a project that uses dataforge, and close what the audit finds. Use when asked to audit, walk through or check the viz for wrong or unclear displays, or to verify after a deploy that earlier findings are fixed.
---

# viz audit

How to walk the viz of a project that uses dataforge, record what is wrong, fix it in the right repository, and prove each fix. The obligations come from the rules in `AGENTS.md`; this file cites them by ID and does not restate them.

## 1. Pick what to walk

- List the attempts of the project by outcome (each terminal status the project's viz shows). Pick at least one attempt per outcome; prefer one that has several deliverables, one that has none, and the one with the most items (L47).
- For each attempt, open every tab, and inside each tab every sub-panel and every folded group. Write the walk down as a list of (attempt, tab, panel) so that a skipped panel is visible.
- Use the viz of a test server or the project's running one, read-only. Start no server on a shared port, and touch no queue, no viz service and no data of the project (R335, R336).

## 2. Look for five kinds of problem

| Kind | What it looks like |
|---|---|
| `missing-no-reason` | Data is absent and the page does not say why (not run, not produced, unreadable). A state never reached shows as not run (L40). |
| `mismatch` | A number disagrees with another place: a header count against the rows below, a cost or duration against the detail, a total against its parts. |
| `label` | A label shows an internal name, a raw status word, or reads so that a person takes it to mean something else (L37, L38, L39). |
| `broken-link` | A link, a file reference or a trace jump leads nowhere or to the wrong place. |
| `vague-empty` | An empty state does not say whether nothing exists, nothing matched the filter (L45), or loading failed. |

Click every link; compare each number with a second place that shows the same quantity.

## 3. Record each finding

One row per finding, in a table kept in the PR description or in `$FLEET_SCRATCH` while you work (R217):

| id | screenshot | where (attempt, tab, panel) | kind | why it is wrong | fix site | status |

- The screenshot shows the problem itself, named with the date (R217).
- `fix site`: a problem of the component or its logic is fixed in dataforge; a problem of the data (a missing field, a wrong status written, a wrong label in the project's configuration) is fixed in the project's repository. When it is unclear, read the DOM source of the value: if the page rendered what it was given, it is data.
- Status words, and only these:
  - `fixed`: fixed in code and checked after the switch (section 6).
  - `fixed-new-only`: the code is fixed, but data written before the fix stays as it was; say which data.
  - `deferred`: not fixed now; give who takes it and the trigger (R92).
  - `wontfix-user`: the project's owner decides to leave it; point to where they said so.
  - `no-bug`: on a closer look the display is right; say what you checked.
  - `by-design`: the display follows a design point; link the heading in the design file.
  - `pending-data`: the display is right for the data that exists, and will change when the data arrives; say which data.

## 4. Take screenshots

Headless Chrome through the DevTools protocol, so the shot is of the page after its scripts ran:

1. Start Chrome with `--headless --remote-debugging-port=<free port> --user-data-dir=<new empty dir>` and a fixed window size. Record the PID you started.
2. Connect to the port, open the target URL, wait until the panel's content is in the DOM (not a fixed sleep), then call `Page.captureScreenshot`. Scroll or set the clip to the problem's panel.
3. Take one shot per finding, and one per theme and language when the finding differs between them (R244).
4. Stop Chrome, and any test server you started, by the PID you recorded; never by name with `pkill` or `killall`, which would stop another agent's process. Afterwards count what is left of your PIDs and report the cleanup only when the count is zero (R67).
5. Use synthetic fixtures for dataforge's own tests, demos and screenshots (L48); never start a real Claude from a test (L25).

## 5. Fix and review

- A component fix goes in a dataforge pull request, a data fix in the project's repository, each with its own pull request; a change to this repository follows its rules for design, tests and frontend (R241–R248, R89, L5).
- Attach the before and after screenshots to the pull request (R244, L47). Where the PR is on a public repository, run the pre-push review on the diff, the commit messages and the PR text, with the screenshots' content included (R52), push exactly the head that passed, and only then post screenshots in comments.
- Another agent reviews the pull request; the implementer never merges it (R62).
- A fix counts only if the screenshot shows it. When the change cannot be seen (a value in an attribute, a link target), read the DOM and quote the node you read; do not claim `fixed` from the diff.

## 6. Check after the switch

After the fix is live (the deploy or the switch the project uses):

1. Return to the same (attempt, tab, panel) of each finding and take the screenshot again, with the same window size.
2. Judge old data and new code apart: a page showing data written before the fix may still look wrong because of the data. Check a newly produced attempt too; if only it is right, the status is `fixed-new-only`, not `fixed`.
3. Put the result in a comparison table:

| id | before | after | status | note |
|---|---|---|---|---|

Every row ends either `fixed` with its after-screenshot, or with another status word, its reason and who takes it (R92). No row stays open without one.
