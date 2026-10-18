# Setting up a new data machine

From a freshly provisioned machine to one that is set up for coding agents. What the setup installs and how it fits with the rest is in [`docs/design.md`](../docs/design.md); this page is only the manual steps.

Before you start, the machine exists with Ubuntu 24.04 and an agent user with passwordless sudo, set up by the machine's provisioning (outside this repo; see "What this is, and what it is not" in [`docs/design.md`](../docs/design.md)). Every step below runs on the data machine as the agent user unless it says otherwise.

The setup runs twice: once right away, to install every tool, and again once the machine's own settings (step 3) are in place. The settings can be written by hand, as below, or by the machine's own provisioning; the setup only reads them.

## 1. Clone this repo

This repo is public, so the clone needs no token. Replace `<owner>/<repo>` with this repo's coordinates:

```sh
git clone https://github.com/<owner>/<repo> ~/dev/dataforge
```

Done when `git -C ~/dev/dataforge log --oneline -1` prints a commit.

## 2. Install the tools

```sh
~/dev/dataforge/machine/bootstrap.sh
```

This installs every tool pinned in `machine/versions.yml`: Docker, gh, herdr, Claude Code and the gateway client, Tailscale, the 1Password CLI `op`, direnv, Agent Fleet (`fleet`), fednet, atb and statute. Programs that other setups refer to by absolute path are at `/usr/local/bin/<name>` (`fleet`, `fednet`, `atb`, `statute`, `herdr`, `gh`) and `/usr/bin/<name>` (`tailscale`, `op`, `direnv`). The setup only installs them: it joins no tailnet, signs in to no account, and writes no config for fleet or fednet or starts any service of theirs.

With no settings yet, the run skips the git identity and the gh login, and leaves the gateway client without its config; it says so in its output.

Done when the line under `PLAY RECAP` shows `failed=0`.

## 3. Put the machine's settings in place

Only the user, or the machine's provisioning, does this step. Each file is optional to the setup, which skips what is missing.

### The GitHub token

The data machine makes its commits and opens its PRs as one GitHub account, through that account's token. A dedicated bot account is recommended, so that the agents' commits are told apart from a person's.

```sh
mkdir -p -m 700 ~/.config/dataforge && install -m 600 /dev/null ~/.config/dataforge/github-token
```

Then paste that account's token into `~/.config/dataforge/github-token` with an editor, as a single line.

Done when `[ -s ~/.config/dataforge/github-token ] && echo set` prints `set`.

### Git identity and the gateway address

Replace the placeholders with the git identity of the account above and the account gateway's address (`host:port`):

```sh
cat >~/.config/dataforge/setup.yml <<'SETTINGS'
git_user_name: "<account name>"
git_user_email: "<account email>"
redcoast_client_address: "<host:port>"
SETTINGS
```

`bootstrap.sh` passes this file to Ansible on every run. Other variables that can go here are listed at the top of `machine/playbook.yml`; for example `zsh_personal: false` skips the personal zsh layer.

The setup installs the gateway client from the [redcoast](https://github.com/Luolc/redcoast) release pinned in `machine/versions.yml`, checking each file against its pinned sha256: `redcoast-client`, with the shim `redcoast-client-shim-full` as `claude` next to it. Once the address is set, it writes the shim's config, `~/.config/redcoast-client/shim.env`, which points at the machine credential (`~/.config/redcoast-client/machine-credential`) without reading it; the user places the credential, like the GitHub token.

Done when `cat ~/.config/dataforge/setup.yml` shows the three lines with real values.

### Private environment variables

Every zsh sources `~/.config/dataforge/zshenv.local` when it is readable, after the setup's own `PATH`. Environment variables that belong to this machine and not to this repo go there. The setup never creates or overwrites it.

Done when `zsh -c 'echo ok'` prints `ok` with no error.

## 4. Run the setup again

```sh
~/dev/dataforge/machine/bootstrap.sh
```

This run logs gh in with the token (`gh auth login --with-token`, then `gh auth setup-git`), sets the git identity and writes the gateway client's config.

Done when the line under `PLAY RECAP` shows `failed=0`, and `gh auth status --hostname github.com` reports a logged-in account. Running it again should show `changed=0`.

## 5. Log in again

Log out and SSH in again, so the login shell is zsh and the agent user is in the `docker` group.

Done when `echo $SHELL; id -nG` prints `/usr/bin/zsh` and a group list containing `docker`, and `zsh -i -c 'command -v claude'` prints `~/.local/libexec/redcoast-client/current/claude` (with the home directory expanded).

## Upgrades

Tool versions change through PRs to `machine/versions.yml`. On each data machine, run `git -C ~/dev/dataforge pull --ff-only && ~/dev/dataforge/machine/bootstrap.sh`. A gateway client download that does not match its pinned sha256 stops the run and leaves the installed version in use.

fednet is the exception: the setup installs its pinned version only where `/usr/local/bin/fednet` does not exist yet. After that first install, fednet upgrades are done by the self-upgrade units on the machine; on a machine without those units, delete the binary and run `bootstrap.sh` again to upgrade.
