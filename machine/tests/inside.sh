#!/bin/sh
# Container side of the data machine setup test (run.sh starts it). Runs the
# setup from a clean image with no machine settings at all and every tool
# from its real release, then twice with them, checks the last run changes
# nothing, also in check mode, that check mode with other versions to
# install runs to the end and installs none of them, and every tool is at
# its pinned version or, for the tools the repository owner publishes, at
# the release the run reports, statute's picked from a fake release list.
# A Pi or Codex CLI tarball with the wrong sha256 must stop the run and
# leave the installed version in use, and rg and fdfind, which Pi would
# otherwise download, must be on PATH.
# No run may change the owner's shell startup files, Claude Code settings or
# global git configuration, or the login shell. The run without settings
# completes with no Claude Code settings and no shim on PATH; once the
# gateway address is set, a run must stop when the gateway shim is not first
# on PATH in a new login or interactive shell, or when one of the four Claude
# Code keys it checks is not as it needs.
# Then it drives the gateway client install from a fake release (a missing
# checksum file or a tampered download must not replace it) and fednet being
# brought to its latest release. Docker is checked as installed, with its log
# rotation in daemon.json, Chrome as running, with the CJK fonts, no apt
# source and only its pinned .deb kept, a Chrome .deb with the wrong sha256 as
# refused, and check mode on a machine that lacks a vendor apt source, or
# that Chrome, Pi, Codex CLI and the gateway client were never installed on,
# as running to the end.
set -eu
m=$HOME/machine
out=$HOME/out
mkdir -p "$out"
pin() { sed -n "s/^$1: *//p" "$m/versions.yml" | tr -d '"'; }
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

# After the first run, fake release pages for the gateway client and a fake
# release list for statute, served on 127.0.0.1 by fake-release.py (which
# needs the python3 and curl the first run installs) and laid out like
# GitHub's, so the later runs reach neither redcoast's releases nor the
# GitHub API. The server dies with the container.
release=$HOME/release
port=8765
gw_releases=$release/Luolc/redcoast/releases
statute_api=$release/repos/Luolc/agent-constitution/releases
mkdir -p "$gw_releases" "${statute_api%/*}"
serve() {
  python3 -I "$m/tests/fake-release.py" "$release" "$port" >"$out/fake-release.log" 2>&1 &
  server=$!
  tries=0
  until curl -fsS -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null; do
    kill -0 "$server" 2>/dev/null || { cat "$out/fake-release.log"; fail "fake release server exited"; }
    tries=$((tries + 1))
    [ "$tries" -lt 50 ] || fail "fake release server did not start"
    sleep 0.1
  done
}
# agent-constitution's newest releases are not statute's, or not final: the
# setup must pass over them to the newest statute release in the list, which
# is not the repository's latest release. Its files are downloaded from that
# real release.
statute_tag=statute-v0.1.24
cat >"$statute_api" <<STATUTE
[
  {"tag_name": "v9.0.0", "draft": false, "prerelease": false},
  {"tag_name": "statute-v9.1.0", "draft": true, "prerelease": false},
  {"tag_name": "statute-v9.0.0", "draft": false, "prerelease": true},
  {"tag_name": "$statute_tag", "draft": false, "prerelease": false},
  {"tag_name": "statute-v0.1.23", "draft": false, "prerelease": false}
]
STATUTE
fake="-e redcoast_client_release_url=http://127.0.0.1:$port/Luolc/redcoast/releases \
  -e statute_releases_api=http://127.0.0.1:$port/repos/Luolc/agent-constitution/releases"

