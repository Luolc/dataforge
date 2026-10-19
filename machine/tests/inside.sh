#!/bin/sh
# Container side of the data machine setup test (run.sh starts it). Runs the
# setup from a clean image with no machine settings at all and every tool
# from its real release, then twice with them, checks the last run changes
# nothing, also in check mode, that check mode with other versions to
# install runs to the end and installs none of them, and every tool is at
# its pinned version or, for the tools the repository owner publishes, at
# the release the run reports, statute's picked from a fake release list.
# Then it drives the gateway client install from a fake release (a missing
# checksum file or a tampered download must not replace it), the zshenv
# extension point, fednet being brought to its latest release and the
# personal zsh switch. Docker is checked as installed, with its log rotation
# in daemon.json, Chrome as running, with the CJK fonts, no apt source and
# only its pinned .deb kept, a Chrome .deb with the wrong sha256 as refused,
# and check mode on a machine that lacks a vendor apt source, or that Chrome
# was never installed on, as running to the end.
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
  PATH=${setup_path:-$PATH} "$m/bootstrap.sh" -e '{"git_user_name": "Test Agent", "git_user_email": "agent@example.invalid"}' \
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

# A setting the setup does not own, which it must keep, and
# DISABLE_AUTOUPDATER, which the gateway client refuses to start with and the
# setup must remove.
settings=$HOME/.claude/settings.json
mkdir -p "$HOME/.claude"
echo '{"env": {"EXISTING_SETTING": "kept", "DISABLE_AUTOUPDATER": "1"}}' >"$settings"

# A Docker daemon setting the setup does not own, which it must keep, next
# to the log options it owns.
daemon=/etc/docker/daemon.json
sudo mkdir -p /etc/docker
echo '{"debug": true, "log-opts": {"labels": "kept"}}' | sudo tee "$daemon" >/dev/null

# A new machine runs the setup before any of its settings are in place: no
# gateway address, git identity or GitHub token. Every tool comes from its
# real latest release, found without a token.
"$m/bootstrap.sh" >"$out/run0.log" 2>&1 || { tail -n 40 "$out/run0.log"; fail "run without machine settings"; }
for t in fleet fednet atb statute "gateway client"; do
  [ -n "$(tag_of "$t" run0.log)" ] || fail "run0 reports no release for $t"
done
case "$(tag_of statute run0.log)" in statute-v*) ;; *) fail "run0 took statute from $(tag_of statute run0.log)" ;; esac
grep -q 'No redcoast_client_address' "$out/run0.log" || fail "run0 did not report the missing gateway address"
[ ! -e "$HOME/.config/redcoast-client/shim.env" ] || fail "shim.env written without a gateway address"
[ -z "$(git config --global user.email)" ] || fail "git identity set without settings"
ok "run without machine settings: $(recap run0.log), statute $(tag_of statute run0.log), gateway client $(tag_of "gateway client" run0.log)"

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

# Check mode when the latest release of the gateway client and of statute (a
# tarball tool) is not the installed one, and gh is pinned to another
# version: the run reports what it would change and goes on to the end, but
# installs nothing and switches nothing.
gw=$HOME/.local/libexec/redcoast-client
v2=$(stage 2.0.0)
statute_old=$(ls /opt/statute)
gh_old=$(ls /opt/gh)
cp "$statute_api" "$out/statute-api"
jq --arg t statute-v0.1.23 '[{tag_name: $t, draft: false, prerelease: false}] + .' "$out/statute-api" >"$statute_api"
setup run2n.log --check -e gh_version=2.97.0 \
  -e gh_sha256=a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112 ||
  { tail -n 40 "$out/run2n.log"; fail "check-mode run with new versions"; }
cp "$out/statute-api" "$statute_api"
recap run2n.log | grep -Eq 'unreachable=0 +failed=0' || fail "check-mode run with new versions failed: $(recap run2n.log)"
grep -q 'TASK \[Gateway client | switch current\]' "$out/run2n.log" || fail "check-mode run with new versions stopped before switching the gateway client"
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "check mode moved current"
[ ! -e "$gw/2.0.0" ] && [ ! -e "$gw/2.0.0.new" ] || fail "check mode installed gateway 2.0.0"
[ "$(ls /opt/statute)" = "$statute_old" ] || fail "check mode changed /opt/statute: $(ls /opt/statute)"
[ "$(ls /opt/gh)" = "$gh_old" ] || fail "check mode changed /opt/gh: $(ls /opt/gh)"
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

# A machine Chrome was never installed on has no /opt/google-chrome. Check
# mode creates no directory, so it cannot download into one; it still
# previews the rest of the setup and leaves the directory uncreated.
sudo mv /opt/google-chrome "$out/google-chrome"
setup run2h.log --check ||
  { tail -n 40 "$out/run2h.log"; fail "check-mode run before Chrome is installed"; }
[ ! -e /opt/google-chrome ] || fail "check mode created /opt/google-chrome"
sudo mv "$out/google-chrome" /opt/google-chrome
recap run2h.log | grep -Eq 'unreachable=0 +failed=0' || {
  grep -A3 -E '^TASK \[Google Chrome' "$out/run2h.log" || true
  fail "check-mode run before Chrome is installed: $(recap run2h.log)"
}
grep -q 'TASK \[Base agent instructions\]' "$out/run2h.log" || fail "check-mode run before Chrome is installed stopped before the last task"
ok "check-mode run before Chrome is installed: $(recap run2h.log)"

