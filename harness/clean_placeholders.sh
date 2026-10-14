#!/usr/bin/env bash
# Remove the empty placeholder files that Claude Code's sandbox leaves in the agent's working
# directory for the paths it protects from writes. Only empty files with these exact names are
# removed, so a file the agent wrote under one of these names is kept.
# Usage: clean_placeholders.sh <dir>
set -u
dir="${1:?dir}"
for name in .env .env.development .env.development.local .env.local .env.production \
    .env.production.local .env.test .env.test.local .gitmodules .npmrc .yarnrc .yarnrc.yml \
    bunfig.toml package.json package-lock.json pnpm-lock.yaml yarn.lock; do
  f="$dir/$name"
  if [ -f "$f" ] && [ ! -s "$f" ]; then rm -f "$f"; fi
done