# stage <version> [<build>]: publishes a fake gateway release v<version> with
# its SHA256SUMS, makes it the latest and prints the digests of its client
# and shim. Each version or build has different file contents, as a real one
# would.
stage() {
  dir=$gw_releases/download/v$1
  rm -rf "$dir"
  mkdir -p "$dir"
  cp "$m/tests/fake-gateway/redcoast-client" "$dir/redcoast-client-linux-amd64"
  cp "$m/tests/fake-gateway/claude" "$dir/redcoast-client-shim-full"
  echo "# version $1 ${2:-}" >>"$dir/redcoast-client-shim-full"
  (cd "$dir" && sha256sum redcoast-client-linux-amd64 redcoast-client-shim-full >SHA256SUMS)
  echo "v$1" >"$gw_releases/latest"
  cut -d' ' -f1 "$dir/SHA256SUMS" | paste -sd' '
}
# setup <log name> [more ansible options]
# setup_path, when set, is the PATH the setup runs with.
setup() {
  log=$out/$1
  shift
  # shellcheck disable=SC2086 # $fake is a list of options
  PATH=${setup_path:-$PATH} "$m/bootstrap.sh" \
    $fake -e redcoast_client_address=gateway.invalid:7801 "$@" >"$log" 2>&1
}
# The installed programs have the digests in "<client digest> <shim digest>".
installed_is() { # <version> <digests>
  printf '%s  redcoast-client\n%s  claude\n' "${2% *}" "${2#* }" >"$out/expected"
  (cd "$gw/$1" && sha256sum --check --strict --quiet "$out/expected")
}
recap() { grep -E '^localhost +:' "$out/$1"; }
# The release a run reports for a tool, from its "<tool> <tag>" line.
tag_of() { sed -n "s/^ *\"msg\": \"$1 \(.*\)\"$/\1/p" "$out/$2" | tail -n 1; }

# The owner's files, which no run of the setup may touch. The agent user's
# login shell is bash, as the image made it, and its ~/.profile and ~/.bashrc
# are the stock ones; the zsh files and ~/.gitconfig are only there to be
# left alone. There are no Claude Code settings yet: on a new machine the
# setup runs before the owner's dotfiles are in place, and it checks the shim
# on PATH and the Claude Code settings only once the gateway address is set.
cat >"$HOME/.zshenv" <<'ZSHENV'
export PATH="$HOME/.local/libexec/redcoast-client/current:$HOME/.local/bin:$PATH"
ZSHENV
echo '# The owner'"'"'s zshrc.' >"$HOME/.zshrc"
echo '# The owner'"'"'s p10k.zsh.' >"$HOME/.p10k.zsh"
printf '[user]\n\tname = Owner\n\temail = owner@example.invalid\n' >"$HOME/.gitconfig"
owner_files=".profile .bashrc .zshenv .zshrc .p10k.zsh .gitconfig"
# shellcheck disable=SC2086 # one word per file
(cd "$HOME" && sha256sum $owner_files) >"$out/owner.sha256"
owner_unchanged() { (cd "$HOME" && sha256sum --check --quiet "$out/owner.sha256"); }
login_shell=$(getent passwd agent | cut -d: -f7)
settings=$HOME/.claude/settings.json
claude_state=$HOME/.claude.json

# A Docker daemon setting the setup does not own, which it must keep, next
# to the log options it owns.
daemon=/etc/docker/daemon.json
sudo mkdir -p /etc/docker
echo '{"debug": true, "log-opts": {"labels": "kept"}}' | sudo tee "$daemon" >/dev/null

# A new machine runs the setup before any of its settings are in place: no
# gateway address or GitHub token. Every tool comes from its
# real latest release, found without a token.
"$m/bootstrap.sh" >"$out/run0.log" 2>&1 || { tail -n 40 "$out/run0.log"; fail "run without machine settings"; }
for t in fleet fednet atb statute "gateway client"; do
  [ -n "$(tag_of "$t" run0.log)" ] || fail "run0 reports no release for $t"
