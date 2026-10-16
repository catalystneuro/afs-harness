"""Review a finished run with a model called through OpenRouter, for the review-and-response experiment.

The reviewer reads a blinded copy of the run, the same view the judges get: the README, results and
metrics files, the analysis code, the run's final message, and every figure as an image, with paths
that name the condition rewritten to /run and model names replaced by MODEL. It gets one request with
no tools, so it cannot run code, and its answer is constrained to a strict JSON schema. Every reviewer
is called the same way, so the arms differ only in the model.

    review.py blind <work> <out>                       blinded copy of an attempt directory
    review.py review <blinded> --model M --out r1.json  first review
    review.py review <blinded> --model M --out r2.json --round 2 --first r1.json
                                                        second review of the revised run, given the
                                                        first review and the author's RESPONSE.md
    review.py render r1.json                            the review as text for the author's prompt

Requests are pinned to the model's own vendor as the provider, with no fallbacks and with
require_parameters, so a request that cannot be served with the reasoning effort and the schema
fails instead of being served without them. A review is saved only if the response names the
requested model. OPENROUTER_API_KEY must be set for `review`.
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent / "review"

# Blinding, as in the study's grading packages.
SKIP_DIRS = {"__pycache__", "pylib", "remfile_cache", ".ipynb_checkpoints", "cache", "lindi_cache"}
SKIP_FILES = {"transcript.jsonl", "provenance.json", "stderr.log"}
SKIP_EXT = {".pkl", ".npz", ".npy", ".db", ".nwb", ".h5", ".parquet"}
IMAGE_EXT = {".png", ".jpg", ".svg", ".pdf"}
PATH_RE = re.compile(r"/home/runner/sweep/(?:\.private/)?(?:runs|smoke)/[A-Za-z0-9._-]+/[A-Za-z0-9_-]+-\d+/attempt-\d+")
MODEL_RE = re.compile(r"(?:claude-)?(?:opus|haiku|sonnet)-5-5(?:-(?:low|high))?", re.I)
# Names that would tell the author which reviewer it has; removed from the review it is shown.
VENDOR_RE = re.compile(r"\b(?:gpt[-\w.]*|openai|astra|claude[-\w.]*|anthropic|opus|haiku|sonnet|gemini[-\w.]*)\b", re.I)

MAX_TEXT_CHARS = 60_000
MAX_FIGURES = 12
METRIC_SUFFIXES = {".json", ".csv", ".tsv", ".txt", ".md"}
PROVIDERS = {"openai": "openai", "anthropic": "anthropic", "google": "google-ai-studio"}

POINT = {"type": "object", "additionalProperties": False,
         "required": ["id", "location", "issue", "affects_conclusion", "what_would_settle_it"],
         "properties": {"id": {"type": "integer"}, "location": {"type": "string"}, "issue": {"type": "string"},
                        "affects_conclusion": {"type": "boolean"}, "what_would_settle_it": {"type": "string"}}}
SUPPORTED = {"type": "string", "enum": ["yes", "partly", "no"]}
SCHEMA_1 = {"type": "object", "additionalProperties": False,
            "required": ["summary", "main_conclusion_supported", "points"],
            "properties": {"summary": {"type": "string"}, "main_conclusion_supported": SUPPORTED,
                           "points": {"type": "array", "items": POINT}}}
SCHEMA_2 = {"type": "object", "additionalProperties": False,
            "required": ["summary", "main_conclusion_supported", "earlier_points", "new_points"],
            "properties": {"summary": {"type": "string"}, "main_conclusion_supported": SUPPORTED,
                           "earlier_points": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False,
                               "required": ["id", "status", "comment"],
                               "properties": {"id": {"type": "integer"},
                                              "status": {"type": "string", "enum": ["resolved", "partly resolved",
                                                                                    "not resolved", "withdrawn"]},
                                              "comment": {"type": "string"}}}},
                           "new_points": {"type": "array", "items": POINT}}}


def blind(work, dst):
    """Copy an attempt directory without its transcript, provenance, logs, caches and large binaries,
    with condition paths and model names rewritten, and a stand-in transcript holding only the final message."""
    for root, dirs, files in os.walk(work):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith("_cache")]
        for f in files:
            src = Path(root) / f
            if f in SKIP_FILES or src.suffix in SKIP_EXT or src.stat().st_size > 20e6:
                continue
            out = dst / src.relative_to(work)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            if out.suffix.lower() not in IMAGE_EXT:
                try:
                    text = out.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                new = MODEL_RE.sub("MODEL", PATH_RE.sub("/run", text))
                if new != text:
                    out.write_text(new, encoding="utf-8")
    final = ""
    transcript = Path(work) / "transcript.jsonl"
    if transcript.exists():
        for line in open(transcript, errors="replace"):
            if '"type":"result"' in line:
                final = str(json.loads(line).get("result", ""))
    (dst / "transcript.jsonl").write_text(
        json.dumps({"type": "result", "result": MODEL_RE.sub("MODEL", PATH_RE.sub("/run", final))}) + "\n")


def text_part(label, path):
    text = path.read_text(errors="replace")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n[... cut: the file has {len(text)} characters in all ...]"
    return {"type": "text", "text": f"===== {label} =====\n{text}"}


def notebook_text(path):
    nb = json.loads(path.read_text(errors="replace"))
    out = []
    for cell in nb.get("cells", []):
        out.append("".join(cell.get("source", [])))
        for o in cell.get("outputs", []):
            if "text" in o:
                out.append("".join(o["text"]))
            elif "text/plain" in o.get("data", {}):
                out.append("".join(o["data"]["text/plain"]))
    return "\n\n".join(out)


def content(run):
    """The blinded run as message parts: README first, then the final message, metrics, code, figures."""
    parts = []
    readme = run / "README.md"
    parts.append(text_part("README.md", readme) if readme.exists()
                 else {"type": "text", "text": "===== README.md =====\n[the run has no README.md]"})
    final = run / "transcript.jsonl"
    if final.exists():
        msg = json.loads(final.read_text().splitlines()[0]).get("result", "")
        parts.append({"type": "text", "text": f"===== final message of the run =====\n{msg}"})
    for p in sorted(run.rglob("*")):
        if p.is_file() and p.suffix in METRIC_SUFFIXES and p.name not in ("transcript.jsonl", "README.md"):
            parts.append(text_part(str(p.relative_to(run)), p))
    scripts = sorted(p for p in run.rglob("*.py") if "__pycache__" not in p.parts)
    for p in scripts:
        parts.append(text_part(str(p.relative_to(run)), p))
    if not scripts:
        for p in sorted(run.rglob("*.ipynb")):
            parts.append({"type": "text", "text": f"===== {p.relative_to(run)} (cells and text outputs) =====\n"
                                                  + notebook_text(p)[:MAX_TEXT_CHARS]})
    figures = sorted(run.rglob("*.png"))
    for p in figures[:MAX_FIGURES]:
        parts.append({"type": "text", "text": f"===== figure {p.relative_to(run)} ====="})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode(), "detail": "high"}})
    if len(figures) > MAX_FIGURES:
        parts.append({"type": "text", "text": f"[{len(figures) - MAX_FIGURES} more figures not included]"})
    return parts


def body(run, a):
    if a.round == 1:
        system, schema = (HERE / "reviewer_round1.md").read_text(), SCHEMA_1
        intro = []
    else:
        system, schema = (HERE / "reviewer_round2.md").read_text(), SCHEMA_2
        first = json.loads(Path(a.first).read_text())
        first = {k: first[k] for k in ("summary", "main_conclusion_supported", "points")}
        intro = [{"type": "text", "text": "===== your first review =====\n" + json.dumps(first, indent=1)}]
    return {
        "model": a.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": intro + content(run)}],
        "reasoning": {"effort": a.effort},
        "max_tokens": a.max_tokens,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": f"review_round{a.round}", "strict": True, "schema": schema}},
        "provider": {"order": [PROVIDERS[a.model.split("/")[0]]], "allow_fallbacks": False,
                     "require_parameters": True},
    }


def review(a):
    from openai import OpenAI
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("set OPENROUTER_API_KEY")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    req = body(Path(a.run), a)
    for attempt in range(5):
        try:
            r = client.chat.completions.create(
                **{x: req[x] for x in ("model", "messages", "max_tokens", "response_format")},
                extra_body={"reasoning": req["reasoning"], "provider": req["provider"]})
            break
        except Exception as e:   # rate limits and provider errors: wait and retry, then fail
            if attempt == 4:
                sys.exit(f"review failed after retries: {type(e).__name__}: {e}")
            time.sleep(30 * (attempt + 1))
    if not r.model.startswith(a.model):   # allows a dated snapshot name
        sys.exit(f"served by {r.model}, not {a.model}; not saved")
    if r.choices[0].finish_reason != "stop":
        sys.exit(f"review did not finish (finish_reason {r.choices[0].finish_reason}); not saved")
    out = json.loads(r.choices[0].message.content)
    out["_reviewer"] = {"model": r.model, "requested_model": a.model, "effort": a.effort, "round": a.round,
                        "response_id": r.id, "usage": r.usage.model_dump() if r.usage else {}}
    Path(a.out).write_text(json.dumps(out, indent=2))
    n = len(out.get("points", out.get("new_points", [])))
    print(f"round {a.round}: {n} points, conclusion supported: {out['main_conclusion_supported']}, "
          f"cost {out['_reviewer']['usage'].get('cost')}")


def render(path):
    """The first review as plain text for the author, with any model or vendor name removed."""
    r = json.loads(Path(path).read_text())
    lines = [r["summary"], ""]
    for p in r["points"]:
        lines += [f"{p['id']}. {p['issue']}",
                  f"   Where: {p['location']}",
                  f"   Affects a conclusion: {'yes' if p['affects_conclusion'] else 'no'}",
                  f"   What would settle it: {p['what_would_settle_it']}", ""]
    return VENDOR_RE.sub("[name removed]", MODEL_RE.sub("[name removed]", "\n".join(lines)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("blind")
    b.add_argument("work")
    b.add_argument("out")
    r = sub.add_parser("review")
    r.add_argument("run", help="blinded copy of the run")
    r.add_argument("--model", required=True, help="OpenRouter model id, e.g. openai/gpt-6-astra")
    r.add_argument("--effort", default="high")
    r.add_argument("--max-tokens", type=int, default=32000)
    r.add_argument("--round", type=int, choices=[1, 2], default=1)
    r.add_argument("--first", help="the first review, for round 2")
    r.add_argument("--out", required=True)
    p = sub.add_parser("render")
    p.add_argument("review")
    a = ap.parse_args()
    if a.command == "blind":
        out = Path(a.out)
        if out.exists():
            sys.exit(f"{out} exists")
        blind(Path(a.work), out)
    elif a.command == "review":
        if a.round == 2 and not a.first:
            sys.exit("round 2 needs --first")
        review(a)
    else:
        print(render(a.review))


if __name__ == "__main__":
    main()
