# dataforge design

The current design, edited in place. Why a decision was made lives in the PR that made it. Only an orchestra-triggered design pass edits this file; other PRs describe their effect on it under "Design impact".

## 1. What this is, and what it is not

dataforge is the shared part of a "data machine": a small multi-agent workshop that builds datasets with coding agents (today Claude Code, the only supported agent). Each dataset lives in its own repo. This repo holds what every dataset repo shares:

- the machine setup that turns a fresh Ubuntu 24.04 machine into a data machine;
- the files every agent on the machine gets (the base `AGENTS.md`; section 6);
- a Python package, `dataforge` in `python/`, that dataset repos install; it holds the state machine that runs an attempt at one data item, and the actions its states call (section 5). Planned, not built.

Starting agents, carrying messages between them, tracking who owes work and flagging stuck agents belong to Agent Fleet, a separate project. The setup installs its `fleet` binary and the tools it calls (fednet, atb, statute), each at its latest release (invariant 2); configuring and running them is the machine's own provisioning (section 4).

How a dataset repo works, which dataforge supports but does not enforce:

- Whoever writes a change does not merge it. A separate agent reviews before merge.
- A change to the dataset-level acceptance criteria passes a two-arm regression: the reference sample scores full marks and an empty output fails. Per-item check scripts ship with the data and go through normal review.
- Specs (what to build, what counts as acceptable) are edited only by people.
- Runtime state lives on the machine under `~/scratch/<repo>/` and is never committed: `progress/<job>.log`, an optional `board.md`, and `lessons.md`, where any agent writes suggestions that become PRs.

Not in scope:

- Dataset-specific code and dependencies; they stay in the dataset's repo. That includes the states of the state machine and what each does: its prompts, validators, checks, rubrics, how pass@k is judged, its caps and the text of each report.
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

```text
  dataset repo (planned, not built)
  ------------------------------------------------------------------
  pyproject.toml: dataforge @ git+https://github.com/Luolc/dataforge@<sha>#subdirectory=python
  its states (bodies, transitions, caps) -> dataforge.statemachine.run_attempt
    -> actions: agent (via an AgentRunner), container, script, fan_out
```

The setup in `machine/` runs once per machine and again for upgrades. The gateway shim is first on `PATH` in every zsh (invariant 5), so a `claude` started in a shell goes through it; the setup passes no token.

## 3. Invariants

Each one links to the test that guards it. Test files: [`machine/tests/`](../machine/tests/) (setup in a clean container), [`.github/scripts/`](../.github/scripts/).

1. The setup is idempotent: a second run reports `changed=0`, and so does a run in check mode. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "second run changed something", "check-mode run would change something".
2. Every third-party tool version is pinned in [`machine/versions.yml`](../machine/versions.yml) (Ubuntu archive packages follow the release). The tools the repository owner publishes, statute, fleet, fednet, atb and the gateway client (redcoast-client and the shim), are not pinned there: each setup run installs the latest non-draft, non-prerelease release of the tool, statute's being the newest `statute-v*` one of a repository that releases more than statute, and verifies it against the checksum file in that release; when the release has none, it installs nothing and fails the setup, naming the tool. A fednet that upgraded itself to its latest release is left as it is; any other is replaced. [`machine/tests/inside.sh`](../machine/tests/inside.sh): the per-tool version checks, "statute: run reports …", "a release without its checksum file was accepted", "fednet replaced".
3. A gateway client download that does not match its release's `SHA256SUMS` installs nothing, and the installed version stays in use; a failed replacement puts the old copy back. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "tampered download was accepted", "current moved after a failed replacement".
4. The setup merges its Claude Code settings into the existing files and keeps keys it does not own, including the gateway's auth and proxy settings. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "an existing setting was dropped".
5. The gateway shim is first on `PATH` in every zsh, interactive or not, as long as `zshenv.local` (section 4), which is sourced after the setup's `PATH`, puts nothing in front of it. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "in a non-interactive zsh, claude is …, not the shim".
6. The setup completes on a machine with none of its settings in place (section 4): it finds every release without a GitHub token, installs every tool and skips the gh login, the git identity and the gateway client's config. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "run without machine settings".
7. Every zsh sources `zshenv.local` when it is readable, and the setup never writes it. [`machine/tests/inside.sh`](../machine/tests/inside.sh): "zshenv.local not sourced", "the setup changed zshenv.local".
8. This file has at most 200 lines. [`design-length.test.sh`](../.github/scripts/design-length.test.sh).