done
case "$(tag_of statute run0.log)" in statute-v*) ;; *) fail "run0 took statute from $(tag_of statute run0.log)" ;; esac
grep -q 'No redcoast_client_address' "$out/run0.log" || fail "run0 did not report the missing gateway address"
grep -q 'and of the Claude Code settings did not run' "$out/run0.log" || fail "run0 did not report the checks it skipped"
[ ! -e "$HOME/.config/redcoast-client/shim.env" ] || fail "shim.env written without a gateway address"
owner_unchanged || fail "run0 changed the owner's files"
[ ! -e "$settings" ] && [ ! -e "$claude_state" ] || fail "run0 wrote Claude Code settings"
[ "$(getent passwd agent | cut -d: -f7)" = "$login_shell" ] || fail "run0 changed the login shell"
ok "run without machine settings: $(recap run0.log), statute $(tag_of statute run0.log), gateway client $(tag_of "gateway client" run0.log)"

# What machine/README.md (step 2) has the owner put in place before the
# setup runs with the gateway address: ~/.profile and ~/.bashrc put the
# gateway shim first on PATH, and the Claude Code settings have the four keys
# the setup checks, next to one it does not.
for f in profile bashrc; do
  cat >>"$HOME/.$f" <<'SHIM'
export PATH="$HOME/.local/libexec/redcoast-client/current:$HOME/.local/bin:$PATH"
SHIM
  cp "$HOME/.$f" "$out/$f"
done
mkdir -p "${settings%/*}"
echo '{"skipDangerousModePermissionPrompt": true, "env": {"DISABLE_UPDATES": "1", "EXISTING_SETTING": "kept"}}' >"$settings"
cp "$settings" "$out/settings.json"
echo '{"hasCompletedOnboarding": true}' >"$claude_state"
cp "$claude_state" "$out/claude.json"
# shellcheck disable=SC2086 # one word per file
(cd "$HOME" && sha256sum $owner_files "${settings#"$HOME"/}" "${claude_state#"$HOME"/}") >"$out/owner.sha256"

serve
v1=$(stage 1.0.0)

# A .deb of an earlier Chrome pin, which the next run must remove.
sudo touch /opt/google-chrome/google-chrome-stable_1.0.0.0-1_amd64.deb

setup run1.log || { tail -n 40 "$out/run1.log"; fail "first run"; }
ok "first run: $(recap run1.log)"
setup run2.log || { tail -n 40 "$out/run2.log"; fail "second run"; }
recap run2.log | grep -Eq 'changed=0 +unreachable=0 +failed=0' || {
  grep -A1 -E '^TASK ' "$out/run2.log" | grep -B1 -E '^changed:' || true
  fail "second run changed something: $(recap run2.log)"
}
ok "second run: $(recap run2.log)"
setup run2c.log --check || { tail -n 40 "$out/run2c.log"; fail "check-mode run"; }
recap run2c.log | grep -Eq 'changed=0 +unreachable=0 +failed=0' || {
  grep -A1 -E '^TASK ' "$out/run2c.log" | grep -B1 -E '^changed:' || true
  fail "check-mode run would change something: $(recap run2c.log)"
}
ok "check-mode run: $(recap run2c.log)"
grep -A1 -F 'TASK [Gateway client | claude is the shim in a new shell]' "$out/run2.log" | grep -q '^ok:' ||
  fail "second run did not check that claude is the shim"

# A new shell that finds another claude first, login or interactive: the run
# stops, naming the shell and the README's section. Check mode runs the
# check too, so these runs use it.
for t in profile:-l bashrc:-i; do
  file=$HOME/.${t%%:*}
  echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$file"
  if setup "run2s${t#*:}.log" --check; then fail "a $t that puts ~/.local/bin first was accepted"; fi
  cp "$out/${t%%:*}" "$file"
  grep -q "claude is not the gateway shim $HOME/.local/libexec/redcoast-client/current/claude in a new shell: .$login_shell ${t#*:}. finds claude at ..$HOME/.local/bin/claude." "$out/run2s${t#*:}.log" ||
    { tail -n 20 "$out/run2s${t#*:}.log"; fail "run2s${t#*:} failed for another reason"; }
  grep -qF 'README.md, \"The gateway shim on PATH\", shows' "$out/run2s${t#*:}.log" || fail "run2s${t#*:} does not name the README's section"
done
ok "a new login or interactive shell that finds another claude stops the run"

