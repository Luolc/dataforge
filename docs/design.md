# dataforge design

The current design, edited in place. Why a decision was made lives in the PR that made it. Only an orchestra-triggered design pass edits this file; other PRs describe their effect on it under "Design impact".

## 1. What this is, and what it is not

dataforge is the shared part of a "data machine": a small multi-agent workshop that builds datasets with coding agents (today Claude Code, the only supported agent). Each dataset lives in its own repo. This repo holds what every dataset repo shares:

- the machine setup that turns a fresh Ubuntu 24.04 machine into a data machine;
- the files every agent on the machine gets (the base `AGENTS.md`; section 5).

Starting agents, carrying messages between them, tracking who owes work and flagging stuck agents belong to Agent Fleet, a separate project. The setup installs its `fleet` binary and the tools it calls (fednet, atb, statute), pinned like every other tool for now (planned: tools the repository owner publishes will instead be installed at their latest release, invariant 2); configuring and running them is the machine's own provisioning (section 4).

How a dataset repo works, which dataforge supports but does not enforce:

- Whoever writes a change does not merge it. A separate agent reviews before merge.
- A change to the dataset-level acceptance criteria passes a two-arm regression: the reference sample scores full marks and an empty output fails. Per-item check scripts ship with the data and go through normal review.
- Specs (what to build, what counts as acceptable) are edited only by people.
- Runtime state lives on the machine under `~/scratch/<repo>/` and is never committed: `progress/<job>.log`, an optional `board.md`, and `lessons.md`, where any agent writes suggestions that become PRs.

Not in scope:

- Dataset-specific code and dependencies; they stay in the dataset's repo.
- Provisioning the machine itself (creating it, the network, the first user and SSH key), and every credential: the user or the machine's own provisioning places them.
- Joining a tailnet, signing in to any account, and the config and services of fleet and fednet: the setup installs those programs and nothing more.
- Logging the machine into model accounts. Model traffic goes through redcoast, an account gateway developed as a separate project; the machine holds no model login.
- Merging PRs, cross-machine messaging, and model or effort policy.

## 2. Parts and how they connect

```text
  data machine
  ------------------------------------------------------------------
  machine/bootstrap.sh -> Ansible (machine/playbook.yml)
    installs uv, Docker, zsh, gh, herdr, Claude Code, gateway client,
    Tailscale, op, direnv, fleet, fednet, atb, statute

  redcoast GitHub Release  ------>  gateway client (shim `claude` + redcoast-client)

  every `claude` in a pane -> shim -> redcoast-client -> redcoast -> model
```

The setup in `machine/` runs once per machine and again for upgrades. The gateway shim is first on `PATH` in every zsh (invariant 5), so a `claude` started in a shell goes through it; the setup passes no token.

## 3. Invariants

Each one links to the test that guards it. Test files: [`machine/tests/`](../machine/tests/) (setup in a clean container), [`.github/scripts/`](../.github/scripts/).

1. The setup is idempotent: a second run reports `changed=0`. [`machine/tests/inside.sh`](../machine/tests/inside.sh).
2. Every third-party tool version is pinned in [`machine/versions.yml`](../machine/versions.yml) (Ubuntu archive packages follow the release). fednet upgrades itself, so its pin is only the version installed where none is yet; a rerun leaves an upgraded fednet alone. Planned, not built yet: tools the repository owner publishes are not pinned there; each setup run installs the latest non-draft, non-prerelease release of the tool and verifies it against the checksum file in that release, and when the release has none installs nothing and fails the setup, naming the tool. The tools are statute, fleet, fednet, atb, limae and the gateway client (redcoast-client and the shim). Untested until then. [`machine/tests/inside.sh`](../machine/tests/inside.sh): the per-tool version checks, "the setup replaced an upgraded fednet".
3. A gateway client download that does not match its pinned sha256 installs nothing, and the installed version stays in use; a failed replacement puts the old copy back. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "tampered download was accepted", "current moved after a failed replacement".
4. The setup merges its Claude Code settings into the existing files and keeps keys it does not own, including the gateway's auth and proxy settings. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "an existing setting was dropped".
5. The gateway shim is first on `PATH` in every zsh, interactive or not, as long as `zshenv.local` (section 4), which is sourced after the setup's `PATH`, puts nothing in front of it. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "in a non-interactive zsh, claude is …, not the shim".
6. The setup completes on a machine with none of its settings in place (section 4): it installs every tool and skips the gh login, the git identity and the gateway client's config. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "run without machine settings".
7. Every zsh sources `zshenv.local` when it is readable, and the setup never writes it. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "zshenv.local not sourced", "the setup changed zshenv.local".
8. This file has at most 200 lines. [`design-length.test.sh`](../.github/scripts/design-length.test.sh).

## 4. Interfaces

- Setting up a data machine: [`machine/README.md`](../machine/README.md); settings in [`machine/playbook.yml`](../machine/playbook.yml), versions in [`machine/versions.yml`](../machine/versions.yml).
- The machine's own settings, which the setup reads and never writes: the GitHub token in `~/.config/dataforge/github-token`, the git identity and gateway address in `~/.config/dataforge/setup.yml`, and private environment variables in `~/.config/dataforge/zshenv.local`.
- Install paths that other setups may rely on: `/usr/local/bin/<name>` for `fleet`, `fednet`, `atb`, `statute`, `herdr` and `gh`; `/usr/bin/<name>` for `tailscale`, `op` and `direnv`.

## 5. Known issues and next steps

- No real data machine exists yet. Waiting on: the GitHub account the data machine commits and opens PRs with (a dedicated bot account is recommended), authorized per dataset repo; the gateway machine credential and that account's token, placed by the user. Then check on that machine what no test covers: the `gh` login, the gateway client release download, and that `git` is there before the setup runs (the README clones with it first).
- Planned, not built: sourcing `zshenv.local` before the setup's `PATH` instead of after it, so that invariant 5 no longer depends on that file. It will replace the current order in `machine/files/zsh/zshenv`.
- The base `AGENTS.md` for data machines is not written yet; the setup installs `machine/agent/AGENTS.md` once it exists.
- The personal zsh layer is a cleaned copy of the user's own configuration and can drift from it.
- Claude Code is the only supported agent. Supporting others needs changes in the setup and in the agent runtime.
