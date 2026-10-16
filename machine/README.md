# Setting up a new data machine

From a freshly provisioned machine to one that is set up for coding agents. What the setup installs and how it fits with the rest is in [`docs/design.md`](../docs/design.md); this page is only the manual steps.

Before you start, the machine exists with Ubuntu 24.04 and an agent user with passwordless sudo, set up by the machine's provisioning (outside this repo; see "What this is, and what it is not" in [`docs/design.md`](../docs/design.md)). Every step below runs on the data machine as the agent user unless it says otherwise.

## 1. Put the GitHub token in place

Only the user does this step.

The data machine makes its commits and opens its PRs as one GitHub account, through that account's token. A dedicated bot account is recommended, so that the agents' commits are told apart from a person's.

```sh
mkdir -p -m 700 ~/.config/dataforge && install -m 600 /dev/null ~/.config/dataforge/github-token
```

Then paste that account's token into `~/.config/dataforge/github-token` with an editor, as a single line.

Done when `[ -s ~/.config/dataforge/github-token ] && echo set` prints `set`.

## 2. Log gh in with the token

The pinned gh comes with the setup; until then, the one from the Ubuntu archive does:

```sh
sudo apt-get install -y gh
```

Done when `gh --version` prints a version.

gh reads the token from the file; the value never appears on the command line:

```sh
gh auth login --hostname github.com --with-token < ~/.config/dataforge/github-token
```

Done when `gh auth status --hostname github.com` reports a logged-in account.

```sh
gh auth setup-git --hostname github.com
```

Done when `git config --global --get-all credential.https://github.com.helper` prints a line containing `gh auth git-credential`.

## 3. Clone this repo and write the machine's settings

Replace `<owner>/<repo>` with this repo's coordinates:

```sh
gh repo clone <owner>/<repo> ~/dev/dataforge
```

Done when `git -C ~/dev/dataforge log --oneline -1` prints a commit.

Replace the placeholders with the git identity of the account from step 1:

```sh
cat >~/.config/dataforge/setup.yml <<'EOF'
git_user_name: "<account name>"
git_user_email: "<account email>"
EOF
```

`bootstrap.sh` passes this file to Ansible on every run. Other variables that can go here are listed at the top of `machine/playbook.yml`; for example `zsh_personal: false` skips the personal zsh layer.

Done when `cat ~/.config/dataforge/setup.yml` shows the two lines with real values.

## 4. Point the gateway client at the gateway

The setup in the next step downloads the gateway client from the [redcoast](https://github.com/Luolc/redcoast) release pinned in `machine/versions.yml`, checks each file against its pinned sha256, and installs `redcoast-client` with the shim `redcoast-client-shim-full` as `claude` next to it. It writes the shim's config, `~/.config/redcoast-client/shim.env`, which points at the machine credential (`~/.config/redcoast-client/machine-credential`) without reading it; the user places the credential, like the GitHub token.

On the data machine, add the gateway's address (`host:port`) to the machine's settings:

```sh
echo 'redcoast_client_address: "<host:port>"' >>~/.config/dataforge/setup.yml
```

Done when `grep redcoast_client_address ~/.config/dataforge/setup.yml` shows the real address.

## 5. Run the setup

```sh
~/dev/dataforge/machine/bootstrap.sh
```

Done when the line under `PLAY RECAP` shows `failed=0`. Running it again should show `changed=0`.

## 6. Log in again

Log out and SSH in again, so the login shell is zsh and the agent user is in the `docker` group.

Done when `echo $SHELL; id -nG` prints `/usr/bin/zsh` and a group list containing `docker`, and `zsh -i -c 'command -v claude'` prints `~/.local/libexec/redcoast-client/current/claude` (with the home directory expanded).

## Upgrades

Tool versions change through PRs to `machine/versions.yml`. On each data machine, run `git -C ~/dev/dataforge pull --ff-only && ~/dev/dataforge/machine/bootstrap.sh`. A gateway client download that does not match its pinned sha256 stops the run and leaves the installed version in use.
