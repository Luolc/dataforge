#!/bin/sh
# Host side of the data machine setup test. Builds a clean Ubuntu 24.04 image
# (cached), then runs inside.sh in it with machine/ mounted. Usage:
# machine/tests/run.sh. Needs docker and network access (the setup downloads
# every tool). Set DATAFORGE_MACHINE_IMAGE to use an image built elsewhere
# (CI) instead of building one here. Set DATAFORGE_MACHINE_APT_CACHE to a
# directory to keep the downloaded apt packages there across runs; apt still
# installs every one of them.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
machine=$(cd "$here/.." && pwd)

image=${DATAFORGE_MACHINE_IMAGE:-}
if [ -z "$image" ]; then
  # One tag per checkout, so worktrees testing at the same time do not clash.
  image=dataforge-machine-test-$(printf %s "$here" | sha256sum | cut -c1-8)
  docker build -q -t "$image" "$here" >/dev/null
fi
apt_cache=${DATAFORGE_MACHINE_APT_CACHE:-}
docker run --rm ${apt_cache:+-v "$apt_cache:/var/cache/apt/archives"} \
  -v "$machine:/home/agent/machine:ro" \
  "$image" sh /home/agent/machine/tests/inside.sh
