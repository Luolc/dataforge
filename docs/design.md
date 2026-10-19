# dataforge design

The current design, edited in place. Why a decision was made lives in the PR that made it. Only an orchestra-triggered design pass edits this file; other PRs describe their effect on it under "Design impact".

## 1. What this is, and what it is not

dataforge is the shared part of a "data machine": a small multi-agent workshop that builds datasets with coding agents (today Claude Code, the only supported agent). Each dataset lives in its own repo. This repo holds what every dataset repo shares:

- the machine setup that turns a fresh Ubuntu 24.04 machine into a data machine;
- the files every agent on the machine gets (the base `AGENTS.md`; section 6);
- a Python package, `dataforge` in `python/`, that dataset repos install; its module `dataforge.curation` holds the state machine that runs an attempt at one data item, the states and actions it ships, and a Harbor trial runner (section 5). Planned, not built.

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
  its State subclasses (transitions, caps) -> dataforge.curation.Machine.run
    -> Actions: agent, script (on Host or Docker), trial (HarborRunner); fan_out
    -> ResourcePool (memory reservations shared by every attempt)
  gateway, if any: wired in through the Docker and HarborRunner hooks
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

The state machine's invariants (section 5) are untested until its tests exist under `python/tests/`; each names the test planned to guard it:

9. The machine moves only as the current state's transitions say; an outcome with no transition raises `ValueError` naming the state and the outcome. Untested (`test_an_outcome_without_a_transition_is_an_error`).
10. An attempt ends with the status the table "Ending an attempt" (section 5) gives, and never enters a state more than its `max_visits` times. Untested (`test_cap_without_a_mark_fails`, `test_cap_after_a_mark_keeps_the_marked_visit`, `test_guard_blocks_with_budget`, `test_last_outcome_ends_on_reentry`).
11. The validator binds the agent: the host runs its own copy on the outputs, resumes the session with its output at most `max_fixes` times, and fails the action when it still fails. Untested (`test_validator_failure_resumes_the_agent_with_its_output`, `test_validator_failing_past_max_fixes_returns_to_author`).
12. An exception from a state's `run` or an action it calls reaches the caller unchanged, after the visit is recorded with outcome `raised`; one from `guard` or `on_step` reaches it unchanged and adds nothing to the record. Untested (`test_a_raising_body_is_recorded_and_raised`, `test_a_raising_guard_or_on_step_adds_nothing_to_the_record`).
13. The pool never lets its reservations exceed `memory_gb`, or more than `max_running` of them run at once; a request waits until it fits, and a reservation is returned when its start fails. Untested (`test_pool_waits_until_memory_is_returned`, `test_pool_max_running_caps_the_reservations_held`).
14. `PassAtKState` runs k trials and returns `all_solved`, `none_solved` or `in_range` by how many `solved` accepts, a trial with an exception counting as unsolved. Untested.

## 4. Interfaces

- Setting up a data machine: [`machine/README.md`](../machine/README.md); settings in [`machine/playbook.yml`](../machine/playbook.yml), versions in [`machine/versions.yml`](../machine/versions.yml).
- The machine's own settings, which the setup reads and never writes: the GitHub token in `~/.config/dataforge/github-token`, the git identity and gateway address in `~/.config/dataforge/setup.yml`, and private environment variables in `~/.config/dataforge/zshenv.local`.
- Install paths that other setups may rely on: `/usr/local/bin/<name>` for `fleet`, `fednet`, `atb`, `statute`, `herdr` and `gh`; `/usr/bin/<name>` for `tailscale`, `op` and `direnv`.
- The state machine, planned, not built: `dataforge.curation` exports `Machine`, `State`, `Outcome`, `Goto`, `End`, `Report`, `Context`, `Attempt` and `Visit`; the states `AgentState`, `ScriptState`, `TrialState` and `PassAtKState`; `Actions` and `fan_out`; the environments `Host` and `Docker` with `Mount`; `AgentSpec`, `AgentTask`, `ScriptSpec`, `Limits` and `ActionResult`; the protocols `AgentRunner` (returning `AgentRun`) and `TrialRunner` (returning `TrialResult`), with `HarborRunner` implementing the latter; and `ResourcePool` (section 5). A dataset repo installs it from git, pinned to a commit of main whose CI run for the push succeeded on its first attempt; upgrading is changing that SHA. There is no tag and no package index release. Each runtime dependency is added with its reason in the PR that adds it.

## 5. The state machine

Planned, not built: `python/` does not exist yet. It replaces nothing in this repo.

