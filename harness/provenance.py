"""Write provenance.json for one attempt, verify its isolation, and classify its outcome.

Called by run_clean.sh after the agent exits. The isolation check reads the transcript's
init event and fails the attempt (status `isolation_violation`) if the agent could see
anything beyond the declared environment: skills other than the three vendored ones, any
MCP server, an auto-memory path, or a project agent.

Outcome classes:
  success               the session ended with a successful result event
  infra_error           empty transcript, API error, overload, rate or usage limit, or a session that
                        ended while retrying a failed API call; eligible for retry
  agent_ended           any other ending (budget cap, max turns, error during execution);
                        kept and scored, never retried
  isolation_violation   the environment was not what was declared; the attempt is invalid
Prints one tab-separated status line: lane problem rep attempt status detail
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXPECTED_SKILLS = {"analyzing-dandi-datasets", "using-nemos", "using-pynapple"}
INFRA_MARKERS = ("overloaded", "rate_limit", "rate limit", "api_error", "529", "503", "502",
                 "ECONNRESET", "socket hang up", "issue with the selected model",
                 "usage limit", "limit reached", "hit your limit")
# A subscription's usage limit ends the session with an ordinary assistant message, for example
# "You've hit your session limit · resets 4:40pm (UTC)", not with a failed API call.
USAGE_LIMIT_RE = re.compile(r"hit your (?:\w+ )?limit", re.I)
RESET_RE = re.compile(r"resets (\d{1,2})(?::(\d{2}))?\s*([ap]m) \(UTC\)", re.I)


def seconds_until_reset(text, now=None):
    """Seconds until the usage-limit reset named in text ("resets 4:40pm (UTC)"), or None."""
    m = RESET_RE.search(text or "")
    if not m:
        return None
    now = now or datetime.now(timezone.utc)
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    reset = now.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return (reset - now).total_seconds()


def ended_on_api_retry(events):
    """True when the last event before the result is a failed API call that Claude Code was
    retrying, which is how a session ends when the subscription's usage limit or a rate limit
    runs out mid-run, whatever the wording of the final error."""
    before_result = [e for e in events if e.get("type") != "result"]
    return bool(before_result) and before_result[-1].get("type") == "system" and \
        before_result[-1].get("subtype") == "api_retry"


# The sandbox problems name a dandiset on the DANDI sandbox that holds one session of a public dandiset.
# Tool calls that mention the public source dandiset are recorded so that a reviewer can check which
# dandiset the agent analyzed. A mention alone (a comment that credits the source) is common, so these
# are not scored automatically.
CONTROL_SOURCES = {"grid-sandbox": "000582", "auditory-sandbox": "000986"}


def source_mentions(events, problem):
    source = next((v for k, v in CONTROL_SOURCES.items() if problem.startswith(k)), None)
    if source is None:
        return []
    out = []
    for e in events:
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                text = json.dumps(block.get("input", {}))
                at = text.find(source)
                if at >= 0:
                    out.append({"tool": block.get("name"), "context": text[max(0, at - 120):at + 120]})
    return out


def classify(events, init, result, stderr, exit_code=0):
    result_text = str(result.get("result") or "")
    if not events or not init:
        return "infra_error", "empty transcript"
    if exit_code in (124, 137) and not result:
        # Stopped by run_clean.sh's wall-clock limit: the agent was still working.
        return "agent_ended", "time limit"
    if result.get("subtype") == "success" and not result.get("is_error"):
        return "success", f"{result.get('num_turns')} turns"
    if any(m.lower() in (stderr + result_text).lower() for m in INFRA_MARKERS) or result.get("subtype") is None \
            or USAGE_LIMIT_RE.search(result_text):
        return "infra_error", (result_text or stderr)[-200:].replace("\n", " ")
    if ended_on_api_retry(events):
        return "infra_error", "ended while retrying a failed API call: " + result_text[-150:].replace("\n", " ")
    return "agent_ended", str(result.get("subtype"))


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob("*")):
        if p.is_file() and p.name != ".DS_Store":
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_events(path):
    events = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return events


def main():
    ap = argparse.ArgumentParser()
    for k in ("work", "priv", "harness", "lane", "model", "problem", "rep", "attempt", "cli",
              "start", "end", "exit-code", "budget", "effort", "memory-max", "oom-kills"):
        ap.add_argument(f"--{k}", required=True)
    a = ap.parse_args()
    work, priv, harness = Path(a.work), Path(a.priv), Path(a.harness)
    events = read_events(work / "transcript.jsonl")
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})

    violations = []
    skills = set(init.get("skills") or [])
    builtin_file = harness / "builtin_skills.txt"
    lines = builtin_file.read_text().splitlines() if builtin_file.exists() else []
    builtin = {l.strip() for l in lines if l.strip() and not l.startswith("#")}
    pinned_for = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("# cli_version:")), None)
    if pinned_for and pinned_for != a.cli:
        violations.append(f"builtin_skills.txt is for CLI {pinned_for}, not {a.cli}; regenerate it")
    extra = skills - EXPECTED_SKILLS - builtin
    missing = EXPECTED_SKILLS - skills
    if missing and init:
        violations.append(f"missing skills {sorted(missing)}")
    if extra:
        violations.append(f"extra user skills {sorted(extra)}")
    if init.get("mcp_servers"):
        violations.append(f"mcp servers {[m.get('name') for m in init['mcp_servers']]}")
    if (init.get("memory_paths") or {}).get("auto"):
        violations.append("auto-memory path present")
    agents = set(init.get("agents") or [])
    builtin_agents = {"claude", "Explore", "general-purpose", "Plan", "statusline-setup", "claude-code-guide"}
    if agents - builtin_agents:
        violations.append(f"extra agents {sorted(agents - builtin_agents)}")
    # The credential scrub forces permission mode `default`; any other mode means it was not active.
    if init and init.get("permissionMode") != "default":
        violations.append(f"permission mode {init.get('permissionMode')}, not default (credential scrub off)")
    if init and init.get("claude_code_version") != a.cli:
        violations.append(f"init reports CLI {init.get('claude_code_version')}")
    fallbacks = [e for e in events if e.get("type") == "system" and e.get("subtype") == "model_refusal_fallback"]
    # Any tool call that reaches this study's own repository (earlier agents' solutions, grades,
    # the rubric) contaminates the run. Checked in tool inputs, which covers WebFetch, WebSearch,
    # and shell commands such as curl or git clone.
    study_repo_hits = 0
    for e in events:
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and \
                    "agent-foundational-studies" in json.dumps(block.get("input", {})):
                study_repo_hits += 1
    usage = result.get("modelUsage") or {}
    # a model that produced substantial output; for a very short session, any model that produced output
    served = sorted(k for k, v in usage.items() if (v or {}).get("outputTokens", 0) > 500) or \
        sorted(k for k, v in usage.items() if (v or {}).get("outputTokens", 0) > 0)

    stderr = (work / "stderr.log").read_text(errors="replace") if (work / "stderr.log").exists() else ""
    if violations:
        status, detail = "isolation_violation", "; ".join(violations)
    else:
        status, detail = classify(events, init, result, stderr, int(a.exit_code))
    if study_repo_hits:
        detail += f"; accessed this study's repository in {study_repo_hits} tool calls"
    if fallbacks:
        detail += f"; model fallback {fallbacks[0].get('original_model')}->{fallbacks[0].get('fallback_model')}"
    if int(a.oom_kills):
        detail += f"; {a.oom_kills} process(es) killed at the memory cap"
    if served and a.model not in served:
        detail += f"; served {served}"

    prov = {
        "lane": a.lane, "model_requested": a.model, "models_served": served,
        "problem": a.problem, "rep": a.rep, "attempt": int(a.attempt),
        "start_utc": a.start, "end_utc": a.end, "exit_code": int(a.exit_code),
        "status": status, "detail": detail, "isolation_violations": violations,
        "study_repo_tool_calls": study_repo_hits,
        "source_dandiset_mentions": source_mentions(events, a.problem),
        "cli_version": a.cli, "budget_usd": float(a.budget), "effort": a.effort,
        "memory_max_bytes": a.memory_max, "oom_kills": int(a.oom_kills),
        "result": {k: result.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms",
                                              "total_cost_usd", "stop_reason", "usage")},
        "init": {k: init.get(k) for k in ("model", "claude_code_version", "cwd", "tools", "skills",
                                          "mcp_servers", "agents", "plugins", "memory_paths", "permissionMode",
                                          "per_turn_effort_active")},
        "hashes": {
            "system_addendum": file_hash(harness / "SYSTEM_ADDENDUM.md"),
            "autonomy_suffix": file_hash(harness / "autonomy_suffix.txt"),
            "problems_tsv": file_hash(harness / "problems.tsv"),
            "skills_tree": tree_hash(harness / "skills"),
            "requirements_lock": file_hash(harness / "requirements.lock"),
            "pip_freeze": file_hash(priv / "pip-freeze.txt") if (priv / "pip-freeze.txt").exists() else None,
            "prompt_file": file_hash(priv / "prompt.md") if (priv / "prompt.md").exists() else None,
        },
    }
    (work / "provenance.json").write_text(json.dumps(prov, indent=1, default=str))
    print("\t".join([a.lane, a.problem, a.rep, a.attempt, status, detail]))


if __name__ == "__main__":
    main()
