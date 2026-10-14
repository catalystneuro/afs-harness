#!/usr/bin/env bash
# Test for clean_placeholders.sh: empty placeholders go, agent files and non-empty files stay.
# Usage: bash harness/test_clean_placeholders.sh
set -eu
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
d="$(mktemp -d)"; trap 'rm -rf "$d"' EXIT
touch "$d/.env" "$d/.env.test.local" "$d/yarn.lock" "$d/bunfig.toml" "$d/empty_result.txt"
echo '{"name": "x"}' > "$d/package.json"
echo "# results" > "$d/README.md"
bash "$here/clean_placeholders.sh" "$d"
for gone in .env .env.test.local yarn.lock bunfig.toml; do
  [ ! -e "$d/$gone" ] || { echo "FAIL: $gone was not removed"; exit 1; }
done
for kept in package.json README.md empty_result.txt; do
  [ -e "$d/$kept" ] || { echo "FAIL: $kept was removed"; exit 1; }
done
echo "ok"
