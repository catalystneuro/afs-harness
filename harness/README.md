# Clean Harness for the Confirmatory Sweep

This directory replaces the launchers in `runs-2026-07-21-exploratory/` for any run that will be reported as confirmatory. The exploratory sweep ran in the author's everyday Claude Code environment, and `audit/CONTAMINATION.md` documents what that allowed: a shared auto-memory directory that agents wrote dataset recipes into and later runs read, reruns that started in directories holding a killed attempt's work, personal MCP connectors and skills, a global CLAUDE.md, a grader subagent visible to the agents, six CLI versions, and a prompt that named a skill (`using-pynapple`) that was not installed for most runs. This harness removes each of those channels and checks, from every transcript, that it did.

## What Each Attempt Gets

Each attempt of each (lane, problem, rep) cell runs in its own `attempt-N` directory under `$AFS_SWEEP_DIR/runs/`, which must sit outside any git repository with no CLAUDE.md in any parent directory. The agent's config directory, home directory, and Python environment are created fresh per attempt under `$AFS_SWEEP_DIR/.private/`, outside the agent's working directory. The config directory holds only the three skills vendored in `skills/` (pinned to the commit in `skills/SOURCE.txt`) and a settings file that disables auto-memory and claude.ai connectors. The Python environment is built from `requirements.lock` with uv, so packages one agent installs never reach another. MCP is disabled with `--strict-mcp-config` and an empty config, and the tools that schedule work past the session (Monitor, ScheduleWakeup, cron) are disallowed.

About twenty exploratory runs, including seven of Sonnet 5's ten failures, ended while the agent waited on a background job, because a headless session exits when the agent stops responding. The harness sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` to one hour, and `SYSTEM_ADDENDUM.md` now tells every agent that background processes are stopped when the session ends. Both apply equally to all lanes.

The output contract in `SYSTEM_ADDENDUM.md` differs from the exploratory one in two ways. The final jupytext script must regenerate every figure and number from the archive when run from a clean copy of the directory, and it must write a `results.json` holding every number the README reports. The first makes re-execution meaningful (most exploratory runs split the work across many scripts and pickled caches), and the second makes claim checking exact (see `reexecute.py`).

## Files

| File | Purpose |
|---|---|
| `run_clean.sh` | Runs one attempt of one cell with the isolation above, then calls `provenance.py`. |
| `write_settings.sh` | Writes each attempt's settings file, used by `run_clean.sh` and the workflow's preflight. |
| `clean_placeholders.sh` | Removes the empty placeholder files (`.env`, `package.json`, `yarn.lock`, and so on) that Claude Code's sandbox leaves in the working directory. `test_clean_placeholders.sh` tests it. |
| `provenance.py` | Writes `provenance.json` (versions, hashes of the addendum, skills, and lockfile, the init event, the result), checks isolation from the init event, and classifies the outcome. |
| `test_provenance.py` | Tests how `provenance.py` classifies outcomes, including a session cut off by a usage limit. |
| `sweep.py` | Shuffles all cells with a fixed seed, runs them in a pool, and retries only infrastructure errors, identically for every lane. Logs every attempt. |
| `reexecute.py` | Checks README numbers against the run's outputs, or re-executes the main script in a scratch copy (`--source notebook` or `clean`). `--corrupt` is the deliberate-corruption self-test. |
| `lanes.tsv`, `problems.tsv` | Models and prompts. |
| `SYSTEM_ADDENDUM.md`, `autonomy_suffix.txt` | What every agent is told, apart from the one-sentence prompt. |
| `skills/`, `requirements.lock` | The pinned skills and Python environment. |

## Running

Credentials come from the environment, not the keychain, because each attempt has a fresh config directory. For a subscription, run `claude setup-token` once and export the token it prints as `CLAUDE_CODE_OAUTH_TOKEN`. For API billing, export `ANTHROPIC_API_KEY`.

```bash
export AFS_SWEEP_DIR=~/afs-sweeps/confirmatory-2026-10
export AFS_CLI_VERSION=2.1.296          # refuse to run on any other version
export CLAUDE_CODE_OAUTH_TOKEN=...      # from `claude setup-token`

# Isolation smoke test: one cheap call per lane, fails loudly on any violation
python harness/sweep.py --smoke --lanes opus-5-5-low opus-5-5-high haiku-5-5-low haiku-5-5-high

# Full sweep
python harness/sweep.py --lanes opus-5-5-low opus-5-5-high haiku-5-5-low haiku-5-5-high --problems all --reps 5 --par 4 --seed 1
```

Confirmatory sweeps run in GitHub Actions through `.github/workflows/sweep.yml`, one cell per job on a fresh runner, with the repository checkout deleted before the agent starts. The workflow reads the token from the `CLAUDE_CODE_OAUTH_TOKEN` secret and runs a one-turn preflight before each cell. The token is stripped from the agent's commands, which run in Claude Code's sandbox (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`). That sandbox forces permission mode `default`, so `run_clean.sh` lists the allowed tools explicitly, and `provenance.py` treats any other permission mode as an isolation violation.

```bash
gh workflow run sweep.yml -f lane=opus-5-5-high -f problems=placefields -f reps=1
gh run download <run-id> -D ~/afs-sweeps/confirmatory-2026-10
```

Open-weights lanes need a proxy, which the workflow does not start, so they still run locally.

## Known Gaps

`builtin_skills.txt` lists the skills bundled with the pinned CLI, taken from a smoke test with an empty config directory, and `provenance.py` refuses to run against a different CLI version until it is regenerated. Web search and fetch remain available to the agents by design, since dataset discovery is part of the task; every search and fetch is in the transcript for the retrieval audit. `/tmp/remfile_cache` is shared across attempts, but it holds only raw data bytes from the archive. A container would add filesystem isolation beyond the fresh HOME; the harness is written so that `run_clean.sh` can be called unchanged inside one.