# The same with zsh as the login shell, as on a real data machine: the line
# in ~/.zshenv alone passes both shells, and a ~/.zshrc that puts
# ~/.local/bin first stops the run at the interactive one. The new shells
# start from a clean environment, so a PATH of the setup's caller with the
# shim first does not stand in for a missing line in ~/.zshenv.
sudo usermod -s /usr/bin/zsh agent
cp "$HOME/.zshenv" "$out/zshenv"
cp "$HOME/.zshrc" "$out/zshrc"
setup run2z.log --check || { tail -n 20 "$out/run2z.log"; fail "a zsh login shell with the line in ~/.zshenv was refused"; }
grep -A1 -F 'TASK [Gateway client | claude is the shim in a new shell]' "$out/run2z.log" | grep -q '^ok:' ||
  fail "the zsh run did not check that claude is the shim"
echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$HOME/.zshrc"
if setup run2zi.log --check; then fail "a ~/.zshrc that puts ~/.local/bin first was accepted"; fi
cp "$out/zshrc" "$HOME/.zshrc"
grep -q "in a new shell: ./usr/bin/zsh -i. finds claude at ..$HOME/.local/bin/claude." "$out/run2zi.log" ||
  { tail -n 20 "$out/run2zi.log"; fail "run2zi failed for another reason"; }
echo '# No PATH line.' >"$HOME/.zshenv"
setup_path=$HOME/.local/libexec/redcoast-client/current:$PATH
if setup run2ze.log --check; then
  fail "a ~/.zshenv without the line was accepted because the caller's PATH has the shim first"
fi
setup_path=
cp "$out/zshenv" "$HOME/.zshenv"
grep -q 'in a new shell: ./usr/bin/zsh -l. finds claude at' "$out/run2ze.log" ||
  { tail -n 20 "$out/run2ze.log"; fail "run2ze failed for another reason"; }
sudo usermod -s "$login_shell" agent
ok "a zsh login shell passes with the line in ~/.zshenv alone, and stops the run without it or behind a ~/.zshrc that puts ~/.local/bin first"

