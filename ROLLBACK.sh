#!/usr/bin/env sh
set -eu
target="${1:-.}"
case "$target" in
  ""|"/"|".") echo "Pass an explicit deployment-copy path to remove." >&2; exit 2 ;;
esac
rm -rf -- "$target"