The state machine's invariants (section 5) are untested until its tests exist in `python/tests/test_statemachine.py`:

9. The engine moves only as the current state's transitions say, and an outcome a state has no transition for raises `ValueError` naming the state and the outcome. Untested.
10. An attempt ends with the status the table of section 5, "Ending an attempt", gives for how it ended, and never enters a state more than its `max_visits` times. Untested.
11. An agent action with a validator resumes its session with the validator's output at most `max_fixes` times, and fails when the validator still fails after them. Untested.
12. An exception from a body, an action, `guard` or `on_step` reaches the caller unchanged, after the visit is recorded with outcome `raised`. Untested.
13. The package imports nothing outside the standard library. Untested.

## 4. Interfaces

- Setting up a data machine: [`machine/README.md`](../machine/README.md); settings in [`machine/playbook.yml`](../machine/playbook.yml), versions in [`machine/versions.yml`](../machine/versions.yml).
- The machine's own settings, which the setup reads and never writes: the GitHub token in `~/.config/dataforge/github-token`, the git identity and gateway address in `~/.config/dataforge/setup.yml`, and private environment variables in `~/.config/dataforge/zshenv.local`.
- Install paths that other setups may rely on: `/usr/local/bin/<name>` for `fleet`, `fednet`, `atb`, `statute`, `herdr` and `gh`; `/usr/bin/<name>` for `tailscale`, `op` and `direnv`.
- The state machine, planned, not built: `dataforge.statemachine` exports `run_attempt`, `State`, `Outcome`, `Goto`, `End`, `Report`, `Attempt`, the specs `AgentSpec`, `ContainerSpec` and `ScriptSpec`, `ActionResult`, the protocols `AgentRunner` (with its result `AgentRun`) and `ContainerRunner`, and `DockerRunner` (section 5). A dataset repo installs it from git, pinned to a commit of main whose CI run for the push succeeded on its first attempt; upgrading is changing that SHA. There is no tag and no package index release.

## 5. The state machine

Planned, not built: `python/` does not exist yet. It replaces nothing in this repo.

An attempt makes one data item. It is a sequence of visits to states, starting at the first state `run_attempt` is given. A dataset writes each state as `State(name, body, transitions, max_visits)`:

- `body(ctx) -> Outcome(name, data, last=False)` does the state's work by calling the actions below; `data` is a dict that converts to JSON.
- `transitions` maps each outcome name to `Goto(state, report=True, mark=None)` or `End(status)`. Each state chooses where to go; the engine knows no state's name, and this repo holds no dataset's table.
- `max_visits` caps how many times the state is entered in one attempt.

A `Goto` with `report=True` hands the target state `ctx.report`, a `Report(from_state, outcome, data, artifacts)`; the dataset renders its text. A `mark` (for example `too_easy`) records that the item as this visit left it may be kept if the attempt ends without passing. `last=True` says the state is not to be entered again.

### Actions

Each action takes named input artifacts, writes named output artifacts and returns `ActionResult(status, outputs, log)`. A body calls them on its context:

