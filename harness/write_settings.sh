#!/usr/bin/env bash
# Write the settings file for one attempt's fresh config directory. Used by run_clean.sh and by the
# workflow's preflight, so that both run with the same settings.
# Usage: write_settings.sh <config_dir>
set -eu
config="${1:?config dir}"
mkdir -p "$config"
cat > "$config/settings.json" <<JSON
{
  "autoMemoryEnabled": false,
  "disableClaudeAiConnectors": true,
  "includeCoAuthoredBy": false
}
JSON
