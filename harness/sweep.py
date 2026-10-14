"""Run a benchmark sweep through run_clean.sh in randomized order, with retries for
infrastructure failures only.

Every (lane, problem, rep) cell is shuffled with a fixed seed so that time-varying API
behavior is not confounded with lane or problem. A cell is retried only when its attempt
ends in `infra_error`, up to --max-attempts, and the same rule applies to every lane.
`agent_ended` attempts are final and are scored like any other. `isolation_violation`
stops the sweep, since it means the environment is wrong for every cell.

Every attempt is logged to <sweep>/attempts.tsv, and the plan to <sweep>/plan.tsv.

Usage:
    python harness/sweep.py --lanes opus-5 sonnet-5 --problems all --reps 5 --par 4 --seed 1
    python harness/sweep.py --smoke --lanes opus-5 haiku-4-5    # isolation check, one cheap call per lane
Requires AFS_SWEEP_DIR, AFS_CLI_VERSION, and credentials in the environment (see run_clean.sh).
"""

import argparse
import os
import random
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
LOCK = threading.Lock()


def read_tsv_keys(path):
    return [l.split("\t")[0] for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


def run_cell(lane, problem, rep, max_attempts, log, smoke, stop):
    env = dict(os.environ, AFS_SMOKE="1" if smoke else "0")
    for _ in range(max_attempts):
        if stop.is_set():
            return lane, problem, rep, "skipped", "sweep stopped"
        p = subprocess.run(["bash", str(HARNESS / "run_clean.sh"), lane, problem, rep],
                           capture_output=True, text=True, env=env)
        line = (p.stdout.strip().splitlines() or [f"{lane}\t{problem}\t{rep}\t?\tharness_error\t{p.stderr[-200:]}"])[-1]
        fields = line.split("\t") + [""] * 6
        status, detail = fields[4], fields[5]
        with LOCK:
            with open(log, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}\t{line}\n")
        print(line, flush=True)
        if status == "isolation_violation" or status == "harness_error":
            stop.set()
            return lane, problem, rep, status, detail
        if status != "infra_error":
            return lane, problem, rep, status, detail
    return lane, problem, rep, "infra_error_exhausted", detail


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lanes", nargs="+", required=True)
    ap.add_argument("--problems", nargs="+", default=["all"])
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--rep", type=int, help="run only this rep number (one matrix cell in Actions)")
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    sweep = Path(os.environ.get("AFS_SWEEP_DIR", "")).expanduser()
    if not str(sweep) or not os.environ.get("AFS_CLI_VERSION"):
        sys.exit("set AFS_SWEEP_DIR and AFS_CLI_VERSION")
    sweep.mkdir(parents=True, exist_ok=True)
    known_lanes = read_tsv_keys(HARNESS / "lanes.tsv")
    known_problems = read_tsv_keys(HARNESS / "problems.tsv")
    bad = [l for l in a.lanes if l not in known_lanes]
    if bad:
        sys.exit(f"unknown lanes {bad}; see lanes.tsv")
    problems = known_problems if a.problems == ["all"] else a.problems
    if a.smoke:
        problems, a.reps = [problems[0]], 1

    reps = [a.rep] if a.rep else range(1, a.reps + 1)
    cells = [(l, p, f"{r:02d}") for l in a.lanes for p in problems for r in reps]
    random.Random(a.seed).shuffle(cells)
    tag = "smoke" if a.smoke else "runs"
    (sweep / f"plan.{tag}.tsv").write_text(
        f"# seed={a.seed} created={datetime.now(timezone.utc).isoformat()}\n"
        + "".join(f"{i}\t{l}\t{p}\t{r}\n" for i, (l, p, r) in enumerate(cells)))
    log = sweep / f"attempts.{tag}.tsv"

    stop = threading.Event()
    outcomes = []
    with ThreadPoolExecutor(max_workers=a.par) as pool:
        futs = [pool.submit(run_cell, l, p, r, a.max_attempts, log, a.smoke, stop) for l, p, r in cells]
        for f in as_completed(futs):
            outcomes.append(f.result())
    counts = {}
    for *_, status, _ in outcomes:
        counts[status] = counts.get(status, 0) + 1
    print(f"done: {counts}")
    if stop.is_set():
        sys.exit("stopped early: an isolation violation or harness error needs attention before continuing")


if __name__ == "__main__":
    main()
