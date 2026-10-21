#!/bin/sh
# The design files stay short (AGENTS.md, L53).
# Usage: design-length.sh [file...]
# Without a file, checks docs/design.md, and docs/design-viz.md and docs/design-share.md when they exist.
# Exit 0 when every file has 200 lines or fewer, 1 when one has more, 4 when one
# cannot be read.
set -u
if [ "$#" -eq 0 ]; then
  set -- docs/design.md
  for extra in docs/design-viz.md docs/design-share.md; do
    [ -e "$extra" ] && set -- "$@" "$extra"
  done
fi
max=200
status=0
for file in "$@"; do
  [ -r "$file" ] || { echo "cannot read $file" >&2; exit 4; }
  lines=$(wc -l <"$file")
  if [ "$lines" -gt "$max" ]; then
    echo "$file has $lines lines, more than $max" >&2
    status=1
  fi
done
exit "$status"
