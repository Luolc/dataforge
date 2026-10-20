# Setting up a new data machine

From a freshly provisioned machine to one that is set up for coding agents. What the setup installs and how it fits with the rest is in [`docs/design.md`](../docs/design.md); this page is only the manual steps.

Before you start, the machine exists with Ubuntu 24.04 and an agent user with passwordless sudo, set up by the machine's provisioning (outside this repo; see "What this is, and what it is not" in [`docs/design.md`](../docs/design.md)). Every step below runs on the data machine as the agent user unless it says otherwise.

The setup runs twice: once right away, to install every tool, and again once the machine's own settings (step 4) are in place. The settings can be written by hand, as below, or by the machine's own provisioning; the setup only reads them.

## 1. Clone this repo

This repo is public, so the clone needs no token. Replace `<owner>/<repo>` with this repo's coordinates:

```sh
git clone https://github.com/<owner>/<repo> ~/dev/dataforge
```

Done when `git -C ~/dev/dataforge log --oneline -1` prints a commit.

## 2. Prepare the shell and Claude Code settings

The setup writes no shell startup file and no Claude Code settings: they belong to the machine's owner, who writes them by hand or with their own dotfiles. The setup only checks the few things it cannot run without, and when one is not in place it stops, naming what is missing and the section below. It checks them only once the gateway address (step 4) is set, since until then the shim cannot start Claude Code anyway: the first run (step 3) skips the checks and says so in its output, and the run after the address is set (step 5) makes them. So put them in place before step 5; doing it now does no harm. A machine set up by an earlier version of the setup keeps the `~/.zshenv`, `~/.zshrc` and `~/.p10k.zsh` that version wrote; the setup no longer touches them.

### The gateway shim on PATH

Every new shell of the agent user, login or interactive, must find the gateway client's `claude` shim before the real Claude Code, so that every `claude` started in a pane goes through the gateway. The setup installs the shim in step 3, into a directory that does not exist yet; listing it on `PATH` before then does nothing. When the login shell is zsh, put this line in `~/.zshenv`, which every zsh reads, and make sure nothing read after it (`~/.zprofile`, `~/.zshrc`, `~/.zlogin`) puts another directory in front of it:

```sh
export PATH="$HOME/.local/libexec/redcoast-client/current:$HOME/.local/bin:$PATH"
```

`~/.local/bin` holds uv, Claude Code, Pi and Codex CLI. When the login shell is bash, put the same line at the end of both `~/.profile` and `~/.bashrc`.

Every run of the setup with the gateway address set, once it has installed the shim, starts a login shell and an interactive shell of the agent user's login shell, each with a clean environment, and stops unless `command -v claude` prints `~/.local/libexec/redcoast-client/current/claude` (with the home directory expanded) in both.

Done when `grep -F redcoast-client/current ~/.zshenv` (or `~/.profile` and `~/.bashrc`) prints the line.

### Claude Code settings

Agents on a data machine are started with `--dangerously-skip-permissions` and nobody is at the screen, and Claude Code must stay at the version the setup pins. With the gateway address set, the setup checks these four keys, and stops when one is not as listed:

- `hasCompletedOnboarding` is `true` in `~/.claude.json`, so the first-run onboarding does not appear.
- `skipDangerousModePermissionPrompt` is `true` in `~/.claude/settings.json`, so the bypass-permissions warning does not appear.
- `env.DISABLE_UPDATES` is set in `~/.claude/settings.json`. Besides turning off the background updates, it makes `claude update` and `claude install` refuse to run, so even a `claude` not started through the gateway client stays at the pinned version.
- `env.DISABLE_AUTOUPDATER` is not set in `~/.claude/settings.json`: the gateway client sets it for the sessions it starts, and refuses to start `claude` while a settings file sets it.

On a machine that has neither file yet, this writes both with the four keys. It leaves a file that already exists as it is; add the keys to such a file by hand.

```sh
(
  umask 077
  mkdir -p ~/.claude
  [ -e ~/.claude.json ] || cat >~/.claude.json <<'JSON'
{
  "hasCompletedOnboarding": true
}
JSON
  [ -e ~/.claude/settings.json ] || cat >~/.claude/settings.json <<'JSON'
{
  "skipDangerousModePermissionPrompt": true,
  "env": {
    "DISABLE_UPDATES": "1"
  }
}
JSON
)
```

Also recommended in `~/.claude/settings.json`, though the setup does not check them:

- `"autoMemoryEnabled": false`: agents keep nothing in Claude Code's own memory between sessions.
- `"DISABLE_ERROR_REPORTING": "1"`, `"DISABLE_FEEDBACK_COMMAND": "1"` and `"CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1"` under `env`: no error reports or feedback leave the machine, and no survey appears in a pane nobody watches.
- `"model": "opus[1m]"`, `"effortLevel": "high"`, `"autoCompactEnabled": true` and `"autoCompactWindow": 520000`: the default model, effort and context window for agents started without `--model`.
- `"attribution": {"commit": "", "pr": "", "sessionUrl": false}`: commits and PRs carry no Claude Code attribution or session link.
- `"promptSuggestionEnabled": false`: the suggested prompt in the input box reads like a typed draft when an agent reads another agent's pane.
- `"disableRemoteControl": true`: data machines have no Remote Control.