# Each of the four Claude Code keys the setup checks, not as it needs: the
# run stops, naming the key, its file and the README's section.
for t in \
  "$claude_state|del(.hasCompletedOnboarding)|hasCompletedOnboarding is not true in $claude_state" \
  "$settings|.skipDangerousModePermissionPrompt = false|skipDangerousModePermissionPrompt is not true in $settings" \
  "$settings|del(.env.DISABLE_UPDATES)|env.DISABLE_UPDATES is not set in $settings" \
  "$settings|.env.DISABLE_AUTOUPDATER = \"1\"|env.DISABLE_AUTOUPDATER is set in $settings"; do
  file=${t%%|*}
  rest=${t#*|}
  jq "${rest%%|*}" "$file" >"$out/mutated.json"
  cp "$out/mutated.json" "$file"
  if setup run2k.log --check; then fail "Claude Code settings accepted: ${rest#*|}"; fi
  cp "$out/settings.json" "$settings"
  cp "$out/claude.json" "$claude_state"
  grep -q "Claude Code settings: ${rest#*|}\." "$out/run2k.log" || { tail -n 20 "$out/run2k.log"; fail "run2k failed for another reason than: ${rest#*|}"; }
  grep -qF 'README.md, \"Claude Code settings\", shows' "$out/run2k.log" || fail "run2k does not name the README's section"
done
owner_unchanged || fail "the owner's files are not restored after the failing runs"
ok "each of the four Claude Code keys, when not as needed, stops the run"

# Check mode when the latest release of the gateway client and of statute (a
# tarball tool) is not the installed one, and gh and Pi are pinned to
# another version: the run reports what it would change and goes on to the
# end, but installs nothing and switches nothing.
gw=$HOME/.local/libexec/redcoast-client
v2=$(stage 2.0.0)
statute_old=$(ls /opt/statute)
gh_old=$(ls /opt/gh)
pi_old=$(ls "$HOME/.local/libexec/pi")
cp "$statute_api" "$out/statute-api"
jq --arg t statute-v0.1.23 '[{tag_name: $t, draft: false, prerelease: false}] + .' "$out/statute-api" >"$statute_api"
setup run2n.log --check -e gh_version=2.97.0 \
  -e gh_sha256=a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112 \
  -e pi_version=1.1.0 -e pi_sha256=3faa94666cd3849d37af320ff749407d0271b07a9b94f420c87e30866e10e289 ||
  { tail -n 40 "$out/run2n.log"; fail "check-mode run with new versions"; }
cp "$out/statute-api" "$statute_api"
recap run2n.log | grep -Eq 'unreachable=0 +failed=0' || fail "check-mode run with new versions failed: $(recap run2n.log)"
grep -q 'TASK \[Gateway client | switch current\]' "$out/run2n.log" || fail "check-mode run with new versions stopped before switching the gateway client"
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "check mode moved current"
[ ! -e "$gw/2.0.0" ] && [ ! -e "$gw/2.0.0.new" ] || fail "check mode installed gateway 2.0.0"
[ "$(ls /opt/statute)" = "$statute_old" ] || fail "check mode changed /opt/statute: $(ls /opt/statute)"
[ "$(ls /opt/gh)" = "$gh_old" ] || fail "check mode changed /opt/gh: $(ls /opt/gh)"
[ "$(ls "$HOME/.local/libexec/pi")" = "$pi_old" ] || fail "check mode changed ~/.local/libexec/pi: $(ls "$HOME/.local/libexec/pi")"
[ "$(readlink "$HOME/.local/bin/pi")" = "$HOME/.local/libexec/pi/$(pin pi_version)/pi" ] || fail "check mode relinked pi"
ok "check-mode run with new versions: $(recap run2n.log), nothing installed"

# Versions. Docker is only checked as installed; whether it runs is checked
# on the first real data machine.
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
check_version() { # <label> <expected substring> <command...>
  label=$1
  want=$2
  shift 2
  [ -n "$want" ] || fail "$label: no expected version"
  got=$("$@" 2>&1 | head -n 1)
  case "$got" in *"$want"*) ok "$label: $got" ;; *) fail "$label: want $want, got: $got" ;; esac
}
check_version uv "uv $(pin uv_version)" uv --version
check_version herdr "$(pin herdr_version)" herdr --version
check_version gh "gh version $(pin gh_version)" gh --version
check_version claude "$(pin claude_code_version) (Claude Code)" claude --version
pi_got=$("$HOME/.local/bin/pi" --version 2>&1)
[ "$pi_got" = "$(pin pi_version)" ] || fail "pi: want $(pin pi_version), got: $pi_got"
ok "pi: $pi_got"
check_version codex "codex-cli $(pin codex_version)" "$HOME/.local/bin/codex" --version
check_version docker "$(pin docker_version | sed 's/^[0-9]*://; s/-.*//')" docker --version
check_version zsh "zsh " zsh --version
# The tools other setups refer to by absolute path, at those paths.
check_version tailscale "$(pin tailscale_version)" /usr/bin/tailscale version
check_version op "$(pin op_version | sed 's/-.*//')" /usr/bin/op --version
check_version node "v$(pin nodejs_version | sed 's/-.*//')" /usr/bin/node --version
check_version chrome "Google Chrome $(pin google_chrome_version | sed 's/-.*//')" /usr/bin/google-chrome --version
# Chrome has no apt source, so apt upgrade cannot move it off the pin:
# neither the package's postinst nor its daily cron job has added one.
sudo /etc/cron.daily/google-chrome || fail "Chrome's cron job failed"
chrome_sources=$(ls /etc/apt/sources.list.d | grep -i chrome || true)
[ -z "$chrome_sources" ] || fail "an apt source for Chrome: $chrome_sources"
ok "no apt source for Chrome, after its cron job"
chrome_debs=$(ls /opt/google-chrome)
[ "$chrome_debs" = "google-chrome-stable_$(pin google_chrome_version)_amd64.deb" ] ||
  fail "/opt/google-chrome holds more than the pinned .deb: $chrome_debs"
