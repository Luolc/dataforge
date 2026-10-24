---
name: ui-audit
description: How to audit the viz (the web viewer of attempts) of a project that uses dataforge, and close what the audit finds. Use when asked to audit, walk through or check the viz for wrong or unclear displays, or to verify after a deploy that earlier findings are fixed.
---

# viz audit

How to walk the viz of a project that uses dataforge, record what is wrong, fix it in the right repository, and prove each fix. The obligations come from the rules in `AGENTS.md`; this file cites them by ID and does not restate them.

## 1. Pick what to walk

- List the attempts of the project by outcome (each terminal status the project's viz shows). Pick at least one attempt per outcome; prefer one that has several deliverables, one that has none, and the one with the most items.
- For each attempt, open every tab, and inside each tab every sub-panel and every folded group. Write the walk down as a list of (attempt, tab, panel) so that a skipped panel is visible.
- Only the data is read-only: read the project's data and change none of it, and touch no queue. Do not start, stop or change the project's running viz service; to look at the viz, start a test server of your own on a free port, never a shared one, and stop it by its recorded PID (section 4) (R335, R336).

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

One row per finding, in a table kept in the PR description or in a scratch directory outside the repository while you work:

| id | screenshot | where (attempt, tab, panel) | kind | why it is wrong | fix site | status |

- The screenshot shows the problem itself, named with the date.
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

Use `dataforge viz screenshot`, which drives headless Chrome over the DevTools protocol, so the shot is of the page after its scripts ran, in the theme the page itself applied:

```
dataforge viz screenshot --url <viewer address> --project <id> --attempt <id> \
  [--tab <name>] [--panel <name>] [--width <px>] [--height <px>] \
  --theme light|dark --out <file.png>
```

1. Start the test server of section 1 on a free port and record its PID. The tool starts its own Chrome with a new empty profile.
2. Take one shot per finding, and one per theme (`--theme light` and `--theme dark`) and per language when the finding differs between them (R244). Keep the window size the same (`--width`, `--height`) between a shot and the one that checks it (section 6).
3. `--tab` is a tab's name (default `runs`). `--panel` is the `part` of the tab, a panel of the column beside the tab's content; a tab with a single part has no such column and refuses `--panel`.
4. The tool waits until the page has loaded, shows no `Loading…` and marks the tab and panel as current, reads the theme back from the page, and exits non-zero without writing the file when the theme the page applied is not the one asked, when the tab or panel is not on the page, or when the page does not settle. A failure is a finding about the page or the command line, never a reason to take the shot by another route.
5. Stop the test server by the PID you recorded; never by name with `pkill` or `killall`, which would stop another agent's process. The tool stops its Chrome by PID itself and fails if a process of it is left; afterwards count what is left of your own PIDs and report the cleanup only when the count is zero (R67).
6. Use synthetic fixtures for dataforge's own tests, demos and screenshots (L48); never start a real Claude from a test (L25).

## 5. Fix and review

- A component fix goes in a dataforge pull request, a data fix in the project's repository, each with its own pull request; a change to this repository follows its rules for design, tests and frontend (R241, R243–R248, R89, L5).
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
