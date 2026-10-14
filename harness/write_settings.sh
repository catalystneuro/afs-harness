#!/usr/bin/env bash
# Write the settings file for one attempt's fresh config directory. Used by run_clean.sh and by the
# workflow's preflight, so that both run with the same settings.
#
# The credential scrub runs every shell command in Claude Code's sandbox, which by default lets
# commands write only to the working directory and a temp directory. The agent's fresh home
# directory and Python environment are opened for writing, so that `pip install` and Jupyter work
# as they would outside the sandbox. The config directory itself stays protected by the sandbox.
# Usage: write_settings.sh <config_dir> <home_dir> <venv_dir>
set -eu
config="${1:?config dir}"; home="${2:?home dir}"; venv="${3:?venv dir}"
mkdir -p "$config"
cat > "$config/settings.json" <<JSON
{
  "autoMemoryEnabled": false,
  "disableClaudeAiConnectors": true,
  "includeCoAuthoredBy": false,
  "sandbox": {
    "filesystem": {
      "allowWrite": ["$home", "$venv"]
    }
  }
}
JSON
