"""Check whether the numbers in a run's README are backed by its outputs.

For one run directory, this extracts every quantitative claim from README.md and
looks for each number in a body of evidence, classifying it as:

  output     the number appears in the evidence (at the README's precision)
  parameter  the number does not appear in the evidence but is a literal in the code
             (a bin size, a threshold), so it is a setting, not a result
  unbacked   the number appears nowhere the run produced

Only text evidence is searched (notebook outputs and text files). Numbers a run kept
only in pickles or arrays count as unbacked here, so backed_fraction is a lower bound
under --source original. A run that saved no text output at all is reported with
execution "no_evidence" so it is not mistaken for a run whose outputs contradict it.

Three evidence sources are supported:

  --source original   text outputs already in the run's .ipynb, plus any .txt/.log/
                      .csv/.json the run wrote. Fast, and needs no data access.
  --source notebook   re-execute the main jupytext script in a scratch copy of the
                      run, keeping its cache/ directory, and use the fresh outputs.
  --source clean      the same, but with cache/, figures, and all generated data
                      removed first, which tests whether the main script regenerates
                      the result from the archive on its own.

The main script is the .py whose stem matches the .ipynb (the jupytext pair the output
contract asks for). A run without one is reported as `no_main_script`.

Usage:
    python harness/reexecute.py RUN_DIR [--source original|notebook|clean] [--timeout 1800]
    python harness/reexecute.py RUN_DIR --corrupt   # self-test: alter one README number
Writes RUN_DIR/../<run>.claimcheck.json unless --out is given, and prints a summary.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NUM_RE = re.compile(r"(?<![\w.])[-−]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w])")
EVIDENCE_EXT = {".txt", ".log", ".csv", ".json", ".tsv", ".md"}
SKIP_FILES = {"README.md", "transcript.jsonl", "grade.json", "cost.json", "stderr.log",
              "grade.stderr.log", "grade.transcript.jsonl", "assets.json"}
GENERATED_EXT = {".png", ".svg", ".pdf", ".npz", ".npy", ".pkl", ".pickle", ".h5", ".parquet",
                 ".csv", ".json", ".txt", ".log", ".ipynb"}


def parse_number(tok):
    tok = tok.replace("−", "-").replace(",", "")
    try:
        return float(tok)
    except ValueError:
        return None


def decimals(tok):
    tok = tok.replace(",", "")
    if "e" in tok.lower():
        return None
    return len(tok.split(".")[1]) if "." in tok else 0


def is_trivial(value, tok, context):
    """Numbers that are identifiers or labels, not claims."""
    if value is None:
        return True
    if re.fullmatch(r"0*\d{6}", tok):  # dandiset ids
        return True
    if "." not in tok and abs(value) <= 10:  # small counts, figure and panel numbers
        return True
    if "." not in tok and 1900 <= value <= 2100:  # years
        return True
    if re.search(r"(fig(ure)?|panel|step|section|table|sub-|ses-|dandiset|DANDI:)\s*$", context, re.I):
        return True
    return False


REFS_RE = re.compile(r"^#+\s*(references|bibliography|citations?)\b.*$", re.I | re.M)
CITATION_RE = re.compile(r"\d+\s*\(\d+\)\s*,?\s*\d+\s*[-–]\s*\d+|\(\d{4}\)|\b(doi|arXiv)[:\s]\S+", re.I)


def extract_claims(readme_text):
    """Numbers stated in the README, excluding a trailing reference list and
    bibliographic fragments such as volume(issue), pages."""
    m = REFS_RE.search(readme_text)
    if m:
        readme_text = readme_text[:m.start()]
    readme_text = CITATION_RE.sub(" ", readme_text)
    claims = []
    for m in NUM_RE.finditer(readme_text):
        tok = m.group(0)
        before = readme_text[max(0, m.start() - 25):m.start()]
        value = parse_number(tok)
        if is_trivial(value, tok, before):
            continue
        ctx = readme_text[max(0, m.start() - 60):m.end() + 40].replace("\n", " ")
        claims.append({"token": tok, "value": value, "decimals": decimals(tok), "context": ctx.strip()})
    return claims


def numbers_in(text):
    out = []
    for m in NUM_RE.finditer(text):
        v = parse_number(m.group(0))
        if v is not None and math.isfinite(v):
            out.append(v)
    return out


def matches(claim, pool):
    """True if some evidence number rounds to the claimed value, allowing for
    percentages written as fractions and vice versa."""
    target, d = claim["value"], claim["decimals"]
    candidates = [target]
    if "%" in claim["context"] or "percent" in claim["context"].lower():
        candidates.append(target / 100)
    tol = 0.5 * 10 ** (-d) if d is not None else abs(target) * 1e-3
    for x in pool:
        for t in candidates:
            scale = 1 if t is target else 100
            if abs(x * scale - target) <= tol + 1e-12:
                return True
    return False


def notebook_text(ipynb):
    try:
        nb = json.loads(Path(ipynb).read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    chunks = []
    for cell in nb.get("cells", []):
        for out in cell.get("outputs", []):
            if "text" in out:
                chunks.append("".join(out["text"]))
            data = out.get("data", {})
            if "text/plain" in data:
                chunks.append("".join(data["text/plain"]))
    return "\n".join(chunks)


def evidence_text(run_dir):
    parts = []
    for p in Path(run_dir).rglob("*"):
        if not p.is_file() or p.name in SKIP_FILES or "grade" in p.name:
            continue
        if p.suffix == ".ipynb":
            parts.append(notebook_text(p))
        elif p.suffix in EVIDENCE_EXT and p.stat().st_size < 5_000_000:
            parts.append(p.read_text(errors="replace"))
    return "\n".join(parts)


def code_text(run_dir):
    return "\n".join(p.read_text(errors="replace") for p in Path(run_dir).rglob("*.py")
                     if "__pycache__" not in p.parts)


def find_main_script(run_dir):
    run_dir = Path(run_dir)
    for nb in sorted(run_dir.glob("*.ipynb")):
        py = nb.with_suffix(".py")
        if py.exists():
            return py
    return None


def reexecute(run_dir, clean, timeout, python):
    """Copy the run to a scratch directory, optionally strip generated outputs,
    execute the main jupytext script, and return (status, evidence_text, detail)."""
    main = find_main_script(run_dir)
    if main is None:
        return "no_main_script", "", "no .py/.ipynb jupytext pair"
    scratch = Path(tempfile.mkdtemp(prefix="afs-reexec-"))
    work = scratch / Path(run_dir).name
    shutil.copytree(run_dir, work, ignore=shutil.ignore_patterns(
        "transcript.jsonl", "grade*", "stderr.log", "__pycache__", ".ipynb_checkpoints"))
    if clean:
        for p in list(work.rglob("*")):
            if p.is_dir() and p.name == "cache":
                shutil.rmtree(p, ignore_errors=True)
        for p in list(work.rglob("*")):
            if p.is_file() and p.suffix in GENERATED_EXT and p.name != "README.md":
                p.unlink()
    out_nb = work / "_reexec.ipynb"
    # Register a kernelspec for this interpreter in the scratch directory and put it first
    # on JUPYTER_PATH, so a notebook that names a generic "python3" kernel cannot resolve to
    # a user-level kernelspec that points at some other environment.
    kdir = scratch / "jupyter"
    subprocess.run([python, "-m", "ipykernel", "install", "--prefix", str(kdir), "--name", "afs-reexec"],
                   capture_output=True, check=True)
    bindir = str(Path(python).parent)
    env = dict(os.environ, MPLBACKEND="Agg", PATH=bindir + os.pathsep + os.environ.get("PATH", ""),
               JUPYTER_PATH=str(kdir / "share" / "jupyter"))
    cmd = [python, "-m", "jupytext", "--set-kernel", "afs-reexec", "--to", "ipynb", "--execute",
           "--output", str(out_nb), main.name]
    try:
        proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", "", f"exceeded {timeout}s; scratch at {work}"
    if proc.returncode != 0:
        tail = re.sub(r"\x1b\[[0-9;]*m", "", proc.stderr or proc.stdout)[-2000:]
        err = re.findall(r"^(\w+(?:Error|Exception)):", tail, re.M)
        return f"error:{err[-1] if err else 'unknown'}", "", tail
    text = notebook_text(out_nb) + "\n" + evidence_text(work)
    return "ok", text, f"scratch at {work}"


def check(run_dir, source="original", timeout=1800, python=sys.executable, readme_override=None):
    run_dir = Path(run_dir).resolve()
    readme = readme_override if readme_override is not None else (
        (run_dir / "README.md").read_text(errors="replace") if (run_dir / "README.md").exists() else "")
    claims = extract_claims(readme)
    status, detail = "ok", ""
    if source == "original":
        ev = evidence_text(run_dir)
    else:
        status, ev, detail = reexecute(run_dir, clean=(source == "clean"), timeout=timeout, python=python)
    pool = numbers_in(ev)
    code_pool = numbers_in(code_text(run_dir))
    if status == "ok" and not pool:
        status = "no_evidence"
    for c in claims:
        if pool and matches(c, pool):
            c["status"] = "output"
        elif matches(c, code_pool):
            c["status"] = "parameter"
        else:
            c["status"] = "unbacked"
    n_out = sum(c["status"] == "output" for c in claims)
    n_unb = sum(c["status"] == "unbacked" for c in claims)
    result_claims = n_out + n_unb
    return {
        "run": f"{run_dir.parent.name}/{run_dir.name}",
        "source": source,
        "execution": status,
        "detail": detail,
        "has_readme": bool(readme),
        "n_claims": len(claims),
        "n_output": n_out,
        "n_parameter": sum(c["status"] == "parameter" for c in claims),
        "n_unbacked": n_unb,
        "backed_fraction": round(n_out / result_claims, 3) if result_claims else None,
        "specificity": specificity(claims, pool),
        "claims": claims,
    }


def sig_digits(tok):
    digits = tok.replace(",", "").replace("-", "").replace("−", "").split("e")[0].replace(".", "").lstrip("0")
    return len(digits)


def specificity(claims, pool, steps=(0.83, 0.91, 1.07, 1.19, 1.31)):
    """Share of perturbed claim values that the evidence does NOT match by chance.
    Near 1, a match is informative; well below 1, the evidence contains so many
    numbers at the claim's precision that a match says little."""
    if not pool:
        return None
    trials = hits = 0
    for c in claims:
        if c["status"] != "output" or c["value"] == 0:
            continue
        for k in steps:
            d = c["decimals"] or 0
            fake = dict(c, value=round(c["value"] * k, d), context="")
            if fake["value"] == c["value"]:
                continue
            trials += 1
            hits += matches(fake, pool)
    return round(1 - hits / trials, 3) if trials else None