for d in dev wt scratch; do [ -d "$HOME/$d" ] || fail "$HOME/$d missing"; done
ok "dev, wt and scratch under $HOME"
[ "$(git config --global user.email)" = agent@example.invalid ] || fail "git identity"
[ "$(git config --global user.name)" = "Test Agent" ] || fail "git identity"
ok "git identity"
[ "$(getent passwd agent | cut -d: -f7)" = /usr/bin/zsh ] || fail "login shell"
id -nG agent | grep -qw docker || fail "docker group"
ok "login shell zsh, in docker group"
[ "$(jq -r '."log-driver"' "$daemon")" = json-file ] || fail "daemon.json: log-driver is not json-file"
[ "$(jq -r '."log-opts"."max-size"' "$daemon")" = 50m ] || fail "daemon.json: log-opts.max-size is not 50m"
[ "$(jq '."log-opts"."max-file"' "$daemon")" = '"5"' ] || fail "daemon.json: log-opts.max-file is not the string \"5\""
[ "$(jq -c '[.debug, ."log-opts".labels]' "$daemon")" = '[true,"kept"]' ] || fail "daemon.json: an existing setting was dropped"
ok "Docker logs rotate: $(jq -c '."log-opts"' "$daemon"), existing keys kept"
[ "$(jq -r .autoMemoryEnabled "$settings")" = false ] || fail "memory not off"
[ "$(jq -r .env.EXISTING_SETTING "$settings")" = kept ] || fail "an existing setting was dropped"
[ "$(jq -r .env.DISABLE_UPDATES "$settings")" = 1 ] || fail "env.DISABLE_UPDATES is not 1"
[ "$(jq -r '.env | has("DISABLE_AUTOUPDATER")' "$settings")" = false ] || fail "env.DISABLE_AUTOUPDATER still in settings"
[ "$(jq -r .hasCompletedOnboarding "$HOME/.claude.json")" = true ] || fail "onboarding"
ok "Claude Code settings merged, existing key kept, DISABLE_AUTOUPDATER removed"

# Gateway client: the shim resolves first in every zsh, interactive or not.
shim() { zsh -i -c 'command -v claude' 2>/dev/null | tail -n 1; }
[ "$(shim)" = "$gw/current/claude" ] || fail "claude is $(shim), not the shim"
plain=$(zsh -c 'command -v claude')
[ "$plain" = "$gw/current/claude" ] || fail "in a non-interactive zsh, claude is $plain, not the shim"
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "current is not 1.0.0"
grep -qx 'prefix=' "$HOME/.config/redcoast-client/shim.env" || fail "shim.env has no empty prefix= line"
ok "gateway 1.0.0 installed, claude resolves to the shim in every zsh, shim.env has an empty prefix"

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
[ "$(shim)" = "$gw/current/claude" ] || fail "claude is $(shim) after a failed replacement"
ok "failed replacement of 2.0.0 put the old copy back, current still works"

setup run7.log || { tail -n 40 "$out/run7.log"; fail "reinstall of 2.0.0"; }
grep -q 'rebuilt' "$gw/2.0.0/claude" || fail "2.0.0 not reinstalled for the rebuilt release"
[ ! -e "$gw/2.0.0.old" ] || fail "old copy left behind"
ok "rebuilt 2.0.0 reinstalled"

# Every zsh sources the machine's own zshenv.local, which the setup leaves
# as it is. A fednet that is not its latest release (a stand-in binary here)
# is replaced by that release.
local_env=$HOME/.config/dataforge/zshenv.local
mkdir -p "${local_env%/*}"
echo 'export DATAFORGE_TEST_LOCAL=from-local' >"$local_env"
cp "$local_env" "$out/zshenv.local"
[ "$(zsh -c 'echo $DATAFORGE_TEST_LOCAL')" = from-local ] || fail "zshenv.local not sourced"
printf '#!/bin/sh\necho stand-in\n' >"$out/fednet"
sudo install -m 755 "$out/fednet" /usr/local/bin/fednet

# The personal zsh layer is on by default and can be switched off.
grep -q oh-my-zsh.sh "$HOME/.zshrc" || fail "personal layer missing by default"
setup run8.log -e zsh_personal=false || { tail -n 40 "$out/run8.log"; fail "zsh_personal=false"; }
cmp -s "$local_env" "$out/zshenv.local" || fail "the setup changed zshenv.local"
[ "$(zsh -c 'echo $DATAFORGE_TEST_LOCAL')" = from-local ] || fail "zshenv.local not sourced after a rerun"
ok "zshenv.local sourced and kept"
check_version "fednet replaced" "$(tag_of fednet run8.log)" /usr/local/bin/fednet version
if grep -q oh-my-zsh "$HOME/.zshrc"; then fail "personal layer still in .zshrc"; fi
[ "$(shim)" = "$gw/current/claude" ] || fail "without the personal layer, claude is $(shim)"
ok "zsh_personal=false drops the personal layer, the shim stays first"

echo "PASS"
