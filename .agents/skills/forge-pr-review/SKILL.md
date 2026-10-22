---
name: forge-pr-review
description: Review checks specific to dataforge. Use when reviewing any PR in this repo.
---

# forge PR review

The review checks specific to this repo, on top of general review practice.

## Public content

- The repo is public (L1 in `AGENTS.md`): review every PR for what a public repository may not hold.
- Role names used in this repo: the dev machine, the data machine, the gateway host.
- gitleaks scans for credentials in the commit hook and in CI; nothing mechanical scans for private names, hosts or repositories, so read for them.

## Design doc

- A PR that is not a design pass leaves `docs/design.md`, `docs/design-viz.md` and `docs/design-share.md` untouched. Check that its "Design impact" names the section or invariant of any of them the change really affects, with the guarding test, or says "none" only when nothing there changes.

## Shared formats

- A format in `python/src/dataforge/formats/builtin.py` is what the processes of two installs share on one machine. A PR that changes what one of them means, so that the code of the version before would read or write it wrongly beside the new code, raises its version there, even when its snapshot under `python/tests/formats/` stays the same (a change of the snapshot already fails its tests until the version is raised): for example a new key in a job's `labels` that the old code acts on, a new value of a string field with no CHECK, such as a record's `status`, that the old code reads, a time in another clock or unit, or a new signal or exit status between the scheduler and a job's wrapper. A PR that leaves the version as it is while doing so is a finding.
- A PR that adds something two installs share on one machine (a table, a file or directory in the state directory, a variable or argument the scheduler gives a job's wrapper) and declares no format for it in `builtin.py`, or covers it by none there, is a finding.
