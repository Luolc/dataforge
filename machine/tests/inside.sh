#!/bin/sh
# Container side of the data machine setup test (run.sh starts it). Runs the
# setup from a clean image with no machine settings at all, then twice with
# them, checks the last run changes nothing and every tool is at its pinned
# version, then drives the gateway client install (from a fake release on the
# local disk; a tampered download must not replace it), the zshenv extension
# point, fednet's self-upgrade being left alone and the personal zsh switch.
set -eu
m=$HOME/machine
out=$HOME/out
mkdir -p "$out"
pin() { sed -n "s/^$1: *//p" "$m/versions.yml" | tr -d '"'; }
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

# A fake gateway release, laid out like the real one's download URLs and
# read through file://, so the setup never reaches GitHub.
release=$HOME/release
# stage <version> [<build>]: publishes a fake release v<version> and prints
# the digests of its client and shim. Each version or build has different
# file contents, as a real one would.
stage() {
  dir=$release/v$1
  rm -rf "$dir"
  mkdir -p "$dir"
  cp "$m/tests/fake-gateway/redcoast-client" "$dir/redcoast-client-linux-amd64"
  cp "$m/tests/fake-gateway/claude" "$dir/redcoast-client-shim-full"
  echo "# version $1 ${2:-}" >>"$dir/redcoast-client-shim-full"
  sha256sum "$dir/redcoast-client-linux-amd64" "$dir/redcoast-client-shim-full" | cut -d' ' -f1 | paste -sd' '
}
# setup <log name> <gateway version> "<client digest> <shim digest>" [more ansible options]
# setup_path, when set, is the PATH the setup runs with.
setup() {
  log=$out/$1
  gw_version=$2
  gw_client_sum=${3% *}
  gw_shim_sum=${3#* }
  shift 3
  PATH=${setup_path:-$PATH} "$m/bootstrap.sh" -e '{"git_user_name": "Test Agent", "git_user_email": "agent@example.invalid"}' \
    -e redcoast_client_release_url="file://$release" -e redcoast_client_version="$gw_version" \
    -e redcoast_client_sha256="$gw_client_sum" -e redcoast_client_shim_full_sha256="$gw_shim_sum" \
    -e redcoast_client_address=gateway.invalid:7801 \
    "$@" >"$log" 2>&1
}
# The installed programs have the digests in "<client digest> <shim digest>".
installed_is() { # <version> <digests>
  printf '%s  redcoast-client\n%s  claude\n' "${2% *}" "${2#* }" >"$out/expected"
  (cd "$gw/$1" && sha256sum --check --strict --quiet "$out/expected")
}
recap() { grep -E '^localhost +:' "$out/$1"; }

# A setting the gateway client might own; the setup must keep it.
settings=$HOME/.claude/settings.json
mkdir -p "$HOME/.claude"
echo '{"env": {"ANTHROPIC_BASE_URL": "http://gateway.invalid"}}' >"$settings"

v1=$(stage 1.0.0)
# A new machine runs the setup before any of its settings are in place: no
# gateway address, git identity or GitHub token. Only the fake gateway
# release is passed, so nothing reaches GitHub for it.
"$m/bootstrap.sh" -e redcoast_client_release_url="file://$release" -e redcoast_client_version=1.0.0 \
  -e redcoast_client_sha256="${v1% *}" -e redcoast_client_shim_full_sha256="${v1#* }" \
  >"$out/run0.log" 2>&1 || { tail -n 40 "$out/run0.log"; fail "run without machine settings"; }
grep -q 'No redcoast_client_address' "$out/run0.log" || fail "run0 did not report the missing gateway address"
[ ! -e "$HOME/.config/redcoast-client/shim.env" ] || fail "shim.env written without a gateway address"
[ -z "$(git config --global user.email)" ] || fail "git identity set without settings"
ok "run without machine settings: $(recap run0.log)"

setup run1.log 1.0.0 "$v1" || { tail -n 40 "$out/run1.log"; fail "first run"; }
ok "first run: $(recap run1.log)"
setup run2.log 1.0.0 "$v1" || { tail -n 40 "$out/run2.log"; fail "second run"; }
recap run2.log | grep -Eq 'changed=0 +unreachable=0 +failed=0' || {
  grep -A1 -E '^TASK ' "$out/run2.log" | grep -B1 -E '^changed:' || true
  fail "second run changed something: $(recap run2.log)"
}
ok "second run: $(recap run2.log)"

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
check_version direnv "$(dpkg-query -W -f '${Version}' direnv | sed 's/-.*//')" /usr/bin/direnv version
check_version fleet "fleet $(pin fleet_version)" /usr/local/bin/fleet --version
check_version fednet "v$(pin fednet_version)" /usr/local/bin/fednet version
check_version atb "atb $(pin atb_version)" /usr/local/bin/atb --version
check_version statute "statute $(pin statute_version)" /usr/local/bin/statute --version

for d in dev wt scratch; do [ -d "$HOME/$d" ] || fail "$HOME/$d missing"; done
ok "dev, wt and scratch under $HOME"
[ "$(git config --global user.email)" = agent@example.invalid ] || fail "git identity"
[ "$(git config --global user.name)" = "Test Agent" ] || fail "git identity"
ok "git identity"
[ "$(getent passwd agent | cut -d: -f7)" = /usr/bin/zsh ] || fail "login shell"
id -nG agent | grep -qw docker || fail "docker group"
ok "login shell zsh, in docker group"
[ "$(jq -r .autoMemoryEnabled "$settings")" = false ] || fail "memory not off"
[ "$(jq -r .env.ANTHROPIC_BASE_URL "$settings")" = http://gateway.invalid ] || fail "an existing setting was dropped"
[ "$(jq -r .hasCompletedOnboarding "$HOME/.claude.json")" = true ] || fail "onboarding"
ok "Claude Code settings merged, existing key kept"

# Gateway client: the shim resolves first in every zsh, interactive or not.
shim() { zsh -i -c 'command -v claude' 2>/dev/null | tail -n 1; }
gw=$HOME/.local/libexec/redcoast-client
[ "$(shim)" = "$gw/current/claude" ] || fail "claude is $(shim), not the shim"
plain=$(zsh -c 'command -v claude')
[ "$plain" = "$gw/current/claude" ] || fail "in a non-interactive zsh, claude is $plain, not the shim"
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "current is not 1.0.0"
grep -qx 'prefix=' "$HOME/.config/redcoast-client/shim.env" || fail "shim.env has no empty prefix= line"
ok "gateway 1.0.0 installed, claude resolves to the shim in every zsh, shim.env has an empty prefix"

installed_is 1.0.0 "$v1" || fail "1.0.0 does not have its pinned digests"
ok "gateway 1.0.0 matches its pins"

# A download that does not match its pin: the run stops, 1.0.0 stays. The
# shim is downloaded after the client, so the client, which matched, must not
# be installed either.
v2=$(stage 2.0.0)
echo tampered >>"$release/v2.0.0/redcoast-client-shim-full"
if setup run3.log 2.0.0 "$v2"; then fail "tampered download was accepted"; fi
grep -Eq 'item=redcoast-client-shim-full.*did not match' "$out/run3.log" || { tail -n 20 "$out/run3.log"; fail "run3 failed for another reason"; }
[ "$(readlink "$gw/current")" = 1.0.0 ] || fail "current moved after a failed install"
[ ! -e "$gw/2.0.0" ] || fail "2.0.0 installed from a tampered download"
ok "tampered 2.0.0 refused, 1.0.0 still current"

# The same version published intact installs and switches.
v2=$(stage 2.0.0)
setup run4.log 2.0.0 "$v2" || { tail -n 40 "$out/run4.log"; fail "intact 2.0.0"; }
[ "$(readlink "$gw/current")" = 2.0.0 ] || fail "current is not 2.0.0"
[ -x "$gw/1.0.0/claude" ] || fail "1.0.0 removed"
ok "intact 2.0.0 installed and current"

# A new pin for the installed version reinstalls it. When the last step,
# moving the new copy into place, fails (an injected mv failure), the old
# copy is put back and `current` still works.
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
if setup run5.log 2.0.0 "$v2b"; then fail "injected mv failure was not reported"; fi
setup_path=
grep -q 'injected mv failure' "$out/run5.log" || { tail -n 20 "$out/run5.log"; fail "run5 failed for another reason"; }
[ "$(readlink "$gw/current")" = 2.0.0 ] || fail "current moved after a failed replacement"
cmp -s "$gw/2.0.0/claude" "$out/claude-2.0.0-before" || fail "old 2.0.0 not put back"
installed_is 2.0.0 "$v2" || fail "restored 2.0.0 does not match its old pins"
[ "$(shim)" = "$gw/current/claude" ] || fail "claude is $(shim) after a failed replacement"
ok "failed replacement of 2.0.0 put the old copy back, current still works"

setup run6.log 2.0.0 "$v2b" || { tail -n 40 "$out/run6.log"; fail "reinstall of 2.0.0"; }
grep -q 'rebuilt' "$gw/2.0.0/claude" || fail "2.0.0 not reinstalled for the new pin"
[ ! -e "$gw/2.0.0.old" ] || fail "old copy left behind"
ok "new pin for 2.0.0 reinstalled it"

# Every zsh sources the machine's own zshenv.local, which the setup leaves
# as it is. fednet, upgraded in place by itself (a stand-in binary here),
# is not taken back to the pinned version.
local_env=$HOME/.config/dataforge/zshenv.local
mkdir -p "${local_env%/*}"
echo 'export DATAFORGE_TEST_LOCAL=from-local' >"$local_env"
cp "$local_env" "$out/zshenv.local"
[ "$(zsh -c 'echo $DATAFORGE_TEST_LOCAL')" = from-local ] || fail "zshenv.local not sourced"
printf '#!/bin/sh\necho upgraded\n' >"$out/fednet"
sudo install -m 755 "$out/fednet" /usr/local/bin/fednet

# The personal zsh layer is on by default and can be switched off.
grep -q oh-my-zsh.sh "$HOME/.zshrc" || fail "personal layer missing by default"
setup run7.log 2.0.0 "$v2b" -e zsh_personal=false || { tail -n 40 "$out/run7.log"; fail "zsh_personal=false"; }
cmp -s "$local_env" "$out/zshenv.local" || fail "the setup changed zshenv.local"
cmp -s /usr/local/bin/fednet "$out/fednet" || fail "the setup replaced an upgraded fednet"
[ "$(zsh -c 'echo $DATAFORGE_TEST_LOCAL')" = from-local ] || fail "zshenv.local not sourced after a rerun"
ok "zshenv.local sourced and kept, upgraded fednet left alone"
if grep -q oh-my-zsh "$HOME/.zshrc"; then fail "personal layer still in .zshrc"; fi
[ "$(shim)" = "$gw/current/claude" ] || fail "without the personal layer, claude is $(shim)"
ok "zsh_personal=false drops the personal layer, the shim stays first"

echo "PASS"
