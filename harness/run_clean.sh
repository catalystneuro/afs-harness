#!/usr/bin/env bash
# Run one attempt of one benchmark cell (lane, problem, rep) in an isolated environment.
#
# Isolation, per attempt:
#   - a fresh working directory under $AFS_SWEEP_DIR, which must not be inside a git repo
#     and must have no CLAUDE.md in any ancestor directory
#   - a fresh CLAUDE_CONFIG_DIR holding only the three vendored skills and a settings file
#     that turns auto-memory and claude.ai connectors off
#   - a fresh HOME, so nothing under the real ~/.claude or ~/.config is reachable by default
#   - a fresh Python venv built from harness/requirements.lock, so packages an agent installs
#     do not carry over to the next run
#   - no MCP servers (--strict-mcp-config with an empty config)
# Nothing is ever deleted or overwritten: each attempt gets its own attempt-N directory.
#
# Usage: run_clean.sh <lane> <problem> <rep>
# Required environment:
#   AFS_SWEEP_DIR      root of this sweep, e.g. ~/afs-sweeps/confirmatory-2026-10
#   AFS_CLI_VERSION    the pinned Claude Code version, e.g. 2.1.296; the run refuses otherwise
#   CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY, for Anthropic lanes
#   OPENROUTER_API_KEY and a running proxy, for open-weights lanes (see lanes.tsv)
# Optional:
#   AFS_BG_WAIT_MS     how long headless mode waits for background tasks at exit (default 1 h)
#   AFS_PYTHON         interpreter used to build each venv (default: python3.12 on PATH)
#   AFS_SMOKE=1        replace the task with a one-word reply and a $1 cap, to check isolation
#                      cheaply; the attempt is written under <sweep>/smoke/ instead of runs/
# Prints one tab-separated status line: lane problem rep attempt status detail
set -u
HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lane="${1:?lane}"; problem="${2:?problem}"; rep="${3:?rep}"
: "${AFS_SWEEP_DIR:?set AFS_SWEEP_DIR}" "${AFS_CLI_VERSION:?set AFS_CLI_VERSION}"
sweep="$(mkdir -p "$AFS_SWEEP_DIR" && cd "$AFS_SWEEP_DIR" && pwd)"

