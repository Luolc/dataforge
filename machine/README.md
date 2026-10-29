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

This installs Docker, gh, herdr, chezmoi, Claude Code, Pi and Codex CLI (the clients the gateway client starts for Codex sessions), Tailscale, the 1Password CLI `op`, Node.js (from NodeSource, with npm), Google Chrome (`google-chrome-stable`, Google's `.deb`, with no apt source) and direnv at the versions pinned in `machine/versions.yml`, the Noto CJK fonts, ripgrep and fd (as `fdfind`, both for Pi) from the Ubuntu archive, XeLaTeX with Beamer, `fontspec`, TikZ and `latexmk` (`xelatex`, `latexmk`, from the Ubuntu archive without its recommended packages), and the tools the repository owner publishes, the gateway client, Agent Fleet (`fleet`), fednet, atb and statute, at the latest release of each, checked against the checksum file published in that release. It needs no GitHub token for that: it reads the latest release from the release pages, and statute's from one anonymous GitHub API call. Claude Code, Pi and Codex CLI are the agent user's own, at `~/.local/bin/claude`, `~/.local/bin/pi` and `~/.local/bin/codex`. Programs that other setups refer to by absolute path are at `/usr/local/bin/<name>` (`fleet`, `fednet`, `atb`, `statute`, `herdr`, `chezmoi`, `gh`) and `/usr/bin/<name>` (`tailscale`, `op`, `direnv`, `node`, `npm`, `npx`, `google-chrome`). The setup only installs them: it joins no tailnet, signs in to no account, and writes no config for fleet, fednet, Pi or Codex CLI or starts any service of theirs. It does configure Docker's log rotation, `log-driver` and `log-opts` in `/etc/docker/daemon.json` (`docker_log_max_size` and `docker_log_max_file` in `machine/playbook.yml`), keeping the file's other keys, and restarts dockerd when those change, which stops the running containers. It also writes the agent user's dotfiles (see "The dotfiles" below).

With no settings yet, the run skips the gh login, and leaves the gateway client without its config; it says so in its output.

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

### The gateway address

Replace the placeholder with the account gateway's address (`host:port`):

```sh
cat >~/.config/dataforge/setup.yml <<'SETTINGS'
redcoast_client_address: "<host:port>"
SETTINGS
```

`bootstrap.sh` passes this file to Ansible on every run. Other variables that can go here are listed at the top of `machine/playbook.yml`; for example `zsh_personal: false` skips the personal zsh layer.

The setup installs the gateway client from the latest [redcoast](https://github.com/Luolc/redcoast) release, checking each file against that release's `SHA256SUMS`: `redcoast-client`, with the shim `redcoast-client-shim-full` as `claude` next to it. Once the address is set, it writes the shim's config, `~/.config/redcoast-client/shim.env`, which points at the machine credential (`~/.config/redcoast-client/machine-credential`) without reading it; the user places the credential, like the GitHub token.

Done when `cat ~/.config/dataforge/setup.yml` shows the line with a real value.

### Private environment variables

Every zsh sources `~/.config/dataforge/zshenv.local` when it is readable, after the setup's own `PATH`. Environment variables that belong to this machine and not to this repo go there. The setup never creates or overwrites it.

Done when `zsh -c 'echo ok'` prints `ok` with no error.

## 4. Run the setup again

```sh
~/dev/dataforge/machine/bootstrap.sh
```

This run logs gh in with the token (`gh auth login --with-token`) and writes the gateway client's config.

Done when the line under `PLAY RECAP` shows `failed=0`, and `gh auth status --hostname github.com` reports a logged-in account. Running it again should show `changed=0`.

### Git identity and credential helper

The setup never writes `~/.gitconfig`; the machine's owner configures git there, by hand or with their dotfiles. For the account above, once the run above has logged gh in:

```sh
git config --global user.name "<account name>"
git config --global user.email "<account email>"
gh auth setup-git --hostname github.com
```

Done when `git config --global user.email` prints the email and `git config --global --get-all credential.https://github.com.helper` prints a `gh auth git-credential` line.

## 5. Log in again

Log out and SSH in again, so the login shell is zsh and the agent user is in the `docker` group.

Done when `echo $SHELL; id -nG` prints `/usr/bin/zsh` and a group list containing `docker`, and `zsh -i -c 'command -v claude'` prints `~/.local/libexec/redcoast-client/current/claude` (with the home directory expanded).

## The dotfiles

The setup writes the agent user's `~/.zshenv`, `~/.zshrc`, `~/.p10k.zsh`, oh-my-zsh and powerlevel10k (the last three only with `zsh_personal`, the default), Claude Code's status line script `~/.claude/statusline-herdr.sh`, and its own keys in Claude Code's `~/.claude/settings.json` and `~/.claude.json`, from the chezmoi source in [`machine/dotfiles/`](dotfiles/), with the chezmoi it installs at `/usr/local/bin/chezmoi` (`machine/tasks/dotfiles.yml`). It runs chezmoi with a config, state and cache of its own, `~/.config/dataforge/chezmoi.toml`, `~/.local/state/dataforge/chezmoistate.boltdb` and `~/.cache/dataforge/chezmoi`, so a chezmoi the machine's owner uses for their own dotfiles is neither read nor written. In the two Claude Code files it only merges in its keys, and leaves a file that already has them as it is.

Before it replaces a file it has never written, as on the first run, the setup copies the file, when its content differs from the setup's, to `~/.local/state/dataforge/backup/<date and time>/`, under the same path and with the same mode, and lists the copies in its output.

After that, the setup does not overwrite a dotfile changed since it wrote it: the run stops and names the file. To see what changed, run the `diff` command the message gives, which is the setup's chezmoi with its paths:

```sh
/usr/local/bin/chezmoi --source ~/dev/dataforge/machine/dotfiles --destination ~ --config ~/.config/dataforge/chezmoi.toml --persistent-state ~/.local/state/dataforge/chezmoistate.boltdb --cache ~/.cache/dataforge/chezmoi diff ~/.zshrc
```

Then either undo the change, or keep it somewhere the setup does not write, such as `~/.config/dataforge/zshenv.local`, and let the setup write the file again by running the same command with `apply --force ~/.zshrc` in place of `diff ~/.zshrc`. A change that belongs on every data machine is a PR to `machine/dotfiles/`.

In check mode (`bootstrap.sh --check`) the setup only shows the diff of the dotfiles, without oh-my-zsh and powerlevel10k, and writes no file and nothing of chezmoi's.

Done when the same `diff` command without a file prints nothing.

## Upgrades

Pinned versions change through PRs to `machine/versions.yml`. On each data machine, run `git -C ~/dev/dataforge pull --ff-only && ~/dev/dataforge/machine/bootstrap.sh`; the same run also brings the repository owner's tools to their latest release. A release without its checksum file, or a download that does not match it, stops the run with an error naming the tool, and leaves the installed version in use.