An attempt makes one data item. It is a sequence of visits to states, starting at the first state given to `Machine(states, *, guard=None, on_step=None)`; `machine.run(root)` first checks that state names are unique, every `Goto` target exists and every `max_visits` is at least 1. A state is a subclass of `State(name, transitions, max_visits=1, on_exhausted="failed")`:

- `run(ctx) -> Outcome(name, data, last=False)` does the state's work, usually through the actions below; `data` is a dict that converts to JSON.
- `transitions` maps each outcome name to `Goto(state, report=True, mark=None)` or `End(status)`. Each state chooses where to go; the machine knows no state's name, and this repo holds no dataset's table.
- `max_visits` caps how many times the state is entered in one attempt; `on_exhausted` is the status when that cap ends it with no mark.

A `Goto` with `report=True` hands the target state `ctx.report`, a `Report(from_state, outcome, data, artifacts)`; the dataset renders its text. A `mark` (for example `too_easy`) records that the item as this visit left it may be kept if the attempt ends without passing. `last=True` says the state is not to be entered again. A dataset may subclass `Machine` too: `guard(ctx, state)` and `on_step(attempt)` are its methods, which by default call the callables it was given.

### The states it ships

Each takes the bound `Actions` and maps its result to an outcome through `outcome(ctx, result)`, which a subclass may override, as it may each method that reads `ctx`:

| State | Runs | Default outcomes |
| -- | -- | -- |
| `AgentState(..., actions, env=HOST)` | `actions.agent` on `spec(ctx)` | `ok` (the agent ended well and the validator, if any, passes), `failed` |
| `ScriptState(..., actions, env=HOST)` | `actions.script` on `spec(ctx)` | `ok`, `failed` |
| `TrialState(..., actions, task_dir, agent, limits)` | one `actions.trial` | `solved`, `unsolved`, `error` (the trial raised) |
| `PassAtKState(..., actions, task_dir, agent, limits, k)` | k trials, `trial-0` to `trial-<k-1>`, through `fan_out` | `all_solved`, `none_solved`, `in_range` (invariant 14), with data `{solved, errors, k}` |

`solved(result)` accepts a trial without an exception whose `reward` is at least 1.0; a subclass may change it. Whether trials that raised should count otherwise is the dataset's choice, through `outcome`.

### Actions

The machine (`dataforge.curation.engine`) knows no action; the actions (`dataforge.curation.actions`) reach it only through the context's `claim`, `resolve` and `record`, so a dataset may also call them from its own `run`. A dataset binds them once, `actions = Actions(agent_runner, trial_runner, pool)`; they write their outputs to the visit's directory `ctx.outdir`, whose names are unique within the visit.

What runs and where it runs are apart. An environment is `Host()` or `Docker(image, mounts, cpus, cpu_shares, memory_gb, pids, network, labels, security, env, pre_start)`. A `Docker` environment is a container the dataset opens with `with Docker(...) as env:`; it is created on first use, started for the actions in it and stopped when none runs, so it keeps its files across visits, and removed when the `with` block exits. Inputs, outputs and the validator are copied in and out of it. `agent` and `script` return `ActionResult(status, outputs, log, value, cost_usd)`:

- `actions.agent(ctx, spec, env)` runs an `AgentSpec`, a headless coding agent: `prompt`, `workdir`, `inputs`, `outputs`, `tools`, an optional `output_schema` (whose parsed reply is `value`) and an optional `validator` with `max_fixes`. The agent gets a read-only copy of the validator at a fixed path, `/dataforge/validator/<name>` in a `Docker` environment and `validators/<state>/<visit>/<action>/<name>` under the attempt's root on the host, and the prompt tells it to make it pass; the host then reruns its own original on the outputs (invariant 11). A rubric judge is an agent with no tools and an `output_schema`.
- `actions.script(ctx, spec, env)` runs a `ScriptSpec`, a command with its inputs as paths, and passes when it exits 0. A script on the host that declares `memory_gb` reserves it from the pool, for heavy host work such as an image build. A validator is an executable that takes the output directory as its one argument.
- `actions.trial(ctx, name, task_dir, agent, limits)` runs one trial of a task: it starts the task's environment within `Limits(memory_gb, cpus)`, runs the agent (`nop`, `oracle` or an agent's configuration) and its verifier, and returns `TrialResult(rewards, exception, cost_usd, seconds, artifacts)`; its files become the artifact `name`.
- `fan_out(calls)` runs each zero-argument call at once, each in its own thread, and returns their results in the order given.