Done when `cat ~/.claude.json ~/.claude/settings.json` shows the four keys as listed.

### Login shell and prompt

The setup installs zsh, but does not make it the login shell, and installs no oh-my-zsh, powerlevel10k or prompt configuration; those are the owner's too. To make zsh the login shell once step 3 has installed it, run `sudo chsh -s /usr/bin/zsh "$USER"`, with the line above in `~/.zshenv`.

## 3. Install the tools

```sh
~/dev/dataforge/machine/bootstrap.sh
```

This installs Docker, gh, herdr, Claude Code, Pi and Codex CLI (the clients the gateway client starts for Codex sessions), Tailscale, the 1Password CLI `op`, Node.js (from NodeSource, with npm), Google Chrome (`google-chrome-stable`, Google's `.deb`, with no apt source) and direnv at the versions pinned in `machine/versions.yml`, zsh, the Noto CJK fonts, ripgrep and fd (as `fdfind`, both for Pi) from the Ubuntu archive, and the tools the repository owner publishes, the gateway client, Agent Fleet (`fleet`), fednet, atb and statute, at the latest release of each, checked against the checksum file published in that release. It needs no GitHub token for that: it reads the latest release from the release pages, and statute's from one anonymous GitHub API call. Claude Code, Pi and Codex CLI are the agent user's own, at `~/.local/bin/claude`, `~/.local/bin/pi` and `~/.local/bin/codex`. Programs that other setups refer to by absolute path are at `/usr/local/bin/<name>` (`fleet`, `fednet`, `atb`, `statute`, `herdr`, `gh`) and `/usr/bin/<name>` (`tailscale`, `op`, `direnv`, `node`, `npm`, `npx`, `google-chrome`). The setup only installs them: it joins no tailnet, signs in to no account, and writes no config for fleet, fednet, Pi or Codex CLI or starts any service of theirs. It does configure Docker's log rotation, `log-driver` and `log-opts` in `/etc/docker/daemon.json` (`docker_log_max_size` and `docker_log_max_file` in `machine/playbook.yml`), keeping the file's other keys, and restarts dockerd when those change, which stops the running containers.

With none of step 4's settings yet, the run skips the gh login and the checks of step 2, and leaves the gateway client without its config; it says so in its output.

Done when the line under `PLAY RECAP` shows `failed=0`.

## 4. Put the machine's settings in place

Only the user, or the machine's provisioning, does this step. Each file is optional to the setup, which skips what is missing.

### The GitHub token

The data machine makes its commits and opens its PRs as one GitHub account, through that account's token. A dedicated bot account is recommended, so that the agents' commits are told apart from a person's.

```sh
mkdir -p -m 700 ~/.config/dataforge && install -m 600 /dev/null ~/.config/dataforge/github-token
```

Then paste that account's token into `~/.config/dataforge/github-token` with an editor, as a single line.

Done when `[ -s ~/.config/dataforge/github-token ] && echo set` prints `set`.

### The gateway address

Replace the placeholder with the account gateway's address (`host:port`):

```sh
cat >~/.config/dataforge/setup.yml <<'SETTINGS'
redcoast_client_address: "<host:port>"
SETTINGS
```

`bootstrap.sh` passes this file to Ansible on every run. Other variables that can go here are listed at the top of `machine/playbook.yml`; for example `docker_log_max_file`.

The setup installs the gateway client from the latest [redcoast](https://github.com/Luolc/redcoast) release, checking each file against that release's `SHA256SUMS`: `redcoast-client`, with the shim `redcoast-client-shim-full` as `claude` next to it. Once the address is set, it writes the shim's config, `~/.config/redcoast-client/shim.env`, which points at the machine credential (`~/.config/redcoast-client/machine-credential`) without reading it; the user places the credential, like the GitHub token.

Done when `cat ~/.config/dataforge/setup.yml` shows the line with a real value.

## 5. Run the setup again

```sh
~/dev/dataforge/machine/bootstrap.sh
```

This run logs gh in with the token (`gh auth login --with-token`), writes the gateway client's config and makes the checks of step 2, stopping when one fails.

Done when the line under `PLAY RECAP` shows `failed=0`, and `gh auth status --hostname github.com` reports a logged-in account. Running it again should show `changed=0`.

### Git identity and credential helper

The setup never writes `~/.gitconfig`; the machine's owner configures git there, by hand or with their dotfiles. For the account above, once the run above has logged gh in:

```sh
git config --global user.name "<account name>"
git config --global user.email "<account email>"
gh auth setup-git --hostname github.com
```

Done when `git config --global user.email` prints the email and `git config --global --get-all credential.https://github.com.helper` prints a `gh auth git-credential` line.

## 6. Log in again

Log out and SSH in again, so the agent user is in the `docker` group.

Done when `id -nG` prints a group list containing `docker`, and `command -v claude` prints `~/.local/libexec/redcoast-client/current/claude` (with the home directory expanded).

## Upgrades

Pinned versions change through PRs to `machine/versions.yml`. On each data machine, run `git -C ~/dev/dataforge pull --ff-only && ~/dev/dataforge/machine/bootstrap.sh`; the same run also brings the repository owner's tools to their latest release. A release without its checksum file, or a download that does not match it, stops the run with an error naming the tool, and leaves the installed version in use.