ok "/opt/google-chrome holds only $chrome_debs"
cjk=$(fc-list | grep -c 'Noto Sans CJK') || fail "fc-list lists no Noto Sans CJK font"
ok "fc-list: $cjk Noto Sans CJK faces"
# Pi looks up rg and fd (or fdfind) on PATH and downloads them unverified
# when they are missing.
for t in rg fdfind; do
  found=$(command -v "$t") || fail "$t not on PATH"
  ok "$t: $found"
done
check_version direnv "$(dpkg-query -W -f '${Version}' direnv | sed 's/-.*//')" /usr/bin/direnv version
check_version fleet "fleet $(tag_of fleet run2.log | sed 's/^v//')" /usr/local/bin/fleet --version
check_version fednet "$(tag_of fednet run2.log)" /usr/local/bin/fednet version
check_version atb "atb $(tag_of atb run2.log | sed 's/^v//')" /usr/local/bin/atb --version
[ "$(tag_of statute run2.log)" = "$statute_tag" ] || fail "statute: run reports $(tag_of statute run2.log), not $statute_tag"
check_version statute "statute ${statute_tag#statute-v}" /usr/local/bin/statute --version

# A pinned Chrome .deb whose digest does not match: the run fails, naming
# the package, and the installed Chrome stays.
bad=0000000000000000000000000000000000000000000000000000000000000000
if setup run2g.log -e google_chrome_sha256=$bad; then fail "a Chrome .deb with the wrong sha256 was accepted"; fi
grep -q 'google-chrome-stable: the pinned .deb' "$out/run2g.log" || { tail -n 20 "$out/run2g.log"; fail "run2g failed for another reason"; }
check_version "chrome after a wrong sha256" "Google Chrome $(pin google_chrome_version | sed 's/-.*//')" /usr/bin/google-chrome --version

