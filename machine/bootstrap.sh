#!/bin/sh
# Entry point of the data machine setup (machine/README.md). Run it as the agent user,
# who needs sudo; run it again after pulling to upgrade or repair, since every
# step is idempotent. It installs uv, then runs playbook.yml through uv with
# a local connection.
#
# Usage: machine/bootstrap.sh [ansible-playbook options]
# For example `-e zsh_personal=false`. Settings that belong to one machine
# (git identity, token location) go in ~/.config/dataforge/setup.yml, which is
# passed to Ansible when it exists.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
pin() { sed -n "s/^$1: *//p" "$here/versions.yml"; }

# Ansible's modules run on this machine's python3; its apt module needs
# python3-apt. curl fetches uv below.
missing=
# shellcheck disable=SC2046 # one word per package
for pkg in $(pin ubuntu_bootstrap_packages); do
  dpkg-query -W -f '${Status}' "$pkg" 2>/dev/null | grep -q 'ok installed' || missing="$missing $pkg"
done
if [ -n "$missing" ]; then
  sudo apt-get update -q
  # shellcheck disable=SC2086 # one word per package
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends $missing
fi

uv_version=$(pin uv_version)
uv=$HOME/.local/bin/uv
if [ "$("$uv" --version 2>/dev/null | cut -d' ' -f2)" != "$uv_version" ]; then
  echo "bootstrap: installing uv $uv_version"
  tmp=$(mktemp -d)
  tarball=$tmp/uv.tar.gz
  curl -fsSL -o "$tarball" \
    "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-x86_64-unknown-linux-gnu.tar.gz"
  echo "$(pin uv_sha256)  $tarball" | sha256sum -c --quiet -
  tar -xzf "$tarball" -C "$tmp"
  mkdir -p "$HOME/.local/bin"
  install -m 755 "$tmp/uv-x86_64-unknown-linux-gnu/uv" "$tmp/uv-x86_64-unknown-linux-gnu/uvx" "$HOME/.local/bin/"
  rm -rf "$tmp"
fi

set -- -i localhost, -c local "$here/playbook.yml" "$@"
local_settings=$HOME/.config/dataforge/setup.yml
[ -f "$local_settings" ] && set -- -e "@$local_settings" "$@"
ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_INVENTORY_UNPARSED_WARNING=False \
  exec "$uv" tool run --python "$(pin ansible_python_version)" \
  --from "ansible-core==$(pin ansible_core_version)" ansible-playbook "$@"