Agents run through the dataset's `AgentRunner`: `start(spec, task, env)`, where `task` is `AgentTask(prompt, inputs, outdir, validator)` with the prompt as the actions extend it and paths as `env` sees them, and `resume(session, prompt)` return `AgentRun(session, status, cost_usd, trace, result)`, and an optional `close(session)` runs after the action. The session is the runner's own object. This package holds no agent runner yet. Trials run through a `TrialRunner`, `run(task_dir, agent, limits, outdir)`, and a dataset may give its own. `HarborRunner(harbor, ...)` implements it by running the Harbor CLI at the path it is given, so the package does not depend on Harbor; it labels each trial's containers, networks and images with `dataforge.trial=<id>` and removes them all after the run.

### The gateway

Neither `Docker` nor `HarborRunner` knows the model gateway: its client and credentials differ from machine to machine, and the machine setup already puts the gateway shim on `PATH` (invariant 5). A dataset wires it in through their hooks: extra `mounts`; `env` variables, which `HarborRunner` sets both in the task's container and for the Harbor process; a host-side `pre_start` called before each start, with the environment for `Docker` and the trial's output directory for `HarborRunner`; and for `HarborRunner`, `compose_overlay` files applied after its own.

### Scheduling

A `ResourcePool(memory_gb, max_running=None)`, one per process, is shared by every attempt and action in it, attempts running in threads and a visit's actions running concurrently. Before a `Docker` environment starts, a trial runs or a host script with `memory_gb` runs, it reserves its memory limit, waiting while the pool lacks it, and returns it when it stops or fails to start (invariant 13). Each action holds one reservation at most (a script in a `Docker` environment runs under the environment's), so no two wait on each other. CPUs are shared by each container's `cpus` cap and `cpu_shares` weight, not reserved. Creating a pool larger than the machine's `MemAvailable` raises `ValueError`, and so does a reservation larger than the pool.

### Artifacts

Each attempt has a directory, the `root` the dataset gives. An action's outputs are stored under `artifacts/<state>/<visit>/<name>`. `ctx.artifacts.get(name)` returns the latest version of an artifact and `ctx.artifacts.history(name)` every version, oldest first. An action's inputs name artifacts or give paths; actions get them read-only.

### Ending an attempt

| Status | When |
| -- | -- |
| the `End` status | a transition reached `End(status)`, for example `passed` |
| the last mark | a state's visits were exceeded, it was entered again after `last=True`, or `guard` blocked it, and a `Goto` taken before had a mark; `kept_visit` names the visit that `Goto` left |
| the state's `on_exhausted` | its visits were exceeded or it was entered again after `last=True`, with no mark before |
| `budget` | `guard` blocked a state with no mark before |

`guard(ctx, state) -> str | None` runs before each visit; a reason blocks the visit. In the last three rows `reason` names the state, the visit and the cause. An exception is never turned into a status (invariant 12).

### The record

`run` returns an `Attempt`; `to_json()` gives `{status, reason, kept_visit, final, visits}`. Each visit is `{state, visit, outcome, data, mark, actions: [{kind, status, outputs, cost_usd, started_at, ended_at}]}`, times in UTC ISO 8601. `final` maps each state to its last visit up to the end, or up to `kept_visit` when the item is kept, pass@k's included. The machine writes no record file: after each visit it calls `on_step(attempt)`, and the dataset decides where the record goes.

### What a dataset gives

Its states, as subclasses of `State` or of the states above, with transitions, marks and caps; the attempt's root; its `Docker` environments; `Actions` bound to an `AgentRunner` when its states use agents, a `TrialRunner` (`HarborRunner` or its own) when they use trials, and the pool; the gateway wiring, if it needs one; and optionally `guard` and `on_step`.

### Open questions

- The module's name: `dataforge.curation` stands until the repository owner picks one.

## 6. Known issues and next steps

- No real data machine exists yet. Waiting on: the GitHub account the data machine commits and opens PRs with (a dedicated bot account is recommended), authorized per dataset repo; the gateway machine credential and that account's token, placed by the user. Then check on that machine what no test covers: the `gh` login, the gateway client release download, and that `git` is there before the setup runs (the README clones with it first).
- Planned, not built: sourcing `zshenv.local` before the setup's `PATH` instead of after it, so that invariant 5 no longer depends on that file. It will replace the current order in `machine/files/zsh/zshenv`.
- The state machine has no user yet; a dataset repo is the first to adopt it.
- The base `AGENTS.md` for data machines is not written yet; the setup installs `machine/agent/AGENTS.md` once it exists.
- The personal zsh layer is a cleaned copy of the user's own configuration and can drift from it.
- Claude Code is the only supported agent. Supporting others needs changes in the setup and in the agent runtime.
