# Benchmark Harness for Autonomous DANDI Analyses

This repository runs the benchmark sweep for a study of whether autonomous coding agents can reproduce foundational systems neuroscience findings from data on the [DANDI Archive](https://dandiarchive.org). Each run gives an agent a one-sentence prompt naming a phenomenon, such as hippocampal place cells, and the agent finds a dataset, analyzes it, and writes up the result with no human input.

`harness/` holds the runner, the prompts, the pinned skills and Python environment, and the provenance checks; `harness/README.md` describes them. `.github/workflows/sweep.yml` runs one (model, problem, repetition) cell per job on a fresh GitHub-hosted runner. The repository is public so that jobs get the larger standard runners.

```bash
gh workflow run sweep.yml --repo catalystneuro/afs-harness -f lane=opus-5-5 -f problems=placefields -f reps=1
```