def corrupt_selftest(run_dir):
    """Alter one README number that the original evidence backs, choosing a new value
    that appears nowhere in the evidence, and confirm the checker flags it. Uses a
    claim with at least three significant digits, since shorter values collide with
    unrelated numbers by chance (see `specificity`)."""
    run_dir = Path(run_dir).resolve()
    readme = (run_dir / "README.md").read_text(errors="replace")
    base = check(run_dir, "original")
    pool = numbers_in(evidence_text(run_dir)) + numbers_in(code_text(run_dir))
    backed = [c for c in base["claims"] if c["status"] == "output" and sig_digits(c["token"]) >= 3]
    if not backed:
        return {"run": base["run"], "selftest": "skipped", "reason": "no backed claim with 3+ significant digits"}
    target = backed[len(backed) // 2]
    d = target["decimals"] or 0
    for k in (1.37, 1.53, 0.71, 1.91, 0.59, 2.3):
        new_tok = f"{target['value'] * k:.{d}f}"
        probe = dict(target, token=new_tok, value=float(new_tok))
        if not matches(probe, pool):
            break
    else:
        return {"run": base["run"], "selftest": "skipped", "reason": "no absent replacement value found"}
    # replace the claim itself, at the same number boundaries the extractor uses, so a
    # longer number containing the same digits is not hit first
    pat = re.compile(r"(?<![\w.])" + re.escape(target["token"]) + r"(?![\w])")
    hits = [m for m in pat.finditer(readme) if target["context"][:30].strip() in readme[max(0, m.start() - 80):m.end() + 60]]
    m = hits[0] if hits else pat.search(readme)
    corrupted = readme[:m.start()] + new_tok + readme[m.end():]
    after = check(run_dir, "original", readme_override=corrupted)
    flagged = any(c["token"] == new_tok and c["status"] == "unbacked" for c in after["claims"])
    return {"run": base["run"], "selftest": "pass" if flagged else "FAIL",
            "original": target["token"], "corrupted": new_tok,
            "specificity": base["specificity"], "context": target["context"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--source", choices=["original", "notebook", "clean"], default="original")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out")
    ap.add_argument("--corrupt", action="store_true", help="run the deliberate-corruption self-test")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.corrupt:
        print(json.dumps(corrupt_selftest(a.run_dir)))
        return
    res = check(a.run_dir, a.source, a.timeout, a.python)
    out = Path(a.out) if a.out else Path(a.run_dir).resolve().parent / f"{Path(a.run_dir).name}.claimcheck.{a.source}.json"
    out.write_text(json.dumps(res, indent=1))
    summary = {k: res[k] for k in ("run", "source", "execution", "n_claims", "n_output",
                                    "n_parameter", "n_unbacked", "backed_fraction", "specificity")}
    print(json.dumps(summary))
    if not a.quiet:
        for c in res["claims"]:
            if c["status"] == "unbacked":
                print(f"  unbacked {c['token']:>10}  ...{c['context']}...")


if __name__ == "__main__":
    main()