- `ctx.agent(AgentSpec)` runs a headless coding agent: `prompt`, `workdir`, `inputs`, `outputs`, an optional `container` (image, mounts, network), `tools`, an optional `output_schema`, and an optional `validator` with `max_fixes`. The prompt tells the agent about the validator and how to run it, and the engine runs it again after the agent ends (invariant 11). A rubric judge is an agent with no tools and an `output_schema`.
- `ctx.container(ContainerSpec)` runs one command in a container through the `ContainerRunner` given to `run_attempt`. `DockerRunner`, which uses `docker run`, is the default; a dataset that runs something else (Harbor's nop and oracle, say) implements the same protocol.
- `ctx.script(ScriptSpec)` runs a script on the host with its inputs as paths, passing when it exits 0. A validator is a `ScriptSpec`, run on the agent step's output directory.
- `ctx.fan_out(make, k, concurrency)` runs `make(i)` for `i` in `0..k-1`, one at a time when `concurrency` is 1, all at once when it is `k`, otherwise in a pool of that size, and returns the results in order. pass@k is a `fan_out` of agents or containers, and the body turns the count of passes into its outcome.

Agents run through the `AgentRunner` protocol given to `run_attempt`: `start(spec)` and `resume(session, prompt)`, each returning `AgentRun(session, status, cost_usd, trace)`. The engine depends on nothing else about the agent; an implementation for each agent tool is the dataset's until this package has one.

### Artifacts

Each attempt has a directory, the `root` given to `run_attempt`. An action's outputs are stored under `artifacts/<state>/<visit>/<name>`. `ctx.artifacts.get(name)` returns the latest version of an artifact and `ctx.artifacts.history(name)` every version, oldest first. An action's inputs name artifacts or paths; agents, containers and scripts get them read-only.

### Ending an attempt

| Status | When |
| -- | -- |
| the `End` status | a transition reached `End(status)`, for example `passed` |
| the last mark | a state's visits were exceeded, a state was entered again after `last=True`, or `guard` blocked a state, and a transition taken before had a mark; `kept_visit` names the visit that transition left |
| `error` | a state's visits were exceeded or it was entered again after `last=True`, with no mark before; `reason` names the state and the visit |
| `budget` | `guard` blocked a state with no mark before; `reason` holds the guard's reason |

`guard(ctx, state) -> str | None` is optional and runs before each visit; a reason blocks the visit. An exception is never turned into a status (invariant 12).

### The record

`run_attempt` returns an `Attempt`; `to_json()` gives `{status, reason, kept_visit, final, visits}`. Each visit is `{state, visit, outcome, data, actions: [{kind, status, outputs, started_at, ended_at}]}`, times in UTC ISO 8601. `final` is, for each state, its last visit up to the end, or up to `kept_visit` when the item is kept: the last result of every state that judged the item, pass@k's included. The engine writes no file of its own: after each visit it calls the optional `on_step(attempt)`, and the dataset decides where the record goes. Attempts in one process may run concurrently.

### What a dataset gives

Its states, with their bodies, transitions, marks and caps; an `AgentRunner`; a `ContainerRunner` when `docker run` does not fit; and optionally `guard` and `on_step`.

### Open questions

- The interface above is a proposal the repository owner has not decided yet.

## 6. Known issues and next steps

- No real data machine exists yet. Waiting on: the GitHub account the data machine commits and opens PRs with (a dedicated bot account is recommended), authorized per dataset repo; the gateway machine credential and that account's token, placed by the user. Then check on that machine what no test covers: the `gh` login, the gateway client release download, and that `git` is there before the setup runs (the README clones with it first).
- Planned, not built: sourcing `zshenv.local` before the setup's `PATH` instead of after it, so that invariant 5 no longer depends on that file. It will replace the current order in `machine/files/zsh/zshenv`.
- The state machine has no user yet; a dataset repo is the first to adopt it.
- The base `AGENTS.md` for data machines is not written yet; the setup installs `machine/agent/AGENTS.md` once it exists.
- The personal zsh layer is a cleaned copy of the user's own configuration and can drift from it.
- Claude Code is the only supported agent. Supporting others needs changes in the setup and in the agent runtime.
