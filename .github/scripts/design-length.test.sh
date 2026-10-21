#!/bin/sh
# Arms of design-length.sh: at the limit, one line over, no file, two files with
# either one over, and the defaults in a tree with and without docs/design-viz.md and docs/design-share.md.
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
seq 200 >"$tmp/200.md"
seq 201 >"$tmp/201.md"
status=0
expect() { # <want exit> <file...>
  want=$1
  shift
  "$here/design-length.sh" "$@" 2>/dev/null
  got=$?
  [ "$got" -eq "$want" ] || { echo "FAIL: $*: exit $got, want $want"; status=1; }
}
expect_tree() { # <want exit> <design.md lines> [design-viz.md lines [design-share.md lines]]
  want=$1
  rm -rf "$tmp/tree"
  mkdir -p "$tmp/tree/docs"
  seq "$2" >"$tmp/tree/docs/design.md"
  [ "$#" -lt 3 ] || seq "$3" >"$tmp/tree/docs/design-viz.md"
  [ "$#" -lt 4 ] || seq "$4" >"$tmp/tree/docs/design-share.md"
  (cd "$tmp/tree" && "$here/design-length.sh" 2>/dev/null)
  got=$?
  [ "$got" -eq "$want" ] || { echo "FAIL: defaults $*: exit $got, want $want"; status=1; }
}
expect 0 "$tmp/200.md"
expect 1 "$tmp/201.md"
expect 4 "$tmp/missing.md"
expect 0 "$tmp/200.md" "$tmp/200.md"
expect 1 "$tmp/200.md" "$tmp/201.md"
expect 1 "$tmp/201.md" "$tmp/200.md"
expect_tree 0 200
expect_tree 1 201
expect_tree 0 200 200
expect_tree 1 200 201
expect_tree 0 200 200 200
expect_tree 1 200 200 201
[ "$status" -eq 0 ] && echo "design-length: ok"
exit "$status"