# A Pi or Codex CLI tarball, of a version that exists, whose digest does
# not match the pin: the run fails, naming the tool, before it keeps the
# tarball, unpacks it or relinks the tool, and the installed version stays.
for t in pi:1.1.0 codex:0.159.2; do
  name=${t%%:*}
  version=${t#*:}
  if setup "run2-$name.log" -e "${name}_version=$version" -e "${name}_sha256=$bad"; then
    fail "a $name $version tarball with the wrong sha256 was accepted"
  fi
  grep -Eq "$name: the pinned release $version could not be installed .*did not match" "$out/run2-$name.log" ||
    { tail -n 20 "$out/run2-$name.log"; fail "run2-$name failed for another reason"; }
  left=$(ls "$HOME/.local/libexec/$name")
  [ "$left" = "$(pin "${name}_version")
$(pin "${name}_version").tar.gz" ] || fail "~/.local/libexec/$name after a wrong sha256: $left"
done
check_version "pi after a wrong sha256" "$(pin pi_version)" "$HOME/.local/bin/pi" --version
check_version "codex after a wrong sha256" "codex-cli $(pin codex_version)" "$HOME/.local/bin/codex" --version

# A machine set up before a vendor apt source was added (here, without the
# Node.js source, and Node.js pinned to a version it does not have): check
# mode cannot look up that source's packages, reports it and still previews
# the rest of the setup.
sources=/etc/apt/sources.list.d
sudo mv "$sources/nodesource.sources" "$out/"
setup run2v.log --check -e nodejs_version=26.11.1-1nodesource1 ||
  { tail -n 40 "$out/run2v.log"; fail "check-mode run without the Node.js source"; }
sudo mv "$out/nodesource.sources" "$sources/"
recap run2v.log | grep -Eq 'unreachable=0 +failed=0 .*ignored=1' || {
  grep -A3 -E '^TASK \[Tailscale' "$out/run2v.log" || true
  fail "check-mode run without the Node.js source: $(recap run2v.log)"
}
grep -q 'TASK \[Base agent instructions\]' "$out/run2v.log" || fail "check-mode run without the Node.js source stopped before the last task"
ok "check-mode run without the Node.js source: $(recap run2v.log)"

# A machine Chrome, Pi, Codex CLI and the gateway client were never
# installed on has no /opt/google-chrome or
# ~/.local/libexec/{pi,codex,redcoast-client}. Check mode creates no
# directory, so it cannot download into one, and there is no shim yet to
# check for; it still previews the rest of the setup and leaves the
# directories uncreated.
sudo mv /opt/google-chrome "$out/google-chrome"
mv "$HOME/.local/libexec/pi" "$HOME/.local/libexec/codex" "$HOME/.local/libexec/redcoast-client" "$out/"
setup run2h.log --check ||
  { tail -n 40 "$out/run2h.log"; fail "check-mode run before Chrome, Pi, Codex CLI and the gateway client are installed"; }
[ ! -e /opt/google-chrome ] || fail "check mode created /opt/google-chrome"
for t in pi codex redcoast-client; do
  [ ! -e "$HOME/.local/libexec/$t" ] || fail "check mode created ~/.local/libexec/$t"
done
sudo mv "$out/google-chrome" /opt/google-chrome
mv "$out/pi" "$out/codex" "$out/redcoast-client" "$HOME/.local/libexec/"
recap run2h.log | grep -Eq 'unreachable=0 +failed=0' || {
  grep -A3 -E '^TASK \[(Google Chrome|Gateway client)' "$out/run2h.log" || true
  fail "check-mode run before Chrome, Pi, Codex CLI and the gateway client are installed: $(recap run2h.log)"
}
grep -A1 -F 'TASK [Gateway client | claude is the shim in a new shell]' "$out/run2h.log" | grep -q '^skipping:' ||
  fail "check mode checked for a shim that was never installed"
grep -q 'TASK \[Base agent instructions\]' "$out/run2h.log" || fail "check-mode run before Chrome, Pi, Codex CLI and the gateway client are installed stopped before the last task"
ok "check-mode run before Chrome, Pi, Codex CLI and the gateway client are installed: $(recap run2h.log)"

for d in dev wt scratch; do [ -d "$HOME/$d" ] || fail "$HOME/$d missing"; done
ok "dev, wt and scratch under $HOME"
id -nG agent | grep -qw docker || fail "docker group"
ok "in docker group"
[ "$(jq -r '."log-driver"' "$daemon")" = json-file ] || fail "daemon.json: log-driver is not json-file"
[ "$(jq -r '."log-opts"."max-size"' "$daemon")" = 50m ] || fail "daemon.json: log-opts.max-size is not 50m"
[ "$(jq '."log-opts"."max-file"' "$daemon")" = '"5"' ] || fail "daemon.json: log-opts.max-file is not the string \"5\""
[ "$(jq -c '[.debug, ."log-opts".labels]' "$daemon")" = '[true,"kept"]' ] || fail "daemon.json: an existing setting was dropped"
ok "Docker logs rotate: $(jq -c '."log-opts"' "$daemon"), existing keys kept"

# Gateway client.
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "current is not 1.0.0"
grep -qx 'prefix=' "$HOME/.config/redcoast-client/shim.env" || fail "shim.env has no empty prefix= line"
ok "gateway 1.0.0 installed, shim.env has an empty prefix"

installed_is 1.0.0 "$v1" || fail "1.0.0 does not have the release's digests"
ok "gateway 1.0.0 matches its release's SHA256SUMS"

# A latest release without its checksum file: the run fails, naming the
# tool, and installs nothing from it.
v2=$(stage 2.0.0)
rm "$gw_releases/download/v2.0.0/SHA256SUMS"
if setup run3.log; then fail "a release without its checksum file was accepted"; fi
grep -q 'gateway client: release v2.0.0 has no checksum file SHA256SUMS' "$out/run3.log" || { tail -n 20 "$out/run3.log"; fail "run3 failed for another reason"; }
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "current moved without a checksum file"
[ ! -e "$gw/2.0.0" ] && [ ! -e "$gw/2.0.0.new" ] || fail "2.0.0 installed without a checksum file"
ok "2.0.0 without SHA256SUMS refused, 1.0.0 still current"

# A download that does not match the release's SHA256SUMS: the run stops,
# 1.0.0 stays. The shim is downloaded after the client, so the client, which
# matched, must not be installed either.
v2=$(stage 2.0.0)
echo tampered >>"$gw_releases/download/v2.0.0/redcoast-client-shim-full"
if setup run4.log; then fail "tampered download was accepted"; fi
grep -Eq 'item=redcoast-client-shim-full.*did not match' "$out/run4.log" || { tail -n 20 "$out/run4.log"; fail "run4 failed for another reason"; }
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "current moved after a failed install"
[ ! -e "$gw/2.0.0" ] || fail "2.0.0 installed from a tampered download"
ok "tampered 2.0.0 refused, 1.0.0 still current"

# The same version published intact installs and switches.
v2=$(stage 2.0.0)
setup run5.log || { tail -n 40 "$out/run5.log"; fail "intact 2.0.0"; }
[ "$(readlink "$gw/current")" = 2.0.0 ] || fail "current is not 2.0.0"
[ -x "$gw/1.0.0/claude" ] || fail "1.0.0 removed"
ok "intact 2.0.0 installed and current"

# A release rebuilt under the installed tag reinstalls it. When the last
# step, moving the new copy into place, fails (an injected mv failure), the
# old copy is put back and `current` still works.
cp "$gw/2.0.0/claude" "$out/claude-2.0.0-before"
v2b=$(stage 2.0.0 rebuilt)
mkdir -p "$HOME/failmv"
cat >"$HOME/failmv/mv" <<'FAILMV'
#!/bin/sh
case "$1" in *.new) echo "injected mv failure" >&2; exit 1 ;; esac
exec /usr/bin/mv "$@"
FAILMV
chmod +x "$HOME/failmv/mv"
setup_path=$HOME/failmv:$PATH
if setup run6.log; then fail "injected mv failure was not reported"; fi
setup_path=
grep -q 'injected mv failure' "$out/run6.log" || { tail -n 20 "$out/run6.log"; fail "run6 failed for another reason"; }
[ "$(readlink "$gw/current")" = 2.0.0 ] || fail "current moved after a failed replacement"
cmp -s "$gw/2.0.0/claude" "$out/claude-2.0.0-before" || fail "old 2.0.0 not put back"
installed_is 2.0.0 "$v2" || fail "restored 2.0.0 does not have its old digests"
"$gw/current/claude" >/dev/null || fail "the shim does not run after a failed replacement"
ok "failed replacement of 2.0.0 put the old copy back, current still works"

setup run7.log || { tail -n 40 "$out/run7.log"; fail "reinstall of 2.0.0"; }
grep -q 'rebuilt' "$gw/2.0.0/claude" || fail "2.0.0 not reinstalled for the rebuilt release"
[ ! -e "$gw/2.0.0.old" ] || fail "old copy left behind"
ok "rebuilt 2.0.0 reinstalled"

# A fednet that is not its latest release (a stand-in binary here) is
# replaced by that release.
printf '#!/bin/sh\necho stand-in\n' >"$out/fednet"
sudo install -m 755 "$out/fednet" /usr/local/bin/fednet
setup run8.log || { tail -n 40 "$out/run8.log"; fail "run with a stand-in fednet"; }
check_version "fednet replaced" "$(tag_of fednet run8.log)" /usr/local/bin/fednet version

# After every run above, the owner's files and the login shell are as they
# were before the first.
owner_unchanged || fail "the setup changed the owner's files"
ok "the owner's shell startup files, Claude Code settings and ~/.gitconfig unchanged"
[ "$(getent passwd agent | cut -d: -f7)" = "$login_shell" ] || fail "the setup changed the login shell"
ok "login shell still $login_shell"

echo "PASS"
