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

- A PR that is not a design pass leaves `docs/design.md` untouched. Check that its "Design impact" names the section or invariant the change really affects, with the guarding test, or says "none" only when nothing there changes.