fail() { printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$lane" "$problem" "$rep" "${attempt:-0}" "harness_error" "$1"; exit 3; }

# ---- preflight: the environment itself ------------------------------------------------
v="$(claude --version 2>/dev/null | awk '{print $1}')"
[ "$v" = "$AFS_CLI_VERSION" ] || fail "claude version $v != pinned $AFS_CLI_VERSION"
git -C "$sweep" rev-parse --show-toplevel >/dev/null 2>&1 && fail "sweep dir is inside a git repo"
d="$sweep"; while [ "$d" != "/" ]; do [ -e "$d/CLAUDE.md" ] && fail "found $d/CLAUDE.md above the sweep dir"; d="$(dirname "$d")"; done

row="$(awk -F'\t' -v k="$lane" '$1==k' "$HARNESS/lanes.tsv")"
[ -n "$row" ] || fail "unknown lane $lane"
model="$(cut -f2 <<<"$row")"; base_url="$(cut -f3 <<<"$row")"; budget="$(cut -f4 <<<"$row")"
effort="$(cut -f5 <<<"$row")"
[ -n "$effort" ] || fail "lane $lane has no effort level"
prompt="$(awk -F'\t' -v k="$problem" '$1==k{print $2}' "$HARNESS/problems.tsv")"
[ -n "$prompt" ] || fail "unknown problem $problem"
kind=runs
# credentials, checked before anything is created
if [ -n "$base_url" ]; then
  [ -n "${OPENROUTER_API_KEY:-}" ] || fail "open-weights lanes need OPENROUTER_API_KEY and a running proxy"
  auth_env=("ANTHROPIC_BASE_URL=$base_url" "ANTHROPIC_AUTH_TOKEN=proxy" "ANTHROPIC_API_KEY=")
elif [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then auth_env=("CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN")
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then auth_env=("ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY")
else fail "set CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) or ANTHROPIC_API_KEY"; fi
if [ "${AFS_SMOKE:-0}" = 1 ]; then prompt="Reply with the single word: ready"; budget=1; kind=smoke; fi

# ---- fresh attempt directories ----------------------------------------------------------
cell="$sweep/$kind/$lane/$problem-$rep"
mkdir -p "$cell"
attempt=1; while [ -e "$cell/attempt-$attempt" ]; do attempt=$((attempt + 1)); done
work="$cell/attempt-$attempt"
priv="$sweep/.private/$kind/$lane/$problem-$rep/attempt-$attempt"   # config, home, venv: outside the agent's cwd
mkdir -p "$work" "$priv/config/skills" "$priv/home"

cp -R "$HARNESS/skills/analyzing-dandi-datasets" "$HARNESS/skills/using-nemos" "$HARNESS/skills/using-pynapple" "$priv/config/skills/"
bash "$HARNESS/write_settings.sh" "$priv/config"

py="${AFS_PYTHON:-python3.12}"
uv venv -q --python "$py" "$priv/venv" >/dev/null 2>&1 || fail "venv creation failed"
VIRTUAL_ENV="$priv/venv" uv pip install -q -r "$HARNESS/requirements.lock" >/dev/null 2>&1 || fail "venv install failed"

# ---- environment for the agent ----------------------------------------------------------
env_vars=(
  "HOME=$priv/home"
  "JUPYTER_DATA_DIR=$priv/home/jupyter-data"
  "CLAUDE_CONFIG_DIR=$priv/config"
  "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1"
  "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1"
  "ENABLE_CLAUDEAI_MCP_SERVERS=false"
  "DISABLE_AUTOUPDATER=1"
  "DISABLE_TELEMETRY=1"
  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
  "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=${AFS_BG_WAIT_MS:-3600000}"
  "MPLBACKEND=Agg"
  "VIRTUAL_ENV=$priv/venv"
  "PATH=$priv/venv/bin:/usr/local/bin:/usr/bin:/bin:$(dirname "$(command -v claude)"):$(dirname "$(command -v uv)")"
)
env_vars+=("${auth_env[@]}")

suffix="$(cat "$HARNESS/autonomy_suffix.txt")"
[ "$kind" = smoke ] && suffix=""
addendum="$(cat "$HARNESS/SYSTEM_ADDENDUM.md")"
# The credential scrub (CLAUDE_CODE_SUBPROCESS_ENV_SCRUB) strips the token from the agent's commands
# and runs them in Claude Code's sandbox (bubblewrap on Linux). It forces permission mode `default`,
# so this list and --permission-prompts none decide what runs: any other tool is refused. That
# leaves out the tools that only make sense with a human watching or that schedule work past the
# session (Monitor, ScheduleWakeup, cron, worktrees).
# The sandbox lets commands write only to the working directory, a temp directory, and directories
# passed with --add-dir, so the agent's home and venv are added; otherwise `pip install` and
# Jupyter fail with a read-only file system. Even then the sandbox keeps some paths in the home
# directory read-only, ~/.local among them, so JUPYTER_DATA_DIR moves Jupyter's data out of
# ~/.local/share. (The sandbox drops XDG_DATA_HOME, so that variable cannot do it.)
allowed="Bash,Read,Write,Edit,Glob,Grep,NotebookEdit,WebFetch,WebSearch,Skill,Agent,TodoWrite"

start="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
( cd "$work" && env -i "${env_vars[@]}" claude -p "${prompt}${suffix}" \
    --model "$model" --effort "$effort" \
    --permission-prompts none --allowedTools "$allowed" \
    --add-dir "$priv/home" "$priv/venv" \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
    --setting-sources user \
    --append-system-prompt "$addendum" \
    --output-format stream-json --verbose \
    --max-budget-usd "$budget" \
    > transcript.jsonl 2> stderr.log )
code=$?
end="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- provenance and outcome -------------------------------------------------------------
bash "$HARNESS/clean_placeholders.sh" "$work"
VIRTUAL_ENV="$priv/venv" uv pip freeze > "$priv/pip-freeze.txt" 2>/dev/null
python3 "$HARNESS/provenance.py" \
  --work "$work" --priv "$priv" --harness "$HARNESS" \
  --lane "$lane" --model "$model" --problem "$problem" --rep "$rep" --attempt "$attempt" \
  --cli "$v" --start "$start" --end "$end" --exit-code "$code" --budget "$budget" --effort "$effort"
